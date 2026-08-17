# AI 策略 Agent 群组审查与改造方案（2026-08）

> 背景：长线已切换到 long_trend_v2（规则化 L1 + Chandelier，无 LLM）；中线已去 LLM 化
> （FactorRoute 入场 + 规则化六维管理）。本文盘点当前 AI 策略 agent 群组的真实状态，
> 并给出符合当前项目运行情况的修改建议。

---

## 一、现状盘点（谁还在跑、谁已停）

### 1. 三层交易主循环（APScheduler 实际在跑的）

| 循环 | 周期 | 文件 | 现状 |
|---|---|---|---|
| _run_midlong_loop_safe | 45s | loops/midlong_loop.py -> mlto_cycle.py | 长线=规则化 V2；中线=FactorRoute+规则管理 |
| _run_scalp_loop_safe | 10s | loops/scalp_loop.py | 短线因子引擎 + 多道闸（规则为主） |
| _run_unified_loop_safe | 30s | master_execution.py + analyst 系统 | summary 模式，长线 LLM 已跳过 |

### 2. 各 agent 的真实状态

| Agent | 文件 | 原职责 | 现在状态 |
|---|---|---|---|
| TrendAgent | trend_agent.py | 长线方向 LLM 判定 + 持仓复查 + 金字塔 | 长线方向已停（V2 接管）；review_position 仍被 scalp 复查、中线复查用（但中线复查已规则化） |
| SwingAgent | swing_agent.py | 中线独立分析 | 已废弃（文件自己标注分支已删除），仅留 is_swing_nature 路由判定 |
| MLTO thesis | mlto/orchestrator.py -> run_mlto_tick | 长线 thesis LLM（含 mid_view）+ 中线 thesis | 长线已停（任务循环 continue）；中线已停（MIDLONG_MID_VIA_MLTO=false）；mid_view 不再刷新 |
| KlineAnalyst / MasterController | trading_analysts.py | 多 agent 短线/市场 LLM 分析 | 仍在跑（喂市场快照，短线为主） |
| FactorRoute | factor_engine/midlong_factor_route.py | 中线因子入场 | 活跃（规则，source=factor_route） |
| ScalpRouter | loops/scalp_loop.py | 短线开平仓 | 活跃（因子+门禁规则为主，尾部少量 LLM 复查） |
| long_trend_v2 | long_trend_v2.py | 长线规则化 L1 + Chandelier | 活跃（唯一长线决策源） |
| midlong_position_manager | full_auto/midlong_position_manager.py | 中长线六维持仓管理 | 活跃（MIDLONG_REVIEW_LLM=false -> 规则版 _rule_direction/_dim_pyramid） |

---

## 二、已完成的去 LLM 改造（本次+前序）

1. 长线入场：mlto_cycle.py 长线方向判定跳过 trend_agent.analyze_direction，改用 entry_signal()（L1 五信号投票，up 才 buy，多头单边）。
2. 长线 thesis LLM：任务提交循环里对 tier=long 直接 continue，不再进线程池、不再发 LLM。
3. 长线退出：_run_midlong_active_exit / manage_position / hold_timeout_trend_review 对 long 改走 manage_long_position（结构破坏 + Chandelier），跳过旧 bias/no_progress/分档TP/15min复查。
4. 中线 thesis：任务提交循环里 MIDLONG_MID_VIA_MLTO=false 时对 tier=mid 直接 continue（本来就是 no-op，现在连空转都省了）。
5. master_execution 长线 LLM：MASTER_MIDLONG_LLM_MODE=summary -> 长线 LLM 段已 action=hold 跳过，交由独立 midlong 循环负责。
6. execute_mlto_lane：只在 MASTER_MIDLONG_LLM_MODE != summary 时被调，当前 summary 模式下等效停用。

结论：旧长线信号已完全停止；中线已是规则驱动。长线全程 0 次 LLM，中线入场/管理全程 0 次 LLM。

---

## 三、仍需处理 / 建议的修改（按优先级）

### P0 —— 直接清理（低风险，去掉死代码与噪音）
1. swing_agent.py：确认无任何生产调用后，将整文件标记为废弃（保留 is_swing_nature 或迁到公共工具），避免误触旧中线 LLM 路径。
2. trend_agent.analyze_direction：加启动期断言/日志，确认长线路径不再命中（master_execution 非 summary 分支、mlto_cycle 旧分支都已被 V2 覆盖）。若确无调用，标注 deprecated。
3. mid_view 链路：qual_layer / quant_layer / decision_hub 的 mid_timing（权重 0.15）现在恒为 None。要么删掉该信号，要么明确注释已停用、待中线规则化择时替代。

### P1 —— 收敛 agent 入口（降低复杂度）
4. master_execution 与 mlto_cycle 双入口：长线决策现在只在独立 midlong 循环生效，master_execution 的 long 段是 summary-skip。建议把 master_execution 的 trend 段整段删掉（只留 scalp/中线路由），消除两个长线入口的认知负担。
5. execute_mlto_lane：summary 模式下已不可达。确认后删除或降级为 debug 占位，避免未来有人改 MASTER_MIDLONG_LLM_MODE 后意外恢复旧 LLM。

### P2 —— 短线/中线的 LLM 边界（单独决策，不要混做）
6. scalp 复查 LLM（scalp_loop.py:1343 trend_agent.review_position）：这是短线持仓复查的 LLM，与长线无关。是否也要去 LLM 化，需要单独评估短线盈亏后再定——不建议本次顺带改。
7. KlineAnalyst / MasterController：这是市场快照分析层，供三层共用。是否保留 LLM，取决于短线/市场情绪是否需要 LLM 摘要；与长中线规则化不冲突。

### P3 —— 数据/记忆配套
8. trend_cycles 记忆表（trend_cycle_archive.py）：V2 长线现在规则化，趋势周期归档/相似周期/周报应继续喂给 manage_long_position 或用于复盘，确认接线没有因 LLM 下线而断。
9. 因子挖掘 DSR/PBO 门：GPU 挖出的因子仍走 _dsr_pbo_gate 校验后进中线 FactorRoute——规则化中线对因子质量的要求不变，保持现有门。

---

## 四、一句话结论

- 长线：long_trend_v2（L1 + Chandelier）是唯一决策源，0 LLM。
- 中线：FactorRoute 入场 + 规则化六维管理，0 LLM。
- 短线：因子引擎 + 门禁为主，仍有少量 LLM 复查（保留待单独评估）。
- 可清理的死代码：SwingAgent、TrendAgent 长线方向段、MLTO thesis（long/mid）、master_execution 长线段、mid_view 链路。

建议按 P0 -> P1 顺序做清理；P2/P3 单独立项，避免把短线/因子矿的事和长中线去 LLM 混在一起。