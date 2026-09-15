/**
 * Shared formatting helpers (pure functions, no React, no side effects).
 *
 * Consolidated from previously duplicated local helpers across the app.
 * These are deterministic string formatters; keep them locale-stable.
 *
 * [F195 2026-09-15] **所有格式化函数必须是"全函数"（total）：null/undefined/NaN → "—"**。
 *
 * 现场：`/arbitrage/lanes` 刷新即崩，报的是 DOM 层
 * `NotFoundError: Failed to execute 'insertBefore' ... not a child of this node` ✗。
 * 真实原因在浏览器里才看得到：
 * ```
 * TypeError: Cannot read properties of null (reading 'toLocaleString')
 *     at fmtNum (chunk ...:1:5739)
 *     at Array.map (<anonymous>)
 * ```
 * 即**某个表格行把 null 传给了 `fmtNum`** ⇒ 该行渲染抛错 ⇒ 整页被错误边界接管；
 * 而 React 在恢复过程中做 DOM 迁移失败，最终暴露成那句 `insertBefore` ✗✗
 * （**所以报错文案与根因完全不是一回事** —— 只看文案会一直查 DOM 结构）。
 *
 * 为什么修在这里而不是只修那一个调用点：
 *   这些函数被 ~100 处调用，且参数几乎都来自接口字段（**接口字段为 null 是常态**：
 *   车道没数据、账户没分配、成交为 0 等）⇒ 任何一处漏判都会让**整页**不可用 ✗。
 *   `fmtTime` 早就是全函数（见下），其余四个不是 —— 这种"一半全函数"的接口最危险 ✗。
 *   统一成"缺值渲染 —"既不会崩，也符合界面语义 ✓。
 *
 * 注意：签名**有意放宽**为 `number | null | undefined`。这会放弃一部分编译期的
 * "不许传 null" 压力，但换到的是"任意字段缺失都不会白屏"；对这个项目（数据驱动、
 * 字段可空、静态导出 + 客户端 hydrate）这是正确的取舍 ✓（与 `fmtTime` 一致 ✓）。
 */

function isNum(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

/**
 * Signed money: "+$1,234.56" for non-negative values, "-$1,234.56" for negative.
 * Matches the dashboard KPI / P&L attribution behaviour.
 */
export function fmtMoney(v: number | null | undefined): string {
  if (!isNum(v)) return "—";
  return `${v >= 0 ? "+" : ""}$${v.toFixed(2)}`;
}

/**
 * Unsigned money: "$1,234.56" (no sign).
 */
export function fmtUsd(v: number | null | undefined): string {
  if (!isNum(v)) return "—";
  return `$${v.toFixed(2)}`;
}

/**
 * Percent: "12.34%" (value already scaled by 100).
 */
export function fmtPct(v: number | null | undefined, digits = 2): string {
  if (!isNum(v)) return "—";
  return `${v.toFixed(digits)}%`;
}

/**
 * Price with symbol-aware decimals (TickerBar behaviour):
 * BTC/ETH/SOL/BNB → 2 decimals, every other symbol → 4 decimals.
 */
const HIGH_PRECISION_SYMBOLS = new Set(["BTC", "ETH", "SOL", "BNB"]);
export function fmtPrice(symbol: string, price: number | null | undefined): string {
  if (!isNum(price)) return "—";
  const digits = HIGH_PRECISION_SYMBOLS.has(symbol) ? 2 : 4;
  return price.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

/**
 * Thousands-separated number, e.g. "12,345.67".
 */
export function fmtNum(v: number | null | undefined, digits = 2): string {
  if (!isNum(v)) return "—";
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
