// Record real task conversations; all experiment execution is requested through the page.
import { chromium, expect } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { verifyCandidates } from './reference-experiment.mjs';

const project = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const base = process.env.MLAB_REVIEW_URL || 'http://127.0.0.1:8878';
const output = path.resolve(
  process.env.MLAB_REVIEW_DIR || path.join(project, 'workspaces/studio-review/live'),
);
const scenarios = {
  9.1: {
    selected: 'fp8_3b',
    question:
      'Before we run anything, why might a smaller model at higher precision be useful on a device with limited memory? Help me understand the tradeoff in this task in under 130 words.',
    experiment:
      'Let us try it. Create one experiment with exactly the unchanged int4_7b and fp8_3b task examples, and run both simulations on development with seed 42 only. Explain what the results show and what changing both model size and precision prevents us from concluding. Keep your reply under 130 words.',
  },
  9.2: {
    selected: 'c16',
    question:
      'Before we run anything, why can serving more requests at once hurt the latency target? Explain what I should look for in this task in under 130 words.',
    experiment:
      'Create one experiment with exactly two configurations: the unchanged baseline task example, and c16 copied from baseline with only maximum concurrency changed to 16. Run both simulations on development with seed 42 only. Compare the actual latency and throughput, keeping your reply under 130 words.',
  },
  9.3: {
    selected: 'fp8_single',
    question:
      'Before we run anything, what makes a dependency workload different from independent requests? Explain how the critical path helps me understand this task in under 130 words.',
    experiment:
      'Create one experiment with exactly two configurations: the unchanged fp8_single task example, and int4_control copied from it with only the first deployment weight precision changed to INT4. Keep everything else identical. Run both simulations on development with seed 42 only. Explain the actual results in under 130 words.',
  },
};
const tasks = process.argv.slice(2).length ? process.argv.slice(2) : Object.keys(scenarios);
if (new Set(tasks).size !== tasks.length || tasks.some((task) => !scenarios[task]))
  throw Error('Choose distinct task IDs from 9.1, 9.2 and 9.3');
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function read(url) {
  const response = await fetch(base + url);
  const result = await response.json();
  if (!response.ok || result.ok === false)
    throw Error(result.error?.message || response.statusText);
  return result;
}
async function until(check, deadline = Date.now() + 300_000) {
  while (Date.now() < deadline) {
    const result = await check();
    if (result) return result;
    await pause(1000);
  }
  throw Error('Timed out waiting for the recorded conversation');
}
function expectedCandidates(task, templates) {
  const copy = (id) => {
    const candidate = templates.find((item) => item.id === id);
    if (!candidate) throw Error(`Missing task example ${id}`);
    return structuredClone(candidate);
  };
  if (task === '9.1') return [copy('int4_7b'), copy('fp8_3b')];
  const candidates = [copy(task === '9.2' ? 'baseline' : 'fp8_single')];
  candidates.push(structuredClone(candidates[0]));
  candidates[1].id = task === '9.2' ? 'c16' : 'int4_control';
  if (task === '9.2') candidates[1].strategy.serving.max_concurrency = 16;
  else candidates[1].strategy.deployments[0].weight_precision = 'INT4';
  return candidates;
}
const course = await read('/api/course');
if (course.agent !== 'codex' || course.provider !== 'codex-soc-qwen')
  throw Error('Start a Codex server with the codex-soc-qwen profile before recording');
if (course.simulator !== 'fixture' || course.hardware !== 'standin')
  throw Error('This demonstration expects fixture simulation and the hardware stand-in');
await mkdir(output, { recursive: true });
const summaries = [];
const browser = await chromium.launch({ slowMo: 100 });
try {
  for (const task of tasks) {
    const spec = scenarios[task];
    const folder = path.join(output, `task-${task}`);
    await mkdir(folder, { recursive: true });
    let session = null;
    let previousSession = null;
    const context = await browser.newContext({
      viewport: { width: 1600, height: 1050 },
      locale: 'en-US',
      recordVideo: { dir: folder, size: { width: 1600, height: 1050 } },
    });
    await context.addInitScript(() => localStorage.setItem('mlab-language', 'en'));
    const page = await context.newPage();
    page.setDefaultTimeout(20_000);
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('console', (message) => {
      if (message.type() === 'error') errors.push(message.text());
    });
    const view = () => read(`/api/workbench/sessions/${session || previousSession}`);
    const chat = () => read(`/api/sessions/${session}/chat?limit=500`);
    const screenshot = async (name) => {
      await pause(1200);
      await page.screenshot({ path: path.join(folder, `${name}.png`), fullPage: true });
    };
    let outcome = 'incomplete';
    let failure = null;
    let initialBudget;
    let experiment;
    async function exportArtifacts() {
      if (!session) {
        const summary = { task, session, previousSession, outcome, failure, browserErrors: errors };
        await writeFile(path.join(folder, 'conversation.json'), '[]\n');
        await writeFile(
          path.join(folder, 'conversation.md'),
          `# Task ${task}\n\nNo new conversation was opened.\n`,
        );
        await writeFile(path.join(folder, 'summary.json'), JSON.stringify(summary, null, 2));
        return summary;
      }
      const latest = await chat();
      let entries = latest.entries;
      let previous = latest;
      while (previous.entries.length === 500) {
        previous = await read(
          `/api/sessions/${session}/chat?limit=500&before=${encodeURIComponent(previous.entries[0].id)}`,
        );
        entries = [...previous.entries, ...entries];
      }
      const messages = entries.filter((entry) =>
        ['student', 'agent', 'error', 'card'].includes(entry.role),
      );
      await writeFile(path.join(folder, 'conversation.json'), JSON.stringify(messages, null, 2));
      const prose = messages.map((entry) => {
        const author = {
          student: 'Student',
          agent: 'Assistant',
          error: 'Error',
          card: 'Action card',
        }[entry.role];
        const refs = entry.refs?.length ? `\n\nReferences: ${entry.refs.join(', ')}` : '';
        const text =
          entry.role === 'card'
            ? `State: ${entry.state}\n\n\`\`\`json\n${JSON.stringify(entry, null, 2)}\n\`\`\``
            : entry.text || '';
        return `## ${author}\n\n${text}${refs}\n`;
      });
      await writeFile(
        path.join(folder, 'conversation.md'),
        `# Task ${task}: real SoC conversation\n\nModel: qwen3.8:27b. Codex: 0.167.2. Performance data is synthetic.\n\n${prose.join('\n')}`,
      );
      const state = await view();
      const summary = {
        task,
        session,
        previousSession,
        outcome,
        failure,
        provider: course.provider,
        model: 'qwen3.8:27b',
        modelLabel: course.model_label,
        codex: '0.167.2',
        simulator: course.simulator,
        hardware: course.hardware,
        experiment: experiment?.key,
        selected: spec.selected,
        initialBudget,
        budget: state.budget,
        usage: latest.agent.usage,
        runs: state.runs.filter((run) => run.session === session),
        experiments: state.experiments.filter((item) => item.session === session),
        cards: messages.filter((entry) => entry.role === 'card'),
        browserErrors: errors,
        capturedAt: new Date().toISOString(),
      };
      await writeFile(path.join(folder, 'summary.json'), JSON.stringify(summary, null, 2));
      return summary;
    }
    async function send(text) {
      const before = (await chat()).agent.usage.turns;
      const deadline = Date.now() + 300_000;
      await page.getByRole('textbox', { name: 'Message', exact: true }).fill(text);
      await page.getByRole('button', { name: 'Send', exact: true }).click();
      return { before, deadline };
    }
    async function waitIdle(turn) {
      await until(async () => {
        const result = await chat();
        if (result.agent.status === 'error')
          throw Error(result.entries.filter((entry) => entry.role === 'error').at(-1)?.text);
        return result.agent.usage.turns > turn.before && result.agent.status === 'idle';
      }, turn.deadline);
      await exportArtifacts();
    }
    async function speak(text, name) {
      console.log(`${task}: ${name}, waiting for real SoC reply`);
      await waitIdle(await send(text));
      await screenshot(name);
    }
    try {
      console.log(`${task}: opening a new conversation through the page`);
      await page.goto(`${base}/#/${task}/overview`);
      await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toBeVisible();
      previousSession = new URL(page.url()).hash.split('/')[1];
      const existing = await view();
      initialBudget = existing.budget;
      if (initialBudget.remaining < 1) throw Error('Start a fresh course with measurement budget');
      await screenshot('00-existing-conversation');
      await page.getByRole('button', { name: 'New conversation', exact: true }).click();
      session = await until(async () => {
        const selected = new URL(page.url()).hash.split('/')[1];
        if (!selected || selected === previousSession) return false;
        const state = await read(`/api/workbench/sessions/${selected}`);
        return state.session === selected ? selected : false;
      }, Date.now() + 30_000);
      const opened = await view();
      if (
        opened.experiments.some((item) => item.session === session) ||
        opened.runs.some((run) => run.session === session) ||
        opened.budget.used !== initialBudget.used
      )
        throw Error('A new conversation inherited evidence or changed the measurement budget');
      const preserved = await read(`/api/workbench/sessions/${previousSession}`);
      if (
        preserved.runs.length !== existing.runs.length ||
        preserved.experiments.length !== existing.experiments.length
      )
        throw Error('Earlier conversation was not preserved');
      await expect(page.locator('.discussion-message.student')).toHaveCount(0);
      console.log(`${task}: ${previousSession} preserved; recording new conversation ${session}`);
      await screenshot('01-conversation');
      await speak(spec.question, '02-conceptual-question');
      let state = await view();
      if (
        state.experiments.some((item) => item.session === session) ||
        state.runs.some((run) => run.session === session)
      )
        throw Error('A conceptual question unexpectedly created or executed an experiment');
      await speak(spec.experiment, '03-requested-simulation');
      state = await until(async () => {
        const value = await view();
        const runs = value.runs.filter((run) => run.session === session);
        if (runs.some((run) => ['failed', 'cancelled', 'timed_out'].includes(run.status)))
          throw Error('A requested simulation failed');
        return runs.length >= 2 && runs.every((run) => run.status === 'succeeded') ? value : false;
      });
      const experiments = state.experiments.filter((item) => item.session === session);
      if (experiments.length !== 1) throw Error('Expected one two-configuration experiment');
      experiment = experiments[0];
      verifyCandidates(expectedCandidates(task, state.templates), experiment.candidates);
      const simulations = state.runs.filter((run) => run.session === session);
      if (
        simulations.length !== 2 ||
        simulations.some(
          (run) => run.role !== 'simulator' || run.workload !== 'development' || run.seed !== 42,
        )
      )
        throw Error('The simulations did not match the requested two development runs');
      const selected = simulations.find((run) => run.candidate === spec.selected);
      if (!selected) throw Error('The selected configuration has no simulation result');
      await page.getByRole('link', { name: 'Run history', exact: true }).click();
      await page.getByLabel('Select visible results').check();
      await page.getByRole('button', { name: 'Compare results', exact: true }).click();
      await expect(page.locator('.comparison-table')).toBeVisible();
      await screenshot('03a-compare-and-conversation');
      await page.getByRole('textbox', { name: 'Message', exact: true }).fill(`@${selected.id}`);
      const picker = page.getByRole('listbox', { name: 'Matching records', exact: true });
      await expect(picker).toBeVisible();
      await expect(picker.getByRole('option')).toHaveCount(1);
      await screenshot('04-inline-reference');
      await picker.getByRole('option').click();
      await speak(
        `@${selected.id} Help me understand this result. Read its actual metrics and compare it with the other configuration we just ran. What can I conclude, and what remains uncertain? Cite the results and keep your reply under 130 words.`,
        '05-evidence-discussion',
      );
      const attached = (await chat()).entries.filter((entry) => entry.role === 'student').at(-1);
      if (!attached.refs?.includes(selected.key))
        throw Error('The history attachment was not saved');
      console.log(`${task}: requesting one sandbox measurement`);
      const turn = await send(
        `Please run exactly one sandbox measurement of ${spec.selected} from our experiment on the development workload, with seed 42. Then compare it with its simulation and explain what it tells us in under 130 words.`,
      );
      const pending = await until(async () => {
        const result = await chat();
        if (result.agent.status === 'error') throw Error('The measurement request failed');
        const card = result.entries.find(
          (entry) =>
            entry.role === 'card' && entry.card === 'execution' && entry.state === 'pending',
        );
        if (!card && result.agent.usage.turns > turn.before && result.agent.status === 'idle')
          throw Error('The assistant finished without proposing the requested measurement');
        return card;
      }, turn.deadline);
      const planned = pending.summary;
      if (
        planned.session !== session ||
        planned.hardware_runs !== 1 ||
        planned.points.length !== 1 ||
        planned.points[0].candidate !== spec.selected ||
        planned.points[0].workload !== 'development' ||
        planned.points[0].runs !== 1 ||
        planned.points[0].seed !== 42
      )
        throw Error('The measurement card differs from the student request');
      verifyCandidates(
        [experiment.candidates.find((item) => item.id === spec.selected)],
        planned.configs,
      );
      if ((await view()).runs.some((run) => run.session === session && run.role === 'hardware'))
        throw Error('A measurement started before confirmation');
      const card = page.locator('.conversation-action-card.pending');
      await expect(card).toHaveCount(1);
      await card.getByText('Configuration details', { exact: true }).click();
      await exportArtifacts();
      await screenshot('06-measurement-confirmation');
      await card.getByText('Configuration details', { exact: true }).click();
      await card.getByRole('button', { name: 'Run measurement', exact: true }).click();
      await waitIdle(turn);
      state = await until(async () => {
        const value = await view();
        const runs = value.runs.filter((run) => run.session === session && run.role === 'hardware');
        if (runs.some((run) => ['failed', 'cancelled', 'timed_out'].includes(run.status)))
          throw Error('The requested measurement failed');
        return runs.length === 1 && runs[0].status === 'succeeded' ? value : false;
      });
      if (state.budget.used !== initialBudget.used + 1)
        throw Error('The measurement spent an unexpected amount of budget');
      await screenshot('07-measurement-discussion');
      await page.getByRole('link', { name: 'Run history', exact: true }).click();
      await expect(page.locator('.runs-table')).toBeVisible();
      await screenshot('08-execution-history');
      await page.getByRole('button', { name: 'Record directory', exact: false }).click();
      await page
        .locator('.record-directory-items')
        .getByRole('button')
        .filter({ hasText: planned.measurement })
        .click();
      await expect(page.locator('.records-view')).toBeVisible();
      await screenshot('09-measurement-analysis');
      await page.getByRole('link', { name: 'Report', exact: true }).click();
      await expect(page.locator('.record-report')).toBeVisible();
      await page.getByRole('button', { name: 'Save report', exact: true }).click();
      await expect(
        page.getByRole('button', { name: 'Update saved report', exact: true }),
      ).toBeVisible();
      await screenshot('09a-report-and-conversation');
      await page.setViewportSize({ width: 390, height: 844 });
      await page.getByRole('button', { name: 'Conversation', exact: true }).click();
      await screenshot('10-mobile-conversation');
      await page.setViewportSize({ width: 1600, height: 1050 });
      await page.getByRole('button', { name: 'New conversation', exact: true }).click();
      await expect(page.locator('.discussion-message')).toHaveCount(0);
      await expect(
        page.getByRole('button', { name: 'Configure a run', exact: true }),
      ).toBeVisible();
      await screenshot('11-clean-new-conversation');
      await page
        .getByRole('button', { name: `Conversation ${session.split('-')[1]}`, exact: true })
        .click();
      await expect(page.locator('.record-report')).toBeVisible();
      await screenshot('12-restored-conversation');
      if (errors.length) throw Error(`Browser errors: ${errors.join('; ')}`);
      outcome = 'complete';
      console.log(`${task}: complete; two requested simulations and one confirmed measurement`);
    } catch (error) {
      failure = String(error);
      outcome = 'failed';
      await screenshot('failure').catch(() => {});
      await writeFile(path.join(folder, 'failure.txt'), failure);
      throw error;
    } finally {
      try {
        summaries.push(await exportArtifacts());
      } finally {
        const video = page.video();
        await context.close();
        if (video) await video.saveAs(path.join(folder, 'walkthrough.webm'));
        await writeFile(path.join(output, 'summary.json'), JSON.stringify(summaries, null, 2));
      }
    }
  }
} finally {
  await browser.close();
}
