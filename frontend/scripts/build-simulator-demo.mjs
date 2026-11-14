// Package completed recordings with chapter navigation and unchanged transcripts.
import { execFileSync } from 'node:child_process';
import { lstat, readFile, readdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const project = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const input = path.resolve(
  process.env.MLAB_REVIEW_DIR || path.join(project, 'workspaces/simulator-demo-2026-11-14/videos'),
);
const output = path.dirname(input);
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
const link = (file) =>
  path.relative(output, file).split(path.sep).map(encodeURIComponent).join('/');
const time = (seconds) =>
  `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;
const timestamp = (seconds) => new Date(Math.round(seconds * 1000)).toISOString().slice(11, 23);
const names = {
  9.2: ['完整探索', '并发数、吞吐量与延迟余量'],
  9.1: ['端侧部署', '同精度模型、内存与质量边界'],
  9.3: ['依赖任务', '副本数、依赖释放与完成时间'],
};
const captions = {
  '04-compare.png': '并排查看模拟结果',
  '05-inline-reference.png': '输入 @ 选择引用记录',
  '07-request-inspection.png': '查看最慢请求的时序',
  '11-sandbox-comparison-confirmation.png': '确认沙盒执行内容和预算',
  '14-validation-confirmation.png': '重复运行与保留负载验证',
  '16-paired-comparison.png': '对比模拟和沙盒结果',
  '18-student-observation.png': '记录自己的观察',
  '20-report.png': '实验报告',
  '22-clean-conversation.png': '从干净的会话重新开始',
  '25-evidence-review.png': '引用记录，复核解释的证据',
  '26-report-status.png': '复核未评估与未完成的状态',
  '25-evidence-correction.png': '用运行记录核对 Agent 的解释',
};
const records = [];
const publicFiles = ['index.html', 'README.md', 'manifest.json'];
for (const task of ['9.2', '9.1', '9.3']) {
  const folder = path.join(input, `task-${task}`);
  const evidence = JSON.parse(await readFile(path.join(folder, 'evidence.json')));
  if (evidence.outcome !== 'complete' || evidence.browserErrors.length)
    throw Error(`Task ${task} has not completed successfully`);
  const probe = JSON.parse(
    execFileSync(
      'ffprobe',
      [
        '-v',
        'error',
        '-show_streams',
        '-show_format',
        '-of',
        'json',
        path.join(folder, 'walkthrough.mp4'),
      ],
      { encoding: 'utf8' },
    ),
  );
  const duration = Number(probe.format.duration);
  const stream = probe.streams.find((item) => item.codec_type === 'video');
  if (!(duration > 0) || stream.width !== 1600 || stream.height !== 1000)
    throw Error(`Unexpected video dimensions or duration for ${task}`);
  const chapters = JSON.parse(await readFile(path.join(folder, 'chapters.json')));
  if (
    chapters.some(
      (chapter, index) =>
        !Number.isFinite(chapter.seconds) ||
        chapter.seconds < 0 ||
        chapter.seconds >= duration ||
        (index && chapter.seconds <= chapters[index - 1].seconds),
    )
  )
    throw Error(`Invalid chapter times for ${task}`);
  const vtt =
    'WEBVTT\n\n' +
    chapters
      .map(
        (chapter, index) =>
          `${index + 1}\n${timestamp(chapter.seconds)} --> ${timestamp(Math.min(duration, chapters[index + 1] ? chapters[index + 1].seconds - 0.02 : duration))}\n${chapter.title}\n`,
      )
      .join('\n');
  await writeFile(path.join(folder, 'chapters.vtt'), vtt);
  const messages = JSON.parse(await readFile(path.join(folder, 'conversation.json')));
  const images = (await readdir(folder))
    .filter((name) => /^\d{2}-[a-z0-9-]+\.png$/.test(name))
    .sort();
  const segments = evidence.segments || [{ file: 'walkthrough.webm' }];
  if (segments.some((segment) => !/^(walkthrough|part-\d{2})\.webm$/.test(segment.file)))
    throw Error(`Unexpected recording segment name for ${task}`);
  const files = [
    'walkthrough.mp4',
    'walkthrough.webm',
    'chapters.json',
    'chapters.raw.json',
    'edit-list.json',
    'chapters.vtt',
    'conversation.md',
    'conversation.json',
    'tool-activity.json',
    'evidence.json',
    'report.json',
    ...images,
    ...segments.map((segment) => segment.file).filter((name) => name !== 'walkthrough.webm'),
  ];
  for (const name of files) {
    if (!(await lstat(path.join(folder, name))).isFile())
      throw Error(`Not a regular file: ${name}`);
    publicFiles.push(path.relative(output, path.join(folder, name)));
  }
  records.push({
    task,
    title: names[task][0],
    topic: names[task][1],
    duration,
    displayDuration: time(duration),
    video: link(path.join(folder, 'walkthrough.mp4')),
    raw: link(path.join(folder, 'walkthrough.webm')),
    transcript: link(path.join(folder, 'conversation.md')),
    evidence: link(path.join(folder, 'evidence.json')),
    subtitles: link(path.join(folder, 'chapters.vtt')),
    chapterText: vtt,
    segments: segments.map((segment) => ({
      ...segment,
      url: link(path.join(folder, segment.file)),
    })),
    chapters,
    poster: link(path.join(folder, '25-evidence-review.png')),
    simulations: evidence.runs.filter((run) => run.role === 'simulator').length,
    measurements: evidence.runs.filter((run) => run.role === 'hardware').length,
    turns: messages.filter((entry) => entry.role === 'student').length,
    images: images.map((name) => ({
      url: link(path.join(folder, name)),
      name,
      caption: captions[name] || name,
    })),
    messages: messages.filter((entry) => ['student', 'agent', 'error'].includes(entry.role)),
    conclusion: evidence.conclusions.at(-1)?.conclusion,
    reviewNote:
      '本次使用真实模拟器 API 和明确选择的 Qwen3 BF16/RX9070XT 联调配置。沙盒仍是演示数据，模型质量尚未评估。末段保留了对早先不严谨解释的追问与复核；对话原文未改写，报告保留未解决事项。',
  });
}
for (const name of ['verification.json', 'player-check.json', 'player-preview.png']) {
  try {
    if ((await lstat(path.join(output, name))).isFile()) publicFiles.push(name);
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
}
const manifest = {
  recordedAt: new Date().toISOString(),
  speed: 1,
  edited: true,
  editPolicy: 'Only long model waits are shortened; original footage and edit lists are retained.',
  performance: 'Uncalibrated simulator API predictions; synthetic sandbox stand-in',
  records,
};
await writeFile(path.join(output, 'manifest.json'), JSON.stringify(manifest, null, 2));
const recordingNote = '操作与回复保持 1×；仅剪短长时间等待。原始录像和剪辑清单一并保留。';
const data = JSON.stringify(records).replaceAll('<', '\\u003c');
const page = `<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>hello-mlsys · 探索操作演示</title><style>
:root{color-scheme:light;--ink:#192e35;--muted:#677e82;--line:#dce5df;--green:#28694f;--paper:#f5f6f1}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.65 system-ui,-apple-system,"Noto Sans CJK SC",sans-serif}button,a{touch-action:manipulation}a{color:var(--green);text-underline-offset:4px}button{font:inherit;cursor:pointer}main{max-width:1440px;margin:auto;padding:30px 32px 80px}.brand{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--line);padding-bottom:20px}.brand strong{font-size:21px;letter-spacing:-.6px}.brand span,.eyebrow{color:var(--muted);font-size:12px;letter-spacing:1px}header{display:grid;grid-template-columns:1.5fr 1fr;gap:56px;align-items:end;padding:42px 0 32px}h1{font-size:clamp(28px,3.3vw,46px);line-height:1.3;letter-spacing:-1.5px;margin:10px 0 16px}h2{font-size:22px;margin:0 0 12px}p{margin:8px 0}header p{max-width:640px;color:var(--muted)}.notice{border-left:3px solid var(--green);padding-left:20px;font-size:13px}.notice strong{color:var(--green)}.player-layout{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:22px;align-items:start}.screen{background:#111b20;border-radius:12px;overflow:hidden;box-shadow:0 18px 48px #203b2e18}video{width:100%;display:block;aspect-ratio:1.6;background:#111b20}video::cue{font-size:16px;background:#142b37d9}.video-bar{padding:14px 18px;display:flex;justify-content:space-between;gap:20px;color:#e2e9df;font-size:13px}.video-bar a{color:#b1d5bf}.task-tabs{display:grid;gap:10px;margin-bottom:20px}.task{background:white;border:1px solid var(--line);text-align:left;border-radius:9px;padding:14px 16px;color:var(--ink)}.task.active{border-color:var(--green);box-shadow:inset 3px 0 var(--green)}.task strong,.task small{display:block}.task small{color:var(--muted);margin-top:3px}.chapter-panel{border:1px solid var(--line);border-radius:10px;background:white;padding:18px}.chapter-panel h2{font-size:16px}.chapters{max-height:480px;overflow:auto;display:grid;gap:6px}.chapter{border:0;background:transparent;text-align:left;padding:8px 5px;display:grid;grid-template-columns:43px 1fr;gap:8px;font-size:12px;border-radius:5px;color:var(--ink)}.chapter:hover,.chapter.current{background:#edf3ec}.chapter time{font-variant-numeric:tabular-nums;color:var(--green)}.summary{display:flex;gap:22px;flex-wrap:wrap;padding:22px 0;color:var(--muted);font-size:13px}.summary strong{font-size:20px;color:var(--ink);margin-right:5px}.section{margin-top:36px;border-top:1px solid var(--line);padding-top:26px}.section-head{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:18px}.photos{display:grid;grid-template-columns:repeat(3,1fr);gap:15px}.photos a{display:block;color:var(--muted);text-decoration:none;font-size:12px}.photos img{width:100%;border:1px solid var(--line);border-radius:8px;aspect-ratio:1.6;object-fit:cover;object-position:top;display:block;margin-bottom:5px}details{border:1px solid var(--line);background:white;border-radius:8px;padding:18px}summary{cursor:pointer;color:var(--green)}.transcript{max-width:900px;margin:22px auto}.message{padding:20px 24px;border:1px solid var(--line);border-radius:9px;margin:16px 0}.message.student{background:#f0f5ef}.message h3{font-size:13px;margin:0 0 10px;color:var(--green)}.message pre{font:14px/1.85 inherit;white-space:pre-wrap;overflow-wrap:anywhere;margin:0}.footer{font-size:12px;color:var(--muted);margin-top:34px}.keyboard{font-size:12px;color:var(--muted)}button:focus-visible,a:focus-visible,summary:focus-visible{outline:3px solid #8dafc0;outline-offset:3px}@media(max-width:1000px){.player-layout{grid-template-columns:1fr}.sidebar{display:grid;grid-template-columns:1fr 1fr;gap:20px}.chapters{max-height:250px}header{gap:20px}}@media(max-width:650px){main{padding:18px 14px 40px}header{display:block;padding:26px 0}.notice{margin-top:22px}.sidebar{display:block}.photos{grid-template-columns:1fr 1fr}.brand span{display:none}.video-bar{flex-wrap:wrap}.section-head{align-items:start;flex-direction:column}}
.message pre{font-family:inherit;font-size:14px;line-height:1.85}
.video-frame{position:relative}.poster-cover{position:absolute;inset:0;border:0;padding:0;background:#14221c;display:grid;place-items:center;width:100%;height:100%;color:white}.poster-cover[hidden]{display:none}.poster-cover img{position:absolute;inset:0;width:100%;height:100%;object-fit:contain}.poster-cover span{position:relative;padding:14px 24px;border-radius:28px;background:#205e47ee;box-shadow:0 4px 22px #122b2440;font-weight:600}
</style></head><body><main>
<div class="brand"><strong>hello-mlsys</strong><span>EXPLORATION DEMO · NORMAL SPEED</span></div>
<header><div><span class="eyebrow">从一个问题开始</span><h1>和 Agent 一起，<br>把想法变成实验。</h1><p>先讨论，再尝试；随时调整配置、追问结果。这里记录了三个任务的实际操作，从自由提问一直到实验报告。</p></div><div class="notice"><strong>正常速度 · 实际操作 · 真实模型对话</strong><p>学生操作按演示脚本执行，Agent 回复来自 SoC Qwen。${recordingNote}</p><p>真实模拟器 API · Qwen3 BF16 / RX9070XT 联调配置。模拟未经实机校准，沙盒仍为演示数据；原课程基线尚未验证。</p></div></header>
<div class="player-layout"><div><div class="screen"><div class="video-frame"><video id="video" controls preload="metadata" playsinline></video><button id="poster-cover" class="poster-cover" aria-label="播放演示"><img alt="实验界面预览"><span>▶ 播放演示</span></button></div><div class="video-bar"><span id="current-title"></span><a id="download" download>下载 MP4</a><a id="original" download>WebM</a></div></div><div class="summary" id="summary"></div><p class="keyboard">点击右侧章节跳转。播放器默认 1×，可全屏查看细节。</p></div><aside class="sidebar"><div class="task-tabs" id="tasks"></div><div class="chapter-panel"><h2>操作章节</h2><nav class="chapters" id="chapters" aria-label="操作章节"></nav></div></aside></div>
<section class="section"><div class="section-head"><h2>关键画面</h2><a id="evidence">查看执行记录</a></div><div class="photos" id="photos"></div></section>
<section class="section"><div class="section-head"><h2>实际对话</h2><a id="transcript-link" download>下载完整对话与确认卡</a></div><p id="review-note" class="keyboard"></p><details><summary>展开本任务的全部问答</summary><div class="transcript" id="transcript"></div></details></section>
<p class="footer">SoC Qwen 3.8 27B · Codex 0.167.2 · 独立课程目录与 Codex 状态。字幕轨道提供章节标题；原始回复及确认卡保存在对话文件中。</p>
</main><script>
const records=${data};
let video=document.getElementById('video');
const cover=document.getElementById('poster-cover');
cover.addEventListener('click',()=>video.play().catch(()=>{}));
let current;
let trackURL;
const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const time=seconds=>Math.floor(seconds/60)+':'+String(Math.floor(seconds%60)).padStart(2,'0');
function choose(task,seconds=0,play=false){
current=records.find(record=>record.task===task)||records[0];
video.pause();Array.from(video.textTracks).forEach(track=>{track.mode='disabled';});
video.removeAttribute('src');video.load();
const replacement=document.createElement('video');replacement.id='video';replacement.controls=true;replacement.preload='auto';replacement.playsInline=true;
video.replaceWith(replacement);video=replacement;
video.poster=current.poster;video.src=current.video;video.playbackRate=1;
cover.querySelector('img').src=current.poster;cover.hidden=seconds>0;
video.onplay=()=>{cover.hidden=true;};
if(trackURL)URL.revokeObjectURL(trackURL);
trackURL=URL.createObjectURL(new Blob([current.chapterText],{type:'text/vtt'}));
const track=document.createElement('track');track.kind='subtitles';track.label='章节提示';track.srclang='zh';track.src=trackURL;video.append(track);
video.onloadedmetadata=()=>{if(seconds>0)video.currentTime=Math.min(seconds,video.duration);if(play)video.play().catch(()=>{});};
video.ontimeupdate=()=>{let active=0;current.chapters.forEach((chapter,index)=>{if(chapter.seconds<=video.currentTime)active=index;});document.querySelectorAll('.chapter').forEach((button,index)=>button.classList.toggle('current',index===active));};
document.getElementById('current-title').textContent='任务 '+current.task+' · '+current.title+' · '+current.displayDuration;
document.getElementById('download').href=current.video;document.getElementById('original').href=current.raw;
document.getElementById('evidence').href=current.evidence;document.getElementById('transcript-link').href=current.transcript;
document.getElementById('review-note').textContent=current.reviewNote;
document.querySelectorAll('.task').forEach(button=>{button.classList.toggle('active',button.dataset.task===current.task);button.setAttribute('aria-pressed',String(button.dataset.task===current.task));});
document.getElementById('summary').innerHTML='<span><strong>'+current.turns+'</strong>次学生提问</span><span><strong>'+current.simulations+'</strong>次模拟</span><span><strong>'+current.measurements+'</strong>次沙盒运行</span><span>含笔记、结论与报告</span>';
document.getElementById('chapters').innerHTML=current.chapters.map((chapter,index)=>'<button class="chapter" data-index="'+index+'"><time>'+time(chapter.seconds)+'</time><span>'+escape(chapter.title)+'</span></button>').join('');
document.getElementById('chapters').scrollTop=0;
document.querySelectorAll('.chapter').forEach(button=>button.addEventListener('click',()=>{const at=current.chapters[Number(button.dataset.index)].seconds;video.currentTime=at;video.play().catch(()=>{});history.replaceState(null,'','#'+current.task+'@'+Math.floor(at));}));
const preferred=['04-compare.png','05-inline-reference.png','07-request-inspection.png','11-sandbox-comparison-confirmation.png','14-validation-confirmation.png','16-paired-comparison.png','18-student-observation.png','20-report.png','22-clean-conversation.png','25-evidence-correction.png','25-evidence-review.png','26-report-status.png'];
document.getElementById('photos').innerHTML=current.images.filter(image=>preferred.includes(image.name)).map(image=>'<a href="'+image.url+'" target="_blank" rel="noopener"><img loading="lazy" src="'+image.url+'" alt="'+escape(image.caption)+'">'+escape(image.caption)+'</a>').join('');
document.getElementById('transcript').innerHTML=current.messages.map(message=>'<article class="message '+escape(message.role)+'"><h3>'+({student:'学生',agent:'Agent',error:'执行提示'}[message.role])+'</h3><pre>'+escape(message.text)+'</pre></article>').join('');
history.replaceState(null,'','#'+current.task+'@'+Math.floor(seconds));
}
document.getElementById('tasks').innerHTML=records.map(record=>'<button class="task" data-task="'+record.task+'"><strong>'+record.task+' · '+record.title+' <span style="float:right">'+record.displayDuration+'</span></strong><small>'+record.topic+'</small></button>').join('');
document.querySelectorAll('.task').forEach(button=>button.addEventListener('click',()=>choose(button.dataset.task)));
const match=location.hash.match(/^#(9\\.[123])@(\\d+)$/);choose(match?.[1]||'9.2',Number(match?.[2]||0));
</script></body></html>`;
await writeFile(path.join(output, 'index.html'), page);
const markdown = `# Simulator integration video demo

Open [the chapter player](index.html) for all three recordings, screenshots, and unchanged conversations. Playback is 1x. Only the middle of long model waits is removed; each task includes its original WebM, original chapter timestamps and an exact edit list.

Scripted student actions use the visible UI. Replies come from the real SoC Qwen 3.8 27B API through isolated Codex 0.167.2. The simulator is the unmodified MLSys 1.20.0 service, accessed only over HTTP. The explicitly selected protocol-v1.20 exercise uses Qwen3 BF16 models on RX9070XT; these are not the original course baselines. Simulation is uncalibrated. Sandbox performance is synthetic. Model quality is not evaluated.

${records
  .map(
    (record) => `## Task ${record.task} (${record.displayDuration})

[MP4](${record.video}) · [Original WebM](${record.raw}) · [Transcript](${record.transcript}) · [Evidence](${record.evidence})

${record.simulations} simulations, ${record.measurements} sandbox runs, ${record.turns} student messages. Conclusion: ${record.conclusion}.

${record.chapters.map((chapter) => `- ${time(chapter.seconds)} — ${chapter.title}`).join('\n')}`,
  )
  .join('\n\n')}

## Reproduction

See integrations/mlsys-simulator/APP_INTEGRATION.zh-CN.md in the repository. Credentials, course state and private runtime logs are excluded from this package.
`;
await writeFile(path.join(output, 'README.md'), markdown);
if (process.argv.includes('--zip')) {
  execFileSync(
    path.join(project, '.venv/bin/python'),
    [
      '-c',
      [
        'import json, pathlib, sys, zipfile',
        'root = pathlib.Path(sys.argv[1]).resolve()',
        'with zipfile.ZipFile(root / "simulator-demo.zip", "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:',
        '    for name in json.loads(sys.stdin.read()):',
        '        file = root / name',
        '        assert not file.is_symlink() and file.is_file() and file.resolve().is_relative_to(root)',
        '        archive.write(file, name)',
      ].join('\n'),
      output,
    ],
    { input: JSON.stringify(publicFiles) },
  );
}
console.log(
  JSON.stringify(
    {
      player: path.join(output, 'index.html'),
      videos: records.map(({ task, displayDuration, simulations, measurements }) => ({
        task,
        displayDuration,
        simulations,
        measurements,
      })),
    },
    null,
    2,
  ),
);
