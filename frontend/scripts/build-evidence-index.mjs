// Build a local review page from the recorder's public artifacts only.
import { mkdir, readFile, readdir, rename, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const project = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const input = path.resolve(
  process.env.MLAB_REVIEW_DIR || path.join(project, 'workspaces/all-tasks-review/live'),
);
const output = path.dirname(input);
const tasks = [
  {
    id: '9.1',
    title: 'On-device assistant',
    metrics: [
      ['ttft_ms.p50', 'Median first token', 'ms'],
      ['decode_tok_s', 'Decode rate', 'tok/s'],
      ['vram_peak_gib', 'Peak memory', 'GiB'],
      ['energy_j_per_req', 'Energy', 'J/request'],
    ],
  },
  {
    id: '9.2',
    title: 'Serving under an SLA',
    metrics: [
      ['ttft_ms.p99', 'P99 first token', 'ms'],
      ['goodput_tok_s', 'Goodput', 'tok/s'],
      ['completion_rate', 'Completion rate', ''],
    ],
  },
  {
    id: '9.3',
    title: 'Workload-driven inference cluster',
    metrics: [
      ['makespan_s', 'Completion time', 's'],
      ['critical_path_s', 'Critical path', 's'],
      ['completion_rate', 'Completion rate', ''],
    ],
  },
];
const stages = [
  ['01-overview', 'Task overview'],
  ['02-agent-proposal', 'Assistant proposes an experiment'],
  ['03-student-experiment', 'Student edits the experiment'],
  ['04-simulation-history', 'Simulation results'],
  ['05-comparison', 'Configuration comparison'],
  ['06-agent-analysis', 'Assistant discusses the results'],
  ['07-request-inspection', 'Individual request inspection'],
  ['08-measurement-review', 'Student reviews the measurement'],
  ['09-complete-history', 'Simulation and measurement history'],
  ['10-agent-measurement-review', 'Assistant reviews the measured gap'],
  ['11-conclusion', 'Student submits a conclusion'],
];
const escape = (value) =>
  String(value ?? '').replace(
    /[&<>"']/g,
    (character) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character],
  );
const markdownText = (value) =>
  escape(value)
    .replaceAll('|', '\\|')
    .replace(/[\r\n]/g, ' ');
const number = (value) =>
  typeof value === 'number' && Number.isFinite(value)
    ? value.toLocaleString('en-US', { maximumFractionDigits: 3 })
    : '—';
const metric = (values, key) => key.split('.').reduce((value, part) => value?.[part], values);
const href = (file) =>
  path.relative(output, file).split(path.sep).map(encodeURIComponent).join('/');
const labels = {
  constraints_met: 'Meets constraints',
  constraints_violated: 'Outside constraints',
  mock_only: 'Demonstration evidence only',
  unresolved: 'Needs more evidence',
  insufficient_evidence: 'Needs more evidence',
  quality_unknown: 'Quality not assessed',
};
const label = (value) => labels[value] || String(value || 'Pending').replaceAll('_', ' ');

async function json(file) {
  try {
    return JSON.parse(await readFile(file, 'utf8'));
  } catch (error) {
    // The index can be rebuilt while the recorder is still creating an artifact.
    if (error.code === 'ENOENT' || error instanceof SyntaxError) return null;
    throw error;
  }
}
async function atomic(file, content) {
  const temporary = `${file}.tmp-${process.pid}`;
  await writeFile(temporary, content);
  await rename(temporary, file);
}
async function collect(task) {
  const directory = path.join(input, `task-${task.id}`);
  const files = new Set(
    await readdir(directory).catch((error) => {
      if (error.code === 'ENOENT') return [];
      throw error;
    }),
  );
  const evidence = await json(path.join(directory, 'evidence.json'));
  const conversation = await json(path.join(directory, 'conversation.json'));
  const messages = Array.isArray(conversation)
    ? conversation.filter(
        (message) =>
          ['student', 'agent', 'error'].includes(message?.role) && typeof message.text === 'string',
      )
    : [];
  const images = stages.filter(([name]) => files.has(`${name}.png`));
  const videos = ['walkthrough.mp4', 'walkthrough.webm'].filter((name) => files.has(name));
  const runs = Array.isArray(evidence?.runs) ? evidence.runs : [];
  const conclusion = Array.isArray(evidence?.conclusions) ? evidence.conclusions.at(-1) : null;
  const browserErrors = Array.isArray(evidence?.browserErrors)
    ? evidence.browserErrors.length
    : null;
  const complete = Boolean(
    evidence &&
    conclusion &&
    messages.some((message) => message.role === 'agent') &&
    videos.length &&
    images.length === stages.length &&
    browserErrors === 0,
  );
  return {
    ...task,
    directory,
    files,
    evidence,
    messages,
    images,
    videos,
    runs,
    conclusion,
    browserErrors,
    complete,
  };
}

function renderRuns(task) {
  if (!task.runs.length)
    return '<p class="pending">Run metrics will appear after this recording finishes.</p>';
  return `<details class="metrics"><summary>Inspect ${task.runs.length} recorded runs</summary><div class="table-scroll"><table><thead><tr><th>Run</th><th>Configuration</th><th>Source / workload</th><th>Execution</th><th>Result</th>${task.metrics.map(([, title, unit]) => `<th>${escape(title)}${unit ? `<small>${escape(unit)}</small>` : ''}</th>`).join('')}</tr></thead><tbody>${task.runs.map((run) => `<tr><td>${escape(run.id)}</td><td>${escape(run.candidate)}</td><td>${run.role === 'hardware' ? 'Measurement' : 'Simulation'}<small>${escape(run.workload || 'development')} · repeat ${escape(run.repeat ?? '—')}</small></td><td>${escape(label(run.status))}</td><td>${escape(label(run.verdict))}</td>${task.metrics.map(([key]) => `<td class="numeric">${number(metric(run.metrics, key))}</td>`).join('')}</tr>`).join('')}</tbody></table></div></details>`;
}

function renderTask(task) {
  const artifact = (name) => href(path.join(task.directory, name));
  const state = task.complete
    ? 'Complete'
    : task.files.size
      ? 'Recording / evidence incomplete'
      : 'Not recorded yet';
  const usage = task.evidence?.usage || {};
  const downloads = ['conversation.md', 'conversation.json', 'evidence.json'].filter((name) =>
    task.files.has(name),
  );
  return `<section class="task" id="task-${task.id}">
    <header class="task-header"><div><span class="eyebrow">TASK ${task.id}</span><h2>${escape(task.title)}</h2></div><span class="badge ${task.complete ? 'complete' : ''}">${state}</span></header>
    <div class="task-stats"><div><span>Simulation runs</span><strong>${task.runs.filter((run) => run.role !== 'hardware').length}</strong></div><div><span>Measurement runs</span><strong>${task.runs.filter((run) => run.role === 'hardware').length}</strong></div><div><span>Task budget used / total</span><strong>${number(task.evidence?.budget?.used)} / ${number(task.evidence?.budget?.total)}</strong></div><div><span>Conclusion</span><strong class="conclusion">${escape(label(task.conclusion?.conclusion))}</strong></div></div>
    <p class="task-note">${task.evidence ? `Selected configuration: <strong>${escape(task.evidence.selected)}</strong>. ${number(task.evidence.budget?.remaining)} measurement runs remain. Captured ${escape(task.evidence.capturedAt || '')}.` : 'This page can be regenerated while recording continues. Missing artifacts are shown as pending.'}</p>
    <div class="video-panel"><div><h3>Full walkthrough</h3><p>Original recording speed (1×). Use the native video controls to pause or seek.</p></div>${task.videos.length ? `<video controls preload="metadata"${task.images.length ? ` poster="${artifact(`${task.images[0][0]}.png`)}"` : ''}>${task.videos.map((name) => `<source src="${artifact(name)}" type="video/${name.endsWith('.mp4') ? 'mp4' : 'webm'}">`).join('')}Your browser cannot play this video. Use the download link below.</video><p class="downloads">${task.videos.map((name) => `<a href="${artifact(name)}">Download ${escape(path.extname(name).slice(1).toUpperCase())}</a>`).join('')}</p>` : '<p class="pending">The walkthrough video is not ready yet.</p>'}</div>
    <div class="section-heading"><h3>Screenshot sequence</h3><span>${task.images.length} / ${stages.length} captured</span></div><div class="gallery">${stages.map(([name, title], index) => (task.files.has(`${name}.png`) ? `<figure><a href="${artifact(`${name}.png`)}" target="_blank" rel="noreferrer"><img loading="lazy" src="${artifact(`${name}.png`)}" alt="Task ${task.id}: ${escape(title)}"></a><figcaption><span>${String(index + 1).padStart(2, '0')}</span>${escape(title)}</figcaption></figure>` : `<figure class="missing"><div>Not captured yet</div><figcaption><span>${String(index + 1).padStart(2, '0')}</span>${escape(title)}</figcaption></figure>`)).join('')}</div>
    ${renderRuns(task)}
    <div class="section-heading"><h3>Full conversation</h3><span>${task.messages.filter((message) => message.role === 'agent').length} assistant replies</span></div><p class="conversation-note">${task.evidence ? `Provider: ${escape(task.evidence.provider)} · Model: ${escape(task.evidence.model)} · Codex ${escape(task.evidence.codex)}. Input tokens: ${number(usage.input_tokens)}; output tokens: ${number(usage.output_tokens)}.` : 'The transcript will appear when this task completes.'} Student prompts are scripted for this demonstration. Assistant messages are the model responses, displayed in full as plain text.</p><div class="conversation">${task.messages.length ? task.messages.map((message, index) => `<article class="message ${message.role}" id="task-${task.id}-message-${index + 1}"><header><strong>${message.role === 'student' ? 'Student' : message.role === 'agent' ? 'Assistant' : 'Error'}</strong><a href="#task-${task.id}-message-${index + 1}">#${index + 1}</a></header><div class="message-body">${escape(message.text)}</div></article>`).join('') : '<p class="pending">No completed transcript is available yet.</p>'}</div>
    <footer class="task-footer"><span>Browser errors: ${task.browserErrors === null ? 'not reported yet' : task.browserErrors}</span><div class="downloads">${downloads.map((name) => `<a href="${artifact(name)}">${escape(name)}</a>`).join('')}</div></footer>
  </section>`;
}

const records = await Promise.all(tasks.map(collect));
const captured = records.filter((task) => task.complete).length;
const generated = new Date().toISOString();
const html = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><title>hello-mlsys · Task walkthroughs</title><style>
:root{color-scheme:light;--page:#f6f7f3;--surface:#fff;--ink:#23302b;--muted:#59685f;--line:#dce3dc;--green:#2f6953;--wash:#eaf3ec;--amber:#775627}*{box-sizing:border-box}body{margin:0;background:var(--page);color:var(--ink);font:16px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif}a{color:var(--green);text-underline-offset:3px}a:hover{text-decoration-thickness:2px}a:focus-visible,summary:focus-visible,video:focus-visible{outline:3px solid #5b987a;outline-offset:4px}.container{max-width:1380px;margin:auto;padding:40px 32px 64px}.brand{font-size:14px;font-weight:700;color:var(--green)}.hero{padding:30px 0 34px;max-width:950px}.eyebrow{font-size:12px;letter-spacing:.12em;font-weight:700;color:var(--green)}h1{font-size:clamp(32px,4vw,54px);line-height:1.15;letter-spacing:-.035em;margin:12px 0 20px}h2{font-size:29px;letter-spacing:-.025em;line-height:1.25;margin:7px 0 0}h3{font-size:20px;margin:0}p{margin:10px 0 18px}.hero p{font-size:18px;color:var(--muted)}.notice{border:1px solid #e4dcc9;background:#f7f1e5;color:#5e4d2f;padding:18px 22px;border-radius:12px}.task-nav{display:flex;gap:12px;flex-wrap:wrap;margin:28px 0}.task-nav a{padding:12px 18px;background:var(--surface);border:1px solid var(--line);border-radius:9px;text-decoration:none;font-weight:600}.task-nav small{margin-left:12px;color:var(--muted);font-weight:400}.task{margin-top:38px;padding:30px;background:var(--surface);border:1px solid var(--line);border-radius:16px;scroll-margin-top:20px}.task-header,.section-heading,.task-footer{display:flex;align-items:center;justify-content:space-between;gap:20px}.badge{font-size:12px;border:1px solid var(--line);padding:5px 11px;border-radius:20px;white-space:nowrap;color:var(--muted)}.badge.complete{background:var(--wash);color:var(--green);border-color:#c2ddcb}.task-stats{display:grid;grid-template-columns:repeat(3,1fr) 1.6fr;margin:28px 0 16px;background:var(--page);border-radius:10px}.task-stats>div{padding:16px 20px;display:grid;gap:5px}.task-stats span,.task-note,.section-heading>span,.conversation-note,.task-footer{font-size:13px;color:var(--muted)}.task-stats strong{font-size:26px}.task-stats strong.conclusion{font-size:17px;align-self:center}.video-panel{padding:22px;margin:26px 0;border:1px solid var(--line);border-radius:12px}.video-panel p{font-size:14px;color:var(--muted)}video{display:block;width:100%;max-height:760px;margin-top:18px;background:#18241e;border-radius:8px}.downloads{display:flex;gap:16px;flex-wrap:wrap;font-size:13px}.section-heading{margin:28px 0 16px}.gallery{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}figure{margin:0;border:1px solid var(--line);border-radius:9px;overflow:hidden}figure>a{display:block;background:var(--page)}figure img{width:100%;height:auto;aspect-ratio:1600/1050;object-fit:cover;object-position:top;display:block}figcaption{padding:11px 13px;font-size:13px;line-height:1.4;display:flex;gap:10px}figcaption span{color:var(--green);font-weight:700}.missing>div{aspect-ratio:1600/1050;display:grid;place-items:center;background:var(--page);color:var(--muted);font-size:13px}.pending{padding:18px;border:1px dashed var(--line);background:var(--page);border-radius:8px;color:var(--muted)}.metrics{margin:28px 0;padding:16px 18px;border:1px solid var(--line);border-radius:10px}.metrics summary{cursor:pointer;font-weight:600}.table-scroll{overflow-x:auto;margin-top:16px}table{width:100%;border-collapse:collapse;white-space:nowrap;font-size:13px}th,td{text-align:left;border-bottom:1px solid var(--line);padding:11px 13px}th{background:var(--page);font-weight:600}th small,td small{display:block;font-size:11px;color:var(--muted);font-weight:400}.numeric{text-align:right;font-variant-numeric:tabular-nums}.conversation{display:grid;gap:15px;max-width:1000px}.message{border:1px solid var(--line);border-radius:10px;padding:20px 23px;background:var(--surface)}.message.student{background:var(--page);margin-right:5%}.message.agent{border-left:4px solid var(--green)}.message.error{border-color:#c48170}.message header{display:flex;justify-content:space-between;margin-bottom:12px;font-size:13px}.message header a{font-weight:400}.message-body{white-space:pre-wrap;overflow-wrap:anywhere;font-size:15px;line-height:1.7}.task-footer{margin-top:24px;padding-top:18px;border-top:1px solid var(--line)}.page-footer{margin-top:30px;color:var(--muted);font-size:12px} @media(max-width:950px){.gallery{grid-template-columns:repeat(2,minmax(0,1fr))}.task-stats{grid-template-columns:repeat(2,1fr)}}@media(max-width:600px){.container{padding:22px 16px 36px}.task{padding:20px 16px}.task-header,.task-footer{align-items:flex-start;flex-direction:column}.gallery{grid-template-columns:1fr}.task-stats>div{padding:13px}.task-stats strong{font-size:22px}.video-panel{padding:14px}.message{padding:16px}.task-nav{gap:8px}.task-nav a{width:100%}h2{font-size:25px}}@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important}}
</style></head><body><main class="container"><div class="brand">hello-mlsys / review artifacts</div><header class="hero"><span class="eyebrow">THREE TASKS · STUDENT-CONTROLLED EXPERIMENTS</span><h1>Task walkthroughs</h1><p>Screenshots, full conversations, execution evidence and original-speed video for tasks 9.1, 9.2 and 9.3.</p><strong>${captured} of 3 task recordings complete</strong></header><aside class="notice"><strong>Real model conversations; synthetic performance data.</strong> The recorder calls SoCLaaS Qwen through the pinned Codex integration. Students' demonstration prompts are scripted, and the assistant's replies are captured without rewriting. Simulation metrics come from the fixture; measurements come from the hardware stand-in. These results do not establish real GPU performance. Only each task's available artifacts are presented below.</aside><nav class="task-nav" aria-label="Task recordings">${records.map((task) => `<a href="#task-${task.id}">${task.id} · ${escape(task.title)}<small>${task.complete ? 'Complete' : 'Pending'}</small></a>`).join('')}</nav>${records.map(renderTask).join('')}<footer class="page-footer">Generated ${escape(generated)}. This page loads local artifacts only and includes no API credentials, private model reasoning or internal tool audit.</footer></main></body></html>`;

const markdown = `# Task walkthroughs

Open [the evidence gallery](index.html) for screenshots, full conversations, run metrics and video controls.

The assistant conversations use real SoCLaaS Qwen responses through the pinned Codex integration. Student demonstration prompts are scripted. Simulation and measurement metrics are synthetic fixture/stand-in data and do not establish real GPU performance. Videos retain their original recording speed (1x).

${captured} of 3 task recordings are complete. Missing files are pending; run the index generator again after recording finishes.

| Task | Status | Screenshots | Model | Conclusion |
| --- | --- | --- | --- | --- |
${records.map((task) => `| [${task.id}: ${task.title}](index.html#task-${task.id}) | ${task.complete ? 'Complete' : 'Pending'} | ${task.images.length}/${stages.length} | ${markdownText(task.evidence?.model || 'Not reported yet')} | ${markdownText(label(task.conclusion?.conclusion))} |`).join('\n')}

${records
  .map(
    (task) => `## Task ${task.id}: ${task.title}

${task.videos.map((name) => `- [Video (${path.extname(name).slice(1).toUpperCase()})](${href(path.join(task.directory, name))})`).join('\n')}
${['conversation.md', 'conversation.json', 'evidence.json']
  .filter((name) => task.files.has(name))
  .map((name) => `- [${name}](${href(path.join(task.directory, name))})`)
  .join('\n')}
${task.images.map(([name, title]) => `- [${title}](${href(path.join(task.directory, `${name}.png`))})`).join('\n')}
${task.files.size ? '' : 'No artifacts have been recorded yet.\n'}`,
  )
  .join('\n')}
Generated ${generated}. The gallery reads only the recorder's public evidence, screenshots, videos and conversation files; it does not read credentials or internal model audit logs.
`;
await mkdir(output, { recursive: true });
await atomic(path.join(output, 'index.html'), html);
await atomic(path.join(output, 'README.md'), markdown);
console.log(`Evidence gallery: ${path.join(output, 'index.html')} (${captured}/3 complete)`);
