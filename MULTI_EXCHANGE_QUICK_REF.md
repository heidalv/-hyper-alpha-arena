# 多交易所市场流采集 - 快速参考

## 🚀 快速开始

### 1. 重启后端 (加载新代码)

```bash
# Windows: 双击运行
RESTART_AND_VERIFY.bat

# 或手动执行
STOP.bat
QUICK.bat
```

---

### 2. 验证是否生效

```bash
# 运行验证脚本
backend\.venv\Scripts\python.exe backend/verify_multi_exchange.py
```

**成功标志**:
```
[SUCCESS] 成功! 检测到两个交易所的数据:
   - Hyperliquid: OK
   - Aster DEX:   OK
```

---

### 3. 查看日志

```bash
# 实时查看关键日志
Get-Content logs/backend.log -Wait | Select-String "MarketFlow|asterdex|hyperliquid"
```

**预期日志**:
```
[Startup] 多交易所市场流采集器已启动: {'hyperliquid': True, 'asterdex': True}
```

---

## 🔧 配置说明

### .env 配置项

```bash
# 激活的交易所列表 (逗号分隔)
ACTIVE_MARKET_FLOW_EXCHANGES=hyperliquid,asterdex

# CVD 聚合窗口 (秒)
CVD_AGGREGATION_WINDOW_SECONDS=15

# 默认交易所 (新用户)
DEFAULT_EXCHANGE=asterdex
```

### settings.py 默认值

```python
ACTIVE_MARKET_FLOW_EXCHANGES = ["hyperliquid", "asterdex"]
CVD_AGGREGATION_WINDOW_SECONDS = 15
DEFAULT_EXCHANGE = "asterdex"
```

---

## 📊 API 使用

### 获取 CVD 数据

```bash
# 跨所聚合 (默认)
curl "http://localhost:8000/api/market-flow/indicators?symbol=BTC&period=1h&indicators=cvd"

# 仅 Hyperliquid
curl "http://localhost:8000/api/market-flow/indicators?symbol=BTC&period=1h&indicators=cvd&exchange=hyperliquid"

# 仅 Aster DEX
curl "http://localhost:8000/api/market-flow/indicators?symbol=BTC&period=1h&indicators=cvd&exchange=asterdex"
```

### Python 调用

```python
from services.market_flow_indicators import get_flow_indicators_for_prompt

# 跨所聚合
result = get_flow_indicators_for_prompt(db, "BTC", "1h", ["CVD"])

# 指定交易所
result = get_flow_indicators_for_prompt(db, "BTC", "1h", ["CVD"], exchange="asterdex")
```

---

## 🔍 故障排查

### 问题 1: 只看到 Hyperliquid 数据

**可能原因**:
- 后端未重启
- ACTIVE_MARKET_FLOW_EXCHANGES 配置错误
- Aster DEX 采集器启动失败

**解决方案**:
1. 检查 `.env` 配置
2. 重启后端
3. 查看日志: `logs/backend.log`

---

### 问题 2: Aster DEX watch_order_book 报错

**现象**: 日志中频繁出现 `[asterdex] watch_order_book 异常`

**原因**: Aster DEX 可能不支持 WebSocket 订单簿

**影响**: 仅 orderbook 功能不可用,trades 和 asset_metrics 正常

**解决方案**: 
- 可忽略 (不影响核心功能)
- 或注释掉 `_watch_orderbook_loop` 相关代码

---

### 问题 3: 数据库锁竞争

**现象**: `database is locked` 错误

**解决方案**:
```bash
# 增加聚合窗口
CVD_AGGREGATION_WINDOW_SECONDS=30

# 或升级数据库 (当前使用 PostgreSQL,风险较低)
```

---

## 🔄 回滚方案

### 方案 1: 仅启用 Hyperliquid

编辑 `.env`:
```bash
ACTIVE_MARKET_FLOW_EXCHANGES=hyperliquid
```

重启后端。

---

### 方案 2: 完全禁用市场流

编辑 `.env`:
```bash
ACTIVE_MARKET_FLOW_EXCHANGES=
```

重启后端。

---

## 📁 相关文件

### 核心代码
- [startup.py](file:///d:/BaiduNetdiskDownload/001Alpha-Windows迁移包-20260610-2228/001Alpha/Hyper-Alpha-Arena/backend/services/startup.py#L269-L304) - 启动逻辑
- [full_auto_trading_service.py](file:///d:/BaiduNetdiskDownload/001Alpha-Windows迁移包-20260610-2228/001Alpha/Hyper-Alpha-Arena/backend/services/full_auto_trading_service.py#L1328-L1350) - 会话订阅
- [market_flow_indicators.py](file:///d:/BaiduNetdiskDownload/001Alpha-Windows迁移包-20260610-2228/001Alpha/Hyper-Alpha-Arena/backend/services/market_flow_indicators.py) - CVD 查询
- [asterdex_collector.py](file:///d:/BaiduNetdiskDownload/001Alpha-Windows迁移包-20260610-2228/001Alpha/Hyper-Alpha-Arena/backend/services/market_flow/asterdex_collector.py) - Aster DEX 采集器

### 测试工具
- [verify_multi_exchange.py](file:///d:/BaiduNetdiskDownload/001Alpha-Windows迁移包-20260610-2228/001Alpha/Hyper-Alpha-Arena/backend/verify_multi_exchange.py) - 验证脚本
- [RESTART_AND_VERIFY.bat](file:///d:/BaiduNetdiskDownload/001Alpha-Windows迁移包-20260610-2228/001Alpha/Hyper-Alpha-Arena/RESTART_AND_VERIFY.bat) - 一键重启

### 文档
- [MULTI_EXCHANGE_TEST_SUMMARY.md](file:///d:/BaiduNetdiskDownload/001Alpha-Windows迁移包-20260610-2228/001Alpha/Hyper-Alpha-Arena/MULTI_EXCHANGE_TEST_SUMMARY.md) - 测试总结
- [MULTI_EXCHANGE_TEST_REPORT.md](file:///d:/BaiduNetdiskDownload/001Alpha-Windows迁移包-20260610-2228/001Alpha/Hyper-Alpha-Arena/MULTI_EXCHANGE_TEST_REPORT.md) - 完整报告
- [MULTI_EXCHANGE_TEST_GUIDE.md](file:///d:/BaiduNetdiskDownload/001Alpha-Windows迁移包-20260610-2228/001Alpha/Hyper-Alpha-Arena/MULTI_EXCHANGE_TEST_GUIDE.md) - 详细指南

---

## 💡 常用命令

### 查看采集器状态

```python
from services.market_flow import market_flow_registry
status = market_flow_registry.status_all()
print(status)
```

### 手动触发订阅

```python
from services.market_flow import market_flow_registry
success = market_flow_registry.ensure_subscribed("asterdex", ["BTC", "ETH"])
print(f"订阅结果: {success}")
```

### 清理旧数据 (谨慎使用)

```sql
-- 删除 7 天前的 Aster DEX 数据
DELETE FROM market_trades_aggregated 
WHERE exchange = 'asterdex' AND timestamp < EXTRACT(EPOCH FROM NOW() - INTERVAL '7 days') * 1000;
```

---

**最后更新**: 2026-06-20  
**维护人员**: AI Assistant
