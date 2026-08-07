/**
 * 中长线策略配置 API 客户端（中线/长线共用，通过 tier 参数区分）
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
  order: number;
}

export interface StrategyStats {
  trades: number;
  win_rate: number;
  net_pnl: number;
  profit_factor: number;
  avg_hold_hours: number;
}

export interface StrategyConfigResponse {
  tier: string;
  config: Record<string, number | boolean>;
  param_defs: Record<string, ParamDef>;
  groups: Record<string, GroupDef>;
  stats: StrategyStats;
  fetched_at: number;
}

export interface Preset {
  name: string;
  description: string;
  params: Record<string, number | boolean>;
}

export type Tier = 'mid' | 'long';

export async function fetchStrategyConfig(tier: Tier): Promise<StrategyConfigResponse> {
  const resp = await apiRequest(`/strategy-config/${tier}`);
  return resp.json();
}

export async function updateStrategyConfig(tier: Tier, updates: Record<string, any>): Promise<{ success: boolean; updated_count: number; config: Record<string, any> }> {
  const resp = await apiRequest(`/strategy-config/${tier}`, {
    method: 'PUT',
    body: JSON.stringify({ updates }),
  });
  return resp.json();
}

export async function fetchStrategyPresets(tier: Tier): Promise<Record<string, Preset>> {
  const resp = await apiRequest(`/strategy-config/${tier}/presets`);
  return resp.json();
}
