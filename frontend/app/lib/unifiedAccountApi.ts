/**
 * 统一账户 API 客户端 — AI 交易员 + 套利中心 共用入口（阶段 5.3）
 *
 * 对应后端 /api/unified-account/* 端点（unified_account_routes.py）
 * 双表共存归一化视图：AI 树（PaperBalance）+ 套利树（ArbitragePaperAccountDB）
 */

const BASE =
  `${(import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, '') || '/api'}/unified-account`;

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

// ════════════════════════════════════════════════════════
//  类型定义
// ════════════════════════════════════════════════════════

export type AccountScope = 'ai' | 'arbitrage';

export interface UnifiedPaperAccount {
  id: number;
  scope: AccountScope;
  source_table: string; // "paper_balances" / "arbitrage_paper_accounts"
  name: string;
  total_equity: number;
  available_balance: number;
  frozen_balance: number;
  unrealized_pnl: number;
  realized_pnl: number;
  total_fee_paid: number;
  initial_balance: number;
  status: string;
  owner_account_id: number | null;
  exchange: string | null;
  risk_profile: string | null;
}

export interface CombinedExposure {
  ai_equity: number;
  ai_frozen: number;
  ai_upnl: number;
  arbitrage_equity: number;
  arbitrage_frozen: number;
  arbitrage_upnl: number;
  total_equity: number;
  total_frozen: number;
  total_upnl: number;
  ai_account_id: number | null;
  arbitrage_account_id: number | null;
}

export interface FeeScheduleEntry {
  exchange: string;
  maker_fee_rate: number;
  taker_fee_rate: number;
  maker_fee_pct: number;
  taker_fee_pct: number;
  min_notional_usd: number;
  maintenance_margin_rate: number;
  maintenance_margin_pct: number;
  quantity_step: number;
}

export interface FeeScheduleResponse {
  default_exchange: string;
  exchanges: FeeScheduleEntry[];
}

export interface TransferResult {
  success: boolean;
  amount: number;
  from_scope: AccountScope;
  from_id: number;
  to_scope: AccountScope;
  to_id: number;
  error?: string;
}

// ════════════════════════════════════════════════════════
//  API 函数
// ════════════════════════════════════════════════════════

/** 列出所有 paper 账户（归一化视图） */
export async function listPaperAccounts(
  scope?: AccountScope,
  ownerAccountId?: number,
): Promise<{ accounts: UnifiedPaperAccount[]; count: number }> {
  const params = new URLSearchParams();
  if (scope) params.set('scope', scope);
  if (ownerAccountId != null) params.set('owner_account_id', String(ownerAccountId));
  const qs = params.toString();
  return request(`/list${qs ? `?${qs}` : ''}`);
}

/** 获取单个归一化账户视图 */
export async function getUnifiedAccount(
  scope: AccountScope,
  accountId: number,
): Promise<UnifiedPaperAccount> {
  return request(`/${scope}/${accountId}`);
}

/** 跨系统（AI + 套利）合并敞口 */
export async function getCombinedExposure(
  aiAccountId?: number,
  arbitrageAccountId?: number,
): Promise<CombinedExposure> {
  const params = new URLSearchParams();
  if (aiAccountId != null) params.set('ai_account_id', String(aiAccountId));
  if (arbitrageAccountId != null) params.set('arbitrage_account_id', String(arbitrageAccountId));
  const qs = params.toString();
  return request(`/exposure/combined${qs ? `?${qs}` : ''}`);
}

/** 费率表（fee_schedule_service 摘要） */
export async function getFeeSchedule(): Promise<FeeScheduleResponse> {
  return request('/fee-schedule');
}

/** 跨账户资金划转（记账层） */
export async function transferCapital(
  fromScope: AccountScope,
  fromId: number,
  toScope: AccountScope,
  toId: number,
  amount: number,
): Promise<TransferResult> {
  return request('/transfer', {
    method: 'POST',
    body: JSON.stringify({
      from_scope: fromScope,
      from_id: fromId,
      to_scope: toScope,
      to_id: toId,
      amount,
    }),
  });
}

// ════════════════════════════════════════════════════════
//  辅助函数
// ════════════════════════════════════════════════════════

/** 格式化美元金额 */
export function fmtUsd(val: number | null | undefined, digits: number = 2): string {
  const n = Number(val ?? 0);
  return Number.isFinite(n) ? `$${n.toFixed(digits)}` : '$0.00';
}

/** 格式化百分比（0.00005 → "0.005%"） */
export function fmtPct(rate: number | null | undefined, digits: number = 4): string {
  const n = Number(rate ?? 0);
  return Number.isFinite(n) ? `${(n * 100).toFixed(digits)}%` : '0%';
}
