# 中长线 LLM 主脑说明书

> 给操盘的人看：钱谁说了算、什么时候会开仓、关掉哪些会空转的东西。

## 一句话

中线 / 长线**新开仓的唯一理由**：包月双模型（Minimax + GLM）对这笔交易给出一张新鲜的 `accepted` 论题，并且写了「建议开仓」。

因子分、V2 趋势规则、E1 日任务、图审、日度简报都是**证据**，不能自己开仓。
图审有新鲜共识时才做否决（禁令 / 强反向 / 亏后再开要同向支持）。**没图照样分析、照样可以开。**

没有新鲜论题 = 不开。不会悄悄退回因子开仓。

## 三层反应（不是按钟点做梦）

循环每 45 秒盯盘。**问模型**只在行情变了才发生，不会 45 秒问一次。

| 层 | 谁在看 | 多久反应 | 干什么 |
| --- | --- | --- | --- |
| 立刻下手 | 硬止损 / 失效价打穿 / 熔断 / `should_close` | 每个 45 秒 tick | 平仓，不等模型说话 |
| 行情变了重问 | 4h 或日线收盘、现价冲击、失效价靠近、追高、图审打架、重事件 | 变盘池每轮最多 5 个 | 重新问包月双模型 |
| 最长沉默 | 成功票：中线 4h / 长线 8h；**失败票短退避 20/40 分钟** | 失败票不得锁死满 TTL | 防止低分假鲜空转 |
| 图片图审 | 日线/周线形态 | 8 小时 | 附加否决，不挡分析 |

中线的「实时」是 **4 小时 K 一收盘就重问**，不是等到我们自己的 4 小时闹钟。

一根 K 还没走完，价格已经冲过 1.2%（中线）或 2.5%（长线），或者已经涨飞不好追，也会提前重问。冲击类重问最少隔 30 分钟。

**契约**：论题必须带数字 `invalidation.price`，否则不能 `accepted`。软证据（缺图/taker/E1）不得绑架 `recommend_open`。

没图照样分析、照样可以开。有新鲜图才做否决。

## 操盘看板该看什么

1. 主脑状态：`brain=llm` · 无票禁开 · 缺图可开 · 短线新开禁
2. 论题台账：方向 · 开/观望 · should_close · 失效价 · watch 原因 · run_id
3. 证据栏：因子 / V2 / 图审（标明不能自己开仓）

页面入口：Agent 监控「论题台账」；`/api/full-auto/tick-intervals` 的 `mid_mode=llm_brain`。

## 决策链

1. 这个币这个周期**已经有仓** → 每个 tick 看硬止损、论题失效价、`should_close`；熔断 / 组合超限仍然可以立刻平。
2. **没有仓**，论题还在这根决策 K 上、价格没跑飞、模型达成共识 → 按论题决定开或不开。
3. 决策 K 收盘、价格冲击、失效价靠近、论题过期、图审打架、高严重度新事件 → 打一次包月双模型。缺图**不是**重问或禁开的理由。
4. 两个模型共识不到 0.7、两边都写中性、或合成方向是中性 → 不算交易论题，持有现金，不开。
5. 模型说开 → 还要过硬闸：资金费、震荡、组合敞口、熔断。价格已经追飞或论题所在的 K 已经收盘，先重问再开，不拿旧票追。**有新鲜图**时再加图审否决；没图这一闸放行。
6. 真正下单只走一个口：`execute_midlong_open(..., source="mlto")`。持仓上会留下论题编号。

## 模型必须吃到的数据

缺了现价或 K 线，模型即使写「建议开仓」也会被代码改成不开，确信也不会超过 60%。缺图不会触发这条。

- 现价；中线 1h/4h/1d，长线 4h/1d/1w
- 图审若有：结构、持仓建议、失效条件（8 小时扫一轮，窗内已有共识则跳过）
- 日度简报里这个币的方向
- 资金费 / 持仓量 / 清算
- 当前仓、浮盈、止损距离、拿了多久
- 近 14 天同币两侧盈亏（专门打「亏完再开」）
- 因子 / V2 / E1 的看法（标明「证据，不是指令」）
- 未过期的高严重度事件

## 开关（改 `.env` 必须同步 `RUNTIME_CONFIG_FACTS.md`）

| 开关 | 现在 | 意思 |
| --- | --- | --- |
| `MIDLONG_BRAIN_MODE` | `llm` | 唯一合法值。不要加 hybrid。 |
| `MIDLONG_NO_THESIS_NO_OPEN` | `true` | 没有真论题就不开。改成 `false` 是紧急全禁，不是放开因子。 |
| `MIDLONG_CHART_REQUIRED` | `false` | 默认缺图仍可开。改成 `true` 才是紧急收紧。 |
| `ANALYSIS_TREND_CHART_SEC` | `28800` | 图审 8 小时扫一轮。 |
| `ANALYSIS_TREND_CHART_FRESH_H` | `8` | 8 小时内已有共识则跳过。 |
| `MIDLONG_THESIS_TTL_MID_S` | `14400` | 成功票最长沉默 4 小时；失败票见 FAIL_BACKOFF。 |
| `MIDLONG_THESIS_TTL_LONG_S` | `28800` | 成功票最长沉默 8 小时。 |
| `MIDLONG_THESIS_FAIL_BACKOFF_MID_S` | `1200` | 失败票 20 分钟后再问。 |
| `MIDLONG_THESIS_WATCH_REFRESH_CAP` | `5` | 变盘优先刷新上限。 |
| `ANALYSIS_WEEKLY_ENABLED` | `false` | 周复盘默认不注册。 |
| `ANALYSIS_TIMING_ENABLED` | `false` | 择时默认不注册。 |
| `SCALP_OPEN_DISABLED` | `true` | 纸盘和实盘都禁止短线新开；已有短线仓只能平。 |
| `MIDLONG_MID_VIA_FACTOR_ROUTE` | `false` | 因子不再自己开中线。 |
| `LONG_TREND_V2` | `0` | V2 不再自己开长线。 |
| `TREND_E1_ENABLED` | `false` | E1 不下单。 |
| `TREND_E1_LONG_LANE_EXCLUSIVE` | `false` | 不挡 LLM 长线。 |
| `THESIS_SHADOW_ENABLED` | `false` | 停本地 14B 影子论题。 |
| `PAIR_SELECTOR_WATCHER_ENABLED` | `false` | 停短线选币扫描。 |
| `E5_SHADOW_ENABLED` | `false` | 停事件影子车道。 |

紧急停机：把 `MIDLONG_NO_THESIS_NO_OPEN` 改成 `false`，或把 `MIDLONG_BRAIN_MODE` 切走。这两种都是**全禁中长线新开**，不会把方向盘还给因子。若要回旧因子开仓，必须另外把 `MIDLONG_MID_VIA_FACTOR_ROUTE` 设回 true，并且改代码里的紧急停机闸——仓库默认不提供静默回退。

## 任务生死表

启动后看 `/api/ops/jobs`。停用项不应该再出现，或者 `last_run` 不再增长。

**立刻停（漏开或空转）**

| 任务 | 怎么停的 |
| --- | --- |
| 短线新开 | `SCALP_OPEN_DISABLED` 在纸盘/实盘 `place_order` 硬拒 |
| 选币扫描 `pair_selector_watcher` | 开关 false，启动不注册 |
| E5 影子扫描 | `E5_SHADOW_ENABLED=false`，不注册 |
| E1 日任务下单 | `TREND_E1_ENABLED=false`，日 cron 也不再注册 |
| 短线独立循环 / 日检 / 信号结算 | `SCALP_OPEN_DISABLED` 时不注册 |
| 本地 thesis 影子 / 委员会影子 | 两个 SHADOW 开关 false |
| `capital_allocate` | `ALLOCATOR_APPLY=false` 时不注册 |
| `agent_timing` | 代码不再注册（与分析层择时重复） |
| `agent_param_search` | `AGENT_PARAM_SEARCH_ENABLED=false` 不注册 |
| QAA 积分套利 | `QAA_REBATE_SCHEDULE_ENABLED=false` |
| QAA 九卡主循环 | 保持不进主循环，启动日志写「已退役」 |

**保留（饲料 / 观察 / 底座）**

- 日度简报、图审、事件扫描、账本评分
- 观察 Agent：`signal_review` / `execution_qa` / `anomaly`（不控仓）
- 风控 tick、OMS、数据中心、K 线同步、`experiment_advance`
- 中长线周报（每周一 4:00，也会写 `backend/data/midlong_reports/latest.md`）

## 怎么验收

纸盘跑一段时间后：

1. 新开仓的来源必须是 `mlto`，并且能对上论题编号。
2. 短线 / E1 / 因子路由的**新开**必须是 0。
3. 抽几张论题：缺现价时不能出现「建议开仓」。
4. 周报里「LLM 主脑验收」一节能看到来源拆分。

## 代码入口

- 主脑编排：`backend/services/mlto/brain.py`
- 论题账本：`backend/services/mlto/thesis_store.py`（读 analytics 库，重启不丢）
- 下单口：`backend/services/full_auto/midlong_executor.py`
- 独立循环接线：`backend/services/full_auto/mlto_cycle.py`
