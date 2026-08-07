/**
 * 短线策略配置 API 客户端
 */
import { apiRequest } from './api';

export interface ParamDef {
  env: string;
  default: string;
  type: 'float' | 'int' | 'bool';
  min: number;
  max: number;
  group: string;
  label: string;
  unit: string;
}

export interface GroupDef {
  title: string;
  icon: string;
  order: number;
}

export interface ScalpConfig {
  tp_pct: number;
  sl_pct: number;
  atr_sl_mult: number;
  atr_tp_mult: number;
  max_sl_pct: number;
  max_tp_pct: number;
  min_rr: number;
  max_hold_sec: number;
  roi_t1_sec: number;
  roi_t2_sec: number;
  roi_t3_sec: number;
  execute_threshold: number;
  confirm_threshold: number;
  ev_min_pct: number;
  ev_tp_realization: number;
  ev_gate_enabled: boolean;
  position_pct: number;
  leverage: number;
  tier_budget: number;
  max_opens_per_tick: number;
  open_cooldown: number;
  ai_reverse_disabled: boolean;
  liq_magnet_disabled: boolean;
  reduce_min_loss: number;
  mr_enabled: boolean;
  mr_min_range: number;
  mr_max_range: number;
  mr_rsi_os: number;
  mr_rsi_ob: number;
  mr_size_mult: number;
  liquidity_filter: boolean;
  min_volume_usd: number;
  [key: string]: number | boolean;
}

export interface EVResult {
  ev_pct: number;
  rr: number;
  breakeven_win: number;
  daily_return: number;
  monthly_return: number;
  round_trip_cost: number;
  fee_ratio: number;
}

export interface ScalpStats {
  trades: number;
  win_rate: number;
  net_pnl: number;
  profit_factor: number;
  avg_win: number;
  avg_loss: number;
}

export interface ScalpConfigResponse {
  config: ScalpConfig;
  param_defs: Record<string, ParamDef>;
  groups: Record<string, GroupDef>;
  stats: ScalpStats;
  ev: EVResult;
  fetched_at: number;
}

export interface PresetParams {
  [key: string]: number | boolean;
}

export interface Preset {
  name: string;
  description: string;
  params: PresetParams;
}

/** 计算EV（前端实时模拟） */
export function calcEV(
  tp: number, sl: number, pWin: number, tpReal: number = 0.55,
  leverage: number = 10, positionPct: number = 0.30, tradesPerDay: number = 3
): EVResult {
  const roundTripCost = 0.0021;
  const ev = pWin * tp * tpReal - (1 - pWin) * sl * 1.0 - roundTripCost;
  const rr = sl > 0 ? tp / sl : 0;
  const denom = tp * tpReal + sl * 1.0;
  const breakeven = denom > 0 ? (sl * 1.0 + roundTripCost) / denom : 1.0;
  const daily = ev * leverage * positionPct * tradesPerDay;
  const monthly = daily * 30;
  return {
    ev_pct: Math.round(ev * 1e6) / 1e6,
    rr: Math.round(rr * 100) / 100,
    breakeven_win: Math.round(breakeven * 10000) / 10000,
    daily_return: Math.round(daily * 1e6) / 1e6,
    monthly_return: Math.round(monthly * 1e6) / 1e6,
    round_trip_cost: roundTripCost,
    fee_ratio: Math.round(roundTripCost / Math.max(tp * tpReal, 0.001) * 10000) / 10000,
  };
}

export async function fetchScalpConfig(): Promise<ScalpConfigResponse> {
  const resp = await apiRequest('/scalp-config/');
  return resp.json();
}

export async function updateScalpConfig(updates: Record<string, any>): Promise<{ success: boolean; updated_count: number; config: ScalpConfig; ev: EVResult; errors?: string[] }> {
  const resp = await apiRequest('/scalp-config/', {
    method: 'PUT',
    body: JSON.stringify(updates),
  });
  return resp.json();
}

export async function fetchPresets(): Promise<Record<string, Preset>> {
  const resp = await apiRequest('/scalp-config/presets');
  return resp.json();
}

export interface CurrentPreset {
  preset_key: string;
  preset_name: string;
  is_custom: boolean;
}

export async function fetchCurrentPreset(): Promise<CurrentPreset> {
  const resp = await apiRequest('/scalp-config/current-preset');
  return resp.json();
}

export async function saveCustomPreset(name: string, params: Record<string, any>, description?: string): Promise<{ success: boolean; key: string }> {
  const resp = await apiRequest('/scalp-config/presets/custom', {
    method: 'POST',
    body: JSON.stringify({ name, params, description }),
  });
  return resp.json();
}

export async function deleteCustomPreset(key: string): Promise<{ success: boolean }> {
  const resp = await apiRequest(`/scalp-config/presets/custom/${key}`, {
    method: 'DELETE',
  });
  return resp.json();
}

export async function simulateEV(params: {
  tp_pct: number; sl_pct: number; p_win: number;
  tp_realization?: number; leverage?: number; position_pct?: number; trades_per_day?: number;
}): Promise<EVResult & { sensitivity: { p_win: number; ev_pct: number; daily: number }[] }> {
  const resp = await apiRequest('/scalp-config/simulate', {
    method: 'POST',
    body: JSON.stringify(params),
  });
  return resp.json();
}
