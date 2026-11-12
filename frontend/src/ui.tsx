import { type ReactNode, type ButtonHTMLAttributes } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { X, LoaderCircle, CircleCheck, CircleAlert, Circle, Clock } from 'lucide-react';
import { t, number, metric, label, unit, type Run, type Data } from './api';

export function Button({
  children,
  className = '',
  busy = false,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { busy?: boolean }) {
  return (
    <button
      type="button"
      className={`button ${className}`}
      {...props}
      disabled={props.disabled || busy}
    >
      {busy ? <LoaderCircle size={15} className="spin" /> : null}
      {children}
    </button>
  );
}
export function Badge({ status }: { status: string }) {
  const active = [
    'running',
    'queued',
    'preparing',
    'collecting',
    'measuring',
    'points_predicting',
  ].includes(status);
  const bad = ['failed', 'timed_out', 'constraints_violated'].includes(status);
  const good = ['succeeded', 'completed', 'constraints_met', 'measured'].includes(status);
  const Icon = active ? Clock : bad ? CircleAlert : good ? CircleCheck : Circle;
  const names: Record<string, string> = {
    constraints_met: 'Meets constraints',
    constraints_violated: 'Outside constraints',
    succeeded: 'Completed',
    completed: 'Completed',
    running: 'Running',
    queued: 'Queued',
    preparing: 'Preparing',
    collecting: 'Collecting',
    draft: 'Draft',
    submitted: 'Submitted',
    approved: 'Ready',
    measured: 'Completed',
    measuring: 'Running',
    cancelled: 'Cancelled',
    failed: 'Run failed',
    timed_out: 'Timed out',
    insufficient_evidence: 'Needs more evidence',
    quality_unknown: 'Quality not assessed',
    points_predicting: 'Preparing predictions',
  };
  return (
    <span className={`badge ${active ? 'active' : bad ? 'bad' : good ? 'good' : ''}`}>
      <Icon size={12} />
      {t(names[status] || status)}
    </span>
  );
}
export function Modal({
  title,
  description,
  open,
  onClose,
  children,
  wide = false,
}: {
  title: string;
  description?: string;
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={(value) => !value && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-shade" />
        <Dialog.Content
          className={`dialog ${wide ? 'wide' : ''}`}
          aria-describedby={description ? 'dialog-description' : undefined}
        >
          <div className="dialog-heading">
            <div>
              <Dialog.Title>{title}</Dialog.Title>
              {description && (
                <Dialog.Description id="dialog-description">{description}</Dialog.Description>
              )}
            </div>
            <Dialog.Close className="icon-button" aria-label={t('Close')}>
              <X size={19} />
            </Dialog.Close>
          </div>
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
export function Empty({
  title,
  children,
  action,
}: {
  title: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <div className="empty-orbit">
        <span />
        <span />
        <span />
      </div>
      <h2>{title}</h2>
      <p>{children}</p>
      {action}
    </div>
  );
}
export function ErrorLine({ text }: { text: string }) {
  return text ? (
    <div className="error-line" role="alert">
      <CircleAlert size={16} />
      <span>{text}</span>
    </div>
  ) : null;
}

export function Tradeoff({
  rows,
  overview,
  onSelect,
}: {
  rows: Run[];
  overview: Data;
  onSelect: (run: Run) => void;
}) {
  const yKey = overview.key_metrics[0];
  const objective = overview.objective.metric || overview.objective.report?.[0] || 'goodput_tok_s';
  const xKey = objective === yKey ? 'critical_path_s' : objective;
  const values = rows.filter(
    (row) =>
      typeof metric(row.metrics, xKey) === 'number' &&
      typeof metric(row.metrics, yKey) === 'number',
  );
  if (!values.length) return null;
  const threshold = overview.constraints.find(
    (item: Data) => item.metric === yKey && item.value !== null,
  )?.value;
  const xMax = Math.max(...values.map((row) => metric(row.metrics, xKey) || 0), 1) * 1.15;
  const yMax =
    Math.max(...values.map((row) => metric(row.metrics, yKey) || 0), threshold || 0, 1) * 1.15;
  const x = (value: number) => 78 + (value / xMax) * 602;
  const y = (value: number) => 278 - (value / yMax) * 238;
  return (
    <div className="plot">
      <div className="plot-heading">
        <div>
          <span className="eyebrow">{t('PERFORMANCE LANDSCAPE')}</span>
          <h3>{t('See the trade-offs')}</h3>
        </div>
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
      </div>
      <svg viewBox="0 0 720 330" role="img" aria-label={`${label(xKey)} / ${label(yKey)}`}>
        {threshold && (
          <rect
            x="78"
            y={y(threshold)}
            width="602"
            height={278 - y(threshold)}
            className="plot-zone"
          />
        )}
        {[0, 0.25, 0.5, 0.75, 1].map((fraction) => (
          <g key={fraction}>
            <line
              x1="78"
              x2="680"
              y1={y(fraction * yMax)}
              y2={y(fraction * yMax)}
              className="plot-grid"
            />
            <text x="65" y={y(fraction * yMax) + 4} textAnchor="end">
              {number(fraction * yMax, 0)}
            </text>
            <text x={x(fraction * xMax)} y="300" textAnchor="middle">
              {number(fraction * xMax, 0)}
            </text>
          </g>
        ))}
        {threshold && (
          <g>
            <line x1="78" x2="680" y1={y(threshold)} y2={y(threshold)} className="threshold" />
            <text x="676" y={y(threshold) - 7} textAnchor="end">
              {t('Limit')} · {number(threshold)} {unit(yKey)}
            </text>
          </g>
        )}
        <text x="78" y="22">
          {label(yKey)} · {unit(yKey)}
        </text>
        <text x="380" y="325" textAnchor="middle">
          {label(xKey)} · {unit(xKey)}
        </text>
        {values.map((row) => (
          <circle
            key={row.key}
            cx={x(metric(row.metrics, xKey)!)}
            cy={y(metric(row.metrics, yKey)!)}
            r="6"
            tabIndex={0}
            role="button"
            aria-label={`${row.candidate}, ${row.id}`}
            className={row.role === 'hardware' ? 'plot-hardware' : 'plot-simulator'}
            onClick={() => onSelect(row)}
            onKeyDown={(event) => event.key === 'Enter' && onSelect(row)}
          >
            <title>
              {row.candidate}: {number(metric(row.metrics, yKey))} {unit(yKey)} ·{' '}
              {number(metric(row.metrics, xKey))} {unit(xKey)}
            </title>
          </circle>
        ))}
      </svg>
      <div className="plot-footnote">
        {t('Select a point to inspect its configuration and results.')}
      </div>
    </div>
  );
}
