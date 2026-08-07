/**
 * Hermes 自进化系统 API 客户端（frontend-next 移植版）
 *
 * S2-11 从已冻结的 frontend/ 移植：Hermes 生命周期面板所需的
 * maturity / health / schedule / patterns 端点。
 * 统一走 apiRequest（自动带 Bearer token + 401 refresh）。
 */

import { apiRequest } from "./api";

// ──── 类型定义 ────

export interface MaturityScore {
  maturity_score: number;
  l1_wisdom: number;
  l2_prompt: number;
  l3_architecture: number;
  l4_genesis: number;
  details: {
    wisdom_records: number;
    high_confidence_patterns: number;
    prompt_versions: number;
    latest_prompt_improved_rate: number;
    architecture_proposals: number;
    genesis_total: number;
    genesis_validated: number;
    genesis_promoted: number;
  };
}

export interface PatternItem {
  param_key: string;
  market_condition: string;
  direction: string;
  outcome: string;
  sample_count: number;
  avg_pnl_impact: number;
  confidence_avg: number;
  pattern_summary: string;
}

export interface HermesHealth {
  db_ok: boolean;
  db_error?: string | null;
  sidecar_ok?: boolean;
  maturity: MaturityScore;
  l1?: Record<string, unknown>;
  l2?: Record<string, unknown>;
  l3?: Record<string, number>;
  l4?: Record<string, number>;
  error?: string;
}

// ──── 时间轴：定时任务状态 ────

export interface HermesTaskSchedule {
  job_id: string;
  layer: string; // L1 | L2 | L3 | L4
  label: string; // 中文标签
  desc: string;
  interval_s: number; // 间隔秒数
  last_started_at: string | null; // ISO8601
  last_finished_at: string | null; // ISO8601
  last_status: "ok" | "error" | "running" | null;
  is_running: boolean;
  last_error: string | null;
  next_run_time: string | null; // ISO8601，预计下次运行
  registered: boolean; // 是否已注册到调度器
}

// ──── API 函数 ────

export async function getHermesMaturity(): Promise<MaturityScore> {
  return apiRequest<MaturityScore>("/hermes/maturity");
}

export async function getHermesHealth(): Promise<HermesHealth> {
  return apiRequest<HermesHealth>("/hermes/health");
}

export async function getHermesSchedule(): Promise<{ tasks: HermesTaskSchedule[] }> {
  return apiRequest<{ tasks: HermesTaskSchedule[] }>("/hermes/schedule");
}

export async function getHermesPatterns(
  minSamples = 2
): Promise<{ patterns: PatternItem[] }> {
  return apiRequest<{ patterns: PatternItem[] }>(`/hermes/patterns?min_samples=${minSamples}`);
}
