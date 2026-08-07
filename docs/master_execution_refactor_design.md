# master_execution.py 拆分设计文档

> 2026-07-21 专项6：核心路径重构边界设计
> 目标：将 execute_master_decisions（L149-3879，3730行）拆分为可维护的模块
> 原则：增量重构，每步可独立验证和回滚

---

## 1. 现状量化

| 指标 | 当前值 | 业界标准 |
|------|--------|---------|
| 单函数行数 | 3730 行 | <=300 行 |
| 文件总行数 | 3880 行 | <=500 行 |
| if/elif 分支数 | ~230 个 | <=50 个 |
| MasterExecutionHost 回调字段 | 40 个 Callable | <=10 个 |
| 调用层数 | >=8 层 | 3 层 |

---

## 2. 逻辑块边界分析

通过 AST 和代码注释分析，execute_master_decisions 可划分为以下7个逻辑阶段：

### 阶段1：数据准备（L149-420，~270行）
**可提取为 `_prepare_decisions_context()`**

- L149-170: 函数签名、imports、变量初始化
- L170-280: 策略解析 + tier 映射 + nature 推导
  - `nature_to_tier_map` 查找
  - `expand_multi_tier_decisions` 多周期扇出
  - 延迟排队信号重注入
- L280-420: 市场数据补全
  - orchestrator frozen/wait 预计算
  - 硬风控门槛（RiskAnalyst score > 80）
  - 同向持仓敞口限制
  - per-tier 保证金追踪

**输入**: decisions, positions_list, market_summary, host
**输出**: processed_decisions, tier_context_map, risk_flags

### 阶段2：决策过滤门控（L420-560，~140行）
**可提取为 `_apply_decision_gates()`**

- L420-450: data_readiness_gate 门控（allow_open_action）
- L450-500: orchestrator 方向矛盾检测（orchestrator_blocks_open）
- L500-560: factor_veto_check 因子否决

**输入**: processed_decisions, market_summary, snapshot
**输出**: filtered_decisions（部分 action 降级为 hold）

### 阶段3：开仓执行分支（L560-1298，~740行）
**可提取为 `_handle_open_position()`**

- L560-860: 开仓前置检查
  - 已有持仓冲突检测
  - 仓位大小计算（extract_ai_position_pct + calibrate_confidence）
  - 杠杆解析（resolve_decision_leverage）
  - 方向胜率查询（get_direction_win_rate, get_symbol_direction_wr）
- L860-1073: 下单执行
  - paper_engine.place_order 调用
  - TP/SL 价格计算（_compute_initial_tp_sl_prices）
  - sub_position_manager 注册
- L1073-1298: 开仓后处理
  - 反向持仓检测（_opposite）
  - mark_master_decision_executed
  - log_pipeline_audit 审计

### 阶段4：持仓管理分支（L1298-2310，~1010行）
**可提取为 `_handle_position_management()`**

- L1298-1408: hold 处理 + 未知 action 拒绝
- L1408-1744: 加仓/金字塔仓处理
  - _want_side 方向匹配
  - alignment_scale 对齐缩放
  - partial_close_tracker 部分 平仓跟踪
- L1744-2212: hold 的附加处理
  - hold_timeout 超期检查
  - TDI position advice
- L2212-2310: close/reduce 分支
  - tiny_close_allowed_by_hardfact 检查
  - paper_loss_locks_disabled 检查
  - defensive_reduce_cap 防御性减仓上限
  - sub_position_manager 部分 平仓

### 阶段5：MLTO 长线分支（L2310-2700，~390行）
**可提取为 `_handle_mlto_lane()`**

- L2310-2500: execute_mlto_lane 调用
  - build_midlong_agent_envelope 构建
  - midlong_persistence_allow 持久化许可
  - 长线趋势 Agent 独立触发

### 阶段6：独立 Agent 开仓（L2700-3200，~500行）
**可提取为 `_handle_independent_agent_open()`**

- L2700-3000: try_execute_independent_agent_open
  - swing_agent / trend_agent 独立触发
  - factor_veto_check 因子否决
  - ensure_bound_strategy 策略绑定

### 阶段7：后处理（L3200-3879，~680行）
**可提取为 `_post_execution_hooks()`**

- L3200-3400: TP/SL 设置
  - validate_tp_sl_by_nature 校验
  - finalize_open_tp_sl 兜底
- L3400-3600: 风控审计
  - get_account_risk_score
  - log_pipeline_audit
- L3600-3879: 事件日志 + 仓位刷新
  - append_event
  - refresh_positions_local
  - safe_commit

---

## 3. 推荐拆分策略

### Phase 1（低风险，先执行）：提取纯数据准备函数

```
master_execution.py
├── _prepare_decisions_context()    # 阶段1
├── _apply_decision_gates()         # 阶段2
└── execute_master_decisions()      # 调用上面两个，保留主循环
```

**预估工作量**：2-3小时
**风险**：低（纯数据转换，无副作用）
**验证方式**：拆分前后对同一组 decisions 产出相同结果

### Phase 2（中风险）：提取执行分支 handler

```
master_execution.py
├── _handle_open_position()         # 阶段3
├── _handle_position_management()   # 阶段4
├── _handle_mlto_lane()             # 阶段5
├── _handle_independent_agent_open() # 阶段6
└── execute_master_decisions()      # 主循环按 action 路由到各 handler
```

**预估工作量**：4-6小时
**风险**：中（涉及 host 回调传递，需保证上下文一致）
**关键**：每个 handler 接收统一的 ExecutionContext（包含 host + position + market_data + decision）

### Phase 3（中风险）：提取后处理 hooks

```
master_execution.py
├── _post_execution_hooks()         # 阶段7
└── execute_master_decisions()      # 循环结束后统一调 hooks
```

**预估工作量**：2小时
**风险**：中（hooks 需要收集循环中所有决策结果）

### Phase 4（高风险，最后执行）：MasterExecutionHost 瘦身

将 40 个 Callable 回调归类为 5 个接口：

| 接口 | 当前字段数 | 合并后 |
|------|-----------|--------|
| MarketDataPort | 6 个（market_scan, orch_blocks, factor_veto 等） | 1 个 Protocol |
| ExecutionPort | 5 个（execute_paper, execute_mlto, try_independent 等） | 1 个 Protocol |
| RiskPort | 8 个（get_today_pnl, get_account_risk, tiny_close 等） | 1 个 Protocol |
| PositionPort | 7 个（refresh_positions, validate_tp_sl, is_exempt 等） | 1 个 Protocol |
| AuditPort | 6 个（log_pipeline, append_event, safe_commit 等） | 1 个 Protocol |

**预估工作量**：6-8小时
**风险**：高（涉及 FullAutoTradingService 的所有调用方）
**不建议在当前迭代执行**——等 Phase 1-3 稳定后再启动

---

## 4. ExecutionContext 数据类设计

```python
@dataclass
class ExecutionContext:
    """每个决策的执行上下文（替代散落的局部变量）"""
    # 标识
    symbol: str
    tier: str
    nature: str
    action: str

    # 决策数据
    decision: Dict[str, Any]
    confidence: int
    reasoning: str

    # 市场数据
    market_info: Dict[str, Any]
    entry_price: float
    mark_price: float

    # 持仓数据
    existing_position: Optional[Dict[str, Any]]
    position_id: Optional[int]

    # 风控状态
    risk_score: float
    today_realized_pnl: float

    # 引用
    host: MasterExecutionHost
    db: Session
    session: Any
    account_id: int
```

---

## 5. 拆分后的目标文件结构

```
backend/services/full_auto/
├── master_execution.py          # 主入口 + execute_master_decisions（<=200行）
├── execution_context.py         # ExecutionContext 数据类
├── decision_gates.py            # _apply_decision_gates（阶段2）
├── open_position_handler.py     # _handle_open_position（阶段3）
├── position_management_handler.py  # _handle_position_management（阶段4）
├── mlto_handler.py              # _handle_mlto_lane（阶段5）
├── independent_agent_handler.py # _handle_independent_agent_open（阶段6）
└── post_execution.py            # _post_execution_hooks（阶段7）
```

---

## 6. 迁移安全措施

1. **Feature flag**：`MASTER_EXECUTION_REFACTORED`（默认 false），新旧路径并行
2. **Shadow 模式**：新路径计算结果但不执行，与旧路径对比
3. **逐 Phase 上线**：每个 Phase 独立验证后再进入下一个
4. **回滚方案**：每个 Phase 的改动在单独 git commit，可独立 revert

---

## 7. 不建议现在执行的原因

- master_execution.py 是系统核心交易路径，每次修改都可能影响实盘
- 当前 P0-P2 修复刚完成，需要先让系统稳定运行验证修复效果
- 拆分本身不改变逻辑，只是代码组织形式的改善——不影响交易策略效果
- 建议在积累足够的实盘验证数据后（至少2周），确认修复效果再启动拆分
