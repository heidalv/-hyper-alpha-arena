# 架构精简专项方案（Q4 → 因子精选 + Agent 合并的迁移路径）

> 日期: 2026-07-21
> 关联: 交易系统四大核心问题根因分析报告 Q4
> 状态: **因子精选已实施 + Agent 合并方案已产出（待执行）**

---

## 一、问题回顾

根因分析报告 Q4 指出：系统存在 **128 因子 + 9 Agent + 6 进化引擎** 的过度工程化，
核心能力被稀释。本专项的目标是：在保持交易能力的前提下精简架构。

---

## 二、因子精选 — 已实施 ✅

### 已完成的工作

| 组件 | 文件 | 状态 |
|------|------|------|
| IC 批量评估 + 自动淘汰 | `factor_cleanup_service.py` (294行) | ✅ 已创建 |
| 信号管线过滤 rejected 因子 | `factor_evaluation_pipeline.py` | ✅ 已修改 |
| 每周触发清洗 | `evolution_scheduler.py` | ✅ 已修改 |

### 淘汰规则

| 判定 | 阈值 | 动作 |
|------|------|------|
| IC ≤ -0.05 且样本 ≥ 20 | 负贡献因子 | → rejected (权重置0) |
| |IC| < 0.02 且样本 ≥ 30 | 噪声因子 | → low_signal (降权观察) |
| Top-50 by IC | 高贡献因子 | → active (保留) |

### 当前状态
- 因子总量: 1144 个 .py 文件（含 971 个 AI 生成）
- 目标: ≤ 50 个活跃因子
- 下一步: 触发首次 `run_batch_ic_cleanup(force=True)` 产出实际清洗报告

---

## 三、进化引擎冻结 — 已实施 ✅

### 已冻结的系统

| 系统 | 产出 | 主开关 |
|------|------|--------|
| Hermes L1-L4 (619→0) | 零真实产出 | `HERMES_ENABLED=false` |
| Strategy Genesis (330→0) | 零毕业策略 | `EVOLUTION_SYSTEMS_ENABLED=false` |
| Architecture Evolution (217→0) | 零架构改进 | `EVOLUTION_SYSTEMS_ENABLED=false` |
| Distributed Evolution | 未实际运行 | `EVOLUTION_SYSTEMS_ENABLED=false` |
| NSGA-II 遗传优化 | 离线运行 | `EVOLUTION_SYSTEMS_ENABLED=false` |
| OpenCode Scheduler (7 tasks) | 零任务执行 | `EVOLUTION_SYSTEMS_ENABLED=false` |

### 保留的监控任务
- SRR (Self-Reinforcement Review)
- Pace Monitoring
- Maturity Tracking
- Health Check

---

## 四、Agent 合并迁移路径 — 方案设计

### 4.1 当前 9 Agent 概况

| # | Agent | LLM Level | 职责 | 热路径 | 建议 |
|---|-------|-----------|------|--------|------|
| 1 | genetic_optimizer | NONE | 离线遗传优化 | ❌ | **冻结** (已实施) |
| 2 | market_data | NONE | 数据快照 | ✅ | 保留 |
| 3 | factor_engine | QUICK | 因子计算 | ✅ | 保留 |
| 4 | intel_signal | QUICK | 8源情报信号 | ✅ | **降级** |
| 5 | risk_control | NONE | 5条硬规则 | ✅ | 保留 (fail-closed) |
| 6 | mt_orchestrator | QUICK | 多周期协调 | ✅ | 保留 |
| 7 | master_controller | **DEEP** | 最终决策 | ✅ | **优化** |
| 8 | trade_execution | NONE | 下单/平仓 | ✅ | 保留 |
| 9 | signal_bus | NONE | 信号聚合 | ✅ | **吸收** |

### 4.2 合并方案（3阶段）

#### Phase 1: genetic_optimizer 冻结（已完成 ✅）
- Agent 保留在 ALL_CARDS 但 EVOLUTION_SYSTEMS_ENABLED=false 时跳过
- 实际效果: 9 Agent → **8 活跃 Agent**

#### Phase 2: signal_bus 吸收 intel_signal（中期，建议2周后）

**现状分析**:
```
当前信号路径（冗余）:
  ai_decision_service → intel_signal_engine.compute_trading_signal()     ← 直接调用
  mt_orchestrator     → intel_signal_engine.compute_trading_signal()     ← 直接调用  
  qaa_legacy_cycle    → unified_signal_bus.get_unified_signal()          ← 通过 QAA
  rule_router         → intel_signal.get_signals / signal_bus.get_unified ← 路由层
  
问题: 同一个情报信号被3条路径独立计算，signal_bus 的聚合结果反而未被主路径使用
```

**合并方案**:
```
目标路径（统一）:
  signal_hub.get_unified_signal(symbol)
    ├── intel_signal_engine.compute_trading_signal()    ← 内部调用
    ├── factor_engine.get_factor_signal()               ← 内部调用
    └── confirmation_engine.get_confirmation()          ← 内部调用
  
  ai_decision_service → signal_hub.get_unified_signal()
  mt_orchestrator     → signal_hub.get_unified_signal()
```

**迁移步骤**:
1. 在 `signal_bus.py` 新增 `get_unified_signal_v2()` — 内部合并 intel_signal 调用
2. `ai_decision_service.py`: 将直接调用 `intel_signal_engine` 改为调用 `signal_bus.get_unified_signal_v2()`
3. `mt_orchestrator.py`: 同上
4. 删除 `intel_signal` AgentCard（标记为 deprecated）
5. QAA rule_router 中移除 intel_signal 路由规则

**预期收益**:
- 消除 3 处冗余 intel_signal 调用
- LLM QUICK 调用减少 1 个 Agent 开销
- 8 活跃 Agent → **7 活跃 Agent**

**风险**: 中等 — 需确保 signal_bus 的聚合逻辑与 intel_signal 直接调用的结果一致

#### Phase 3: master_controller DEEP → QUICK（远期，建议4周后）

**现状分析**:
```
master_controller:
  - LLM Level: DEEP (240s 超时)
  - 在主决策热路径同步调用
  - 每个交易对每次决策都会触发
  - 问题: 高延迟 + 高成本 + 单点阻塞
```

**优化方案**:
```
优化后:
  master_controller_v2:
  - LLM Level: QUICK (90s 超时)
  - 决策上下文预计算 (6个 analyst 的结果已包含全部信息)
  - LLM 只做最终综合判断，不做重新分析
  - DebateLayer 改为异步预处理
```

**迁移步骤**:
1. 新增 `master_controller_v2` AgentCard (QUICK level)
2. 重构 prompt: 将 6 个 analyst 的结构化输出压缩为精简决策模板
3. 灰度切换: 50% 流量走 v2，50% 走 v1，对比决策质量
4. 确认无退化后全量切换
5. 删除 `master_controller` (DEEP) AgentCard

**预期收益**:
- 决策延迟: 240s → 90s (−62.5%)
- LLM token 成本: 下降 ~60%
- 不改变 Agent 数量，但显著降低热路径延迟

### 4.3 合并后架构

```
最终活跃 Agent (7个):
  ┌─────────────┐    ┌──────────────┐    ┌──────────────────┐
  │ market_data │───▶│ factor_engine│───▶│ signal_hub       │
  │   (NONE)    │    │   (QUICK)    │    │ (原 signal_bus)  │
  └─────────────┘    └──────────────┘    │ (NONE, 含 intel) │
                                         └────────┬─────────┘
                                                  │
  ┌─────────────┐    ┌──────────────┐    ┌───────▼─────────┐
  │ risk_control│◀───│mt_orchestrator│◀──│master_controller│
  │   (NONE)    │    │   (QUICK)    │    │  (QUICK, 原DEEP) │
  │ fail-closed │    │              │    └─────────────────┘
  └─────────────┘    └──────────────┘             │
                          │                       ▼
                          ▼              ┌─────────────────┐
                  ┌──────────────┐      │trade_execution  │
                  │ (决策数据)   │      │    (NONE)       │
                  └──────────────┘      └─────────────────┘
```

### 4.4 不合并的 Agent 及理由

| Agent | 不合并理由 |
|-------|-----------|
| market_data | 数据层独立性 — 持续运行 vs 按需调用 |
| factor_engine | 共享依赖 — 被 scalp_loop / mt_orchestrator / signal_bus 三方调用 |
| risk_control | 安全隔离 — fail-closed 确定性检查必须独立于 LLM |
| mt_orchestrator | 多周期协调有其独立的 prompt 和决策逻辑 |
| trade_execution | 执行层独立性 — 幂等下单/平仓不可与其他逻辑耦合 |

---

## 五、QAA 路由层精简（附带优化）

### 现状
`qaa_legacy_cycle.py` 和 `rule_router.py` 为每个 Agent 创建了 passthrough handler:
```python
"intel_signal": lambda a, p: qaa_intel_signal(a, p, host),
"signal_bus": lambda a, p: qaa_signal_bus(a, p, host),
# ... 共 8 个 handler
```

### 问题
这些 handler 只是调用底层 service 的薄包装，增加了 QAA 框架开销（序列化、路由、circuit breaker）
但没有增加任何功能。

### 建议
- 短期: 保留 QAA 路由层（提供 circuit breaker / timeout 保护）
- 中期: 评估是否可以去掉 QAA 路由层，直接调用底层 service
- 远期: 如果去掉，9 个 handler → 0，ALL_CARDS 注册表变为纯文档

---

## 六、实施时间线

| 阶段 | 内容 | 时机 | 风险 |
|------|------|------|------|
| Phase 0 | 因子精选 + 进化冻结 | ✅ 已完成 | — |
| Phase 1 | genetic_optimizer 冻结 | ✅ 已完成 | — |
| Phase 2a | signal_bus 吸收 intel_signal | 实盘验证2周后 | 中 |
| Phase 2b | QAA 路由层评估 | Phase 2a 完成后 | 低 |
| Phase 3 | master_controller DEEP→QUICK | Phase 2a 稳定后 | 高 |

---

## 七、验证指标

| 指标 | 当前 | 目标 | 测量方式 |
|------|------|------|----------|
| 活跃 Agent 数 | 9 | 7 | ALL_CARDS 计数 |
| 活跃因子数 | ~1144 | ≤50 | factor_cleanup_report |
| 进化引擎 CPU 消耗 | 高 | 0 | EVOLUTION_SYSTEMS_ENABLED=false |
| 决策延迟 (P95) | ~240s (DEEP) | ≤90s (QUICK) | 日志统计 |
| LLM token / 决策 | 高 | −60% | analytics DB |
| intel_signal 冗余调用 | 3处 | 1处 | grep 调用计数 |
