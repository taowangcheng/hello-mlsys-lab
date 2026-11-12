import {
  Children,
  cloneElement,
  isValidElement,
  useEffect,
  useMemo,
  useState,
  type ReactElement,
  type ReactNode,
} from 'react';
import ReactMarkdown from 'react-markdown';
import { ArrowUpRight, Download, MessageSquare, RefreshCw } from 'lucide-react';
import {
  act,
  api,
  label,
  number,
  stored,
  store,
  t,
  unit,
  type Data,
  type Experiment,
  type Workspace,
} from './api';
import { Badge, Button, ErrorLine, Tradeoff } from './ui';

type Props = {
  workspace: Workspace;
  reference: string;
  onDiscuss: (keys: string[]) => void;
  onOpen: (key: string) => void;
  reload: () => void;
};
type OpenRecord = (id: string) => void;
const titles: Record<string, string> = {
  not_evaluated: 'Not run',
  mock_only: 'Demonstration evidence',
  unresolved: 'Needs more evidence',
  insufficient_evidence: 'Needs more evidence',
  validated: 'Validated',
};
function outcome(value: string | null | undefined) {
  return value ? t(titles[value] || value.replaceAll('_', ' ')) : t('No conclusion yet');
}
function RecordLink({
  id,
  open,
  children,
}: {
  id: string;
  open: OpenRecord;
  children?: ReactNode;
}) {
  return (
    <button className="text-button" onClick={() => open(id)}>
      {children || id}
      <ArrowUpRight size={12} />
    </button>
  );
}
function signed(value: unknown) {
  return typeof value === 'number' ? `${value > 0 ? '+' : ''}${number(value)}%` : '—';
}
function shift(value: string | null) {
  return t(value === 'all' ? 'All requests' : value === 'tail' ? 'Tail only' : 'No clear shift');
}
function interval(value: unknown) {
  return Array.isArray(value) && value.length === 2
    ? `${number(value[0])}–${number(value[1])}`
    : '—';
}

export function ValidationMatrix({ matrix = {}, holdouts }: { matrix?: Data; holdouts: string[] }) {
  function cell(value: unknown) {
    return (
      <span className={value === false ? 'bad-text' : ''}>
        {t(value === true ? 'Met' : value === false ? 'Violated' : 'Not run')}
      </span>
    );
  }
  return (
    <div className="table-wrap record-validation">
      <table className="detail-table">
        <thead>
          <tr>
            <th>{t('Evidence')}</th>
            <th>{t('Development')}</th>
            <th>{t('Repeat')}</th>
            {holdouts.map((name) => (
              <th key={name}>{label(name)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {['simulator', 'hardware'].map((role) => (
            <tr key={role}>
              <th>{t(role === 'hardware' ? 'Measurement' : 'Simulation')}</th>
              <td>{cell(matrix[role]?.development)}</td>
              <td>{cell(matrix[role]?.repeat)}</td>
              {holdouts.map((name) => (
                <td key={name}>{cell(matrix[role]?.holdout?.[name])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted">
        {t(
          'A completed run can still violate a constraint. Missing checks remain visible; they do not prevent further exploration.',
        )}
      </p>
    </div>
  );
}

function PairedChart({ chart, open }: { chart: Data; open: OpenRecord }) {
  const bars = (chart.bars || []).filter((row: Data) =>
    [row.simulated, row.hardware, row.value].some((value) => typeof value === 'number'),
  );
  if (!bars.length) return null;
  const paired = chart.kind === 'paired';
  const maximum =
    Math.max(
      1,
      chart.threshold?.value || 0,
      ...bars.flatMap((row: Data) => [
        row.simulated || 0,
        row.hardware || 0,
        row.value || 0,
        row.interval?.[1] || 0,
      ]),
    ) * 1.08;
  const left = 185;
  const width = 530;
  const x = (value: number) => left + (value / maximum) * width;
  const height = 42 + bars.length * 58;
  return (
    <section className="plot record-paired-chart">
      <div className="plot-heading">
        <h3>{t(paired ? 'Prediction and measurement' : 'Simulation results')}</h3>
        <span>
          {label(chart.metric)} · {unit(chart.metric)}
        </span>
      </div>
      {paired && (
        <div className="legend">
          <span>
            <i className="sim-dot" />
            {t('Simulation')}
          </span>
          <span>
            <i className="hw-dot" />
            {t('Measurement')}
          </span>
        </div>
      )}
      <svg
        viewBox={`0 0 800 ${height}`}
        role="img"
        aria-label={t(paired ? 'Prediction and measurement' : 'Simulation results')}
      >
        {chart.threshold?.value != null && (
          <line
            x1={x(chart.threshold.value)}
            x2={x(chart.threshold.value)}
            y1="10"
            y2={height - 25}
            className="threshold"
          />
        )}
        {bars.map((row: Data, index: number) => {
          const y = 16 + index * 58;
          const values: [number | undefined, string][] = paired
            ? [
                [row.simulated, 'Simulation'],
                [row.hardware, 'Measurement'],
              ]
            : [[row.value, 'Simulation']];
          return (
            <g
              key={`${row.run}-${index}`}
              role="button"
              tabIndex={0}
              aria-label={`${row.run}: ${row.label}`}
              onClick={() => open(row.run)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  open(row.run);
                }
              }}
            >
              <text x="8" y={y + 14}>
                {row.run}
              </text>
              <text x="8" y={y + 31}>
                {row.candidate || row.label}
              </text>
              {values.map(
                ([value, name], offset) =>
                  typeof value === 'number' && (
                    <g key={name}>
                      <rect
                        x={left}
                        y={y + offset * 18}
                        width={Math.max(1, x(value) - left)}
                        height="12"
                        rx="3"
                        fill={name === 'Measurement' ? 'var(--accent)' : 'var(--accent-soft)'}
                      />
                      <text x={x(value) + 7} y={y + 11 + offset * 18}>
                        {number(value)}
                      </text>
                      <title>
                        {t(name)}: {number(value)} {unit(chart.metric)}
                      </title>
                    </g>
                  ),
              )}
              {paired && Array.isArray(row.interval) && (
                <line
                  x1={x(row.interval[0])}
                  x2={x(row.interval[1])}
                  y1={y + 24}
                  y2={y + 24}
                  stroke="var(--ink)"
                  strokeWidth="2"
                />
              )}
            </g>
          );
        })}
        <text x={left} y={height - 5}>
          0
        </text>
        <text x={left + width} y={height - 5} textAnchor="end">
          {number(maximum)} {unit(chart.metric)}
        </text>
      </svg>
    </section>
  );
}

function RequestRatios({
  session,
  measurement,
  run,
  open,
}: {
  session: string;
  measurement: string;
  run: string;
  open: OpenRecord;
}) {
  const [result, setResult] = useState<{ key: string; data?: Data; error?: string }>();
  const key = `${session}/${measurement}/${run}`;
  useEffect(() => {
    let current = true;
    api(
      `/api/sessions/${encodeURIComponent(session)}/measurements/${encodeURIComponent(measurement)}/pairs?run=${encodeURIComponent(run)}`,
    )
      .then((data) => {
        if (current) setResult({ key, data });
      })
      .catch((error) => {
        if (current) setResult({ key, error: error.message });
      });
    return () => {
      current = false;
    };
  }, [key]);
  if (result?.key !== key) return <p role="status">{t('Loading per-request data…')}</p>;
  if (result.error) return <ErrorLine text={result.error} />;
  const data = result.data!;
  const pairs: Data[] = data.pairs || [];
  const max = Math.max(1.2, ...pairs.map((pair) => pair.ratio)) * 1.08;
  const y = (ratio: number) => 200 - (ratio / max) * 165;
  return (
    <section className="detail-panel record-request-ratios">
      <h3>{t('Per-request ratios')}</h3>
      <p>
        <RecordLink id={run} open={open} /> / <RecordLink id={data.sim_run} open={open} /> ·{' '}
        {shift(data.shift)}
      </p>
      <p>
        {t(
          'Each point divides measured first-token latency by its matching simulation. Requests are ordered from slowest measured latency to fastest; 1 means equal latency.',
        )}
      </p>
      <svg
        className="plot"
        viewBox="0 0 760 230"
        role="img"
        aria-label={t('Measured to simulated latency ratio')}
      >
        <line x1="45" x2="735" y1={y(1)} y2={y(1)} className="threshold" />
        <text x="8" y={y(1) + 4}>
          1×
        </text>
        {pairs.map((pair, index) => (
          <circle
            key={pair.id}
            cx={60 + (index * 655) / Math.max(1, pairs.length - 1)}
            cy={y(pair.ratio)}
            r={pair.slowest ? 6 : 4}
            fill={pair.slowest ? 'var(--orange)' : 'var(--accent)'}
          >
            <title>
              {pair.id}: {number(pair.ratio, 3)}×
            </title>
          </circle>
        ))}
        <text x="45" y="222">
          {t('Slowest requests first')}
        </text>
      </svg>
      <p>
        {t('Median ratio')}: {number(data.per_request_ratio?.median, 3)}× · {t('Slowest group')}:{' '}
        {number(data.slowest_ratio?.median, 3)}×
      </p>
      <div className="table-wrap">
        <table className="detail-table">
          <thead>
            <tr>
              <th>{t('Request')}</th>
              <th>{t('Simulation')} (ms)</th>
              <th>{t('Measurement')} (ms)</th>
              <th>{t('Ratio')}</th>
              <th>{t('Group')}</th>
            </tr>
          </thead>
          <tbody>
            {pairs.map((pair) => (
              <tr key={pair.id}>
                <td>{pair.id}</td>
                <td className="numeric">{number(pair.sim)}</td>
                <td className="numeric">{number(pair.hw)}</td>
                <td className="numeric">{number(pair.ratio, 3)}×</td>
                <td>{pair.slowest ? t('Slowest group') : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function ConfigRecord({
  data,
  workspace,
  open,
}: {
  data: Data;
  workspace: Workspace;
  open: OpenRecord;
}) {
  const ids = new Set(data.runs.map((run: Data) => run.run));
  const rows = workspace.runs.filter((run) => run.session === workspace.session && ids.has(run.id));
  const experiment = workspace.experiments.find(
    (item) => item.session === workspace.session && item.id === data.workflow,
  );
  const candidate = experiment?.candidates.find((item) => item.id === data.candidate);
  return (
    <>
      <section className="detail-panel">
        <h2>{t('Configuration evidence')}</h2>
        <p>{outcome(data.preview)}</p>
        <p>{t(data.reason || '')}</p>
        <p>
          {t('Appears in')}:{' '}
          {(data.names || []).map((name: string) => (
            <RecordLink key={name} id={name} open={open} />
          ))}
        </p>
      </section>
      <section className="detail-panel">
        <h2>{t('Validation matrix')}</h2>
        <ValidationMatrix matrix={data.validation} holdouts={workspace.overview.holdouts} />
      </section>
      <PairedChart chart={data.chart} open={open} />
      <Tradeoff rows={rows} overview={workspace.overview} onSelect={(run) => open(run.id)} />
      {['simulator', 'hardware'].map((role) => (
        <section className="detail-panel" key={role}>
          <h2>{t(role === 'hardware' ? 'Measurements' : 'Simulations')}</h2>
          <div className="table-wrap">
            <table className="detail-table">
              <thead>
                <tr>
                  <th>{t('Run')}</th>
                  <th>{t('Experiment or measurement')}</th>
                  <th>{t('Workload')}</th>
                  <th>{t('Status')}</th>
                  <th>{t('Constraints')}</th>
                  <th>
                    {label(data.metric)} ({unit(data.metric)})
                  </th>
                  {role === 'hardware' && (
                    <>
                      <th>{t('Matching simulation')}</th>
                      <th>{t('Gap')}</th>
                      <th>{t('Shift')}</th>
                    </>
                  )}
                </tr>
              </thead>
              <tbody>
                {data.runs
                  .filter((run: Data) => run.role === role)
                  .map((run: Data) => (
                    <tr key={run.run}>
                      <td>
                        <RecordLink id={run.run} open={open} />
                      </td>
                      <td>
                        <RecordLink id={run.owner} open={open} />
                      </td>
                      <td>{label(run.holdout || run.split)}</td>
                      <td>
                        <Badge status={run.status} />
                      </td>
                      <td>
                        <Badge status={run.verdict || 'not_evaluated'} />
                      </td>
                      <td className="numeric">{number(run.value)}</td>
                      {role === 'hardware' && (
                        <>
                          <td>
                            {run.sim_run ? (
                              <RecordLink id={run.sim_run} open={open}>
                                {run.sim_run} · {number(run.simulated)}
                              </RecordLink>
                            ) : (
                              '—'
                            )}
                          </td>
                          <td>{signed(run.gap_pct)}</td>
                          <td>{shift(run.shift)}</td>
                        </>
                      )}
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
          {!data.runs.some((run: Data) => run.role === role) && (
            <p>{t(role === 'hardware' ? 'No measurements yet.' : 'No simulations yet.')}</p>
          )}
        </section>
      ))}
      {candidate && (
        <details className="detail-panel">
          <summary>{t('Saved configuration')}</summary>
          <pre className="configuration-json">{JSON.stringify(candidate, null, 2)}</pre>
        </details>
      )}
    </>
  );
}

function MeasurementRecord({
  data,
  session,
  open,
}: {
  data: Data;
  session: string;
  open: OpenRecord;
}) {
  const [selected, setSelected] = useState('');
  const alignment = data.alignment;
  const rows: Data[] = alignment?.rows || [];
  const predictions: Data[] = data.predictions || data.draft?.rows || [];
  const groups = useMemo(() => {
    const result = new Map<string, Data[]>();
    for (const row of rows) {
      const key = `${row.workflow}/${row.candidate}/${row.workload}`;
      result.set(key, [...(result.get(key) || []), row]);
    }
    return [...result.values()];
  }, [rows]);
  return (
    <>
      <section className="detail-panel">
        <h2>{t('Measurement plan')}</h2>
        <Badge status={data.stage || data.status} />
        <p>
          {t('Hardware budget')}: {data.budget.remaining} / {data.budget.total} {t('available')}
        </p>
        <ErrorLine text={data.points_error || data.draft?.error || ''} />
        <div className="table-wrap">
          <table className="detail-table">
            <thead>
              <tr>
                <th>{t('Configuration')}</th>
                <th>{t('Workload')}</th>
                <th>{t('Repetitions')}</th>
                <th>{t('Reason')}</th>
              </tr>
            </thead>
            <tbody>
              {data.points.map((point: Data, index: number) => (
                <tr key={index}>
                  <td>
                    {point.workflow ? (
                      <RecordLink id={`${point.workflow}/${point.candidate}`} open={open}>
                        {point.candidate}
                      </RecordLink>
                    ) : (
                      point.candidate
                    )}
                  </td>
                  <td>{label(point.workload)}</td>
                  <td>{point.runs}</td>
                  <td>{point.rationale || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <section className="detail-panel">
        <h2>{t('Predictions before measurement')}</h2>
        <div className="table-wrap">
          <table className="detail-table">
            <thead>
              <tr>
                <th>{t('Configuration')}</th>
                <th>{t('Workload')}</th>
                <th>{t('Matching simulation')}</th>
                <th>
                  {label(data.chart.metric)} ({unit(data.chart.metric)})
                </th>
                <th>{t('Predicted margin')}</th>
              </tr>
            </thead>
            <tbody>
              {predictions.map((point, index) => (
                <tr key={index}>
                  <td>{point.candidate}</td>
                  <td>{label(point.workload)}</td>
                  <td>
                    {point.sim_run ? <RecordLink id={point.sim_run} open={open} /> : t('Not run')}
                  </td>
                  <td>{number(point.simulated)}</td>
                  <td>{signed(point.sim_margin_pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!predictions.length && <p>{t('No matching predictions yet.')}</p>}
      </section>
      <PairedChart chart={data.chart} open={open} />
      {groups.map((group) => (
        <section
          className="detail-panel measurement-group"
          key={`${group[0].workflow}/${group[0].candidate}/${group[0].workload}`}
        >
          <h3>
            {group[0].candidate} · {label(group[0].workload)}
          </h3>
          <div className="table-wrap">
            <table className="detail-table">
              <thead>
                <tr>
                  <th>{t('Measured run')}</th>
                  <th>{t('Simulation')}</th>
                  <th>{t('Measurement')}</th>
                  <th>{t('Gap')}</th>
                  <th>{t('Within-run interval')}</th>
                  <th>{t('Repeated measurements')}</th>
                  <th>{t('Shift')}</th>
                </tr>
              </thead>
              <tbody>
                {group.map((row) => (
                  <tr key={row.run_id}>
                    <td>
                      <RecordLink id={row.run_id} open={open} />
                    </td>
                    <td>
                      <RecordLink id={row.sim_run} open={open}>
                        {number(row.simulated)}
                      </RecordLink>
                    </td>
                    <td>{number(row.hardware)}</td>
                    <td>{signed(row.gap_pct)}</td>
                    <td>{interval(row.hardware_interval)}</td>
                    <td>
                      {row.repeat_values.map((value: number) => number(value)).join(', ') || '—'}
                    </td>
                    <td>{shift(row.shift)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="table-wrap">
            <table className="detail-table">
              <thead>
                <tr>
                  <th>{t('Run')}</th>
                  <th>{t('Simulated verdict')}</th>
                  <th>{t('Measured verdict')}</th>
                  <th>{t('Simulated margin')}</th>
                  <th>{t('Measured margin')}</th>
                  <th>{t('Effective configuration differences')}</th>
                </tr>
              </thead>
              <tbody>
                {group.map((row) => (
                  <tr key={row.run_id}>
                    <td>{row.run_id}</td>
                    <td>
                      <Badge status={row.sim_verdict} />
                    </td>
                    <td>
                      <Badge status={row.hardware_verdict} />
                      {row.verdict_flip && (
                        <span className="bad-text"> · {t('Verdict changed')}</span>
                      )}
                    </td>
                    <td>{signed(row.sim_margin_pct)}</td>
                    <td>{signed(row.hardware_margin_pct)}</td>
                    <td>{row.config_diff.length ? row.config_diff.join('; ') : t('None')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ))}
      {alignment && (
        <section className="detail-panel">
          <h2>{t('Interpreting the difference')}</h2>
          <p>
            {t(
              'Within-run intervals resample requests from one execution. They do not measure variation between repeated executions.',
            )}
          </p>
          <p>
            {t(
              'A shift across many requests suggests a different pattern from a shift confined to the slowest requests. Neither pattern alone proves a cause.',
            )}
          </p>
          {alignment.ranking.simulated.length > 1 && (
            <>
              <p>
                {t('Order by increasing value')} · {t('Simulation')}:{' '}
                {alignment.ranking.simulated.join(' < ')} · {t('Measurement')}:{' '}
                {alignment.ranking.hardware.join(' < ')}
              </p>
              {alignment.rank_flip && (
                <p className="bad-text">
                  {t('The order changed between simulation and measurement.')}
                </p>
              )}
            </>
          )}
          {alignment.pending.length > 0 && (
            <p>
              {t('Still running')}: {alignment.pending.join(', ')}
            </p>
          )}
          {rows.length > 0 && (
            <label>
              {t('Inspect request ratios')}
              <select
                aria-label={t('Inspect request ratios')}
                value={selected}
                onChange={(event) => setSelected(event.target.value)}
              >
                <option value="">{t('Choose a measured run')}</option>
                {rows.map((row) => (
                  <option value={row.run_id} key={row.run_id}>
                    {row.run_id} · {row.candidate} · {label(row.workload)}
                  </option>
                ))}
              </select>
            </label>
          )}
        </section>
      )}
      {selected && rows.some((row) => row.run_id === selected) && (
        <RequestRatios session={session} measurement={data.id} run={selected} open={open} />
      )}
      {!rows.length && (
        <section className="detail-panel">
          <h2>{t('Measurement results')}</h2>
          <p>
            {t(
              'No completed measurements yet. The plan and predictions remain available while you explore.',
            )}
          </p>
        </section>
      )}
    </>
  );
}

function ReportMarkdown({
  text,
  allowed,
  open,
}: {
  text: string;
  allowed: Set<string>;
  open: OpenRecord;
}) {
  function linkText(value: string): ReactNode[] {
    return value
      .split(/(\br\d{3}(?:\/[A-Za-z0-9_-]+)?\b|\bm\d{3}\b|\b(?:sim|hw)_\d+\b)/g)
      .map((part, index) =>
        allowed.has(part) ? <RecordLink key={index} id={part} open={open} /> : part,
      );
  }
  function children(value: ReactNode): ReactNode {
    return Children.map(value, (child) => {
      if (typeof child === 'string') return linkText(child);
      if (!isValidElement(child) || child.type === 'a') return child;
      const element = child as ReactElement<{ children?: ReactNode }>;
      return cloneElement(element, {}, children(element.props.children));
    });
  }
  const markdown = (value: string) => (
    <ReactMarkdown
      components={{
        p: ({ children: value }) => <p>{children(value)}</p>,
        li: ({ children: value }) => <li>{children(value)}</li>,
        a: ({ href, children: value }) => (
          <a href={href} target="_blank" rel="noreferrer">
            {value}
          </a>
        ),
      }}
    >
      {value}
    </ReactMarkdown>
  );
  // The report format uses simple pipe tables. Keep all cells visible without raw HTML.
  const lines = text.split('\n');
  const blocks: ReactNode[] = [];
  let prose: string[] = [];
  const cells = (line: string) =>
    line
      .trim()
      .replace(/^\|/, '')
      .replace(/\|$/, '')
      .split(/(?<!\\)\|/)
      .map((cell) => cell.trim().replaceAll('\\|', '|'));
  const flush = () => {
    if (prose.length) {
      blocks.push(<div key={`text-${blocks.length}`}>{markdown(prose.join('\n'))}</div>);
      prose = [];
    }
  };
  for (let index = 0; index < lines.length; index++) {
    if (lines[index].trim().startsWith('|') && /^\s*\|?\s*:?-{3,}/.test(lines[index + 1] || '')) {
      flush();
      const headings = cells(lines[index]);
      const rows: string[][] = [];
      index += 2;
      while (index < lines.length && lines[index].trim().startsWith('|'))
        rows.push(cells(lines[index++]));
      index--;
      blocks.push(
        <div className="table-wrap" key={`table-${blocks.length}`}>
          <table className="detail-table">
            <thead>
              <tr>
                {headings.map((heading, cell) => (
                  <th key={cell}>{heading}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr key={i}>
                  {row.map((cell, j) => (
                    <td key={j}>{markdown(cell)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
    } else prose.push(lines[index]);
  }
  flush();
  return <div className="record-report">{blocks}</div>;
}

export function RecordView({ workspace, reference, onDiscuss, onOpen, reload }: Props) {
  const qualified = reference.match(/^(\d\.\d-\d+)\/(.+)$/);
  const session = workspace.session;
  const id = qualified ? qualified[2] : reference;
  const valid = !qualified || qualified[1] === session;
  const key = `${session}/${id}`;
  const [revision, setRevision] = useState(0);
  const [result, setResult] = useState<{
    key: string;
    record?: Data;
    notebook?: Data;
    error?: string;
  }>();
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState('');
  const updates =
    workspace.runs
      .filter((run) => run.session === session)
      .map((run) => `${run.id}:${run.status}`)
      .join(',') +
    workspace.experiments
      .filter((experiment) => experiment.session === session)
      .map((experiment) => `${experiment.id}:${experiment.revision}`)
      .join(',') +
    workspace.measurements
      .filter((measurement) => measurement.session === session)
      .map((measurement) => `${measurement.id}:${measurement.status}:${measurement.revision}`)
      .join(',') +
    workspace.notes.length +
    workspace.conclusions.length;
  useEffect(() => {
    let current = true;
    if (!valid) return;
    const path = id.split('/').map(encodeURIComponent).join('/');
    Promise.all([
      api(`/api/sessions/${encodeURIComponent(session)}/records/${path}`),
      id === 'report'
        ? api(`/api/sessions/${encodeURIComponent(session)}/notebook`)
        : Promise.resolve(undefined),
    ])
      .then(([record, notebook]) => {
        if (current) setResult({ key, record, notebook });
      })
      .catch((error) => {
        if (current) setResult({ key, error: error.message });
      });
    return () => {
      current = false;
    };
  }, [key, valid, revision, updates]);
  function open(record: string) {
    onOpen(`${session}/${record}`);
  }
  async function saveReport() {
    setSaving(true);
    setSaveError('');
    try {
      await act(session, 'report', {});
      reload();
      setRevision((value) => value + 1);
    } catch (error) {
      setSaveError((error as Error).message);
    } finally {
      setSaving(false);
    }
  }
  if (!valid)
    return (
      <ErrorLine
        text={t('This record belongs to another session. Open that session to inspect it.')}
      />
    );
  if (result?.key !== key) return <p role="status">{t('Loading evidence…')}</p>;
  if (result.error)
    return (
      <>
        <ErrorLine text={result.error} />
        <Button onClick={() => setRevision((value) => value + 1)}>{t('Try again')}</Button>
      </>
    );
  const data = result.record!;
  const notebook = result.notebook;
  const title =
    data.kind === 'config'
      ? data.candidate
      : data.kind === 'measurement'
        ? `${t('Measurement')} ${data.id}`
        : t('Report');
  const allowed = new Set<string>([
    ...workspace.runs.filter((run) => run.session === session).map((run) => run.id),
    ...workspace.experiments
      .filter((item) => item.session === session)
      .flatMap((item) => [
        item.id,
        ...item.candidates.map((candidate) => `${item.id}/${candidate.id}`),
      ]),
    ...workspace.measurements.filter((item) => item.session === session).map((item) => item.id),
  ]);
  return (
    <div className="records-view">
      <div className="page-heading">
        <div>
          <span className="eyebrow">{t('EVIDENCE AND REASONING')}</span>
          <h1>{title}</h1>
          <p>
            {id === 'report'
              ? t('The current evidence, your reasoning and the questions still open.')
              : key}
          </p>
        </div>
        <div className="actions">
          <Button
            onClick={() => {
              reload();
              setRevision((value) => value + 1);
            }}
          >
            <RefreshCw size={14} />
            {t('Refresh')}
          </Button>
          <Button onClick={() => onDiscuss([key])}>
            <MessageSquare size={14} />
            {t('Discuss this')}
          </Button>
        </div>
      </div>
      {workspace.overview.backends?.hardware === 'standin' && (
        <div className="data-notice">
          {t(
            'These measurements use demonstration data. They do not establish real GPU performance.',
          )}
        </div>
      )}
      {data.kind === 'config' && <ConfigRecord data={data} workspace={workspace} open={open} />}
      {data.kind === 'measurement' && (
        <MeasurementRecord key={key} data={data} session={session} open={open} />
      )}
      {data.kind === 'report' && (
        <>
          <section className="detail-panel">
            <h2>{t('Evidence summary')}</h2>
            <p>{outcome(data.conclusion)}</p>
            <p>
              {t(
                'You can review or save a report at any time. Unfinished checks and unanswered questions remain part of the record.',
              )}
            </p>
            <Button busy={saving} onClick={saveReport}>
              <Download size={14} />
              {t(data.written ? 'Update saved report' : 'Save report')}
            </Button>
            <ErrorLine text={saveError} />
          </section>
          {notebook?.validation ? (
            <section className="detail-panel">
              <h2>{t('Validation matrix')}</h2>
              <p>
                {notebook.validation.config && (
                  <RecordLink id={notebook.validation.config} open={open} />
                )}
              </p>
              <ValidationMatrix
                matrix={notebook.validation.matrix}
                holdouts={workspace.overview.holdouts}
              />
            </section>
          ) : (
            <section className="detail-panel">
              <h2>{t('Validation')}</h2>
              <p>
                {t(
                  'No configuration has been selected for a conclusion. You can continue exploring and report what is known so far.',
                )}
              </p>
            </section>
          )}
          <section className="detail-panel">
            <ReportMarkdown text={data.markdown} allowed={allowed} open={open} />
          </section>
          <section className="detail-panel">
            <h2>{t('Linked evidence')}</h2>
            <div className="reference-chips">
              {[...allowed].map((record) => (
                <RecordLink key={record} id={record} open={open} />
              ))}
            </div>
            {!allowed.size && <p>{t('No experiment records yet.')}</p>}
          </section>
        </>
      )}
    </div>
  );
}

export function ExperimentReflection({
  workspace,
  experiment,
  reload,
  onDiscuss,
}: {
  workspace: Workspace;
  experiment: Experiment;
  reload: () => void;
  onDiscuss: (keys: string[]) => void;
}) {
  const key = `mlab-note-reflection-${experiment.key}`;
  const [text, setText] = useState(() => stored(key, ''));
  const [data, setData] = useState<{ key: string; record?: Data; error?: string }>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    setText(stored(key, ''));
    let current = true;
    if (experiment.session === workspace.session)
      api(
        `/api/sessions/${encodeURIComponent(workspace.session)}/records/${encodeURIComponent(experiment.id)}`,
      )
        .then((record) => {
          if (current) setData({ key, record });
        })
        .catch((problem) => {
          if (current) setData({ key, error: problem.message });
        });
    return () => {
      current = false;
    };
  }, [key, experiment.revision, workspace.session]);
  if (experiment.session !== workspace.session) return null;
  const record = data?.key === key ? data.record : undefined;
  const sections = [
    ['Hypothesis', record?.hypothesis?.claim],
    ['Alternative explanation', record?.hypothesis?.alternative],
    ['Falsification condition', record?.hypothesis?.falsifier],
    ['Plan comments', record?.review?.student],
    ['Changes and responses', record?.review?.agent],
    ['Analysis', record?.analysis],
    ['Decision and reasons', record?.decision?.text],
  ].filter(([, value]) => typeof value === 'string' && value.trim());
  const notes = workspace.notes.filter(
    (note) => note.session === workspace.session && note.refs?.includes(experiment.key),
  );
  async function save() {
    if (!text.trim()) return;
    setBusy(true);
    setError('');
    try {
      await act(workspace.session, 'note', { text: text.trim(), refs: [experiment.key] });
      setText('');
      store(key, '');
      reload();
    } catch (problem) {
      setError((problem as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <details className="detail-panel experiment-reflection">
      <summary>{t('Thinking and reflection')}</summary>
      <p>
        {t(
          'Keep your expectations, alternatives and interpretation with this experiment. These notes are optional and can be revisited at any time.',
        )}
      </p>
      {data?.key === key && data.error && <ErrorLine text={data.error} />}
      {sections.map(([title, value]) => (
        <section key={title}>
          <h3>{t(title)}</h3>
          <ReactMarkdown>{value}</ReactMarkdown>
        </section>
      ))}
      {record?.claims && (
        <p className={record.claims.valid ? '' : 'bad-text'}>
          {record.claims.valid
            ? t('Number check passed: {n} claims match the run results', {
                n: record.claims.checked,
              })
            : t('Number check failed: {errors}', { errors: record.claims.errors.join('; ') })}
        </p>
      )}
      {notes.map((note) => (
        <article className="research-note" key={note.id}>
          <h3>{t('Your reflection')}</h3>
          <ReactMarkdown>{note.text}</ReactMarkdown>
        </article>
      ))}
      <label>
        {t('Your reflection')}
        <textarea
          rows={4}
          value={text}
          onChange={(event) => {
            setText(event.target.value);
            store(key, event.target.value);
          }}
          placeholder={t(
            'What did you expect? What else could explain the result? What would change your mind?',
          )}
        />
      </label>
      <ErrorLine text={error} />
      <div className="actions">
        <Button busy={busy} disabled={!text.trim()} onClick={save}>
          {t('Save reflection')}
        </Button>
        <Button onClick={() => onDiscuss([experiment.key])}>{t('Discuss this experiment')}</Button>
      </div>
    </details>
  );
}
