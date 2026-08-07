# 中长线策略周度绩效报表（近 14 天）

生成时间: 2026-08-03T04:00:00

> 数据来源: paper_orders(平仓单pnl) + paper_positions + paper_funding_ledger + mlto_thesis_events。对应 04 综合方案 §3.5 / P3 证明闭环。

## P3 证明闭环 KPI

- 开仓数（窗口内）: 22  swing=11 / trend_follow=11
- NIBBLE/BUILD 事件: 2269 (NIBBLE=2269, BUILD=0)
- NIBBLE/BUILD→24h 开仓转化率: 20.3% (460/2269)
- 交易净盈亏: -8.75 | Funding 流水: +0.05 | **含费率净盈亏: -8.70**
- 最大同向名义: long=457 / short=720 | 最大净敞口名义=720 | 最大同向笔数 long=2 short=4

## swing
- 样本量（平仓单数）: 18
- 窗口内开仓数: 11
- 胜率: 55.6%
- 盈亏比 (profit factor): 0.86
- 毛利/毛亏: +6.26 / -7.29 | 净盈亏: -1.02
- 亏损全平后 24h 同向再开率: 0.0%（0/2） | 目标 ≤ 20%
- close_reason 分布:
  - master_running_reduce: 6 (33.3%)
  - master_running_close: 5 (27.8%)
  - dust_cleanup: 1 (5.6%)
  - ai_reverse: 1 (5.6%)
  - 分批TP#1 浮盈8.5%≥6.0%: 1 (5.6%)
  - 分批TP#1 浮盈8.8%≥6.0%: 1 (5.6%)
  - max_hold_timeout: 1 (5.6%)
  - profit_drawdown_partial: 1 (5.6%)
  - breakeven_tp: 1 (5.6%)
- 分档 TP 触达率 (tp_level_reached，基于开仓样本，非平仓单):
  - L0: 11 (100.0%)
  - L1: 0 (0.0%)
  - L2: 0 (0.0%)
  - L3: 0 (0.0%)

**可用定义达标情况**：
- 样本量>=40: [FAIL] (18)
- 胜率>=40%或盈亏比>=1.8: [PASS]
- 同向再开率<=20%: [PASS]

## trend_follow
- 样本量（平仓单数）: 39
- 窗口内开仓数: 11
- 胜率: 61.5%
- 盈亏比 (profit factor): 0.85
- 毛利/毛亏: +42.57 / -50.30 | 净盈亏: -7.73
- 亏损全平后 24h 同向再开率: 0.0%（0/2） | 目标 ≤ 20%
- close_reason 分布:
  - trend_review_reduce_40%: 9 (23.1%)
  - trend_review_close: 7 (17.9%)
  - trend_review_reduce_50%: 6 (15.4%)
  - master_running_reduce: 4 (10.3%)
  - trend_review_reduce_30%: 3 (7.7%)
  - emergency_drawdown: 3 (7.7%)
  - dust_cleanup: 2 (5.1%)
  - symbol_removed: 2 (5.1%)
  - staged_tp1: 2 (5.1%)
  - manual: 1 (2.6%)
- 分档 TP 触达率 (tp_level_reached，基于开仓样本，非平仓单):
  - L0: 9 (81.8%)
  - L1: 2 (18.2%)
  - L2: 0 (0.0%)
  - L3: 0 (0.0%)

**可用定义达标情况**：
- 样本量>=40: [FAIL] (39)
- 胜率>=40%或盈亏比>=1.8: [PASS]
- 同向再开率<=20%: [PASS]

## position
- 样本量: 0（暂无数据，可能是修复刚上线还未积累样本）

## 附件：Walk-Forward（代理趋势）

- 生成: 2026-07-31T09:21:55.977531+00:00
- BTC: OOS ret=2.81% Sharpe=0.39 MaxDD=24.20% DSR=0.46549176326774677
- ETH: OOS ret=1.88% Sharpe=-0.01 MaxDD=27.12% DSR=0.29210468592695493
- SOL: OOS ret=4.48% Sharpe=0.56 MaxDD=31.04% DSR=0.537939620573051
