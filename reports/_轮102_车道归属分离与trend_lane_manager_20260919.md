# 轮102 · 阶段3：车道归属分离（trend_lane_manager + 24 模块归属判定）

> 承接 `reports/_轮100_中线与长线分离_架构诊断与第一阶段_20260919.md` 的阶段 3。
> 提交：`轮102`（`trend_lane_manager.py` + `manage_position` 接线 + 归属判定 + 棘轮测试）

---

## 一、24 个 `midlong_*` 模块的归属判定（实测，静态信号）

判据：`tier` 引用数（是否按档位分支）、"长线例外"补丁数（`tier=='long'` / `_LONG`）、
车道专属键数、是否已接车道真源（E1 / `lane_policy`）。

| 模块 | 行数 | tier | 长线例外 | 车道键 | 已接真源 | 归属判定 |
|---|---:|---:|---:|---:|:--:|---|
| `midlong_position_manager.py` | 2091 | 95 | 4 | 0 | 是 | **两车道共用 + 长线补丁** → 本轮抽走长线决策 |
| `midlong_helpers.py` | 1950 | 114 | 0 | 0 | 否 | **两车道共用**（建仓链主流程）→ 阶段4/5 拆 |
| `mlto_cycle.py` | 1072 | 31 | 2 | 0 | 是 | **两车道共用 + 长线补丁**（脑循环）→ 阶段5 拆 |
| `midlong_executor.py` | 723 | 22 | 0 | 0 | 否 | **两车道共用**（建仓执行）→ 阶段5 拆 |
| `midlong_circuit_gate.py` | 716 | 8 | 0 | 0 | 否 | **两车道共用**（熔断闸）→ 只有阈值该分车道 |
| `midlong_factor_route.py` | 684 | 4 | 0 | 0 | 否 | **两车道共用**（因子路由）→ 阶段4 |
| `midlong_portfolio_risk.py` | 590 | 18 | 5 | 3 | 是 | **两车道共用 + 长线补丁**（组合风险）→ 已部分分车道 |
| `midlong_registry_factors.py` | 600 | 0 | 0 | 0 | 否 | **待判定**（无 tier 分支：疑似纯因子层） |
| `midlong_cold_pool.py` | 593 | 0 | 0 | 0 | 否 | **待判定**（冷池，纯因子层） |
| `midlong_active_factor_set.py` | 566 | 0 | 0 | 0 | 否 | **待判定**（活跃因子集，纯因子层） |
| `midlong_loop.py` | 508 | 8 | 0 | 0 | 否 | **两车道共用**（循环）→ 阶段5 拆 |
| `midlong_belief_loop.py` | 421 | 2 | 1 | 0 | 否 | **两车道共用 + 长线补丁** |
| `midlong_trade_design.py` | 410 | 14 | 3 | 4 | 是 | **两车道共用 + 长线补丁** → 轮101 已分倍数值 |
| `midlong_direction_audit.py` | 406 | 3 | 0 | 0 | 否 | **两车道共用**（审计） |
| `midlong_location_gate.py` | 387 | 8 | 2 | 2 | 否 | **两车道共用 + 长线补丁** |
| `midlong_ev_gate.py` | 307 | 1 | 2 | 2 | 否 | **两车道共用 + 长线补丁** |
| `midlong_chart_gate.py` | 266 | 3 | 0 | 0 | 否 | **两车道共用**（图表闸） |
| `midlong_regime_weights.py` | 249 | 0 | 0 | 0 | 否 | **待判定**（regime 权重，纯因子层） |
| `midlong_mtf_constraint.py` | 187 | 9 | 1 | 0 | 否 | **两车道共用 + 长线补丁** |
| `midlong_exit_guard.py` | 152 | 12 | 3 | 0 | 否 | **两车道共用 + 长线补丁**（出场守卫）→ 应与本轮的 `trend_lane_manager` 合并 |
| `midlong_interlock_watch.py` | 145 | 0 | 0 | 0 | 否 | **待判定** |
| `mid_long_quant_brief.py` | 141 | 0 | 0 | 0 | 否 | **待判定**（量化简报） |
| `mid_long_structure_stop.py` | 114 | 0 | 0 | 0 | 否 | **待判定**（结构止损，疑似长线专属） |
| `midlong_flow_gate.py` | 96 | 0 | 0 | 0 | 否 | **待判定**（资金流闸） |
| **合计** | **13374** | | | | **4/24** | 其中"两车道共用"**16 个（10870 行）** |

**结论**：合并不是命名问题，是**16 个模块（1.09 万行）真的同时服务两条车道**。
其中 7 个已经长出"长线补丁"（说明有人踩过坑并就地打补丁），8 个连 `tier` 分支都没有
（需要按内容逐个判定：纯因子层的可以直接保留共用，策略层的必须分）。

## 二、本轮的归属分离：`trend_lane_manager.py`

### 边界

**只做决策，不做执行**：

```python
TrendDecision = decide(
    thesis_reason=..., review_action=..., pyramid_action=...,
    pnl_pct=..., pyramid_min_pnl_pct=..., rule_passthrough=..., dca_action=...,
)
```

- 纯函数（无 DB / 无时间 / 无随机 / 无执行层 import —— 有测试断言）；
- 执行仍由 `midlong_position_manager` 的既有 helper 完成（不搬 700 行代码）；
- 将来要把执行也搬进来，只需在本模块加 `run()`，调用方换一行。

### 长线契约（唯一一处定义）

| 输入 | 决策 | 理由 |
|---|---|---|
| `thesis_invalidation`（价格击穿失效价） | **thesis_exit** | 规则失效是车道契约里的价格型出场 |
| `thesis_should_close`（LLM 裁量） | thesis_exit + **需过 F39 反转确认闸** | 长线最短持仓 72h，裁量平仓必须过闸 |
| `tighten_trailing` | **hold（拒绝）** | 2026-09-18 事故机制：1% 带宽收割趋势仓 |
| `reduce` | **hold（拒绝）** | #4659 被减 6 次只剩 2.7% |
| `add` 且浮盈 / 规则直通达阈值 | **pyramid** | 用户要求："长线趋势是滚仓盈利为目的的" |
| `dca`（逆势补仓） | hold（拒绝） | DCA 是中线/震荡工具，不是趋势工具 |
| 其它 | hold | 长线默认不是"找理由平仓" |

接线：`manage_position` 的论题分支现在先问该模块；被拒绝的动作会记日志
`车道决策=... → 不走论题平仓`。

## 三、回归与棘轮

新增 `test_trend_lane_ownership_20260918.py`（**22 项**）：

- 决策矩阵逐条（规则失效 / 裁量过闸 / 两个禁止动作 / 滚仓 / DCA / 默认持有 / 禁止优先）；
- 纯函数性与"无执行层依赖"的 AST 守卫；
- 接线守卫：`manage_position` 必须 import 该模块，**不得在本地再定义一份禁令**；
- **棘轮**：`midlong_*` 模块数 ≤ 24、其中"两车道共用 + 长线补丁"的 ≤ **16**、
  归属判定报告必须存在且覆盖关键模块 —— 这样合并只许减、不许增。

## 四、运行中验证（部署后实测）

| 项 | 实测 |
|---|---|
| 部署一致性 | `boot = HEAD = 69e54e4`、`matches_disk = true` |
| 趋势车道跳过日内保护 | 每 ~20 分钟一次：`[Paper][轮99] BTC long 属趋势车道（tier=long nature=trend_follow）→ 跳过…` |
| BTC #4712（唯一趋势仓） | 仍持有 **17.37h**、止损 **+2.50%**（距现价 −2.86%）、浮盈 +8.52 —— 未被收紧 |
| 中线仓 | BNB/ETH/SOL/ASTER 4 笔，止损均 ≈ −1.5%（中线自己的口径），两条车道互不干扰 |
| 回归 | 广域 **1063 通过 / 3 失败**（3 项均为既有 market_maker `TailGates`）；`verify_audit` 67/0、`verify_rotation96` 12/12 均 exit 0 |

**诚实说明**：**滚仓尚未观察到实盘触发** —— 长线仓的 LLM 复查受 `last_trend_review_ts`
节流（4h），BTC 上次复查在轮99 之前、下次窗口要等到复查周期到期。
所以"滚仓路径已恢复"目前是**测试级验证**（决策矩阵 17 项 + 接线守卫），
要等下一个复查窗口才能在日志里看到 `manage_pyramid` / `pyramid_skip`。

## 五、后续（阶段3 剩余 / 阶段4 / 阶段5）

| 项 | 内容 |
|---|---|
| 3b | 把 `manage_position` 里长线分支的**执行**也搬进 `trend_lane_manager.run()`（本模块加 `run()`，调用方换一行） |
| 3c | 与 `midlong_exit_guard.py`（152 行、3 处长线补丁）合并 —— 出场守卫本就属于车道决策 |
| 3d | 8 个"待判定"模块逐个按内容分类：纯因子层（`*_factors`/`cold_pool`/`regime_weights`）可保留共用，策略层必须分 |
| 4 | 命名空间分离：`MID_*` / `LONG_*` 前缀 + 164 个共用键的迁移表 |
| 5 | 入口分离：`midlong_executor` 建仓链按车道分叉；`mlto_cycle` 的 tier 分支改成两条流水线 |
