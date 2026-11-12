import { test, expect } from '@playwright/test';
import words from '../../src/mlab/web/static/locales/zh.json' with { type: 'json' };

test('a student controls experiments, measurements, history and evidence discussion', async ({
  page,
}) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text());
  });
  await page.goto('/#/9.2/runs');
  await page.getByRole('button', { name: 'New experiment plan', exact: true }).click();
  await page.getByLabel('Experiment name', { exact: true }).fill('Concurrency near the SLA');
  await page
    .getByRole('textbox', { name: 'What do you want to find out?', exact: true })
    .fill('Does concurrency 20 retain enough latency margin?');
  await page
    .getByRole('textbox', { name: 'What do you expect?', exact: true })
    .fill('Higher concurrency may improve goodput and worsen the tail.');
  await page.getByLabel('Maximum concurrency', { exact: true }).fill('16');
  await page.getByRole('button', { name: 'Add a variant', exact: true }).click();
  await page.getByLabel('Configuration name', { exact: true }).fill('c20');
  await page.getByLabel('Maximum concurrency').fill('20');
  await page.getByRole('button', { name: 'Save draft', exact: true }).click();
  await expect(page.getByText('Draft saved', { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByLabel('Experiment name', { exact: true })).toHaveValue(
    'Concurrency near the SLA',
  );
  await page.getByRole('button', { name: /c20 rx9070/ }).click();
  await expect(page.getByLabel('Maximum concurrency')).toHaveValue('20');
  await page.screenshot({ path: '../workspaces/ui-refactor-check/experiment.png', fullPage: true });
  await page.getByRole('button', { name: 'Run simulations', exact: true }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).not.toBeVisible();
  await page.getByRole('button', { name: 'Run simulations', exact: true }).click();
  await page.getByRole('button', { name: 'Start simulations', exact: true }).click();
  await expect(page.locator('.runs-table tbody tr')).toHaveCount(2);
  await expect(page.locator('.runs-table').getByText('Completed', { exact: true })).toHaveCount(2);
  await page.getByLabel('Search runs', { exact: true }).fill('c20');
  await page.locator('.runs-table .row-link').click();
  await page.getByRole('button', { name: 'Requests', exact: true }).click();
  await expect(page.locator('.request-row').first()).toBeVisible();
  await page.getByLabel('Order requests', { exact: true }).selectOption('slowest');
  await page.locator('.request-row').first().click();
  await expect(page.locator('.request-inspector')).toContainText('First token at');
  await page.getByRole('button', { name: 'Observations', exact: true }).click();
  await page
    .getByLabel('Your observation', { exact: true })
    .fill('The simulated tail is close to the limit. Measure it before choosing.');
  await page.getByRole('button', { name: 'Save observation', exact: true }).click();
  await expect(page.locator('.research-note')).toContainText('Measure it before choosing.');
  await page.getByRole('button', { name: 'All runs', exact: true }).click();
  await expect(page.getByLabel('Search runs', { exact: true })).toHaveValue('c20');
  await page.getByLabel('Search runs', { exact: true }).fill('');
  await page.getByLabel('Select visible results').check();
  await page.getByRole('button', { name: 'Compare results', exact: true }).click();
  await expect(page.locator('.comparison-table')).toContainText('Maximum concurrency');
  await expect(page.locator('.comparison-table')).toContainText('20');
  await page.screenshot({ path: '../workspaces/ui-refactor-check/comparison.png', fullPage: true });
  await page.getByRole('button', { name: 'Discuss these results', exact: true }).click();
  await expect(page.locator('.discussion-context .reference-chips > span')).toHaveCount(2);
  await page.getByLabel('Message', { exact: true }).fill('What could explain the difference?');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(page.locator('.discussion-message.agent')).toContainText('offline preview');
  await page.getByRole('link', { name: 'Run history', exact: true }).click();
  await page.getByRole('button', { name: 'Arrange measurement', exact: true }).click();
  await page.getByRole('dialog').getByRole('checkbox').nth(1).check();
  await page
    .getByLabel('Why measure these configurations?', { exact: true })
    .fill('Check the small predicted margin.');
  await page.getByRole('button', { name: 'Review measurement', exact: true }).click();
  await expect(
    page.getByRole('button', { name: 'Confirm and measure', exact: true }),
  ).toBeEnabled();
  await page.screenshot({
    path: '../workspaces/ui-refactor-check/measurement.png',
    fullPage: true,
  });
  await page.getByRole('button', { name: 'Confirm and measure', exact: true }).click();
  await expect(page.locator('.runs-table tbody tr')).toHaveCount(3);
  await expect(page.locator('.runs-table').getByText('Completed', { exact: true })).toHaveCount(3);
  await expect(page.locator('.budget-card > strong')).toContainText('7');
  await page.screenshot({ path: '../workspaces/ui-refactor-check/runs.png', fullPage: true });
  await page.getByRole('button', { name: /c20 hw_0001/ }).click();
  await expect(page.getByText('Matching simulation', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Use this configuration', exact: true }).click();
  await expect(page.getByLabel('Maximum concurrency')).toHaveValue('20');
  await expect(page.getByText('Based on an earlier experiment', { exact: false })).toBeVisible();
  await page
    .getByRole('textbox', { name: 'What do you expect?', exact: true })
    .fill('An unsaved idea should survive a reload.');
  await page.reload();
  await expect(page.getByRole('textbox', { name: 'What do you expect?', exact: true })).toHaveValue(
    'An unsaved idea should survive a reload.',
  );
  await page.getByRole('button', { name: 'Arrange measurement', exact: true }).click();
  await page
    .getByLabel('Why measure these configurations?', { exact: true })
    .fill('Repeat this configuration to check the discrepancy.');
  await page.getByRole('button', { name: 'Review measurement', exact: true }).click();
  await expect(
    page.getByRole('button', { name: 'Confirm and measure', exact: true }),
  ).toBeEnabled();
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: 'Review', exact: true }).click();
  await expect(
    page.getByRole('button', { name: 'Confirm and measure', exact: true }),
  ).toBeEnabled();
  await page.getByRole('button', { name: 'Confirm and measure', exact: true }).click();
  await expect(page.locator('.runs-table').getByText('Completed', { exact: true })).toHaveCount(4);
  expect(errors).toEqual([]);
});

test('dependency workloads compare completion time and critical path without duplicate rows', async ({
  page,
}) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text());
  });
  await page.goto('/#/9.3/runs');
  await page.getByRole('button', { name: 'New experiment plan', exact: true }).click();
  await page.getByLabel('Experiment name', { exact: true }).fill('Dependency workload controls');
  await page.getByRole('button', { name: 'Add a variant', exact: true }).click();
  await page.getByLabel('Configuration name', { exact: true }).fill('dag_control');
  await page.getByRole('button', { name: 'Run simulations', exact: true }).click();
  await page.getByRole('button', { name: 'Start simulations', exact: true }).click();
  await expect(page.locator('.runs-table tbody tr')).toHaveCount(2);
  await expect(page.locator('.runs-table').getByText('Completed', { exact: true })).toHaveCount(2);
  await page.getByLabel('Select visible results').check();
  await page.getByRole('button', { name: 'Compare results', exact: true }).click();
  await expect(
    page.locator('.comparison-table td').filter({ hasText: /^Completion time/ }),
  ).toHaveCount(1);
  await expect(
    page.locator('.comparison-table td').filter({ hasText: /^Critical path time/ }),
  ).toHaveCount(1);
  expect(errors).toEqual([]);
});

test('the workbench supports Chinese, dark mode and narrow screens', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto('/#/9.3/overview');
  await page.getByRole('button', { name: 'Chinese', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: words['Workload-driven inference cluster'] }),
  ).toBeVisible();
  await page.getByRole('button', { name: words['Switch theme'], exact: true }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await page.screenshot({
    path: '../workspaces/ui-refactor-check/overview-zh-dark.png',
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.getByRole('button', { name: words['Start an experiment'], exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
  ).toBeTruthy();
  await page.screenshot({ path: '../workspaces/ui-refactor-check/mobile.png', fullPage: true });
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await expect(
    page.getByRole('button', { name: words['Start an experiment'], exact: true }),
  ).toBeVisible();
  expect(errors).toEqual([]);
});

test('one measurement can include configurations from different experiments', async ({
  page,
  request,
}) => {
  const opened = await (await request.post('/api/sessions', { data: { task: '9.2' } })).json();
  const endpoint = `/api/workbench/sessions/${opened.session}`;
  const initial = await (await request.get(endpoint)).json();
  const experiments = [];
  for (const concurrency of [16, 20]) {
    const candidate = structuredClone(
      initial.templates.find((item: any) => item.id === 'baseline'),
    );
    candidate.id = `c${concurrency}`;
    candidate.strategy.serving.max_concurrency = concurrency;
    const experiment = await (
      await request.post(`${endpoint}/create`, {
        data: {
          title: `Concurrency ${concurrency}`,
          candidates: [candidate],
          seeds: [42],
          workloads: ['development'],
          request_id: crypto.randomUUID(),
        },
      })
    ).json();
    expect(experiment.ok).toBe(true);
    const launched = await (
      await request.post(`${endpoint}/simulate`, {
        data: {
          workflow: experiment.id,
          revision: experiment.revision,
          confirm: true,
          request_id: crypto.randomUUID(),
        },
      })
    ).json();
    expect(launched.ok).toBe(true);
    experiments.push(experiment);
  }
  await expect
    .poll(
      async () =>
        (await (await request.get(endpoint)).json()).runs.filter(
          (run: any) => run.status === 'succeeded',
        ).length,
    )
    .toBe(2);
  const unrelated = await (
    await request.post(`${endpoint}/create`, {
      data: {
        title: 'Unrelated draft',
        candidates: [initial.templates.find((item: any) => item.id === 'baseline')],
        seeds: [42],
        workloads: ['development'],
        request_id: crypto.randomUUID(),
      },
    })
  ).json();
  await page.goto(`/#/${opened.session}/runs`);
  await page.getByRole('button', { name: 'Arrange measurement', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog
    .getByRole('combobox', { name: 'Experiment plan', exact: true })
    .selectOption(experiments[0].key);
  await dialog.getByRole('checkbox').check();
  await dialog
    .getByRole('button', { name: 'Add these points and choose more', exact: true })
    .click();
  await dialog
    .getByRole('combobox', { name: 'Experiment plan', exact: true })
    .selectOption(experiments[1].key);
  await dialog.getByRole('checkbox').check();
  await dialog
    .getByRole('button', { name: 'Add these points and choose more', exact: true })
    .click();
  await dialog
    .getByRole('combobox', { name: 'Experiment plan', exact: true })
    .selectOption(unrelated.key);
  await dialog.getByRole('button', { name: 'Review measurement', exact: true }).click();
  await expect(dialog.locator('.prediction-line')).toHaveCount(2);
  expect(
    (await (await request.get(endpoint)).json()).experiments.find(
      (item: any) => item.key === unrelated.key,
    ).editable,
  ).toBe(true);

  await expect(dialog).toContainText('c16');
  await expect(dialog).toContainText('c20');
  await dialog.getByRole('button', { name: 'Confirm and measure', exact: true }).click();
  await expect
    .poll(
      async () =>
        (await (await request.get(endpoint)).json()).runs.filter(
          (run: any) => run.role === 'hardware' && run.status === 'succeeded',
        ).length,
    )
    .toBe(2);
});
