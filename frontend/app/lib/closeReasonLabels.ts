/** 成交记录 close_reason → 中文操作标签（模拟盘 / 全自动共用） */

const CLOSE_REASON_LABELS: Record<string, string> = {
  tp: '止盈平仓',
  sl: '止损平仓',
  manual: '手动平仓',
  trailing: '追踪止盈',
  liquidation: '爆仓',
  partial_tp: '部分止盈',
  manual_partial: '手动减仓',
  tp_cleanup: '微仓清理',
  safety_tp: '安全止盈',
  ai_reverse: 'AI反向开仓',
  ai_take_profit: 'AI止盈',
  ai_cut_loss: 'AI止损',
  ai_close: 'AI平仓',
  breakeven_sl: '保本止损',
  breakeven_tp: '保本止盈',
  master_running: '总控决策',
  master_running_reduce: '总控减仓',
  master_running_close: '总控平仓',
  master_running_close_tiny: '总控微仓清理',
  master_defensive: '防守平仓',
  master_defensive_reduce: '防守减仓',
  master_defensive_close_tiny: '防守微仓清理',
  defensive_close: '防守平仓',
  defensive_reduce: '防守减仓',
  defensive_close_tiny: '防守微仓清理',
  max_hold_timeout: '持仓超时',
  hold_timeout: '持仓超时',
  hold_timeout_review: '持仓超时复查',
  trend_review: '趋势复查',
  trend_review_close: '趋势复查平仓',
  trend_review_reduce: '趋势复查减仓',
  scalp_fast_review: '短线快速复查',
  trend_reversal: '趋势反转',
  trend_weakening: '趋势减弱',
  structure_break: '结构破坏',
  drawdown_protection: '回撤保护',
  emergency_drawdown: '紧急回撤保护',
  tp_target: '目标止盈',
  profit_lock_1: '分批锁利①',
  profit_lock_2: '分批锁利②',
  profit_lock_3: '分批锁利③',
  profit_lock: '分批锁利',
  breakeven_push: '保本推进',
  breakeven_fallback: '保本保护',
  tight_trail_90: '紧追踪',
  tight_trail: '紧追止损',
  profit_drawdown_partial: '盈利回撤部分平仓',
  profit_drawdown_full: '盈利回撤全部平仓',
  trailing_hit: '追踪止盈触发',
  dust_cleanup: '碎仓清理',
  profit_stage_close: '阶段止盈平仓',
  nature_trailing_hit: '性质追踪止盈',
  health_force_reduce: '健康强制减仓',
  tp_staged: '分批止盈',
  nature_tp_staged: '性质分批止盈',
  stop_loss: '止损',
};

const CLOSE_REASON_COLORS: Record<string, string> = {
  tp: 'bg-green-100 text-green-700 border-green-300 dark:bg-green-950 dark:text-green-300',
  sl: 'bg-red-100 text-red-700 border-red-300 dark:bg-red-950 dark:text-red-300',
  manual: 'bg-blue-100 text-blue-700 border-blue-300 dark:bg-blue-950 dark:text-blue-300',
  trailing: 'bg-green-100 text-green-700 border-green-300 dark:bg-green-950 dark:text-green-300',
  liquidation: 'bg-red-100 text-red-800 border-red-400 dark:bg-red-950 dark:text-red-300',
  partial_tp: 'bg-emerald-50 text-emerald-700 border-emerald-300 dark:bg-emerald-950 dark:text-emerald-300',
  manual_partial: 'bg-sky-50 text-sky-700 border-sky-300 dark:bg-sky-950 dark:text-sky-300',
  tp_cleanup: 'bg-gray-100 text-gray-600 border-gray-300 dark:bg-gray-800 dark:text-gray-400',
  ai_reverse: 'bg-violet-100 text-violet-700 border-violet-300 dark:bg-violet-950 dark:text-violet-300',
  ai_take_profit: 'bg-green-100 text-green-700 border-green-300 dark:bg-green-950 dark:text-green-300',
  ai_cut_loss: 'bg-red-100 text-red-700 border-red-300 dark:bg-red-950 dark:text-red-300',
  ai_close: 'bg-violet-100 text-violet-700 border-violet-300 dark:bg-violet-950 dark:text-violet-300',
  breakeven_tp: 'bg-green-100 text-green-700 border-green-300 dark:bg-green-950 dark:text-green-300',
  safety_tp: 'bg-green-100 text-green-700 border-green-300 dark:bg-green-950 dark:text-green-300',
  master_running: 'bg-violet-100 text-violet-700 border-violet-300 dark:bg-violet-950 dark:text-violet-300',
  master_running_reduce: 'bg-violet-100 text-violet-700 border-violet-300 dark:bg-violet-950 dark:text-violet-300',
  master_running_close: 'bg-violet-100 text-violet-700 border-violet-300 dark:bg-violet-950 dark:text-violet-300',
  master_running_close_tiny: 'bg-gray-100 text-gray-600 border-gray-300 dark:bg-gray-800 dark:text-gray-400',
  master_defensive: 'bg-orange-100 text-orange-700 border-orange-300 dark:bg-orange-950 dark:text-orange-300',
  master_defensive_reduce: 'bg-orange-100 text-orange-700 border-orange-300 dark:bg-orange-950 dark:text-orange-300',
  master_defensive_close_tiny: 'bg-gray-100 text-gray-600 border-gray-300 dark:bg-gray-800 dark:text-gray-400',
  defensive_close: 'bg-orange-100 text-orange-700 border-orange-300 dark:bg-orange-950 dark:text-orange-300',
  defensive_reduce: 'bg-orange-100 text-orange-700 border-orange-300 dark:bg-orange-950 dark:text-orange-300',
  defensive_close_tiny: 'bg-gray-100 text-gray-600 border-gray-300 dark:bg-gray-800 dark:text-gray-400',
  max_hold_timeout: 'bg-amber-100 text-amber-700 border-amber-300 dark:bg-amber-950 dark:text-amber-300',
  hold_timeout: 'bg-amber-100 text-amber-700 border-amber-300 dark:bg-amber-950 dark:text-amber-300',
  hold_timeout_review: 'bg-amber-100 text-amber-700 border-amber-300 dark:bg-amber-950 dark:text-amber-300',
  trend_review: 'bg-indigo-100 text-indigo-700 border-indigo-300 dark:bg-indigo-950 dark:text-indigo-300',
  trend_review_close: 'bg-indigo-100 text-indigo-700 border-indigo-300 dark:bg-indigo-950 dark:text-indigo-300',
  trend_review_reduce: 'bg-indigo-100 text-indigo-700 border-indigo-300 dark:bg-indigo-950 dark:text-indigo-300',
  scalp_fast_review: 'bg-cyan-100 text-cyan-700 border-cyan-300 dark:bg-cyan-950 dark:text-cyan-300',
  drawdown_protection: 'bg-amber-100 text-amber-700 border-amber-300 dark:bg-amber-950 dark:text-amber-300',
  emergency_drawdown: 'bg-red-100 text-red-700 border-red-300 dark:bg-red-950 dark:text-red-300',
  tp_target: 'bg-green-100 text-green-700 border-green-300 dark:bg-green-950 dark:text-green-300',
  profit_lock_1: 'bg-emerald-50 text-emerald-700 border-emerald-300 dark:bg-emerald-950 dark:text-emerald-300',
  profit_lock_2: 'bg-emerald-50 text-emerald-700 border-emerald-300 dark:bg-emerald-950 dark:text-emerald-300',
  profit_lock_3: 'bg-emerald-50 text-emerald-700 border-emerald-300 dark:bg-emerald-950 dark:text-emerald-300',
  breakeven_push: 'bg-sky-50 text-sky-700 border-sky-300 dark:bg-sky-950 dark:text-sky-300',
  breakeven_fallback: 'bg-sky-50 text-sky-700 border-sky-300 dark:bg-sky-950 dark:text-sky-300',
  tight_trail_90: 'bg-violet-100 text-violet-700 border-violet-300 dark:bg-violet-950 dark:text-violet-300',
  tight_trail: 'bg-violet-100 text-violet-700 border-violet-300 dark:bg-violet-950 dark:text-violet-300',
  profit_drawdown_partial: 'bg-amber-100 text-amber-800 border-amber-300 dark:bg-amber-950 dark:text-amber-200',
  profit_drawdown_full: 'bg-amber-100 text-amber-800 border-amber-400 dark:bg-amber-950 dark:text-amber-200',
  trailing_hit: 'bg-green-100 text-green-700 border-green-300 dark:bg-green-950 dark:text-green-300',
  dust_cleanup: 'bg-gray-100 text-gray-600 border-gray-300 dark:bg-gray-800 dark:text-gray-400',
};

function matchDynamicCloseReason(reason: string): string | null {
  const m1 = reason.match(/^trend_review_reduce_(\d+)%$/);
  if (m1) return `趋势复查减仓${m1[1]}%`;

  const m2 = reason.match(/^tp_staged_(\d+)$/);
  if (m2) return `分批止盈${m2[1]}`;

  const m3 = reason.match(/^nature_tp_staged_(\d+)$/);
  if (m3) return `性质分批止盈${m3[1]}`;

  const m4 = reason.match(/^profit_lock_(\d+)$/);
  if (m4) return `分批锁利${m4[1]}`;

  return null;
}

/** 将 close_reason 转为中文操作名；pnl 用于 sl / breakeven_sl 语义区分 */
export function getCloseReasonLabel(reason: string, pnl?: number | null): string {
  const key = (reason || '').trim();
  if (!key) return '平仓';

  if (key === 'sl') {
    const p = pnl ?? 0;
    if (p > 0) return '保本止盈';
    if (p === 0) return '保本平仓';
    return '止损平仓';
  }
  if (key === 'breakeven_sl') {
    const p = pnl ?? 0;
    return p >= 0 ? '保本止盈' : '保本止损';
  }

  const dynamic = matchDynamicCloseReason(key);
  if (dynamic) return dynamic;

  return CLOSE_REASON_LABELS[key] || key.replace(/_/g, ' ');
}

export function getCloseReasonColorClass(reason: string, pnl?: number | null): string {
  const key = (reason || '').trim();
  if (key === 'sl') {
    return (pnl ?? 0) >= 0
      ? 'bg-green-100 text-green-700 border-green-300 dark:bg-green-950 dark:text-green-300'
      : CLOSE_REASON_COLORS.sl;
  }
  if (key === 'breakeven_sl') {
    return (pnl ?? 0) >= 0
      ? 'bg-green-100 text-green-700 border-green-300 dark:bg-green-950 dark:text-green-300'
      : 'bg-amber-100 text-amber-700 border-amber-300 dark:bg-amber-950 dark:text-amber-300';
  }
  if (key.startsWith('trend_review')) {
    return CLOSE_REASON_COLORS.trend_review;
  }
  if (key.startsWith('hold_timeout')) {
    return CLOSE_REASON_COLORS.hold_timeout_review;
  }
  return CLOSE_REASON_COLORS[key] || 'text-gray-600 border-gray-300';
}
