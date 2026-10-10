/**
 * 学习进化 · 车道视图 API 客户端（[2026-10-10 前端升级]）
 *
 * 对应后端三块只读视图 + 两个安全动作（全部复用既有写入接口，不新增权限面）：
 *   GET  /api/learning/lane/inlet       高频入口进度（PP 往返 → 主脑）
 *   GET  /api/learning/lane/advisor     车道顾问（决策时间轴 + 白名单现值 + 待判/已判）
 *   GET  /api/learning/lane/kb/cards    知识卡片文件 + 索引状态 + 按车道可见数
 *   POST /api/learning/loop/trigger/{job}  手动触发某个 tick（这里只用 hft_brain）
 *   POST /api/rag/reindex              重建索引（这里只用 knowledge_cards）
 *
 * 约定：相对路径不带 `/api`（`apiRequest` 会拼 `getBackendUrl() + "/api"`），
 * 与 `intelligentLearningApi.ts` 一致；鉴权/Bearer/401 刷新都由 `apiRequest` 负责。
 */
import { apiRequest } from "./api";

// ─────────────────────────────────────────────
//  ① 高频入口
// ─────────────────────────────────────────────

export type InletHealth = "ok" | "warn" | "bad" | "off" | "unknown";

export interface InletCursor {
  present: boolean;
  path?: string;
  ts?: number | null;
  ts_iso?: string | null;
  id?: number | null;
  ingested_total?: number | null;
  skipped_total?: number | null;
  failed_total?: number | null;
  applied_at?: number | null;
  applied_at_iso?: string | null;
  error?: string;
}

export interface InletLedger {
  roundtrips_since_era?: number | null;
  last_close_ts?: string | null;
  legs_last_10min?: number | null;
  era_start_ts?: number;
  era_start_iso?: string | null;
  error?: string | null;
}

export interface InletTick {
  job_id?: string;
  registered_in_loop?: boolean;
  has_heartbeat?: boolean;
  last_tick_at?: string | null;
  next_tick_at?: string | null;
  interval_sec?: number | null;
  loop_enabled?: boolean;
  loop_paused?: boolean;
  last_extra?: Record<string, unknown>;
  p50_ms?: number | null;
  p95_ms?: number | null;
  error?: string;
}

export interface InletStatus {
  lane_id: string;
  strategy_id: string;
  tier: string;
  nature: string;
  enabled: boolean;
  isolation_on?: boolean | null;
  backend_has_tick?: boolean;
  cursor: InletCursor;
  ledger: InletLedger;
  budget: Record<string, unknown>;
  tick: InletTick;
  pending_from_cursor?: number | null;
  health: InletHealth;
  health_reason: string;
  as_of: string;
}

export function getInletStatus(): Promise<InletStatus> {
  return apiRequest<InletStatus>("/learning/lane/inlet", { timeout: 20000 });
}

// ─────────────────────────────────────────────
//  ② 车道顾问
// ─────────────────────────────────────────────

export type AdvisorDecision =
  | "applied"
  | "proposal_only"
  | "hold"
  | "rejected"
  | "blocked_by_gate"
  | "blocked_by_counterfactual"
  | "kb_unavailable"
  | "llm_invalid"
  | "not_due"
  | "disabled"
  | "no_whitelist"
  | "apply_failed"
  | "error"
  | "unknown";

export interface AdvisorJournalItem {
  ts?: number;
  ts_iso?: string | null;
  lane?: string;
  decision: AdvisorDecision;
  why: string;
  param?: string | null;
  old?: number | null;
  new?: number | null;
  cited?: string[];
  kb?: {
    degraded?: boolean;
    reason?: string;
    card_ids?: string[];
    cards?: number;
    lessons?: number;
  };
  gate?: { name?: string; passed?: boolean; why?: string } | null;
  evidence_digest?: Record<string, unknown>;
  raw_llm?: string;
}

export interface PendingVerdict {
  param?: string;
  old?: number;
  new?: number;
  rollback?: number;
  applied_at?: number;
  verdict_at?: number;
  verdict_metric?: string;
  reason?: string;
}

export interface VerdictRecord {
  ts?: number;
  param?: string;
  old?: number;
  new?: number;
  rollback?: number;
  metric?: string;
  before?: { n?: number; v?: number };
  after?: { n?: number; v?: number };
  verdict?: string;
  rolled_back?: boolean;
}

export interface AdvisorPayload {
  lane: string;
  journal: AdvisorJournalItem[];
  decision_counts: Record<string, number>;
  journal_present: boolean;
  whitelists: Record<string, Record<string, [number, number]>>;
  current: Record<string, Record<string, number | null>>;
  pending_verdicts: PendingVerdict[];
  pending_present: boolean;
  verdict_history: VerdictRecord[];
  sources: Record<string, string>;
  rules: Record<string, string>;
  as_of: string;
}

export async function getAdvisor(opts?: {
  lane?: string;
  limit?: number;
  includeRaw?: boolean;
}): Promise<AdvisorPayload> {
  const qs = new URLSearchParams();
  if (opts?.lane) qs.set("lane", opts.lane);
  qs.set("limit", String(opts?.limit ?? 50));
  qs.set("include_raw", opts?.includeRaw ? "1" : "0");
  return apiRequest<AdvisorPayload>(`/learning/lane/advisor?${qs.toString()}`, {
    timeout: 20000,
  });
}

// ─────────────────────────────────────────────
//  ③ 知识卡片
// ─────────────────────────────────────────────

export interface KnowledgeCard {
  id: string;
  title?: string;
  lanes?: string[];
  topics?: string[];
  source?: string;
  source_url?: string;
  claim?: string;
  not_when?: string;
  verified_in_repo?: string;
  text?: string;
}

export interface KbCardsPayload {
  file: {
    present: boolean;
    count: number;
    meta?: Record<string, unknown>;
    unverified?: Array<{ title?: string; why?: string }>;
    error?: string;
  };
  collection: {
    name: string;
    ready?: boolean | null;
    degraded?: boolean | null;
    doc_count?: number | null;
    last_indexed?: string | null;
    last_indexed_source?: string;
    /** 冷启动提示（RAG 未加载时非空）：文档会在第一次检索时载入 */
    note?: string;
    error?: string;
  };
  cards: KnowledgeCard[];
  by_lane: Record<string, number>;
  all_collections?: Record<string, { doc_count?: number | null; last_indexed?: string | null }>;
  warm?: boolean;
  as_of: string;
  error?: string;
}

/**
 * 知识卡片状态。
 *
 * ⚠️ `warm=1` 会触发 RAG 懒加载（bge-large ~1.3GB，实测 **26s**）——**别在首屏用**，
 * 只在用户点「载入卡片索引」时用；其余情况后端自己会在第一次检索时加载。
 */
export function getKbCards(warm = false): Promise<KbCardsPayload> {
  return apiRequest<KbCardsPayload>(
    `/learning/lane/kb/cards${warm ? "?warm=1" : ""}`,
    { timeout: warm ? 180000 : 20000 },
  );
}

// ─────────────────────────────────────────────
//  安全动作（复用既有写入接口）
// ─────────────────────────────────────────────

export interface LoopTriggerResult {
  ok: boolean;
  job?: string;
  elapsed_ms?: number;
  error?: string;
  valid?: string[];
  /** 该 tick 的 `_record_tick` 摘要（高频入口：scanned/ingested/failed/cursor） */
  extra?: Record<string, unknown>;
  ingested?: number;
  scanned?: number;
  skipped_rows?: number;
  failed?: number;
  cursor?: { ts?: number; id?: number };
}

/** 手动触发学习循环的某个 tick（这里只用于高频入口补扫）。 */
export function triggerLoopJob(job = "hft_brain"): Promise<LoopTriggerResult> {
  return apiRequest<LoopTriggerResult>(`/learning/loop/trigger/${job}`, {
    method: "POST",
    timeout: 120000,
  });
}

export interface LoopPauseResult {
  ok: boolean;
  paused: boolean;
}

export function setLoopPaused(paused: boolean): Promise<LoopPauseResult> {
  return apiRequest<LoopPauseResult>(
    `/learning/loop/${paused ? "pause" : "resume"}`,
    { method: "POST", timeout: 15000 }
  );
}

export interface RagReindexResult {
  source_type?: string;
  indexed?: number;
  file_count?: number;
  warning?: string;
  full_reindex?: boolean;
  counts?: Record<string, number>;
}

/** 重建卡片索引（静态文件源，走后端 index_knowledge_cards）。 */
export function reindexKnowledgeCards(): Promise<RagReindexResult> {
  return apiRequest<RagReindexResult>("/rag/reindex", {
    method: "POST",
    body: JSON.stringify({ source_type: "knowledge_cards" }),
    timeout: 180000,
  });
}
