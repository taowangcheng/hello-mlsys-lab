// Build a portable review gallery from explicitly allowed public recording artifacts.
import { execFileSync } from 'node:child_process';
import { lstat, mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const project = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const input = path.resolve(
  process.env.MLAB_REVIEW_DIR || path.join(project, 'workspaces/studio-review/live'),
);
const output = path.dirname(input);
const archive = process.argv.slice(2).includes('--zip');
if (process.argv.slice(2).some((argument) => argument !== '--zip'))
  throw Error('Usage: node scripts/build-review.mjs [--zip]');
const tasks = [
  {
    id: '9.1',
    title: 'An assistant that fits the device',
    topic: 'Model size, precision and memory',
  },
  {
    id: '9.2',
    title: 'Serving within a latency target',
    topic: 'Concurrency, throughput and tail latency',
  },
  {
    id: '9.3',
    title: 'Understanding dependent work',
    topic: 'Precision, dependencies and the critical path',
  },
];
const stages = [
  ['00-existing-conversation', 'Before starting a new conversation'],
  ['01-conversation', 'A clean conversation'],
  ['02-conceptual-question', 'Understand the task before running anything'],
  ['03-requested-simulation', 'Ask for two simulations'],
  ['03a-compare-and-conversation', 'Compare results while discussing them'],
  ['04-history-picker', 'Attach a previous result'],
  ['05-evidence-discussion', 'Discuss the recorded evidence'],
  ['06-measurement-confirmation', 'Review the exact measurement request'],
  ['07-measurement-discussion', 'Interpret the measured result'],
  ['08-execution-history', 'Explore execution history'],
  ['09-measurement-analysis', 'Inspect predictions and measurements'],
  ['09a-report-and-conversation', 'Read the report beside the conversation'],
  ['10-mobile-conversation', 'Continue on a narrow screen'],
  ['11-clean-new-conversation', 'Start another clean session'],
  ['12-restored-conversation', 'Return to the previous session'],
];
const highlights = [
  [
    '03a-compare-and-conversation',
    'Compare',
    'Metrics and changed settings remain beside the discussion.',
  ],
  [
    '09-measurement-analysis',
    'Investigate',
    'Inspect predictions, measured differences and uncertainty.',
  ],
  [
    '09a-report-and-conversation',
    'Report',
    'Keep evidence and open questions together, without a final conclusion.',
  ],
  [
    '11-clean-new-conversation',
    'Start fresh',
    'A new session is empty; earlier work stays in its own session.',
  ],
];
// No recursive directory scans: raw videos, server logs, credentials and courses are excluded.
const whitelist = [
  ...stages.map(([name]) => `${name}.png`),
  'failure.png',
  'failure.txt',
  'walkthrough.webm',
  'walkthrough.mp4',
  'conversation.md',
  'conversation.json',
  'summary.json',
];
const escape = (value) =>
  String(value ?? '').replace(
    /[&<>"']/g,
    (character) =>
      ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
      })[character],
  );
const number = (value) =>
  typeof value === 'number' && Number.isFinite(value)
    ? value.toLocaleString('en-US', { maximumFractionDigits: 3 })
    : '—';
const label = (value) =>
  ({
    constraints_met: 'Meets constraints',
    constraints_violated: 'Outside constraints',
    succeeded: 'Completed',
    simulator: 'Simulation',
    hardware: 'Measurement',
  })[value] || String(value ?? 'Not reported').replaceAll('_', ' ');
const link = (file) =>
  path.relative(output, file).split(path.sep).map(encodeURIComponent).join('/');
async function regular(file) {
  try {
    return (await lstat(file)).isFile();
  } catch (error) {
    if (error.code === 'ENOENT') return false;
    throw error;
  }
}
async function directory(folder) {
  try {
    return (await lstat(folder)).isDirectory();
  } catch (error) {
    if (error.code === 'ENOENT') return false;
    throw error;
  }
}
async function json(file) {
  if (!(await regular(file))) return null;
  try {
    return JSON.parse(await readFile(file, 'utf8'));
  } catch (error) {
    if (error instanceof SyntaxError || error.code === 'ENOENT') return null;
    throw error;
  }
}
async function atomic(file, content) {
  const temporary = `${file}.tmp-${process.pid}`;
  await writeFile(temporary, content);
  await rename(temporary, file);
}
await mkdir(output, { recursive: true });
const records = await Promise.all(
  tasks.map(async (task) => {
    const folder = path.join(input, `task-${task.id}`);
    const available = await directory(folder);
    const files = new Set(
      (
        await Promise.all(
          whitelist.map(async (name) =>
            available && (await regular(path.join(folder, name))) ? name : null,
          ),
        )
      ).filter(Boolean),
    );
    const summary = available ? await json(path.join(folder, 'summary.json')) : null;
    const conversation = available ? await json(path.join(folder, 'conversation.json')) : null;
    const messages = Array.isArray(conversation)
      ? conversation.filter(
          (entry) => entry && ['student', 'agent', 'card', 'error'].includes(entry.role),
        )
      : [];
    const images = stages.filter(([name]) => files.has(`${name}.png`));
    const runs = Array.isArray(summary?.runs) ? summary.runs : [];
    const replies = messages.filter(
      (message) => message.role === 'agent' && typeof message.text === 'string',
    );
    const realDialogue = summary?.provider === 'codex-soc-qwen' && replies.length > 0;
    const complete =
      summary?.outcome === 'complete' &&
      !summary.failure &&
      summary.browserErrors?.length === 0 &&
      images.length === stages.length &&
      files.has('walkthrough.webm') &&
      files.has('conversation.md') &&
      realDialogue &&
      runs.filter((run) => run.role === 'simulator' && run.status === 'succeeded').length === 2 &&
      runs.filter((run) => run.role === 'hardware' && run.status === 'succeeded').length === 1;
    const status = complete
      ? 'Complete'
      : summary?.outcome === 'failed'
        ? 'Needs attention'
        : files.size
          ? 'In progress / incomplete'
          : 'Awaiting recording';
    return {
      ...task,
      folder,
      files,
      summary,
      messages,
      replies,
      images,
      runs,
      realDialogue,
      complete,
      status,
    };
  }),
);

function imageCard(task, name, title, description = '') {
  const file = `${name}.png`;
  const image = task.files.has(file)
    ? `<a href="${link(path.join(task.folder, file))}" target="_blank" rel="noreferrer"><img src="${link(path.join(task.folder, file))}" alt="Task ${task.id}: ${escape(title)}" loading="lazy"></a>`
    : '<div class="placeholder">Not captured yet</div>';
  return `<figure>${image}<figcaption><strong>${escape(title)}</strong>${description ? `<span>${escape(description)}</span>` : ''}</figcaption></figure>`;
}
function messageCard(message, index, task) {
  const role = {
    student: 'Student',
    agent: 'Assistant',
    card: 'Measurement confirmation',
    error: 'Error',
  }[message.role];
  let body;
  if (message.role === 'card') {
    const summary = message.summary || {};
    body = `<p>State: <strong>${escape(label(message.state))}</strong> · Requested runs: ${number(summary.hardware_runs)}</p>
      <div class="table-wrap"><table><thead><tr><th>Configuration</th><th>Workload</th><th>Seed</th><th>Runs</th></tr></thead><tbody>${(summary.points || []).map((point) => `<tr><td>${escape(point.candidate)}</td><td>${escape(point.workload)}</td><td>${escape(point.seed)}</td><td>${escape(point.runs)}</td></tr>`).join('')}</tbody></table></div>
      ${summary.configs ? `<details><summary>Exact requested configurations</summary><pre>${escape(JSON.stringify(summary.configs, null, 2))}</pre></details>` : ''}`;
  } else body = `<div class="message-text">${escape(message.text || '')}</div>`;
  const refs =
    Array.isArray(message.refs) && message.refs.length
      ? `<p class="refs">References: ${message.refs.map(escape).join(' · ')}</p>`
      : '';
  return `<article class="message ${message.role}" id="task-${task.id}-message-${index + 1}"><header><strong>${role}</strong><a href="#task-${task.id}-message-${index + 1}">#${index + 1}</a></header>${body}${refs}</article>`;
}
function render(task) {
  const fileLink = (name) => link(path.join(task.folder, name));
  const downloads = [
    'conversation.md',
    'conversation.json',
    'summary.json',
    'walkthrough.webm',
    'walkthrough.mp4',
    'failure.txt',
  ].filter((name) => task.files.has(name));
  const video = ['walkthrough.mp4', 'walkthrough.webm'].filter((name) => task.files.has(name));
  return `<section class="task" id="task-${task.id}">
    <header class="task-heading"><div><span class="eyebrow">TASK ${task.id} / ${escape(task.topic)}</span><h2>${escape(task.title)}</h2></div><span class="status ${task.complete ? 'complete' : ''}">${task.status}</span></header>
    <div class="stats"><div><span>Assistant replies</span><strong>${task.replies.length}</strong></div><div><span>Completed simulations</span><strong>${task.runs.filter((run) => run.role === 'simulator' && run.status === 'succeeded').length}</strong></div><div><span>Completed measurements</span><strong>${task.runs.filter((run) => run.role === 'hardware' && run.status === 'succeeded').length}</strong></div><div><span>Task budget used / total</span><strong>${number(task.summary?.budget?.used)} / ${number(task.summary?.budget?.total)}</strong></div></div>
    <p class="metadata">${task.summary ? `Session ${escape(task.summary.session || 'not opened')} · Model ${escape(task.summary.model || 'not reported')} · Codex ${escape(task.summary.codex || 'not reported')} · Captured ${escape(task.summary.capturedAt || 'not reported')}` : 'No recording metadata yet. Regenerate this page after artifacts arrive.'}</p>
    ${task.summary?.failure ? `<p class="attention">Recording stopped: ${escape(task.summary.failure)}</p>` : ''}
    <div class="highlights">${highlights.map(([name, title, description]) => imageCard(task, name, title, description)).join('')}</div>
    <div class="video-block"><div class="section-heading"><h3>Watch the complete interaction</h3><span>Original speed · 1×</span></div>${video.length ? `<video controls preload="metadata"${task.files.has('01-conversation.png') ? ` poster="${fileLink('01-conversation.png')}"` : ''}>${video.map((name) => `<source src="${fileLink(name)}" type="video/${name.endsWith('.mp4') ? 'mp4' : 'webm'}">`).join('')}Use the video download link below if playback is unavailable.</video>` : '<p class="pending">The walkthrough video has not been exported yet.</p>'}<p class="fine">No automatic playback or speed changes. The video includes the model’s response time.</p></div>
    <details class="panel"><summary>All screenshots <span>${task.images.length} / ${stages.length}</span></summary><div class="gallery">${stages.map(([name, title]) => imageCard(task, name, title)).join('')}${task.files.has('failure.png') ? imageCard(task, 'failure', 'Recording failure') : ''}</div></details>
    <details class="panel"><summary>Full conversation <span>${task.replies.length} assistant replies</span></summary><p class="fine">${task.realDialogue ? 'These are the recorded real SoCLaaS Qwen replies, shown in full without editorial rewriting.' : 'Real-model conversation has not yet been verified by the exported provider metadata.'} Student prompts are scripted for a repeatable demonstration. References and confirmation cards are preserved.</p><div class="conversation">${task.messages.length ? task.messages.map((message, index) => messageCard(message, index, task)).join('') : '<p class="pending">No conversation has been exported yet.</p>'}</div></details>
    <details class="panel"><summary>Result summary <span>Fixture / stand-in data</span></summary><p class="fine">Performance values below are synthetic. Execution completion and constraint verdict are separate. A completed process can still violate a constraint.</p><div class="table-wrap"><table><thead><tr><th>Run</th><th>Method</th><th>Configuration</th><th>Workload</th><th>Status</th><th>Constraint verdict</th><th>Primary metric value</th></tr></thead><tbody>${task.runs.map((run) => `<tr><td>${escape(run.id)}</td><td>${escape(label(run.role))}</td><td>${escape(run.candidate)}</td><td>${escape(run.workload)}</td><td>${escape(label(run.status))}</td><td>${escape(label(run.verdict))}</td><td>${number(run.key_value)}</td></tr>`).join('')}</tbody></table></div>${!task.runs.length ? '<p class="pending">No completed results have been exported yet.</p>' : ''}<p class="fine">${task.id === '9.1' ? 'Primary metric: median first-token latency (ms).' : task.id === '9.2' ? 'Primary metric: p99 first-token latency (ms).' : 'Primary metric: total completion time (s).'} Full metric names and values remain in summary.json. Browser errors: ${task.summary ? number(task.summary.browserErrors?.length) : 'not reported'}.</p></details>
    <footer class="downloads">${downloads.length ? downloads.map((name) => `<a href="${fileLink(name)}" download>${escape(name)}</a>`).join('') : '<span>Downloads will appear as recording progresses.</span>'}</footer>
  </section>`;
}

const generated = new Date().toISOString();
const completed = records.filter((record) => record.complete).length;
const html = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src 'self' data:; media-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"><title>hello-mlsys · Studio review</title><style>
:root{color-scheme:light;--paper:#f3f3ed;--surface:#fffefa;--ink:#20372e;--muted:#62736a;--line:#dce2d7;--green:#205c43;--wash:#e7f0e5;--amber:#775025}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.65 system-ui,-apple-system,'Segoe UI',sans-serif}a{color:var(--green);text-underline-offset:4px}a:hover{text-decoration-thickness:2px}a:focus-visible,summary:focus-visible,video:focus-visible{outline:3px solid #8caa4b;outline-offset:4px}main{max-width:1440px;margin:auto;padding:32px 40px 72px}.brand{display:flex;justify-content:space-between;align-items:center;font-size:13px;letter-spacing:.04em}.brand strong{font-size:17px}.brand span{color:var(--muted)}.hero{padding:66px 0 40px;max-width:1040px}.eyebrow{font-size:11px;letter-spacing:.12em;font-weight:700;text-transform:uppercase;color:var(--green)}h1{font-size:clamp(38px,5vw,68px);line-height:1.06;letter-spacing:-.05em;margin:18px 0 24px}h1 em{font-family:Georgia,serif;font-weight:400;color:var(--green)}.hero>p{max-width:770px;font-size:19px;color:var(--muted)}h2{font-size:30px;line-height:1.18;letter-spacing:-.03em;margin:10px 0 0}h3{font-size:21px;margin:0}.notice{display:grid;grid-template-columns:1fr 1fr;gap:30px;padding:25px 28px;background:var(--wash);border:1px solid #ccdcca;border-radius:15px}.notice strong{display:block;font-size:15px;margin-bottom:5px}.notice p{font-size:14px;margin:0;color:#46604e}.task-nav{display:flex;flex-wrap:wrap;gap:12px;margin:26px 0 42px}.task-nav a{display:flex;gap:16px;align-items:center;justify-content:space-between;text-decoration:none;padding:12px 17px;border:1px solid var(--line);border-radius:10px;background:var(--surface)}.task-nav span{font-size:12px;color:var(--muted)}.task{padding:32px;margin:30px 0 0;border:1px solid var(--line);border-radius:19px;background:var(--surface);scroll-margin-top:20px}.task-heading,.section-heading{display:flex;justify-content:space-between;align-items:center;gap:20px}.status{border:1px solid var(--line);border-radius:30px;padding:6px 12px;font-size:12px;white-space:nowrap;color:var(--muted)}.status.complete{background:var(--wash);color:var(--green);border-color:#c4d9be}.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));margin:28px 0 14px;padding:14px 0;background:var(--paper);border-radius:12px}.stats>div{display:grid;gap:6px;padding:3px 22px;border-right:1px solid var(--line)}.stats>div:last-child{border:0}.stats span{font-size:12px;color:var(--muted)}.stats strong{font-size:29px;line-height:1.25;letter-spacing:-.04em;font-variant-numeric:tabular-nums}.metadata,.fine,.section-heading>span{color:var(--muted);font-size:12px}.metadata{overflow-wrap:anywhere}.highlights{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;margin-top:24px}figure{margin:0;border:1px solid var(--line);border-radius:11px;overflow:hidden}figure>a{display:block;background:var(--paper)}figure img{display:block;width:100%;aspect-ratio:1600/1050;object-fit:cover;object-position:top}.placeholder{aspect-ratio:1600/1050;display:grid;place-items:center;background:var(--paper);font-size:12px;color:var(--muted)}figcaption{padding:13px 15px;font-size:13px;line-height:1.5}figcaption strong,figcaption span{display:block}figcaption span{font-size:12px;margin-top:6px;color:var(--muted)}.video-block{padding:24px;margin-top:24px;border:1px solid var(--line);border-radius:12px}video{display:block;width:100%;max-height:740px;margin-top:20px;border-radius:8px;background:#162b20}.pending{border:1px dashed var(--line);padding:20px;border-radius:8px;color:var(--muted);font-size:14px}.attention{padding:16px;background:#fff0df;border-left:3px solid var(--amber);overflow-wrap:anywhere}.panel{border-top:1px solid var(--line);margin-top:24px;padding-top:20px}.panel>summary{cursor:pointer;font-weight:650;font-size:17px}.panel>summary span{font-weight:400;color:var(--muted);font-size:12px;margin-left:10px}.gallery{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;margin-top:20px}.conversation{display:grid;gap:13px;max-width:1050px;margin-top:20px}.message{padding:20px 24px;border:1px solid var(--line);border-radius:10px}.message.student{margin-right:4%;background:var(--paper)}.message.agent{border-left:4px solid var(--green)}.message.card{border-left:4px solid #a2893e;background:#f8f6ee}.message.error{border-color:#b1755c}.message header{display:flex;justify-content:space-between;font-size:12px;margin-bottom:12px}.message-text{white-space:pre-wrap;overflow-wrap:anywhere;font-size:14px;line-height:1.8}.refs{font-size:12px;color:var(--muted);overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}.table-wrap{overflow:auto;margin-top:16px}table{border-collapse:collapse;font-size:12px;width:100%;text-align:left;white-space:nowrap}td,th{padding:10px 13px;border-bottom:1px solid var(--line)}th{background:var(--paper)}.downloads{display:flex;gap:16px;flex-wrap:wrap;padding-top:22px;margin-top:22px;border-top:1px solid var(--line);font-size:12px;color:var(--muted)}.page-footer{margin-top:30px;font-size:12px;color:var(--muted)}@media(max-width:1100px){.highlights{grid-template-columns:repeat(2,minmax(0,1fr))}.gallery{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:650px){main{padding:24px 15px 40px}.hero{padding:40px 0 24px}.hero>p{font-size:16px}.notice{grid-template-columns:1fr;gap:18px;padding:20px}.task{padding:22px 16px}.task-heading,.section-heading{align-items:flex-start;flex-direction:column;gap:12px}h2{font-size:26px}.stats{grid-template-columns:repeat(2,minmax(0,1fr));row-gap:16px}.stats>div:nth-child(2){border:0}.stats strong{font-size:25px}.highlights,.gallery{grid-template-columns:1fr}.video-block{padding:16px}.message{padding:16px}.task-nav a{width:100%}.brand{font-size:10px}}@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important}}
</style></head><body><main><div class="brand"><strong>hello-mlsys</strong><span>STUDIO REVIEW / LOCAL ARTIFACTS</span></div><header class="hero"><span class="eyebrow">A closer look at the learning workspace</span><h1>Explore, discuss,<br>and follow the <em>evidence.</em></h1><p>Three tasks, a persistent conversation, and room for students to choose what to investigate. Review the actual interactions, the results they produced, and the places where work remains.</p><strong>${completed} of 3 recordings complete</strong>${archive ? ' · <a href="studio-review.zip" download>Download review bundle</a>' : ''}</header><aside class="notice"><div><strong>Real dialogue</strong><p>Completed recordings use real SoCLaaS Qwen 3.8 27B replies through Codex 0.167.2. Student prompts are scripted. Full replies and confirmation cards are preserved; missing or partial recordings are labeled below.</p></div><div><strong>Synthetic performance</strong><p>Simulations use the fixture and measurements use a hardware stand-in. These values support reviewing the learning experience; they do not establish performance on a real GPU. Videos play at their original speed.</p></div></aside><nav class="task-nav" aria-label="Task recordings">${records.map((task) => `<a href="#task-${task.id}"><strong>Task ${task.id}</strong><span>${task.status}</span></a>`).join('')}</nav>${records.map(render).join('')}<footer class="page-footer">Generated ${escape(generated)}. This is a static snapshot; regenerate it after new artifacts arrive. Local assets only. No external fonts, scripts or analytics.</footer></main></body></html>`;
await atomic(path.join(output, 'index.html'), html);
const markdown = `# Studio review

Open [the review gallery](index.html) for comparison, measurement, report and fresh-session screenshots, full conversations and original-speed videos.

${completed} of 3 recordings are complete. This is a snapshot generated at ${generated}; missing files remain pending until the gallery is regenerated.

Completed recordings contain real SoCLaaS Qwen 3.8 27B dialogue through Codex 0.167.2. Student prompts are scripted, while assistant replies are preserved without rewriting. Performance data comes from the synthetic fixture and hardware stand-in; it does not establish real GPU performance. Videos retain their original speed (1×).

| Task | Status | Screenshots | Assistant replies | Session |
| --- | --- | --- | --- | --- |
${records.map((task) => `| [${task.id}: ${task.title}](index.html#task-${task.id}) | ${task.status} | ${task.images.length}/${stages.length} | ${task.replies.length} | ${escape(task.summary?.session || 'Not opened')} |`).join('\n')}

${records
  .map(
    (task) => `## Task ${task.id}

${
  ['conversation.md', 'conversation.json', 'summary.json', 'walkthrough.webm', 'walkthrough.mp4']
    .filter((name) => task.files.has(name))
    .map((name) => `- [${name}](${link(path.join(task.folder, name))})`)
    .join('\n') || 'No exported files yet.'
}
`,
  )
  .join('\n')}
## Regenerate

From the repository root:

\`\`\`bash
cd frontend
node_modules/.bin/node scripts/build-review.mjs --zip
\`\`\`

The default input is \`workspaces/studio-review/live\`; \`MLAB_REVIEW_DIR\` overrides it, matching the recorder. The gallery and README are written to the input directory's parent. Omit \`--zip\` to generate only the gallery and README. The recorder runs separately and needs its own local server.

${archive ? '[Download the portable review bundle](studio-review.zip). Extract it before opening index.html so local videos and images resolve.\n\n' : ''}The archive includes only this index, this README, and explicitly allowed public screenshots, transcripts, summaries and walkthrough videos for the three task directories. It never recursively includes a course, logs, credentials or raw recorder files. Symlinks are excluded. Partial recordings can be bundled, but remain labeled incomplete.
`;
await atomic(path.join(output, 'README.md'), markdown);
if (archive) {
  const manifest = [
    'index.html',
    'README.md',
    ...records.flatMap((task) =>
      [...task.files].map((name) => path.relative(output, path.join(task.folder, name))),
    ),
  ];
  const python = path.join(project, '.venv/bin/python');
  // The manifest is passed as JSON, never interpreted by a shell or expanded recursively.
  execFileSync(
    python,
    [
      '-c',
      `import json, pathlib, sys, zipfile
root = pathlib.Path(sys.argv[1]).resolve()
target = root / 'studio-review.zip'
temporary = root / 'studio-review.zip.tmp'
with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
    for relative in json.loads(sys.argv[2]):
        source = root / relative
        if source.is_symlink() or not source.is_file() or not source.resolve().is_relative_to(root):
            raise ValueError('Artifact changed or left the review directory: ' + relative)
        archive.write(source, relative)
temporary.replace(target)
`,
      output,
      JSON.stringify(manifest),
    ],
    { stdio: 'inherit' },
  );
}
console.log(`Review gallery: ${path.join(output, 'index.html')} (${completed}/3 complete)`);
if (archive) console.log(`Review bundle: ${path.join(output, 'studio-review.zip')}`);
