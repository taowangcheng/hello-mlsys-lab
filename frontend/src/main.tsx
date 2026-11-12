import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  Activity,
  ArrowRight,
  ArrowUpRight,
  BookOpen,
  Check,
  ChevronRight,
  Copy,
  Cpu,
  FileText,
  FlaskConical,
  GitCompare,
  LayoutDashboard,
  Layers,
  Maximize2,
  MessageSquare,
  Moon,
  Plus,
  Sun,
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import {
  act,
  api,
  label,
  language,
  nav,
  number,
  route,
  setLanguage,
  setStorageScope,
  store,
  stored,
  t,
  terminal,
  time,
  type Data,
  type Experiment,
  type Workspace,
} from './api';
import { Badge, Button, Empty, ErrorLine, Tradeoff } from './ui';
import { Editor, Measurement } from './editor';
import { Comparison, Notes, RunDetail, RunTable } from './results';
import { Chat } from './chat';
import { RecordView, ExperimentReflection } from './records';
import './style.css';
import './nonce';

const sections = [
  ['overview', 'Task overview', LayoutDashboard],
  ['plans', 'Experiments', Layers],
  ['runs', 'Runs', Activity],
  ['compare', 'Compare', GitCompare],
  ['report', 'Report', FileText],
  ['notes', 'Notes & conclusions', BookOpen],
] as const;

function App() {
  const [location, setLocation] = useState(route);
  const [course, setCourse] = useState<Data | null>(null);
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [error, setError] = useState('');
  const [working, setWorking] = useState(false);
  const [locale, setLocale] = useState(language);
  const [dark, setDark] = useState(stored('mlab-dark', false));
  const [pane, setPane] = useState('workbench');
  const [focused, setFocused] = useState(false);
  const [chatWidth, setChatWidth] = useState(stored('mlab-chat-width', 430));
  const [refs, setRefs] = useState<string[]>([]);
  const [selected, updateSelected] = useState<string[]>([]);
  const [measurement, setMeasurement] = useState<{
    initial?: Experiment;
    measurement?: Data;
  } | null>(null);
  const contentRef = useRef<HTMLElement>(null);
  const positions = useRef<Record<string, number>>({});
  const [directory, setDirectory] = useState(false);
  useEffect(() => {
    const update = () => setLocation(route());
    window.addEventListener('hashchange', update);
    return () => window.removeEventListener('hashchange', update);
  }, []);
  useEffect(() => {
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
    store('mlab-dark', dark);
  }, [dark]);
  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);
  const reload = useCallback(async () => {
    if (!location.session) return;
    const [value, latest] = await Promise.all([
      api(`/api/workbench/sessions/${location.session}`),
      api('/api/course'),
    ]);
    if (route().session === location.session) {
      setWorkspace(value);
      setCourse(latest);
    }
  }, [location.session]);
  useEffect(() => {
    let current = true;
    setWorkspace(null);
    setError('');
    setRefs([]);
    setMeasurement(null);
    updateSelected([]);
    setDirectory(false);
    async function load() {
      const latest = await api('/api/course');
      if (!current) return;
      setStorageScope(latest.storage_id);
      setCourse(latest);
      if (!latest.tasks.some((item: Data) => item.task === location.task)) {
        nav('9.2', 'overview');
        return;
      }
      if (!location.session) {
        const saved = stored(`mlab-session-${location.task}`, '');
        const available = latest.sessions.filter((item: Data) => item.task === location.task);
        const session =
          available.find((item: Data) => item.id === saved)?.id ||
          available.at(-1)?.id ||
          (await api(`/api/workbench/tasks/${location.task}/open`, {})).session;
        if (current)
          nav(session, location.page === 'conversation' ? 'overview' : location.page, location.key);
        return;
      }
      if (!latest.sessions.some((item: Data) => item.id === location.session))
        throw Error(t('This conversation is not available. Choose one from the list.'));
      store(`mlab-session-${location.task}`, location.session);
      updateSelected(stored(`mlab-compare-${location.session}`, []));
      await api(`/api/workbench/sessions/${location.session}/open`, {});
      const value = await api(`/api/workbench/sessions/${location.session}`);
      if (current) setWorkspace(value);
    }
    load().catch((failure) => {
      if (current) setError(failure.message);
    });
    const timer = setInterval(() => {
      if (!document.hidden && location.session)
        Promise.all([api(`/api/workbench/sessions/${location.session}`), api('/api/course')])
          .then(([value, latest]) => {
            if (current) {
              setWorkspace(value);
              setCourse(latest);
            }
          })
          .catch((failure) => {
            if (current) setError(failure.message);
          });
    }, 2200);
    return () => {
      current = false;
      clearInterval(timer);
    };
  }, [location.task, location.session]);
  const viewKey = `${location.session}/${location.page}/${location.key}`;
  useEffect(() => {
    if (location.session) {
      store(`mlab-location-${location.session}`, { page: location.page, key: location.key });
      if (course) {
        const current = course.sessions.find((item: Data) => item.id === location.session);
        if (current) store(`mlab-seen-${location.session}`, current.last_message_seq || 0);
      }
    }
    if (contentRef.current) contentRef.current.scrollTop = positions.current[viewKey] || 0;
  }, [
    viewKey,
    workspace?.session,
    course?.sessions.find((item: Data) => item.id === location.session)?.last_message_seq,
  ]);
  function setSelected(keys: string[]) {
    updateSelected(keys);
    store(`mlab-compare-${location.session}`, keys);
  }
  function addCompare(key: string) {
    updateSelected((previous) => {
      const next = [...new Set([...previous, key])].slice(0, 12);
      store(`mlab-compare-${location.session}`, next);
      return next;
    });
  }
  function selectSession(session: string) {
    const previous = stored(`mlab-location-${session}`, { page: 'overview', key: '' });
    nav(session, previous.page === 'conversation' ? 'overview' : previous.page, previous.key);
    setPane('workbench');
    setFocused(false);
  }
  async function newSession() {
    const startingLocation = window.location.hash;
    setWorking(true);
    setError('');
    try {
      const value = await api('/api/sessions', { task: location.task });
      if (window.location.hash === startingLocation) {
        nav(value.session, 'overview');
        setPane('workbench');
        setFocused(false);
      }
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setWorking(false);
    }
  }
  async function create(source?: Experiment, candidate?: string) {
    if (!workspace) return;
    setWorking(true);
    setError('');
    try {
      const candidates = source
        ? source.candidates.filter((item) => !candidate || item.id === candidate)
        : [
            workspace.templates.find(
              (item) =>
                item.id ===
                (
                  { '9.1': 'int4_7b', '9.2': 'baseline', '9.3': 'fp8_single' } as Record<
                    string,
                    string
                  >
                )[workspace.task],
            ) || workspace.templates[0],
          ];
      const value = await act(workspace.session, 'create', {
        title: source ? `${source.title} · ${t('Variant')}` : 'Untitled experiment',
        question: source?.question || '',
        expectation: '',
        candidates,
        seeds: source?.seeds || [42],
        workloads: source?.workloads || ['development'],
        source: source?.key,
      });
      await reload();
      if (route().session === workspace.session) nav(workspace.session, 'plans', value.key);
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setWorking(false);
    }
  }
  function discuss(keys: string[]) {
    setRefs(keys);
    setPane('conversation');
  }
  function openReference(key: string) {
    if (!workspace || !key.startsWith(workspace.session + '/')) return;
    const identifier = key.slice(workspace.session.length + 1);
    const page =
      identifier === 'report'
        ? 'report'
        : identifier === 'overview'
          ? 'overview'
          : identifier.includes('/')
            ? 'config'
            : identifier.startsWith('m')
              ? 'measurements'
              : identifier.startsWith('r')
                ? 'plans'
                : 'runs';
    nav(workspace.session, page, key);
    setPane('workbench');
    setFocused(false);
  }
  function resize(event: React.PointerEvent<HTMLDivElement>) {
    event.currentTarget.setPointerCapture(event.pointerId);
    const start = event.clientX;
    const width = chatWidth;
    const node = event.currentTarget;
    const move = (next: PointerEvent) =>
      setChatWidth(Math.max(340, Math.min(650, width + start - next.clientX)));
    const end = () => {
      node.removeEventListener('pointermove', move);
      node.removeEventListener('pointerup', end);
    };
    node.addEventListener('pointermove', move);
    node.addEventListener('pointerup', end);
  }
  useEffect(() => {
    store('mlab-chat-width', chatWidth);
  }, [chatWidth]);
  const run = workspace?.runs.find((item) => item.key === location.key);
  const experiment = workspace?.experiments.find((item) => item.key === location.key);
  const inProgress = workspace?.runs.filter((item) => !terminal(item)).length || 0;
  let content: React.ReactNode = null;
  if (workspace) {
    if (location.page === 'plans' && experiment?.editable)
      content = (
        <Editor
          key={experiment.key}
          original={experiment}
          workspace={workspace}
          reload={reload}
          onMeasure={(item) => setMeasurement({ initial: item })}
        />
      );
    else if (location.page === 'plans' && experiment)
      content = (
        <ExperimentDetail
          experiment={experiment}
          workspace={workspace}
          onClone={create}
          onMeasure={(item) => setMeasurement({ initial: item })}
          onDiscuss={discuss}
          reload={reload}
        />
      );
    else if (location.page === 'plans')
      content = <Experiments workspace={workspace} create={create} />;
    else if (location.page === 'runs' && run)
      content = (
        <RunDetail
          key={run.key}
          run={run}
          workspace={workspace}
          onClone={create}
          onMeasure={(item) => setMeasurement({ initial: item })}
          onDiscuss={discuss}
          onCompare={addCompare}
          reload={reload}
        />
      );
    else if (location.page === 'compare')
      content = (
        <Comparison
          workspace={workspace}
          selected={selected}
          onDiscuss={discuss}
          onClear={() => setSelected([])}
        />
      );
    else if (location.page === 'notes')
      content = <Notes workspace={workspace} refs={selected} reload={reload} />;
    else if (['report', 'config', 'measurements'].includes(location.page))
      content = (
        <RecordView
          key={location.key || 'report'}
          workspace={workspace}
          reference={location.key || `${workspace.session}/report`}
          onDiscuss={discuss}
          onOpen={openReference}
          reload={reload}
        />
      );
    else if (['overview', 'conversation'].includes(location.page))
      content = <Overview workspace={workspace} create={() => create()} />;
    else
      content = (
        <RunTable
          workspace={workspace}
          selected={selected}
          setSelected={setSelected}
          onMeasure={() => setMeasurement({})}
          onCreate={() => create()}
        />
      );
  }
  const activeSessions = (course?.sessions || [])
    .filter((item: Data) => item.task === location.task)
    .slice()
    .reverse();
  return (
    <div
      className={`app-shell studio-shell pane-${pane} ${focused ? 'focus-conversation' : ''}`}
      style={{ '--conversation-width': `${chatWidth}px` } as React.CSSProperties}
    >
      <aside className="navigation" aria-label={t('Tasks and conversations')}>
        <a className="brand" href={`#/${location.session || location.task}/overview`}>
          <span className="brand-symbol">
            m<span>·</span>
          </span>
          <span>
            hello-mlsys<small>{t('EXPERIMENT LAB')}</small>
          </span>
        </a>
        <div className="course-label">{t('YOUR TASKS')}</div>
        <div className="task-switcher">
          {(
            course?.tasks || [
              { task: '9.1', title: 'On-device assistant' },
              { task: '9.2', title: 'Single-node serving under an SLA' },
              { task: '9.3', title: 'Workload-driven inference cluster' },
            ]
          ).map((task: Data) => (
            <button
              key={task.task}
              className={location.task === task.task ? 'selected' : ''}
              onClick={() => {
                if (task.task !== location.task) {
                  window.location.hash = `/${task.task}/overview`;
                  setPane('workbench');
                }
              }}
            >
              <span>{task.task}</span>
              <strong>{t(task.title)}</strong>
              {location.task === task.task && <ChevronRight size={14} />}
            </button>
          ))}
        </div>
        <div className="nav-rule" />
        <div className="session-heading">
          <span>{t('Conversations')}</span>
          <button
            className="icon-button"
            title={t('Start fresh. Earlier work stays in its own conversation.')}
            aria-label={t('New conversation')}
            disabled={working}
            onClick={newSession}
          >
            <Plus size={17} />
          </button>
        </div>
        <div className="session-list">
          {activeSessions.map((item: Data) => {
            const busy = ['running', 'connecting'].includes(item.agent);
            const waiting = ['waiting_approval', 'waiting_answer'].includes(item.agent);
            const unread =
              item.id !== location.session &&
              item.last_message_seq > stored(`mlab-seen-${item.id}`, 0);
            return (
              <button
                key={item.id}
                className={`session-item ${item.id === location.session ? 'selected' : ''}`}
                aria-label={t('Conversation {number}', { number: item.number })}
                aria-current={item.id === location.session ? 'true' : undefined}
                onClick={() => selectSession(item.id)}
              >
                <span className="session-item-top">
                  <strong>{t('Conversation {number}', { number: item.number })}</strong>
                  {unread && <i className="unread-dot" />}
                  <small title={time(item.created_at)}>
                    {new Date(item.created_at).toLocaleTimeString(locale, {
                      hour: '2-digit',
                      minute: '2-digit',
                    })}
                  </small>
                </span>
                <span className="session-title">
                  {item.title && !course?.tasks.some((task: Data) => task.title === item.title)
                    ? item.title
                    : t('A fresh start')}
                </span>
                <span className={`session-state ${waiting ? 'attention' : ''}`}>
                  {waiting
                    ? t('Waiting for you')
                    : busy
                      ? t('Working…')
                      : item.conclusion
                        ? t('Conclusion saved')
                        : t('Ready to continue')}
                </span>
              </button>
            );
          })}
        </div>
        <div className="navigation-bottom">
          <div className="budget-card">
            <div>
              <Cpu size={16} />
              <span>{t('Measurement budget')}</span>
            </div>
            <strong>
              {workspace?.budget.remaining ?? '—'}
              <small>
                {' '}
                / {workspace?.budget.total ?? '—'} {t('available')}
              </small>
            </strong>
            <div className="budget-track">
              <span
                style={{
                  width: `${workspace ? Math.max(0, (workspace.budget.remaining / workspace.budget.total) * 100) : 0}%`,
                }}
              />
            </div>
            <p>{t('Shared across this task')}</p>
          </div>
          <div className="local-status">
            <span className="status-dot" />
            {t('Local workspace')}
            <span className="demo-label">{t('Demo data')}</span>
          </div>
        </div>
      </aside>
      <div className="work-area">
        <header className="topbar">
          <div className="breadcrumbs">
            <span>{t('Lab')}</span>
            <ChevronRight size={13} />
            <span>{location.task}</span>
            <ChevronRight size={13} />
            <strong>
              {t('Conversation {number}', { number: location.session?.split('-')[1] || '—' })}
            </strong>
          </div>
          <div className="topbar-actions">
            {inProgress > 0 && (
              <button className="running-indicator" onClick={() => nav(location.task, 'runs')}>
                <span className="pulse-dot" />
                {t('{count} running', { count: inProgress })}
              </button>
            )}
            <button
              className="icon-button"
              aria-label={t('Switch theme')}
              onClick={() => setDark(!dark)}
            >
              {dark ? <Sun size={16} /> : <Moon size={16} />}
            </button>
            <button
              className="language-button"
              onClick={() => {
                const next = locale === 'zh' ? 'en' : 'zh';
                setLanguage(next);
                setLocale(next);
              }}
            >
              {locale === 'zh' ? 'EN' : t('Chinese')}
            </button>
            <button
              className="icon-button focus-control"
              aria-label={t(focused ? 'Show workbench' : 'Focus conversation')}
              onClick={() => setFocused(!focused)}
            >
              <Maximize2 size={16} />
            </button>
          </div>
        </header>
        <nav className="mobile-panes" aria-label={t('Workspace panels')}>
          {[
            ['sessions', 'Conversations'],
            ['workbench', 'Workbench'],
            ['conversation', 'Conversation'],
          ].map(([key, text]) => (
            <button
              key={key}
              className={pane === key ? 'selected' : ''}
              onClick={() => setPane(key)}
            >
              {t(text)}
            </button>
          ))}
        </nav>
        <ErrorLine text={error} />
        <div className="studio-columns">
          <section className="evidence-workbench" aria-label={t('Experiment workbench')}>
            <nav className="section-nav record-tabs">
              {sections.map(([key, text, Icon]) => (
                <a
                  key={key}
                  href={`#/${location.session || location.task}/${key}`}
                  className={location.page === key ? 'selected' : ''}
                  aria-label={t(text)}
                >
                  <Icon size={15} />
                  <span>{t(text)}</span>
                  {key === 'compare' && selected.length > 0 && <small>{selected.length}</small>}
                </a>
              ))}
            </nav>
            {workspace && (
              <div className="record-directory">
                <button
                  className="text-button"
                  aria-expanded={directory}
                  onClick={() => setDirectory(!directory)}
                >
                  <Layers size={14} />
                  {t('Record directory')}
                  <span>
                    {workspace.experiments.length} / {workspace.runs.length}
                  </span>
                  <ChevronRight size={13} />
                </button>
                {directory && (
                  <div className="record-directory-items">
                    {workspace.experiments.map((item) => (
                      <div key={item.key}>
                        <button onClick={() => openReference(item.key)}>
                          <FlaskConical size={13} />
                          {item.id} · {item.title}
                        </button>
                        <div>
                          {item.candidates.map((candidate) => (
                            <button
                              key={candidate.id}
                              onClick={() => openReference(`${item.key}/${candidate.id}`)}
                            >
                              {candidate.id}
                            </button>
                          ))}
                        </div>
                      </div>
                    ))}
                    {workspace.measurements.map((item) => (
                      <button key={item.key} onClick={() => openReference(item.key)}>
                        <Cpu size={13} />
                        {item.id} · {item.title}
                      </button>
                    ))}
                    <button onClick={() => openReference(`${workspace.session}/report`)}>
                      <FileText size={13} />
                      {t('Report')}
                    </button>
                    {!workspace.experiments.length && (
                      <p>{t('Experiments and results will appear here as you explore.')}</p>
                    )}
                  </div>
                )}
              </div>
            )}
            <main
              className="workspace-content"
              ref={contentRef}
              key={location.session}
              onScroll={(event) => {
                positions.current[viewKey] = event.currentTarget.scrollTop;
              }}
            >
              {working && (
                <div className="working-banner" role="status">
                  {t('Preparing the workspace…')}
                </div>
              )}
              {workspace?.measurements
                .filter(
                  (item) =>
                    ['measure_plan', 'measure_approved'].includes(item.status) &&
                    !item.runs.some((key: string) => key.startsWith('hw_')),
                )
                .map((item) => (
                  <div className="pending-measurement" key={item.key}>
                    <Cpu size={16} />
                    <span>
                      {t('Measurement waiting for review')}: {item.title}
                    </span>
                    <Button className="small" onClick={() => setMeasurement({ measurement: item })}>
                      {t('Review')}
                    </Button>
                  </div>
                ))}
              {workspace ? (
                content
              ) : (
                <div className="loading-state">
                  <div className="loading-line" />
                  <div className="loading-line short" />
                  <div className="loading-block" />
                </div>
              )}
            </main>
          </section>
          <div
            className="pane-resizer"
            role="separator"
            aria-label={t('Resize conversation')}
            aria-orientation="vertical"
            tabIndex={0}
            onPointerDown={resize}
            onKeyDown={(event) => {
              if (['ArrowLeft', 'ArrowRight'].includes(event.key)) {
                event.preventDefault();
                setChatWidth((value) =>
                  Math.max(340, Math.min(650, value + (event.key === 'ArrowLeft' ? 20 : -20))),
                );
              }
            }}
          />
          <div className="conversation-host">
            {workspace && (
              <Chat
                key={workspace.session}
                workspace={workspace}
                refs={refs}
                setRefs={setRefs}
                mode="panel"
                onNavigate={openReference}
                onRefresh={reload}
              />
            )}
          </div>
        </div>
      </div>
      {measurement && workspace && (
        <Measurement
          workspace={workspace}
          {...measurement}
          onClose={() => setMeasurement(null)}
          reload={reload}
        />
      )}
    </div>
  );
}

function Experiments({
  workspace,
  create,
}: {
  workspace: Workspace;
  create: (source?: Experiment) => void;
}) {
  const [filter, setFilter] = useState('all');
  const experiments = workspace.experiments
    .filter((item) => filter === 'all' || (filter === 'draft' ? item.editable : !item.editable))
    .slice()
    .reverse();
  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">{t('IDEAS INTO EVIDENCE')}</span>
          <h1>{t('Experiments')}</h1>
          <p>{t('Keep several ideas open. Run them when you are ready.')}</p>
        </div>
        <Button className="primary" onClick={() => create()}>
          <Plus size={16} />
          {t('New experiment')}
        </Button>
      </div>
      <div className="segmented compact">
        {[
          ['all', 'All experiments'],
          ['draft', 'Drafts'],
          ['run', 'Executed'],
        ].map(([key, text]) => (
          <button
            className={filter === key ? 'selected' : ''}
            onClick={() => setFilter(key)}
            key={key}
          >
            {t(text)}
          </button>
        ))}
      </div>
      {!experiments.length ? (
        <Empty
          title={t('A place for your next idea')}
          action={
            <Button onClick={() => create()}>
              {t('New experiment')}
              <Plus size={15} />
            </Button>
          }
        >
          {t(
            'Draft a configuration yourself, start from a previous experiment, or ask for a suggestion.',
          )}
        </Empty>
      ) : (
        <div className="experiment-grid">
          {experiments.map((item, index) => (
            <article className="experiment-card" key={item.key}>
              <div className="experiment-card-top">
                <span className="experiment-number">
                  {String(workspace.experiments.length - index).padStart(2, '0')}
                </span>
                <Badge status={item.status} />
              </div>
              <button
                className="experiment-title"
                onClick={() => nav(workspace.task, 'plans', item.key)}
              >
                {item.title === 'Untitled experiment' ? t('Untitled experiment') : item.title}
                <ArrowUpRight size={17} />
              </button>
              <p>{item.question || t('Add a question to give this experiment a direction.')}</p>
              <div className="configuration-pills">
                {item.candidates.slice(0, 4).map((candidate) => (
                  <span key={candidate.id}>{candidate.id}</span>
                ))}
                {item.candidates.length > 4 && <span>+{item.candidates.length - 4}</span>}
              </div>
              <div className="experiment-card-bottom">
                <small>
                  {item.author === 'assistant' ? t('Assistant proposal') : time(item.created_at)}
                </small>
                <button className="text-button" onClick={() => create(item)}>
                  <Copy size={13} />
                  {t('Duplicate')}
                </button>
              </div>
            </article>
          ))}
        </div>
      )}
    </>
  );
}

function ExperimentDetail({
  experiment,
  workspace,
  onClone,
  onMeasure,
  onDiscuss,
  reload,
}: {
  experiment: Experiment;
  workspace: Workspace;
  onClone: (item: Experiment) => void;
  onMeasure: (item: Experiment) => void;
  onDiscuss: (keys: string[]) => void;
  reload: () => Promise<void>;
}) {
  const runs = workspace.runs.filter(
    (run) => run.session === experiment.session && run.owner === experiment.id,
  );
  return (
    <>
      <div className="page-heading">
        <div>
          <button className="text-button" onClick={() => nav(workspace.task, 'plans')}>
            {t('All experiments')}
          </button>
          <h1>{experiment.title}</h1>
          <p>{experiment.question}</p>
        </div>
        <div className="actions">
          <Button onClick={() => onDiscuss([experiment.key])}>
            <MessageSquare size={15} />
            {t('Discuss')}
          </Button>
          <Button onClick={() => onMeasure(experiment)} disabled={!experiment.approved}>
            <Cpu size={15} />
            {t('Measure')}
          </Button>
          <Button className="primary" onClick={() => onClone(experiment)}>
            <Copy size={15} />
            {t('Modify and run again')}
          </Button>
        </div>
      </div>
      <div className="experiment-facts">
        <Badge status={experiment.status} />
        <span>
          {experiment.candidates.length} {t('configurations')}
        </span>
        <span>{experiment.workloads.map(label).join(' · ')}</span>
        <span>{time(experiment.created_at)}</span>
      </div>
      {experiment.expectation && (
        <div className="expectation-note">
          <span className="eyebrow">{t('EXPECTATION BEFORE RUNNING')}</span>
          <ReactMarkdown>{experiment.expectation}</ReactMarkdown>
        </div>
      )}
      <Tradeoff
        rows={runs}
        overview={workspace.overview}
        onSelect={(run) => nav(workspace.task, 'runs', run.key)}
      />
      <section className="detail-panel">
        <h2>{t('Results')}</h2>
        <div className="experiment-results">
          {runs.map((run) => (
            <button key={run.key} onClick={() => nav(workspace.task, 'runs', run.key)}>
              <span>
                <strong>{run.candidate}</strong>
                <small>
                  {label(run.workload)} · {run.id}
                </small>
              </span>
              <Badge status={run.status} />
              <strong>
                {number(run.key_value)} <small>{unitFor(workspace)}</small>
              </strong>
              <ArrowUpRight size={16} />
            </button>
          ))}
        </div>
      </section>
      <div className="configuration-links">
        {experiment.candidates.map((candidate) => (
          <button
            key={candidate.id}
            onClick={() => nav(workspace.task, 'config', `${experiment.key}/${candidate.id}`)}
          >
            {candidate.id}
            <ArrowUpRight size={12} />
          </button>
        ))}
      </div>
      <ExperimentReflection
        workspace={workspace}
        experiment={experiment}
        reload={reload}
        onDiscuss={onDiscuss}
      />
      <details className="detail-panel">
        <summary>{t('Saved configurations')}</summary>
        <pre>{JSON.stringify(experiment.candidates, null, 2)}</pre>
      </details>
    </>
  );
}
function unitFor(workspace: Workspace) {
  return workspace.overview.key_metrics[0].includes('_ms') ? 'ms' : 's';
}

function Overview({ workspace, create }: { workspace: Workspace; create: () => void }) {
  return (
    <>
      <div className="task-hero">
        <div>
          <span className="eyebrow">
            {t('SYSTEMS EXPLORATION')} / {workspace.task}
          </span>
          <h1>{t(workspace.overview.title)}</h1>
          <p>{t('Design a system, test your assumptions, and follow the evidence.')}</p>
          <Button className="primary" onClick={create}>
            {t('Start an experiment')}
            <ArrowRight size={17} />
          </Button>
        </div>
        <div className="hero-diagram" aria-hidden="true">
          <span className="diagram-core">
            <Cpu size={34} />
          </span>
          <span className="diagram-node one">
            <FlaskConical size={22} />
          </span>
          <span className="diagram-node two">
            <Activity size={22} />
          </span>
          <span className="diagram-node three">
            <Layers size={22} />
          </span>
        </div>
      </div>
      <div className="overview-grid">
        <section className="detail-panel">
          <span className="eyebrow">{t('THE TARGET')}</span>
          <h2>{t('What a good result looks like')}</h2>
          <p className="objective-text">
            {label(workspace.overview.objective.metric || workspace.overview.objective.report?.[0])}
          </p>
          <table className="detail-table">
            <tbody>
              {workspace.overview.constraints
                .filter((constraint: Data) => constraint.value !== null)
                .map((constraint: Data) => (
                  <tr key={constraint.metric}>
                    <td>{label(constraint.metric)}</td>
                    <td className="numeric">
                      {constraint.op} {number(constraint.value, 3)}{' '}
                      {constraint.metric.includes('_ms') ? 'ms' : ''}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </section>
        <section className="detail-panel">
          <span className="eyebrow">{t('YOUR ROOM TO EXPLORE')}</span>
          <h2>{t('Two ways to test an idea')}</h2>
          <div className="method-row">
            <FlaskConical size={22} />
            <div>
              <h3>{t('Simulate broadly')}</h3>
              <p>{t('Compare configurations and workloads without spending hardware runs.')}</p>
            </div>
          </div>
          <div className="method-row">
            <Cpu size={22} />
            <div>
              <h3>{t('Measure deliberately')}</h3>
              <p>{t('Choose a few configurations to check against the simulation.')}</p>
            </div>
          </div>
        </section>
      </div>
      <div className="data-notice">
        {t(
          'This workspace uses demonstration data. Results illustrate the experiment process and do not establish real hardware performance.',
        )}
      </div>
      <details className="detail-panel task-guide">
        <summary>{t('Read the task guide')}</summary>
        <ReactMarkdown>{workspace.overview.guide}</ReactMarkdown>
      </details>
    </>
  );
}

createRoot(document.getElementById('root')!).render(<App />);
