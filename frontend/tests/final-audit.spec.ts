import { test, expect } from '@playwright/test';

test('slow workspace responses still render while newer polls are pending', async ({
  page,
  request,
}) => {
  const opened = await (await request.post('/api/sessions', { data: { task: '9.2' } })).json();
  await page.route(`**/api/workbench/sessions/${opened.session}`, async (route) => {
    const response = await route.fetch();
    await new Promise((resolve) => setTimeout(resolve, 3000));
    await route.fulfill({ response });
  });
  await page.goto(`/#/${opened.session}/overview`);
  await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toBeVisible({
    timeout: 7000,
  });
});

test('unavailable browser storage keeps the app usable and drafts in the current tab', async ({
  page,
  request,
}) => {
  const opened = await (await request.post('/api/sessions', { data: { task: '9.2' } })).json();
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.addInitScript(() => {
    Object.defineProperty(window, 'localStorage', {
      get() {
        throw new DOMException('Storage is disabled', 'SecurityError');
      },
    });
  });
  await page.goto(`/#/${opened.session}/overview`);
  const input = page.getByRole('textbox', { name: 'Message', exact: true });
  await expect(input).toBeVisible();
  await expect(
    page.getByText(
      'Browser storage is unavailable. Drafts are kept only while this page stays open.',
      { exact: true },
    ),
  ).toBeVisible();
  await input.fill('A question kept in this tab.');
  await page.getByRole('button', { name: 'Configure a run', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Run configuration', exact: true })).toBeVisible();
  await expect(input).toHaveValue('A question kept in this tab.');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(page.locator('.discussion-message.student')).toContainText(
    'A question kept in this tab.',
  );
  expect(errors).toEqual([]);
});

test('a saved report is available in reference suggestions before a conclusion', async ({
  page,
  request,
}) => {
  const opened = await (await request.post('/api/sessions', { data: { task: '9.2' } })).json();
  const saved = await (
    await request.post(`/api/workbench/sessions/${opened.session}/report`, { data: {} })
  ).json();
  expect(saved.ok).toBe(true);
  await page.goto(`/#/${opened.session}/overview`);
  const input = page.getByRole('textbox', { name: 'Message', exact: true });
  await input.fill('@report');
  await expect(page.locator('.discussion-context .reference-chip')).toHaveCount(1);
  await input.press('Escape');
  await page
    .locator('.discussion-context .reference-chip')
    .getByRole('button', { name: /Report/ })
    .first()
    .click();
  await expect(page).toHaveURL(/\/report\//);
  await expect(page.getByRole('heading', { name: 'Report', exact: true })).toBeVisible();
});
