// Capture real model conversations and student-controlled executions in all tasks.
// Start scripts/start-soc.sh on port 8878 first; credentials never enter this script.
import { chromium, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const base = process.env.MLAB_REVIEW_URL || 'http://127.0.0.1:8878';
const project = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const output = path.resolve(
  process.env.MLAB_REVIEW_DIR || path.join(project, 'workspaces/all-tasks-review/live'),
);
const tasks = {
  9.1: {
    title: 'Model size and precision on one GPU',
    selected: 'fp8_3b',
    prompt:
      'Help me compare two on-device designs: the task examples int4_7b and fp8_3b. Create one editable experiment draft with exactly those two configurations, using their example settings unchanged. Name the experiment Model size and precision on one GPU. I will choose the workloads and run it myself. Briefly explain what this comparison can and cannot tell us; it changes both model size and precision. Keep your reply under 140 words.',
  },
  9.2: {
    title: 'Concurrency near the latency limit',
    selected: 'c16',
    prompt:
      'Help me compare concurrency 16 and 20 under this task SLA. Create one editable experiment draft with exactly two configurations, c16 and c20. Copy the task baseline configuration and change only maximum concurrency to 16 and 20 respectively. Name the experiment Concurrency near the latency limit. I will choose workloads and run it myself. Briefly explain the expected tradeoff; keep your reply under 140 words.',
  },
  9.3: {
    title: 'Precision along the dependency path',
    selected: 'fp8_single',
    prompt:
      'Help me compare precision on the large model in this dependency workload. Create one editable experiment draft with exactly two configurations: the task example fp8_single unchanged, and int4_control copied from fp8_single with only the first deployment weight precision changed to INT4. Keep context lengths, placement, replicas and scheduling identical. Name the experiment Precision along the dependency path. I will choose workloads and run it myself. Briefly explain what to inspect in the critical path; keep your reply under 140 words.',
  },
};
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function api(url, body) {
  const response = await fetch(
    base + url,
    body
      ? {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        }
      : {},
  );
  const result = await response.json();
  if (!response.ok || result.ok === false)
    throw Error(result.error?.message || response.statusText);
  return result;
}
async function until(check, limit = 180_000) {
  const end = Date.now() + limit;
  while (Date.now() < end) {
    const value = await check();
    if (value) return value;
    await pause(1000);
  }
  throw Error('Timed out waiting for the recorded operation');
}
function verifyConfigurations(task, templates, candidates) {
  const copy = (id) => structuredClone(templates.find((candidate) => candidate.id === id));
  let expected;
  if (task === '9.1') expected = [copy('int4_7b'), copy('fp8_3b')];
  else if (task === '9.2')
    expected = [16, 20].map((concurrency) => {
      const candidate = copy('baseline');
      candidate.id = `c${concurrency}`;
      candidate.strategy.serving.max_concurrency = concurrency;
      return candidate;
    });
  else {
    expected = [copy('fp8_single'), copy('fp8_single')];
    expected[1].id = 'int4_control';
    expected[1].strategy.deployments[0].weight_precision = 'INT4';
  }
  // Compare effective settings so omitted schema defaults do not mask extra changes.
  execFileSync(
    path.join(project, '.venv/bin/python'),
    [
      '-c',
      [
        'import json, sys',
        'from mlab.contracts import Candidate',
        'def normalize(items):',
        '    result = {}',
        '    for item in items:',
        '        value = Candidate.model_validate(item).model_dump(mode="json")',
        '        result[value["id"]] = {key: value[key] for key in ("infra", "strategy")}',
        '    return result',
        'expected, actual = json.load(sys.stdin)',
        'assert normalize(expected) == normalize(actual), "The proposed settings differ from the requested comparison"',
      ].join('\n'),
    ],
    { input: JSON.stringify([expected, candidates]), cwd: project },
  );
}
const summaries = [];
const course = await api('/api/course');
if (course.agent !== 'codex' || course.provider !== 'codex-soc-qwen') {
  throw Error('This recorder requires a Codex server using the codex-soc-qwen profile');
}
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ slowMo: 100 });
try {
  for (const task of process.argv.slice(2).length ? process.argv.slice(2) : Object.keys(tasks)) {
    const spec = tasks[task];
    const folder = path.join(output, `task-${task}`);
    await mkdir(folder, { recursive: true });
    const { session } = await api('/api/sessions', { task });
    const context = await browser.newContext({
      viewport: { width: 1600, height: 1050 },
      locale: 'en-US',
      recordVideo: { dir: folder, size: { width: 1600, height: 1050 } },
    });
    await context.addInitScript(() => {
      localStorage.setItem('mlab-language', 'en');
    });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('console', (message) => {
      if (message.type() === 'error') errors.push(message.text());
    });
    page.setDefaultTimeout(15_000);
    const view = () => api(`/api/workbench/tasks/${task}`);
    const screenshot = async (name) => {
      await pause(1000);
      await page.screenshot({ path: path.join(folder, `${name}.png`), fullPage: true });
    };
    async function saveConversation() {
      const chat = await api(`/api/sessions/${session}/chat?limit=1000`);
      const messages = chat.entries.filter((e) => ['student', 'agent', 'error'].includes(e.role));
      await writeFile(path.join(folder, 'conversation.json'), JSON.stringify(messages, null, 2));
      await writeFile(
        path.join(folder, 'conversation.md'),
        `# Task ${task}: real SoC Qwen conversation\n\nModel: qwen3.8:27b. Codex: 0.167.2. Synthetic performance data.\n\n` +
          messages
            .map(
              (m) =>
                `## ${m.role === 'student' ? 'Student' : m.role === 'agent' ? 'Assistant' : 'Error'}\n\n${m.text}\n`,
            )
            .join('\n'),
      );
      return chat;
    }
    async function speak(text, name) {
      const before = (await api(`/api/sessions/${session}/chat?limit=1000`)).agent.usage.turns;
      await page.getByRole('textbox', { name: 'Message', exact: true }).fill(text);
      await page.getByRole('button', { name: 'Send', exact: true }).click();
      console.log(`${task}: waiting for real model (${name})`);
      await until(async () => {
        const chat = await api(`/api/sessions/${session}/chat?limit=1000`);
        if (chat.agent.status === 'error')
          throw Error(chat.entries.filter((e) => e.role === 'error').at(-1)?.text || 'Model error');
        return chat.agent.usage.turns > before && chat.agent.status === 'idle';
      }, 300_000);
      await pause(1600);
      await saveConversation();
      await screenshot(name);
      console.log(`${task}: model turn completed (${name})`);
    }
    try {
      console.log(`${task}: starting ${session}`);
      const initial = await view();
      if (initial.budget.remaining < initial.overview.holdouts.length + 2)
        throw Error('Not enough task budget remains; start a fresh review course');
      await page.goto(`${base}/#/${task}/overview`);
      await expect(
        page.getByRole('button', { name: 'Start an experiment', exact: true }),
      ).toBeVisible();
      await screenshot('01-overview');
      await page.getByRole('button', { name: 'Discuss', exact: true }).click();
      await speak(spec.prompt, '02-agent-proposal');
      let state = await view();
      const experiment = state.experiments
        .filter((e) => e.session === session && e.author === 'assistant')
        .at(-1);
      if (
        !experiment ||
        experiment.candidates.length !== 2 ||
        !experiment.candidates.some((c) => c.id === spec.selected)
      )
        throw Error('The model did not provide the requested two-configuration draft');
      verifyConfigurations(task, state.templates, experiment.candidates);
      if (state.runs.some((r) => r.session === session))
        throw Error('An experiment ran before the student confirmed it');
      await page.getByRole('button', { name: 'Close discussion', exact: true }).click();
      await page.goto(`${base}/#/${task}/plans/${encodeURIComponent(experiment.key)}`);
      await page
        .getByRole('textbox', { name: 'What do you expect?', exact: true })
        .fill(
          'I will compare the designs across workloads, then measure the selected design. These results are demonstration evidence.',
        );
      await page.getByLabel('Repetitions', { exact: true }).fill('2');
      for (const checkbox of await page.locator('.workload-options input').all())
        await checkbox.check();
      await page.getByRole('button', { name: 'Save draft', exact: true }).click();
      await expect(page.getByText('Draft saved', { exact: true })).toBeVisible();
      await screenshot('03-student-experiment');
      await page.getByRole('button', { name: 'Run simulations', exact: true }).click();
      await page.getByRole('button', { name: 'Start simulations', exact: true }).click();
      state = await until(async () => {
        const v = await view();
        const runs = v.runs.filter((r) => r.session === session);
        return runs.length &&
          runs.every((r) => ['succeeded', 'failed', 'cancelled', 'timed_out'].includes(r.status))
          ? v
          : false;
      });
      if (state.runs.filter((r) => r.session === session).some((r) => r.status !== 'succeeded'))
        throw Error('A simulation failed');
      await screenshot('04-simulation-history');
      const firstRuns = state.runs.filter(
        (r) => r.session === session && r.workload === 'development' && r.repeat === 0,
      );
      for (const run of firstRuns)
        await page.getByLabel(`Select ${run.id}`, { exact: true }).check();
      await page.getByRole('button', { name: 'Compare results', exact: true }).click();
      await expect(page.locator('.comparison-table')).toBeVisible();
      await screenshot('05-comparison');
      await page.getByRole('button', { name: 'Discuss these results', exact: true }).click();
      await speak(
        'Compare the selected results using their actual metrics and configuration changes. Which tradeoff matters for this task? Explain one limitation and one useful measurement to make next. Cite the run IDs and keep it under 180 words.',
        '06-agent-analysis',
      );
      await page.getByRole('button', { name: 'Close discussion', exact: true }).click();
      const preferred = firstRuns.find((r) => r.candidate === spec.selected);
      await page.goto(`${base}/#/${task}/runs/${encodeURIComponent(preferred.key)}`);
      await page.getByRole('button', { name: 'Requests', exact: true }).click();
      await expect(page.locator('.request-row').first()).toBeVisible();
      await page.getByLabel('Order requests', { exact: true }).selectOption('slowest');
      await page.locator('.request-row').first().click();
      await screenshot('07-request-inspection');
      const workloads = ['development', ...state.overview.holdouts];
      for (let i = 0; i < workloads.length; i++) {
        const beforeRuns = (await view()).runs.filter(
          (r) => r.session === session && r.role === 'hardware',
        ).length;
        await page.goto(`${base}/#/${task}/runs`);
        await page.getByRole('button', { name: 'Arrange measurement', exact: true }).click();
        await page
          .getByRole('dialog')
          .getByRole('combobox', { name: 'Experiment', exact: true })
          .selectOption(experiment.key);
        await page
          .locator('.check-list label')
          .filter({
            has: page.locator('strong').filter({ hasText: new RegExp(`^${spec.selected}$`) }),
          })
          .getByRole('checkbox')
          .check();
        await page
          .getByRole('dialog')
          .getByRole('combobox', { name: 'Workload', exact: true })
          .selectOption(workloads[i]);
        await page
          .getByRole('dialog')
          .getByLabel('Repetitions', { exact: true })
          .fill(i === 0 ? '2' : '1');
        await page
          .getByRole('textbox', { name: 'Why measure these configurations?', exact: true })
          .fill(
            'Check whether the selected design and its margin hold across repetitions and workload changes.',
          );
        await page.getByRole('button', { name: 'Review measurement', exact: true }).click();
        await expect(
          page.getByRole('button', { name: 'Confirm and measure', exact: true }),
        ).toBeEnabled({ timeout: 60_000 });
        if (i === 0) await screenshot('08-measurement-review');
        await page.getByRole('button', { name: 'Confirm and measure', exact: true }).click();
        await until(async () => {
          const v = await view();
          const rows = v.runs.filter((r) => r.session === session && r.role === 'hardware');
          const failed = rows.filter((r) =>
            ['failed', 'cancelled', 'timed_out'].includes(r.status),
          );
          if (failed.length)
            throw Error(
              `Measurement failed: ${failed.map((r) => `${r.id}: ${r.status}`).join(', ')}`,
            );
          return (
            rows.length === beforeRuns + (i === 0 ? 2 : 1) &&
            rows.every((r) => r.status === 'succeeded')
          );
        });
      }
      state = await view();
      await screenshot('09-complete-history');
      await page.goto(`${base}/#/${task}/compare`);
      await page.getByRole('button', { name: 'Clear selection', exact: true }).click();
      const hardware = state.runs.find((r) => r.session === session && r.role === 'hardware');
      await page.goto(`${base}/#/${task}/runs/${encodeURIComponent(hardware.key)}`);
      await expect(
        page.getByRole('button', { name: 'Compare this pair', exact: true }),
      ).toBeVisible();
      await page.getByRole('button', { name: 'Compare this pair', exact: true }).click();
      await page.getByRole('button', { name: 'Discuss these results', exact: true }).click();
      await speak(
        `Review all simulation and measurement evidence for ${spec.selected}, including ${state.runs
          .filter((r) => r.session === session && r.role === 'hardware')
          .map((r) => `${r.id} (${r.workload}, repeat ${r.repeat})`)
          .join(
            ', ',
          )}. Read every listed measurement before deciding whether validation is missing; the selected pair is only one part of the experiment. Does this design meet the task requirements? Explain the gap and remaining uncertainty, distinguish demonstration data from real GPU evidence, and cite run IDs. Keep it under 200 words.`,
        '10-agent-measurement-review',
      );
      await page.getByRole('button', { name: 'Close discussion', exact: true }).click();
      await page.goto(`${base}/#/${task}/notes`);
      const note = `I select ${spec.selected} after checking development, repeat and all provided holdout workloads. I compared the configuration and per-request evidence. All measurements here are synthetic stand-in data; this does not establish real hardware performance.`;
      await page.getByRole('textbox', { name: 'Your observation', exact: true }).fill(note);
      await page.getByRole('button', { name: 'Save observation', exact: true }).click();
      await expect(page.locator('.research-note')).toContainText(spec.selected);
      await page.getByRole('button', { name: 'Submit conclusion', exact: true }).click();
      await page
        .getByRole('dialog')
        .getByRole('combobox', { name: 'Configuration', exact: true })
        .selectOption(`${experiment.key}/${spec.selected}`);
      await expect(
        page.getByRole('dialog').getByRole('button', { name: 'Submit conclusion', exact: true }),
      ).toBeEnabled();
      await page
        .getByRole('dialog')
        .getByRole('button', { name: 'Submit conclusion', exact: true })
        .click();
      await expect(
        page.getByRole('heading', { name: 'Submitted conclusions', exact: true }),
      ).toBeVisible();
      await screenshot('11-conclusion');
      state = await view();
      if (state.conclusions.filter((c) => c.session === session).at(-1)?.conclusion !== 'mock_only')
        throw Error('The task did not reach a fully validated demonstration conclusion');
      const chat = await saveConversation();
      const meta = {
        task,
        session,
        experiment: experiment.key,
        selected: spec.selected,
        provider: 'SoCLaaS',
        model: 'qwen3.8:27b',
        modelLabel: course.model_label,
        codex: '0.167.2',
        simulator: course.simulator,
        hardware: course.hardware,
        usage: chat.agent.usage,
        budget: state.budget,
        conclusions: state.conclusions.filter((c) => c.session === session),
        runs: state.runs.filter((r) => r.session === session),
        browserErrors: errors,
        capturedAt: new Date().toISOString(),
      };
      await writeFile(path.join(folder, 'evidence.json'), JSON.stringify(meta, null, 2));
      if (errors.length) throw Error(`Browser errors: ${errors.join('; ')}`);
      summaries.push(meta);
      console.log(
        `${task}: complete, ${meta.runs.length} runs, conclusion ${meta.conclusions.at(-1)?.conclusion}`,
      );
    } catch (error) {
      await screenshot('failure').catch(() => {});
      await saveConversation().catch(() => {});
      await writeFile(path.join(folder, 'failure.txt'), String(error));
      throw error;
    } finally {
      const video = page.video();
      await context.close();
      if (video) await video.saveAs(path.join(folder, 'walkthrough.webm'));
    }
  }
} finally {
  await browser.close();
  await writeFile(path.join(output, 'summary.json'), JSON.stringify(summaries, null, 2));
}
