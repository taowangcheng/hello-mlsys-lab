import words from '../../src/mlab/web/static/locales/zh.json';

export type Data = Record<string, any>;

export function secondaryMetric(overview: Data): string {
  const objective = overview.objective.metric || overview.objective.report?.[0];
  return objective === overview.key_metrics[0] ? 'critical_path_s' : objective;
}
export type Experiment = Data & {
  key: string;
  id: string;
  session: string;
  title: string;
  candidates: Data[];
  seeds: number[];
  workloads: string[];
  revision: string;
  editable: boolean;
  runs: string[];
};
export type Run = Data & {
  key: string;
  id: string;
  session: string;
  candidate: string;
  status: string;
  role: string;
  metrics: Data;
  key_value?: number;
  objective_value?: number;
};
export type Workspace = Data & {
  task: string;
  session: string;
  experiments: Experiment[];
  runs: Run[];
  notes: Data[];
  measurements: Data[];
  templates: Data[];
  rules: Data;
  infra: Data;
  overview: Data;
  budget: Data;
};

export let language =
  localStorage.getItem('mlab-language') || (navigator.language.startsWith('zh') ? 'zh' : 'en');
export function setLanguage(value: string) {
  language = value;
  localStorage.setItem('mlab-language', value);
  document.documentElement.lang = value;
}
export function t(text: string, values: Data = {}) {
  let value = language === 'zh' ? (words as Record<string, string>)[text] || text : text;
  for (const [key, replacement] of Object.entries(values))
    value = value.replaceAll(`{${key}}`, String(replacement));
  return value;
}
let storageScope = 'pending';
export function setStorageScope(value: string) {
  storageScope = value;
}
function storageKey(key: string) {
  return /^mlab-(plan|note|filters|compare|chat|conversation|session|location|seen)-/.test(key)
    ? `${storageScope}:${key}`
    : key;
}
export function stored<T>(key: string, fallback: T): T {
  try {
    return JSON.parse(localStorage.getItem(storageKey(key)) || 'null') ?? fallback;
  } catch {
    return fallback;
  }
}
export function store(key: string, value: unknown) {
  localStorage.setItem(storageKey(key), JSON.stringify(value));
}
export async function api(path: string, body?: Data): Promise<any> {
  const response = await fetch(path, {
    method: body ? 'POST' : 'GET',
    headers: { 'Content-Type': 'application/json', 'X-Mlab-Lang': language },
    body: body ? JSON.stringify(body) : undefined,
  });
  const value = await response.json();
  if (!response.ok || value.ok === false)
    throw new Error(t(value.error?.message || 'Could not complete this action. Please try again.'));
  return value;
}
export function act(session: string, action: string, body: Data) {
  return api(`/api/workbench/sessions/${session}/${action}`, {
    request_id: crypto.randomUUID(),
    ...body,
  });
}
export function nav(target: string, page: string, key = '') {
  const current = route();
  const session = /^9\.[123]-\d+$/.test(target)
    ? target
    : current.task === target && current.session
      ? current.session
      : target;
  location.hash = `/${session}/${page}${key ? '/' + encodeURIComponent(key) : ''}`;
}
export function route() {
  const [target = '9.2', page = 'overview', key = ''] = location.hash
    .replace(/^#\/?/, '')
    .split('/');
  const session = /^9\.[123]-\d+$/.test(target) ? target : '';
  let decoded = '';
  try {
    decoded = decodeURIComponent(key);
  } catch {
    decoded = key;
  }
  return { task: session ? session.split('-')[0] : target || '9.2', session, page, key: decoded };
}
export function number(value: unknown, digits = 1) {
  return typeof value === 'number' && Number.isFinite(value)
    ? value.toLocaleString(language === 'zh' ? 'zh-CN' : 'en-US', { maximumFractionDigits: digits })
    : '—';
}
export function metric(values: Data, key: string): number | undefined {
  return key?.split('.').reduce<any>((value, part) => value?.[part], values);
}
export function unit(key: string) {
  return key.includes('_ms')
    ? 'ms'
    : key.includes('tok_s')
      ? 'tok/s'
      : key.endsWith('_s')
        ? 's'
        : key.includes('energy')
          ? 'J/req'
          : key.includes('gib')
            ? 'GiB'
            : '';
}
export function label(key: string) {
  const labels: Record<string, string> = {
    'ttft_ms.p99': 'P99 first-token latency',
    'ttft_ms.p50': 'Median first-token latency',
    goodput_tok_s: 'Goodput',
    throughput_tok_s: 'Throughput',
    makespan_s: 'Completion time',
    critical_path_s: 'Critical path time',
    decode_tok_s: 'Decode throughput',
    vram_peak_gib: 'Peak device memory',
    energy_j_per_req: 'Energy per request',
    completion_rate: 'Completion rate',
    'serving.max_concurrency': 'Maximum concurrency',
    'serving.chunked_prefill_tokens': 'Prefill chunk size',
    'serving.gpu_memory_utilization': 'GPU memory utilization',
    'serving.kv_fraction': 'KV cache fraction',
    'serving.kv_budget_gib': 'KV cache budget',
    'serving.continuous_batching': 'Continuous batching',
    'cache.prefix_cache': 'Prefix caching',
    'deployments.model': 'Model',
    'deployments.weight_precision': 'Weight precision',
    'deployments.kv_precision': 'KV precision',
    'deployments.context_tokens': 'Context length',
    'deployments.parallel.kind': 'Parallelism',
    'deployments.parallel.degree': 'Parallel degree',
    'deployments.replicas': 'Replicas',
    'deployments.devices': 'Device placement',
    'scheduling.priority': 'Scheduling priority',
    'scheduling.preemption': 'Preemption policy',
    speculative: 'Speculative decoding',
    development: 'Development workload',
    short_prompt_distribution: 'Short prompts',
    long_prompt_distribution: 'Long prompts',
    one_dimension_change: 'Changed workload',
    typical: 'Typical workload',
    long_context: 'Long context',
    other_prompt_set: 'Alternative prompts',
    second_dag_same_distribution: 'Another dependency graph',
    'routing.kind': 'Routing policy',
    'routing.policy': 'Routing policy',
  };
  const deployment = key.match(/^deployments\[(\d+)\]\.(.+)$/);
  if (deployment) {
    const field = deployment[2];
    const device = field.match(/^devices\[(\d+)\]$/);
    const setting = device
      ? t('Device {number}', { number: Number(device[1]) + 1 })
      : t(labels[`deployments.${field}`] || field.replaceAll('_', ' '));
    return t('Model {number} · {setting}', { number: Number(deployment[1]) + 1, setting });
  }
  return t(labels[key] || key.replaceAll('_', ' '));
}
export function time(value: string) {
  return new Date(value).toLocaleString(language === 'zh' ? 'zh-CN' : 'en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}
export const terminal = (run: Run) =>
  ['succeeded', 'failed', 'cancelled', 'timed_out'].includes(run.status);
