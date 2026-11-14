import { useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowUp,
  AtSign,
  ChevronRight,
  MessageSquare,
  PanelRightClose,
  RotateCw,
  Search,
  Square,
  X,
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import {
  api,
  label,
  language,
  number,
  stored,
  store,
  t,
  unit,
  type Data,
  type Workspace,
} from './api';
import { Button, ErrorLine, Modal } from './ui';
import {
  MentionComposer,
  inlineReferences,
  matchesReference,
  withoutReference,
  type Reference,
} from './mentions';

function references(workspace: Workspace): Reference[] {
  const session = workspace.session;
  const experiments = workspace.experiments.filter((item) => item.session === session);
  const items: Reference[] = [
    {
      key: `${session}/overview`,
      title: t('Task overview'),
      kind: t('Task'),
      detail: workspace.overview.title,
      search: 'task overview',
    },
  ];
  for (const run of workspace.runs
    .slice()
    .reverse()
    .filter((item) => item.session === session))
    items.push({
      key: run.key,
      title: `${run.id} · ${run.candidate}`,
      kind: t(run.role === 'hardware' ? 'Sandbox measurement' : 'Simulation'),
      detail: `${run.session} · ${label(run.workload || 'development')}`,
      search: `${run.key} ${run.candidate} ${run.workload} ${run.role}`,
    });
  for (const experiment of experiments.slice().reverse()) {
    items.push({
      key: experiment.key,
      title: `${experiment.id} · ${experiment.title}`,
      kind: t('Experiment plan'),
      detail: experiment.workloads.map(label).join(', '),
      search: `${experiment.key} ${experiment.title} ${experiment.candidates.map((item) => item.id).join(' ')}`,
    });
    for (const candidate of experiment.candidates)
      items.push({
        key: `${experiment.key}/${candidate.id}`,
        title: candidate.id,
        kind: t('Configuration'),
        detail: `${experiment.id} · ${experiment.title}`,
        search: `${experiment.key}/${candidate.id} ${candidate.rationale || ''}`,
      });
  }
  for (const measurement of (workspace.measurements || []).filter(
    (item: Data) => item.session === session,
  ))
    items.push({
      key: measurement.key,
      title: `${measurement.id} · ${measurement.title}`,
      kind: t('Measurement'),
      detail: t('Simulation and sandbox results'),
      search: `${measurement.key} ${measurement.title}`,
    });
  if (workspace.report_available || workspace.notebook?.report || workspace.conclusions?.length)
    items.push({
      key: `${session}/report`,
      title: t('Report'),
      kind: t('Report'),
      detail: t('Conclusions and supporting evidence'),
      search: 'report conclusions',
    });
  return items;
}
function ExecutionCard({
  entry,
  session,
  refresh,
}: {
  entry: Data;
  session: string;
  refresh: () => void;
}) {
  const [working, setWorking] = useState(false);
  const [error, setError] = useState('');
  const summary = entry.summary || {};
  const pending = entry.state === 'pending';
  const execution = entry.card === 'execution';
  async function decide(allow: boolean) {
    setWorking(true);
    setError('');
    try {
      await api(
        `/api/sessions/${session}/${execution ? 'execution' : 'approvals'}/${entry.card_id}`,
        execution ? { allow } : { decision: allow ? 'allow' : 'deny' },
      );
      refresh();
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setWorking(false);
    }
  }
  const state: Record<string, string> = {
    pending: 'Ready for your confirmation',
    approved: 'Confirmed',
    denied: 'Cancelled',
    cancelled: 'Cancelled',
    expired: 'This request has expired',
    completed: 'Completed',
    failed: 'Run failed',
  };
  return (
    <article className={`conversation-action-card ${pending ? 'pending' : 'resolved'}`}>
      <span className="eyebrow">{t(state[entry.state] || entry.state)}</span>
      <h3>
        {t(execution ? 'Measure in the sandbox' : summary.title || 'Review the proposed action')}
      </h3>
      {execution ? (
        <>
          <p>
            {t('Review the configurations and workloads before using your measurement budget.')}
          </p>
          {summary.backend === 'standin' && (
            <p className="evidence-note">
              {t('Demonstration data — these measurements do not run on a real GPU.')}
            </p>
          )}
          <dl className="conversation-action-facts">
            <div>
              <dt>{t('Configurations')}</dt>
              <dd>{(summary.configs || []).map((item: Data) => item.id).join(', ')}</dd>
            </div>
            <div>
              <dt>{t('Measurement runs')}</dt>
              <dd>{summary.hardware_runs}</dd>
            </div>
            <div>
              <dt>{t('Available budget')}</dt>
              <dd>
                {summary.hardware_budget?.remaining ?? summary.hardware_budget?.available ?? '—'}
              </dd>
            </div>
          </dl>
          <div className="conversation-measurement-points">
            {(summary.points || []).map((point: Data, index: number) => (
              <div key={`${point.candidate}-${point.workload}-${index}`}>
                <strong>{point.candidate}</strong>
                <span>{label(point.workload || 'development')}</span>
                <span>
                  {point.runs === 1 ? t('1 run') : t('{count} runs', { count: point.runs })}
                </span>
                <span>
                  {t('Seed')}: {point.seed}
                </span>
                {typeof point.simulated === 'number' && (
                  <span>
                    {t('Simulation')}: {number(point.simulated)} {unit(summary.metric || '')}
                  </span>
                )}
                {point.sim_status && point.sim_status !== 'succeeded' && (
                  <span className="data-notice">
                    {t(
                      'Simulation did not complete ({reason}). You can still confirm a sandbox check.',
                      { reason: t(point.sim_failure_class || point.sim_status) },
                    )}
                  </span>
                )}
              </div>
            ))}
          </div>
          <details className="conversation-configuration-details">
            <summary>{t('Configuration details')}</summary>
            <pre className="configuration-json">
              {JSON.stringify(summary.configs || [], null, 2)}
            </pre>
          </details>
        </>
      ) : (
        <dl className="conversation-action-facts">
          {(summary.facts || []).map(([name, value]: [string, unknown], index: number) => (
            <div key={index}>
              <dt>{t(name)}</dt>
              <dd>{Array.isArray(value) ? value.map(String).join(', ') : String(value ?? '—')}</dd>
            </div>
          ))}
        </dl>
      )}
      <ErrorLine text={error} />
      {pending && (
        <div className="conversation-action-buttons">
          <Button className="primary" busy={working} onClick={() => decide(true)}>
            {execution
              ? summary.hardware_runs === 1
                ? t('Run measurement')
                : t('Run {count} measurements', { count: summary.hardware_runs })
              : t('Confirm')}
          </Button>
          <Button disabled={working} onClick={() => decide(false)}>
            {t('Cancel')}
          </Button>
        </div>
      )}
    </article>
  );
}
function QuestionCard({
  entry,
  session,
  refresh,
}: {
  entry: Data;
  session: string;
  refresh: () => void;
}) {
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [error, setError] = useState('');
  const [working, setWorking] = useState(false);
  const pending = entry.state === 'pending';
  async function submit(cancel = false) {
    setWorking(true);
    try {
      await api(
        `/api/sessions/${session}/questions/${entry.card_id}`,
        cancel ? { cancel } : { answers },
      );
      refresh();
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setWorking(false);
    }
  }
  return (
    <article className="conversation-action-card">
      <h3>{t('A question for you')}</h3>
      {(entry.questions || []).map((question: Data, index: number) => (
        <div className="conversation-question" key={index}>
          <p>{question.question}</p>
          <div className="conversation-question-options">
            {(question.options || []).map((option: Data) => (
              <Button
                key={option.label}
                disabled={!pending}
                className={answers[question.question] === option.label ? 'selected' : ''}
                onClick={() => setAnswers({ ...answers, [question.question]: option.label })}
              >
                {option.label}
              </Button>
            ))}
          </div>
          {pending && (
            <input
              aria-label={question.question}
              placeholder={t('Or write your answer')}
              value={answers[question.question] || ''}
              onChange={(event) =>
                setAnswers({ ...answers, [question.question]: event.target.value })
              }
            />
          )}
        </div>
      ))}
      <ErrorLine text={error} />
      {pending && (
        <div className="conversation-action-buttons">
          <Button
            className="primary"
            busy={working}
            disabled={!Object.values(answers).some((value) => value.trim())}
            onClick={() => submit()}
          >
            {t('Reply')}
          </Button>
          <Button disabled={working} onClick={() => submit(true)}>
            {t('Skip')}
          </Button>
        </div>
      )}
    </article>
  );
}

// Render simple Markdown tables while keeping raw HTML disabled.
function MessageMarkdown({ text }: { text: string }) {
  const lines = text.split('\n');
  const blocks: { text?: string; rows?: string[][] }[] = [];
  let paragraph: string[] = [];
  let fenced = false;
  const cells = (line: string) =>
    line
      .trim()
      .replace(/^\|/, '')
      .replace(/\|$/, '')
      .split(/(?<!\\)\|/)
      .map((cell) => cell.trim().replaceAll('\\|', '|'));
  const flush = () => {
    if (paragraph.length) blocks.push({ text: paragraph.join('\n') });
    paragraph = [];
  };
  for (let index = 0; index < lines.length; index++) {
    if (/^\s*(```|~~~)/.test(lines[index])) fenced = !fenced;
    const separator = lines[index + 1];
    if (
      !fenced &&
      lines[index].includes('|') &&
      separator &&
      cells(separator).length > 1 &&
      cells(separator).every((cell) => /^:?-{3,}:?$/.test(cell))
    ) {
      flush();
      const rows = [cells(lines[index])];
      index += 2;
      while (index < lines.length && lines[index].trim() && lines[index].includes('|'))
        rows.push(cells(lines[index++]));
      index--;
      blocks.push({ rows });
    } else paragraph.push(lines[index]);
  }
  flush();
  const markdown = (value: string) => (
    <ReactMarkdown
      components={{
        a: ({ href, children }) => (
          <a href={href} target="_blank" rel="noreferrer">
            {children}
          </a>
        ),
      }}
    >
      {value}
    </ReactMarkdown>
  );
  return (
    <>
      {blocks.map((block, index) =>
        block.rows ? (
          <div className="table-wrap conversation-markdown-table" key={index}>
            <table>
              <thead>
                <tr>
                  {block.rows[0].map((cell, column) => (
                    <th key={column}>{markdown(cell)}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {block.rows.slice(1).map((row, rowIndex) => (
                  <tr key={rowIndex}>
                    {block.rows![0].map((_, column) => (
                      <td key={column}>{markdown(row[column] || '')}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div key={index}>{markdown(block.text || '')}</div>
        ),
      )}
    </>
  );
}
function operationLabels(entry: Data): [string, string, string] {
  const labels: Record<string, [string, string, string]> = {
    list: [
      'Review experiment records',
      'Reviewing experiment records…',
      'Experiment records reviewed',
    ],
    show: ['Read a result', 'Reading a result…', 'Result reviewed'],
    draft: [
      'Prepare an experiment plan',
      'Preparing an experiment plan…',
      'Experiment plan prepared',
    ],
    simulate: ['Submit a simulation', 'Starting the simulation…', 'Simulation submitted'],
    measure: [
      'Arrange a sandbox measurement',
      'Preparing the measurement…',
      'Measurement submitted',
    ],
    wait: ['Check run progress', 'Waiting for results…', 'Run progress updated'],
    cancel: ['Stop a run', 'Requesting the run to stop…', 'Stop requested'],
    Read: [
      'Read supporting material',
      'Reading supporting material…',
      'Supporting material reviewed',
    ],
    Edit: ['Update an experiment plan', 'Updating an experiment plan…', 'Experiment plan updated'],
    Write: ['Update an experiment plan', 'Updating an experiment plan…', 'Experiment plan updated'],
    AskUserQuestion: ['Prepare a question', 'Preparing a question…', 'Question prepared'],
  };
  return (
    labels[entry.tool === 'Explore' ? entry.action : entry.tool] || [
      'Operation details',
      'Working on your request',
      'View operation details',
    ]
  );
}
function operationSummary(entries: Data[], active: boolean) {
  const latest = [...entries].reverse();
  const current = latest[0];
  if (!('result' in current))
    return active ? t(operationLabels(current)[1]) : t('No result recorded');
  const entry =
    latest.find(
      (entry) =>
        entry.is_error ||
        ['draft', 'simulate', 'measure', 'cancel'].includes(entry.action) ||
        ['Edit', 'Write'].includes(entry.tool),
    ) || current;
  if (entry.is_error) return `${t(operationLabels(entry)[0])} · ${t('Did not complete')}`;
  return t(operationLabels(entry)[2]);
}
const busyStatuses = ['running', 'connecting', 'waiting_approval', 'waiting_answer'];

export function Chat({
  workspace,
  refs,
  setRefs,
  mode = 'panel',
  focusRequest = 0,
  onClose,
  onNavigate,
  onRefresh,
}: {
  workspace: Workspace;
  refs: string[];
  setRefs: (keys: string[]) => void;
  mode?: 'page' | 'panel';
  focusRequest?: number;
  onClose?: () => void;
  onNavigate: (key: string) => void;
  onRefresh?: () => void;
}) {
  const session = workspace.session;
  const [messages, setMessages] = useState<Data[]>([]);
  const [agent, setAgent] = useState<Data>({ status: 'offline' });
  const [streaming, setStreaming] = useState('');
  const [text, setText] = useState('');
  const [error, setError] = useState('');
  const [connectionError, setConnectionError] = useState('');
  const [sending, setSending] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const [interruptFirst, setInterruptFirst] = useState(false);
  const [picker, setPicker] = useState(false);
  const [search, setSearch] = useState('');
  const [chosen, setChosen] = useState<string[]>([]);
  const [history, setHistory] = useState(false);
  const [older, setOlder] = useState(false);
  const refreshChat = useRef<() => void>(() => {});
  const refreshWorkspace = useRef(onRefresh);
  refreshWorkspace.current = onRefresh;
  const active = useRef(true);
  const scroll = useRef<HTMLDivElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (!focusRequest) return;
    const frame = requestAnimationFrame(() => composer.current?.focus());
    return () => cancelAnimationFrame(frame);
  }, [focusRequest]);
  const follow = useRef(true);
  const draftKey = `mlab-chat-${workspace.task}-${session}`;
  const refsKey = `${draftKey}-refs`;
  const skipRefSave = useRef(false);
  const locale = language;
  const allReferences = useMemo(() => references(workspace), [workspace, locale]);
  const byKey = useMemo(
    () => new Map(allReferences.map((item) => [item.key, item])),
    [allReferences],
  );
  const matches = allReferences.filter((item) => matchesReference(item, search));
  const inline = inlineReferences(text, session).filter((key) => byKey.has(key));
  const attached = [...new Set([...refs, ...inline])];
  const messageLimit = workspace.limits?.message_characters || 32000;
  const oversized = [...text.trim()].length > messageLimit;
  const currentDraft = useRef({ text, refs });
  currentDraft.current = { text, refs };
  const status = agent.status || 'offline';
  const busy = busyStatuses.includes(status);
  useEffect(() => {
    setText(stored(draftKey, ''));
    const saved = stored<unknown>(refsKey, []);
    skipRefSave.current = true;
    setRefs(
      Array.isArray(saved)
        ? saved
            .filter(
              (key): key is string =>
                typeof key === 'string' && key.startsWith(`${session}/`) && byKey.has(key),
            )
            .slice(0, 12)
        : [],
    );
  }, [draftKey]);
  useEffect(() => {
    if (skipRefSave.current) {
      skipRefSave.current = false;
      return;
    }
    store(
      refsKey,
      refs.filter((key) => key.startsWith(`${session}/`) && byKey.has(key)).slice(0, 12),
    );
  }, [refs]);
  useEffect(() => {
    active.current = true;
    let current = true;
    let initialized = false;
    let eventVersion = 0;
    let last: number | null = null;
    let timer: ReturnType<typeof setTimeout>;
    setMessages([]);
    setHistory(false);
    setError('');
    setStreaming('');
    follow.current = true;
    const merge = (incoming: Data[]) =>
      setMessages((previous) => {
        const entries = new Map(previous.map((entry) => [entry.id, entry]));
        for (const entry of incoming) entries.set(entry.id, { ...entries.get(entry.id), ...entry });
        return [...entries.values()].sort((a, b) => a.id.localeCompare(b.id));
      });
    const load = async () => {
      const requestedVersion = eventVersion;
      try {
        const result = await api(`/api/sessions/${session}/chat?limit=100`);
        if (!current || requestedVersion !== eventVersion) return;
        merge(result.entries);
        setAgent(result.agent);
        if (!busyStatuses.includes(result.agent.status)) setStreaming('');
        setConnectionError('');
        if (!initialized) setHistory(result.entries.length === 100);
        initialized = true;
      } catch (error) {
        if (current) setConnectionError((error as Error).message);
      }
    };
    refreshChat.current = load;
    const events = async () => {
      try {
        const result = await api(last === null ? '/api/events' : `/api/events?last=${last}`);
        if (!current) return;
        if (result.resync) setStreaming('');
        if (last === null || result.resync) await load();
        last = result.last;
        let recordsChanged = false;
        for (const event of result.events || []) {
          if (event.session !== session) continue;
          eventVersion++;
          const data = event.data || {};
          if (event.type === 'chat.delta') setStreaming((value) => value + data.text);
          if (event.type === 'chat.append') {
            merge([data.entry]);
            if (data.entry.role === 'agent' || data.entry.role === 'error') setStreaming('');
          }
          if (event.type === 'chat.update') merge([data.entry]);
          if (event.type === 'agent.status') {
            setAgent((previous) => ({ ...previous, ...data }));
            if (!busyStatuses.includes(data.status)) {
              setStreaming('');
              setInterruptFirst(false);
            }
          }
          if (event.type === 'agent.usage')
            setAgent((previous) => ({ ...previous, usage: data.usage }));
          if (['records.changed', 'runs.progress', 'task.state'].includes(event.type))
            recordsChanged = true;
        }
        if (recordsChanged) refreshWorkspace.current?.();
        setConnectionError('');
      } catch (error) {
        if (current) setConnectionError((error as Error).message);
      }
      if (current) timer = setTimeout(events, document.hidden ? 4000 : 650);
    };
    events();
    const resync = setInterval(load, 5000);
    return () => {
      current = false;
      active.current = false;
      clearTimeout(timer);
      clearInterval(resync);
    };
  }, [session]);
  useEffect(() => {
    if (scroll.current && follow.current) scroll.current.scrollTop = scroll.current.scrollHeight;
  }, [messages, streaming, mode]);
  function draft(value: string) {
    currentDraft.current.text = value;
    setText(value);
    store(draftKey, value);
  }
  function openPicker() {
    setChosen(attached.filter((key) => byKey.has(key)));
    setSearch('');
    setPicker(true);
  }
  function changed() {
    refreshChat.current();
    refreshWorkspace.current?.();
  }
  async function loadOlder() {
    if (!messages.length) return;
    setOlder(true);
    follow.current = false;
    try {
      const result = await api(
        `/api/sessions/${session}/chat?limit=100&before=${encodeURIComponent(messages[0].id)}`,
      );
      if (active.current) {
        setMessages((previous) => [...result.entries, ...previous]);
        setHistory(result.entries.length === 100);
      }
    } catch (error) {
      if (active.current) setError((error as Error).message);
    } finally {
      if (active.current) setOlder(false);
    }
  }
  async function send() {
    if (!text.trim() || sending || oversized) return;
    setSending(true);
    setError('');
    follow.current = true;
    try {
      await api(`/api/workbench/sessions/${session}/chat`, {
        text: text.trim(),
        refs: attached.filter((key) => key.startsWith(`${session}/`)),
        mode: interruptFirst ? 'interrupt_then_send' : 'queue',
      });
      if (!active.current) return;
      if (
        currentDraft.current.text === text &&
        JSON.stringify(currentDraft.current.refs) === JSON.stringify(refs)
      ) {
        draft('');
        setRefs([]);
      }
      setInterruptFirst(false);
      changed();
    } catch (error) {
      if (active.current) setError((error as Error).message);
    } finally {
      if (active.current) setSending(false);
    }
  }
  async function reconnect() {
    setReconnecting(true);
    setError('');
    try {
      await api(`/api/sessions/${session}/agent/restart`, {});
      if (active.current) changed();
    } catch (error) {
      if (active.current) setError((error as Error).message);
    } finally {
      if (active.current) setReconnecting(false);
    }
  }
  function messageReferences(message: Data) {
    const keys: string[] = Array.isArray(message.refs) ? [...message.refs] : [];
    const tokens = new Set<string>(
      ((message.text || '').match(/[A-Za-z0-9_./-]+/g) || []).map((value: string) =>
        value.replace(/[.]+$/, ''),
      ),
    );
    for (const item of allReferences) {
      const id = item.key.slice(session.length + 1);
      if (id !== 'overview' && id !== 'report' && (tokens.has(id) || tokens.has(item.key)))
        keys.push(item.key);
    }
    return [...new Set(keys)].filter((key) => key.startsWith(`${session}/`) && byKey.has(key));
  }
  function citation(key: string, removable = false) {
    const reference = byKey.get(key);
    return (
      <span key={key} className="reference-chip">
        <button
          className={removable ? '' : 'citation'}
          onClick={() => onNavigate(key)}
          title={reference ? `${reference.kind} · ${reference.detail}` : key}
        >
          <strong>{reference?.title || key}</strong>
          <small>{reference ? `${reference.kind} · ${reference.detail}` : ''}</small>
        </button>
        {removable && (
          <button
            aria-label={t('Remove reference {reference}', { reference: reference?.title || key })}
            onClick={() => {
              setRefs(refs.filter((item) => item !== key));
              draft(withoutReference(text, key, session));
            }}
          >
            <X size={12} />
          </button>
        )}
      </span>
    );
  }
  const visible: Data[] = [];
  for (const message of messages) {
    if (message.role === 'tool') {
      const previous = visible.at(-1);
      if (previous?.role === 'activity') previous.entries.push(message);
      else visible.push({ id: `activity-${message.id}`, role: 'activity', entries: [message] });
    } else if (
      ['student', 'agent', 'error', 'card'].includes(message.role) ||
      (message.role === 'notice' && message.to === 'student' && !message.reason)
    )
      visible.push(message);
  }
  const operationActive = (index: number) => {
    const later = visible.slice(index + 1);
    return (
      busy &&
      !later.some((entry) => ['agent', 'error'].includes(entry.role)) &&
      later.filter((entry) => entry.role === 'student').length <= (agent.queued || 0)
    );
  };
  const statusLabels: Record<string, string> = {
    offline: 'Ready to chat',
    idle: 'Ready',
    running: 'Working on your request',
    connecting: 'Connecting…',
    waiting_approval: 'Waiting for your confirmation',
    waiting_answer: 'Waiting for your answer',
    error: 'Connection problem',
  };
  const usage = agent.usage || {};
  return (
    <section
      className={`discussion conversation conversation-${mode}`}
      aria-label={t('Conversation')}
    >
      <div className="discussion-header">
        <span>
          <MessageSquare size={18} />
          <strong>{t('Conversation')}</strong>
        </span>
        {onClose && (
          <button className="icon-button" onClick={onClose} aria-label={t('Close discussion')}>
            <PanelRightClose size={17} />
          </button>
        )}
      </div>
      <div className="conversation-agent-bar">
        <span className={`conversation-status ${busy ? 'busy' : ''}`} role="status">
          {busy && <span className="pulse-dot" />}
          {t(statusLabels[status] || 'Ready')}
        </span>
        {agent.queued > 0 && (
          <span>
            {agent.queued === 1
              ? t('1 message queued')
              : t('{count} messages queued', { count: agent.queued })}
          </span>
        )}
        <Button className="small ghost" busy={reconnecting} onClick={reconnect}>
          <RotateCw size={13} />
          {t('Reconnect')}
        </Button>
      </div>
      <div className="conversation-toolbar">
        <Button className="small conversation-reference-button" onClick={openPicker}>
          <AtSign size={15} />
          {t('Add from history')}
        </Button>
        <details className="conversation-usage">
          <summary>{t('Model usage')}</summary>
          <p>
            {t('{turns} turns · {tokens} tokens', {
              turns: usage.turns || 0,
              tokens: number((usage.input_tokens || 0) + (usage.output_tokens || 0), 0),
            })}
            {usage.cost_available !== false && typeof usage.cost_usd === 'number'
              ? ` · $${usage.cost_usd.toFixed(3)}${usage.cost_estimated ? ` ${t('(estimated)')}` : ''}`
              : ''}
          </p>
        </details>
      </div>
      <div
        className="discussion-messages"
        ref={scroll}
        onScroll={(event) => {
          const node = event.currentTarget;
          follow.current = node.scrollHeight - node.scrollTop - node.clientHeight < 80;
        }}
      >
        {history && (
          <Button className="small ghost" busy={older} onClick={loadOlder}>
            {t('Load earlier messages')}
          </Button>
        )}
        {!visible.length && !streaming && (
          <div className="discussion-empty">
            <div className="assistant-mark">m</div>
            <h3>{t('What would you like to explore?')}</h3>
            <p>{t('Ask about the task, discuss an idea, or try an experiment together.')}</p>
            <div className="conversation-starters">
              {[
                [
                  'Understand the task',
                  'Help me understand this task and what a useful result would look like.',
                ],
                [
                  'Plan an experiment',
                  'Help me compare two configurations. Explain the trade-offs before we run them.',
                ],
              ].map(([title, prompt]) => (
                <button
                  key={title}
                  onClick={() => {
                    draft(t(prompt));
                    composer.current?.focus();
                  }}
                >
                  <span>{t(title)}</span>
                  <ChevronRight size={15} />
                </button>
              ))}
            </div>
          </div>
        )}
        {visible.map((message, index) =>
          message.role === 'activity' ? (
            <details className="conversation-activity" key={message.id}>
              <summary>{operationSummary(message.entries, operationActive(index))}</summary>
              {message.entries.map((entry: Data) => (
                <div className="conversation-activity-step" key={entry.id}>
                  <strong>{t(operationLabels(entry)[0])}</strong>
                  <span>
                    {t(
                      entry.is_error
                        ? 'Did not complete'
                        : 'result' in entry
                          ? 'Processed'
                          : operationActive(index)
                            ? 'In progress'
                            : 'No result recorded',
                    )}
                  </span>
                  <div className="message-citations">
                    {messageReferences(entry).map((key) => citation(key))}
                  </div>
                </div>
              ))}
            </details>
          ) : message.role === 'card' ? (
            message.card === 'question' ? (
              <QuestionCard key={message.id} entry={message} session={session} refresh={changed} />
            ) : (
              <ExecutionCard key={message.id} entry={message} session={session} refresh={changed} />
            )
          ) : (
            <article className={`discussion-message ${message.role}`} key={message.id}>
              <span className="message-author">
                {t(
                  message.role === 'student'
                    ? 'You'
                    : message.role === 'error'
                      ? 'Connection'
                      : message.role === 'notice'
                        ? 'Update'
                        : 'Lab assistant',
                )}
              </span>
              <MessageMarkdown text={message.text || ''} />
              {messageReferences(message).length > 0 && (
                <div className="message-citations" aria-label={t('Referenced evidence')}>
                  {messageReferences(message).map((key) => citation(key))}
                </div>
              )}
            </article>
          ),
        )}
        {streaming && (
          <article className="discussion-message agent streaming" aria-live="polite">
            <span className="message-author">{t('Lab assistant')}</span>
            <MessageMarkdown text={streaming} />
          </article>
        )}
        {status === 'running' && !streaming && (
          <div className="reply-status">
            <span className="pulse-dot" />
            {t('Preparing a reply…')}
          </div>
        )}
      </div>
      <div className="discussion-composer">
        <ErrorLine
          text={
            oversized
              ? t('Messages can contain at most {limit} characters. Your draft is kept.', {
                  limit: messageLimit,
                })
              : error || connectionError
          }
        />
        {attached.length > 0 && (
          <div className="discussion-context">
            <span className="eyebrow">{t('ATTACHED TO YOUR NEXT MESSAGE')}</span>
            <div className="reference-chips">
              {attached.filter((key) => byKey.has(key)).map((key) => citation(key, true))}
            </div>
          </div>
        )}
        <MentionComposer
          inputRef={composer}
          value={text}
          onChange={draft}
          onSend={send}
          options={allReferences}
          selected={attached}
          session={session}
        />
        {busy && (
          <div className="conversation-send-mode">
            <span>{t('Your message will follow the current reply')}</span>
            <label>
              <input
                type="checkbox"
                checked={interruptFirst}
                onChange={(event) => setInterruptFirst(event.target.checked)}
              />
              {t('Stop the current reply first')}
            </label>
          </div>
        )}
        <div className="composer-actions">
          <Button className="small ghost" onClick={openPicker}>
            <AtSign size={14} />
            {t('History')}
          </Button>
          <span>{t('Shift + Enter for a new line')}</span>
          {busy && (
            <Button
              className="small ghost"
              onClick={() =>
                api(`/api/sessions/${session}/agent/interrupt`, {})
                  .then(changed)
                  .catch((error) => setError(error.message))
              }
            >
              <Square size={12} />
              {t('Stop reply')}
            </Button>
          )}
          <Button
            className="primary icon-button"
            aria-label={t('Send')}
            busy={sending}
            disabled={!text.trim() || oversized}
            onClick={send}
          >
            <ArrowUp size={17} />
          </Button>
        </div>
      </div>
      <Modal
        title={t('Add from history')}
        description={t('Choose records from this conversation to include with your message.')}
        open={picker}
        onClose={() => setPicker(false)}
        wide
      >
        <label className="history-search">
          <Search size={17} />
          <input
            autoFocus
            aria-label={t('Search history')}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={t('Search configurations, workloads, runs or reports…')}
          />
        </label>
        <div className="history-list">
          {matches.length ? (
            matches.map((item) => (
              <label key={item.key} className="history-option">
                <input
                  type="checkbox"
                  aria-label={`${item.title} · ${item.detail}`}
                  checked={chosen.includes(item.key)}
                  disabled={!chosen.includes(item.key) && chosen.length >= 12}
                  onChange={(event) =>
                    setChosen(
                      event.target.checked
                        ? [...chosen, item.key]
                        : chosen.filter((key) => key !== item.key),
                    )
                  }
                />
                <span>
                  <strong className="history-reference-title">{item.title}</strong>
                  <small className="history-reference-meta">
                    {item.kind} · {item.detail}
                  </small>
                </span>
              </label>
            ))
          ) : (
            <p>{t('No matching records in this conversation.')}</p>
          )}
        </div>
        <div className="dialog-actions">
          <span>{t('{count} of 12 references selected', { count: chosen.length })}</span>
          <Button
            className="primary"
            onClick={() => {
              draft(
                inline
                  .filter((key) => !chosen.includes(key))
                  .reduce((value, key) => withoutReference(value, key, session), text),
              );
              setRefs(chosen.filter((key) => !inline.includes(key)));
              setPicker(false);
              composer.current?.focus();
            }}
          >
            {t('Attach selected')}
          </Button>
        </div>
      </Modal>
    </section>
  );
}
