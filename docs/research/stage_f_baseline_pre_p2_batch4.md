# Stage F 滚动监控报告

生成时间: 2026-04-22T03:59:22.374691+00:00

## KPI 当前值

| KPI | 值 | 状态 | 说明 |
|---|---|---|---|
| sl_trigger_rate_7d | 0.25 | **TRIP** | 0.25 > 0.2 (hard_above) |
| avg_leverage_7d | 10.77 | **OK** | 10.77 |
| bucket_concurrency_peak | 0 | **OK** | 0 |
| missing_nature_ratio_24h | 0.0 | **OK** | 0.0 |
| xpl_trade_count_7d | 2 | **TRIP** | 2 < 3 (hard_below) |
| cum_pnl_pct_7d | -0.0003 | **OK** | -0.0003 |
| heartbeat_hours | 9.67 | **TRIP** | 9.67 > 6 (hard_above) |

## 熔断判定
**已触发熔断**：sl_trigger_rate_7d, xpl_trade_count_7d, heartbeat_hours

[DRY-RUN] 未写入 flag 文件。实际运行时会落 `data\stage_f_rollback.flag`。