import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  workers: 1,
  outputDir: '../workspaces/ui-refactor-check/browser-results',
  use: {
    baseURL: 'http://127.0.0.1:8877',
    viewport: { width: 1440, height: 1000 },
    locale: 'en-US',
    actionTimeout: 10_000,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: `../.venv/bin/python -m mlab web ../.test-runs/browser-${process.pid}-${Date.now()} --agent fake --simulator fixture --no-browser --no-opening --port 8877`,
    url: 'http://127.0.0.1:8877/api/course',
    reuseExistingServer: false,
    timeout: 20_000,
  },
});
