# 多交易所市场流采集架构重构 - 测试验证指南

## 已完成的重构内容

### ✅ Step 1: 配置驱动启动逻辑 (startup.py)
- 读取 `ACTIVE_MARKET_FLOW_EXCHANGES` 配置动态激活采集器
- 为每个交易所构建独立的 symbols_map
- Hyperliquid 使用默认解析逻辑(信号池/AI_TRADING_SYMBOLS)
- Aster DEX 从交易对配置加载前10个币种

### ✅ Step 2: 会话级动态订阅 (full_auto_trading_service.py)
- 在 `start_session` 方法中添加市场流订阅逻辑
- 根据优先级确定交易所: `active_exchange` > `account.selected_exchange` > `DEFAULT_EXCHANGE`
- 调用 `market_flow_registry.ensure_subscribed()` 实现引用计数订阅

### ✅ Step 3: CVD 聚合逻辑交易所隔离 (market_flow_indicators.py)
- 所有 `_get_*_data` 函数添加可选 `exchange` 参数
- `get_flow_indicators_for_prompt` 支持按交易所过滤
- 保持向后兼容: exchange=None 时跨所聚合

### ✅ Step 4: Aster DEX 采集器功能增强 (asterdex_collector.py)
- 新增 `_watch_orderbook_loop`: ccxt.pro watch_order_book 实时订阅
- 新增 `_poll_asset_metrics_loop`: REST API 轮询 funding rate / mark price
- 覆盖 `_flush_orderbook`: 订单簿快照落库到 MarketOrderbookSnapshots
- 覆盖 `_flush_asset_metrics`: 资产指标落库到 MarketAssetMetrics

---

## 测试步骤

### 1. 后端启动验证

**操作**:
```bash
cd d:\BaiduNetdiskDownload\001Alpha-Windows迁移包-20260610-2228\001Alpha\Hyper-Alpha-Arena
python backend/main.py
```

**检查日志** (`logs/backend.log`):
```
[MarketFlowRegistry] 默认采集器已注册: ['hyperliquid', 'asterdex']
[Startup] 多交易所市场流采集器已启动: {'hyperliquid': True, 'asterdex': True} (window=15s)
[asterdex] watch_trades + watch_order_book + poll_asset_metrics 启动，symbols=['BTC', 'ETH', 'SOL']
[hyperliquid] 采集器已启动，symbols=[...]
```

**预期结果**:
- ✅ 两个采集器都成功启动
- ✅ Aster DEX 显示三个任务(trades/orderbook/asset_metrics)都在运行
- ✅ 聚合窗口为 15 秒

---

### 2. 数据库写入验证

**操作**: 启动后端后等待 2-3 分钟,然后查询数据库

**SQL 查询**:
```sql
-- 检查 trades 数据是否按交易所隔离
SELECT 
    exchange, 
    symbol, 
    COUNT(*) as record_count,
    MIN(timestamp) as earliest,
    MAX(timestamp) as latest
FROM market_trades_aggregated
GROUP BY exchange, symbol
ORDER BY exchange, symbol;

-- 检查 orderbook 数据(Aster DEX 特有)
SELECT 
    exchange, 
    symbol, 
    COUNT(*) as snapshot_count
FROM market_orderbook_snapshots
GROUP BY exchange, symbol
ORDER BY exchange, symbol;

-- 检查 asset_metrics 数据(funding/mark_price)
SELECT 
    exchange, 
    symbol, 
    COUNT(*) as metrics_count,
    AVG(funding_rate) as avg_funding
FROM market_asset_metrics
GROUP BY exchange, symbol
ORDER BY exchange, symbol;
```

**预期结果**:
- ✅ `market_trades_aggregated` 中有 `hyperliquid` 和 `asterdex` 两类数据
- ✅ `market_orderbook_snapshots` 中至少有 `asterdex` 的数据(Hyperliquid 也有)
- ✅ `market_asset_metrics` 中有 funding_rate 和 mark_price 数据

---

### 3. 会话级动态订阅验证

**操作步骤**:

#### 场景 A: 创建 Hyperliquid 账户并启动会话
1. 在前端创建新账户,选择交易所为 `hyperliquid`
2. 选择交易对: BTC, ETH
3. 启动全自动交易会话

**检查日志**:
```
[Session] 账户 123 交易所 hyperliquid 市场流订阅: 成功, symbols=['BTC', 'ETH']
```

#### 场景 B: 创建 Aster DEX 账户并启动会话
1. 在前端创建新账户,选择交易所为 `asterdex`
2. 选择交易对: SOL, BNB
3. 启动全自动交易会话

**检查日志**:
```
[Session] 账户 124 交易所 asterdex 市场流订阅: 成功, symbols=['SOL', 'BNB']
```

**预期结果**:
- ✅ 两个会话都能成功订阅对应交易所的市场流
- ✅ 日志显示正确的交易所标识和 symbols 列表

---

### 4. CVD 数据隔离验证

**操作**: 在前端查看 CVD 图表或调用 API

**API 测试**:
```bash
# 获取 Hyperliquid 的 CVD 数据
curl "http://localhost:8000/api/market-flow/indicators?symbol=BTC&period=1h&indicators=cvd&exchange=hyperliquid"

# 获取 Aster DEX 的 CVD 数据
curl "http://localhost:8000/api/market-flow/indicators?symbol=BTC&period=1h&indicators=cvd&exchange=asterdex"

# 跨所聚合(不传 exchange 参数)
curl "http://localhost:8000/api/market-flow/indicators?symbol=BTC&period=1h&indicators=cvd"
```

**预期结果**:
- ✅ 带 `exchange` 参数的请求返回对应交易所的数据
- ✅ 不带 `exchange` 参数的请求返回跨所聚合数据(如果有多个交易所都有 BTC 数据)

---

### 5. KlineAnalyst Flow Block 验证

**操作**: 启动一个会话后,观察 LLM 分析日志

**检查日志** (`logs/backend.log`):
```
[KlineAnalyst] BTC 流式深度分析 ...
## 订单流（BTC，来自 Hyperliquid 成交聚合）
- 1h CVD累计: 1,234,567 | 当期Δ: 12,345
- 1h Taker买/卖比: 1.234 (买$567,890 / 卖$456,789)
```

**预期结果**:
- ✅ Flow block 正确显示所选交易所的 CVD/Taker 数据
- ✅ 数据来源标注正确(Hyperliquid 或 Aster DEX)

---

### 6. 压力测试(可选)

**操作**: 同时启动 3-5 个会话,分别使用不同交易所和交易对

**监控指标**:
- CPU 使用率
- 内存占用
- 数据库写入频率
- 日志中是否有 flush 失败警告

**预期结果**:
- ✅ 多个采集器并行运行无冲突
- ✅ 数据库写入稳定(每 15 秒一次批量 flush)
- ✅ 无明显性能瓶颈

---

## 常见问题排查

### Q1: Aster DEX 采集器启动失败

**症状**: 日志显示 `[asterdex] 创建 ccxt 实例失败`

**原因**: ccxt.pro 未安装或版本不兼容

**解决**:
```bash
pip install ccxt>=4.0.0
```

---

### Q2: 订单簿数据为空

**症状**: `market_orderbook_snapshots` 表中无数据

**原因**: 
- Aster DEX API 可能不支持 watch_order_book
- 网络连接问题

**排查**:
```python
# 手动测试 ccxt.pro watch_order_book
import ccxt.async_support as ccxt
import asyncio

async def test():
    ex = ccxt.binance()
    ex.urls['api']['fapiPublic'] = 'https://fapi.asterdex.com/fapi/v1'
    try:
        ob = await ex.watch_order_book('BTC/USDT:USDT')
        print("Success:", ob)
    except Exception as e:
        print("Error:", e)
    finally:
        await ex.close()

asyncio.run(test())
```

---

### Q3: Funding Rate 数据缺失

**症状**: `market_asset_metrics.funding_rate` 全为 NULL

**原因**: Aster DEX premiumIndex API 返回格式与预期不符

**排查**:
```python
# 检查 API 响应格式
response = await exchange.fapiPrivateGetPremiumIndex({"symbol": "BTCUSDT"})
print(response)
# 确认字段名是否为 lastFundingRate / markPrice
```

---

### Q4: 会话订阅冲突

**症状**: 第二个会话启动时报错或第一个会话被中断

**原因**: `ensure_subscribed` 引用计数逻辑有问题

**排查**:
- 检查 `registry.py` 中的 `ensure_subscribed` 实现
- 确认同一 symbol 被多次订阅时不会重复启动采集器

---

## 回滚方案

如果重构后出现严重问题,可以快速回滚:

### 方案 1: 禁用多交易所功能

在 `.env` 文件中设置:
```env
ACTIVE_MARKET_FLOW_EXCHANGES=hyperliquid
```

这样只会启动 Hyperliquid 采集器,Aster DEX 不会被激活。

### 方案 2: 恢复旧版单所模式

如果问题严重,可以临时注释掉 `startup.py` 中的多交易所启动代码,改用旧的 `market_flow_collector.py`(如果还存在)。

---

## 后续优化建议

1. **引用计数完善**: 当前 `ensure_subscribed` 只是合并 symbols,未来可以实现真正的引用计数,最后一个会话退出时自动取消订阅

2. **Aster DEX OI 支持**: 如果 Aster DEX 提供 Open Interest API,可以在 `_poll_asset_metrics_loop` 中补充

3. **性能监控**: 添加采集器健康度指标(延迟、丢包率、重连次数)到监控面板

4. **前端交易所筛选**: 在前端 CVD 图表中添加交易所下拉框,让用户能切换查看不同交易所的数据

---

## 总结

本次重构成功实现了:
- ✅ 配置驱动的多交易所采集器动态激活
- ✅ 基于账户配置的会话级动态订阅
- ✅ CVD/OI/Funding 等指标的交易所隔离查询
- ✅ Aster DEX 完整能力(trades + orderbook + asset_metrics)

系统现在支持 Hyperliquid 和 Aster DEX 并行运行,不同交易员可以根据需要选择不同的交易所,互不干扰。
