/**
 * Shared formatting helpers (pure functions, no React, no side effects).
 *
 * Consolidated from previously duplicated local helpers across the app.
 * These are deterministic string formatters; keep them locale-stable.
 */

/**
 * Signed money: "+$1,234.56" for non-negative values, "-$1,234.56" for negative.
 * Matches the dashboard KPI / P&L attribution behaviour.
 */
export function fmtMoney(v: number): string {
  return `${v >= 0 ? "+" : ""}$${v.toFixed(2)}`;
}

/**
 * Unsigned money: "$1,234.56" (no sign).
 */
export function fmtUsd(v: number): string {
  return `$${v.toFixed(2)}`;
}

/**
 * Percent: "12.34%" (value already scaled by 100).
 */
export function fmtPct(v: number, digits = 2): string {
  return `${v.toFixed(digits)}%`;
}

/**
 * Price with symbol-aware decimals (TickerBar behaviour):
 * BTC/ETH/SOL/BNB → 2 decimals, every other symbol → 4 decimals.
 */
const HIGH_PRECISION_SYMBOLS = new Set(["BTC", "ETH", "SOL", "BNB"]);
export function fmtPrice(symbol: string, price: number): string {
  if (HIGH_PRECISION_SYMBOLS.has(symbol)) {
    return price.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  return price.toLocaleString("en-US", { minimumFractionDigits: 4, maximumFractionDigits: 4 });
}

/**
 * Thousands-separated number, e.g. "12,345.67".
 */
export function fmtNum(v: number, digits = 2): string {
  return v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

/**
 * Timestamp → "MM/DD HH:mm" in zh-CN (24h).
 *
 * This is the MOST COMMON implementation among the five previously duplicated
 * fmtTime helpers — ScheduleLogPanel, RAGStatusPanel and HealthChannelsPanel are
 * byte-for-byte identical to this (6 call sites). The divergences below are
 * intentionally NOT consolidated because they change the rendered string, so
 * their local helpers are kept in place:
 *   - CooldownMatrixPanel adds `second: "2-digit"` and renders "—" for falsy input.
 *   - DecisionChainPanel omits `hour12: false` and renders "—" for falsy input.
 * The base formatters never pass null/undefined; returning "—" here keeps the
 * widened signature total without changing any existing reached output.
 */
export function fmtTime(ts: string | number | Date | null | undefined): string {
  if (ts == null) return "—";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return String(ts);
  return d.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
}
