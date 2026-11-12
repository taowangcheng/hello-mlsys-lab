// hello-mlsys course page: sessions on the left, each one attempt at a task from the start; the session's records in the
// middle, with their directory on top; the session's agent on the right. Everything shown comes from the server's read-only views; text is inserted with
// textContent only. Forms write the student's own words; cards ask the student before a gated command runs.
import { barChart, format, pairedChart, ratioChart, timelineChart, unitOf } from "./charts.js";
import { LANGUAGES, language, load, preferred, remember, t, tm } from "./i18n.js";

// Labels are written in English and shown through t(); server sentences go through tm().
const SOURCE = { student: "Verbatim", agent: "Agent draft", mlab: "Computed by mlab" };
const SESSION = /^\d\.\d-\d+$/;  // A session's id: its task and number, e.g. 9.2-1.
const STATUS = { draft: "Draft", approved: "Approved", submitted: "Submitted", decided: "Decided", measured: "Measured", queued: "Queued",
                 running: "Running", succeeded: "Succeeded", failed: "Failed", cancelled: "Cancelled", timed_out: "Timed out", completed: "Completed" };
const VERDICT = { constraints_met: "Constraints met", constraints_violated: "Constraints violated", not_evaluated: "Not evaluated" };
const CARD_STATE = { pending: "Pending", approved: "Approved", denied: "Denied", cancelled: "Cancelled", answered: "Answered",
                     expired: "Expired (mlab web restarted)" };
const BUSY = ["running", "waiting_approval", "waiting_answer"];
// Questions "Ask agent" puts in the message box, in the student's own voice.
const ASK = { round: "Do this round's results support my hypothesis?", run: "What does this run's result show?",
              measurement: "Do the hardware results match the simulation? If not, what might explain the gap?",
              overview: "What evidence is still missing before I can submit a conclusion?", report: "Which conclusions in this report hold up?",
              config: "How does this configuration do in simulation and on hardware?",
              compare: "Can these runs be compared? What do the differences show?" };
const MEASURE_STEPS = new Set(["points", "points_predicting", "points_approve", "measure_submit", "measure_retry", "measure_locked"]);

const app = { course: null, session: null, picking: false, states: {}, badges: {}, unread: {}, overview: null, notebook: null, record: null, recordId: null,
              tab: null, chat: [], agent: null, streaming: "", pairs: {}, expanded: {}, pending: false, pages: {} };

// ---------- small helpers ----------

const $ = (selector) => document.querySelector(selector);

function el(tag, props, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else if (["value", "checked", "disabled", "selected", "hidden", "open"].includes(key)) node[key] = value;
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  return append(node, children);
}

function append(node, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false || child === "") continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

// replaceChildren() would print null as text; this skips empty children like el() does.
function fill(node, ...children) {
  node.replaceChildren();
  return append(node, children);
}

function stored(key, fallback) {
  try {
    const value = localStorage.getItem(key);
    return value ? JSON.parse(value) : fallback;
  } catch {
    return fallback;
  }
}

function store(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Private windows and blocked storage: drafts then live only in memory.
  }
}

function debounce(fn, wait) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

function short(text, limit = 80) {
  const line = String(text || "").replace(/\s+/g, " ").trim();
  return line.length > limit ? `${line.slice(0, limit - 1)}…` : line;
}

function statusText(code) {
  return t(STATUS[code] || code || "");
}

function itemLabel(item) {
  if (item.kind === "round") return t("Simulation round {n}", { n: item.iteration });
  if (item.kind === "measurement") return t("Measurement {id}", { id: item.id });
  return t("Conclusion submitted");
}

function link(id, session = app.session) {
  return `#/${session}/${encodeURIComponent(id)}`;
}

function go(id, tab, session = app.session) {
  const target = link(id, session) + (tab ? `?tab=${tab}` : "");
  if (location.hash === target) route();
  else location.hash = target;
}

function button(label, kind, onclick, extra = {}) {
  return el("button", { type: "button", class: `btn ${kind || ""}`.trim(), text: label, onclick, ...extra });
}

function chip(text, cls = "") {
  return el("span", { class: `chip ${cls}`.trim(), text });
}

function src(source) {
  return el("span", { class: `src src-${source}`, text: t(SOURCE[source]) });
}

function section(title, source, ...content) {
  return el("section", { class: "section" }, el("header", {}, el("h3", { text: title }), source ? src(source) : null), ...content);
}

function textBlock(text, empty = "Not written yet") {
  return text && String(text).trim() ? el("div", { class: "text", text }) : el("p", { class: "missing", text: t(empty) });
}

// A card or preview fact from the server: a sentence, or a list of them shown one per line.
function factValue(value) {
  return Array.isArray(value) ? el("div", {}, value.map((line) => el("div", { text: tm(line) }))) : tm(value);
}

function kv(pairs) {
  return el("dl", { class: "kv" }, pairs.filter(([, value]) => value !== undefined && value !== null && value !== "")
    .flatMap(([key, value]) => [el("dt", { text: key }), el("dd", {}, value instanceof Node ? value : String(value))]));
}

function table(columns, rows, onRow) {
  const head = el("thead", {}, el("tr", {}, columns.map((column) => el("th", { class: column.num ? "num" : null, text: column.label }))));
  const body = el("tbody", {}, rows.map((row, index) => el("tr", { class: onRow ? "clickable" : null, onclick: onRow ? () => onRow(index) : null },
    row.map((cell, column) => el("td", { class: columns[column].num ? "num" : null }, cell ?? "—")))));
  return el("div", { class: "table-wrap" }, el("table", {}, head, body));
}

function verdictCell(verdict) {
  if (!verdict) return "—";
  return el("span", { class: verdict === "constraints_met" ? "ok" : verdict === "constraints_violated" ? "bad" : null, text: t(VERDICT[verdict] || verdict) });
}

function runLink(id) {
  return el("a", { href: link(id), text: id });
}

// A record ID standing on its own: round r002, measurement m001, configuration r002/c16, run sim_0004 or hw_0001.
// Inside a path (rounds/r002/analysis.md) or a command in backticks it stays text.
const RECORD_ID = /^(?:r\d{3}\/[A-Za-z0-9][A-Za-z0-9_-]{0,47}|r\d{3}|m\d{3}|(?:sim|hw|mock)_\d{4})$/;
const INLINE = /(`[^`]+`|\*\*[^*]+\*\*|(?<![\w\/.-])(?:r\d{3}\/[A-Za-z0-9][A-Za-z0-9_-]{0,47}|r\d{3}|m\d{3}|(?:sim|hw|mock)_\d{4})(?![\w\/-]|\.\w))/;

// Markdown from the agent and the records (analysis, evidence, reports), built as DOM. Record IDs become links.
function inline(text) {
  return String(text).split(INLINE).filter(Boolean).map((part) => {
    if (part.startsWith("`") && part.endsWith("`")) return el("code", { text: part.slice(1, -1) });
    if (part.startsWith("**") && part.endsWith("**")) return el("strong", {}, inline(part.slice(2, -2)));
    if (RECORD_ID.test(part)) return el("a", { href: link(part), text: part });
    return document.createTextNode(part);
  });
}

function markdown(text) {
  const root = el("div", { class: "md" });
  const lines = String(text || "").split("\n");
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) { index += 1; continue; }
    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      root.append(el(`h${Math.min(4, heading[1].length + 1)}`, {}, inline(heading[2])));
      index += 1;
      continue;
    }
    if (line.trim().startsWith("|") && /^\s*\|?\s*:?-{3,}/.test(lines[index + 1] || "")) {
      const cells = (row) => row.trim().replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim());
      const header = cells(line);
      const body = [];
      index += 2;
      while (index < lines.length && lines[index].trim().startsWith("|")) body.push(cells(lines[index++]));
      root.append(el("div", { class: "table-wrap" }, el("table", {}, el("thead", {}, el("tr", {}, header.map((cell) => el("th", {}, inline(cell))))),
        el("tbody", {}, body.map((row) => el("tr", {}, row.map((cell) => el("td", {}, inline(cell)))))))));
      continue;
    }
    if (/^\s*[-*]\s+/.test(line)) {
      const list = el("ul");
      while (index < lines.length && /^\s*[-*]\s+/.test(lines[index])) list.append(el("li", {}, inline(lines[index++].replace(/^\s*[-*]\s+/, ""))));
      root.append(list);
      continue;
    }
    const paragraph = [];
    while (index < lines.length && lines[index].trim() && !/^(#{1,6})\s|^\s*[-*]\s+|^\s*\|/.test(lines[index])) paragraph.push(lines[index++]);
    if (!paragraph.length) paragraph.push(lines[index++]);
    root.append(el("p", {}, inline(paragraph.join("\n"))));
  }
  return root;
}

// ---------- server ----------

// A port forward can drop a request, or leave it hanging. Every request has a time limit; reads are then retried
// twice. Writes are never repeated: a write that timed out may still have happened, and the next poll shows it.
async function api(path, body, attempt = 0) {
  const read = body === undefined;
  // The page's language goes with every request: the server writes the report in it and tells the agent which to use.
  const headers = { "X-Mlab-Lang": language() };
  const options = read ? { headers } : { method: "POST", headers: { ...headers, "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const again = (failure) => (read && attempt < 2
    ? new Promise((resolve) => setTimeout(() => resolve(api(path, body, attempt + 1)), 500 * (attempt + 1)))
    : failure);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), read ? 12000 : 60000);
  try {
    let response;
    try {
      response = await fetch(path, { credentials: "same-origin", signal: controller.signal, ...options });
    } catch {
      return again({ ok: false, error: { code: "E_NETWORK", message: controller.signal.aborted ? t("The server timed out (the port forward may be slow)") : t("Cannot reach mlab web; check that it is still running") } });
    }
    try {
      return await response.json();
    } catch {
      return again({ ok: false, error: { code: "E_HTTP", message: controller.signal.aborted ? t("The server timed out") : `HTTP ${response.status}` } });
    }
  } finally {
    clearTimeout(timer);
  }
}

function sessionApi(path, body, session = app.session) {
  return api(`/api/sessions/${session}/${path}`, body);
}

function state() {
  return app.states[app.session] || {};
}

// ---------- drafts: what the student typed survives re-renders and reloads ----------

const drafts = stored("mlab-drafts", {});
const persist = debounce(() => store("mlab-drafts", drafts), 300);

function draftKey(form) {
  return `${app.session}:${app.recordId}:${form}`;
}

function getDraft(form, field, fallback = "") {
  const value = drafts[draftKey(form)]?.[field];
  return value === undefined ? fallback : value;
}

function setDraft(form, field, value) {
  (drafts[draftKey(form)] ||= {})[field] = value;
  persist();
}

function dropDraft(form) {
  delete drafts[draftKey(form)];
  persist();
}

function textField(form, field, label, { help, initial = "", rows = 3, placeholder } = {}) {
  return el("label", { class: "field" }, el("span", { text: label }), help ? el("small", { text: help }) : null,
    el("textarea", { rows, placeholder, value: getDraft(form, field, initial || ""), oninput: (event) => setDraft(form, field, event.target.value) }));
}

function toast(text, session) {
  const node = el("div", { class: "toast", role: "status", text, onclick: () => { node.remove(); if (session && session !== app.session) go("", null, session); } });
  $("#toasts").append(node);
  setTimeout(() => node.remove(), 7000);
}

// Submit a form; when the agent is busy, offer to stop it first.
async function submitForm(kind, body, status, { form, done, message } = {}) {
  status.replaceChildren(el("p", { class: "note", text: t("Submitting…") }));
  const handle = (result) => {
    if (result.ok) {
      if (form) dropDraft(form);
      const then = body.then || "notify";
      toast(message || t(then === "save" ? "Saved" : then === "confirm" ? "Saved and confirmed" : "Saved; agent notified"));
      status.replaceChildren();
      if (done) done(result);
      refreshAll();
      return;
    }
    status.replaceChildren(el("p", { class: "form-error", text: tm(result.error?.message) || t("Failed") }));
    if (result.error?.code === "E_AGENT_BUSY") {
      status.append(button(t("Stop the agent and submit"), "small", async () => handle(await sessionApi(`forms/${kind}`, { ...body, interrupt: true }))));
    }
  };
  handle(await sessionApi(`forms/${kind}`, body));
}

// ---------- routing ----------

function parseHash() {
  const [path, query] = location.hash.replace(/^#\/?/, "").split("?");
  const [session, ...rest] = (path || "").split("/");
  const id = rest.join("/");
  const params = new URLSearchParams(query || "");
  return { session: SESSION.test(session) ? session : null, id: id ? decodeURIComponent(id) : null, tab: params.get("tab"), run: params.get("run") };
}

function sessions() {
  return app.course?.sessions || [];
}

function sessionOf(id) {
  return sessions().find((item) => item.id === id);
}

// The session a visit opens: the last one used, else the newest waiting for the student, else the newest.
function pickSession() {
  const list = sessions();
  const last = stored("mlab-session", null);
  if (list.some((item) => item.id === last)) return last;
  const waiting = list.filter((item) => (app.badges[item.id] || item.badge)?.tone === "attention");
  return (waiting.at(-1) || list.at(-1))?.id || null;
}

// One request brings everything a session's page shows: state, overview, chat, the open record and the notebook.
// Each session's last page is kept, so switching back shows it at once and refreshes behind it. A load that finishes
// after a newer navigation started is dropped.
let navigation = 0;

function loadPage(session, id, withChat) {
  const query = new URLSearchParams();
  if (id && id !== "compare") query.set("record", id);
  if (!withChat) query.set("chat", "0");
  const page = api(`/api/sessions/${session}/page?${query}`);
  if (id !== "compare") return page;
  const runs = tray(session);
  return Promise.all([page, runs.length >= 2 ? sessionApi("compare", { runs }, session) : null]).then(([result, found]) => {
    if (result.ok) {
      result.record_id = "compare";
      result.record = !found ? { ok: true, kind: "compare", empty: true, rows: [] } : found.ok ? { ...found, kind: "compare" } : found;
    }
    return result;
  });
}

function keep(session, page) {
  const kept = app.pages[session] || {};
  app.pages[session] = { overview: page.overview, chat: page.chat ?? kept.chat ?? [], agent: page.agent, recordId: page.record_id,
                         record: page.record, notebook: page.notebook };
  app.states[session] = page.state;
  if (page.state?.badge) app.badges[session] = page.state.badge;
}

function show(session, { chat = true } = {}) {
  const page = app.pages[session];
  app.overview = page.overview;
  app.chat = page.chat;
  app.agent = page.agent;
  app.recordId = page.recordId;
  app.record = page.record?.ok ? page.record : null;
  app.notebook = page.notebook?.ok ? page.notebook : null;
  renderSessions();
  renderFacts();
  if (chat) renderRight();
  else { renderAgentHead(); renderComposerStatus(); renderMessages(); renderQuick(); }
  renderNav();
  if (app.record) renderMiddle();
  else $("#record").replaceChildren(el("div", { class: "banner warn", text: tm(page.record?.error?.message) || t("Cannot open this record") }));
}

async function route() {
  const mine = ++navigation;
  const parsed = parseHash();
  const session = parsed.session || app.session || pickSession();
  if (!session) {
    app.session = null;
    showWelcome();
    return;
  }
  const switched = session !== app.session;
  app.session = session;
  app.tab = parsed.tab;
  store("mlab-session", session);
  if (switched) {
    app.unread[session] = 0;
    app.streaming = "";
    sessionApi("agent/open", {}, session);  // Status changes arrive as events; they are newer than this reply.
  }
  const kept = app.pages[session];
  const id = parsed.id || kept?.recordId || null;
  if (parsed.run && id) app.pairs[id] = parsed.run;
  if (kept && (!parsed.id || parsed.id === kept.recordId)) show(session);
  else {
    renderSessions();
    $("#record").replaceChildren(el("p", { class: "note", text: t("Loading…") }));
  }
  const page = await loadPage(session, id, switched || !kept);
  if (mine !== navigation) return;
  if (!page.ok) {
    if (!kept) $("#record").replaceChildren(el("div", { class: "banner warn", text: t("{message}. Reload the page.", { message: tm(page.error?.message) || t("Cannot read this session") }) }));
    return;
  }
  keep(session, page);
  show(session, { chat: switched || !kept });
}

// A few recent sessions load in the background after the first page, so switching to them is instant too.
async function prefetch() {
  for (const item of sessions().slice(-4).reverse()) {
    if (app.pages[item.id]) continue;
    const page = await loadPage(item.id, null, true);
    if (page.ok && !app.pages[item.id]) keep(item.id, page);
  }
  renderSessions();
}

const refreshPage = debounce(async () => {
  if (!app.session || !app.pages[app.session]) return;
  if (editing()) {
    app.pending = true;
    return;
  }
  const mine = navigation;
  const session = app.session;
  const page = await loadPage(session, app.recordId, false);
  if (mine !== navigation || session !== app.session || !page.ok) return;
  const scroll = $("#middle").scrollTop;
  keep(session, page);
  show(session, { chat: false });
  $("#middle").scrollTop = scroll;
}, 300);

const refreshRecord = refreshPage;
const refreshAll = refreshPage;

// A re-render while the student types would move the cursor; it waits until the field loses focus.
function editing() {
  const active = document.activeElement;
  return Boolean(active && $("#middle").contains(active) && ["TEXTAREA", "INPUT", "SELECT"].includes(active.tagName));
}

// ---------- top bar ----------

function sessionName(item) {
  return item ? `${item.task} ${t(item.title)} · ${t("Session {n}", { n: item.number })}` : "";
}

function renderCurrent() {
  const item = sessionOf(app.session);
  const badge = item ? app.badges[item.id] || item.badge : null;
  fill($("#current"), item ? el("span", { class: "current-name", text: sessionName(item) }) : null,
    badge?.text ? el("span", { class: `tab-badge tone-${badge.tone}`, text: t(badge.text) }) : null);
}

function renderFacts() {
  const current = state();
  const budget = current.budget?.hardware;
  const theme = stored("mlab-theme", "auto");
  const agentLabel = app.course?.agent === "fake" ? t("Example agent (offline)") : t("Model: {name}", { name: app.course?.provider || t("Claude Code account") });
  const other = language() === "zh" ? "en" : "zh";
  const themeLabel = t({ auto: "Theme: system", light: "Theme: light", dark: "Theme: dark" }[theme]);
  fill($("#facts"),
    budget ? el("span", { class: "fact", title: t("The hardware budget is per task and shared by its sessions; runs that fail because of the environment are not counted"),
                          text: t("Hardware runs left: {left}/{total}", { left: budget.remaining, total: budget.total }) }) : null,
    el("span", { class: "fact placeholder-tag", title: tm(app.course?.notice) || "", text: t("Placeholder numbers") }),
    el("span", { class: "fact", text: agentLabel }),
    el("button", { type: "button", class: "btn icon", title: themeLabel, "aria-label": themeLabel, text: { auto: "◐", light: "☀", dark: "☾" }[theme], onclick: cycleTheme }),
    // The other language, named in itself. Switching reloads the page; drafts are kept in the browser.
    el("button", { type: "button", class: "btn small", lang: other === "zh" ? "zh-CN" : "en", title: t("Page language"), text: LANGUAGES[other],
                   onclick: () => { remember(other); location.reload(); } }));
}

function applyTheme() {
  const theme = stored("mlab-theme", "auto");
  if (theme === "auto") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", theme);
}

function cycleTheme() {
  const order = ["auto", "light", "dark"];
  store("mlab-theme", order[(order.indexOf(stored("mlab-theme", "auto")) + 1) % order.length]);
  applyTheme();
  renderFacts();
}

// ---------- left column: sessions, each one attempt at a task from the start ----------

// Times are stored in UTC and shown in the viewer's own time zone.
function when(iso, full = false) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (value) => String(value).padStart(2, "0");
  const day = full ? `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` : `${date.getMonth() + 1}-${pad(date.getDate())}`;
  return `${day} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function renderSessions() {
  const list = sessions();
  const tasks = app.course?.tasks || [];
  const nodes = [el("div", { class: "side-head" }, el("h3", { text: t("Sessions") }),
    button(t(app.picking ? "Cancel" : "New session"), app.picking ? "ghost small" : "small primary", () => { app.picking = !app.picking; renderSessions(); }))];
  if (app.picking) {
    nodes.push(el("div", { class: "picker" }, el("p", { class: "note", text: t("A new session starts a task from scratch, with its own records and agent. Sessions of the same task share the hardware budget.") }),
      tasks.map((task) => button(`${task.task} ${t(task.title)}`, "pick", () => startSession(task.task)))));
  }
  if (!list.length) nodes.push(el("p", { class: "empty", text: t("No sessions yet. Click “New session” to pick a task.") }));
  for (const task of tasks) {
    const items = list.filter((item) => item.task === task.task).reverse();
    if (!items.length) continue;
    const budget = app.states[items[0].id]?.budget?.hardware || items[0].budget;
    nodes.push(el("div", { class: "group" }, el("div", { class: "group-head" }, el("b", { text: `${task.task} ${t(task.title)}` }),
      budget ? el("span", { class: "note", title: t("Shared by the sessions of this task"), text: t("Hardware runs left: {left}/{total}", { left: budget.remaining, total: budget.total }) }) : null),
      el("ul", { class: "sessions" }, items.map(sessionItem))));
  }
  fill($("#left"), nodes);
  renderCurrent();
  const waiting = list.filter((item) => (app.badges[item.id] || item.badge)?.tone === "attention").length;
  document.title = `${waiting ? `(${waiting}) ` : ""}${t("hello-mlsys workbench")}`;
}

function sessionItem(item) {
  const badge = app.badges[item.id] || item.badge || { text: "", tone: "info" };
  const conclusion = app.states[item.id]?.conclusion ?? item.conclusion;
  return el("li", {}, el("a", { class: "session", href: link("", item.id), "aria-current": item.id === app.session ? "true" : "false",
                                onclick: () => setPane("middle") },
    el("span", { class: "session-name" }, t("Session {n}", { n: item.number }), el("small", { class: "note", text: when(item.created_at) }),
      app.unread[item.id] ? el("span", { class: "tab-unread", text: app.unread[item.id] }) : null),
    badge.text ? el("span", { class: `tab-badge tone-${badge.tone}`, text: t(badge.text) }) : null,
    conclusion ? el("span", { class: "note", text: t("Conclusion: {value}", { value: conclusion }) }) : null));
}

async function loadCourse() {
  const result = await api("/api/course");
  if (!result.ok) {
    $("#record").replaceChildren(el("div", { class: "banner warn", text: t("{message}. Reload the page.", { message: tm(result.error?.message) || t("Not connected to mlab web") }) }));
    return result;
  }
  app.course = result;
  for (const item of result.sessions) if (item.badge) app.badges[item.id] = item.badge;
  renderSessions();
  renderFacts();
  return result;
}

async function startSession(task) {
  const result = await api("/api/sessions", { task });
  if (!result.ok) {
    toast(tm(result.error?.message) || t("Failed to create the session"));
    return;
  }
  app.picking = false;
  await loadCourse();
  go("overview", null, result.session);
}

function showWelcome() {
  app.overview = app.notebook = app.record = null;
  renderSessions();
  renderFacts();
  $("#recnav").replaceChildren();
  fill($("#record"), el("div", { class: "record-head" }, el("div", { class: "record-title" }, el("h2", { text: t("Choose a task") }))),
    el("p", { class: "lead", text: t("Each session works through a task from scratch, with its own records and agent. Start a new session at any time; earlier sessions are kept. Sessions of the same task share the hardware budget.") }),
    el("div", { class: "tasks" }, (app.course?.tasks || []).map((task) => el("div", { class: "section task-card" }, el("span", { class: "task-id", text: task.task }),
      el("h3", { text: t(task.title) }), button(t("Start"), "primary", () => startSession(task.task))))));
  $("#right").replaceChildren(el("p", { class: "empty", text: t("Start a session to chat with the agent here.") }));
}

// ---------- the record directory, on top of the middle column: timeline, configurations, compare tray ----------

function tray(session = app.session) {
  return stored(`mlab-tray-${session}`, []);
}

function setTray(runs) {
  store(`mlab-tray-${app.session}`, [...new Set(runs)].slice(0, 12));
  renderNav();
}

function addToTray(run) {
  setTray([...tray(), run]);
  toast(t("{run} added to the compare tray", { run }));
}

function renderNav() {
  const box = $("#recnav");
  const notebook = app.notebook;
  if (!box) return;
  if (!notebook) {
    box.replaceChildren();
    return;
  }
  const open = stored("mlab-directory", false);
  const runs = tray();
  const chips = [navChip(t("Overview"), "overview"), ...notebook.items.filter((item) => item.kind !== "conclusion").map((item) =>
    navChip(item.kind === "round" ? t("{id} · round {n}", { id: item.id, n: item.iteration }) : t("{id} · measurement", { id: item.id }), item.id,
            itemMeta(item).filter(Boolean).join(" · ")))];
  if (notebook.report || notebook.conclusion) chips.push(navChip(t("Report"), "report"));
  if (runs.length >= 2) chips.push(navChip(t("Compare ({n})", { n: runs.length }), "compare"));
  const closed = runs.length ? t("Directory ({configs} configurations, {tray} in tray)", { configs: notebook.configs.length, tray: runs.length })
    : t("Directory ({configs} configurations)", { configs: notebook.configs.length });
  fill(box, el("div", { class: "recnav-row" }, el("nav", { class: "recnav-chips", "aria-label": t("Records") }, chips),
    button(open ? t("Hide directory") : closed, "ghost small",
      () => { store("mlab-directory", !open); renderNav(); })),
    open ? el("div", { class: "dir-panel" },
      el("div", {}, el("div", { class: "side-head" }, el("h3", { text: t("Records") }), notebook.selection ? chip(t("Selected: {config}", { config: notebook.selection }), "current") : null), directory(notebook)),
      el("div", {}, el("div", { class: "side-head" }, el("h3", { text: t("Configurations ({n})", { n: notebook.configs.length }) })), configList(notebook)),
      trayPanel(runs)) : null);
}

function navChip(label, target, title) {
  return el("a", { class: "nav-chip", href: link(target), title: title || null, "aria-current": app.recordId === target ? "true" : "false", text: label });
}

function trayPanel(runs) {
  return el("div", { class: "tray" }, el("div", { class: "side-head" }, el("h3", { text: t("Compare tray ({n})", { n: runs.length }) }),
    runs.length ? button(t("Clear"), "ghost small", () => setTray([])) : null),
    runs.length ? el("ul", { class: "tray-list" }, runs.map((run) => el("li", {}, el("a", { href: link(run), text: run }),
      el("button", { type: "button", "aria-label": t("Remove {run}", { run }), text: "×", onclick: () => setTray(runs.filter((item) => item !== run)) })))) :
      el("p", { class: "empty", text: t("Use “Add to compare” on runs to compare them across rounds and measurements.") }),
    runs.length >= 2 ? button(t("Compare"), "small primary", () => go("compare")) : null);
}

// Rounds, measurements and submitted conclusions, oldest first, each with where it came from.
function itemMeta(item) {
  if (item.kind === "round") {
    return [statusText(item.status), item.selected ? t("Selected {candidate}", { candidate: item.selected }) : "",
            item.source ? t("Candidates from {round}", { round: item.source }) : ""];
  }
  if (item.kind === "measurement") {
    return [statusText(item.status), item.points?.length ? t("Measures {points}", { points: item.points.join(", ") }) : "",
            item.flagged.length ? t("{n} off from simulation", { n: item.flagged.length }) : ""];
  }
  return [item.selected, item.conclusion];
}

function itemTarget(item) {
  return item.kind === "conclusion" ? "report" : item.id;
}

function directory(notebook) {
  const items = [];
  for (const item of notebook.items) {
    const current = app.recordId === item.id;
    items.push(el("li", {}, el("a", { class: "dir-item", href: link(itemTarget(item)), "aria-current": current ? "true" : "false" },
      itemLabel(item), el("span", { class: "meta", text: itemMeta(item).filter(Boolean).join(" · ") }))));
    const open = app.expanded[item.id] ?? (current || (app.record?.kind === "run" && item.runs?.some((run) => run.id === app.recordId)));
    if (item.runs?.length) {
      items.push(el("li", {}, button(open ? t("Hide runs") : t("Runs ({n})", { n: item.runs.length }), "ghost small", () => { app.expanded[item.id] = !open; renderNav(); })));
      if (open) {
        items.push(el("li", {}, el("ul", { class: "dir-runs" }, item.runs.map((run) => el("li", { onclick: () => go(run.id), title: run.id },
          el("span", { class: run.verdict === "constraints_met" ? "ok" : run.verdict === "constraints_violated" ? "bad" : null, text: run.id }),
          el("span", { class: "note", text: `${run.candidate}${run.split !== "development" ? ` · ${run.holdout || run.split}` : ""}` }))))));
      }
    }
  }
  if (notebook.report || notebook.conclusion) items.push(el("li", {}, el("a", { class: "dir-item", href: link("report"), text: t("Report") })));
  if (!items.length) return el("p", { class: "empty", text: t("No records yet") });
  return el("ul", { class: "dir" }, items);
}

// Each configuration once, under the first round/candidate that named it.
function configList(notebook) {
  if (!notebook.configs.length) return el("p", { class: "empty", text: t("Configurations appear here once a plan is approved") });
  return el("ul", { class: "dir" }, notebook.configs.map((config) => {
    const hardware = config.missing.hardware;
    const meta = [config.selected ? t("Current selection") : "", t("{n} simulated", { n: config.simulated }), config.measured ? t("{n} measured", { n: config.measured }) : "",
                  hardware ? (hardware.length ? t("Hardware validation: {n} missing", { n: hardware.length }) : t("Hardware validation complete")) : ""];
    return el("li", {}, el("a", { class: "dir-item", href: link(config.id), "aria-current": app.recordId === config.id ? "true" : "false" },
      config.id, el("span", { class: "meta", text: meta.filter(Boolean).join(" · ") })));
  }));
}

function approvedRounds() {
  return (app.notebook?.items || []).filter((item) => item.kind === "round" && item.status !== "draft");
}

// ---------- middle column ----------

function renderMiddle() {
  const record = app.record;
  if (!record) return;
  const views = { overview: viewOverview, round: viewRound, run: viewRun, measurement: viewMeasurement, config: viewConfig, report: viewReport, compare: viewCompare };
  const view = views[record.kind];
  $("#record").replaceChildren(...[view ? view(record) : el("p", { text: t("Unknown record") })].flat(Infinity).filter(Boolean));
  app.pending = false;
}

function head(title, crumbs, actions = [], status) {
  return el("div", { class: "record-head" },
    el("nav", { class: "crumbs", "aria-label": t("Breadcrumb") }, crumbs.map(([label, id]) => (id ? el("a", { href: link(id), text: label }) : el("span", { text: label })))),
    el("div", { class: "record-title" }, el("h2", { text: title }), status ? chip(status) : null, el("div", { class: "actions" }, actions)));
}

function ask(kind, ids) {
  const refs = [].concat(ids).map((id) => `@${id}`).join(" ");
  return button(t("Ask agent"), "ghost small", () => prefill(`${refs} ${t(ASK[kind])}`.trim()));
}

function crumbs() {
  return [[sessionName(sessionOf(app.session)) || `${app.overview?.task || ""} ${app.overview?.title || ""}`, "overview"]];
}

function progressList(runs) {
  const live = runs.filter((run) => !["succeeded", "failed", "cancelled", "timed_out"].includes(run.status));
  if (!live.length) return null;
  return el("div", { class: "section" }, el("p", { class: "note", text: t("Running:") }),
    el("ul", { class: "progress" }, live.map((run) => el("li", { text: `${run.id} ${run.candidate} · ${statusText(run.phase || run.status)}` }))));
}

// The task at a glance: the selection, the conclusion it would get now, what it still lacks, and everything done so far.
function viewOverview(notebook) {
  const current = state();
  const validation = notebook.validation;
  const latest = notebook.conclusion;
  const nodes = [head(t("Overview"), [[crumbs()[0][0], null]],
    [ask("overview", []), notebook.report ? button(t("View report"), "ghost small", () => go("report")) : null], latest ? t("Submitted: {conclusion}", { conclusion: latest.conclusion }) : null)];
  if (current.step === "next_step") nodes.push(nextStepPanel());
  if (["new_round", "hypothesis", "falsifier"].includes(current.step) && !current.workflow) nodes.push(hypothesisForm(null));
  nodes.push(section(t("Research question"), "agent", textBlock(notebook.question, "No research question in round 1's plan yet")));
  nodes.push(section(t("Selection and conclusion"), "mlab", kv([
    [t("Current selection"), notebook.selection ? el("a", { href: link(notebook.selection), text: notebook.selection }) : t("None")],
    [t("Submitted conclusion"), latest ? t("{conclusion} ({config}, {at})", { conclusion: latest.conclusion, config: latest.config, at: when(latest.at, true) }) : t("None")],
    [t("Conclusion if submitted now"), validation?.conclusion], [t("Basis"), tm(validation?.reason || latest?.reason)],
    [t("Hardware budget"), t("{used} used, {left} left of {total}", { used: notebook.budget.used, left: notebook.budget.remaining, total: notebook.budget.total })]]),
    meter(notebook.budget.used, notebook.budget.total)));
  if (validation) nodes.push(section(t("Validation matrix (current selection)"), "mlab", validationTable(validation)));
  nodes.push(section(t("Records"), null, notebook.items.length ? table([{ label: t("Record") }, { label: t("Status") }, { label: t("Content") }],
    notebook.items.map((item) => [el("a", { href: link(itemTarget(item)), text: itemLabel(item) }), statusText(item.status),
      item.kind === "round" ? short(item.hypothesis, 60) || "—" : itemMeta(item).slice(item.kind === "measurement" ? 1 : 0).filter(Boolean).join(" · ") || "—"]),
    (index) => go(itemTarget(notebook.items[index]))) : el("p", { class: "empty", text: t("No records yet") })));
  nodes.push(section(t("Timeline"), "mlab", notebook.timeline.length ? el("ul", { class: "timeline" }, notebook.timeline.map((event) =>
    el("li", {}, el("time", { text: when(event.at, true) }), `${t(event.label)} `, event.id ? el("a", { href: link(event.type === "concluded" ? "report" : event.id), text: event.id }) : null))) :
    el("p", { class: "empty", text: t("No events yet") })));
  return nodes;
}

// One configuration across the task: where it appears, every simulation and hardware run of it, and its validation.
function viewConfig(config) {
  const current = state();
  const metric = config.metric;
  const simulated = config.runs.filter((run) => run.role !== "hardware");
  const measured = config.runs.filter((run) => run.role === "hardware");
  const status = el("div");
  const nodes = [head(config.id, [...crumbs(), [config.id, null]], [ask("config", config.id)], config.selected ? t("Current selection") : null),
    section(t("Summary"), "mlab", kv([[t("Appears in"), config.names.join(", ")], [t("Conclusion on current evidence"), config.preview], [t("Basis"), tm(config.reason)],
      [t("Submitted conclusions"), config.conclusions.length ? config.conclusions.join(", ") : null]]))];
  if (config.chart.bars.length) nodes.push(config.chart.kind === "paired" ? pairedChart(config.chart, { onSelect: (run) => go(run) }) : barChart(config.chart, { onSelect: (run) => go(run) }));
  nodes.push(section(t("Validation matrix"), "mlab", validationTable({ candidate: config.id, matrix: config.validation, missing: config.missing })));
  nodes.push(section(t("Simulation"), "mlab", simulated.length ? table([{ label: t("Run") }, { label: t("From") }, { label: t("Workload") }, { label: t("Verdict") }, { label: metric, num: true }],
    simulated.map((run) => [runLink(run.run), run.owner, run.holdout || run.split, verdictCell(run.verdict), format(run.value)])) :
    el("p", { class: "empty", text: t("No simulations yet") })));
  nodes.push(section(t("Hardware"), "mlab", measured.length ? table([{ label: t("Run") }, { label: t("From") }, { label: t("Workload") }, { label: t("Verdict") }, { label: metric, num: true },
    { label: t("Frozen prediction"), num: true }, { label: t("Gap"), num: true }, { label: t("Shift") }],
    measured.map((run) => [runLink(run.run), run.owner, run.holdout || run.split, verdictCell(run.verdict), format(run.value),
      run.simulated === undefined ? "—" : format(run.simulated), run.gap_pct === undefined || run.gap_pct === null ? "—" : `${run.gap_pct}%`,
      run.shift === "all" ? t("All requests") : run.shift === "tail" ? t("Tail only") : "—"])) :
    el("p", { class: "empty", text: t("No hardware runs yet. It can be picked as a hardware point.") })));
  if (current.step === "next_step") {
    nodes.push(el("div", { class: "row" }, button(t("New round from {round}'s candidates", { round: config.workflow }), "small", () => {
      submitForm("new_round", { source: config.workflow }, status, { message: t("New round created; these candidates will be simulated again"), done: (result) => go(result.workflow) });
    })), status);
  }
  return nodes;
}

function meter(used, total) {
  return el("div", { class: `meter ${used > total ? "over" : ""}`, role: "img", "aria-label": t("{used} of {total} used", { used, total }) },
    (() => { const bar = el("span"); bar.style.width = `${Math.min(100, (used / Math.max(total, 1)) * 100)}%`; return bar; })());
}

function validationTable(validation) {
  const holdouts = app.overview?.holdouts || [];
  const cell = (value) => (value === true ? el("span", { class: "ok", text: t("Met") }) : value === false ? el("span", { class: "bad", text: t("Violated") }) : t("Not run"));
  const source = (role) => t(role === "hardware" ? "Hardware" : "Simulation");
  const roles = Object.keys(validation.matrix);
  if (!roles.length) return el("p", { class: "empty", text: t("No usable runs for {candidate} yet", { candidate: validation.candidate }) });
  return [table([{ label: t("Source") }, { label: t("Development") }, { label: t("Repeat") }, ...holdouts.map((name) => ({ label: t("Holdout: {name}", { name }) }))],
    roles.map((role) => [source(role), cell(validation.matrix[role].development), cell(validation.matrix[role].repeat),
      ...holdouts.map((name) => cell(validation.matrix[role].holdout[name]))])),
    el("p", { class: "note", text: roles.map((role) => t("{source} missing: {missing}", { source: source(role), missing: validation.missing[role].join(", ") || t("none") })).join("; ") })];
}

// One simulator round, in four tabs.
function viewRound(round) {
  const current = state();
  const isCurrent = round.editable && current.workflow === round.id;
  const form = isCurrent ? current.form : null;
  const formTab = { hypothesis: "hypothesis", review: "plan", decision: "decision" }[form];
  const tabs = [["hypothesis", "Hypothesis"], ["plan", "Plan"], ["results", "Results"], ["decision", "Analysis & decision"]];
  const tab = tabs.some(([key]) => key === app.tab) ? app.tab : formTab || (round.runs.length ? "results" : round.candidates.length ? "plan" : "hypothesis");
  const panels = { hypothesis: roundHypothesis, plan: roundPlan, results: roundResults, decision: roundDecision };
  return [head(t("Simulation round {n}", { n: round.iteration }), [...crumbs(), [round.id, null]], [ask("round", round.id)], statusText(round.status)),
    isCurrent && current.step === "next_step" ? nextStepPanel() : null,
    isCurrent && form === "unlock" ? unlockPanel("workflow", round.errors) : null,
    el("div", { class: "subtabs", role: "tablist" }, tabs.map(([key, label]) => el("button", {
      type: "button", role: "tab", "aria-selected": key === tab ? "true" : "false", class: key === formTab ? "dot" : null, text: t(label), onclick: () => go(round.id, key),
    }))), panels[tab](round, form)];
}

function constraintsBox() {
  const constraints = (app.overview?.constraints || []).filter((item) => item.value !== null && item.value !== undefined);
  return el("div", { class: "banner" }, el("b", { text: t("Task constraints: ") }), constraints.map((item) => `${item.metric} ${item.op} ${item.value}`).join("; ") || t("See the task description"));
}

function hypothesisForm(round) {
  const key = "hypothesis";
  const status = el("div");
  const previous = round?.hypothesis || {};
  return el("div", { class: "form", id: "form-hypothesis" }, el("h4", { text: t("Hypothesis") }),
    el("p", { class: "lead", text: t("Saved verbatim to hypothesis.md; the agent does not rewrite it.") }),
    constraintsBox(), round?.previous_decision ? el("div", { class: "banner" }, el("b", { text: t("Previous decision ({id}): ", { id: round.previous_decision.id }) }), short(round.previous_decision.text, 200)) : null,
    textField(key, "claim", t("Hypothesis"), { help: t("Which mechanism causes the problem?"), initial: previous.claim }),
    textField(key, "alternative", t("Alternative explanation (optional)"), { help: t("What else could explain it?"), initial: previous.alternative, rows: 2 }),
    textField(key, "falsifier", t("Falsification condition"), { help: t("Which result, with what outcome, would show the hypothesis is wrong."), initial: previous.falsifier }),
    el("div", { class: "row" },
      button(t("Save and notify agent"), "primary", () => send("notify")), button(t("Save only"), null, () => send("save"))), status);
  function send(then) {
    submitForm("hypothesis", { claim: getDraft(key, "claim", previous.claim || ""), alternative: getDraft(key, "alternative", previous.alternative || ""),
                               falsifier: getDraft(key, "falsifier", previous.falsifier || ""), then }, status,
               { form: key, done: (result) => { if (result.workflow && result.workflow !== app.recordId) go(result.workflow); } });
  }
}

function roundHypothesis(round, form) {
  if (form === "hypothesis") return hypothesisForm(round);
  return [section(t("Hypothesis"), "student", textBlock(round.hypothesis.claim)), section(t("Alternative explanation"), "student", textBlock(round.hypothesis.alternative, "Not given")),
          section(t("Falsification condition"), "student", textBlock(round.hypothesis.falsifier))];
}

function roundPlan(round, form) {
  const base = round.candidates[0]?.id;
  const check = round.check;
  return [
    round.plan.error ? el("div", { class: "banner warn", text: tm(round.plan.error) }) : null,
    section(t("Research question and derived requirements"), "agent", textBlock(round.plan.question, "The agent has not written the research question yet"),
      round.plan.derived_requirements.length ? table([{ label: t("Requirement") }, { label: t("Value") }, { label: t("Derivation") }],
        round.plan.derived_requirements.map((item) => [item.name, item.value, item.derivation])) : null),
    check ? el("div", { class: `banner ${check.stale ? "warn" : ""}`, text: check.stale ? [t("The plan changed after the check and must be checked again."), tm(check.error)].filter(Boolean).join(" ")
      : t("Plan checked ({hash}…).", { hash: check.hash.slice(0, 19) }) }) : null,
    round.approval ? el("div", { class: "banner", text: t("Approved at {at}; candidates frozen.", { at: when(round.approval.at, true) }) }) : null,
    section(t("Candidates (differences from {base})", { base: base || t("the first candidate") }), "agent", round.candidates.length ? table([{ label: t("Candidate") }, { label: t("Rationale") }, { label: t("Differences") }],
      round.candidates.map((item, index) => [round.candidates_frozen ? el("a", { href: link(`${round.id}/${item.id}`), text: item.id }) : item.id, tm(item.rationale), index === 0 ? t("Baseline") :
        (item.diff.map((d) => `${d.field}: ${d.base ?? t("default")} → ${d.value ?? t("default")}`).join("; ") || t("Same as the baseline"))])) : el("p", { class: "empty", text: t("No candidates yet") })),
    // While the review form is open and nothing is written yet, the form alone stands for the comments.
    form === "review" && !(round.review.student || "").trim() ? null : section(t("Plan comments"), "student", textBlock(round.review.student, "No comments yet")),
    round.review.agent ? section(t("Changes and responses"), "agent", textBlock(round.review.agent)) : null,
    form === "review" ? reviewForm(round) : null,
    reuseButtons(round)];
}

function reviewForm(round) {
  const key = "review";
  const status = el("div");
  const verdicts = getDraft(key, "verdicts", {});
  const reasons = getDraft(key, "reasons", {});
  const approve = button(t("Save comments and approve"), "primary", () => send("confirm"));
  const update = () => {
    const values = Object.values(getDraft(key, "verdicts", {}));
    approve.disabled = !round.check || round.check.stale || values.some((value) => value !== "keep");
    approve.title = approve.disabled ? t("A candidate is marked change or drop, or the plan is not checked; ask the agent to revise first") : "";
  };
  const rows = round.candidates.map((item) => el("div", { class: "field review-row" }, el("span", { text: item.id }),
    el("div", { class: "verdicts", role: "radiogroup", "aria-label": t("Verdict on {id}", { id: item.id }) }, [["keep", "Keep"], ["change", "Change"], ["drop", "Drop"]].map(([value, label]) =>
      el("label", {}, el("input", { type: "radio", name: `review-${round.id}-${item.id}`, value, checked: verdicts[item.id] === value,
        onchange: () => { setDraft(key, "verdicts", { ...getDraft(key, "verdicts", {}), [item.id]: value }); update(); } }), t(label)))),
    el("input", { type: "text", placeholder: t("Reason: what does it test? What should change?"), value: reasons[item.id] || "",
      oninput: (event) => setDraft(key, "reasons", { ...getDraft(key, "reasons", {}), [item.id]: event.target.value }) })));
  update();
  return el("div", { class: "form", id: "form-review" }, el("h4", { text: t("Plan review") }),
    el("p", { class: "lead", text: t("Mark each candidate keep, change or drop, with a reason. Comments are saved verbatim to review.md and frozen with the plan on approval.") }),
    rows, textField(key, "general", t("Overall comment (optional)"), { rows: 2 }),
    el("div", { class: "row" }, approve, button(t("Ask the agent to revise"), null, () => send("notify")), button(t("Save only"), null, () => send("save"))), status);
  function send(then) {
    const chosen = getDraft(key, "verdicts", {});
    const why = getDraft(key, "reasons", {});
    const entries = round.candidates.filter((item) => chosen[item.id]).map((item) => ({ candidate: item.id, verdict: chosen[item.id], reason: why[item.id] || "" }));
    submitForm("review", { entries, general: getDraft(key, "general", ""), then }, status, { form: key });
  }
}

function roundResults(round) {
  const nodes = [progressList(round.runs)];
  if (round.chart.bars.length) nodes.push(barChart(round.chart, { onSelect: (run) => go(run) }));
  const metric = round.chart.metric;
  nodes.push(section(t("Runs"), "mlab", round.runs.length ? table(
    [{ label: t("Run") }, { label: t("Candidate") }, { label: t("Workload") }, { label: t("Verdict") }, { label: metric, num: true }, { label: t("Margin"), num: true }, { label: "" }],
    round.runs.map((run) => [runLink(run.id), run.candidate, run.holdout || run.split, verdictCell(run.verdict), format(run.key_value),
      run.margin_pct === null || run.margin_pct === undefined ? "—" : `${run.margin_pct}%`, button(t("Add to compare"), "ghost small", (event) => { event.stopPropagation(); addToTray(run.id); })])) :
    el("p", { class: "empty", text: t("No runs yet. The agent submits the simulations once the plan is approved.") })));
  return nodes;
}

function roundDecision(round, form) {
  const claims = round.claims;
  return [
    section(t("Analysis"), "agent", round.analysis.trim() ? markdown(round.analysis) : el("p", { class: "missing", text: t("The agent has not written the analysis yet") }),
      claims ? el("p", { class: claims.valid ? "form-ok" : "form-error", text: claims.valid ? t("Number check passed: {n} claims match the run results", { n: claims.checked })
        : t("Number check failed: {errors}", { errors: claims.errors.map(tm).join("; ") }) }) : null),
    section(t("Falsification condition"), "student", textBlock(round.hypothesis.falsifier)),
    round.previous_decision ? section(t("Previous decision ({id})", { id: round.previous_decision.id }), "student", textBlock(round.previous_decision.text)) : null,
    form === "decision" && !(round.decision.text || "").trim() ? null : section(t(round.decision.confirmed ? "Decision (confirmed)" : "Decision"), "student",
      textBlock(round.decision.text, "No decision yet"),
      round.decision.selected ? el("p", { class: "note", text: t("Selected candidate: {candidate}", { candidate: round.decision.selected }) }) : null),
    form === "decision" ? decisionForm(round) : null,
    reuseButtons(round)];
}

function decisionForm(round) {
  const key = "decision";
  const status = el("div");
  const area = el("textarea", { rows: 4, value: getDraft(key, "text", ""), placeholder: t("Decision and reasons, citing run IDs, e.g. based on sim_0003 …"),
                                oninput: (event) => setDraft(key, "text", event.target.value) });
  const candidate = el("select", { onchange: (event) => setDraft(key, "candidate", event.target.value) }, el("option", { value: "", text: t("No candidate (decision only)") }),
    round.candidates.map((item) => el("option", { value: item.id, text: item.id, selected: getDraft(key, "candidate", "") === item.id })));
  const insert = (run) => {
    const at = area.selectionStart ?? area.value.length;
    area.value = `${area.value.slice(0, at)}${run}${area.value.slice(at)}`;
    setDraft(key, "text", area.value);
    area.focus();
  };
  return el("div", { class: "form", id: "form-decision" }, el("h4", { text: t("Decision") }),
    el("p", { class: "lead", text: t("Read the analysis against the falsification condition. Keep, revise or reject the hypothesis, give the reasons, and cite at least one run of this round.") }),
    el("label", { class: "field" }, el("span", { text: t("Decision and reasons") }), area),
    el("div", { class: "row" }, el("span", { class: "note", text: t("Insert run:") }), round.runs.map((run) => button(run.id, "small", () => insert(run.id)))),
    el("label", { class: "field" }, el("span", { text: t("Selected candidate") }), candidate),
    el("div", { class: "row" }, button(t("Save and confirm"), "primary", () => send("confirm")), button(t("Save and ask the agent to confirm"), null, () => send("notify")),
      button(t("Save only"), null, () => send("save"))), status);
  function send(then) {
    submitForm("decision", { text: getDraft(key, "text", ""), candidate: getDraft(key, "candidate", "") || null, then }, status, { form: key });
  }
}

function reuseButtons(round) {
  if (!round.candidates_frozen) return null;
  const current = state();
  if (current.step !== "next_step") return null;
  const status = el("div");
  return el("div", {}, el("div", { class: "row" }, button(t("New round from these candidates"), "small", () => {
    submitForm("new_round", { source: round.id }, status, { message: t("New round created; these candidates will be simulated again"), done: (result) => go(result.workflow) });
  })), status);
}

function nextStepPanel() {
  const key = "next_step";
  const status = el("div");
  const current = state();
  const preview = el("div");
  const rounds = approvedRounds();
  // Changing an option's own field picks that option too.
  const pick = (value, event) => {
    setDraft(key, "choice", value);
    const radio = event.target.closest("label.choice")?.querySelector("input[type=radio]");
    if (radio) radio.checked = true;
  };
  const source = el("select", { onchange: (event) => { setDraft(key, "source", event.target.value); pick("round", event); } }, el("option", { value: "", text: t("New candidates drafted by the agent") }),
    rounds.map((round) => el("option", { value: round.id, text: t("Candidates from {round}", { round: itemLabel(round) }), selected: getDraft(key, "source", "") === round.id })));
  const candidate = el("input", { type: "text", value: getDraft(key, "candidate", current.selected_candidate || ""), placeholder: t("Candidate ID"),
                                  oninput: (event) => { setDraft(key, "candidate", event.target.value); pick("validate", event); } });
  // The conclusion is for one configuration: the current selection unless another is picked here.
  const configs = app.notebook?.configs || [];
  const chosenConfig = () => getDraft(key, "config", configs.find((config) => config.selected)?.id || "");
  const target = el("select", { onchange: (event) => { setDraft(key, "config", event.target.value); pick("conclude", event); showPreview(); } },
    configs.map((config) => el("option", { value: config.id, text: config.selected ? t("{id} (current)", { id: config.id }) : config.id, selected: chosenConfig() === config.id })));
  const showPreview = async () => {
    const config = chosenConfig();
    const result = await sessionApi(`preview/conclude${config ? `?candidate=${encodeURIComponent(config)}` : ""}`);
    preview.replaceChildren(result.will_fail ? el("p", { class: "form-error", text: t("Cannot submit yet: {reason}", { reason: tm(result.will_fail) }) }) :
      kv((result.facts || []).map(([name, value]) => [t(name), factValue(value)])));
  };
  const options = [["round", "Another simulation round", "Uses no hardware budget.", el("label", { class: "field" }, el("span", { text: t("Candidates") }), source)],
                   ["measure", "Choose hardware points", "Measure a few points on the hardware stand-in to check the simulation; uses the hardware budget.", null],
                   ["validate", "Validate on the simulator", "Run the repeat and holdout workloads for the selected candidate.", el("label", { class: "field" }, el("span", { text: t("Candidate") }), candidate)],
                   ["conclude", "Submit a conclusion", "Preview the conclusion level first. Exploration can continue after submitting, and a later submission replaces this one.",
                    el("div", {}, configs.length ? el("label", { class: "field" }, el("span", { text: t("Configuration") }), target) : null, preview)]];
  const chosen = getDraft(key, "choice", "");
  if (chosen === "conclude") showPreview();
  return el("div", { class: "form", id: "form-next_step" }, el("h4", { text: t("Next step") }),
    current.measurement_stage === "measuring" ? el("p", { class: "lead", text: t("The measurement is queued; simulation can continue meanwhile.") }) : null,
    el("div", { class: "choices" }, options.map(([value, label, help, extra]) => el("label", { class: "choice" },
      el("input", { type: "radio", name: "next-step", value, checked: chosen === value, onchange: () => { setDraft(key, "choice", value); if (value === "conclude") showPreview(); } }),
      el("span", {}, el("b", { text: t(label) }), el("br"), el("small", { class: "help", text: t(help) }), extra)))),
    textField(key, "detail", t("Reason (optional)"), { rows: 2 }),
    el("div", { class: "row" }, button(t("Confirm"), "primary", () => {
      const choice = getDraft(key, "choice", "");
      if (!choice) { status.replaceChildren(el("p", { class: "form-error", text: t("Select an option") })); return; }
      if (choice === "conclude") submitForm("conclude", { candidate: chosenConfig() || null }, status, { form: key, message: t("Conclusion submitted; report updated"), done: () => go("report") });
      else submitForm("next_step", { choice, detail: getDraft(key, "detail", ""), source: getDraft(key, "source", "") || null,
                                     candidate: getDraft(key, "candidate", current.selected_candidate || "") || null }, status,
                      { form: key, done: (result) => { if (result.workflow || result.measurement) go(result.workflow || result.measurement); } });
    })), status);
}

function unlockPanel(target, errors) {
  const status = el("div");
  return el("div", { class: "form", id: "form-unlock" }, el("h4", { text: t("Locked after three identical errors") }),
    el("p", { class: "lead", text: t("Check the error record and the agent's attempts. Unlocking resets the count so the agent can try another way.") }),
    kv(Object.entries(errors || {}).map(([code, count]) => [code, t("{n} times", { n: count })])),
    button(t("Unlock"), "primary", () => submitForm("unlock", { target }, status)), status);
}

// One run: configuration, constraints, coverage, logs and its request timeline.
function viewRun(run) {
  const nodes = [head(run.id, [...crumbs(), [run.owner, run.owner], [run.id, null]], [ask("run", run.id), button(t("Add to compare"), "ghost small", () => addToTray(run.id)),
    run.config ? button(t("Configuration {id}", { id: run.config }), "ghost small", () => go(run.config)) : null],
    statusText(run.status))];
  const metric = app.overview?.key_metrics?.[0];
  nodes.push(section(t("Summary"), "mlab", kv([[t("Candidate"), run.candidate], [t("Source"), t(run.role === "hardware" ? "Hardware (stand-in)" : "Simulation")],
    [t("Workload"), `${run.holdout ? t("Holdout: {name}", { name: run.holdout }) : run.split}${run.workload ? ` (${run.workload})` : ""}`], ["seed / repeat", run.seed === undefined ? null : `${run.seed} / ${run.repeat}`],
    [t("Verdict"), run.verdict ? verdictCell(run.verdict) : null], [metric, run.key_value === undefined ? null : `${format(run.key_value)} ${unitOf(metric)}`],
    [t("Margin"), run.margin_pct === undefined || run.margin_pct === null ? null : `${run.margin_pct}%`], [t("Failure"), run.failure],
    [t("Progress"), run.progress ? statusText(run.progress.phase) : null]])));
  if (run.paired) {
    nodes.push(section(t("Matching prediction"), "mlab", el("p", {}, t("Prediction frozen at approval: "), runLink(run.paired.sim_run),
      ` ${t("simulated {value}", { value: format(run.paired.simulated) })}`)));
  }
  if (run.constraints?.length) {
    nodes.push(section(t("Constraints"), "mlab", table([{ label: t("Metric") }, { label: t("Requirement") }, { label: t("Observed"), num: true }, { label: t("Met") }],
      run.constraints.map((item) => [item.metric, `${item.op} ${item.value}`, format(item.observed), item.met === true ? el("span", { class: "ok", text: t("Yes") }) :
        item.met === false ? el("span", { class: "bad", text: t("No") }) : "—"]))));
  }
  if (run.config_diff?.length) {
    nodes.push(section(t("Effective vs. requested configuration"), "mlab", table([{ label: t("Field") }, { label: t("Requested") }, { label: t("Effective") }],
      run.config_diff.map((item) => [item.field, String(item.requested ?? t("default")), String(item.effective)]))));
  }
  if (run.holdout_change) {
    nodes.push(el("div", { class: "banner", text: t("The holdout protocol changes one dimension: {field} {before} → {after}", run.holdout_change) }));
  }
  if (run.coverage) {
    nodes.push(section(t("Coverage and assumptions"), "mlab", el("ul", {}, (run.coverage.assumptions || []).map((item) => el("li", { text: tm(item) }))),
      run.coverage.extrapolated?.length ? el("p", { class: "form-error", text: t("Outside the simulator's range: {items}", { items: run.coverage.extrapolated.join(", ") }) }) : null));
  }
  if (run.verdict) {
    const timeline = el("div", {}, el("p", { class: "note", text: t("Loading the request timeline…") }));
    sessionApi(`runs/${run.id}/requests`).then((result) => {
      timeline.replaceChildren(result.ok && result.rows.length ? timelineChart(result) : el("p", { class: "empty", text: t("No request records for this run") }));
    });
    nodes.push(timeline);
  }
  if (run.logs) {
    nodes.push(el("details", { class: "section" }, el("summary", { text: t(run.logs_truncated ? "Logs (first 2000 characters)" : "Logs") }), el("pre", { class: "text mono", text: run.logs })));
  }
  return nodes;
}

// One hardware measurement: the student's points, the frozen predictions, and the hardware results next to them.
function viewMeasurement(measurement) {
  const current = state();
  const isCurrent = measurement.editable && current.measurement === measurement.id;
  const form = isCurrent ? current.form : null;
  const status = measurement.stage === "measured" ? "measured" : measurement.status;
  return [head(t("Measurement {id}", { id: measurement.id }), [...crumbs(), [measurement.id, null]], [ask("measurement", measurement.id)],
    statusText(status)),
    isCurrent && form === "unlock" ? unlockPanel("measurement", measurement.errors) : null,
    measurement.status === "draft" ? pointsSection(measurement, isCurrent ? form : null) : frozenPoints(measurement),
    measurementRuns(measurement),
    measurement.alignment?.rows.length ? comparisonSections(measurement) : null];
}

function pointRows(points) {
  return table([{ label: t("Candidate") }, { label: t("Round") }, { label: t("Workload") }, { label: t("No. of runs"), num: true }, { label: t("Reason") }],
    points.map((point) => [point.candidate, point.workflow || t("latest round"), point.workload, point.runs, point.rationale]));
}

function frozenPoints(measurement) {
  return [section(t("Hardware points"), "student", pointRows(measurement.points)),
          measurement.predictions ? section(t("Simulator predictions frozen at approval"), "mlab", predictionTable(measurement.predictions)) : null];
}

function predictionTable(rows) {
  const metric = app.overview?.key_metrics?.[0];
  return table([{ label: t("Candidate") }, { label: t("Workload") }, { label: t("No. of runs"), num: true }, { label: t("Simulated {metric}", { metric }), num: true }, { label: t("Margin"), num: true }],
    rows.map((row) => [row.candidate, row.workload, row.runs, row.sim_run ? format(row.simulated) : t("No prediction yet"),
      row.sim_margin_pct === undefined || row.sim_margin_pct === null ? "—" : `${row.sim_margin_pct}%`]));
}

function pointsSection(measurement, form) {
  const nodes = [];
  if (measurement.points_error) nodes.push(el("div", { class: "banner warn", text: tm(measurement.points_error) }));
  if (form === "points" || form === "approve_points") nodes.push(pointsForm(measurement));
  else if (measurement.points.length) nodes.push(section(t("Hardware points"), "student", pointRows(measurement.points)));
  if (measurement.draft) {
    nodes.push(section(t(measurement.draft.checked ? "Simulator predictions (checked)" : "Simulator predictions (not checked)"), "mlab",
      measurement.draft.error ? el("p", { class: "form-error", text: tm(measurement.draft.error) }) : predictionTable(measurement.draft.rows)));
  }
  if (form === "approve_points" && measurement.draft?.checked) nodes.push(approvePointsPanel(measurement));
  return nodes;
}

function pointsForm(measurement) {
  const key = "points";
  const status = el("div");
  const holdouts = app.overview?.holdouts || [];
  const initial = measurement.points.map((point) => ({ ...point }));
  let points = getDraft(key, "points", initial);
  const budget = measurement.budget;
  const totals = el("div");
  const list = el("div");
  const save = (next) => { points = next; setDraft(key, "points", next); draw(); };
  const metric = app.overview?.key_metrics?.[0];
  const pick = el("select", {}, el("option", { value: "", text: t("Add a candidate from a decided round…") }), measurement.choices.map((choice, index) => el("option", {
    value: String(index), text: t(choice.selected ? "{candidate} (round {n}, selected): simulated {value}, margin {margin}%" : "{candidate} (round {n}): simulated {value}, margin {margin}%",
                                  { candidate: choice.candidate, n: choice.iteration, value: format(choice.value), margin: choice.margin_pct ?? "—" }),
  })));
  pick.addEventListener("change", () => {
    const choice = measurement.choices[Number(pick.value)];
    if (choice) save([...points, { candidate: choice.candidate, workflow: choice.workflow, workload: "development", runs: 1, rationale: "" }]);
    pick.value = "";
  });
  function draw() {
    const used = points.reduce((sum, point) => sum + Number(point.runs || 1), 0);
    totals.replaceChildren(el("p", { class: used > budget.remaining || used > measurement.per_batch ? "form-error" : "note",
      text: t("Uses {used} hardware runs; {left} left after approval ({total} in total, at most {batch} per batch).",
              { used, left: budget.remaining - used, total: budget.total, batch: measurement.per_batch }) }),
    meter(budget.used + used, budget.total));
    list.replaceChildren(...points.map((point, index) => {
      const change = (field, value) => save(points.map((item, at) => (at === index ? { ...item, [field]: value } : item)));
      return el("div", { class: "section" }, el("header", {}, el("h3", { text: `${point.candidate} (${point.workflow})` }),
        button(t("Remove"), "ghost small", () => save(points.filter((_, at) => at !== index)))),
        el("div", { class: "row" },
          el("label", { class: "field" }, el("span", { text: t("Workload") }), el("select", { onchange: (event) => change("workload", event.target.value) },
            ["development", ...holdouts].map((name) => el("option", { value: name, text: name === "development" ? t("Development workload") : t("Holdout: {name}", { name }), selected: point.workload === name })))),
          el("label", { class: "field" }, el("span", { text: t("No. of runs") }), el("input", { type: "number", min: 1, max: 4, value: String(point.runs || 1),
            onchange: (event) => change("runs", Math.max(1, Math.min(4, Number(event.target.value) || 1))) }))),
        el("label", { class: "field" }, el("span", { text: t("Reason (verbatim)") }), el("input", { type: "text", value: point.rationale || "",
          placeholder: t("Why this point? E.g. it is my pick, but the simulation is only 3% under the SLA"), oninput: (event) => { points[index] = { ...points[index], rationale: event.target.value }; setDraft(key, "points", points); } })));
    }));
  }
  draw();
  return el("div", { class: "form", id: "form-points" }, el("h4", { text: t("Hardware points") }),
    el("p", { class: "lead", text: t("For each point: candidate, workload, runs and reason. More than one run shows the noise between runs.") }),
    el("label", { class: "field" }, el("span", { text: t("Add a candidate (simulated {metric} and margin shown)", { metric }) }), pick), list, totals,
    el("div", { class: "row" }, button(t("Save and ask the agent to check"), "primary", () => send("notify")), button(t("Save only"), null, () => send("save"))), status);
  function send(then) {
    submitForm("points", { points: getDraft(key, "points", points), then }, status, { form: key });
  }
}

function approvePointsPanel(measurement) {
  const status = el("div");
  const facts = el("div", {}, el("p", { class: "note", text: t("Loading…") }));
  sessionApi(`preview/measure_approve?plan=${encodeURIComponent(measurement.draft.hash)}`).then((result) => {
    fill(facts, kv((result.facts || []).map(([name, value]) => [t(name), factValue(value)])), el("ul", {}, (result.effects || []).map((item) => el("li", { text: tm(item) }))),
      result.will_fail ? el("p", { class: "form-error", text: t("Cannot approve: {reason}", { reason: tm(result.will_fail) }) }) : null);
  });
  return el("div", { class: "form", id: "form-approve_points" }, el("h4", { text: t("Approve measurement") }), facts,
    button(t("Approve measurement ({n} runs)", { n: measurement.draft.hardware_runs }), "primary", () => submitForm("approve_points", {}, status)), status);
}

function measurementRuns(measurement) {
  if (!measurement.runs.length) return null;
  const hardware = measurement.runs.filter((run) => run.role === "hardware");
  return [progressList(measurement.runs), hardware.length ? section(t("Hardware runs"), "mlab", table([{ label: t("Run") }, { label: t("Candidate") }, { label: t("Workload") }, { label: t("Verdict") }, { label: t("Value"), num: true }],
    hardware.map((run) => [runLink(run.id), run.candidate, run.holdout || run.split, verdictCell(run.verdict), format(run.key_value)]))) : null];
}

// The hardware results next to the simulator's frozen predictions. Reading them is up to you; the agent helps when asked.
function comparisonSections(measurement) {
  const alignment = measurement.alignment;
  const rows = alignment.rows;
  const pairsBox = el("div");
  const showPairs = async (run) => {
    pairsBox.replaceChildren(el("p", { class: "note", text: t("Loading per-request data…") }));
    const result = await sessionApi(`measurements/${measurement.id}/pairs?run=${encodeURIComponent(run)}`);
    pairsBox.replaceChildren(result.ok ? ratioChart(result) : el("p", { class: "form-error", text: tm(result.error?.message) }));
    app.pairs[measurement.id] = run;
    history.replaceState(null, "", `${link(measurement.id)}?run=${encodeURIComponent(run)}`);  // A shareable link to this view.
  };
  if (app.pairs[measurement.id]) showPairs(app.pairs[measurement.id]);
  const shift = { all: "All requests", tail: "Tail only" };
  return [
    pairedChart(measurement.chart, { onSelect: (run) => go(run) }),
    section(t("Simulation vs. hardware"), "mlab", table([{ label: t("Hardware run") }, { label: t("Simulated"), num: true }, { label: t("Hardware"), num: true }, { label: t("Gap"), num: true },
      { label: t("Hardware interval") }, { label: t("Repeats") }, { label: t("Shift") }, { label: "" }],
      rows.map((row) => [el("span", {}, runLink(row.run_id), el("br"), el("small", { class: "help", text: `${row.candidate}·${row.workload}` })),
        format(row.simulated), format(row.hardware),
        row.gap_pct === null ? "—" : `${row.gap_pct > 0 ? "+" : ""}${row.gap_pct}%`,
        row.hardware_interval ? `${format(row.hardware_interval[0])}–${format(row.hardware_interval[1])}` : "—", row.repeat_values.map((value) => format(value)).join(", ") || "—",
        row.beyond_noise ? el("span", { class: "flag", text: t(shift[row.shift] || "Beyond noise") }) : t("Within noise"), button(t("Per-request ratios"), "ghost small", () => showPairs(row.run_id))])),
      pairsBox),
    section(t("Verdicts and effective configuration"), "mlab", table([{ label: t("Hardware run") }, { label: t("Simulated verdict") }, { label: t("Hardware verdict") },
      { label: t("Simulated margin"), num: true }, { label: t("Hardware margin"), num: true }, { label: t("Configuration differences") }],
      rows.map((row) => [runLink(row.run_id), verdictCell(row.sim_verdict),
        row.verdict_flip ? el("span", { class: "flag", text: t("{verdict} (flipped)", { verdict: t(VERDICT[row.hardware_verdict] || row.hardware_verdict) }) }) : verdictCell(row.hardware_verdict),
        row.sim_margin_pct === null ? "—" : `${row.sim_margin_pct}%`, row.hardware_margin_pct === null ? "—" : `${row.hardware_margin_pct}%`, row.config_diff.join("; ") || t("None")])),
      el("p", { class: alignment.rank_flip ? "flag" : "note", text: t(alignment.rank_flip ? "Order (ascending): simulated {sim}; hardware {hw}; the order flipped" : "Order (ascending): simulated {sim}; hardware {hw}",
        { sim: alignment.ranking.simulated.join(" < ") || "—", hw: alignment.ranking.hardware.join(" < ") || "—" }) }),
      alignment.pending.length ? el("p", { class: "note", text: t("Not finished: {runs}", { runs: alignment.pending.join(", ") }) }) : null),
    el("div", { class: "row" }, button(t("Ask the agent to analyze"), "small", () => prefill(`@${measurement.id} ${t(ASK.measurement)}`))),
    el("details", { class: "section" }, el("summary", { text: t("How to read this") }), el("ul", {}, alignment.notes.map((note) => el("li", { text: tm(note) }))))];
}

function viewReport(report) {
  const status = el("div");
  return [head(t("Report"), [...crumbs(), [t("Report"), null]], [ask("report", []),
    button(t("Regenerate"), "ghost small", () => submitForm("report", { then: "save" }, status))], report.conclusion ? t("Conclusion: {value}", { value: report.conclusion }) : t("No conclusion yet")),
  status, report.written ? null : el("div", { class: "banner", text: t("Preview from the current records. Written to report.md when a conclusion is submitted or on “Regenerate”.") }),
  el("div", { class: "section" }, markdown(report.markdown))];
}

function viewCompare(found) {
  const runs = tray();
  const nodes = [head(t("Compare"), [[crumbs()[0][0], null], [t("Compare"), null]], runs.length ? [ask("compare", runs)] : [])];
  if (found.empty) return [...nodes, el("p", { class: "empty", text: t("The tray needs at least two runs. Add runs with “Add to compare”.") })];
  const comparable = found.checks.every((check) => check.comparable);
  const differing = found.differences.join(", ") || found.checks.filter((c) => !c.comparable).map((c) => c.field).join(", ");
  nodes.push(el("div", { class: `banner ${comparable ? "" : "warn"}`, text: comparable ? t("These runs are directly comparable") : t("These fields differ: {fields}. Compare within the groups below.", { fields: differing }) }));
  nodes.push(section(t("Comparability"), "mlab", table([{ label: t("Field") }, ...found.rows.map((row) => ({ label: row.run_id })), { label: t("Same") }],
    found.checks.map((check) => [check.field, ...check.values.map((value) => short(String(value ?? "—"), 40)), check.comparable ? el("span", { class: "ok", text: t("Yes") }) : el("span", { class: "bad", text: t("No") })]))));
  if (found.groups.length > 1) {
    const groups = found.groups.map((group, index) => t("group {n}: {runs}", { n: index + 1, runs: group.join(", ") })).join("; ");
    nodes.push(el("p", { class: "note", text: t("Comparable groups: {groups}", { groups }) }));
  }
  nodes.push(barChart(found.chart, { onSelect: (run) => go(run) }));
  nodes.push(section(t("Runs and strategy differences"), "mlab", table([{ label: t("Run") }, { label: t("From") }, { label: t("Source") }, { label: t("Workload") }, { label: t("Verdict") },
    { label: found.key_metric, num: true }, { label: t("Differences from {run}", { run: found.rows[0].run_id }) }],
    found.rows.map((row) => [runLink(row.run_id), row.owner, t(row.role === "hardware" ? "Hardware" : "Simulation"), row.holdout || row.split, verdictCell(row.verdict),
      format(row.key_metric_value), row.diff.map((d) => `${d.field}: ${d.base ?? t("default")}→${d.value ?? t("default")}`).join("; ") || "—"]))));
  nodes.push(button(t("Ask the agent to analyze"), "primary", () => prefill(`${runs.map((run) => `@${run}`).join(" ")} ${t(ASK.compare)}`)));
  return nodes;
}

// ---------- right column: the task's agent ----------

function renderRight() {
  $("#right").replaceChildren(el("div", { class: "agent-head", id: "agent-head" }), el("div", { class: "messages", id: "messages", "aria-live": "polite" }),
    el("div", { class: "composer", id: "composer" }));
  renderAgentHead();
  renderMessages();
  renderComposer();
}

function renderAgentHead() {
  const box = $("#agent-head");
  if (!box) return;
  const agent = app.agent || {};
  const usage = agent.usage || {};
  const busy = ["running", "waiting_approval", "waiting_answer", "connecting"].includes(agent.status);
  const tone = agent.status === "error" ? "error" : ["waiting_approval", "waiting_answer"].includes(agent.status) ? "attention" : busy ? "busy" : "info";
  const tokens = (usage.input_tokens || 0) + (usage.output_tokens || 0);
  fill(box, el("div", { class: "agent-line" },
    el("span", { class: `agent-status tone-${tone}`, text: t(agent.label || "Agent offline") }),
    agent.queued ? el("span", { class: "chip", text: t("{n} queued", { n: agent.queued }) }) : null,
    el("div", { class: "row" }, busy ? button(t("Stop"), "small", () => sessionApi("agent/interrupt", {})) : null,
      button(t("Reconnect"), "ghost small", () => sessionApi("agent/restart", {}), { title: t("Disconnect and reconnect, keeping the conversation") }))),
    el("div", { class: "agent-usage", text: t(usage.cost_estimated ? "{turns} turns · {tokens} tokens · ${cost} (estimated)" : "{turns} turns · {tokens} tokens · ${cost}",
      { turns: usage.turns || 0, tokens: tokens.toLocaleString("en-US"), cost: (usage.cost_usd || 0).toFixed(3) }) }),
    agent.detail ? el("div", { class: "agent-detail", text: tm(agent.detail) }) : null);
}

function renderMessages() {
  const box = $("#messages");
  if (!box) return;
  const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  fill(box, app.chat.map(message), app.streaming ? el("div", { class: "msg agent streaming", text: app.streaming }) : null, turnCard());
  if (nearBottom) box.scrollTop = box.scrollHeight;
}

// A file the agent read or wrote, shown from the session's folder: rounds/r001/plan.yaml.
function inSession(path) {
  const marker = `/${app.session}/`;
  const at = String(path).lastIndexOf(marker);
  return at >= 0 ? String(path).slice(at + marker.length) : String(path);
}

const TOOLS = { Read: "Read {path}", Edit: "Edit {path}", Write: "Write {path}" };

function toolLine(entry) {
  if (entry.tool === "Bash") return `$ ${entry.text}`;
  if (TOOLS[entry.tool]) return t(TOOLS[entry.tool], { path: inSession(entry.text) });
  if (entry.tool === "AskUserQuestion") return t("Question: {text}", { text: entry.text });
  return `${entry.tool}: ${entry.text}`;
}

function message(entry) {
  if (entry.role === "student") {
    return el("div", { class: "msg student" }, entry.text,
      entry.refs?.length ? el("div", { class: "refs" }, entry.refs.map((ref) => el("a", { href: link(ref), text: `@${ref}` }))) : null);
  }
  if (entry.role === "agent") return el("div", { class: "msg agent" }, inline(entry.text));
  if (entry.role === "tool") {
    return el("details", { class: `tool ${entry.is_error ? "error" : ""}` }, el("summary", { text: toolLine(entry) }),
      entry.result ? el("pre", { text: entry.result }) : el("p", { class: "note", text: t("Running…") }));
  }
  if (entry.role === "notice") {
    return el("div", { class: `notice ${entry.to === "student" ? "to-student" : ""}` },
      entry.to === "agent" ? el("span", { class: "notice-tag", text: t("Sent to agent") }) : null, tm(entry.text),
      entry.reason ? el("div", { class: "note", text: tm(entry.reason) }) : null,
      entry.action ? el("div", {}, button(t(entry.action.label), "small", () => sessionApi("agent/continue", { owner: entry.action.owner }))) : null);
  }
  if (entry.role === "card") return entry.card === "question" ? questionCard(entry) : approvalCard(entry);
  return el("div", { class: "error-line", text: tm(entry.text) });
}

function approvalCard(entry) {
  const summary = entry.summary || {};
  const pending = entry.state === "pending";
  const reason = el("textarea", { rows: 2, hidden: true, placeholder: t("Reason for denying (sent to the agent and recorded verbatim)") });
  const deny = button(t("Deny"), null, async () => {
    if (reason.hidden) { reason.hidden = false; deny.textContent = t("Confirm denial"); reason.focus(); return; }
    await sessionApi(`approvals/${entry.card_id}`, { decision: "deny", reason: reason.value });
  });
  return el("div", { class: `card ${pending ? "" : "resolved"}` }, cardHead(entry, t(summary.title || "Command")),
    el("div", { class: "command", text: `$ ${entry.text}` }),
    summary.facts?.length ? kv(summary.facts.map(([name, value]) => [t(name), factValue(value) || "—"])) : null,
    summary.effects?.length ? [el("div", { class: "note", text: t("On approval:") }), el("ul", {}, summary.effects.map((item) => el("li", { text: tm(item) })))] : null,
    summary.will_fail ? el("div", { class: "banner warn", text: t("mlab will refuse this: {reason}", { reason: tm(summary.will_fail) }) }) : null,
    (summary.warnings || []).map((item) => el("p", { class: "note", text: tm(item) })),
    pending ? [reason, el("div", { class: "row" }, button(t(summary.will_fail ? "Run anyway" : "Approve"), "primary", () => sessionApi(`approvals/${entry.card_id}`, { decision: "allow" })), deny)] : null);
}

// A card's title, with its state next to it: pending, approved, denied…
function cardHead(entry, title, waiting = t(CARD_STATE.pending)) {
  const state = entry.state === "pending" ? waiting : t(CARD_STATE[entry.state] || entry.state);
  return el("div", { class: "card-head" }, el("span", { class: `card-state state-${entry.state}`, text: state }), el("h4", { text: title }));
}

function questionCard(entry) {
  const pending = entry.state === "pending";
  const questions = entry.questions || [];
  const picked = questions.map(() => new Set());
  const others = questions.map(() => el("input", { type: "text", placeholder: t("Other") }));
  return el("div", { class: `card ${pending ? "" : "resolved"}` }, cardHead(entry, t("Question from the agent"), t("Answer needed")),
    questions.map((question, index) => el("div", {}, el("p", {}, el("b", { text: question.question })),
      el("div", { class: "options" }, (question.options || []).map((option) => el("label", { class: "choice" },
        el("input", { type: question.multiSelect ? "checkbox" : "radio", name: `${entry.card_id}-${index}`, disabled: !pending,
          onchange: (event) => { if (!question.multiSelect) picked[index].clear(); if (event.target.checked) picked[index].add(option.label); else picked[index].delete(option.label); } }),
        el("span", {}, el("b", { text: option.label }), option.description ? el("small", { class: "help", text: ` ${option.description}` }) : null)))),
      pending ? others[index] : null)),
    pending ? el("div", { class: "row" }, button(t("Submit"), "primary", () => {
      const answers = {};
      questions.forEach((question, index) => { const text = others[index].value.trim() || [...picked[index]].join(", "); if (text) answers[question.question] = text; });
      if (Object.keys(answers).length) sessionApi(`questions/${entry.card_id}`, { answers });
    }), button(t("Skip"), null, () => sessionApi(`questions/${entry.card_id}`, { cancel: true }))) : null);
}

function formTarget(current) {
  if (MEASURE_STEPS.has(current.step) && current.measurement) return [current.measurement, null];
  const tab = { hypothesis: "hypothesis", falsifier: "hypothesis", plan_review: "plan", decision: "decision" }[current.step];
  return [current.workflow || "overview", tab];
}

function turnCard() {
  const current = state();
  const agent = app.agent || {};
  if (!current.step || !(current.form || current.actor === "student") || ["running", "waiting_approval", "waiting_answer"].includes(agent.status)) return null;
  const [id, tab] = formTarget(current);
  return el("div", { class: "turn" }, el("h4", { text: t(current.step_label) }), current.hint ? el("p", { text: t(current.hint) }) : null,
    el("div", { class: "row" }, button(t("Open the form"), "primary small", () => { go(id, tab); setPane("middle"); }),
      el("span", { class: "note", text: t("or reply in the chat") })));
}

function renderTurn() {
  renderMessages();
}

function renderQuick() {
  const box = $("#quick");
  if (!box) return;
  box.replaceChildren(...(state().quick_replies || []).map((text) => t(text)).map((text) => el("button", { type: "button", text, onclick: () => sendChat(text) })));
}

function mentionables() {
  const items = [];
  for (const config of app.notebook?.configs || []) items.push([config.id, config.selected ? `${t("Configuration")} · ${t("Current selection")}` : t("Configuration")]);
  for (const item of (app.notebook?.items || []).filter((entry) => entry.kind !== "conclusion")) {
    items.push([item.id, itemLabel(item)]);
    for (const run of item.runs || []) items.push([run.id, `${run.candidate} · ${itemLabel(item)}`]);
  }
  return items;
}

function renderComposer() {
  const box = $("#composer");
  if (!box) return;
  const area = el("textarea", { id: "composer-input", rows: 3, placeholder: t("Message the agent. Use @ to cite a record, e.g. @r002 or @r002/c16. Enter sends; Shift+Enter adds a line."),
                                value: stored(`mlab-compose-${app.session}`, "") });
  const menu = el("ul", { class: "mention", hidden: true, role: "listbox" });
  const stop = el("input", { type: "checkbox", id: "interrupt-first" });
  let options = [];
  let selected = 0;
  const token = () => area.value.slice(0, area.selectionStart).match(/@([\w-]*)$/);
  const close = () => { menu.hidden = true; options = []; };
  const choose = (id) => {
    const found = token();
    if (!found) return;
    const at = area.selectionStart;
    area.value = `${area.value.slice(0, at - found[0].length)}@${id} ${area.value.slice(at)}`;
    close();
    area.focus();
  };
  const update = () => {
    store(`mlab-compose-${app.session}`, area.value);
    const found = token();
    if (!found) { close(); return; }
    options = mentionables().filter(([id]) => id.startsWith(found[1])).slice(0, 12);
    selected = 0;
    menu.replaceChildren(...options.map(([id, label], index) => el("li", { role: "option", "aria-selected": index === selected ? "true" : "false",
      onmousedown: (event) => { event.preventDefault(); choose(id); } }, id, el("small", { text: label }))));
    menu.hidden = !options.length;
  };
  area.addEventListener("input", update);
  area.addEventListener("blur", () => setTimeout(close, 150));
  area.addEventListener("keydown", (event) => {
    if (!menu.hidden && options.length) {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        selected = (selected + (event.key === "ArrowDown" ? 1 : options.length - 1)) % options.length;
        [...menu.children].forEach((child, index) => child.setAttribute("aria-selected", index === selected ? "true" : "false"));
        return;
      }
      if (event.key === "Enter" || event.key === "Tab") { event.preventDefault(); choose(options[selected][0]); return; }
      if (event.key === "Escape") { close(); return; }
    }
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); send(); }
  });
  const send = async () => {
    const text = area.value.trim();
    if (!text) return;
    const result = await sendChat(text, stop.checked ? "interrupt_then_send" : "queue");
    if (result.ok) { area.value = ""; store(`mlab-compose-${app.session}`, ""); stop.checked = false; }
  };
  app.stopFirst = stop;
  box.replaceChildren(el("div", { class: "quick", id: "quick" }), menu, area,
    el("div", { class: "composer-foot" }, el("div", { class: "composer-status", id: "composer-status" }), button(t("Send"), "primary", send)));
  renderQuick();
  renderComposerStatus();
}

// Redrawn on every status event, so it always matches the header.
function renderComposerStatus() {
  const slot = $("#composer-status");
  if (!slot) return;
  const busy = BUSY.includes(app.agent?.status);
  if (!busy && app.stopFirst) app.stopFirst.checked = false;
  fill(slot, busy ? el("span", { text: t("The agent is working; the message is sent when it finishes") }) : null,
    busy ? el("label", {}, app.stopFirst, t("Stop the agent first")) : null);
}

function prefill(text) {
  const area = $("#composer-input");
  if (!area) return;
  area.value = text;
  store(`mlab-compose-${app.session}`, text);
  setPane("right");
  area.focus();
  area.setSelectionRange(text.length, text.length);
}

async function sendChat(text, mode = "queue") {
  const result = await sessionApi("chat", { text, mode });
  if (!result.ok) toast(tm(result.error?.message) || t("Failed to send"));
  return result;
}

// ---------- events from the server ----------

// Events come in batches from short requests (GET /api/events?last=N), which every port forward passes through.
// Handlers only mark what changed; each part of the page is redrawn once per batch.
const EVENTS = {
  "task.state": (data, dirty) => {
    const before = app.states[data.session] || {};
    app.states[data.session] = { ...before, ...data.state };
    if (data.state.badge) app.badges[data.session] = data.state.badge;
    dirty.add("sessions");
    if (data.session !== app.session) return;
    dirty.add("facts").add("messages").add("quick");
    if (before.step !== data.state.step || before.form !== data.state.form || before.selection !== data.state.selection) dirty.add("all");
  },
  "agent.status": (data, dirty) => {
    if (data.badge) app.badges[data.session] = data.badge;
    dirty.add("sessions");
    if (data.session !== app.session) return;
    const wasBusy = BUSY.includes(app.agent?.status);
    app.agent = { ...(app.agent || {}), status: data.status, label: data.label, detail: data.detail, queued: data.queued ?? app.agent?.queued };
    dirty.add("agent");
    if (wasBusy !== BUSY.includes(data.status)) dirty.add("messages");
  },
  "agent.usage": (data, dirty) => { if (data.session === app.session) { app.agent = { ...(app.agent || {}), usage: data.usage }; dirty.add("agent"); } },
  "records.changed": (data, dirty) => { if (data.session === app.session) dirty.add("record"); },
  "runs.progress": (data, dirty) => { if (data.session === app.session) dirty.add("record"); },
  "chat.append": (data, dirty) => {
    if (data.session !== app.session) {
      app.unread[data.session] = (app.unread[data.session] || 0) + 1;
      dirty.add("sessions");
      return;
    }
    if (!app.chat.some((item) => item.id === data.entry.id)) app.chat.push(data.entry);
    if (data.entry.role === "agent") app.streaming = "";
    dirty.add("messages");
  },
  "chat.update": (data, dirty) => {
    if (data.session !== app.session) return;
    const entry = app.chat.find((item) => item.id === data.entry.id);
    if (entry) Object.assign(entry, data.entry);
    dirty.add("messages");
  },
  "chat.delta": (data, dirty) => { if (data.session === app.session) { app.streaming += data.text; dirty.add("messages"); } },
  "approval.request": (data) => { if (data.session !== app.session) toast(t("{session}: approval needed", { session: nameOf(data.session) }), data.session); },
  "question.request": (data) => { if (data.session !== app.session) toast(t("{session}: question from the agent", { session: nameOf(data.session) }), data.session); },
  notify: (data) => toast(t("{session}: {message}", { session: nameOf(data.session), message: tm(data.text) }), data.session),
  "course.changed": (data, dirty) => dirty.add("course"),
};

function nameOf(session) {
  const item = sessionOf(session);
  return item ? t("{task} · Session {n}", { task: item.task, n: item.number }) : session;
}

function redraw(dirty) {
  if (dirty.has("course")) loadCourse();
  else if (dirty.has("sessions")) renderSessions();
  if (dirty.has("facts")) renderFacts();
  if (dirty.has("agent")) { renderAgentHead(); renderComposerStatus(); }
  if (dirty.has("messages")) renderMessages();
  if (dirty.has("quick")) renderQuick();
  if (dirty.has("all")) refreshAll();
  else if (dirty.has("record")) refreshRecord();
}

async function connectEvents() {
  let last = null;
  let quietSince = Date.now();
  for (;;) {
    const result = await api(last === null ? "/api/events" : `/api/events?last=${last}`);
    if (result.ok) {
      const dirty = new Set();
      if (result.resync && last !== null && app.session) reloadPage();
      for (const event of result.events || []) {
        try {
          EVENTS[event.type]?.({ session: event.session, ...event.data }, dirty);
        } catch (error) {
          console.error(event.type, error);
        }
      }
      if (result.events?.length) quietSince = Date.now();
      redraw(dirty);
      last = result.last;
    }
    // Often while something is happening, less when quiet, rarely in a background tab.
    const busy = BUSY.includes(app.agent?.status) || Date.now() - quietSince < 20000;
    await new Promise((resolve) => setTimeout(resolve, document.hidden ? 8000 : busy ? 1000 : 2500));
  }
}

async function reloadPage() {
  const session = app.session;
  const page = await loadPage(session, app.recordId, true);
  if (page.ok && session === app.session) {
    keep(session, page);
    show(session);
  }
}

// ---------- layout on narrow screens ----------

function setPane(name) {
  for (const pane of ["left", "middle", "right"]) $(`#${pane}`).classList.toggle("shown", pane === name);
  for (const control of document.querySelectorAll("#switcher button")) control.setAttribute("aria-pressed", control.dataset.pane === name ? "true" : "false");
}

// A script error is shown on the page, so a student can tell the TA what happened.
function reportError(error) {
  const text = error?.stack ? String(error.stack).split("\n").slice(0, 2).join(" ") : error?.message || String(error);
  if (text.includes("ResizeObserver loop")) return;  // The browser's own notice; nothing broke.
  const box = $("#record");
  if (box) box.prepend(el("div", { class: "banner warn", text: t("Page error: {error}. Reload the page; if it happens again, send this message to the TA.", { error: text }) }));
  console.error(error);
}

window.addEventListener("error", (event) => reportError(event.error || event.message));
window.addEventListener("unhandledrejection", (event) => reportError(event.reason));

async function boot() {
  applyTheme();
  await load(preferred());
  for (const node of document.querySelectorAll("[data-i18n]")) node.textContent = t(node.textContent.trim());
  for (const node of document.querySelectorAll("[aria-label]")) node.setAttribute("aria-label", t(node.getAttribute("aria-label")));
  for (const control of document.querySelectorAll("#switcher button")) control.addEventListener("click", () => setPane(control.dataset.pane));
  connectEvents();
  window.addEventListener("hashchange", route);
  $("#middle").addEventListener("focusout", () => setTimeout(() => { if (app.pending && !editing()) refreshRecord(); }, 50));
  const course = loadCourse();
  if (!parseHash().session) await course;  // Choosing a session needs the list; a link that names one can load at once.
  await route();
  await course;
  prefetch();
}

boot();
