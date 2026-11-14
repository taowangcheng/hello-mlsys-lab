import { useEffect, useRef, useState } from 'react';
import {
  ArrowLeft,
  ArrowRight,
  Plus,
  Copy,
  Trash2,
  Play,
  Check,
  Save,
  FlaskConical,
  Cpu,
} from 'lucide-react';
import {
  act,
  api,
  t,
  store,
  storageAvailable,
  stored,
  nav,
  route,
  label,
  number,
  type Data,
  type Experiment,
  type Workspace,
} from './api';
import { Button, ErrorLine, Modal } from './ui';

function getValue(object: Data, parts: string[]): any {
  return parts.reduce<any>((value, key) => value?.[key], object);
}
function setValue(object: Data, parts: string[], value: any) {
  const last = parts.at(-1)!;
  const parent = parts.slice(0, -1).reduce<Data>((item, key) => (item[key] ||= {}), object);
  if (value === undefined) delete parent[last];
  else parent[last] = value;
}

function editableContent(value: Experiment) {
  return JSON.stringify({
    title: value.title,
    question: value.question || '',
    expectation: value.expectation || '',
    candidates: value.candidates,
    seeds: value.seeds,
    workloads: value.workloads,
  });
}

export function Editor({
  original,
  workspace,
  reload,
  onMeasure,
}: {
  original: Experiment;
  workspace: Workspace;
  reload: () => Promise<void>;
  onMeasure: (item: Experiment) => void;
}) {
  const localKey = `mlab-plan-${original.key}`;
  const [draft, setDraft] = useState<Experiment>(
    () => stored<Experiment | null>(localKey, null) || structuredClone(original),
  );
  const [active, setActive] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(
    draft.revision !== original.revision
      ? t(
          'This draft changed elsewhere. Your local edits are preserved. Duplicate it to keep both versions.',
        )
      : '',
  );
  const [review, setReview] = useState(false);
  const [saved, setSaved] = useState(editableContent(original));
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const dirty = editableContent(draft) !== saved;
  const candidate = draft.candidates[active] || draft.candidates[0];
  const count = draft.candidates.length * draft.seeds.length * draft.workloads.length;
  useEffect(() => {
    store(localKey, draft);
  }, [draft, localKey]);
  const update = (change: (value: Experiment) => void) =>
    setDraft((previous) => {
      const value = structuredClone(previous);
      change(value);
      return value;
    });
  async function saveDraft() {
    const value = await act(draft.session, 'save', { ...draft, workflow: draft.id });
    if (mounted.current) {
      setDraft((current) =>
        editableContent(current) === editableContent(draft)
          ? value
          : { ...current, revision: value.revision },
      );
      setSaved(editableContent(value));
    }
    await reload();
    return value as Experiment;
  }
  async function submit(run = false) {
    if (busy) return;
    const startingLocation = window.location.hash;
    setBusy(true);
    setError('');
    try {
      const value = await saveDraft();
      if (run) {
        await act(value.session, 'simulate', {
          workflow: value.id,
          revision: value.revision,
          confirm: true,
        });
        store(localKey, null);
        await reload();
        if (window.location.hash === startingLocation) nav(workspace.session, 'runs');
      }
      setReview(false);
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  }
  if (!candidate) return null;
  async function arrangeMeasurement() {
    if (busy) return;
    const startingLocation = window.location.hash;
    setBusy(true);
    setError('');
    try {
      const value = await saveDraft();
      if (mounted.current && window.location.hash === startingLocation) onMeasure(value);
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function preserveDraft() {
    if (busy) return;
    const startingLocation = window.location.hash;
    setBusy(true);
    try {
      const value = await act(draft.session, 'create', { ...draft, source: original.key });
      await reload();
      if (mounted.current && window.location.hash === startingLocation)
        nav(workspace.session, 'plans', value.key);
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const devices =
    workspace.infra[candidate.infra]?.nodes.flatMap((node: Data) =>
      node.devices.map((device: Data) => device.id),
    ) || [];
  return (
    <div className="editor-page">
      <div className="page-heading">
        <div>
          <button className="text-button" onClick={() => nav(workspace.session, 'runs')}>
            <ArrowLeft size={14} />
            {t('Back to run history')}
          </button>
          <h1>{t('Run configuration')}</h1>
          <p>{t('Choose what to change, then decide where to run it.')}</p>
        </div>
        <div className="actions">
          <span className="save-state">
            {dirty ? (
              t(storageAvailable() ? 'Changes saved in this browser' : 'Changes kept in this tab')
            ) : (
              <>
                <Check size={13} />
                {t('Draft saved')}
              </>
            )}
          </span>
          <Button busy={busy} onClick={() => submit()}>
            <Save size={15} />
            {t('Save draft')}
          </Button>
          <Button busy={busy} onClick={arrangeMeasurement}>
            <Cpu size={15} />
            {t('Arrange measurement')}
          </Button>
          <Button className="primary" disabled={busy} onClick={() => setReview(true)}>
            <Play size={15} />
            {t('Run simulations')}
          </Button>
        </div>
      </div>
      <ErrorLine text={error} />
      {draft.revision !== original.revision && (
        <Button busy={busy} onClick={preserveDraft}>
          {t('Save as new experiment')}
        </Button>
      )}
      <details className="editor-title">
        <summary>{t('Name and notes (optional)')}</summary>
        <label>
          {t('Experiment name')}
          <input
            className="title-input"
            value={draft.title === 'Untitled experiment' ? '' : draft.title}
            placeholder={t('For example: How much concurrency is enough?')}
            onChange={(event) =>
              update((value) => {
                value.title = event.target.value;
              })
            }
          />
        </label>
        <div className="two-fields">
          <label>
            {t('What do you want to find out?')}
            <textarea
              rows={2}
              value={draft.question || ''}
              placeholder={t('Describe the question this experiment will answer.')}
              onChange={(event) =>
                update((value) => {
                  value.question = event.target.value;
                })
              }
            />
          </label>
          <label>
            {t('What do you expect?')}
            <textarea
              rows={2}
              value={draft.expectation || ''}
              placeholder={t('Optional: write down a prediction before seeing the results.')}
              onChange={(event) =>
                update((value) => {
                  value.expectation = event.target.value;
                })
              }
            />
          </label>
        </div>
      </details>
      {draft.source && (
        <p className="origin-note">
          {t(
            /\/(sim|hw)_/.test(draft.source) ? 'Based on a previous run' : 'Based on a saved plan',
          )}{' '}
          <button
            className="text-button"
            onClick={() =>
              nav(
                workspace.session,
                /\/(sim|hw)_/.test(draft.source) ? 'runs' : 'plans',
                draft.source,
              )
            }
          >
            {draft.source.split('/').slice(1).join('/')}
            <ArrowRight size={12} />
          </button>
        </p>
      )}
      <div className="configuration-layout">
        <aside className="candidate-list">
          <div className="section-label">
            {t('CONFIGURATIONS')}
            <span>{draft.candidates.length}</span>
          </div>
          {draft.candidates.map((item, index) => (
            <button
              className={`candidate-item ${active === index ? 'selected' : ''}`}
              key={index}
              onClick={() => setActive(index)}
            >
              <span className="candidate-letter">{String.fromCharCode(65 + index)}</span>
              <span>
                <strong>{item.id}</strong>
                <small>{item.infra}</small>
              </span>
              <ArrowRight size={14} />
            </button>
          ))}
          <Button
            className="full ghost"
            onClick={() => {
              update((value) => {
                value.candidates.push({
                  ...structuredClone(candidate),
                  id: `variant_${Date.now().toString(36)}`,
                });
              });
              setActive(draft.candidates.length);
            }}
          >
            <Plus size={15} />
            {t('Add a variant')}
          </Button>
          <div className="editor-tip">
            <FlaskConical size={20} />
            <p>{t('Change one variable at a time to make the result easier to explain.')}</p>
          </div>
        </aside>
        <section className="candidate-form">
          <div className="candidate-heading">
            <div>
              <span className="eyebrow">
                {t('CONFIGURATION')} {String.fromCharCode(65 + active)}
              </span>
              <h2>{candidate.id}</h2>
            </div>
            <Button
              className="ghost small"
              disabled={draft.candidates.length <= 1}
              onClick={() => {
                update((value) => {
                  value.candidates.splice(active, 1);
                });
                setActive(Math.max(0, active - 1));
              }}
            >
              <Trash2 size={14} />
              {t('Remove')}
            </Button>
          </div>
          <div className="two-fields">
            <label>
              {t('Configuration name')}
              <input
                value={candidate.id}
                onChange={(event) =>
                  update((value) => {
                    value.candidates[active].id = event.target.value;
                  })
                }
              />
            </label>
            <label>
              {t('Hardware configuration')}
              <select
                value={candidate.infra}
                onChange={(event) =>
                  update((value) => {
                    value.candidates[active].infra = event.target.value;
                  })
                }
              >
                {Object.keys(workspace.infra).map((infra) => (
                  <option key={infra}>{infra}</option>
                ))}
              </select>
            </label>
          </div>
          <label>
            {t('Why include this configuration?')}
            <input
              value={candidate.rationale}
              onChange={(event) =>
                update((value) => {
                  value.candidates[active].rationale = event.target.value;
                })
              }
            />
          </label>
          {['deployments', 'other'].map((group) => (
            <div className="parameter-group" key={group}>
              <h3>
                {t(group === 'deployments' ? 'Model and placement' : 'Serving and scheduling')}
              </h3>
              {(group === 'deployments' ? candidate.strategy.deployments : [null]).map(
                (_deployment: Data | null, index: number) => (
                  <div key={index}>
                    {group === 'deployments' && candidate.strategy.deployments.length > 1 && (
                      <h4>
                        {t('Model')} {index + 1}
                      </h4>
                    )}
                    <div className="parameter-grid">
                      {Object.entries(workspace.rules)
                        .filter(
                          ([path]) => (group === 'deployments') === path.startsWith('deployments.'),
                        )
                        .map(([path, rule]) => {
                          const parts = path.startsWith('deployments.')
                            ? ['deployments', String(index), ...path.split('.').slice(1)]
                            : path.split('.');
                          const value = getValue(candidate.strategy, parts);
                          const previous = getValue(draft.candidates[0].strategy, parts);
                          const changed =
                            active > 0 && JSON.stringify(previous) !== JSON.stringify(value);
                          const change = (value: any) =>
                            update((draft) => {
                              setValue(draft.candidates[active].strategy, parts, value);
                            });
                          return (
                            <label className={changed ? 'changed-field' : ''} key={path}>
                              {label(path)}
                              {rule.type === 'enum' ? (
                                <select
                                  value={value === undefined ? '' : JSON.stringify(value)}
                                  onChange={(event) =>
                                    change(
                                      event.target.value
                                        ? JSON.parse(event.target.value)
                                        : undefined,
                                    )
                                  }
                                >
                                  <option value="">{t('Default')}</option>
                                  {rule.values.map((item: any) => (
                                    <option key={JSON.stringify(item)} value={JSON.stringify(item)}>
                                      {typeof item === 'boolean'
                                        ? t(item ? 'Enabled' : 'Disabled')
                                        : item}
                                    </option>
                                  ))}
                                </select>
                              ) : (
                                <input
                                  type="number"
                                  min={rule.min}
                                  max={rule.max}
                                  step={rule.max <= 1 ? 0.05 : 1}
                                  value={value ?? ''}
                                  placeholder={t('Default')}
                                  onChange={(event) =>
                                    change(
                                      event.target.value === ''
                                        ? undefined
                                        : Number(event.target.value),
                                    )
                                  }
                                />
                              )}
                              {changed && (
                                <small>
                                  {t('Baseline')}: {String(previous ?? t('Default'))}
                                </small>
                              )}
                            </label>
                          );
                        })}
                    </div>
                    {group === 'deployments' && (
                      <label className="placement">
                        {t('Devices')}
                        <div className="device-options">
                          {devices.map((device: string) => (
                            <label key={device}>
                              <input
                                type="checkbox"
                                checked={candidate.strategy.deployments[index].devices.includes(
                                  device,
                                )}
                                onChange={(event) =>
                                  update((value) => {
                                    const deployment =
                                      value.candidates[active].strategy.deployments[index];
                                    deployment.devices = event.target.checked
                                      ? [...deployment.devices, device]
                                      : deployment.devices.filter(
                                          (value: string) => value !== device,
                                        );
                                  })
                                }
                              />
                              {device}
                            </label>
                          ))}
                        </div>
                      </label>
                    )}
                  </div>
                ),
              )}
            </div>
          ))}
        </section>
      </div>
      <section className="execution-settings">
        <div>
          <span className="eyebrow">{t('EXECUTION')}</span>
          <h2>{t('Choose the workloads')}</h2>
          <p>{t('Every selected configuration will run on each selected workload.')}</p>
        </div>
        <div className="execution-controls">
          <div className="workload-options">
            {['development', ...workspace.overview.holdouts].map((workload) => (
              <label key={workload}>
                <input
                  type="checkbox"
                  checked={draft.workloads.includes(workload)}
                  onChange={(event) =>
                    update((value) => {
                      value.workloads = event.target.checked
                        ? [...value.workloads, workload]
                        : value.workloads.filter((item) => item !== workload);
                    })
                  }
                />
                {label(workload)}
              </label>
            ))}
          </div>
          <div className="two-fields">
            <label>
              {t('Repetitions')}
              <input
                type="number"
                min="1"
                max="12"
                value={draft.seeds.length}
                onChange={(event) =>
                  update((value) => {
                    const count = Math.max(1, Math.min(12, Number(event.target.value)));
                    value.seeds = Array.from(
                      { length: count },
                      (_, index) => (value.seeds[0] ?? 42) + index,
                    );
                  })
                }
              />
            </label>
            <label>
              {t('Starting seed')}
              <input
                type="number"
                min="0"
                value={draft.seeds[0]}
                onChange={(event) =>
                  update((value) => {
                    value.seeds = value.seeds.map((_, index) => Number(event.target.value) + index);
                  })
                }
              />
            </label>
          </div>
          <p className="execution-total">
            {t('{count} simulation runs · no hardware budget', { count })}
          </p>
        </div>
      </section>
      <Modal
        title={t('Ready to simulate?')}
        description={t('Review the configurations and workloads before starting.')}
        open={review}
        onClose={() => setReview(false)}
      >
        <div className="review-summary">
          <strong>{draft.title}</strong>
          <p>{draft.candidates.map((item) => item.id).join(' · ')}</p>
          <p>{draft.workloads.map(label).join(' · ')}</p>
          <div className="review-total">
            <span>{t('Simulation runs')}</span>
            <strong>{count}</strong>
          </div>
          <p>{t('The saved configuration will stay attached to every result.')}</p>
        </div>
        <ErrorLine text={error} />
        <div className="dialog-actions">
          <Button onClick={() => setReview(false)}>{t('Keep editing')}</Button>
          <Button className="primary" busy={busy} onClick={() => submit(true)}>
            <Play size={15} />
            {t('Start simulations')}
          </Button>
        </div>
      </Modal>
    </div>
  );
}

export function Measurement({
  workspace,
  initial,
  measurement,
  point,
  onClose,
  reload,
}: {
  workspace: Workspace;
  initial?: Experiment;
  measurement?: Data;
  point?: { candidate: string; workload: string };
  onClose: () => void;
  reload: () => Promise<void>;
}) {
  const options = workspace.experiments.filter((item) => item.approved || item.editable);
  const [key, setKey] = useState(initial?.key || options.at(-1)?.key || '');
  const source = options.find((item) => item.key === key);
  const [selected, setSelected] = useState<string[]>(
    point ? [point.candidate] : initial?.candidates.slice(0, 2).map((item) => item.id) || [],
  );
  const [workload, setWorkload] = useState(point?.workload || 'development');
  const [repetitions, setRepetitions] = useState(1);
  const [reason, setReason] = useState('');
  const [staged, setStaged] = useState<Data[]>([]);
  const [preview, setPreview] = useState<Data | null>(null);
  const [restoring, setRestoring] = useState(Boolean(measurement));
  const [session, setSession] = useState(source?.session || measurement?.session || '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const currentPoints = source
    ? selected.map((candidate) => ({
        candidate,
        workflow: source.id,
        workload,
        runs: repetitions,
        rationale: reason.trim() || 'Selected for measurement',
      }))
    : [];
  const points = [...staged, ...currentPoints].reduce<Data[]>((items, point) => {
    const previous = items.find(
      (item) =>
        item.workflow === point.workflow &&
        item.candidate === point.candidate &&
        item.workload === point.workload,
    );
    if (previous) previous.runs += point.runs;
    else items.push({ ...point });
    return items;
  }, []);
  const count = points.reduce((sum, point) => sum + point.runs, 0);
  async function check(id: string, target = session) {
    const result = await act(target, 'check_measurement', { measurement: id });
    setPreview(result);
    await reload();
  }
  useEffect(() => {
    if (measurement) {
      setSession(measurement.session);
      check(measurement.id, measurement.session)
        .catch((error) => setError(error.message))
        .finally(() => setRestoring(false));
    }
  }, []);
  useEffect(() => {
    if (preview?.status !== 'simulating') return;
    const timer = setInterval(async () => {
      try {
        const result = await api(`/api/sessions/${session}/records/${preview.id}`);
        if (result.runs.length && result.runs.every((run: Data) => run.status === 'succeeded'))
          await check(preview.id);
        else if (
          result.runs.some((run: Data) => ['failed', 'timed_out', 'cancelled'].includes(run.status))
        ) {
          setError(t('A prediction did not finish. Inspect the run before trying again.'));
          clearInterval(timer);
        }
      } catch (error) {
        setError((error as Error).message);
      }
    }, 2000);
    return () => clearInterval(timer);
  }, [preview?.status, preview?.id, session]);
  async function prepare() {
    if (!source || !points.length) {
      setError(t('Select the configurations to measure.'));
      return;
    }
    setBusy(true);
    setError('');
    try {
      const target = currentPoints.length
        ? source
        : options.find((item) => item.id === points[0].workflow)!;
      setSession(target.session);
      const result = await act(target.session, 'prepare_measurement', {
        title: target.title,
        workflow: target.id,
        revision: target.revision,
        confirm_config: true,
        points,
      });
      setPreview(result);
      await reload();
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function submit() {
    setBusy(true);
    setError('');
    try {
      await act(session, 'measure', {
        measurement: preview!.id,
        revision: preview!.plan_hash,
        confirm: true,
      });
      await reload();
      if (route().session === workspace.session) {
        onClose();
        nav(workspace.session, 'runs');
      }
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      open
      onClose={onClose}
      title={t('Arrange a measurement')}
      description={t('Choose the evidence you need before spending hardware runs.')}
    >
      {!preview && measurement ? (
        <p>
          {t(
            restoring
              ? 'Loading measurement…'
              : 'Could not load this measurement. Close it and try again.',
          )}
        </p>
      ) : !preview ? (
        <div className="measurement-form">
          <label>
            {t('Experiment plan')}
            <select
              value={key}
              onChange={(event) => {
                setKey(event.target.value);
                setSelected([]);
              }}
            >
              {options.map((item) => (
                <option key={item.key} value={item.key}>
                  {item.title}
                </option>
              ))}
            </select>
          </label>
          <div className="check-list">
            {source?.candidates.map((item) => (
              <label key={item.id}>
                <input
                  type="checkbox"
                  checked={selected.includes(item.id)}
                  onChange={(event) =>
                    setSelected(
                      event.target.checked
                        ? [...selected, item.id]
                        : selected.filter((id) => id !== item.id),
                    )
                  }
                />
                <span>
                  <strong>{item.id}</strong>
                  <small>{item.rationale}</small>
                </span>
              </label>
            ))}
          </div>
          <div className="two-fields">
            <label>
              {t('Workload')}
              <select value={workload} onChange={(event) => setWorkload(event.target.value)}>
                {['development', ...workspace.overview.holdouts].map((item) => (
                  <option key={item} value={item}>
                    {label(item)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {t('Repetitions')}
              <input
                type="number"
                min="1"
                max="4"
                value={repetitions}
                onChange={(event) => setRepetitions(Number(event.target.value))}
              />
            </label>
          </div>
          {source?.approved && (
            <Button
              className="small"
              disabled={!selected.length}
              onClick={() => {
                setStaged(points);
                setSelected([]);
              }}
            >
              <Plus size={14} />
              {t('Add these points and choose more')}
            </Button>
          )}
          {staged.length > 0 && (
            <div className="staged-measurements">
              <h3>{t('Included in this measurement')}</h3>
              {staged.map((point, index) => (
                <div key={`${point.workflow}/${point.candidate}/${point.workload}`}>
                  <span>
                    <strong>{point.candidate}</strong>
                    <small>
                      {point.workflow} · {label(point.workload)} ·{' '}
                      {t('{count} runs', { count: point.runs })}
                    </small>
                  </span>
                  <button
                    className="icon-button"
                    aria-label={t('Remove measurement point {number}', { number: index + 1 })}
                    onClick={() => setStaged(staged.filter((_, i) => i !== index))}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
            </div>
          )}
          <label>
            {t('Why measure these configurations?')} <small>{t('Optional')}</small>
            <textarea
              aria-label={t('Why measure these configurations?')}
              rows={3}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder={t('For example: check the configuration closest to the SLA limit.')}
            />
          </label>
          <div className="budget-preview">
            <Cpu size={18} />
            <span>{t('Uses {count} hardware runs', { count })}</span>
            <strong>
              {workspace.budget.remaining} {t('available')}
            </strong>
          </div>
        </div>
      ) : (
        <div className="measurement-preview">
          {preview.status === 'simulating' ? (
            <>
              <h3>{t('Preparing matching predictions')}</h3>
              <p>
                {t(
                  'The same configurations are being simulated first. No hardware budget is used yet.',
                )}
              </p>
              <BadgePlaceholder />
              <Button onClick={() => check(preview.id)}>{t('Check predictions')}</Button>
            </>
          ) : (
            <>
              <div className="review-total">
                <span>{t('Hardware runs')}</span>
                <strong>{preview.hardware_runs}</strong>
              </div>
              {preview.points?.map((point: Data) => (
                <div
                  className="prediction-line"
                  key={`${point.workflow}/${point.candidate}/${point.workload}`}
                >
                  <span>
                    <strong>{point.candidate}</strong>
                    <small>
                      {point.workflow} · {label(point.workload)} · {point.runs}×
                    </small>
                  </span>
                  <span>
                    {number(point.simulated)}
                    <small>
                      {t('Predicted margin')}: {number(point.sim_margin_pct)}%
                    </small>
                    {point.sim_status && point.sim_status !== 'succeeded' && (
                      <small>
                        {t(
                          'Simulation did not complete ({reason}). You can still confirm a sandbox check.',
                          { reason: t(point.sim_failure_class || point.sim_status) },
                        )}
                      </small>
                    )}
                  </span>
                </div>
              ))}
              <p>
                {t('{count} runs will remain after this measurement.', {
                  count: Math.max(
                    0,
                    (preview.hardware_budget?.remaining || 0) - preview.hardware_runs,
                  ),
                })}
              </p>
              <div className="data-notice">
                {t(
                  'This workspace uses demonstration measurements. These are not real GPU results.',
                )}
              </div>
            </>
          )}
        </div>
      )}
      <ErrorLine text={error} />
      {preview && preview.hardware_runs > preview.hardware_budget?.remaining && (
        <ErrorLine
          text={t(
            'Not enough measurement runs remain. Choose fewer configurations in a new measurement.',
          )}
        />
      )}
      <div className="dialog-actions">
        <Button onClick={onClose}>{t('Close')}</Button>
        {preview ? (
          <Button
            className="primary"
            busy={busy}
            disabled={
              !preview.plan_hash || preview.hardware_runs > preview.hardware_budget?.remaining
            }
            onClick={submit}
          >
            <Cpu size={15} />
            {t('Confirm and measure')}
          </Button>
        ) : !measurement ? (
          <Button
            className="primary"
            busy={busy}
            disabled={
              !options.length ||
              count > workspace.budget.remaining ||
              count > workspace.overview.budget.hardware_per_batch
            }
            onClick={prepare}
          >
            {t('Review measurement')}
            <ArrowRight size={15} />
          </Button>
        ) : null}
      </div>
    </Modal>
  );
}
function BadgePlaceholder() {
  return (
    <div className="progress-line">
      <span />
    </div>
  );
}
