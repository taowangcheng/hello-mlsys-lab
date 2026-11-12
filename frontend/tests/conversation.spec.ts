import { test as base, expect, type APIRequestContext, type Page } from '@playwright/test';
import { spawn } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { createServer } from 'node:net';
import { mkdir, mkdtemp, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

type ReviewServer = { url: string; directory: string };
const artifacts = resolve('../workspaces/conversation-review');
const chinese = JSON.parse(readFileSync(resolve('../src/mlab/web/static/locales/zh.json'), 'utf8'));
const test = base.extend<{}, { conversationServer: ReviewServer }>({
  conversationServer: [
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
        let ready = false;
        for (let attempt = 0; attempt < 100; attempt++) {
          if (server.exitCode !== null)
            throw new Error(`Conversation server exited: ${diagnostic}`);
          try {
            ready = (await fetch(url + '/api/course')).ok;
          } catch {}
          if (ready) break;
          await new Promise((done) => setTimeout(done, 100));
        }
        if (!ready) throw new Error(`Conversation server did not start: ${diagnostic}`);
        await use({ url, directory });
      } finally {
        if (server.exitCode === null)
          await new Promise<void>((done) => {
            const timeout = setTimeout(() => {
              server.kill('SIGKILL');
              done();
            }, 5000);
            server.once('exit', () => {
              clearTimeout(timeout);
              done();
            });
            server.kill('SIGTERM');
          });
        await writeFile(resolve(directory, 'browser-review-server.log'), diagnostic);
      }
    },
    { scope: 'worker' },
  ],
  baseURL: async ({ conversationServer }, use) => use(conversationServer.url),
});

function monitor(page: Page) {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text());
  });
  return errors;
}
async function send(page: Page, text: string) {
  await page.getByRole('textbox', { name: 'Message', exact: true }).fill(text);
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(page.locator('.discussion-message.student').last()).toContainText(text);
}
async function post(request: APIRequestContext, path: string, data: unknown) {
  const result = await (await request.post(path, { data })).json();
  expect(result.ok, JSON.stringify(result.error)).toBe(true);
  return result;
}
async function screenshot(page: Page, name: string) {
  await page.screenshot({ path: resolve(artifacts, name + '.png'), fullPage: true });
}

test('the three-pane workbench accepts conceptual questions on every task', async ({
  page,
  request,
}) => {
  const errors = monitor(page);
  await page.goto('/');
  await expect(page.locator('.conversation-panel')).toBeVisible();
  for (const task of ['9.1', '9.2', '9.3']) {
    const opened = await post(request, '/api/sessions', { task });
    await page.goto(`/#/${opened.session}/overview`);
    await expect(page.locator('.workspace-content')).toBeVisible();
    await expect(
      page.getByRole('button', { name: 'Understand the task', exact: true }),
    ).toBeVisible();
    await send(page, 'What is simulation?');
    await expect(page.locator('.discussion-message.agent').last()).not.toBeEmpty();
    const view = await (await request.get(`/api/workbench/sessions/${opened.session}`)).json();
    expect(view.runs).toHaveLength(0);
    const chat = await (await request.get(`/api/sessions/${opened.session}/chat`)).json();
    expect(chat.entries.filter((entry: any) => entry.role === 'student').at(-1).refs || []).toEqual(
      [],
    );
    await screenshot(page, `task-${task}-conceptual-conversation`);
  }
  await page.getByRole('button', { name: 'Reconnect', exact: true }).click();
  await expect(page.locator('.conversation-agent-bar')).toContainText('Ready');
  await page.setViewportSize({ width: 390, height: 844 });
  await page
    .locator('.mobile-panes')
    .getByRole('button', { name: 'Conversation', exact: true })
    .click();
  await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  await screenshot(page, 'mobile-conversation');
  expect(errors).toEqual([]);
});

test('a new conversation starts clean and switching back restores its evidence and draft', async ({
  page,
  request,
}) => {
  const errors = monitor(page);
  const opened = await post(request, '/api/sessions', { task: '9.2' });
  const view = await (await request.get(`/api/workbench/sessions/${opened.session}`)).json();
  const candidates = [16, 20].map((concurrency) => {
    const candidate = structuredClone(view.templates.find((item: any) => item.id === 'baseline'));
    candidate.id = `history_c${concurrency}`;
    candidate.strategy.serving.max_concurrency = concurrency;
    return candidate;
  });
  const experiment = await post(request, `/api/workbench/sessions/${opened.session}/create`, {
    request_id: crypto.randomUUID(),
    title: 'Historical concurrency comparison',
    question: 'Compare the effect of concurrency on the tail.',
    candidates,
    seeds: [42],
    workloads: ['development'],
  });
  const launched = await post(request, `/api/workbench/sessions/${opened.session}/simulate`, {
    request_id: crypto.randomUUID(),
    workflow: experiment.id,
    revision: experiment.revision,
    confirm: true,
  });
  await expect
    .poll(
      async () =>
        (await (await request.get(`/api/workbench/sessions/${opened.session}`)).json()).runs.filter(
          (run: any) => run.status === 'succeeded',
        ).length,
    )
    .toBe(2);
  await page.goto(`/#/${opened.session}/overview`);
  const prompt = 'Compare these two previous runs and explain their latency difference.';
  await page.getByRole('textbox', { name: 'Message', exact: true }).fill(prompt);
  await page.getByRole('button', { name: 'Add from history', exact: true }).click();
  await page.getByRole('textbox', { name: 'Search history', exact: true }).fill('sim_');
  await expect(page.locator('.history-option')).toHaveCount(2);
  for (const option of await page.locator('.history-option').all()) {
    await expect(option).toContainText('Simulation');
    await expect(option).toContainText('Development workload');
    await option.getByRole('checkbox').check();
  }
  await page.getByRole('textbox', { name: 'Search history', exact: true }).fill('history_c16');
  await page
    .locator('.history-option')
    .filter({ has: page.locator('.history-reference-meta', { hasText: /^Configuration/ }) })
    .getByRole('checkbox')
    .check();
  await page.getByRole('button', { name: 'Attach selected', exact: true }).click();
  await expect(page.locator('.discussion-context .reference-chip')).toHaveCount(3);
  await page.reload();
  await expect(page.locator('.discussion-context .reference-chip')).toHaveCount(3);
  await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toHaveValue(prompt);
  await page.getByRole('button', { name: 'New conversation', exact: true }).click();
  await expect(page).not.toHaveURL(new RegExp(`${opened.session}/`));
  const fresh = await page.evaluate(() => location.hash.split('/')[1]);
  await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toHaveValue('');
  await expect(page.locator('.discussion-context .reference-chip')).toHaveCount(0);
  await expect(page.locator('.discussion-message')).toHaveCount(0);
  const empty = await (await request.get(`/api/workbench/sessions/${fresh}`)).json();
  expect(empty.experiments).toHaveLength(0);
  expect(empty.runs).toHaveLength(0);
  expect(empty.measurements).toHaveLength(0);
  await page.getByRole('button', { name: 'Add from history', exact: true }).click();
  await page.getByRole('textbox', { name: 'Search history', exact: true }).fill('history_c');
  await expect(page.locator('.history-option')).toHaveCount(0);
  await page.keyboard.press('Escape');
  await screenshot(page, 'new-conversation-clean-attempt');
  await page
    .locator('.session-list')
    .getByRole('button', { name: `Conversation ${opened.session.split('-')[1]}`, exact: true })
    .click();
  await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toHaveValue(prompt);
  await expect(page.locator('.discussion-context .reference-chip')).toHaveCount(3);
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(
    page.locator('.discussion-message.student .message-citations .citation'),
  ).toHaveCount(3);
  await expect(page.locator('.discussion-context .reference-chip')).toHaveCount(0);
  const transcript = await (await request.get(`/api/sessions/${opened.session}/chat`)).json();
  expect(
    transcript.entries
      .find((entry: any) => entry.role === 'student' && entry.text === prompt)
      .refs.sort(),
  ).toEqual(
    [
      ...launched.runs.map((run: string) => `${opened.session}/${run}`),
      `${opened.session}/${experiment.id}/history_c16`,
    ].sort(),
  );
  await screenshot(page, 'historical-evidence-conversation');
  const draft = 'Could a different workload reverse this result?';
  await page.getByRole('textbox', { name: 'Message', exact: true }).fill(draft);
  await page
    .locator('.discussion-message.student .citation')
    .filter({ hasText: launched.runs[0] })
    .click();
  await expect(page).toHaveURL(
    new RegExp(encodeURIComponent(`${opened.session}/${launched.runs[0]}`)),
  );
  await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toHaveValue(draft);
  await screenshot(page, 'historical-result-alongside-conversation');
  await page
    .locator('.discussion-message.student .citation')
    .filter({ has: page.locator('small', { hasText: /^Configuration/ }) })
    .click();
  await expect(page).toHaveURL(
    new RegExp(`/config/${encodeURIComponent(`${opened.session}/${experiment.id}/history_c16`)}`),
  );
  await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toHaveValue(draft);
  await page.getByRole('textbox', { name: 'Message', exact: true }).fill(draft + ' @');
  await expect(page.getByRole('dialog', { name: 'Add from history', exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  await page.reload();
  await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toHaveValue(
    draft + ' ',
  );
  await expect(
    page.locator('.discussion-message.student .message-citations .citation'),
  ).toHaveCount(3);
  expect(errors).toEqual([]);
});

test('conversation can run a simulation and confirm or cancel sandbox measurement', async ({
  page,
  request,
}) => {
  test.setTimeout(90_000);
  const errors = monitor(page);
  const opened = await post(request, '/api/sessions', { task: '9.3' });
  await page.goto(`/#/${opened.session}/overview`);
  await send(page, 'Run a simulation for this task.');
  await expect
    .poll(
      async () =>
        (await (await request.get(`/api/workbench/sessions/${opened.session}`)).json()).runs.filter(
          (run: any) => run.role === 'simulator' && run.status === 'succeeded',
        ).length,
    )
    .toBe(1);
  await expect(page.locator('.discussion-message.agent').last()).toContainText('sim_0001');
  const operations = page.locator('.conversation-activity').first();
  await expect(operations.locator('summary')).toHaveText('Simulation submitted');
  await operations.locator('summary').click();
  await expect(operations).toContainText('Prepare an experiment plan');
  await expect(operations).toContainText('Submit a simulation');
  await expect(operations).not.toContainText('Activity');
  await expect(page.getByRole('link', { name: 'Experiment plans', exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Run history', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Chinese', exact: true }).click();
  await expect(page.locator('.conversation-activity summary').first()).toHaveText(
    chinese['Simulation submitted'],
  );
  await expect(
    page.getByRole('link', { name: chinese['Experiment plans'], exact: true }),
  ).toBeVisible();
  await expect(page.getByRole('link', { name: chinese['Run history'], exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'EN', exact: true }).click();
  await screenshot(page, 'chat-simulation-result');
  await send(page, 'Run a sandbox measurement for this experiment.');
  let card = page.locator('.conversation-action-card.pending');
  await expect(card).toContainText('Measure in the sandbox');
  await expect(card).toContainText('Available budget');
  await expect(
    page.getByRole('checkbox', { name: 'Stop the current reply first', exact: true }),
  ).toBeVisible();
  await card.getByRole('button', { name: 'Cancel', exact: true }).click();
  await expect(card).toHaveCount(0);
  let view = await (await request.get(`/api/workbench/sessions/${opened.session}`)).json();
  expect(view.budget.used).toBe(0);
  expect(view.runs.filter((run: any) => run.role === 'hardware')).toHaveLength(0);
  await send(page, 'Run a sandbox measurement for this experiment.');
  card = page.locator('.conversation-action-card.pending');
  await expect(card.getByRole('button', { name: 'Run measurement', exact: true })).toBeEnabled();
  await screenshot(page, 'chat-measurement-confirmation');
  await card.getByRole('button', { name: 'Run measurement', exact: true }).click();
  await expect
    .poll(
      async () =>
        (await (await request.get(`/api/workbench/sessions/${opened.session}`)).json()).runs.filter(
          (run: any) => run.role === 'hardware' && run.status === 'succeeded',
        ).length,
    )
    .toBe(1);
  await expect(page.locator('.discussion-message.agent').last()).toContainText('hw_0001');
  view = await (await request.get(`/api/workbench/sessions/${opened.session}`)).json();
  expect(view.budget.used).toBe(1);
  const chat = await (await request.get(`/api/sessions/${opened.session}/chat`)).json();
  const cards = chat.entries.filter(
    (entry: any) => entry.role === 'card' && entry.card === 'execution',
  );
  expect(cards).toHaveLength(2);
  expect(cards.map((entry: any) => entry.state)).toEqual(['denied', 'approved']);
  await page.getByRole('button', { name: 'Add from history', exact: true }).click();
  await page.getByRole('textbox', { name: 'Search history', exact: true }).fill('m00');
  await expect(page.locator('.history-option')).toHaveCount(2);
  await page.keyboard.press('Escape');
  await screenshot(page, 'chat-measurement-result');
  await page
    .locator('.discussion-message.agent .citation')
    .filter({ hasText: 'hw_0001' })
    .last()
    .click();
  const pairRequest = page.waitForRequest(
    (item) => item.url().endsWith('/api/workbench/compare') && item.method() === 'POST',
  );
  await page.getByRole('button', { name: 'Compare this pair', exact: true }).click();
  expect((await pairRequest).postDataJSON().runs.sort()).toEqual([
    `${opened.session}/hw_0001`,
    `${opened.session}/sim_0001`,
  ]);
  await expect(page.locator('.comparison-controls')).toContainText('2 runs selected');
  await expect(page.locator('.comparison-table thead th')).toHaveCount(3);
  await writeFile(
    resolve(artifacts, 'conversation-evidence.json'),
    JSON.stringify(
      {
        source:
          'Deterministic offline conversation transport; fixture simulation and hardware stand-in.',
        session: opened.session,
        runs: view.runs,
        entries: chat.entries,
        browser_errors: errors,
      },
      null,
      2,
    ) + '\n',
  );
  expect(errors).toEqual([]);
});

test('a late simulation response cannot navigate away from another conversation', async ({
  page,
  request,
}) => {
  const errors = monitor(page);
  const first = await post(request, '/api/sessions', { task: '9.2' });
  const second = await post(request, '/api/sessions', { task: '9.2' });
  const endpoint = `/api/workbench/sessions/${first.session}`;
  const initial = await (await request.get(endpoint)).json();
  const candidate = initial.templates.find((item: any) => item.id === 'baseline');
  const experiment = await post(request, `${endpoint}/create`, {
    request_id: crypto.randomUUID(),
    title: 'Delayed response experiment',
    candidates: [candidate],
    seeds: [42],
    workloads: ['development'],
  });
  let release!: () => void;
  const gate = new Promise<void>((done) => {
    release = done;
  });
  let reached!: () => void;
  const responseHeld = new Promise<void>((done) => {
    reached = done;
  });
  await page.route(`**${endpoint}/simulate`, async (route) => {
    const response = await route.fetch();
    reached();
    await gate;
    await route.fulfill({ response });
  });
  try {
    await page.goto(`/#/${first.session}/plans/${encodeURIComponent(experiment.key)}`);
    await page.getByRole('button', { name: 'Run simulations', exact: true }).click();
    await page.getByRole('button', { name: 'Start simulations', exact: true }).click();
    await responseHeld;
    await page
      .locator('.session-list')
      .getByRole('button', { name: `Conversation ${second.session.split('-')[1]}`, exact: true })
      .click();
    await expect(page).toHaveURL(new RegExp(`/#/${second.session}/overview$`));
    await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toBeVisible();
    const destination = page.url();
    const refreshed = Promise.all([
      page.waitForResponse(
        (response) => response.url().endsWith(endpoint) && response.request().method() === 'GET',
      ),
      page.waitForResponse(
        (response) =>
          response.url().endsWith('/api/course') && response.request().method() === 'GET',
      ),
    ]);
    release();
    for (const response of await refreshed) await response.finished();
    await page.evaluate(
      () =>
        new Promise<void>((done) =>
          requestAnimationFrame(() => requestAnimationFrame(() => done())),
        ),
    );
    await expect(page).toHaveURL(destination);
    expect(
      (await (await request.get(`/api/workbench/sessions/${second.session}`)).json()).runs,
    ).toHaveLength(0);
    await expect
      .poll(
        async () =>
          (await (await request.get(endpoint)).json()).runs.filter(
            (run: any) => run.status === 'succeeded',
          ).length,
      )
      .toBe(1);
    expect(errors).toEqual([]);
  } finally {
    release();
  }
});

test('streaming replies, interrupt choice and Markdown tables render in the conversation pane', async ({
  page,
  request,
}) => {
  const errors = monitor(page);
  const opened = await post(request, '/api/sessions', { task: '9.1' });
  let deltaSent = false;
  let finalSent = false;
  let finish = false;
  await page.route('**/api/events*', async (route) => {
    const events = [];
    if (route.request().url().includes('last=') && !deltaSent) {
      deltaSent = true;
      events.push({
        type: 'agent.status',
        session: opened.session,
        data: { status: 'running', queued: 1 },
      });
      events.push({
        type: 'chat.delta',
        session: opened.session,
        data: { text: 'A reply is arriving as it is written.' },
      });
    } else if (finish && !finalSent) {
      finalSent = true;
      events.push({
        type: 'chat.append',
        session: opened.session,
        data: {
          entry: {
            id: 'm999999',
            role: 'agent',
            text: '| Metric | Value |\n| --- | --- |\n| Latency | **123 ms** |',
          },
        },
      });
      events.push({
        type: 'agent.status',
        session: opened.session,
        data: { status: 'idle', queued: 0 },
      });
    }
    await route.fulfill({ json: { ok: true, last: finalSent ? 2 : deltaSent ? 1 : 0, events } });
  });
  await page.goto(`/#/${opened.session}/overview`);
  await expect(page.locator('.streaming')).toContainText('A reply is arriving');
  await expect(page.locator('.conversation-agent-bar')).toContainText('1 message queued');
  await page.getByRole('checkbox', { name: 'Stop the current reply first', exact: true }).check();
  const sent = page.waitForRequest(
    (item) =>
      item.url().endsWith(`/api/workbench/sessions/${opened.session}/chat`) &&
      item.method() === 'POST',
  );
  await send(page, 'Please focus on latency.');
  expect((await sent).postDataJSON().mode).toBe('interrupt_then_send');
  finish = true;
  await expect(page.locator('.conversation-markdown-table table')).toContainText('123 ms');
  await expect(page.locator('.conversation-markdown-table strong')).toHaveText('123 ms');
  await expect(page.locator('.streaming')).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('operation summaries show recovered success and stay active for queued questions', async ({
  page,
  request,
}) => {
  const errors = monitor(page);
  const opened = await post(request, '/api/sessions', { task: '9.2' });
  const entries: Record<string, unknown>[] = [
    { id: 'm000001', role: 'student', text: 'Prepare a plan and run its simulation.' },
    {
      id: 'm000002',
      role: 'tool',
      tool: 'Explore',
      action: 'draft',
      text: 'Explore',
      result: 'A configuration needs correction.',
      is_error: true,
    },
    {
      id: 'm000003',
      role: 'tool',
      tool: 'Explore',
      action: 'draft',
      text: 'Explore',
      result: 'The corrected plan is ready.',
      is_error: false,
    },
    {
      id: 'm000004',
      role: 'tool',
      tool: 'Explore',
      action: 'simulate',
      text: 'Explore',
      result: 'The simulation was submitted.',
      is_error: false,
    },
    { id: 'm000005', role: 'agent', text: 'The corrected plan has been submitted for simulation.' },
  ];
  let agent = { status: 'idle', queued: 0, usage: {} };
  let queuedPhase = false;
  let queuedDelivered = false;
  await page.route(`**/api/sessions/${opened.session}/chat?*`, async (route) => {
    await route.fulfill({ json: { ok: true, entries, agent } });
  });
  await page.route('**/api/events*', async (route) => {
    const events = [];
    if (queuedPhase && !queuedDelivered) {
      queuedDelivered = true;
      const incoming = [
        { id: 'm000006', role: 'student', text: 'Run the next simulation.' },
        { id: 'm000007', role: 'tool', tool: 'Explore', action: 'simulate', text: 'Explore' },
        {
          id: 'm000008',
          role: 'student',
          text: 'Also explain the latency constraint when you finish.',
        },
      ];
      entries.push(...incoming);
      agent = { status: 'running', queued: 1, usage: {} };
      for (const entry of incoming)
        events.push({ type: 'chat.append', session: opened.session, data: { entry } });
      events.push({ type: 'agent.status', session: opened.session, data: agent });
    }
    await route.fulfill({ json: { ok: true, last: queuedDelivered ? 4 : 0, events } });
  });
  await page.goto(`/#/${opened.session}/overview`);
  const recovered = page.locator('.conversation-activity').first();
  await expect(recovered.locator('summary')).toHaveText('Simulation submitted');
  await recovered.locator('summary').click();
  await expect(recovered.locator('.conversation-activity-step').first()).toContainText(
    'Did not complete',
  );
  await expect(recovered.locator('.conversation-activity-step').last()).toContainText('Processed');
  queuedPhase = true;
  await expect(page.locator('.conversation-activity')).toHaveCount(2);
  await expect(page.locator('.discussion-message.student').last()).toContainText(
    'Also explain the latency constraint',
  );
  await expect(page.locator('.conversation-agent-bar')).toContainText('1 message queued');
  const pending = page.locator('.conversation-activity').last();
  await expect(pending.locator('summary')).toHaveText('Starting the simulation…');
  await pending.locator('summary').click();
  await expect(pending).toContainText('In progress');
  await expect(pending).not.toContainText('No result recorded');
  await expect(recovered.locator('summary')).toHaveText('Simulation submitted');
  expect(errors).toEqual([]);
});
