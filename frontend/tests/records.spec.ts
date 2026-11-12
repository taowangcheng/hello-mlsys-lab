import { test as base, expect, type APIRequestContext } from '@playwright/test';
import { spawn } from 'node:child_process';
import { createServer } from 'node:net';
import { mkdir, mkdtemp, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const artifacts = resolve('../workspaces/records-review');
const test = base.extend<{}, { recordsServer: string }>({
  recordsServer: [
    async ({}, use) => {
      await mkdir(artifacts, { recursive: true });
      const directory = await mkdtemp(resolve(artifacts, 'course-'));
      const reservation = createServer();
      await new Promise<void>((done) => reservation.listen(0, '127.0.0.1', done));
      const port = (reservation.address() as { port: number }).port;
      await new Promise<void>((done) => reservation.close(() => done()));
      const server = spawn(
        resolve('../.venv/bin/python'),
        [
          '-m',
          'mlab',
          'web',
          directory,
          '--agent',
          'fake',
          '--simulator',
          'fixture',
          '--no-browser',
          '--no-opening',
          '--port',
          String(port),
        ],
        { cwd: resolve('..'), stdio: ['ignore', 'pipe', 'pipe'] },
      );
      let diagnostic = '';
      for (const stream of [server.stdout, server.stderr])
        stream.on('data', (chunk) => {
          diagnostic = (diagnostic + chunk).slice(-8000);
        });
      const url = `http://127.0.0.1:${port}`;
      try {
        await expect
          .poll(
            async () => {
              if (server.exitCode !== null) throw new Error(diagnostic);
              try {
                return (await fetch(url + '/api/course')).ok;
              } catch {
                return false;
              }
            },
            { timeout: 15000 },
          )
          .toBe(true);
        await use(url);
      } finally {
        if (server.exitCode === null)
          await new Promise<void>((done) => {
            const timer = setTimeout(() => {
              server.kill('SIGKILL');
              done();
            }, 5000);
            server.once('exit', () => {
              clearTimeout(timer);
              done();
            });
            server.kill('SIGTERM');
          });
        await writeFile(resolve(directory, 'browser-review-server.log'), diagnostic);
      }
    },
    { scope: 'worker' },
  ],
  baseURL: async ({ recordsServer }, use) => use(recordsServer),
});

async function post(request: APIRequestContext, path: string, data: unknown) {
  const response = await request.post(path, { data });
  const result = await response.json();
  expect(result.ok, JSON.stringify(result.error)).toBe(true);
  return result;
}

test('configuration, measurement and report views preserve evidence and optional reflection', async ({
  page,
  request,
}) => {
  test.setTimeout(90000);
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const { session } = await post(request, '/api/sessions', { task: '9.2' });
  const endpoint = `/api/workbench/sessions/${session}`;
  const initial = await (await request.get(endpoint)).json();
  const candidate = structuredClone(initial.templates.find((item: any) => item.id === 'baseline'));
  candidate.id = 'c20';
  candidate.strategy.serving.max_concurrency = 20;
  const experiment = await post(request, `${endpoint}/create`, {
    request_id: crypto.randomUUID(),
    title: 'Investigate a narrow SLA margin',
    question: 'Does the simulated margin survive measurement?',
    candidates: [candidate],
    seeds: [42],
    workloads: ['development'],
  });
  await post(request, `${endpoint}/simulate`, {
    request_id: crypto.randomUUID(),
    workflow: experiment.id,
    revision: experiment.revision,
    confirm: true,
  });
  await expect
    .poll(
      async () =>
        (await (await request.get(endpoint)).json()).runs.filter(
          (run: any) => run.status === 'succeeded',
        ).length,
    )
    .toBe(1);
  const preview = await post(request, `${endpoint}/prepare_measurement`, {
    request_id: crypto.randomUUID(),
    workflow: experiment.id,
    points: [
      {
        workflow: experiment.id,
        candidate: 'c20',
        workload: 'development',
        runs: 2,
        rationale: 'Check the margin and compare repeated executions.',
      },
    ],
  });
  await post(request, `${endpoint}/measure`, {
    request_id: crypto.randomUUID(),
    measurement: preview.id,
    revision: preview.plan_hash,
    confirm: true,
  });
  await expect
    .poll(
      async () =>
        (await (await request.get(endpoint)).json()).runs.filter(
          (run: any) => run.role === 'hardware' && run.status === 'succeeded',
        ).length,
    )
    .toBe(2);
  const route = (view: string, id: string) =>
    `/#/${session}/${view}/${encodeURIComponent(`${session}/${id}`)}`;

  await page.goto(route('config', `${experiment.id}/c20`));
  await expect(
    page.getByRole('heading', { name: 'Configuration evidence', exact: true }),
  ).toBeVisible();
  const matrix = page.locator('.record-validation');
  await expect(matrix.getByRole('row').filter({ hasText: 'Simulation' })).toContainText('Met');
  await expect(matrix.getByRole('row').filter({ hasText: 'Measurement' })).toContainText(
    'Violated',
  );
  await expect(matrix).toContainText('Not run');
  await expect(page.locator('.records-view')).toContainText(
    'They do not establish real GPU performance.',
  );
  await page.screenshot({ path: resolve(artifacts, 'configuration-evidence.png'), fullPage: true });

  await page.goto(route('measurements', preview.id));
  await expect(
    page.getByRole('heading', { name: 'Predictions before measurement', exact: true }),
  ).toBeVisible();
  await expect(page.locator('.measurement-group')).toHaveCount(1);
  await expect(page.locator('.measurement-group')).toContainText('Verdict changed');
  await expect(page.locator('.measurement-group')).toContainText('Within-run interval');
  await expect(page.locator('.measurement-group')).toContainText('Repeated measurements');
  const measurement = await (
    await request.get(`/api/sessions/${session}/records/${preview.id}`)
  ).json();
  const measuredRun = measurement.alignment.rows[0].run_id;
  await page.getByLabel('Inspect request ratios', { exact: true }).selectOption(measuredRun);
  const pairs = await (
    await request.get(
      `/api/sessions/${session}/measurements/${preview.id}/pairs?run=${measuredRun}`,
    )
  ).json();
  await expect(page.locator('.record-request-ratios tbody tr')).toHaveCount(pairs.pairs.length);
  await expect(
    page.getByRole('img', { name: 'Measured to simulated latency ratio' }),
  ).toBeVisible();
  await page.screenshot({ path: resolve(artifacts, 'measurement-analysis.png'), fullPage: true });

  await page.goto(route('plans', experiment.id));
  await page.locator('.experiment-reflection summary').click();
  const reflection =
    'The simulation met the SLA, but both measurements exceeded it. I need more margin.';
  await page.getByLabel('Your reflection', { exact: true }).fill(reflection);
  await page.getByRole('button', { name: 'Save reflection', exact: true }).click();
  await expect(page.locator('.experiment-reflection')).toContainText(reflection);
  await page.reload();
  await page.locator('.experiment-reflection summary').click();
  await expect(page.locator('.experiment-reflection')).toContainText(reflection);
  const reflected = await (await request.get(endpoint)).json();
  expect(reflected.notes.at(-1).refs).toContain(`${session}/${experiment.id}`);

  await page.goto(`/#/${session}/report`);
  await expect(page.getByRole('heading', { name: 'Evidence summary', exact: true })).toBeVisible();
  await expect(page.locator('.record-report')).toContainText('c20');
  await expect(page.locator('.record-report table')).not.toHaveCount(0);
  await page.getByRole('button', { name: 'Save report', exact: true }).click();
  await expect(
    page.getByRole('button', { name: 'Update saved report', exact: true }),
  ).toBeVisible();
  const saved = await (await request.get(`/api/sessions/${session}/records/report`)).json();
  expect(saved.written).toBe(true);
  expect(saved.conclusion).toBeNull();
  await page.screenshot({
    path: resolve(artifacts, 'report-with-open-questions.png'),
    fullPage: true,
  });

  const fresh = await post(request, '/api/sessions', { task: '9.2' });
  await page.goto(`/#/${fresh.session}/report`);
  await expect(page.getByRole('heading', { name: 'Evidence summary', exact: true })).toBeVisible();
  await expect(page.locator('.records-view')).not.toContainText('c20');
  await expect(page.locator('.record-report')).not.toContainText(reflection);
  await page.getByRole('button', { name: 'Save report', exact: true }).click();
  await expect(
    page.getByRole('button', { name: 'Update saved report', exact: true }),
  ).toBeVisible();
  const clean = await (await request.get(`/api/workbench/sessions/${fresh.session}`)).json();
  expect(clean.runs).toHaveLength(0);
  expect(clean.budget.used).toBe(reflected.budget.used);
  expect(errors).toEqual([]);
});
