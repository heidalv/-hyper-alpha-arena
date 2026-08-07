# Binance 依赖关系图（Phase 1 清理用）

## 1. 直接 Binance 文件（删除清单）

| 文件 | 说明 |
|------|------|
| `backend/services/binance_market_data.py` | 币安行情 |
| `backend/services/binance_trading_client.py` | 币安交易客户端 |
| `backend/services/binance_proxy_utils.py` | 币安代理 |
| `backend/services/binance_environment.py` | 币安环境/密钥 |
| `backend/services/binance_symbol_service.py` | 币安标的管理 |
| `backend/services/binance_user_stream.py` | 币安用户流 |
| `backend/services/binance_cache.py` | 币安缓存 |
| `backend/api/binance_routes.py` | 币安 API 路由 |

## 2. 引用关系（修改后才能删）

### 2.1 引用 binance_routes / binance_router

- `backend/main.py`：`from .api.binance_routes import router as binance_router`，`app.include_router(binance_router)`  
→ 删除 import 与 include_router。

### 2.2 引用 place_ai_driven_binance_order

- `backend/services/position_tracker_service.py`：紧急平仓、分批止盈、滚仓共 3 处 → 改为调用 HyperLiquid 平仓。
- `backend/services/trading_strategy.py`：2 处 → 改为 HL 或仅保留 HL 策略。
- `backend/services/trading_commands.py`：定义该函数 → 可保留空实现或删除，调用方改为 HL。
- `backend/api/account_routes.py`：1 处 → 改为 HL。
- `backend/api/ai_trading_routes.py`：1 处 → 改为 HL。
- `backend/services/ai_strategy_engine.py`：2 处 → 改为 HL。

### 2.3 引用 binance 行情/K线/环境/客户端

- `backend/services/market_data.py`：get_last_price_from_binance、get_ticker_data_from_binance、get_kline_data_from_binance、get_all_symbols_from_binance → 移除 Binance 分支，仅保留 HyperLiquid。
- `backend/services/unified_data_pool.py`：_capture_binance_account、binance_environment、binance_trading_client → 移除币安账户捕获，仅 HL。
- `backend/services/kline_collectors.py`：binance_proxy_utils → 移除或改为 HL 代理。
- `backend/repositories/kline_repo.py`：get_kline_data_from_binance → 仅用 HL 数据源。
- `backend/services/strategy_coordinator.py`：get_kline_data_from_binance、get_binance_public_exchange、binance_proxy_utils → 改为 HL。
- `backend/services/market_flow_collector.py`：get_binance_selected_symbols → 改为 HL 或统一标的管理。
- `backend/services/startup.py`：binance_user_stream → 移除启动。
- `backend/services/dingtalk/notification_service.py`：binance_trading_client、binance_environment → 仅 HL 或移除钉钉推送中的 Binance 分支。
- `backend/services/monitoring/realtime_monitor.py`：binance_trading_client、binance_environment → 仅 HL。
- `backend/api/ai_strategy_routes.py`：binance_proxy_utils → 移除或改为 HL。

### 2.4 引用 binance_symbol_service

- `backend/services/trading_strategy.py`：get_binance_selected_symbols → 改为 HL 标的管理。
- `backend/api/binance_routes.py`：get_binance_selected_symbols、update_binance_selected_symbols → 随 binance_routes 删除。

### 2.5 脚本/测试（可删或忽略）

- `backend/check_binance_ai_positions.py`：可删。
- `backend/test_binance.py`：可删。

## 3. 执行顺序建议

1. 修改 `position_tracker_service.py`：紧急平仓/分批止盈改为 HL。
2. 修改 `market_data.py`：仅保留 HyperLiquid 分支。
3. 修改 `unified_data_pool.py`：移除 _capture_binance_account。
4. 修改 `trading_commands.py`：place_ai_driven_binance_order 改为抛 NotImplemented 或删除，调用方改为 HL。
5. 修改 `trading_strategy.py`、`account_routes.py`、`ai_trading_routes.py`、`ai_strategy_engine.py`：全部改为 HL 路径。
6. 修改 `startup.py`、`kline_collectors.py`、`kline_repo.py`、`strategy_coordinator.py`、`market_flow_collector.py`、`dingtalk`、`realtime_monitor`、`ai_strategy_routes`：移除或替换 Binance 依赖。
7. 从 `main.py` 移除 binance_router，删除 `api/binance_routes.py`。
8. 删除所有 `services/binance_*.py`。
9. 删除或归档 `check_binance_ai_positions.py`、`test_binance.py`。

## 4. 数据库

- 方案要求：清理 Binance 相关表前做备份。表/迁移见 `add_binance_positions_table.py`、`add_binance_columns.py` 等。  
- 本次执行：仅代码与路由清理；表结构暂不删除，在文档中标注“待后续迁移清理”。
