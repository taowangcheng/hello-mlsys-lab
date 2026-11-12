import { test, expect, type APIRequestContext, type Page, type Request } from '@playwright/test';

async function start(page: Page, request: APIRequestContext) {
  const response = await request.post('/api/sessions', { data: { task: '9.2' } });
  const opened = await response.json();
  expect(opened.ok).toBe(true);
  await page.goto(`/#/${opened.session}/overview`);
  await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toBeVisible();
  return opened.session as string;
}

async function holdResponse(page: Page, pattern: string, matches = (_request: Request) => true) {
  let release!: () => void;
  let received!: () => void;
  let delivered!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  const accepted = new Promise<void>((resolve) => {
    received = resolve;
  });
  const completed = new Promise<void>((resolve) => {
    delivered = resolve;
  });
  let intercepted = false;
  await page.route(pattern, async (route) => {
    if (intercepted || !matches(route.request())) return route.continue();
    intercepted = true;
    const response = await route.fetch();
    received();
    await held;
    await route.fulfill({ response });
    delivered();
  });
  return { accepted, release, completed };
}

async function renderPending(page: Page) {
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
}

test('a delayed send acknowledgement preserves the next draft and its references', async ({
  page,
  request,
}) => {
  const session = await start(page, request);
  let release!: () => void;
  let received!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  const accepted = new Promise<void>((resolve) => {
    received = resolve;
  });
  await page.route(`**/api/workbench/sessions/${session}/chat`, async (route) => {
    const response = await route.fetch();
    received();
    await held;
    await route.fulfill({ response });
  });
  const composer = page.getByRole('textbox', { name: 'Message', exact: true });
  await composer.fill('Explain first-token latency.');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await accepted;
  const next = 'How does this objective differ? @overview';
  await composer.fill(next);
  await composer.press('Escape');
  release();
  await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeEnabled();
  await expect(composer).toHaveValue(next);
  await expect(page.locator('.discussion-context .reference-chip')).toHaveCount(1);
  await page.reload();
  await expect(composer).toHaveValue(next);
  await expect(page.locator('.discussion-context .reference-chip')).toHaveCount(1);
});

test('oversized messages stay editable instead of being silently shortened', async ({
  page,
  request,
}) => {
  await start(page, request);
  const composer = page.getByRole('textbox', { name: 'Message', exact: true });
  const message = 'x'.repeat(32001);
  await composer.fill(message);
  await expect(composer).toHaveValue(message);
  await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeDisabled();
  await composer.press('Enter');
  await expect(page.locator('.discussion-message.student')).toHaveCount(0);
  await expect(composer).toHaveValue(message);
  await composer.fill('A shorter question.');
  await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeEnabled();
});

test('saving a configuration preserves edits made during the request', async ({
  page,
  request,
}) => {
  const session = await start(page, request);
  await page.getByRole('button', { name: 'Configure a run', exact: true }).click();
  const input = page.getByRole('spinbutton', { name: 'Maximum concurrency', exact: true });
  await input.fill('16');
  const pending = await holdResponse(page, `**/api/workbench/sessions/${session}/save`);
  const save = page.getByRole('button', { name: 'Save draft', exact: true });
  await save.click();
  await pending.accepted;
  await input.fill('20');
  pending.release();
  await pending.completed;
  await expect(save).toBeEnabled();
  await expect(input).toHaveValue('20');
  await save.click();
  await expect(page.getByText('Draft saved', { exact: true })).toBeVisible();
  const state = await (await request.get(`/api/workbench/sessions/${session}`)).json();
  expect(state.experiments[0].candidates[0].strategy.serving.max_concurrency).toBe(20);
});

test('saving an observation preserves the next observation and reloads it', async ({
  page,
  request,
}) => {
  const session = await start(page, request);
  await page.goto(`/#/${session}/notes`);
  const input = page.getByRole('textbox', { name: 'Your observation', exact: true });
  await input.fill('My first observation.');
  const pending = await holdResponse(page, `**/api/workbench/sessions/${session}/note`);
  const save = page.getByRole('button', { name: 'Save observation', exact: true });
  await save.click();
  await pending.accepted;
  await input.fill('Another observation in progress.');
  pending.release();
  await pending.completed;
  await expect(save).toBeEnabled();
  await expect(input).toHaveValue('Another observation in progress.');
  await page.reload();
  await expect(input).toHaveValue('Another observation in progress.');
});

test('an older workspace response cannot hide a newly created configuration', async ({
  page,
  request,
}) => {
  const session = await start(page, request);
  const pending = await holdResponse(
    page,
    `**/api/workbench/sessions/${session}`,
    (request) => request.method() === 'GET',
  );
  await pending.accepted;
  await page.getByRole('button', { name: 'Configure a run', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Run configuration', exact: true })).toBeVisible();
  pending.release();
  await pending.completed;
  await renderPending(page);
  expect(
    await page.getByRole('heading', { name: 'Run configuration', exact: true }).isVisible(),
  ).toBe(true);
});

test('a delayed configuration response respects navigation within the conversation', async ({
  page,
  request,
}) => {
  const session = await start(page, request);
  const pending = await holdResponse(page, `**/api/workbench/sessions/${session}/create`);
  await page.getByRole('button', { name: 'Configure a run', exact: true }).click();
  await pending.accepted;
  await page.evaluate((session) => {
    window.location.hash = `/${session}/notes`;
  }, session);
  await expect(
    page.getByRole('heading', { name: 'Notes & conclusions', exact: true }),
  ).toBeVisible();
  const refreshed = page.waitForResponse((response) =>
    response.url().endsWith(`/api/workbench/sessions/${session}`),
  );
  pending.release();
  await pending.completed;
  await refreshed;
  await renderPending(page);
  await expect(page).toHaveURL(new RegExp(`/#/${session}/notes$`));
});

test('literal mentions stay literal and editing after Escape reopens suggestions', async ({
  page,
  request,
}) => {
  await start(page, request);
  const input = page.getByRole('textbox', { name: 'Message', exact: true });
  for (const literal of [
    'student@report.example',
    'https://example.test/@overview',
    String.raw`Write \@report literally`,
  ]) {
    await input.fill(literal);
    await expect(page.locator('.discussion-context .reference-chip')).toHaveCount(0);
    await expect(page.getByRole('listbox', { name: 'Matching records' })).toHaveCount(0);
  }
  await input.fill('@over');
  await expect(page.getByRole('listbox', { name: 'Matching records' })).toBeVisible();
  await input.press('Escape');
  await input.press('Backspace');
  await input.press('r');
  await expect(page.getByRole('listbox', { name: 'Matching records' })).toBeVisible();
});

test('observation citations open the referenced record type', async ({ page, request }) => {
  const session = await start(page, request);
  await page.getByRole('button', { name: 'Configure a run', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Run configuration', exact: true })).toBeVisible();
  const state = await (await request.get(`/api/workbench/sessions/${session}`)).json();
  const ref = state.experiments[0].key;
  expect(
    (
      await (
        await request.post(`/api/workbench/sessions/${session}/note`, {
          data: { text: 'Inspect this configuration.', refs: [ref] },
        })
      ).json()
    ).ok,
  ).toBe(true);
  await page.goto(`/#/${session}/notes`);
  await page.locator('.research-note').getByRole('button', { name: ref, exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Run configuration', exact: true })).toBeVisible();
});

test('a zero seed survives repetition changes and conclusion previews follow the current choice', async ({
  page,
  request,
}) => {
  const session = await start(page, request);
  await page.getByRole('button', { name: 'Configure a run', exact: true }).click();
  await page.getByRole('spinbutton', { name: 'Starting seed', exact: true }).fill('0');
  await page.getByRole('spinbutton', { name: 'Repetitions', exact: true }).fill('2');
  await expect(page.getByRole('spinbutton', { name: 'Starting seed', exact: true })).toHaveValue(
    '0',
  );
  await page.getByRole('button', { name: 'Add a variant', exact: true }).click();
  await page.getByRole('textbox', { name: 'Configuration name', exact: true }).fill('alternative');
  await page.getByRole('button', { name: 'Run simulations', exact: true }).click();
  await page.getByRole('button', { name: 'Start simulations', exact: true }).click();
  await expect(page.locator('.runs-table').getByText('Completed', { exact: true })).toHaveCount(4);
  const state = await (await request.get(`/api/workbench/sessions/${session}`)).json();
  expect(state.experiments[0].seeds).toEqual([0, 1]);
  await page.goto(`/#/${session}/notes`);
  await page.getByRole('button', { name: 'Submit conclusion', exact: true }).click();
  const dialog = page.getByRole('dialog');
  const selection = dialog.getByRole('combobox', { name: 'Configuration', exact: true });
  const first = `${state.experiments[0].key}/${state.experiments[0].candidates[0].id}`;
  const second = `${state.experiments[0].key}/alternative`;
  const pending = await holdResponse(page, '**/preview/conclude?*');
  await selection.selectOption(first);
  await pending.accepted;
  await selection.selectOption(second);
  await expect(dialog.locator('.review-summary')).toContainText('alternative');
  pending.release();
  await pending.completed;
  await renderPending(page);
  expect(await dialog.locator('.review-summary').innerText()).toContain('alternative');
  await dialog
    .locator('.dialog-actions')
    .getByRole('button', { name: 'Close', exact: true })
    .click();
});
