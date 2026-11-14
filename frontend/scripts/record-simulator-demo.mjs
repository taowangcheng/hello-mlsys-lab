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
  process.env.MLAB_REVIEW_DIR || path.join(project, 'workspaces/simulator-demo-2026-11-14/videos'),
);
const words = JSON.parse(await readFile(path.join(project, 'src/mlab/web/static/locales/zh.json')));
const t = (key, values = {}) =>
  Object.entries(values).reduce(
    (text, [name, value]) => text.replaceAll(`{${name}}`, value),
    words[key] || key,
  );
const scenarios = {
  9.1: {
    selected: 'bf16_1_7b',
    templates: ['bf16_0_6b', 'bf16_1_7b'],
    question:
      '先不运行。我看到这次联调使用 Qwen3 BF16 和 RX9070XT，和原课程基线不同。请解释端侧部署中模型大小、内存、响应速度与模型质量的取舍，以及这次能验证什么、还不能验证什么。请用中文，约 220 字。',
    experiment:
      '请采用本会话提供的 bf16_0_6b 和 bf16_1_7b 两个原配置，创建一个比较方案并运行 development 模拟，种子均为 42。只运行这两个点。读取结果后比较延迟和内存，不要把模型质量未知说成达标。请用中文简要回答。',
    observation:
      '这次联调比较同为 BF16 的 Qwen3 0.6B 和 1.7B，检查了延迟、内存及不同提示词下的表现。我选择 1.7B 作为继续探索的配置，不代表它在质量上已经达标。模拟来自真实模拟器 API，但尚未校准；沙盒是演示数据。正式选型仍需要质量评测和目标设备上的测量。',
  },
  9.2: {
    selected: 'c16',
    templates: ['baseline', 'c16'],
    question:
      '先不运行。任务关注服务延迟和有效吞吐量，为什么把最大并发数调高不一定提升吞吐？如果一次小负载测试中并发 16 和 32 的结果相同，能说明什么、不能说明什么？请用中文，约 220 字，并说明本次联调的证据范围。',
    experiment:
      '请采用本会话的 baseline 和 c16 两个原配置，创建一组并发对照并运行 development 模拟，种子均为 42。只运行这两个点。请读取实际结果，若指标相同就如实解释，不要预设排名或虚构差异。请用中文简要回答。',
    observation:
      '当前负载中并发上限 16 与 32 的模拟结果相同，这不能证明并发设置永远无影响。我手动补充了并发 20，也检查了请求时序、重复运行和不同提示词负载。还需设计足够的到达压力并在真实设备上验证；沙盒演示数据不能用于校准模拟器。',
  },
  9.3: {
    selected: 'replica_3',
    templates: ['replica_1', 'replica_3'],
    question:
      '先不运行。这里有先规划、再并行执行、最后汇总的依赖请求。为什么更多副本不一定按比例缩短总完成时间？总完成时间和关键路径时间有什么区别？结合本次 Qwen3 BF16 联调解释，约 220 字。',
    experiment:
      '采用本会话的 replica_1 和 replica_3 原配置，比较工作模型 1 个与 3 个副本。创建同一个方案，分别运行 development 模拟，种子均为 42，只运行这两个点。请核对请求完成数、总完成时间以及依赖关系；如果独立服务时间不可用，不要编造关键路径数值。用中文简要解释。',
    observation:
      '增加工作模型副本后，我核对了 10 个请求的完成情况、依赖释放时间和总完成时间。并行收益仍受依赖结构和汇总阶段限制。模拟器未提供独立服务时间，所以不能把含排队的端到端时间当作关键路径服务时间。本次是 Qwen3 BF16/RX9070XT 联调，不能替代原课程大模型配置或实机验证。',
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
  return scenarios[task].templates.map((id) =>
    structuredClone(templates.find((item) => item.id === id)),
  );
}
const course = await read('/api/course');
if (course.agent !== 'codex' || course.provider !== 'codex-soc-qwen')
  throw Error('A live Codex server using the codex-soc-qwen profile is required');
if (
  course.simulator !== 'mlsys' ||
  course.simulator_profile !== 'protocol-v1.20' ||
  course.hardware !== 'standin'
)
  throw Error(
    'This recording requires the explicit HTTP integration exercise and a disclosed sandbox stand-in',
  );
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ slowMo: 140 });
try {
  for (const task of tasks) {
    const spec = scenarios[task];
    const folder = path.join(output, `task-${task}`);
    await mkdir(folder, { recursive: true });
    try {
      const saved = JSON.parse(await readFile(path.join(folder, 'evidence.json')));
      if (saved.outcome === 'complete') throw Error('Choose a fresh recording directory');
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
    }
    const videoFile = 'walkthrough.webm';
    const segments = [{ file: videoFile }];
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
    const started = Date.now();
    const page = await context.newPage();
    page.setDefaultTimeout(25_000);
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('console', (message) => {
      if (message.type() === 'error') errors.push(message.text());
    });
    const chapters = [];
    const waits = [];
    let session = null;
    let experiment = null;
    let initialBudget;
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
      const count = (await chat()).entries.filter((entry) => entry.role === 'agent').length;
      if (!count) return;
      const replies = page.locator('.discussion-message.agent:not(.streaming)');
      await expect(replies).toHaveCount(count);
      await expect(page.locator('.discussion-message.streaming')).toHaveCount(0);
      const reply = replies.last();
      const author = reply.locator('.message-author');
      await author.scrollIntoViewIfNeeded();
      const panel = await page.locator('.discussion-messages').boundingBox();
      const heading = await author.boundingBox();
      if (panel && heading) {
        await page.mouse.move(panel.x + panel.width / 2, panel.y + 100);
        await page.mouse.wheel(0, heading.y - panel.y - 25);
        await pause(3500);
        const box = await reply.boundingBox();
        const step = Math.max(250, panel.height - 100);
        for (let offset = step; box && offset < box.height; offset += step) {
          await page.mouse.wheel(0, step);
          await pause(3500);
        }
      }
      await pause(3500);
    }
    async function exportArtifacts() {
      if (!session) return;
      const latest = await chat();
      if (latest.entries.length === 500)
        throw Error('Increase transcript pagination before exporting');
      await writeFile(
        path.join(folder, 'tool-activity.json'),
        JSON.stringify(
          latest.entries
            .filter((entry) => entry.role === 'tool')
            .map(({ id, at, tool, text, is_error }) => ({ id, at, tool, text, is_error })),
          null,
          2,
        ),
      );
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
        `# Task ${task}: recorded exploration\n\nReal SoC Qwen conversation. Model: qwen3.8:27b. Codex: 0.167.2. Real simulator API predictions are uncalibrated. Sandbox performance data is synthetic. This uses an explicit Qwen3 BF16 integration profile, not the original course baseline. Messages are unedited.\n\n${prose.join('\n')}`,
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
            simulatorProfile: course.simulator_profile,
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
            waits,
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
      const waiting = (Date.now() - started) / 1000;
      await until(async () => {
        const value = await chat();
        if (value.agent.status === 'error')
          throw Error(
            value.entries.filter((entry) => entry.role === 'error').at(-1)?.text || 'Model error',
          );
        return value.agent.usage.turns > turn.before && value.agent.status === 'idle';
      }, turn.deadline);
      waits.push({ start: waiting, end: (Date.now() - started) / 1000, kind: 'model' });
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
        const waiting = (Date.now() - started) / 1000;
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
        waits.push({
          start: waiting,
          end: (Date.now() - started) / 1000,
          kind: 'model-before-confirmation',
        });
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
      await page.goto(`${base}/#/${task}/overview`);
      await expect(textbox('Message')).toBeVisible();
      session = new URL(page.url()).hash.split('/')[1];
      if (process.env.MLAB_DEMO_FRESH === '1' && (await view()).runs.length) {
        await click(button('New conversation'));
        session = await until(async () => {
          const key = new URL(page.url()).hash.split('/')[1];
          return key !== session ? key : false;
        });
      }
      const initial = await view();
      if (initial.runs.length || initial.experiments.length)
        throw Error('Record into a fresh conversation');
      initialBudget = initial.budget;
      if (initialBudget.remaining < (task === '9.2' ? 6 : task === '9.1' ? 3 : 4))
        throw Error('Not enough task budget for the planned recording');
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
      if (!attached.refs?.includes(selected.key)) throw Error('The inline reference was not saved');
      await screenshot('06-evidence-discussion');
      await mark('深入单次运行：查看配置与请求数据');
      await openRun(selected);
      await click(button('Requests'));
      await page.getByLabel(t('Order requests'), { exact: true }).selectOption('slowest');
      await click(page.locator('.request-row').first());
      await screenshot('07-request-inspection');
      await pause(3000);
      if (task === '9.2') {
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
        await measure(
          '我手动新增了 c20。请安排一次沙盒对比：原方案的 c16 和新方案的 c20，各在 development 上运行 1 次，种子均为 42，共 2 次。其他点先不运行，让我先检查确认卡。完成后用中文简要比较延迟余量。',
          [
            { candidate: 'c16', workload: 'development', runs: 1, seed: 42 },
            { candidate: 'c20', workload: 'development', runs: 1, seed: 42 },
          ],
          '通过对话安排沙盒对比，检查并确认执行',
          '11-sandbox-comparison',
        );
        state = await view();
        const hardware = state.runs.find(
          (run) => run.role === 'hardware' && run.candidate === 'c16',
        );
        await openRun(hardware);
        await click(button('Compare this pair'));
        await screenshot('12-simulation-versus-sandbox');
        await speak(
          `@${hardware.id} @${selected.id} 我想进一步理解这组差异。请继续读取这两次运行的请求数据，给出一个具体请求的首 token 延迟对照。明确一边是真实模拟器预测，一边是沙盒演示数据，能否据此评价模拟器准确度？请用中文，约 250 字。`,
          '引用两次运行，追问具体请求与证据边界',
          '13-deep-reading',
        );
      }
      state = await view();
      const holdouts = state.overview.holdouts;
      await measure(
        `我想继续验证 ${spec.selected}。请只为它安排一组沙盒测量：development 重复 2 次，起始种子 42；${holdouts.join(' 和 ')} 各 1 次，种子 42，共 ${2 + holdouts.length} 次。请遵守每批额度，需要时拆成多张确认卡，不要重复已完成的点。若模拟返回了显存不足或请求未完成，请保留失败记录，在确认卡中说明；不要更改配置或重跑来隐藏失败，仍让我选择是否用沙盒检查。不要选择其他配置。请让我确认后执行，并用中文说明这些验证分别回答什么问题。`,
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
        `请结合 ${hardware.map((run) => `${run.id}（${run.workload}）`).join('、')}，读取所有列出的沙盒演示记录后判断：${spec.selected} 目前是否满足任务约束，是否还缺重复或保留负载证据？区分未校准的模拟、沙盒演示数据与真实硬件证据，说明质量未知和缺失指标的限制。请用中文，约 260 字，不要替我撰写实验结论。`,
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
      if ((await view()).conclusions.at(-1)?.conclusion !== 'unresolved')
        throw Error('Unmeasured model quality must remain unresolved');
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
      {
        await speak(
          '@report 我想在汇报前再核对一次：这份报告中最值得保留的发现是什么？下一步在真实设备上应补什么验证？请用中文简要回答，说明为何报告还不能宣称原课程配置通过验证，不要把演示数据说成真实 GPU 测量。',
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
