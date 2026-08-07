/**
 * 快速试单 + 学习激活 — API 客户端
 * Base: /api/ai-strategies/fast-trial
 */

const BASE =
  `${(import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, '') || '/api'}/ai-strategies/fast-trial`;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(text || `HTTP ${res.status}`);
  }
  return res.json();
}

export interface FastTrialParam {
  key: string;
  type: 'bool' | 'int' | 'float' | 'gear';
  group: string;
  label: string;
  desc?: string;
  min?: number;
  max?: number;
  default?: unknown;
  options?: string[];
  effective: unknown;
  overridden: boolean;
  gear_labels?: Record<string, string>;
}

export interface FastTrialGroup {
  id: string;
  label: string;
  params: FastTrialParam[];
}

export interface FastTrialDashboard {
  pace?: {
    gear: string;
    tick_seconds: number;
    max_strategies_per_tick: number;
    max_symbols_per_tick: number;
    learning_review_every_n: number;
    learning_miner_every_n?: number;
    manual_lock?: boolean;
  };
  scalp?: {
    confirm_threshold: number;
    execute_threshold: number;
    open_cooldown_sec: number;
    reentry_cooldown_sec: number;
    independent_scheduler: boolean;
  };
  learning_bus?: {
    trade_count_total?: number;
    trade_count_since_review?: number;
    next_review_in?: number;
    next_miner_in?: number;
  };
  learning_loop?: {
    paused?: boolean;
    registered?: boolean;
  };
  mlto?: {
    thesis_total: number;
    can_open: number;
    with_llm_summary: number;
  };
  tier_tick?: {
    scheduler_enabled?: boolean;
    intervals_sec?: {
      coordinator?: number;
      short?: number;
      mid?: number;
      long?: number;
    };
    live?: {
      due_now?: string[];
      until_due_sec?: { mid?: number; long?: number };
    };
    note?: string;
  };
  sessions_running?: number;
}

export interface FastTrialPreset {
  id: string;
  label: string;
  desc: string;
  icon?: string;
  accent?: string;
  highlights?: string[];
}

export interface FastTrialState {
  enabled: boolean;
  active_preset?: string | null;
  effective: Record<string, unknown>;
  overrides: Record<string, unknown>;
  schema: { groups: FastTrialGroup[] };
  dashboard: FastTrialDashboard;
  presets: FastTrialPreset[];
}

export function getFastTrialConfig(): Promise<FastTrialState> {
  return request('');
}

export function patchFastTrialConfig(patches: Record<string, unknown>): Promise<FastTrialState> {
  return request('', {
    method: 'PATCH',
    body: JSON.stringify({ patches }),
  });
}

export function applyFastTrialPreset(preset: string): Promise<FastTrialState> {
  return request('/preset', {
    method: 'POST',
    body: JSON.stringify({ preset }),
  });
}

export const GEAR_LABELS: Record<string, string> = {
  blitz: '闪电 30s',
  turbo: '极速 45s',
  warm: '偏快 60s',
  balanced: '均衡 90s',
  conservative: '保守 120s',
};

export const GROUP_LABELS: Record<string, string> = {
  master: '总开关',
  tier_tick: '三周期 Tick',
  pace: '交易节奏',
  open_gate: '中线/长线门控',
  scalp: '短线 ScalpRouter',
  learning: '学习激活',
};

export const PRESET_ACCENT: Record<string, string> = {
  violet: 'border-violet-500/35 bg-violet-500/8 hover:bg-violet-500/12',
  emerald: 'border-emerald-500/35 bg-emerald-500/8 hover:bg-emerald-500/12',
  amber: 'border-amber-500/35 bg-amber-500/8 hover:bg-amber-500/12',
  sky: 'border-sky-500/35 bg-sky-500/8 hover:bg-sky-500/12',
  slate: 'border-border bg-muted/30 hover:bg-muted/50',
  rose: 'border-rose-500/35 bg-rose-500/8 hover:bg-rose-500/12',
};
