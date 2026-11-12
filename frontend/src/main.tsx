import React, { useCallback, useEffect, useState } from 'react';
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
  FlaskConical,
  GitCompare,
  LayoutDashboard,
  Layers,
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
import './style.css';
import './nonce';

const sections = [
  ['overview', 'Task overview', LayoutDashboard],
  ['plans', 'Experiments', Layers],
  ['runs', 'Runs', Activity],
  ['compare', 'Compare', GitCompare],
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
  const [chat, setChat] = useState(stored('mlab-discussion', false));
  const [refs, setRefs] = useState<string[]>([]);
  const [selected, updateSelected] = useState<string[]>([]);
  const [measurement, setMeasurement] = useState<{
    initial?: Experiment;
    measurement?: Data;
  } | null>(null);
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
  useEffect(() => {
    store('mlab-discussion', chat);
  }, [chat]);
  const reload = useCallback(async () => {
    const result = await api(`/api/workbench/tasks/${location.task}`);
    if (route().task === location.task) setWorkspace(result);
  }, [location.task]);
  useEffect(() => {
    let current = true;
    setWorkspace(null);
    setError('');
    setRefs([]);
    setMeasurement(null);
    updateSelected([]);
    api('/api/course')
      .then(async (result) => {
        if (!current) return;
        setStorageScope(result.storage_id);
        updateSelected(stored(`mlab-compare-${location.task}`, []));
        setCourse(result);
        if (!result.tasks.some((item: Data) => item.task === location.task)) {
          nav('9.2', 'runs');
          return;
        }
        await api(`/api/workbench/tasks/${location.task}/open`, {});
        const value = await api(`/api/workbench/tasks/${location.task}`);
        if (current) setWorkspace(value);
      })
      .catch((error) => {
        if (current) setError(error.message);
      });
    const timer = setInterval(() => {
      if (!document.hidden)
        api(`/api/workbench/tasks/${location.task}`)
          .then((value) => {
            if (current) setWorkspace(value);
          })
          .catch((error) => {
            if (current) setError(error.message);
          });
    }, 2200);
    return () => {
      current = false;
      clearInterval(timer);
    };
  }, [location.task]);
  function setSelected(keys: string[]) {
    updateSelected(keys);
    store(`mlab-compare-${location.task}`, keys);
  }
  function addCompare(key: string) {
    updateSelected((previous) => {
      const value = [...new Set([...previous, key])].slice(0, 12);
      store(`mlab-compare-${location.task}`, value);
      return value;
    });
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
      if (route().task === location.task) nav(location.task, 'plans', value.key);
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setWorking(false);
    }
  }
  function discuss(keys: string[]) {
    setRefs(keys);
    setChat(true);
  }
  function openReference(key: string) {
    const identifier = key.split('/')[1];
    nav(location.task, identifier?.startsWith('r') ? 'plans' : 'runs', key);
  }
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
    else if (location.page === 'overview')
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
  return (
    <div className={`app-shell ${chat ? 'with-discussion' : ''}`}>
      <aside className="navigation">
        <a className="brand" href="#/9.2/runs">
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
              onClick={() => nav(task.task, 'runs')}
            >
              <span>{task.task}</span>
              <strong>{t(task.title)}</strong>
              {location.task === task.task && <ChevronRight size={14} />}
            </button>
          ))}
        </div>
        <div className="nav-rule" />
        <nav className="section-nav">
          {sections.map(([key, text, Icon]) => (
            <a
              href={`#/${location.task}/${key}`}
              key={key}
              className={location.page === key ? 'selected' : ''}
            >
              <Icon size={17} />
              <span>{t(text)}</span>
              {key === 'compare' && selected.length > 0 && <small>{selected.length}</small>}
              {key === 'runs' && inProgress > 0 && <span className="nav-dot" />}
            </a>
          ))}
        </nav>
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
            <strong>{t(sections.find(([key]) => key === location.page)?.[1] || 'Runs')}</strong>
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
            <Button className={`small ${chat ? 'selected' : ''}`} onClick={() => setChat(!chat)}>
              <MessageSquare size={15} />
              <span>{t('Discuss')}</span>
            </Button>
          </div>
        </header>
        <main className="workspace-content" key={location.task}>
          <ErrorLine text={error} />
          {working && (
            <div className="working-banner" role="status">
              {t('Preparing the draft…')}
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
      </div>
      {chat && workspace && (
        <Chat
          key={location.task}
          workspace={workspace}
          refs={refs}
          setRefs={setRefs}
          onClose={() => setChat(false)}
          onNavigate={openReference}
        />
      )}
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
}: {
  experiment: Experiment;
  workspace: Workspace;
  onClone: (item: Experiment) => void;
  onMeasure: (item: Experiment) => void;
  onDiscuss: (keys: string[]) => void;
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
