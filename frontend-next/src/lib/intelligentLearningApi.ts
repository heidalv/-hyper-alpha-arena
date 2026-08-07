/**
 * Intelligent Learning Center — 统一 API 客户端（frontend-next 移植版）
 *
 * S2-11 从已冻结的 frontend/ 移植：5 个学习三通道/决策链路/选币反馈端点。
 * 统一走 apiRequest（自动带 Bearer token + 401 refresh）。
 */

import { apiRequest } from "./api";

// ─────────────────────────────────────────────
//  类型定义（与后端 /api/intelligent-learning/* 返回对齐）
// ─────────────────────────────────────────────

export interface WisdomRankedItem {
  id: number;
  type?: string | null;
  tier?: string | null;
  template_id?: string | null;
  effectiveness?: number | null;
  evaluation_count?: number;
  quality_hit_count?: number;
  applied_count?: number;
  strength?: number;
}

export interface WisdomLoopResponse {
  ranked?: WisdomRankedItem[];
  report?: {
    total?: number;
    active?: number;
    deactivated?: number;
    by_type?: Record<string, { count: number; avg_effectiveness: number; total_applied: number; active: number }>;
    top_wisdom?: Array<Record<string, unknown>>;
  };
  error?: string;
}

export interface QaaDomainHeartbeat {
  enabled: boolean;
  interval_sec: number;
  description: string;
  last_run_at: number;
  last_status: string;
  last_error: string;
  run_count: number;
}

export interface QaaSchedulerResponse {
  enabled?: boolean;
  domains?: Record<string, QaaDomainHeartbeat>;
  error?: string;
}

export interface ParamDomainChange {
  param_key: string;
  direction: string;
  old: [number, number];
  new: [number, number];
  n_patterns: number;
}

export interface ParamDomainResponse {
  cfg?: {
    enabled?: boolean;
    expand_ratio?: number;
    expand_max?: number;
    min_samples?: number;
    min_confidence?: number;
  };
  patterns?: {
    total?: number;
    by_direction?: Record<string, number>;
    by_key?: Record<string, { increase: number; decrease: number; avg_pnl_impact: number; n: number }>;
  };
  base_ranges?: Record<string, [number, number]>;
  expanded_ranges?: Record<string, [number, number]>;
  changes?: ParamDomainChange[];
  expanded_count?: number;
  error?: string;
}

export interface DecisionChainWisdom {
  type?: string | null;
  tier?: string | null;
  template_id?: string | null;
  effectiveness?: number | null;
  evaluation_count?: number;
  quality_hit_count?: number;
  is_active?: boolean | null;
}

export interface DecisionChainItem {
  id: number;
  decision_time?: string | null;
  symbol?: string | null;
  operation?: string | null;
  decision_source?: string | null;
  realized_pnl?: number | null;
  wisdom_ids: number[];
  wisdoms: DecisionChainWisdom[];
}

export interface DecisionChainResponse {
  chain?: DecisionChainItem[];
  total_decisions?: number;
  sampled?: number;
  wisdom_covered?: number;
  error?: string;
}

export interface CoinFeedbackResponse {
  ic_weights?: {
    weights?: Record<string, number>;
    ics?: Record<string, number>;
    n_samples?: number;
    enabled?: boolean;
    note?: string;
    computed_at?: number;
  };
  injected?: {
    total?: number;
    with_snapshot?: number;
    hit_24h?: number;
    hit_72h?: number;
    hit_rate_24h?: number;
    hit_rate_72h?: number;
    by_symbol?: Record<string, { n: number; hit24: number; hit72: number; hit_rate_24h: number }>;
  };
  error?: string;
}

// ─────────────────────────────────────────────
//  阶段2(S2-11) 学习三通道看板 API
// ─────────────────────────────────────────────

/** 通道一：wisdom 闭环看板 */
export async function getWisdomLoop(): Promise<WisdomLoopResponse> {
  return apiRequest<WisdomLoopResponse>("/intelligent-learning/wisdom-loop");
}

/** 通道二：参数域扩展状态 */
export async function getParamDomain(): Promise<ParamDomainResponse> {
  return apiRequest<ParamDomainResponse>("/intelligent-learning/param-domain");
}

/** 通道三：QAA 调度统一心跳 */
export async function getQaaScheduler(): Promise<QaaSchedulerResponse> {
  return apiRequest<QaaSchedulerResponse>("/intelligent-learning/qaa-scheduler");
}

/** 决策链路视图 */
export async function getDecisionChain(limit = 20): Promise<DecisionChainResponse> {
  return apiRequest<DecisionChainResponse>(`/intelligent-learning/decision-chain?limit=${limit}`);
}

/** 选币反馈面板 */
export async function getCoinFeedback(): Promise<CoinFeedbackResponse> {
  return apiRequest<CoinFeedbackResponse>("/intelligent-learning/coin-feedback");
}

// ─────────────────────────────────────────────
//  2026-08-06 v6 8.3 阶段1-1 / 9.2 真实健康与 Wisdom 生命周期
// ─────────────────────────────────────────────

/** 学习闭环健康单项 */
export interface LearningHealthItem {
  name: string;
  label: string;
  status: "ok" | "warn" | "dead";
  last_activity?: string | null;
  age_hours?: number | null;
  threshold_hours?: number;
  detail?: string;
}

export interface LearningHealthResponse {
  overall?: "ok" | "warn" | "dead";
  checked_at?: string | null;
  items?: LearningHealthItem[];
  error?: string;
}

/** 真实闭环健康（/api/learning/health） */
export async function getLearningHealth(): Promise<LearningHealthResponse> {
  return apiRequest<LearningHealthResponse>("/learning/health");
}

/** Wisdom 生命周期五步统计 */
export interface WisdomStatsResponse {
  steps?: {
    extract?: { total: number; by_outcome?: Record<string, number>; latest?: string | null };
    gate?: { total: number; latest?: string | null };
    inject?: { total: number; cumulative_count?: number };
    validate?: { total: number; quality_hit_count?: number };
    retire?: { total: number };
  };
  rates?: {
    usage_rate?: number;
    effect_rate?: number;
    retire_rate?: number;
  };
  slot_budget?: {
    enabled: boolean;
    max_slots?: number;
    used?: number;
    note?: string;
  };
  retrieval?: {
    ready?: boolean;
    embedding_model?: string | null;
    total_documents?: number;
    trading_wisdom_docs?: number;
    trading_wisdom_last_indexed?: string | null;
  };
  error?: string;
}

/** Wisdom 生命周期五步真实计数（/api/learning/wisdom/stats） */
export async function getWisdomStats(): Promise<WisdomStatsResponse> {
  return apiRequest<WisdomStatsResponse>("/learning/wisdom/stats");
}

/** RAG 知识库状态（/api/rag/stats） */
export interface RagCollection {
  doc_count?: number;
  last_indexed?: string | null;
}

export interface RagStatsResponse {
  ready?: boolean;
  degraded?: boolean;
  embedding_model?: string | null;
  persist_dir?: string;
  collections?: Record<string, RagCollection>;
  error?: string;
}

/** RAG 知识库统计（/api/rag/stats） */
export async function getRagStats(): Promise<RagStatsResponse> {
  return apiRequest<RagStatsResponse>("/rag/stats");
}

/** RAG 健康（/api/rag/health） */
export interface RagHealthResponse {
  status?: string;
  ready?: boolean;
  degraded?: boolean;
  total_documents?: number;
  embedding_model?: string | null;
  error?: string;
}

export async function getRagHealth(): Promise<RagHealthResponse> {
  return apiRequest<RagHealthResponse>("/rag/health");
}

/** LearningLoop 5 job 状态（/api/learning/loop/status，实测结构） */
export interface LoopStatusResponse {
  enabled?: boolean;
  paused?: boolean;
  registered?: boolean;
  /** job_id → 间隔秒数 */
  intervals?: Record<string, number>;
  /** job_id → 上次 tick 时间（ISO） */
  last_tick_at?: Record<string, string | null>;
  /** job_id → 下次 tick 时间（ISO） */
  next_tick_at?: Record<string, string | null>;
  last_coord_action?: {
    timestamp?: string | null;
    trigger_evolution?: boolean;
    trigger_drl_retrain?: boolean;
    trigger_kelly_update?: boolean;
    reasons?: string[];
    triggered_jobs?: string[];
    skipped_reasons?: string[];
  };
  error?: string;
}

export async function getLoopStatus(): Promise<LoopStatusResponse> {
  return apiRequest<LoopStatusResponse>("/learning/loop/status");
}
