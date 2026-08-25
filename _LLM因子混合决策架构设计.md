> **归一指针(2026-08-25)**: 本文档已被《统一设计与执行计划_LLM因子融合与交易大脑合并归一_V2.md》归一，执行以该文件为准（本文档 13 处断言已被代码复查修正，见统一文件 §2）。
# LLM × 因子混合决策架构设计

> **日期**：2026-08-24　**状态**：设计稿（待评审后进入 P0）
> **前置**：《LLM→因子切换亏损根因调研报告》（同目录）+ `_research_llm_factor/code_archaeology.md`
> （代码考古：插入点/file:line）+ `_research_llm_factor/external_research.md`（30 条文献）。
> 本文所有插入点均为代码考古实测；所有开关均为现存或新增 env 级配置，可回滚。

---

## 一、设计原则（来自调研的 7 条铁律）

| # | 原则 | 一句话 |
|---|---|---|
| P1 | **频率分层** | LLM 低频高价值（thesis/regime/复查），因子高频低延迟（信号/执行）；LLM 永不逐 tick 择时，因子永不单独决定"该不该做" |
| P2 | **选择性 > 放量** | 信号级成本后净期望为负时，每多开一单多亏一单；开单量由证据 + EV Governor 控制，不用"放宽门禁"换流量 |
| P3 | **方向与 regime 是生存层** | 空头条件化；MR 只在 ranging 启用；regime 突变实时切打法 |
| P4 | **出场智能恢复** | 恢复被数据证明净正的 LLM 复查通道（配 min_hold + AI_SOURCES 门控）；碎平通道加最小持有期 |
| P5 | **先修尺子再谈晋升** | 一切因子晋升以"成本后、样本外、regime 分层、时间切分 PBO"为准 |
| P6 | **LLM 与因子互证** | LLM 决策必须引用因子证据（cite-or-reject）；因子信号过 LLM 抽样否决/对抗批判；冲突双轨记账、按结果调权 |
| P7 | **钱跟着证据走** | 正期望层保配额，未证明层乘数 0/0.1 灰度；fractional Kelly + 波动率目标 + 回撤调控 |

---

## 二、总架构：七层分工（谁做什么、谁成就谁）

```
┌─────────────────────────────────────────────────────────────────┐
│ L6 资金与风控   EV Governor · fractional Kelly · vol targeting · tier预算  │
├─────────────────────────────────────────────────────────────────┤
│ L5 出场层       统一出场状态机（唯一出口）· min_hold · AI_SOURCES 最严门控   │
│                 ├─ 因子管：SL / TP / trailing / staged_tp / 超时         │
│                 └─ LLM 管：hold_review / ai_reverse / 失效复查（恢复）      │
├─────────────────────────────────────────────────────────────────┤
│ L4 决策融合     因子提案(Propose) × Regime门 × Thesis门 × LLM批评家(抽样)     │
│                 冲突 → 仲裁 → 双轨归因 → owm 权重(mlto_signal_weights)      │
├─────────────────────────────────────────────────────────────────┤
│ L3 Thesis 层    LLM 方向论题 + 失效条件 + 证据缺口（4h/1d，异步）             │
├─────────────────────────────────────────────────────────────────┤
│ L2 Regime Governor  4h/1d 市场状态机（规则骨架 + LLM 语义校准）              │
│                  方向不对称门 · 空头条件化 · 转换日切换                    │
├─────────────────────────────────────────────────────────────────┤
│ L1 因子引擎     信号生成（scalp 5m / mid 4h-1d / long 1d）· IC 加权投票      │
├─────────────────────────────────────────────────────────────────┤
│ L0 数据与成本底座  真实手续费记账 · 条件信号评价 · 时间切分PBO · 跨域特征注入    │
└─────────────────────────────────────────────────────────────────┘
```

**相互成就的两个方向（本设计的核心）**：

- **因子成就 LLM**：①因子摘要（z-score/IC/regime 分）进 LLM prompt，LLM 决策必须引用字段
  （已有先例：scalp_flash_veto 吃 `factor_breakdown`，scalp_loop.py:773）；②因子预筛噪声，
  LLM 只在"有统计 edge 的候选"上花 token；③因子证伪 LLM——LLM 方向与因子共振不一致时降权/降仓。
- **LLM 成就因子**：①regime/方向门让因子只在有 edge 的域开火（M4：MR 在 trending 接飞刀）；
  ②对抗批判（FactorCritic）过滤假因子（Harvey t≈3 精神）；③LLM 挖矿/写公式扩充因子库
  （ai_factor_discovery 已存在）；④thesis 的失效条件喂给因子仓的 `factor_invalidated` 语义。

---

## 三、分层设计（每层：现状 → 改动 → 验收）

### L0 数据与成本底座（一切结论的前提）

| 改动 | 位置 | 说明 |
|---|---|---|
| paper 手续费真实记账 | `paper_trading_engine.py` 费率路径 | 修 maker 强制/费率低估（近 14 天 1081 笔只记 0.10 手续费）；**所有 paper 结论按实盘再差 8bp 折算** |
| 回测 fill 默认 next_open | `factor_backtest_scorer.py`（fill_model） | 消灭 close 虚高 |
| PBO 时间切分 CSCV | `dsr_pbo.py` | 按时间分组而非 ICIR 值分组；DSR_MIN_SYMBOLS 对齐实际币数 |
| 条件信号评价模式 | 已落地 M0-C1（`factor_engine`） | 反转/MR 因子用"超阈值当根触发 + 持有 fwd 根 + 真实成本"评价——继续全量重扫 |
| 跨域特征注入 mid | 仿 `inject_orderflow_for_factors`（scalp_loop.py:500） | 给 FactorRoute 加 CVD/OI/funding 一致性门（代码考古 §5 核心缺口：mid 只吃 4h/1d OHLCV） |

**验收**：手续费按名义×8bp 记账，误差 <20%；重扫 1115 因子后 MR 可用候选 ≥4 个进入条件评价轨道。

### L1 因子引擎（只管"哪里有统计 edge"，不管"该不该做"）

- scalp：保留 `scalp_factor_router` + `scalp_ranging_mr` 分流，但**信号质量度量改为
  "成本后 net_ret 按 direction×regime×打法 分层"**（M7：alpha 存在于结构里）。
- mid：FactorRoute 机制不变（IC 加权投票、弱 IC 不反手都正确），但把它的输出
  定义为 **Propose（提案）而非 Decide（决定）**——决定权交给 L4。
- 因子晋升消费链：scalp 档因子 0 active 是断链（`scalp_active_factor_set` 已有消费者骨架），
  打通"条件评价 → 影子晋升 → scalp allowlist"。
- LLM 挖矿：`ai_factor_discovery_service` 已本地化，保持；FactorCritic（本地 35B MoE）fail-open 保留。

**验收**：scalp 档 active 因子 0 → ≥4（台阶 3 目标）；每个因子带 regime 分层 IC 报告。

### L2 Regime Governor（方向与 regime 是生存层）

**规则骨架 + LLM 语义校准**的混合状态机，输出 3 个门：

| 门 | 内容 | 证据 |
|---|---|---|
| G-方向 | 市场上行期默认只做多；空头需 `trending-down + funding 极端 + 4h 共振向下` 三条件 | M1：short -104 vs long +50 |
| G-regime | MR 打法仅 `ranging`；trending → 只走趋势/突破打法；转换日（当日已实现波动/ADX 突变）实时切换 | M4：转换日 MR -19.41/-36.60 |
| G-共振 | 与 4h/1d 主趋势相反的同向仓位降仓/禁开 | 旧多周期硬约束（H1-H5）已有 scalp 版 |

实现：规则先行（已在 `scalp_execution_gate` 方向段 + `evaluate_scalp_mtf_constraint`），
**LLM 作为语义校准层**：KlineAnalyst 已在跑（本地 14B，每轮 rotate），其输出升级为
"regime 标签 + 置信度 + 依据"结构（不写自由文本），与规则分类冲突时按 L4 仲裁。

**验收**：空头笔数占比从 ~50% → ≤20%；MR 在 trending 日零开单；转换日切换延迟 ≤ 1 个 tick 周期。

### L3 Thesis 层（恢复 LLM 的"论点"能力——本次去 LLM 损失的核心）

- **恢复路径**：`backend/services/mlto/` 整包仍在（orchestrator/qual_layer/thesis_store/debate_layer，
  仅被 tests 引用）。分两步：
  1. **Shadow 模式**（P1）：`mlto_cycle.py` 重开 thesis 任务提交（`MIDLONG_MID_VIA_MLTO=true`
     但下游不落单，只写 `mlto_thesis` 表 + 与 FactorRoute 的冲突日志）——零交易风险。
  2. **上线模式**（P2）：thesis 输出 `direction + llm_conviction + invalidation + missing_evidence`
     作为 mid/long 入场的 **gate（方向过滤）与 sizing 输入**，不做 tick 级买卖。
- thesis 的失效条件（invalidation_json 已有字段）喂给因子仓：`factor_invalidated` 语义
  从"活跃因子<阈值"扩展为"活跃因子<阈值 **或** thesis 失效条件触发"（`midlong_position_manager.py`
  `_factor_invalidated_reason` L139）。
- 成本控制：thesis 只在 4h/1d 级触发（每 symbol 每 4h 一次），云端 DeepSeek 或本地；
  **预算上限**（如 200 次/天），复用 `llm_budget` 机制。

**验收**：thesis 覆盖率（有活跃 thesis 的 mid/long 候选）≥80%；thesis 方向与事后收益的
方向一致率 >55%（1 周 rolling）；LLM 调用成本 < $3/天。

### L4 决策融合与互证（"相互成就"的实现层）

```
因子提案(Propose, L1) ──┐
Regime 门(G, L2) ────────┤
Thesis 门(方向, L3) ──────┼──→ 合成放行/否决/降仓 ──→ LLM 批评家(抽样 15%) ──→ 执行
信号级证据(cost-net) ────┘        （双方一致→正常仓；                       │
                                  一方否决→降仓/否决；       冲突→归因表→owm 权重调整）
```

- **cite-or-reject**：任何 LLM 否决/确认必须引用具体字段（因子 z、regime 分、funding 值、
  历史教训 id）——已有语义模板（FactorCritic「驳回必须引用字段」）。
- **抽样而非全量**：批评家只审 35–44 分边缘单（FlashVeto 原语义，`scalp_flash_veto.py`
  已实现 needs_veto 分层）——**重开 `SCALP_VETO_MODE=tiered` 即可**（代码考古：零改动回滚）。
  高频 tick 下 LLM 延迟不可接受，抽样 15% + 本地 14B + 云端兜底。
- **冲突归因**：双轨记账（decision_source=llm/factor/hybrid 落库），冲突样本进入
  `mlto_signal_weights`（llm/framework 权重表已存在，win_count/loss_count 字段待接真数据），
  按 rolling 结果调权——**让数据决定谁对，而不是预设谁对**。
- **owm 权重消费**：权重进入 sizing 乘数（llm 权重高 → thesis 主导仓；framework 高 → 因子主导仓），
  与 `midlong_position_manager` 六维的第②维金字塔联动。

**验收**：每笔交易带 decision_source；冲突率、双方胜率、调权轨迹周报可见；
边缘单被 LLM 批评后（通过 vs 否决）的成本后净收益差 >0（否决真的在过滤亏损）。

### L5 出场层（恢复赚钱通道 + 统一状态机）

- **恢复**（数据证明净正的通道）：
  - `SCALP_AI_REVERSE_DISABLED=true → false`：恢复开单后 AI 复审的 close/reduce（LLM 期
    ai_reverse/reverse_netting 通道），但**强制过 `AI_SOURCES` 最严门控 + min_hold（short 1h）**，
    避免历史碎平重演；
  - `MIDLONG_REVIEW_LLM=false → true`：mid/long 方向复查恢复 LLM（trend_agent.review_position），
    min_hold 保护已有（mid 12h / long 72h，M0-11）——**这正是"LLM 智能 + 规则护栏"的组合**；
  - 因子仓防碎平保留：`entry_source=factor_route` 跳过方向复查，仅 `factor_invalidated` 离场
    （`midlong_position_manager.py` L984-991），升级语义见 L3。
- **统一**：所有 LLM 退出走 `unified_exit_state_machine` 唯一出口（`exit/unified_exit_state_machine.py`），
  `master_running_close` 对 scalp 免除（M5：单笔 -24%/保证金）。
- **参数对齐信号**（已改，观察）：TP 1.8% / SL 1.2% / 超时 20min，验证 TP 命中率 24%→≥45%、
  超时 47.5%→≤25%（台阶 1 指标）。

**验收**：出场通道 7 → ≥12 条；`hold_timeout_review`/`ai_reverse` 恢复后其净 pnl 为正；
TP 命中率、超时率达标；无 1h 内碎平（除硬 SL/风控）。

### L6 资金与风控（钱跟着证据走）

- **EV Governor 按层分配**（已存在，改分配逻辑）：trend/swing（LLM 管理，正层）保配额；
  scalp 因子层乘数 0.1 灰度，**连续 5 个交易日成本后正期望 → 0.25 → 0.5**，不达标不回滚。
- **fractional Kelly**：仓位 = f·Kelly（f=0.25 起步），估计误差下不过激（文献：全 Kelly 过激）。
- **波动率目标**：按已实现波动缩放名义敞口（Moreira & Muir），scalp 名义上限 3% equity 不变。
- **回撤调控**：账户回撤 >15% → 全局乘数 ×0.5；>25% → 只允许正层（trend/swing）开仓。
- **pair_research 类账户**：任何新研究账户初始乘数 0.1 且单日熔断（149 账户单日 -390 的教训）。

**验收**：账户 14 周回撤 ≤5%；scalp 乘数按证据阶梯调整有日志轨迹；无单日 >10% 账户级回撤。

### L7 学习闭环（让"相互成就"持续进化）

- 双轨归因 → `mlto_signal_weights` 调权 → `trading_wisdom`（教训注入 prompt，表已存在）→
  prompt 进化（`PROMPT_EVOLUTION_ENABLED=true` 已开）→ 因子晋升/衰减（`factor_decay_monitor`
  接调用点——现在是死代码）。
- 周报三张表：①LLM vs 因子分层盈亏（decision_source×nature）②regime 分层因子 IC ③冲突裁决正确率。

---

## 四、分阶段落地计划

### P0 止血与恢复生存层（1–3 天，全部为开关回滚/小改，可逐项回滚）

| # | 动作 | 改动 | 风险 |
|---|---|---|---|
| P0-1 | 空头条件化（三条件全齐才开空） | `scalp_execution_gate` 方向段 | 低（M1 数据支撑） |
| P0-2 | 恢复 FlashVeto：`SCALP_VETO_MODE=tiered` | env 回滚 | 低（本地 14B 已常驻） |
| P0-3 | 恢复 AI 复审：`SCALP_AI_REVERSE_DISABLED=false`（过 min_hold） | env 回滚 + min_hold 断言 | 中（历史 ai_reverse -2.41，需观察） |
| P0-4 | 恢复 mid/long LLM 复查：`MIDLONG_REVIEW_LLM=true` | env 回滚 | 低（min_hold 已就位） |
| P0-5 | 手续费真实记账 | `paper_trading_engine.py` | 低（只改记账不改交易） |
| P0-6 | scalp 因子层乘数 ×0.1 灰度（EV Governor） | `budget_service` | 低（止血最快的一刀） |

**P0 验收**：日亏速率回到 ≤ LLM 时代水平（-3/天）；出场通道 ≥10 条；手续费按实记账。

### P1 Regime Governor + Thesis Shadow（1 周）

- L2 落地（G-方向/G-regime/G-共振 + KlineAnalyst 结构化 regime 标签）。
- L3 Shadow：`MIDLONG_MID_VIA_MLTO=true` 但下游不落单，thesis 与 FactorRoute 冲突率入表。
- L0 跨域特征注入 mid（CVD/OI/funding 一致性门）。

**P1 验收**：空头占比 ≤20%；转换日 MR 零开单；thesis 冲突率有周报。

### P2 互证上线（1–2 周）

- thesis 上线为 mid/long 入场方向门 + sizing 输入（仍不做 tick 买卖）。
- FactorCritic 扩展到交易信号抽样批判（15% 边缘单）。
- `mlto_signal_weights` 接真 win/loss 数据，owm 权重进 sizing。

**P2 验收**：LLM 否决后的净收益差 >0；owm 调权轨迹可见；LLM 成本 < $3/天。

### P3 资金纪律与学习闭环（1–2 周）

- EV Governor 分层分配 + fractional Kelly + vol targeting + 回撤调控。
- L7 闭环接线（归因→权重→wisdom→prompt 进化→因子衰减监控）。

**P3 验收**：账户周回撤 ≤5%；周报三张表产出；scalp 乘数按证据阶梯调整。

### P4 观察与收紧（持续）

- 每 2 周对拍：混合系统 vs 纯因子（paper 双轨）vs 纯 LLM（历史基线）——**让架构选择本身也被数据检验**。
- scalp 档因子 ≥4 active 进入 allowlist 消费后才讨论 scalp 乘数上调。

---

## 五、配置开关总表（新增/回滚）

| 开关 | 现值 | 目标 | 语义 |
|---|---|---|---|
| `SCALP_VETO_MODE` | off | **tiered** | FlashVeto 边缘 LLM 否决恢复 |
| `SCALP_AI_REVERSE_DISABLED` | true | **false** | 开单后 AI 复审恢复（过 min_hold） |
| `MIDLONG_REVIEW_LLM` | false | **true** | mid/long 方向复查恢复 LLM |
| `MIDLONG_MID_VIA_MLTO` | false | P1 起 **true（shadow）→ P2 上线** | 中线 thesis 恢复 |
| `MASTER_MIDLONG_LLM_MODE` | summary | summary（保持） | Master 长线段不回滚（V2 保留） |
| `LONG_TREND_V2` | 1 | 1（保持） | 长线规则入场保留，LLM 做 thesis 门 + 复查 |
| `HYBRID_LLM_CRITIC_SAMPLE_PCT` | 新增 | 15 | 边缘单 LLM 批评抽样率 |
| `HYBRID_SHORT_REQUIRE_3COND` | 新增 | true | 空头三条件硬门 |
| `HYBRID_MR_REGIME_ONLY` | 新增 | true | MR 只在 ranging 启用 |
| `HYBRID_EV_SCALP_MULT` | 新增 | 0.1 | scalp 层资金乘数灰度起点 |
| `HYBRID_KELLY_FRACTION` | 新增 | 0.25 | fractional Kelly 系数 |

---

## 六、风险与失败模式（双向诚实）

| 失败模式 | 表现 | 缓解 |
|---|---|---|
| LLM 幻觉/被叙事带偏 | 依据不存在的"新闻"开仓 | cite-or-reject（必须引用字段）+ 结构化输出 + 只做门/批评不做 tick 择时 |
| LLM 延迟/成本失控 | 高频链路被拖慢、账单爆炸 | 抽样 15% + 本地 14B 优先 + 预算上限 + 云端只给 thesis 层 |
| LLM 不可复现（TradingAgents 教训） | 同样输入不同输出 | 温度降 0、JSON 约束、影子先行、按 rolling 结果调权而非信任单次 |
| 因子衰减/过拟合（Harvey/McLean） | 晋升后 IC 消失 | 条件评价 + 真实成本 + regime 分层 + 衰减监控 + 换手惩罚 |
| 双方同时看错（共振过度自信） | 一致放行→大亏 | 仓位由外部风险尺度（Kelly/vol/回撤）决定，不由信心决定 |
| 双方频繁冲突 | 系统瘫痪不开单 | 冲突双轨记账、周报裁决、owm 权重按结果收敛；冲突期降为 0.5 仓而非 0 |
| 纸面账再次失真 | paper 好看实盘崩 | L0 手续费真实记账是 P0 前置，未修好不放大仓位 |

---

## 七、一句话总结

**恢复 LLM 做它被证明擅长的事（情境判断、方向、失效条件、智能出场），让因子做它被证明擅长的事
（低延迟信号、机械执行、统计筛选），用「Propose×Gate×Critic×归因调权」把两者焊在一起，
用「真实成本 + 资金纪律」保证无论谁对谁错系统都不至于死——这就是"平衡、相互成就"的全部含义。**
