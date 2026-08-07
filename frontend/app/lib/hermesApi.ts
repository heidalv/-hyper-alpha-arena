/**
 * Hermes 自进化系统 API 客户端
 * 端点: /api/hermes/*
 */

const BASE =
  `${(import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, '') || '/api'}/hermes`;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(text || `HTTP ${res.status}`);
  }
  return res.json();
}

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

export interface WisdomRecord {
  id: number;
  proposal_id: number;
  outcome: string;
  focus: string;
  market_condition: string;
  param_key: string;
  param_direction: string;
  param_delta_pct: number;
  pnl_impact: number;
  win_rate_delta: number;
  confidence: number;
  created_at: string;
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

export interface PromptVersion {
  id: number;
  task_id: string;
  version: string;
  change_type: string;
  change_summary: string;
  proposals_generated: number;
  avg_improved_rate: number;
  avg_degraded_rate: number;
  avg_quality_score: number;
  status: string;
  created_at: string;
  activated_at: string;
}

export interface ABTest {
  id: number;
  task_id: string;
  version_a: string;
  version_b: string;
  proposals_a: number;
  proposals_b: number;
  improved_rate_a: number;
  improved_rate_b: number;
  winner: string;
  status: string;
  started_at: string;
  concluded_at: string;
}

export interface ArchitectureProposal {
  id: number;
  title: string;
  category: string;
  description: string;
  evidence_patterns: string;
  feasibility: string;
  expected_impact: string;
  status: string;
  created_at: string;
}

export interface GenesisCandidate {
  id: number;
  variant_name: string;
  template_seed: string;
  paper_status: string;
  paper_pnl: number;
  paper_win_rate: number;
  paper_trades: number;
  paper_days: number;
  viability_score: number;
  created_at: string;
  validated_at: string;
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

export interface HermesDashboard {
  maturity: MaturityScore;
  schedule?: HermesTaskSchedule[];
  l1_wisdom: { total_records: number; patterns: number };
  l2_prompt: { active_versions: number; running_ab_tests: number };
  l3_architecture: Record<string, number>;
  l4_genesis: Record<string, number>;
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

export async function getHermesDashboard(): Promise<HermesDashboard> {
  return request("/dashboard");
}

export async function getHermesMaturity(): Promise<MaturityScore> {
  return request("/maturity");
}

export async function getHermesHealth(): Promise<HermesHealth> {
  return request("/health");
}

export async function getHermesSchedule(): Promise<{ tasks: HermesTaskSchedule[] }> {
  return request("/schedule");
}

export async function getHermesWisdom(
  limit = 20
): Promise<{ records: WisdomRecord[]; total: number }> {
  return request(`/wisdom?limit=${limit}`);
}

export async function getHermesPatterns(
  minSamples = 2
): Promise<{ patterns: PatternItem[] }> {
  return request(`/patterns?min_samples=${minSamples}`);
}

export async function getHermesPrompts(): Promise<{
  versions: PromptVersion[];
  ab_tests: ABTest[];
}> {
  return request("/prompts");
}

export async function getHermesArchitecture(
  status = "all"
): Promise<{ proposals: ArchitectureProposal[]; stats: Record<string, number> }> {
  return request(`/architecture?status=${status}`);
}

export async function getHermesGenesis(): Promise<{
  candidates: GenesisCandidate[];
  stats: Record<string, number>;
}> {
  return request("/genesis");
}

export async function runHermesTask(
  taskName: string
): Promise<{ ok: boolean; task?: string; result?: unknown; error?: string }> {
  return request(`/run/${taskName}`, { method: "POST" });
}

/** L3 接受架构提案 → 提交 RuntimeGovernor patch */
export async function acceptHermesArchitecture(proposalId: number): Promise<{
  ok?: boolean;
  governor_patch_id?: string;
  error?: string;
}> {
  return request(`/architecture/${proposalId}/accept`, { method: "POST" });
}

/** L3 拒绝架构提案 */
export async function rejectHermesArchitecture(
  proposalId: number,
  reason = ""
): Promise<{ ok?: boolean; error?: string }> {
  const q = reason ? `?reason=${encodeURIComponent(reason)}` : "";
  return request(`/architecture/${proposalId}/reject${q}`, { method: "POST" });
}

/** L4 validated 候选 → Governor 晋升 live */
export async function promoteHermesGenesis(candidateId: number): Promise<{
  ok?: boolean;
  strategy_id?: string;
  governor_patch_id?: string;
  error?: string;
}> {
  return request(`/genesis/${candidateId}/promote`, { method: "POST" });
}

/** 阻断模式学习统计 */
export async function getHermesBlockPatterns(): Promise<{ stats: Record<string, Record<string, number>> }> {
  return request("/block-patterns");
}
