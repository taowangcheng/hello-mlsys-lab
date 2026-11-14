// Record an explicit evidence review without changing earlier replies or runs.
import { chromium, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { readFile, writeFile, rename } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
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
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function read(url) {
  const response = await fetch(base + url);
  const value = await response.json();
  if (!response.ok || value.ok === false) throw Error(value.error?.message || response.statusText);
  return value;
}
async function until(check, deadline = Date.now() + 360000) {
  while (Date.now() < deadline) {
    const value = await check();
    if (value) return value;
    await pause(1000);
  }
  throw Error('Evidence review timed out');
}
const course = await read('/api/course');
const prompts = {
  9.1: '@sim_0001 @sim_0002 @hw_0001 @hw_0002 我想核对之前的解释。你提到“权重体积差约 1.4 GiB”，这个数有什么直接证据？请读取实际逐设备显存记录，区分物理峰值、KV 使用峰值和 KV 池预留；没有权重独立观测就不要猜精确差值。此外，两次合成数据相近能否证明稳定，或说模拟与沙盒差异“超出噪声”？请明确纠正前面不严谨的表述，不能用演示沙盒评价模拟器准确度。只读取，不运行新实验。中文约 350 字。',
  9.2: '@report 请做一次证据复核：你之前称模拟数值为“实测”，是否需要更正？并发 16、20、32 在当前模拟负载下的结果是否真的存在差异？读取原始请求时序后再解释它说明什么；并发上限是每副本的活跃请求上限，还是等候队列长度？两次沙盒合成数据相近能否证明 p99 稳定，模拟与沙盒差异能否作为校准误差？你说短负载中模拟偏乐观，有真实测量支持吗？长提示词模拟失败的具体原因是什么，请继续读取对应错误记录。请纠正此前如有过度推断的说法，给出下一步应控制的负载变量。不要新运行，中文约 320 字。',
  9.3: '@report 请复核依赖链的解释：读取请求级时间戳与缺失指标原因，给出一条实际父子依赖的结束时间、间隔和子节点释放时间。最晚完成依赖链是否等于只含独立服务时间的关键路径？不同 DAG 的绝对总时间能否直接证明泛化或优化效果？你刚才说“模拟器明显高估”“全部 beyond_noise”，但沙盒是合成数据，这能支持校准结论吗？replica_1 没有沙盒运行，又能否证明“模拟与沙盒排序无翻转”？请明确纠正证据不足的说法。只读取，不运行，中文约 450 字。',
};
const tasks = process.argv.slice(2).length ? process.argv.slice(2) : ['9.1', '9.2', '9.3'];
const browser = await chromium.launch({ slowMo: 140 });
try {
  for (const task of tasks) {
    const folder = path.join(output, `task-${task}`);
    const previous = JSON.parse(await readFile(path.join(folder, 'evidence.json')));
    if (previous.outcome !== 'complete') throw Error('Review requires a completed recording');
    const offset = Number(
      execFileSync(
        'ffprobe',
        [
          '-v',
          'error',
          '-show_entries',
          'format=duration',
          '-of',
          'default=noprint_wrappers=1:nokey=1',
          path.join(folder, 'walkthrough.webm'),
        ],
        { encoding: 'utf8' },
      ),
    );
    const videoFile = `part-${String(previous.segments.length + 1).padStart(2, '0')}.webm`;
    const segments = [
      ...previous.segments.map((segment) =>
        segment.file === 'walkthrough.webm' ? { file: 'part-01.webm', duration: offset } : segment,
      ),
      { file: videoFile },
    ];
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
    const errors = [...previous.browserErrors];
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('console', (message) => {
      if (message.type() === 'error') errors.push(message.text());
    });
    const chapters = [...previous.chapters];
    const waits = [...previous.waits];
    let session = previous.session;
    let initialBudget = previous.initialBudget;
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
    try {
      await page.goto(`${base}/#/${session}/compare`);
      await expect(textbox('Message')).toBeVisible();
      const before = await view();
      if (process.env.MLAB_REVIEW_REPORT_ONLY === '1') {
        await mark('阅读证据复核的完整回复');
        await compare(
          before.runs
            .filter((run) => run.role === 'simulator' && run.workload === 'development')
            .slice(0, 2),
        );
        await readReply();
        await screenshot('25-evidence-review');
        await mark('复核报告：区分未评估、未完成和违反约束');
        await navigate('Report');
        await expect(page.locator('.record-report')).toBeVisible();
        await click(button('Update saved report'));
        await writeFile(
          path.join(folder, 'report.json'),
          JSON.stringify(await read(`/api/sessions/${session}/records/report`), null, 2),
        );
        await screenshot('26-report-status');
        const box = await page.locator('.record-report').boundingBox();
        if (box) await page.mouse.move(box.x + 100, 600);
        await page.mouse.wheel(0, 450);
        await pause(6000);
      } else {
        await mark('补充录制：核对解释与证据边界');
        await compare(
          before.runs
            .filter((run) => run.role === 'simulator' && run.workload === 'development')
            .slice(0, 2),
        );
        await speak(
          process.env.MLAB_REVIEW_PROMPT || prompts[task],
          '让 Agent 读取记录，复核前面的解释',
          '25-evidence-review',
        );
        await mark('复核报告中的验证状态');
        await navigate('Report');
        await click(button('Update saved report'));
        await writeFile(
          path.join(folder, 'report.json'),
          JSON.stringify(await read(`/api/sessions/${session}/records/report`), null, 2),
        );
        await screenshot('26-report-status');
      }
      if ((await view()).runs.length !== before.runs.length)
        throw Error('The review unexpectedly started a run');
      await pause(8000);
      if (errors.length) throw Error('Browser errors during evidence review');
      outcome = 'complete';
    } catch (error) {
      outcome = 'failed';
      failure = String(error);
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
    if (previous.segments.length === 1)
      await rename(path.join(folder, 'walkthrough.webm'), path.join(folder, 'part-01.webm'));
    const list = path.join(folder, 'concat.txt');
    await writeFile(list, segments.map((segment) => `file '${segment.file}'\n`).join(''));
    execFileSync('ffmpeg', [
      '-hide_banner',
      '-loglevel',
      'warning',
      '-y',
      '-f',
      'concat',
      '-safe',
      '0',
      '-i',
      list,
      '-c',
      'copy',
      path.join(folder, 'walkthrough.webm'),
    ]);
    console.log(`${task}: evidence review complete`);
  }
} finally {
  await browser.close();
}
