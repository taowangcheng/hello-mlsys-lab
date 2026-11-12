import { test as base, expect, type Page } from '@playwright/test';
import { spawn } from 'node:child_process';
import { createServer } from 'node:net';
import { mkdir, mkdtemp, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

type ReviewServer = { url: string; directory: string };
const reviewRoot = resolve('../workspaces/all-tasks-review/offline');

// A separate course keeps complete histories independent of other UI tests.
const test = base.extend<{}, { reviewServer: ReviewServer }>({
  reviewServer: [
    async ({}, use) => {
      await mkdir(reviewRoot, { recursive: true });
      const directory = await mkdtemp(resolve(reviewRoot, 'course-'));
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
        let ready = false;
        for (let attempt = 0; attempt < 100; attempt++) {
          if (server.exitCode !== null) throw new Error(`Review server exited: ${diagnostic}`);
          try {
            ready = (await fetch(url + '/api/course')).ok;
          } catch {}
          if (ready) break;
          await new Promise((done) => setTimeout(done, 100));
        }
        if (!ready) throw new Error(`Review server did not start: ${diagnostic}`);
        await use({ url, directory });
      } finally {
        if (server.exitCode === null) {
          await new Promise<void>((done) => {
            server.once('exit', () => done());
            server.kill('SIGTERM');
            const timeout = setTimeout(() => {
              server.kill('SIGKILL');
              done();
            }, 5000);
            timeout.unref();
          });
        }
        await writeFile(resolve(directory, 'browser-review-server.log'), diagnostic);
      }
    },
    { scope: 'worker' },
  ],
  baseURL: async ({ reviewServer }, use) => use(reviewServer.url),
});

const scenarios = [
  {
    task: '9.1',
    title: 'Memory and latency on an edge device',
    question:
      'Does a smaller model at higher precision satisfy the edge latency and memory limits?',
    first: 'edge_int4',
    second: 'edge_fp8',
    chosen: 'edge_fp8',
    chosenIndex: 1,
    workloads: ['other_prompt_set'],
    sims: 8,
    hardware: 3,
    difference: 'Weight precision',
    note: 'The smaller FP8 model meets the measured constraints across repetitions and alternate prompts. These fixture and stand-in results are demonstration evidence.',
  },
  {
    task: '9.2',
    title: 'Concurrency and latency margin',
    question:
      'Does concurrency 20 trade away too much latency margin compared with concurrency 16?',
    first: 'c16',
    second: 'c20',
    chosen: 'c16',
    chosenIndex: 0,
    workloads: ['short_prompt_distribution', 'long_prompt_distribution'],
    sims: 12,
    hardware: 4,
    difference: 'Maximum concurrency',
    note: 'Concurrency 16 meets the measured latency limit in repeated and held-out workloads. Higher simulated throughput alone would not establish enough SLA margin. The measurements are synthetic.',
  },
  {
    task: '9.3',
    title: 'Scheduling a dependent workload',
    question: 'How does FIFO compare with critical-path scheduling on the same GPU placement?',
    first: 'dag_critical',
    second: 'dag_fifo',
    chosen: 'dag_critical',
    chosenIndex: 0,
    workloads: ['one_dimension_change', 'second_dag_same_distribution'],
    sims: 12,
    hardware: 4,
    difference: 'Scheduling priority',
    note: 'The critical-path configuration completes every request in the measured development and held-out DAGs. I checked completion before comparing duration. These are stand-in results, not GPU measurements.',
  },
];

async function photograph(page: Page, directory: string, name: string) {
  await page.screenshot({ path: resolve(directory, name + '.png'), fullPage: true });
}

for (const scenario of scenarios) {
  test(`task ${scenario.task}: complete exploration, measurement, conclusion and reuse`, async ({
    page,
    request,
    reviewServer,
  }) => {
    test.setTimeout(120_000);
    const directory = resolve(reviewRoot, `task-${scenario.task}`);
    await mkdir(directory, { recursive: true });
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('console', (message) => {
      if (message.type() === 'error') errors.push(message.text());
    });
    const opened = await (
      await request.post('/api/sessions', { data: { task: scenario.task } })
    ).json();
    expect(opened.ok).toBe(true);
    const session = opened.session;
    await page.goto(`/#/${scenario.task}/overview`);
    await expect(
      page.getByRole('button', { name: 'Start an experiment', exact: true }),
    ).toBeVisible();
    await photograph(page, directory, '01-task-overview');
    await page.getByRole('button', { name: 'Start an experiment', exact: true }).click();
    await page.getByRole('textbox', { name: 'Experiment name', exact: true }).fill(scenario.title);
    await page
      .getByRole('textbox', { name: 'What do you want to find out?', exact: true })
      .fill(scenario.question);
    await page
      .getByRole('textbox', { name: 'What do you expect?', exact: true })
      .fill(
        'I will compare constraints as well as performance, then check the promising configuration on the stand-in backend.',
      );
    await page
      .getByRole('textbox', { name: 'Configuration name', exact: true })
      .fill(scenario.first);
    if (scenario.task === '9.2')
      await page.getByRole('spinbutton', { name: 'Maximum concurrency', exact: true }).fill('16');
    await page.getByRole('button', { name: 'Add a variant', exact: true }).click();
    await page
      .getByRole('textbox', { name: 'Configuration name', exact: true })
      .fill(scenario.second);
    if (scenario.task === '9.1') {
      await page
        .getByRole('combobox', { name: 'Model', exact: true })
        .selectOption({ label: 'llm-3b' });
      await page
        .getByRole('combobox', { name: 'Weight precision', exact: true })
        .selectOption({ label: 'FP8' });
    } else if (scenario.task === '9.2') {
      await page.getByRole('spinbutton', { name: 'Maximum concurrency', exact: true }).fill('20');
    } else {
      await page
        .getByRole('combobox', { name: 'Scheduling priority', exact: true })
        .selectOption({ label: 'fifo' });
    }
    await page
      .getByRole('textbox', { name: 'Why include this configuration?', exact: true })
      .fill(scenario.question);
    for (const checkbox of await page.locator('.workload-options input').all())
      await checkbox.check();
    await page.getByRole('spinbutton', { name: 'Repetitions', exact: true }).fill('2');
    await page.getByRole('button', { name: 'Save draft', exact: true }).click();
    await expect(page.getByText('Draft saved', { exact: true })).toBeVisible();
    await page.locator('.workspace-content').evaluate((element) => element.scrollTo(0, 0));
    await photograph(page, directory, '02-experiment-configurations');
    await page.getByRole('button', { name: 'Run simulations', exact: true }).click();
    await page.getByRole('button', { name: 'Start simulations', exact: true }).click();
    await expect(page.locator('.runs-table tbody tr')).toHaveCount(scenario.sims);
    await expect(page.locator('.runs-table').getByText('Completed', { exact: true })).toHaveCount(
      scenario.sims,
    );
    let evidence = await (await request.get(`/api/workbench/tasks/${scenario.task}`)).json();
    const simulations = evidence.runs.filter((run: any) => run.role === 'simulator');
    expect(simulations.every((run: any) => run.status === 'succeeded')).toBe(true);
    expect(
      simulations.every((run: any) =>
        ['constraints_met', 'constraints_violated'].includes(run.verdict),
      ),
    ).toBe(true);
    // A completed process is not the same as satisfying the task's constraints.
    for (const run of simulations) {
      const row = page.locator('.runs-table tbody tr').filter({ hasText: run.id });
      await expect(row.locator('td').nth(3)).toHaveText('Completed');
      await expect(row.locator('td').nth(6)).toHaveText(
        run.verdict === 'constraints_met' ? 'Meets constraints' : 'Outside constraints',
      );
    }
    await photograph(page, directory, '03-simulation-history');
    const chosenRun = simulations.find(
      (run: any) =>
        run.candidate === scenario.chosen && run.workload === 'development' && run.repeat === 0,
    );
    expect(chosenRun).toBeTruthy();
    await page.locator('.runs-table .row-link').filter({ hasText: chosenRun.id }).click();
    await page.getByRole('button', { name: 'Requests', exact: true }).click();
    await page
      .getByRole('combobox', { name: 'Order requests', exact: true })
      .selectOption('slowest');
    await page.locator('.request-row').first().click();
    await expect(page.locator('.request-inspector')).toContainText('First token at');
    await photograph(page, directory, '04-request-inspection');
    await page.getByRole('button', { name: 'All runs', exact: true }).click();
    const controls = simulations.filter(
      (run: any) => run.workload === 'development' && run.repeat === 0,
    );
    expect(controls).toHaveLength(2);
    for (const run of controls)
      await page.getByRole('checkbox', { name: `Select ${run.id}`, exact: true }).check();
    await page.getByRole('button', { name: 'Compare results', exact: true }).click();
    await expect(page.locator('.comparison-table')).toContainText(scenario.difference);
    await photograph(page, directory, '05-configuration-comparison');
    await page.getByRole('link', { name: 'Runs', exact: true }).click();
    let measured = 0;
    for (const workload of ['development', ...scenario.workloads]) {
      const repeats = workload === 'development' ? 2 : 1;
      await page.getByRole('button', { name: 'Arrange measurement', exact: true }).click();
      const dialog = page.getByRole('dialog');
      await dialog.getByRole('checkbox').nth(scenario.chosenIndex).check();
      await dialog.getByRole('combobox', { name: 'Workload', exact: true }).selectOption(workload);
      await dialog
        .getByRole('spinbutton', { name: 'Repetitions', exact: true })
        .fill(String(repeats));
      await dialog
        .getByRole('textbox', { name: 'Why measure these configurations?', exact: true })
        .fill(`Validate ${scenario.chosen} on ${workload}; repetitions check consistency.`);
      await dialog.getByRole('button', { name: 'Review measurement', exact: true }).click();
      await expect(
        dialog.getByRole('button', { name: 'Confirm and measure', exact: true }),
      ).toBeEnabled();
      if (workload === 'development')
        await photograph(page, directory, '06-measurement-confirmation');
      await dialog.getByRole('button', { name: 'Confirm and measure', exact: true }).click();
      measured += repeats;
      await expect(page.locator('.runs-table').getByText('Completed', { exact: true })).toHaveCount(
        scenario.sims + measured,
      );
    }
    evidence = await (await request.get(`/api/workbench/tasks/${scenario.task}`)).json();
    const hardware = evidence.runs.filter((run: any) => run.role === 'hardware');
    expect(hardware).toHaveLength(scenario.hardware);
    expect(
      hardware.every((run: any) => run.status === 'succeeded' && run.verdict === 'constraints_met'),
    ).toBe(true);
    const measuredRun = hardware.find((run: any) => run.workload === 'development');
    await page.locator('.runs-table .row-link').filter({ hasText: measuredRun.id }).click();
    await expect(page.getByText('Matching simulation', { exact: true })).toBeVisible();
    await photograph(page, directory, '07-measured-result');
    await page.getByRole('button', { name: 'Observations', exact: true }).click();
    await page.getByRole('textbox', { name: 'Your observation', exact: true }).fill(scenario.note);
    await page.getByRole('button', { name: 'Save observation', exact: true }).click();
    await expect(page.locator('.research-note')).toContainText(scenario.note);
    await page.getByRole('link', { name: 'Notes & conclusions', exact: true }).click();
    await page.getByRole('button', { name: 'Submit conclusion', exact: true }).click();
    const dialog = page.getByRole('dialog');
    await dialog
      .getByRole('combobox', { name: 'Configuration', exact: true })
      .selectOption(`${session}/r001/${scenario.chosen}`);
    await expect(
      dialog.getByRole('button', { name: 'Submit conclusion', exact: true }),
    ).toBeEnabled();
    await dialog.getByRole('button', { name: 'Submit conclusion', exact: true }).click();
    await expect(
      page.getByRole('heading', { name: 'Submitted conclusions', exact: true }),
    ).toBeVisible();
    await photograph(page, directory, '08-notes-and-conclusion');
    await page.getByRole('link', { name: 'View report', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Report', exact: true })).toBeVisible();
    await photograph(page, directory, '09-report');
    const report = await (await request.get(`/api/sessions/${session}/records/report`)).json();
    expect(report.written).toBe(true);
    await page.goto(`/#/${scenario.task}/runs`);
    await page.locator('.runs-table .row-link').filter({ hasText: chosenRun.id }).click();
    await page.getByRole('button', { name: 'Use this configuration', exact: true }).click();
    await expect(
      page.getByRole('textbox', { name: 'Configuration name', exact: true }),
    ).toHaveValue(scenario.chosen);
    await expect(page.getByText('Based on an earlier experiment', { exact: false })).toBeVisible();
    await photograph(page, directory, '10-reuse-earlier-configuration');
    evidence = await (await request.get(`/api/workbench/tasks/${scenario.task}`)).json();
    expect(evidence.conclusions.at(-1).conclusion).toBe('mock_only');
    expect(evidence.experiments.at(-1).source).toBe(`${session}/r001`);
    expect(evidence.budget.used).toBe(scenario.hardware);
    await writeFile(
      resolve(directory, 'evidence.json'),
      JSON.stringify(
        {
          task: scenario.task,
          session,
          course_directory: reviewServer.directory,
          source:
            'Offline browser verification with fixture simulation and hardware stand-in; no model conversation.',
          simulated_runs: simulations.length,
          hardware_runs: hardware.length,
          conclusion: evidence.conclusions.at(-1),
          notes: evidence.notes,
          runs: evidence.runs,
          report_written: report.written,
          browser_errors: errors,
        },
        null,
        2,
      ) + '\n',
    );
    expect(errors).toEqual([]);
  });
}
