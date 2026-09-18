// 平仓原因中文化（2026-09-07）
// 后端 close_reason 是机器码（逻辑/统计用），展示层统一走这里翻译成中文。
// 静态码精确匹配 + 动态码前缀匹配（原因里嵌了百分比/时长等变量）。

const EXACT: Record<string, string> = {
  // 基础
  sl: "止损平仓",
  tp: "止盈平仓",
  manual: "手动平仓",
  trailing: "移动止损",
  liquidation: "强制平仓",
  // 保本/分批止盈
  breakeven_tp: "保本止盈",
  breakeven_sl: "保本止损",
  staged_tp1: "分批止盈·首段",
  staged_tp2: "分批止盈·二段",
  staged_tp3: "分批止盈·三段",
  staged_tp1_single: "止盈全平",
  staged_tp2_clear: "分批止盈清仓",
  // 时间类
  max_hold_timeout: "到期强平（持仓上限）",
  hold_timeout_review: "持仓超时AI复审",
  timeout_close: "超时平仓",
  expired: "过期平仓",
  lifecycle_time_decay: "生命周期到期",
  // 论题/主脑
  thesis_should_close: "论题离场（主脑判定）",
  thesis_invalidation: "论题失效（打穿失效价）",
  thesis_long_propagate: "长线传导离场",
  // 回撤/无进展
  profit_drawdown_full: "利润回撤全平",
  profit_drawdown_reduce: "利润回撤减仓",
  no_progress: "无进展离场",
  early_no_progress: "早期无进展离场",
  // 总控/风控
  master_close: "总控平仓",
  master_close_tiny_loss: "总控微亏平仓",
  master_reduce_min_loss: "总控减仓",
  risk_reduce: "风控减仓",
  // 其他
  auto_close: "自动平仓",
  partial_close: "部分平仓",
  dust_cleanup: "零碎仓位清理",
  funding_exit: "资金费率平仓",
  funding_take: "资金费率止盈",
  basis_close: "基差平仓",
  rebalance: "再平衡",
  profit_take: "止盈",
  loss_cut: "止损",
  "trend_review_reduce_30%": "趋势复审减仓30%",
  "trend_review_reduce_50%": "趋势复审减仓50%",
  "trend_review_reduce_70%": "趋势复审减仓70%",
  trend_review_close: "趋势复审清仓",
  master_running_reduce: "总控运行中减仓",
  master_running_close: "总控运行中平仓",
  circuit_breaker: "熔断平仓",
  daily_loss_limit: "日亏损限额",
  forced_liquidation: "强平",
  trailing_stop: "追踪止损",
  signal_exit: "信号退出",
  reversal: "反向平仓",
  // [2026-09-19 轮104] 事故回滚标记：BTC #4712 幽灵止盈（TP 方向反转）的平仓单
  // 被置为 cancelled 并打上这个 close_reason；展示层要让人一眼看出"这笔不算数"，
  // 而不是把机器码原样抛到界面上。
  rotation104_phantom_tp_rollback: "已撤销·幽灵止盈回滚",
};

// 动态码：前缀 → 中文（顺序即优先级，先匹配先生效）
const PREFIX: Array<[string, string]> = [
  ["long_trend_v2:极端回撤≥80%", "长线·极端回撤全平"],
  ["long_trend_v2:极端回撤≥60%", "长线·极端回撤减半"],
  ["long_trend_v2:结构破坏", "长线·结构破坏离场"],
  ["long_trend_v2:Chandelier", "长线·吊灯止损"],
  ["long_trend_v2:no_progress", "长线·无进展离场"],
  ["long_trend_v2:结构目标达成", "长线·结构目标减半"],
  ["long_trend_v2:新高加仓", "长线·新高加仓"],
  ["long_trend_v2:early_no_progress", "长线·早期无进展"],
  ["midlong:no_progress", "中线·无进展离场"],
  ["midlong:trend_invalidation", "中线·趋势反转离场"],
  ["midlong:swing_invalidation", "中线·波段反转离场"],
  ["exit_policy:sl_pct", "固定止损"],
  ["exit_policy:tp_pct", "固定止盈"],
  ["exit_policy:time_limit", "限时退出"],
  ["exit_policy:min_roi_decay", "ROI 衰减退出"],
  ["scalp_review_time_decay", "短线·超时退出"],
  ["scalp_review_trailing", "短线·移动止盈"],
  ["scalp_review", "短线·复审退出"],
];

/** 把后端 close_reason 机器码翻译成中文。未知码原样返回（不丢信息）。 */
export function formatCloseReason(reason: string | null | undefined): string {
  if (!reason) return "—";
  const r = String(reason).trim();
  if (!r) return "—";
  const exact = EXACT[r];
  if (exact) return exact;
  for (const [prefix, label] of PREFIX) {
    if (r.startsWith(prefix)) return label;
  }
  // 含中文的（如 long_trend_v2 动态原因）去掉引擎前缀直接展示
  const colon = r.indexOf(":");
  if (colon > 0 && /[一-鿿]/.test(r.slice(colon + 1))) {
    return r.slice(colon + 1);
  }
  return r;
}
