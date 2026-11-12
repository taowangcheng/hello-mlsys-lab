// Record real student exploration through the UI, with read-only API verification.
// Start scripts/start-soc.sh with a fresh course and --no-opening before recording.
import { chromium, expect } from '@playwright/test';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { verifyCandidates } from './reference-experiment.mjs';

const project = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const base = process.env.MLAB_REVIEW_URL || 'http://127.0.0.1:8878';
const output = path.resolve(
  process.env.MLAB_REVIEW_DIR ||
    path.join(project, 'workspaces/exploration-demo-2026-11-13/videos'),
);
const words = JSON.parse(await readFile(path.join(project, 'src/mlab/web/static/locales/zh.json')));
const t = (key, values = {}) =>
  Object.entries(values).reduce(
    (text, [name, value]) => text.replaceAll(`{${name}}`, value),
    words[key] || key,
  );
const scenarios = {
  9.2: {
    selected: 'c16',
    question:
      '先不运行。我想弄明白：并发数增加，吞吐量和首 token 延迟为什么不一定一起改善？结合这个任务的目标解释一下，我应该重点观察哪些指标？请用中文，控制在 220 字左右。',
    experiment:
      '先做一个小实验：采用任务给定的 baseline 原配置，再复制一份命名为 c16，只把最大并发数改为 16，其他参数完全不变。把它们放在同一个方案里，分别运行 development 模拟，种子均为 42，不运行其他负载。请读完结果再用中文简要比较。',
    observation:
      '我选择 c16 继续验证。提高并发数需要同时查看吞吐量和延迟余量，不能只看模拟器中的吞吐量排名。我检查了开发负载、重复运行和两种保留负载，也对比了相同配置的模拟与沙盒结果。这次的数据来自演示后端，尚不能证明真实 GPU 上满足 SLA；下一步应在真实设备上补做测量。',
  },
  9.1: {
    selected: 'fp8_3b',
    question:
      '先不做实验。这个任务的设备内存有限，为什么更小的 FP8 模型可能比更大的 INT4 模型更合适？模型质量、内存和响应时间应该怎样一起考虑？请用中文，控制在 220 字左右。',
    experiment:
      '帮我建立一个比较方案，使用任务示例中未经改动的 int4_7b 和 fp8_3b。先分别运行 development 模拟，种子均为 42。读完结果后，说明各自的取舍；同时改变了模型大小和精度，能否把性能差异全归因于精度？请用中文简要回答。',
    observation:
      '我选择 fp8_3b。这个比较同时改变了模型大小和精度，所以不能把差异全部归因于量化方式。我核对了内存、响应时间和质量约束，并补做重复运行与另一组提示词的验证。这些演示数据只能支持当前练习中的选择，真实部署前仍需在目标设备上测量性能并评估质量。',
  },
  9.3: {
    selected: 'fp8_single',
    question:
      '先不运行。这个任务里，请求存在依赖关系，它和普通的独立请求批处理有什么区别？关键路径怎样影响总完成时间？请结合任务用中文解释，控制在 220 字左右。',
    experiment:
      '做一个控制变量的比较：使用原始 fp8_single，再复制为 int4_control，只把第一个 deployment 的 weight_precision 改成 INT4，其他参数全部保持相同。放在同一个方案，分别运行 development 模拟，种子均为 42。请核对完成情况、质量和总完成时间，再用中文解释结果。',
    observation:
      '我选择 fp8_single。依赖任务需要先确认所有请求完成，再比较总完成时间和关键路径。本次对照仅调整了第一个模型的权重精度；观察到差异并不能单独证明某种底层机制。我补做了重复运行和两种保留负载验证。所有性能数据均来自演示后端，真实设备上的调度与通信行为仍需实测。',
  },
};
const tasks = process.argv.slice(2).length ? process.argv.slice(2) : ['9.2', '9.1', '9.3'];
if (new Set(tasks).size !== tasks.length || tasks.some((task) => !scenarios[task]))
  throw Error('Choose distinct task IDs from 9.1, 9.2 and 9.3');
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function read(url) {
  const response = await fetch(base + url);
  const value = await response.json();
  if (!response.ok || value.ok === false) throw Error(value.error?.message || response.statusText);
  return value;
}
async function until(check, deadline = Date.now() + 360_000) {
  while (Date.now() < deadline) {
    const value = await check();
    if (value) return value;
    await pause(1000);
  }
  throw Error('Timed out waiting for the recorded operation');
}
function expectedCandidates(task, templates) {
  const copy = (id) => structuredClone(templates.find((item) => item.id === id));
  if (task === '9.1') return [copy('int4_7b'), copy('fp8_3b')];
  const first = copy(task === '9.2' ? 'baseline' : 'fp8_single');
  const second = structuredClone(first);
  second.id = task === '9.2' ? 'c16' : 'int4_control';
  if (task === '9.2') second.strategy.serving.max_concurrency = 16;
  else second.strategy.deployments[0].weight_precision = 'INT4';
  return [first, second];
}
const course = await read('/api/course');
if (course.agent !== 'codex' || course.provider !== 'codex-soc-qwen')
  throw Error('A live Codex server using the codex-soc-qwen profile is required');
if (course.simulator !== 'fixture' || course.hardware !== 'standin')
  throw Error('This recording expects explicitly synthetic performance backends');
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ slowMo: 140 });
try {
  for (const task of tasks) {
    const spec = scenarios[task];
    const folder = path.join(output, `task-${task}`);
    await mkdir(folder, { recursive: true });
    const resumeSession = process.env.MLAB_DEMO_RESUME_SESSION;
    const resuming = resumeSession?.startsWith(`${task}-`);
    if (!resuming) {
      try {
        const saved = JSON.parse(await readFile(path.join(folder, 'evidence.json')));
        if (saved.outcome === 'complete')
          throw Error('A completed recording already exists; choose a fresh MLAB_REVIEW_DIR');
      } catch (error) {
        if (error.code !== 'ENOENT') throw error;
      }
    }
    const reviewOnly = resuming && process.env.MLAB_DEMO_RESUME_STAGE === 'review';
    const previous = resuming
      ? JSON.parse(await readFile(path.join(folder, 'evidence.json')))
      : null;
    const offset = resuming ? Number(process.env.MLAB_DEMO_OFFSET_SECONDS) : 0;
    if (resuming && (!Number.isFinite(offset) || offset <= 0 || previous.session !== resumeSession))
      throw Error('A resumed recording needs its existing session and prior video duration');
    if (resuming && !reviewOnly && task !== '9.2')
      throw Error('Measurement recovery is only supported for task 9.2');
    const videoFile = reviewOnly
      ? `part-${String(previous.segments.length + 1).padStart(2, '0')}.webm`
      : resuming
        ? 'part-02.webm'
        : 'walkthrough.webm';
    const segments = reviewOnly
      ? [
          ...previous.segments.map((segment) =>
            segment.file === 'walkthrough.webm'
              ? { file: 'part-01.webm', duration: offset }
              : segment,
          ),
          { file: videoFile },
        ]
      : resuming
        ? [{ file: 'part-01.webm', duration: offset }, { file: videoFile }]
        : [{ file: videoFile }];
    const context = await browser.newContext({
      viewport: { width: 1600, height: 1000 },
      locale: 'zh-CN',
      recordVideo: { dir: folder, size: { width: 1600, height: 1000 } },
    });
    await context.addInitScript(() => {
      localStorage.setItem('mlab-language', 'zh');
      // The pointer is a recording aid; application content is never replaced.
      document.addEventListener('DOMContentLoaded', () => {
        const pointer = document.createElement('div');
        pointer.setAttribute('aria-hidden', 'true');
        pointer.style.cssText =
          'position:fixed;width:14px;height:14px;border:2px solid white;border-radius:50%;background:#3b55ceaa;box-shadow:0 0 0 1px #263a80;pointer-events:none;z-index:2147483647;left:-30px;top:-30px;transform:translate(-50%,-50%)';
        document.body.append(pointer);
        document.addEventListener('mousemove', (event) => {
          pointer.style.left = `${event.clientX}px`;
          pointer.style.top = `${event.clientY}px`;
        });
        document.addEventListener('pointerdown', () => {
          pointer.style.background = '#ffad32';
        });
        document.addEventListener('pointerup', () => {
          pointer.style.background = '#3b55ceaa';
        });
      });
    });
    const started = Date.now() - offset * 1000;
    const page = await context.newPage();
    page.setDefaultTimeout(25_000);
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('console', (message) => {
      if (message.type() === 'error') errors.push(message.text());
    });
    const chapters = resuming ? JSON.parse(await readFile(path.join(folder, 'chapters.json'))) : [];
    let session = resuming ? resumeSession : null;
    let experiment = resuming ? previous.experiments[0] : null;
    let initialBudget = previous?.initialBudget;
    let outcome = 'incomplete';
    let failure = null;
    const view = () => read(`/api/workbench/sessions/${session}`);
    const chat = () => read(`/api/sessions/${session}/chat?limit=500`);
    const button = (key, root = page) => root.getByRole('button', { name: t(key), exact: true });
    const textbox = (key, root = page) => root.getByRole('textbox', { name: t(key), exact: true });
    async function click(locator) {
      await locator.scrollIntoViewIfNeeded();
      await locator.hover();
      await pause(450);
      await locator.click();
      await pause(700);
    }
    async function type(locator, value) {
      await click(locator);
      await locator.fill('');
      await locator.pressSequentially(value, { delay: 15 });
      await pause(900);
    }
    async function navigate(key) {
      await click(page.getByRole('link', { name: t(key), exact: true }));
    }
    async function mark(title) {
      chapters.push({ seconds: (Date.now() - started) / 1000, title });
      console.log(`${task}: ${title}`);
      await writeFile(path.join(folder, 'chapters.json'), JSON.stringify(chapters, null, 2));
    }
    async function screenshot(name) {
      await pause(1500);
      await page.screenshot({ path: path.join(folder, `${name}.png`), fullPage: true });
    }
    async function readReply() {
      const reply = page.locator('.discussion-message.agent').last();
      if (!(await reply.count())) return;
      await reply.scrollIntoViewIfNeeded();
      const box = await reply.boundingBox();
      if (box) {
        await page.mouse.move(box.x + Math.min(box.width / 2, 200), Math.max(100, box.y + 70));
        await page.mouse.wheel(0, -500);
        await pause(2400);
        await page.mouse.wheel(0, 220);
      }
      await pause(3500);
    }
    async function exportArtifacts() {
      if (!session) return;
      const latest = await chat();
      if (latest.entries.length === 500)
        throw Error('Increase transcript pagination before exporting');
      const messages = latest.entries.filter((entry) =>
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
        const text =
          entry.role === 'card'
            ? `State: ${entry.state}\n\n${JSON.stringify(entry.summary || {}, null, 2)}`
            : entry.text || '';
        return `## ${author}\n\n${text}${entry.refs?.length ? `\n\nReferences: ${entry.refs.join(', ')}` : ''}\n`;
      });
      await writeFile(
        path.join(folder, 'conversation.md'),
        `# Task ${task}: recorded exploration\n\nReal SoC Qwen conversation. Model: qwen3.8:27b. Codex: 0.167.2. Performance data is synthetic. Messages are unedited.\n\n${prose.join('\n')}`,
      );
      const state = await view();
      await writeFile(
        path.join(folder, 'evidence.json'),
        JSON.stringify(
          {
            task,
            session,
            outcome,
            failure,
            provider: course.provider,
            model: 'qwen3.8:27b',
            codex: '0.167.2',
            simulator: course.simulator,
            hardware: course.hardware,
            initialBudget,
            budget: state.budget,
            runs: state.runs,
            experiments: state.experiments,
            measurements: state.measurements,
            conclusions: state.conclusions,
            notes: state.notes,
            usage: latest.agent.usage,
            browserErrors: errors,
            chapters,
            segments,
            capturedAt: new Date().toISOString(),
          },
          null,
          2,
        ),
      );
    }
    async function send(text) {
      const before = (await chat()).agent.usage.turns;
      await type(textbox('Message'), text);
      await click(button('Send'));
      return { before, deadline: Date.now() + 360_000 };
    }
    async function idle(turn) {
      await until(async () => {
        const value = await chat();
        if (value.agent.status === 'error')
          throw Error(
            value.entries.filter((entry) => entry.role === 'error').at(-1)?.text || 'Model error',
          );
        return value.agent.usage.turns > turn.before && value.agent.status === 'idle';
      }, turn.deadline);
      await exportArtifacts();
      await readReply();
    }
    async function speak(text, title, name) {
      await mark(title);
      await idle(await send(text));
      await screenshot(name);
    }
    async function openRun(run) {
      await navigate('Run history');
      const row = page.locator('.runs-table tbody tr').filter({
        has: page.getByLabel(`${t('Select')} ${run.id}`, { exact: true }),
      });
      await click(row.locator('.row-link'));
    }
    async function compare(runs) {
      await navigate('Compare');
      const clear = button('Clear selection');
      if (await clear.isEnabled()) await click(clear);
      await navigate('Run history');
      for (const run of runs)
        await page.getByLabel(`${t('Select')} ${run.id}`, { exact: true }).check();
      await click(button('Compare results'));
      await expect(page.locator('.comparison-table')).toBeVisible();
      await pause(4000);
      const table = page.locator('.comparison-table');
      await table.scrollIntoViewIfNeeded();
      await pause(4000);
    }
    async function measure(prompt, expected, title, name, continuePending = false) {
      await mark(title);
      const beforeRuns = (await view()).runs.filter((run) => run.role === 'hardware').length;
      let turn = continuePending
        ? { before: (await chat()).agent.usage.turns, deadline: Date.now() + 360_000 }
        : await send(prompt);
      const remaining = structuredClone(expected);
      const count = expected.reduce((sum, point) => sum + point.runs, 0);
      let confirmed = 0;
      let cards = 0;
      let clarifications = 0;
      const same = (a, b) =>
        ['candidate', 'workload', 'runs', 'seed'].every((key) => a[key] === b[key]);
      while (remaining.length) {
        const event = await until(async () => {
          const value = await chat();
          if (value.agent.status === 'error') throw Error('Measurement preparation failed');
          const card = value.entries.find(
            (entry) =>
              entry.role === 'card' && entry.card === 'execution' && entry.state === 'pending',
          );
          if (card) return { card };
          if (value.agent.status === 'idle' && value.agent.usage.turns > turn.before)
            return { idle: true };
          return false;
        }, turn.deadline);
        if (event.idle) {
          if (++clarifications > 2)
            throw Error('The requested measurement points remain incomplete');
          turn = await send(
            `还缺这些测量点，请继续安排并让我确认，不要重复已经完成的点：${remaining.map((point) => `${point.candidate}，${point.workload}，${point.runs} 次，起始种子 ${point.seed}`).join('；')}。`,
          );
          continue;
        }
        const pending = event.card;
        const points = pending.summary.points;
        const unmatched = structuredClone(remaining);
        for (const point of points) {
          const index = unmatched.findIndex((item) => same(item, point));
          if (index < 0) throw Error('The measurement card contains an unrequested point');
          unmatched.splice(index, 1);
        }
        const cardCount = points.reduce((sum, point) => sum + point.runs, 0);
        if (!cardCount || pending.summary.hardware_runs !== cardCount)
          throw Error('Unexpected measurement budget');
        if (
          (await view()).runs.filter((run) => run.role === 'hardware').length !==
          beforeRuns + confirmed
        )
          throw Error('A measurement started before student confirmation');
        const state = await view();
        const candidates = pending.summary.configs.map((candidate) =>
          state.experiments
            .flatMap((item) => item.candidates)
            .find((item) => item.id === candidate.id),
        );
        verifyCandidates(candidates, pending.summary.configs);
        const card = page.locator('.conversation-action-card.pending');
        await expect(card).toHaveCount(1);
        await click(card.getByText(t('Configuration details'), { exact: true }));
        await screenshot(`${name}-confirmation${cards ? `-${cards + 1}` : ''}`);
        await pause(4000);
        await click(card.getByText(t('Configuration details'), { exact: true }));
        await click(
          card.getByRole('button', {
            name:
              cardCount === 1
                ? t('Run measurement')
                : t('Run {count} measurements', { count: cardCount }),
            exact: true,
          }),
        );
        confirmed += cardCount;
        cards += 1;
        remaining.splice(0, remaining.length, ...unmatched);
        await until(async () => {
          const runs = (await view()).runs.filter((run) => run.role === 'hardware');
          if (runs.some((run) => ['failed', 'cancelled', 'timed_out'].includes(run.status)))
            throw Error('A sandbox run failed');
          return (
            runs.length === beforeRuns + confirmed &&
            runs.every((run) => run.status === 'succeeded')
          );
        });
        await exportArtifacts();
      }
      await idle(turn);
      await until(async () => {
        const runs = (await view()).runs.filter((run) => run.role === 'hardware');
        if (runs.some((run) => ['failed', 'cancelled', 'timed_out'].includes(run.status)))
          throw Error('A sandbox run failed');
        return (
          runs.length === beforeRuns + count && runs.every((run) => run.status === 'succeeded')
        );
      });
      await screenshot(`${name}-results`);
    }
    let state;
    let selected;
    try {
      if (reviewOnly) {
        if (previous.outcome !== 'complete' || !['9.2', '9.3'].includes(task))
          throw Error('Record the evidence review after the main walkthrough is complete');
        await page.goto(`${base}/#/${session}/runs`);
        await expect(textbox('Message')).toBeVisible();
        const before = await view();
        const reviewIds =
          task === '9.2' ? ['hw_0003', 'hw_0004', 'hw_0006'] : ['hw_0001', 'hw_0002', 'hw_0004'];
        await mark('补录：对照记录复核解释');
        await compare(before.runs.filter((run) => reviewIds.includes(run.id)));
        await screenshot('24-comparing-validation-runs');
        await speak(
          task === '9.2'
            ? '@hw_0003 @hw_0004 @hw_0006 我想核对你刚才的两个解释：两次演示数据几乎相同，真的能排除 p99 波动、证明余量稳定吗？另外，表中 development 为 1959.6 ms，long_prompt 为 1955.3 ms，你却说 long_prompt 最危险。请重新读取这些记录，纠正不严谨的表述，并说清楚小样本和合成数据各自的局限。另外，“必须先有小模拟余量再上机”是流程要求吗？请区分实验策略建议与学生可以自主选择的操作。不要运行新实验。请用中文，约 320 字。'
            : '@hw_0001 @hw_0002 @hw_0004 我想认真核对你的解释。两次合成数据都是 108.1 s，真的能证明结果不受随机波动影响吗？另一条 DAG 是 72.3 s，但节点数、工作量和关键路径可能不同；直接比较绝对时间，就能证明跨 DAG 性能更好、没有过拟合吗？请重新读这些记录，纠正过度推断，说明哪些结论成立、还需要哪些样本或归一化比较。不要运行新实验。请用中文，约 280 字。',
          '追问小样本局限，并纠正解释',
          '24-reviewing-the-explanation',
        );
        await pause(5000);
        await screenshot('25-evidence-correction');
        if ((await view()).runs.length !== before.runs.length)
          throw Error('An evidence question unexpectedly started a new run');
        const answer =
          (await chat()).entries.filter((entry) => entry.role === 'agent').at(-1)?.text || '';
        if (
          (task === '9.2' &&
            (!answer.replaceAll(',', '').includes('1959') ||
              !answer.replaceAll(',', '').includes('1955'))) ||
          !/(不能|不足|无法|不应)/.test(answer)
        )
          throw Error('The evidence review needs manual inspection before publication');
        if (errors.length) throw Error(`Browser errors: ${errors.join('; ')}`);
        outcome = 'complete';
        console.log(`${task}: evidence review recorded`);
        continue;
      }
      if (resuming) {
        await page.goto(`${base}/#/${session}/runs`);
        await expect(textbox('Message')).toBeVisible();
        state = await view();
        selected = state.runs.find(
          (run) => run.role === 'simulator' && run.candidate === spec.selected,
        );
        if (!selected || state.runs.some((run) => run.role === 'hardware'))
          throw Error('Resume is only supported before the first sandbox confirmation');
        await mark('第二段：继续核对并确认沙盒测量');
        await pause(3000);
      } else {
        await page.goto(`${base}/#/${task}/overview`);
        await expect(textbox('Message')).toBeVisible();
        session = new URL(page.url()).hash.split('/')[1];
        const initial = await view();
        if (initial.runs.length || initial.experiments.length)
          throw Error('Record into a fresh conversation');
        initialBudget = initial.budget;
        if (initialBudget.used) throw Error('Record into a fresh task budget');
        await mark('认识任务目标与约束');
        await screenshot('01-task-overview');
        await pause(4500);
        await speak(spec.question, '自由提问：先理解问题', '02-conceptual-discussion');
        if ((await view()).runs.length)
          throw Error('A conceptual question unexpectedly launched a run');
        await speak(spec.experiment, '通过对话安排第一组模拟', '03-requested-simulations');
        state = await until(async () => {
          const value = await view();
          return value.runs.length === 2 && value.runs.every((run) => run.status === 'succeeded')
            ? value
            : false;
        });
        if (state.experiments.length !== 1) throw Error('Expected one initial plan');
        experiment = state.experiments[0];
        verifyCandidates(expectedCandidates(task, state.templates), experiment.candidates);
        const firstRuns = state.runs;
        selected = firstRuns.find((run) => run.candidate === spec.selected);
        if (!selected) throw Error('The selected candidate has no result');
        await mark('运行记录与并排比较');
        await compare(firstRuns);
        await screenshot('04-compare');
        await mark('在输入框中用 @ 引用结果');
        await type(textbox('Message'), `我想追问 @${selected.id}`);
        const picker = page.getByRole('listbox', { name: t('Matching records'), exact: true });
        await expect(picker).toBeVisible();
        await screenshot('05-inline-reference');
        await textbox('Message').press('ArrowDown');
        await textbox('Message').press('Enter');
        await textbox('Message').pressSequentially(
          ' ：请读取实际指标，与另一配置比较。哪些结论有证据，哪些只是待验证的解释？请用中文，约 220 字。',
          { delay: 18 },
        );
        const beforeReference = (await chat()).agent.usage.turns;
        await click(button('Send'));
        await idle({ before: beforeReference, deadline: Date.now() + 360_000 });
        const attached = (await chat()).entries.filter((entry) => entry.role === 'student').at(-1);
        if (!attached.refs?.includes(selected.key))
          throw Error('The inline reference was not saved');
        await screenshot('06-evidence-discussion');
        await mark('深入单次运行：查看配置与请求数据');
        await openRun(selected);
        await click(button('Requests'));
        await page.getByLabel(t('Order requests'), { exact: true }).selectOption('slowest');
        await click(page.locator('.request-row').first());
        await screenshot('07-request-inspection');
        await pause(3000);
      }
      if (task === '9.2') {
        if (!resuming) {
          await mark('学生自行复制配置，调整并发数后运行');
          await click(button('Modify and run again'));
          await type(textbox('Configuration name'), 'c20');
          await page.getByLabel(t('Maximum concurrency'), { exact: true }).fill('20');
          await type(
            textbox('Why include this configuration?'),
            '只调整并发数，检查更高吞吐是否压缩延迟余量。',
          );
          await click(button('Save draft'));
          await screenshot('08-student-edits');
          await click(button('Run simulations'));
          await screenshot('09-simulation-review');
          await click(button('Start simulations'));
          state = await until(async () => {
            const value = await view();
            return value.runs.some((run) => run.candidate === 'c20' && run.status === 'succeeded')
              ? value
              : false;
          });
          const variant = state.experiments.find((item) =>
            item.candidates.some((candidate) => candidate.id === 'c20'),
          );
          const expected = structuredClone(
            experiment.candidates.find((candidate) => candidate.id === 'c16'),
          );
          expected.id = 'c20';
          expected.strategy.serving.max_concurrency = 20;
          verifyCandidates([expected], variant.candidates);
          await compare([selected, state.runs.find((run) => run.candidate === 'c20')]);
          await screenshot('10-student-comparison');
        }
        await measure(
          '我手动新增了 c20。请安排一次沙盒对比：原方案的 c16 和新方案的 c20，各在 development 上运行 1 次，种子均为 42，共 2 次。其他点先不运行，让我先检查确认卡。完成后用中文简要比较延迟余量。',
          [
            { candidate: 'c16', workload: 'development', runs: 1, seed: 42 },
            { candidate: 'c20', workload: 'development', runs: 1, seed: 42 },
          ],
          '通过对话安排沙盒对比，检查并确认执行',
          '11-sandbox-comparison',
          resuming,
        );
        state = await view();
        const hardware = state.runs.find(
          (run) => run.role === 'hardware' && run.candidate === 'c16',
        );
        await openRun(hardware);
        await click(button('Compare this pair'));
        await screenshot('12-simulation-versus-sandbox');
        await speak(
          `@${hardware.id} @${selected.id} 我想进一步理解这组差异。请继续读取这两次运行的请求数据，给出一个具体请求的首 token 延迟对照，并解释它能说明什么、还不能证明什么。请用中文，约 250 字。`,
          '引用两次运行，追问具体请求与证据边界',
          '13-deep-reading',
        );
      }
      state = await view();
      const holdouts = state.overview.holdouts;
      await measure(
        `我想继续验证 ${spec.selected}。请只为它安排一组沙盒测量：development 重复 2 次，起始种子 42；${holdouts.join(' 和 ')} 各 1 次，种子 42，共 ${2 + holdouts.length} 次。全部放在一张确认卡中。不要选择其他配置。请让我确认后执行，并用中文说明这些验证分别回答什么问题。`,
        [
          { candidate: spec.selected, workload: 'development', runs: 2, seed: 42 },
          ...holdouts.map((workload) => ({
            candidate: spec.selected,
            workload,
            runs: 1,
            seed: 42,
          })),
        ],
        '重复运行与不同负载的验证',
        '14-validation',
      );
      await navigate('Run history');
      await screenshot('15-complete-history');
      state = await view();
      const hardware = state.runs.filter(
        (run) => run.role === 'hardware' && run.candidate === spec.selected,
      );
      await openRun(hardware[0]);
      await click(button('Compare this pair'));
      await screenshot('16-paired-comparison');
      await click(button('Discuss these results'));
      await speak(
        `请结合 ${hardware.map((run) => `${run.id}（${run.workload}）`).join('、')}，读取所有列出的实测记录后判断：${spec.selected} 目前是否满足任务约束，是否还缺重复或保留负载证据？区分本次演示数据与真实硬件证据。请用中文，约 260 字，不要替我撰写实验结论。`,
        '结合完整历史讨论结论，而非只看单次结果',
        '17-validation-discussion',
      );
      await mark('学生记录自己的观察并提交结论');
      await navigate('Notes & conclusions');
      await type(textbox('Your observation'), spec.observation);
      await click(button('Save observation'));
      await screenshot('18-student-observation');
      await click(button('Submit conclusion'));
      const dialog = page.getByRole('dialog');
      await dialog
        .getByRole('combobox', { name: t('Configuration'), exact: true })
        .selectOption(`${experiment.key}/${spec.selected}`);
      await expect(button('Submit conclusion', dialog)).toBeEnabled();
      await screenshot('19-conclusion-preview');
      await click(button('Submit conclusion', dialog));
      await expect(
        page.getByRole('heading', { name: t('Submitted conclusions'), exact: true }),
      ).toBeVisible();
      if ((await view()).conclusions.at(-1)?.conclusion !== 'mock_only')
        throw Error('Validation is incomplete or conflicting');
      await mark('查看报告，保留证据与尚未解决的问题');
      await navigate('Report');
      await expect(page.locator('.record-report')).toBeVisible();
      await screenshot('20-report');
      await click(button('Update saved report'));
      const report = await read(`/api/sessions/${session}/records/report`);
      if (!report.written) throw Error('The report was not saved');
      await writeFile(path.join(folder, 'report.json'), JSON.stringify(report, null, 2));
      const reportBox = await page.locator('.record-report').boundingBox();
      if (reportBox) await page.mouse.move(reportBox.x + 150, 580);
      for (let step = 0; step < 3; step++) {
        await page.mouse.wheel(0, 420);
        await pause(2800);
      }
      if (task === '9.2') {
        await speak(
          '@report 我想在汇报前再核对一次：这份报告中最值得保留的发现是什么？下一步在真实设备上应补什么验证？请用中文简要回答，不要把演示数据说成真实 GPU 测量。',
          '在对话中引用报告，讨论下一步探索',
          '21-report-discussion',
        );
        await mark('新建干净会话，再回到之前的探索');
        const before = await view();
        await click(button('New conversation'));
        const fresh = await until(async () => {
          const key = new URL(page.url()).hash.split('/')[1];
          return key !== session ? read(`/api/workbench/sessions/${key}`) : false;
        });
        if (fresh.runs.length || fresh.experiments.length || fresh.notes.length)
          throw Error('The new conversation inherited records');
        if (fresh.budget.used !== before.budget.used)
          throw Error('A new conversation reset the task budget');
        await expect(page.locator('.discussion-message')).toHaveCount(0);
        await screenshot('22-clean-conversation');
        await pause(3000);
        await click(
          page.getByRole('button', {
            name: t('Conversation {number}', { number: session.split('-')[1] }),
            exact: true,
          }),
        );
        await expect(page.locator('.record-report')).toBeVisible();
        if ((await view()).runs.length !== before.runs.length)
          throw Error('Earlier records were not preserved');
        await screenshot('23-restored-conversation');
      }
      await pause(4000);
      if (errors.length) throw Error(`Browser errors: ${errors.join('; ')}`);
      outcome = 'complete';
      console.log(`${task}: recording complete`);
    } catch (error) {
      failure = String(error);
      outcome = 'failed';
      await screenshot('failure').catch(() => {});
      throw error;
    } finally {
      try {
        await exportArtifacts();
      } finally {
        const video = page.video();
        await context.close();
        if (video) await video.saveAs(path.join(folder, videoFile));
      }
    }
  }
} finally {
  await browser.close();
}
