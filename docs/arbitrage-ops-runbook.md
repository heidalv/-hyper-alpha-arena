# 套利系统运维 Runbook

## 1. 启动检查

```bash
# 确认 API 挂载
curl http://localhost:8000/api/arbitrage/status
curl http://localhost:8000/api/rebate/status

# 确认执行权威
curl http://localhost:8000/api/arbitrage/execution-authority
# 期望: authority=fullauto, qaa_arb_plugins_bootstrapped=false
```

## 2. 开启套利

1. 配置各交易所 API 凭证（Exchange Hub）
2. FullAuto 面板开启 **套利开关** (`arb_enabled`)
3. 确认环境变量 `FUNDING_ARB_ENABLED=true`（默认已开）
4. V3 默认 **Paper**；Rebate 默认 **Paper** + `auto_execute=false`

## 3. 日常监控

| 检查项 | 端点 | 正常 |
|--------|------|------|
| V3 仓位 | `GET /api/arbitrage/positions` | 与 Hub Monitor Tab 一致 |
| Rebate 仓位 | `GET /api/rebate/positions` | 有 S3/S8 活跃时可查 |
| 告警 | `GET /api/arbitrage/alerts` | 无 critical |
| Mid 缓存 | `GET /api/arbitrage/mid-cache/status` | stale_entries 少；含 `ws_feed` 段 |
| 资金池 | `GET /api/arbitrage/capital-pool` | utilization < 85% |
| 熔断 | orchestrator status | circuit_breaker_active=false |

## 4. 告警响应

| 代码 | 含义 | 动作 |
|------|------|------|
| `leg_failure` | Live 单腿失败 | 检查对冲所连接；确认主腿已 emergency close |
| `pool_exhaustion` | 资金池 >95% | 暂停新开仓；等待平仓释放 |
| `pool_low` | 资金池 >85% | 关注，考虑减小仓位 |
| `funding_spike` | 费率 >0.1% | 评估是否加仓 funding 或规避 |
| `circuit_breaker` | 熔断激活 | 等待冷却；查日亏原因 |

## 5. 切换 Live（谨慎）

```bash
# V3
curl -X PUT http://localhost:8000/api/arbitrage/mode \
  -H "Content-Type: application/json" \
  -d '{"mode":"live","confirm":true}'

# Rebate
curl -X PATCH http://localhost:8000/api/rebate/mode \
  -H "Content-Type: application/json" \
  -d '{"mode":"live"}'
```

**前置条件：**
- Paper 连续运行 ≥2 周无异常
- HL + Binance 双所凭证有效
- S1 保持 disabled（除非 Rh 补偿验证为正 EV）

## 6. 配置热更新

- V3：`backend/config/arb_config.yaml`（改后重启或 reload）
- Rebate：`PATCH /api/rebate/config/engine` 或 YAML
- 自动开仓：`engine.auto_execute: true`（Rebate only）

## 7. 故障排查

| 现象 | 可能原因 | 处理 |
|------|---------|------|
| Hub V3 仓位为空 | 未开 arb_enabled / 无机会 / DB 迁移未跑 | 查 status + migrations |
| 跨所无机会 | mid 缓存 stale / 仅 1 所连接 | 查 mid-cache/status；确认 `ws_feed.feed_running=true` |

## 9. WebSocket Mid 缓存

配置：`backend/config/arb_config.yaml` → `ws_feed`

```yaml
ws_feed:
  enabled: true
  poll_interval_sec: 2.0
  symbols: [BTC, ETH, SOL]
```

- **Hyperliquid**：MarketFlowCollector L2 WS 实时推送 → `mid_cache`
- **其他所**：CCXT `watch_order_book` 或 2s REST 轮询降级
- WS feed 运行时，FullAuto tick **跳过** REST 批量 mid 刷新

验证：

```bash
curl http://localhost:8000/api/arbitrage/mid-cache/status | jq '.ws_feed'
```

## 10. Rebate S3/S8 小资金验证

Paper 全链路验证（默认；首次运行加 `--force` 绕过活跃天数 R3）：

```bash
cd Hyper-Alpha-Arena
python3 scripts/validate_rebate_s3_s8.py --equity 300 --size-usd 50 --force
```

Live 前（需 API 凭证 + 二次确认）：

```bash
python3 scripts/validate_rebate_s3_s8.py --live --confirm-live --size-usd 30
```

## 11. 执行路径统一（Phase 4）

所有套利执行经 **ExecutionAuthority** 路由，来源标记 `execution_source`：

| 来源 | 路径 | 说明 |
|------|------|------|
| `fullauto` | 90s tick | 唯一自动执行权威 |
| `api` | REST `/api/arbitrage`, `/api/rebate` | 手动触发 |
| `qaa` | QAA Agent executor delegate | read_only bootstrap |

验证：

```bash
curl http://localhost:8000/api/arbitrage/execution-authority | jq
# 期望: authority=fullauto, qaa_plugins_mode=read_only
```

Rebate tick 不再直连 engine，统一走 `ExecutionAuthority.run_rebate_tick()`。

## 12. MarketDataHub 统一 WS 行情总线

配置：`backend/config/arb_config.yaml` → `market_data_hub`

```yaml
market_data_hub:
  enabled: true
  stale_ttl_sec: 5.0
  symbols: [BTC, ETH, SOL]
  channels: [l2_book, trades, funding, asset_ctx]
```

数据流：

- **Hyperliquid**：MarketFlowCollector WS → Hub（l2/trades/funding/asset_ctx）
- **其他所**：CrossExchangeWsFeed 并行 watch/poll → Hub
- **消费者**：mid_cache、price_cache、event_bus（自动桥接）

验证：

```bash
curl http://localhost:8000/api/arbitrage/market-data-hub/status | jq
curl http://localhost:8000/api/arbitrage/mid-cache/status | jq '.market_data_hub'
```

Hub 运行时 FullAuto tick **跳过** REST 批量 mid 刷新。

## 13. Phase 5 — REST 轮询退役 + unified_data_pool 接入

默认配置 `disable_rest_market_stream: true`：

- **不再**启动 5s REST `market_stream` 轮询（Hub WS 为主）
- **stale watchdog**：每 30s 仅对缺失/stale 的 symbol 做 REST 兜底
- Hub 自动桥接 `market_events` → 策略管理器仍收价格更新
- `unified_data_pool._capture_market_data` 优先读 Hub 快照（price/funding/OI）

验证：

```bash
# Hub 状态应含 disable_rest_market_stream=true, watchdog_polls
curl http://localhost:8000/api/arbitrage/market-data-hub/status | jq

# 启动日志应出现 "REST 5s 轮询已跳过"
```

降级：设 `market_data_hub.disable_rest_market_stream: false` 可恢复 Legacy REST 轮询。

## 14. Phase 6 — market_stream 退役（已删除）

`market_stream.py` 已于 Phase 7 **彻底删除**。

| 旧 API | 新 API |
|--------|--------|
| `market_stream.get_price()` | `market_price_service.get_price()` |
| `start_market_stream()` | `sync_market_symbols()` |
| `stop_market_stream()` | `stop_market_price_services()` |
| `refresh_market_stream_symbols()` | `refresh_market_symbols()` |

主模块：`backend/services/market_price_service.py`

正常路径不再创建 REST 轮询线程；Hub + stale watchdog 为唯一数据源。

## 15. Phase 7 — market_stream 删除 + API + 纯 Hub 快照

`market_stream.py` **已删除**。统一入口：

| 模块 | 用途 |
|------|------|
| `market_price_service.py` | 取价 / symbol 同步 |
| `market_data_hub.py` | WS 行情总线 |

**REST API：**

```bash
curl "http://localhost:8000/api/market/prices?symbols=BTC,ETH,SOL"
curl "http://localhost:8000/api/market/prices/snapshots?symbols=BTC,ETH"
curl http://localhost:8000/api/market/hub/status
```

**unified_data_pool** 轻量快照路径已改为 **纯 Hub**（不再读 DB FUNDING/OI 指标）；miss 时仅降级 `price_cache`。
| Rebate 只扫描不开仓 | auto_execute=false | 预期行为；或 POST /execute |
| 双路径下单风险 | QAA arb 插件未 bootstrap | 预期；仅 FullAuto 自动执行 |

## 8. 相关文档

- [rebate-arb-automation.md](./rebate-arb-automation.md)
- [SYSTEM_UPGRADE_DESIGN_V3.md](./SYSTEM_UPGRADE_DESIGN_V3.md) §3
- [API.md](./API.md) — `/api/arbitrage` & `/api/rebate`
