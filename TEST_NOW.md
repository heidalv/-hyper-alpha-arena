# 多交易所架构 - 立即执行测试

## 🎯 当前状态

- ✅ 代码重构完成 (100%)
- ✅ 测试工具就绪
- ⚠️ 后端运行旧代码 (需要重启)

---

## 🚀 立即执行 (3选1)

### 选项 1: 一键验证 (最简单) ⭐推荐

**双击运行**:
```
RESTART_AND_VERIFY.bat
```

脚本会自动完成所有步骤,包括:
- 停止后端
- 重新启动
- 等待30秒
- 运行验证
- 显示结果

---

### 选项 2: 手动重启 + 验证

#### Step 1: 停止后端

**方法 A**: 双击 `STOP.bat`

**方法 B**: 任务管理器中结束 Python 进程

---

#### Step 2: 启动后端

**方法 A**: 双击 `QUICK.bat` (推荐,同时启动前端)

**方法 B**: 仅启动后端
```bash
cd d:\BaiduNetdiskDownload\001Alpha-Windows迁移包-20260610-2228\001Alpha\Hyper-Alpha-Arena
backend\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

---

#### Step 3: 查看启动日志

打开文件: `logs/backend.log`

搜索关键词: `MarketFlowRegistry` 或 `多交易所`

**预期看到**:
```
[Startup] 多交易所市场流采集器已启动: {'hyperliquid': True, 'asterdex': True}
```

如果只看到 Hyperliquid,说明配置有问题。

---

#### Step 4: 等待数据采集

等待 **2-3 分钟**,让 Aster DEX 开始工作。

---

#### Step 5: 运行验证脚本

```bash
cd d:\BaiduNetdiskDownload\001Alpha-Windows迁移包-20260610-2228\001Alpha\Hyper-Alpha-Arena
backend\.venv\Scripts\python.exe backend/verify_multi_exchange.py
```

**成功标志**:
```
[SUCCESS] 成功! 检测到两个交易所的数据:
   - Hyperliquid: OK
   - Aster DEX:   OK
```

---

### 选项 3: 仅验证数据库 (不重启)

如果暂时不想重启后端,可以先检查当前数据库状态:

```bash
cd d:\BaiduNetdiskDownload\001Alpha-Windows迁移包-20260610-2228\001Alpha\Hyper-Alpha-Arena
backend\.venv\Scripts\python.exe backend/verify_multi_exchange.py
```

**预期结果**:
```
[WARN] 仅检测到 Hyperliquid 数据
   原因: 后端在代码修改前启动,需要重启
```

这说明代码已就绪,但需要重启才能生效。

---

## 📊 验证检查清单

重启后,请确认以下各项:

### ✅ 启动阶段
- [ ] 日志中出现 `多交易所市场流采集器已启动`
- [ ] 显示 `{'hyperliquid': True, 'asterdex': True}`
- [ ] 无严重错误(ERROR级别)

### ✅ 数据采集 (等待2-3分钟后)
- [ ] 运行验证脚本显示两个交易所都有数据
- [ ] Hyperliquid trades 数量持续增加
- [ ] Aster DEX trades 数量从0开始增加

### ✅ 功能测试 (可选)
- [ ] 创建 Aster DEX 账户并启动会话
- [ ] 日志显示 `账户 XXX 交易所 asterdex 市场流订阅: 成功`
- [ ] API 调用带 `exchange=asterdex` 参数能返回数据

---

## 🔍 常见问题

### Q1: 重启后还是只有 Hyperliquid?

**检查**:
1. `.env` 文件中是否有 `ACTIVE_MARKET_FLOW_EXCHANGES` 配置
2. 如果没有,使用默认值(应该是 `hyperliquid,asterdex`)
3. 查看日志中是否有 Aster DEX 启动失败的错误

**解决**:
```bash
# 在 .env 中添加或修改
ACTIVE_MARKET_FLOW_EXCHANGES=hyperliquid,asterdex
```

然后再次重启。

---

### Q2: Aster DEX 采集器报错?

**现象**: 日志中频繁出现 `[asterdex] watch_order_book 异常`

**原因**: Aster DEX 可能不支持 WebSocket 订单簿

**影响**: 仅 orderbook 功能不可用,trades 和 asset_metrics 正常

**处理**: 
- 可以忽略(不影响核心功能)
- 或查看完整错误信息判断是否需要修复

---

### Q3: 验证脚本显示 "数据库中无数据"?

**原因**: 
- 刚重启,Aster DEX 还没开始采集
- 或数据库连接有问题

**解决**:
1. 等待 2-3 分钟
2. 再次运行验证脚本
3. 检查数据库连接配置

---

## 📝 测试结果记录

请在下方记录您的测试结果:

### 重启时间: _______________

### 启动日志检查结果:
- [ ] 看到 `多交易所市场流采集器已启动`
- [ ] hyperliquid: True / False
- [ ] asterdex: True / False

### 验证脚本结果 (重启后2-3分钟):
```
粘贴验证脚本输出 here
```

### 遇到的问题:
```
描述遇到的问题 here
```

### 解决方案:
```
描述如何解决的 here
```

---

## 📖 相关文档

- **详细指南**: [MULTI_EXCHANGE_TEST_GUIDE.md](file:///d:/BaiduNetdiskDownload/001Alpha-Windows迁移包-20260610-2228/001Alpha/Hyper-Alpha-Arena/MULTI_EXCHANGE_TEST_GUIDE.md)
- **完整报告**: [MULTI_EXCHANGE_TEST_REPORT.md](file:///d:/BaiduNetdiskDownload/001Alpha-Windows迁移包-20260610-2228/001Alpha/Hyper-Alpha-Arena/MULTI_EXCHANGE_TEST_REPORT.md)
- **快速参考**: [MULTI_EXCHANGE_QUICK_REF.md](file:///d:/BaiduNetdiskDownload/001Alpha-Windows迁移包-20260610-2228/001Alpha/Hyper-Alpha-Arena/MULTI_EXCHANGE_QUICK_REF.md)

---

**准备好了吗?现在就开始吧!** 🚀

双击 `RESTART_AND_VERIFY.bat` 或按照上述步骤操作。
