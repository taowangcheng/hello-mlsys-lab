import { useEffect, useMemo, useRef, useState } from 'react';
import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from '@tanstack/react-table';
import {
  ArrowLeft,
  ArrowUpRight,
  Check,
  Copy,
  Cpu,
  FlaskConical,
  GitCompare,
  MessageSquare,
  Search,
  Square,
  X,
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import {
  act,
  api,
  label,
  metric,
  nav,
  number,
  openReference,
  secondaryMetric,
  store,
  stored,
  t,
  terminal,
  time,
  unit,
  type Data,
  type Experiment,
  type Run,
  type Workspace,
} from './api';
import { Badge, Button, Empty, ErrorLine, Modal, Tradeoff } from './ui';

export function RunTable({
  workspace,
  selected,
  setSelected,
  onMeasure,
  onCreate,
  onDiscuss,
}: {
  workspace: Workspace;
  selected: string[];
  setSelected: (keys: string[]) => void;
  onMeasure: () => void;
  onCreate: () => void;
  onDiscuss: () => void;
}) {
  const filterKey = `mlab-filters-${workspace.session}`;
  const savedFilters = stored<Data>(filterKey, {});
  const [query, setQuery] = useState<string>(savedFilters.query || '');
  const [source, setSource] = useState<string>(savedFilters.source || 'all');
  const [status, setStatus] = useState<string>(savedFilters.status || 'all');
  const [sorting, setSorting] = useState<SortingState>(
    savedFilters.sorting || [{ id: 'created_at', desc: true }],
  );
  useEffect(() => {
    store(filterKey, { query, source, status, sorting });
  }, [filterKey, query, source, status, sorting]);
  const metricKey = workspace.overview.key_metrics[0];
  const objective = secondaryMetric(workspace.overview);
  const data = useMemo(
    () =>
      workspace.runs.filter(
        (run) =>
          (source === 'all' || run.role === source) &&
          (status === 'all' ||
            (status === 'active'
              ? !terminal(run)
              : status === 'violated'
                ? run.verdict === 'constraints_violated'
                : run.status === status)) &&
          `${run.id} ${run.candidate} ${run.owner} ${workspace.experiments.find((item) => item.key === `${run.session}/${run.owner}`)?.title || ''}`
            .toLowerCase()
            .includes(query.toLowerCase()),
      ),
    [workspace.runs, workspace.experiments, source, status, query],
  );
  const columns = useMemo<ColumnDef<Run>[]>(
    () => [
      {
        id: 'select',
        header: () => (
          <input
            aria-label={t('Select visible results')}
            type="checkbox"
            checked={
              data.length > 0 && data.filter(terminal).every((run) => selected.includes(run.key))
            }
            onChange={(event) =>
              setSelected(
                event.target.checked
                  ? data
                      .filter(terminal)
                      .slice(0, 12)
                      .map((run) => run.key)
                  : [],
              )
            }
          />
        ),
        cell: ({ row }) => (
          <input
            type="checkbox"
            aria-label={`${t('Select')} ${row.original.id}`}
            disabled={!terminal(row.original)}
            checked={selected.includes(row.original.key)}
            onClick={(event) => event.stopPropagation()}
            onChange={(event) =>
              setSelected(
                event.target.checked
                  ? [...selected, row.original.key].slice(0, 12)
                  : selected.filter((key) => key !== row.original.key),
              )
            }
          />
        ),
      },
      {
        accessorKey: 'candidate',
        header: t('Configuration'),
        cell: ({ row }) => (
          <div className="run-name">
            <strong>{row.original.candidate}</strong>
            <small>
              {row.original.id} ·{' '}
              {workspace.experiments.find(
                (item) => item.key === `${row.original.session}/${row.original.owner}`,
              )?.title || row.original.owner}
            </small>
          </div>
        ),
      },
      {
        accessorKey: 'role',
        header: t('Source'),
        cell: ({ row }) => (
          <span className={`source-label ${row.original.role}`}>
            {row.original.role === 'hardware' ? <Cpu size={13} /> : <FlaskConical size={13} />}
            {t(row.original.role === 'hardware' ? 'Measurement' : 'Simulation')}
          </span>
        ),
      },
      {
        accessorKey: 'status',
        header: t('Execution'),
        cell: ({ row }) => <Badge status={row.original.status} />,
      },
      {
        accessorKey: 'key_value',
        header: `${label(metricKey)} (${unit(metricKey)})`,
        cell: ({ row }) => <span className="numeric">{number(row.original.key_value)}</span>,
      },
      {
        id: 'objective',
        accessorFn: (row) => metric(row.metrics, objective),
        header: `${label(objective)} (${unit(objective)})`,
        cell: ({ row }) => (
          <span className="numeric">{number(metric(row.original.metrics, objective))}</span>
        ),
      },
      {
        accessorKey: 'verdict',
        header: t('Result'),
        cell: ({ row }) =>
          row.original.verdict ? (
            <Badge status={row.original.verdict} />
          ) : (
            <span className="muted">—</span>
          ),
      },
      {
        accessorKey: 'created_at',
        header: t('Started'),
        cell: ({ row }) => <span className="run-time">{time(row.original.created_at)}</span>,
      },
    ],
    [workspace, selected, data.length],
  );
  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });
  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">{t('YOUR LAB')}</span>
          <h1>
            {t('Run history')} <span className="heading-count">{workspace.runs.length}</span>
          </h1>
          <p>
            {t('Each entry is one simulation or sandbox execution, with its status and results.')}
          </p>
        </div>
        <div className="actions">
          <Button
            onClick={onMeasure}
            disabled={!workspace.experiments.some((item) => item.approved || item.editable)}
          >
            <Cpu size={15} />
            {t('Arrange measurement')}
          </Button>
          {workspace.runs.length > 0 && (
            <Button className="primary" onClick={onDiscuss}>
              <MessageSquare size={15} />
              {t('Ask the assistant')}
            </Button>
          )}
        </div>
      </div>
      {!workspace.runs.length ? (
        <Empty
          title={t('No runs yet')}
          action={
            <div className="actions">
              <Button className="primary" onClick={onDiscuss}>
                <MessageSquare size={16} />
                {t('Ask the assistant')}
              </Button>
              <Button className="ghost" onClick={onCreate}>
                {t('Configure a run')}
              </Button>
            </div>
          }
        >
          {t(
            'Ask a question or request a simulation in conversation. Results will appear here as you explore.',
          )}
        </Empty>
      ) : (
        <>
          <div className="run-toolbar">
            <div className="segmented">
              {[
                ['all', 'All runs'],
                ['simulator', 'Simulation'],
                ['hardware', 'Measurement'],
              ].map(([key, text]) => (
                <button
                  key={key}
                  className={source === key ? 'selected' : ''}
                  onClick={() => setSource(key)}
                >
                  {t(text)}
                  <span>
                    {workspace.runs.filter((run) => key === 'all' || run.role === key).length}
                  </span>
                </button>
              ))}
            </div>
            <div className="filters">
              <div className="search-box">
                <Search size={15} />
                <input
                  aria-label={t('Search runs')}
                  placeholder={t('Search runs or configurations')}
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                />
              </div>
              <select
                aria-label={t('Filter runs')}
                value={status}
                onChange={(event) => setStatus(event.target.value)}
              >
                <option value="all">{t('Any status')}</option>
                <option value="active">{t('In progress')}</option>
                <option value="succeeded">{t('Completed')}</option>
                <option value="violated">{t('Outside constraints')}</option>
                <option value="failed">{t('Run failed')}</option>
                <option value="cancelled">{t('Cancelled')}</option>
              </select>
            </div>
          </div>
          <div className="table-wrap">
            <table className="runs-table">
              <thead>
                {table.getHeaderGroups().map((group) => (
                  <tr key={group.id}>
                    {group.headers.map((header) => (
                      <th
                        key={header.id}
                        aria-sort={
                          header.column.getIsSorted() === 'asc'
                            ? 'ascending'
                            : header.column.getIsSorted() === 'desc'
                              ? 'descending'
                              : undefined
                        }
                      >
                        {header.column.getCanSort() ? (
                          <button
                            className="sort-button"
                            onClick={header.column.getToggleSortingHandler()}
                          >
                            {flexRender(header.column.columnDef.header, header.getContext())}
                            {header.column.getIsSorted()
                              ? header.column.getIsSorted() === 'asc'
                                ? ' ↑'
                                : ' ↓'
                              : ''}
                          </button>
                        ) : (
                          flexRender(header.column.columnDef.header, header.getContext())
                        )}
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              <tbody>
                {table.getRowModel().rows.map((row) => (
                  <tr
                    key={row.original.key}
                    className={selected.includes(row.original.key) ? 'row-selected' : ''}
                    onClick={() => nav(workspace.task, 'runs', row.original.key)}
                  >
                    {row.getVisibleCells().map((cell) => (
                      <td key={cell.id}>
                        {cell.column.id === 'candidate' ? (
                          <button
                            className="row-link"
                            onClick={(event) => {
                              event.stopPropagation();
                              nav(workspace.task, 'runs', row.original.key);
                            }}
                          >
                            {flexRender(cell.column.columnDef.cell, cell.getContext())}
                          </button>
                        ) : (
                          flexRender(cell.column.columnDef.cell, cell.getContext())
                        )}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            {!data.length && (
              <div className="empty-filter">{t('No runs match these filters.')}</div>
            )}
          </div>
          <div className="table-foot">
            <span>{t('{count} runs shown', { count: data.length })}</span>
            <span>{t('Choose completed runs to compare them across experiments.')}</span>
          </div>
          <Tradeoff
            rows={data.filter((run) => run.status === 'succeeded')}
            overview={workspace.overview}
            onSelect={(run) => nav(workspace.task, 'runs', run.key)}
          />
        </>
      )}
      {selected.length > 0 && (
        <div className="selection-bar">
          <span>
            <Check size={15} />
            {t('{count} selected', { count: selected.length })}
          </span>
          <Button
            className="primary small"
            disabled={selected.length < 2}
            onClick={() => nav(workspace.task, 'compare')}
          >
            <GitCompare size={15} />
            {t('Compare results')}
          </Button>
          <button
            className="icon-button"
            aria-label={t('Clear selection')}
            onClick={() => setSelected([])}
          >
            <X size={16} />
          </button>
        </div>
      )}
    </>
  );
}

export function RunDetail({
  run,
  workspace,
  onClone,
  onMeasure,
  onDiscuss,
  onCompare,
  onComparePair,
  reload,
}: {
  run: Run;
  workspace: Workspace;
  onClone: (run: Run) => void;
  onMeasure: (experiment: Experiment, run: Run) => void;
  onDiscuss: (keys: string[]) => void;
  onCompare: (key: string) => void;
  onComparePair: (keys: string[]) => void;
  reload: () => Promise<void>;
}) {
  const [data, setData] = useState<Data | null>(null);
  const [requests, setRequests] = useState<Data | null>(null);
  const [requestOrder, setRequestOrder] = useState('time');
  const [requestId, setRequestId] = useState<string | null>(null);
  const [tab, setTab] = useState('results');
  const [error, setError] = useState('');
  useEffect(() => {
    let current = true;
    api(`/api/sessions/${run.session}/records/${run.id}`)
      .then((value) => {
        if (current) setData(value);
      })
      .catch((error) => setError(error.message));
    if (terminal(run))
      api(`/api/sessions/${run.session}/runs/${run.id}/requests`)
        .then((value) => {
          if (current) setRequests(value);
        })
        .catch((error) => setError(error.message));
    return () => {
      current = false;
    };
  }, [run.key, run.status]);
  const origin = workspace.experiments.find((item) => run.config?.startsWith(item.key + '/'));
  const metricKey = workspace.overview.key_metrics[0];
  const objective = secondaryMetric(workspace.overview);
  const requestRows: Data[] = [...(requests?.rows || [])].sort((a, b) =>
    requestOrder === 'slowest' ? (b.ttft ?? -1) - (a.ttft ?? -1) : a.ready - b.ready,
  );
  const selectedRequest = requestRows.find((item) => item.id === requestId);
  const requestExtent = Math.max(...requestRows.map((row) => row.finish), 1);
  async function cancel() {
    try {
      await act(run.session, 'cancel', { run: run.id });
      await reload();
    } catch (error) {
      setError((error as Error).message);
    }
  }
  return (
    <>
      <div className="page-heading">
        <div>
          <button className="text-button" onClick={() => nav(workspace.task, 'runs')}>
            <ArrowLeft size={14} />
            {t('All runs')}
          </button>
          <h1>
            {run.candidate}
            <span className="record-id">{run.id}</span>
          </h1>
          <p>
            {t(run.role === 'hardware' ? 'Measurement' : 'Simulation')} · {label(run.workload)} ·{' '}
            {time(run.created_at)}
          </p>
        </div>
        <div className="actions">
          <Button onClick={() => setTab('configuration')} disabled={!data?.strategy}>
            {t('View configuration')}
          </Button>
          {origin && (
            <Button onClick={() => onClone(run)} disabled={!data?.strategy}>
              <Copy size={15} />
              {t('Modify and run again')}
            </Button>
          )}
          {origin && (
            <Button onClick={() => onMeasure(origin, run)}>
              <Cpu size={15} />
              {t('Measure this configuration')}
            </Button>
          )}
          <Button onClick={() => onDiscuss([run.key])}>
            <MessageSquare size={15} />
            {t('Discuss')}
          </Button>
          {!terminal(run) && (
            <Button className="danger" onClick={cancel}>
              <Square size={14} />
              {t('Cancel run')}
            </Button>
          )}
        </div>
      </div>
      <ErrorLine text={error} />
      <div className="result-summary">
        <div>
          <span>{t('Execution')}</span>
          <Badge status={run.status} />
        </div>
        <div>
          <span>{label(metricKey)}</span>
          <strong>
            {number(run.key_value)} <small>{unit(metricKey)}</small>
          </strong>
        </div>
        <div>
          <span>{label(objective)}</span>
          <strong>
            {number(metric(run.metrics, objective))} <small>{unit(objective)}</small>
          </strong>
        </div>
        <div>
          <span>{t('Constraint margin')}</span>
          <strong className={run.margin_pct < 0 ? 'bad-text' : ''}>
            {number(run.margin_pct)}
            <small>%</small>
          </strong>
        </div>
      </div>
      <div className="tab-line">
        {[
          ['results', 'Results'],
          ['configuration', 'Configuration'],
          ['requests', 'Requests'],
          ['notes', 'Observations'],
        ].map(([value, text]) => (
          <button
            key={value}
            className={tab === value ? 'selected' : ''}
            onClick={() => setTab(value)}
          >
            {t(text)}
          </button>
        ))}
        <Button
          className="small ghost"
          disabled={!terminal(run)}
          onClick={() => onCompare(run.key)}
        >
          <GitCompare size={14} />
          {t('Add to comparison')}
        </Button>
      </div>
      {data && tab === 'results' && (
        <div className="detail-grid">
          <section className="detail-panel">
            <h2>{t('What the run shows')}</h2>
            {data.verdict && <Badge status={data.verdict} />}
            <table className="detail-table">
              <thead>
                <tr>
                  <th>{t('Metric')}</th>
                  <th>{t('Requirement')}</th>
                  <th>{t('Observed')}</th>
                </tr>
              </thead>
              <tbody>
                {data.constraints?.map((item: Data) => (
                  <tr key={item.metric}>
                    <td>{label(item.metric)}</td>
                    <td>
                      {item.op} {number(item.value)}
                    </td>
                    <td className={item.met === false ? 'bad-text' : ''}>
                      {number(item.observed)} {unit(item.metric)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {data.paired && (
              <div className="paired-notice">
                <strong>{t('Matching simulation')}</strong>
                <button
                  className="text-button"
                  onClick={() =>
                    nav(workspace.task, 'runs', `${run.session}/${data.paired.sim_run}`)
                  }
                >
                  {data.paired.sim_run}
                  <ArrowUpRight size={14} />
                </button>
                <p>
                  {number(data.paired.simulated)} → {number(run.key_value)} {unit(metricKey)}
                </p>
                <Button
                  className="small"
                  onClick={() => {
                    onComparePair([`${run.session}/${data.paired.sim_run}`, run.key]);
                    nav(workspace.task, 'compare');
                  }}
                >
                  {t('Compare this pair')}
                </Button>
              </div>
            )}
            {run.failure && <ErrorLine text={run.failure} />}
          </section>
          <section className="detail-panel">
            <h2>{t('About this result')}</h2>
            <dl>
              <dt>{t('Evidence')}</dt>
              <dd>
                {t(
                  data.evidence_level === 'mock'
                    ? 'Demonstration data'
                    : data.evidence_level || 'Pending',
                )}
              </dd>
              <dt>{t('Experiment plan')}</dt>
              <dd>
                {origin ? (
                  <button
                    className="text-button"
                    onClick={() => nav(workspace.task, 'plans', origin.key)}
                  >
                    {origin.title}
                  </button>
                ) : (
                  run.owner
                )}
              </dd>
              <dt>{t('Seed')}</dt>
              <dd>{data.seed ?? '—'}</dd>
              <dt>{t('Repeat')}</dt>
              <dd>{data.repeat ?? '—'}</dd>
            </dl>
            {data.coverage?.assumptions?.length > 0 && (
              <details>
                <summary>{t('Assumptions')}</summary>
                <ul>
                  {data.coverage.assumptions.map((value: string) => (
                    <li key={value}>{value}</li>
                  ))}
                </ul>
              </details>
            )}
            <details>
              <summary>{t('Execution log')}</summary>
              <pre>{data.logs || t('No log entries yet.')}</pre>
              {data.logs_truncated && (
                <p className="muted">{t('Showing the first 2,000 characters.')}</p>
              )}
              {data.logs && (
                <a href={`/api/sessions/${run.session}/runs/${run.id}/logs`} download>
                  {t('Download complete log')}
                </a>
              )}
            </details>
          </section>
        </div>
      )}
      {data && tab === 'configuration' && (
        <section className="detail-panel">
          <h2>{t('Configuration used for this run')}</h2>
          {data.config_diff?.length > 0 && (
            <div className="warning-note">
              {t('Some settings differed from the requested configuration.')}
              <pre>{JSON.stringify(data.config_diff, null, 2)}</pre>
            </div>
          )}
          <pre className="configuration-json">{JSON.stringify(data.strategy, null, 2)}</pre>
        </section>
      )}
      {tab === 'requests' && (
        <section className="detail-panel">
          <h2>{t('Request timeline')}</h2>
          <p>
            {t(
              'Time to first token includes queueing and prefill. The second segment shows subsequent token generation.',
            )}
          </p>
          {requests?.rows?.length ? (
            <>
              <div className="request-controls">
                <label>
                  {t('Order requests')}
                  <select
                    aria-label={t('Order requests')}
                    value={requestOrder}
                    onChange={(event) => setRequestOrder(event.target.value)}
                  >
                    <option value="time">{t('Arrival order')}</option>
                    <option value="slowest">{t('Slowest first')}</option>
                  </select>
                </label>
                <div className="legend">
                  <span>
                    <i className="request-wait-key" />
                    {t('Time to first token')}
                  </span>
                  <span>
                    <i className="sim-dot" />
                    {t('Token generation')}
                  </span>
                </div>
              </div>
              {selectedRequest && (
                <div className="request-inspector" aria-live="polite">
                  <h3>
                    {t('Request details')}: {selectedRequest.id}
                  </h3>
                  <dl>
                    <div>
                      <dt>{t('Status')}</dt>
                      <dd>{t(selectedRequest.status)}</dd>
                    </div>
                    <div>
                      <dt>{t('Input tokens')}</dt>
                      <dd>{number(selectedRequest.input_tokens, 0)}</dd>
                    </div>
                    <div>
                      <dt>{t('Ready at')}</dt>
                      <dd>{number(selectedRequest.ready)} ms</dd>
                    </div>
                    <div>
                      <dt>{t('First token at')}</dt>
                      <dd>{number(selectedRequest.first)} ms</dd>
                    </div>
                    <div>
                      <dt>{t('Finished at')}</dt>
                      <dd>{number(selectedRequest.finish)} ms</dd>
                    </div>
                    <div>
                      <dt>{t('Time to first token')}</dt>
                      <dd>{number(selectedRequest.ttft)} ms</dd>
                    </div>
                  </dl>
                  {selectedRequest.depends_on?.length > 0 && (
                    <p>
                      {t('Depends on')}: {selectedRequest.depends_on.join(', ')}
                    </p>
                  )}
                  {selectedRequest.critical && (
                    <p>{t('This request is on the latest completion chain.')}</p>
                  )}
                </div>
              )}
              <div className="request-timeline">
                {requestRows.map((item: Data) => {
                  const max = requestExtent;
                  return (
                    <button
                      type="button"
                      aria-pressed={requestId === item.id}
                      onClick={() => setRequestId(item.id)}
                      className={`request-row ${item.critical ? 'critical' : ''}`}
                      key={item.id}
                    >
                      <span>{item.id}</span>
                      <div className="request-lane">
                        {item.ready != null && (item.first ?? item.finish) != null && (
                          <span
                            className="request-wait"
                            style={{
                              left: `${(item.ready / max) * 100}%`,
                              width: `${(Math.max(0, (item.first ?? item.finish) - item.ready) / max) * 100}%`,
                            }}
                          />
                        )}
                        {item.first != null && item.finish != null && (
                          <span
                            className="request-service"
                            style={{
                              left: `${((item.first ?? item.finish) / max) * 100}%`,
                              width: `${(Math.max(0, item.finish - (item.first ?? item.finish)) / max) * 100}%`,
                            }}
                          />
                        )}
                      </div>
                      <strong>{number(item.ttft)} ms</strong>
                    </button>
                  );
                })}
              </div>
              {requests.critical_path?.length > 0 && (
                <p>
                  {t('Latest completion chain')}: {requests.critical_path.join(' → ')}
                  {' · '}
                  {t('Includes waiting; not independent service time.')}
                </p>
              )}
            </>
          ) : (
            <p>{t('Request details will appear when the run finishes.')}</p>
          )}
        </section>
      )}
      {tab === 'notes' && <Notes workspace={workspace} refs={[run.key]} reload={reload} compact />}
    </>
  );
}

export function Comparison({
  workspace,
  selected,
  onDiscuss,
  onClear,
}: {
  workspace: Workspace;
  selected: string[];
  onDiscuss: (keys: string[]) => void;
  onClear: () => void;
}) {
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState('');
  const [baseline, setBaseline] = useState(selected[0]);
  const keys = [baseline, ...selected.filter((key) => key !== baseline)].filter((key) =>
    selected.includes(key),
  );
  useEffect(() => {
    if (selected.length < 2) {
      setData(null);
      return;
    }
    let current = true;
    api('/api/workbench/compare', { runs: keys.length === selected.length ? keys : selected })
      .then((value) => {
        if (current) setData(value);
      })
      .catch((error) => setError(error.message));
    return () => {
      current = false;
    };
  }, [selected.join(','), baseline, workspace.runs.map((run) => run.status).join(',')]);
  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">{t('FOLLOW THE EVIDENCE')}</span>
          <h1>{t('Compare results')}</h1>
          <p>{t('Look at the configuration changes alongside the performance changes.')}</p>
        </div>
        <div className="actions">
          <Button onClick={onClear} disabled={!selected.length}>
            {t('Clear selection')}
          </Button>
          <Button
            className="primary"
            disabled={selected.length < 2}
            onClick={() => onDiscuss(selected)}
          >
            <MessageSquare size={15} />
            {t('Discuss these results')}
          </Button>
        </div>
      </div>
      <ErrorLine text={error} />
      {selected.length < 2 ? (
        <Empty
          title={t('Choose two or more runs')}
          action={
            <Button onClick={() => nav(workspace.task, 'runs')}>
              {t('Browse runs')}
              <ArrowUpRight size={15} />
            </Button>
          }
        >
          {t(
            'Select results from any experiment in this task. Your selection stays with you while you explore.',
          )}
        </Empty>
      ) : (
        data && (
          <>
            <div className="comparison-controls">
              <label>
                {t('Baseline')}
                <select
                  value={baseline || selected[0]}
                  onChange={(event) => setBaseline(event.target.value)}
                >
                  {data.rows.map((row: Run) => (
                    <option key={row.key} value={row.key}>
                      {row.candidate} · {row.id}
                    </option>
                  ))}
                </select>
              </label>
              <span className="muted">
                {t('{count} runs selected', { count: selected.length })}
              </span>
            </div>
            {data.checks.some((check: Data) => !check.same) && (
              <div className="warning-note">
                <strong>{t('Check the comparison conditions')}</strong>
                <p>
                  {t(
                    'These results differ in: {fields}. Account for these differences when interpreting the chart.',
                    {
                      fields: data.checks
                        .filter((check: Data) => !check.same)
                        .map((check: Data) => t(check.field))
                        .join(', '),
                    },
                  )}
                </p>
              </div>
            )}
            <Tradeoff
              rows={data.rows}
              overview={workspace.overview}
              onSelect={(run) => nav(workspace.task, 'runs', run.key)}
            />
            <div className="table-wrap comparison-table">
              <table>
                <thead>
                  <tr>
                    <th>{t('Metric or setting')}</th>
                    {data.rows.map((row: Run) => (
                      <th key={row.key}>
                        <button
                          className="text-button"
                          onClick={() => nav(workspace.task, 'runs', row.key)}
                        >
                          {row.candidate}
                          <ArrowUpRight size={12} />
                        </button>
                        <small>
                          {row.id} · {t(row.role === 'hardware' ? 'Measurement' : 'Simulation')}
                        </small>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {[
                    ...new Set<string>([
                      workspace.overview.key_metrics[0],
                      secondaryMetric(workspace.overview),
                      ...workspace.overview.constraints
                        .filter((item: Data) => item.value !== null)
                        .map((item: Data) => item.metric),
                      'completion_rate',
                    ]),
                  ].map((key) => (
                    <tr key={key}>
                      <td>
                        {label(key)} {unit(key) && `(${unit(key)})`}
                      </td>
                      {data.rows.map((row: Run) => (
                        <td className="numeric" key={row.key}>
                          {number(metric(row.metrics, key), key === 'completion_rate' ? 3 : 1)}
                        </td>
                      ))}
                    </tr>
                  ))}
                  <tr>
                    <td>{t('Constraint margin')}</td>
                    {data.rows.map((row: Run) => (
                      <td
                        key={row.key}
                        className={row.margin_pct < 0 ? 'bad-text numeric' : 'numeric'}
                      >
                        {number(row.margin_pct)}%
                      </td>
                    ))}
                  </tr>
                  {[
                    ...new Set<string>(
                      data.rows.flatMap((row: Data) => row.diff.map((item: Data) => item.field)),
                    ),
                  ].map((field) => (
                    <tr key={field}>
                      <td>{label(field)}</td>
                      {data.rows.map((row: Data, index: number) => {
                        const diff = row.diff.find((item: Data) => item.field === field);
                        const first = data.rows
                          .flatMap((row: Data) => row.diff)
                          .find((item: Data) => item.field === field)?.base;
                        return (
                          <td className={diff ? 'changed-cell' : ''} key={row.key}>
                            {String(
                              index === 0
                                ? (first ?? t('Default'))
                                : (diff?.value ?? first ?? t('Default')),
                            )}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <details className="detail-panel">
              <summary>{t('Comparison conditions')}</summary>
              <table className="detail-table">
                <tbody>
                  {data.checks.map((check: Data) => (
                    <tr key={check.field}>
                      <th>{t(check.field)}</th>
                      {check.values.map((value: string, index: number) => (
                        <td key={index}>{value}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          </>
        )
      )}
    </>
  );
}

export function Notes({
  workspace,
  refs = [],
  reload,
  compact = false,
}: {
  workspace: Workspace;
  refs?: string[];
  reload: () => Promise<void>;
  compact?: boolean;
}) {
  const noteKey = `mlab-note-${workspace.session}-${compact ? refs.join(',') : 'task'}`;
  const [text, setText] = useState(stored(noteKey, ''));
  const currentNote = useRef({ key: noteKey, text });
  currentNote.current = { key: noteKey, text };
  const mounted = useRef(true);
  const previewRequest = useRef(0);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      ++previewRequest.current;
    };
  }, []);
  useEffect(() => {
    store(noteKey, text);
  }, [noteKey, text]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [conclusion, setConclusion] = useState(false);
  const [selection, setSelection] = useState('');
  const [preview, setPreview] = useState<Data | null>(null);
  const notes = workspace.notes
    .filter(
      (note) => !compact || !refs.length || note.refs.some((ref: string) => refs.includes(ref)),
    )
    .slice()
    .reverse();
  async function save() {
    if (busy || !text.trim()) return;
    setBusy(true);
    setError('');
    try {
      await act(workspace.session, 'note', { text, refs });
      if (
        mounted.current &&
        currentNote.current.key === noteKey &&
        currentNote.current.text === text
      )
        setText('');
      await reload();
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function choose(value: string) {
    const request = ++previewRequest.current;
    setSelection(value);
    setPreview(null);
    setError('');
    if (!value) return;
    const [session, ...parts] = value.split('/');
    try {
      const preview = await api(
        `/api/sessions/${session}/preview/conclude?candidate=${encodeURIComponent(parts.join('/'))}`,
      );
      if (request === previewRequest.current) setPreview(preview);
    } catch (error) {
      if (request === previewRequest.current) setError((error as Error).message);
    }
  }
  function closeConclusion() {
    ++previewRequest.current;
    setConclusion(false);
    setSelection('');
    setPreview(null);
  }
  async function submit() {
    setBusy(true);
    const [session, ...parts] = selection.split('/');
    try {
      await act(session, 'conclude', { candidate: parts.join('/'), confirm: true });
      await reload();
      closeConclusion();
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      {!compact && (
        <div className="page-heading">
          <div>
            <span className="eyebrow">{t('YOUR RESEARCH RECORD')}</span>
            <h1>{t('Notes & conclusions')}</h1>
            <p>
              {t('Keep the observations that changed your mind, with the evidence behind them.')}
            </p>
          </div>
          <Button onClick={() => setConclusion(true)}>
            {t('Submit conclusion')}
            <ArrowUpRight size={15} />
          </Button>
        </div>
      )}
      <ErrorLine text={error} />
      <div className="note-composer">
        <label>
          {t('Your observation')}
          <textarea
            rows={4}
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder={t('What did you learn? What would you try next?')}
          />
        </label>
        {refs.length > 0 && (
          <div className="reference-chips">
            {refs.map((ref) => (
              <span key={ref}>{ref}</span>
            ))}
          </div>
        )}
        <div className="actions">
          <Button className="primary" busy={busy} disabled={!text.trim()} onClick={save}>
            {t('Save observation')}
          </Button>
        </div>
      </div>
      <div className="notes-list">
        {notes.map((note) => (
          <article className="research-note" key={note.id}>
            <div className="note-meta">
              <span>{t('Your observation')}</span>
              <time>{time(note.at)}</time>
            </div>
            <ReactMarkdown>{note.text}</ReactMarkdown>
            <div className="reference-chips">
              {note.refs.map((ref: string) => (
                <button key={ref} onClick={() => openReference(ref)}>
                  {ref}
                  <ArrowUpRight size={12} />
                </button>
              ))}
            </div>
          </article>
        ))}
      </div>
      {!compact && workspace.conclusions.length > 0 && (
        <section className="detail-panel">
          <h2>{t('Submitted conclusions')}</h2>
          {workspace.conclusions
            .slice()
            .reverse()
            .map((item: Data) => (
              <article className="conclusion-row" key={item.session + item.id}>
                <span>
                  <strong>{item.selection.candidate}</strong>
                  <small>{time(item.at)}</small>
                </span>
                <span className="badge">
                  {t(
                    item.conclusion === 'mock_only'
                      ? 'Demonstration evidence'
                      : item.conclusion === 'unresolved'
                        ? 'Needs more evidence'
                        : item.conclusion,
                  )}
                </span>
                <p>{item.reason}</p>
                <a href={`#/${item.session}/report`}>
                  {t('View report')}
                  <ArrowUpRight size={12} />
                </a>
              </article>
            ))}
        </section>
      )}
      <Modal
        open={conclusion}
        onClose={closeConclusion}
        title={t('Submit a conclusion')}
        description={t('Choose a configuration and review the evidence supporting it.')}
      >
        <label>
          {t('Configuration')}
          <select
            value={selection}
            disabled={busy}
            onChange={(event) => choose(event.target.value)}
          >
            <option value="">{t('Choose a configuration')}</option>
            {workspace.experiments
              .filter((item) => item.approved)
              .flatMap((item) =>
                item.candidates.map((candidate) => (
                  <option value={`${item.key}/${candidate.id}`} key={`${item.key}/${candidate.id}`}>
                    {item.title} · {candidate.id}
                  </option>
                )),
              )}
          </select>
        </label>
        {preview && (
          <div className="review-summary">
            {preview.facts?.map(([key, value]: [string, any]) => (
              <p key={key}>
                <strong>{t(key)}</strong>:{' '}
                {typeof value === 'object' ? JSON.stringify(value) : t(String(value))}
              </p>
            ))}
            {preview.will_fail && <ErrorLine text={preview.will_fail} />}
          </div>
        )}
        <ErrorLine text={error} />
        <div className="dialog-actions">
          <Button onClick={closeConclusion}>{t('Close')}</Button>
          <Button
            className="primary"
            busy={busy}
            disabled={!selection || !preview || !!preview.will_fail}
            onClick={submit}
          >
            {t('Submit conclusion')}
          </Button>
        </div>
      </Modal>
    </>
  );
}
