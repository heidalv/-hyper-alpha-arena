/**
 * compute API — 算力中心客户端（v6 第十章）
 *
 * 数据源端点（后端 backend/api/compute_routes.py，prefix /api/compute）：
 *   GET  /api/compute/hardware          硬件实时（3s TTL）
 *   GET  /api/compute/gpu-env           torch/cu124 环境探活
 *   GET  /api/compute/evolution/status  因子进化状态 + 最近活动 + 配置
 *   GET  /api/compute/evolution/history 进化日志分页
 *   POST /api/compute/evolution/trigger 手动触发进化（写操作）
 *   GET  /api/compute/factors/active    活跃因子统计
 *   GET  /api/compute/tasks             任务队列聚合
 *   GET  /api/compute/config            配置项全量
 *   PUT  /api/compute/config            配置下发（写操作）
 *   GET  /api/compute/llm/status        本地 LLM 双机状态
 *   POST /api/compute/llm/check         后台连通性检查（写操作）
 *   GET  /api/compute/metrics?window=   历史指标（图表）
 * 已有 API 对接：
 *   GET  /api/factors/scalp-meta-report 元标签采集进度 + 最近报告
 *   POST /api/factors/scalp-meta/train  手动训练（写操作）
 *   GET  /api/rag/stats | /api/rag/health  RAG 状态
 *   POST /api/rag/reindex               RAG 重建（写操作）
 */
import { apiRequest } from "@/lib/api";

// ───────────────────────────── 类型定义 ─────────────────────────────

export interface GpuSnapshot {
  available: boolean;
  name?: string;
  driver?: string;
  mem_total_mb?: number;
  mem_used_mb?: number;
  mem_free_mb?: number;
  mem_available_budget_mb?: number;
  temp_c?: number;
  power_w?: number;
  power_limit_w?: number;
  utilization_pct?: number;
  alerts?: Array<{ severity: string; message: string }>;
  health?: string;
}

export interface HardwareSnapshot {
  ts?: number;
  gpu?: GpuSnapshot;
  cpu?: { logical_cores?: number; physical_cores?: number; usage_pct?: number; load_avg?: number | null };
  memory?: { total_gb?: number; used_gb?: number; available_gb?: number; usage_pct?: number };
  disk?: { disks?: Array<{ mount: string; total_gb: number; free_gb: number; usage_pct: number; low_space: boolean }> };
  error?: string;
}

export interface GpuEnvProbe {
  checked_at: number;
  available: boolean;
  broken?: boolean;
  version?: string;
  cuda_available?: boolean;
  cuda_version?: string;
  device_name?: string;
  error?: string;
  install_hint?: string;
  note?: string;
}

export interface EvolutionActivityItem {
  phase: string;
  action: string;
  factor_id: string;
  source: string;
  reason: string;
  created_at: string;
}

export interface ConfigItem {
  key: string;
  value: string | number | boolean;
  raw: string;
  default: string | number | boolean;
  type: "int" | "float" | "bool";
  min: number | null;
  max: number | null;
  group: string;
  label: string;
  desc: string;
  source: string;
  error: string | null;
}

export interface EvolutionStatus {
  running: boolean;
  last_error: string | null;
  last_activity_at: string | null;
  recent_activity: EvolutionActivityItem[];
  active_factors: { state_dist: Record<string, number>; total: number; error?: string };
  config: ConfigItem[];
  schedule: { daily_cron: string; hourly_weights: string };
}

export interface ActiveFactorItem {
  factor_id: string;
  source: string;
  state: string;
  icir: number | null;
  last_net_ic: number | null;
  current_weight: unknown;
  activated_at: string;
}

export interface TaskItem {
  id?: string | number;
  job_id?: string | number;
  type?: string;
  status?: string;
  created_at?: string;
  [k: string]: unknown;
}

export interface TasksResponse {
  total: number;
  active: number;
  evolution_running: boolean;
  jobs: TaskItem[];
}

export interface LlmStatus {
  config_id: number;
  enabled: boolean;
  note?: string;
  base_url?: string;
  host?: string;
  model?: string;
  config_found?: boolean;
  checking?: boolean;
  last_check?: {
    checked_at?: number;
    steps?: Array<{ name: string; ok: boolean; model?: string; elapsed?: number }>;
    passed?: number;
    total?: number;
    elapsed_sec?: number;
    skipped?: boolean;
    message?: string;
    error?: string;
  };
}

export interface MetricsSeries {
  window: string;
  resource: Record<string, Array<{ ts: number; value: number; extra?: unknown }>>;
  tasks: Record<string, Array<{ ts: number; value: number; extra?: unknown }>>;
  error?: string | null;
}

export interface ScalpMetaReport {
  progress?: {
    raw?: number;
    have?: number;
    need?: number;
    pos?: number;
    neg?: number;
    need_per_class?: number;
    percent?: number;
    ready?: boolean;
  };
  report?: {
    ts?: number;
    usable?: boolean;
    n_settled_raw?: number;
    n_settled?: number;
    pos?: number;
    neg?: number;
    status?: string;
    error?: string;
    auc?: number;
    oos_auc_lgbm?: number;
    [k: string]: unknown;
  } | null;
}

export interface RagStats {
  ready: boolean;
  degraded: boolean;
  degraded_query_count?: number;
  embedding_model?: string | null;
  persist_dir?: string;
  collections?: Record<string, { doc_count: number; last_indexed?: string | null }>;
}

export interface RagHealth {
  status?: string;
  ready: boolean;
  degraded: boolean;
  total_documents?: number;
  embedding_model?: string | null;
}

// ───────────────────────────── API 函数 ─────────────────────────────

/** 硬件资源实时快照（3s TTL 缓存） */
export function getHardware(): Promise<HardwareSnapshot> {
  return apiRequest<HardwareSnapshot>("/compute/hardware", { timeout: 15000 });
}

/** torch / CUDA 环境探活（损坏时如实返回 degraded） */
export function getGpuEnv(): Promise<GpuEnvProbe> {
  return apiRequest<GpuEnvProbe>("/compute/gpu-env", { timeout: 15000 });
}

/** 因子进化状态 + 最近活动 + 配置生效值 */
export function getEvolutionStatus(): Promise<EvolutionStatus> {
  return apiRequest<EvolutionStatus>("/compute/evolution/status", { timeout: 15000 });
}

/** 进化日志分页 */
export function getEvolutionHistory(page = 1, pageSize = 20, action?: string): Promise<{
  total: number; page: number; page_size: number; records: EvolutionActivityItem[]; error?: string;
}> {
  const sp = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  if (action) sp.set("action", action);
  return apiRequest(`/compute/evolution/history?${sp.toString()}`, { timeout: 15000 });
}

/** 手动触发因子进化（后台线程，单飞锁） */
export function triggerEvolution(): Promise<{ success: boolean; message: string; running?: boolean }> {
  return apiRequest("/compute/evolution/trigger", { method: "POST", timeout: 15000 });
}

/** 活跃因子统计 + Top 因子 */
export function getActiveFactors(top = 10): Promise<{
  stats: { state_dist: Record<string, number>; total: number; error?: string };
  top_factors: ActiveFactorItem[]; error?: string;
}> {
  return apiRequest(`/compute/factors/active?top=${top}`, { timeout: 15000 });
}

/** 任务队列聚合 */
export function getTasks(limit = 20): Promise<TasksResponse> {
  return apiRequest(`/compute/tasks?limit=${limit}`, { timeout: 15000 });
}

/** 全量配置项 */
export function getConfigs(): Promise<{ configs: ConfigItem[] }> {
  return apiRequest("/compute/config", { timeout: 15000 });
}

/** 配置下发（后端校验 + 写覆盖文件 + 注入 env） */
export function putConfigs(payload: Record<string, unknown>): Promise<{
  ok: boolean; applied?: Array<{ key: string; value: unknown; source: string }>;
  errors?: Record<string, string>; message?: string;
}> {
  return apiRequest("/compute/config", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    timeout: 15000,
  });
}

/** 本地 LLM 双机状态 */
export function getLlmStatus(): Promise<LlmStatus> {
  return apiRequest<LlmStatus>("/compute/llm/status", { timeout: 15000 });
}

/** 后台触发 LLM 连通性检查 */
export function triggerLlmCheck(): Promise<{ success: boolean; checking: boolean; message: string }> {
  return apiRequest("/compute/llm/check", { method: "POST", timeout: 15000 });
}

/** 历史指标（图表数据源） */
export function getMetrics(window: "1h" | "24h" | "7d" | "30d" = "24h"): Promise<MetricsSeries> {
  return apiRequest<MetricsSeries>(`/compute/metrics?window=${window}`, { timeout: 15000 });
}

/** 元标签采集进度 + 最近训练报告（已有 API） */
export function getScalpMetaReport(): Promise<ScalpMetaReport> {
  return apiRequest<ScalpMetaReport>("/factors/scalp-meta-report", { timeout: 30000 });
}

/** 手动触发元标签训练（已有 API） */
export function triggerScalpMetaTrain(): Promise<Record<string, unknown>> {
  return apiRequest("/factors/scalp-meta/train", { method: "POST", timeout: 15000 });
}

/** RAG 状态（已有 API） */
export function getRagStats(): Promise<RagStats> {
  return apiRequest<RagStats>("/rag/stats", { timeout: 15000 });
}

/** RAG 健康（已有 API） */
export function getRagHealth(): Promise<RagHealth> {
  return apiRequest<RagHealth>("/rag/health", { timeout: 15000 });
}

/** RAG 全量重建（已有 API） */
export function triggerRagReindex(): Promise<Record<string, unknown>> {
  return apiRequest("/rag/reindex", { method: "POST", timeout: 30000 });
}
