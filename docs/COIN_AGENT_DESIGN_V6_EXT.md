# HyperAlpha CoinAgent 详细设计（v6 延伸版 · 运行实况修正稿）

> 版本：2026-08-10 修正稿（替代 2026-08-02 初稿）；**同日 P0+P1 已执行落地（见 §0.1）**
> 基准：《幻方对比与量化增强执行计划_v6》（下称 v6）
> 审查基准：**运行实况（日志/DB 实证）> 代码静态 > v6 文档**——本文档所有断言均附运行实证，不再把“代码在但未启用”或“看板在但通道未建”写成“待建”。

---

## §0 证据基线（2026-08-10 核查）

本稿全部判断基于以下实证，后续章节引用统一简写：

| 证据 | 内容 |
|---|---|
| E1 | 主路径 = 平台级 `coin_select_platform_service.py`（1067 行）：管理员 LLM 双视野扫描（prompt「请同时给出短线 scalp 与中长线 midlong 两套判断」），30min 周期，8-09 完成 19 次、8-10 仍在运行 |
| E2 | 决策包已完整实现并落库（`coin_select_candidates` 7,701 行）：symbol / horizon(scalp\|midlong) / verdict(approve\|watch\|reject) / confidence / direction / reason / risk_notes / invalidation / tier |
| E3 | 双视野看板在库：最近扫描 board_scalp=15 / board_midlong=15；`coin_select_scans` 282 次历史 |
| E4 | verdict 分布（最近 1000 条）：approve 470 / watch 397 / reject 133 → **通过率 47%**，v6 §7.4 目标 20-40%，偏高 |
| E5 | 会话内路径同时在跑（auto_coin_selector Cycle 31/32）：VIP 看板跟投注入、孤儿 auto coin 恢复（V5.3，08-10 01:00 实测恢复 IWM/WMT）、分层淘汰 + 冷却拉黑（价格暴跌 -16.6%/-36.6% → very_long → 拉黑 604,800s） |
| E6 | S2-9 三开关：`AUTO_COIN_SCORE_V3_ENABLED=false`（settings L2320）、`AUTO_COIN_LLM_COMPOSE_ENABLED=false`（L2343）、`AUTO_COIN_CORR_DEDUP_THRESHOLD=0.85`（L2341 默认开）；日志中 llm_compose / V3 rescore 命中 **0 条** |
| E7 | 反馈回填断裂：`auto_coin_selections` 0 行（审查时快照，**执行后已解除**：闭环运行中 3252 行，见 §0.1）——v6 §7.3 第 1 项“反馈闭环”已闭环 |
| E8 | 隔离机制已生效：`AUTO_COIN_FORBID_LONG=true`（settings L2214 默认）、V5.3 孤儿币恢复注入 `auto_coin_symbols`（非 symbols）、注入前 `fixed_now` 排除固定币 |
| E9 | `AUTO_COIN_IC_WEIGHTS_ENABLED=true` 但依赖 `auto_coin_selections.factor_snapshot_json + hit_24h` 样本（settings L2329-2330），表 0 行 → `load_ic_samples` 返回 [] 回退静态权重 |
| E10 | `auto_coin_selector.py` L3171-3178：`AUTO_COIN_SOURCE` 默认 `platform_board`，原文「统一路径：只跟投管理员 VIP 短线看板，不再另跑一套市场扫描+账户 LLM」；V3 rescore（L1408）与 llm_compose（L2024 `if not board_src:` 显式排除）挂在被弃用的 legacy 独立扫描路径 |
| E11 | `full_auto_sessions` 0 行，有 `auto_coin_symbols` / `auto_coin_max_slots` 列，**无 `auto_coin_mid_symbols` 列** |
| E12 | QAA AgentCard：qaa 目录 0 命中，选币决策无审计入口 |

---

## §0.1 执行状态（2026-08-10 P0+P1 落地，09:47 核查）

本稿 P0+P1 已按计划执行完毕，下列实证更新 §0 中已过时的断言：

| 项 | 执行结果 | 运行实证 |
|---|---|---|
| P0 反馈回填 | **闭环已在运行**（审查时 0 行快照已被推翻）：`auto_coin_selections` 3252 行（injected 837 / removed 957 / renewed 1365 / blocked 93）；会话 fa_10d44c724e（08-09 18:18 创建，enabled=True slots=5 n_auto=4）带动注入→回填全链路 | 09:46:10 `[CoinRank.feedback] filled_entry=0 updated_24=0 updated_72=0`（周期 900s）；hit_24h 填充 691/3252 |
| P0 基线回填 | 真实命中率基线：**hit_24h=61.1%（422/691）、hit_72h=69.6%（240/345）**；candidates 近似基线 24h/72h≈50% | 验收快照 09:47 |
| P0 IC 死锁 | 已解除（样本就绪）；但 `get_ic_weights` 仍 enabled=False（n=515，note=no_positive_ic）——**vola_score=+0.162 为唯一显著正 IC 且未入 FACTOR_KEYS**，V3 当前回退静态权重（base .55/flow .2/whale .1/news .1/sector .05） | 验收快照 §4 |
| P1a 通过率校准 | prompt 已收紧（`_build_dual_horizon_prompt` 追加第 7/8 条：approve 每批 20-40%、四要素≥3 项 + confidence≥0.6）；审核节流核验为**已落地**（`AUTO_COIN_AI_REVIEW_INTERVAL_SEC` 默认 1800s，L1438-1439 注释“阶段C：3600→1800”） | 通过率 08 时 46.7% → 09 时 31.9%（收敛趋势，新 prompt 完整小时数据待 10 时） |
| P1b S2-9 移植 | 三处移植完成：① llm_compose 解除 `if not board_src:` 排除（L2021-2057）；② board 路径 V3 IC 重排 + 相关去重（L2109-2131）；③ 同板块上限（L2177-2187）；.env 全局开 V3（SCORE_V3/LLM_COMPOSE/IC_WEIGHTS=true，CORR_DEDUP=0.85） | 09:46:30 日志链：`LLM 组合决策: 11 -> 5` + `V3 rescore applied: 5 candidates kept` + `S2-9 board 相关去重: 5 -> 2` |
| P1b 缺陷修复 | **llm_compose 初次启用即暴露 asyncio 缺陷**：`asyncio.run() cannot be called from a running event loop`（Cycle 在 async 事件循环内同步调用 caller，Python 3.12 还禁止同线程嵌套运行另一 loop）→ 修复为检测 running loop 后转独立线程执行（`_llm_compose_caller` L4235-4248），真实 LLM 双路径验证通过 | 09:46:30 `[AutoCoinSelector] LLM 组合决策: 11 -> 5 (picked=['KAITO','ZEC','XAU','COHR','DIS'])` |
| 后端状态 | 09:45 重启加载全部修改，单实例运行；Cycle 2 done 13567ms（LLM 组合 ~10s 纳入决策链） | rank_source=platform_board degraded=None replaced=0 |
| 遗留（非本计划范围） | ① decision_feedback_service L417 `paper_orders` 表缺失高频警告（净扣费归因失败）；② `raw_market_events` 索引损坏（idx_raw_market_events_hot_lookup block 8223→8241）致 MarketDataIngestQueue 写入失败；③ DataCenter 多币 1h/4h 数据过期（WIF/IWM/BULLA 等，数据断供） | 日志持续告警 |

---

## §1 现状实况（替代初稿"疑点根因分析"）

初稿的核心假设是"选币系统 = 5 维拍脑袋评分 + LLM 布尔审核，一切关键能力待建"。运行实况核查推翻了该假设：**系统当前是"平台级双视野 LLM 扫描 + 会话内看板跟投"的完整双路径，7 项"待建"中 6 项已实现或半实现**（见 §1.1），真正的缺口收敛为 6 项（见 §1.2），以及一个必须回答的问题：**S2-9 为什么实现却从未运行**（见 §1.3）。

### 1.1 初稿断言 → 运行实况修正（7 项）

| 初稿断言 | 运行实况 | 判定 |
|---|---|---|
| 决策包非审核布尔，待建 | 平台路径已产出完整决策包并落库（E2） | 已实现，初稿过时 |
| mid 池待建 | board_midlong 看板已有 15 币（E3）；缺的是"看板→注入池"通道与 4 周验证 | 半实现，缺口 = 注入通道 |
| IC 加权主评分待建 | S2-9 代码在但开关默认 false，从未运行；当前打分 = factor_soft 单因子 + 市场分（E6） | 已实现未启用 |
| LLM 决策者待建 | llm_compose 代码在但开关 false，从未运行；平台路径 LLM 是"扫描者"非"组合决策者"（E6） | 已实现未启用 |
| SafetyNet 待建 | 黑名单/冷却/淘汰/过期/孤立恢复/固定白名单全部在运行中实证（E5/E8） | 已实现运行中 |
| AutoLaunch 钉 AI 币 | append 用户指定 primary_symbol；V5.3 隔离已生效；full_auto_sessions 0 行无脏数据实证 | 断言不成立 |
| 5 维拍脑袋 + 审核布尔 | 那是旧代际 auto_coin_selector 残留；当前主路径是平台双视野 LLM（E1） | 描述过时 |

### 1.2 真实缺口（运行实况收敛，6 项）

1. **反馈回填未闭环**（P0）：~~`auto_coin_selections` 0 行~~ → **已解除**：闭环已在运行（3252 行实证），基线 24h=61.1% / 72h=69.6%（§0.1）——v6 §7.3 第 1 项要求已达标
2. **通过率校准**（P1）：47% 高于 v6 §7.4 目标 20-40%（E4）→ **已执行**：prompt 收紧落地 + 节流核验为已落地 1800s（§0.1），通过率呈收敛趋势（31.9%），完整验证待样本
3. **S2-9 启用决策**（P1）：~~需移植~~ → **已执行**：三处移植完成 + 全局开 V3 + llm_compose asyncio 缺陷修复（§0.1），日志实证 `LLM 组合决策: 11 -> 5` / `S2-9 board 相关去重: 5 -> 2`
4. **选币 6-7 维因子 IC 体系**（P2）：factor_soft 单因子软融合 → factor_pool 子集（方向动量/规模/流动性/资金费率/链上/波动率甜区），v6 §7.3 第 2 项
5. **midlong 看板→注入池通道**（P3）：board_midlong 已有 15 币（E3），注入缺 `auto_coin_mid_symbols` 列（E11）+ ≤3 槽 + 4 周验证前置（v6 阶段3）
6. **QAA AgentCard 注册**（P4）：qaa 目录 0 命中（E12），选币决策无审计入口

### 1.3 核心根因：S2-9 为什么实现却从未运行

三条独立原因叠加，**均非运行故障**：

1. **架构双路径切换（主因）**：系统从"独立市场扫描 + 账户 LLM"切换到"平台看板跟投"统一路径（E10 原文）。V3 rescore（L1408 Enrich 后）与 llm_compose（L2024 `if not board_src:` 显式排除）都挂在被弃用的 legacy 路径上——S2-9 成了"挂在废弃路径上的新能力"
2. **灰度无载体**：settings.py L2319 注释"默认全关 = 旧行为"（2026-08-02 初稿的灰度模式）；V3 仅 paper 默认开（PAPER_AUTO_COIN_SCORE_V3=true）但 full_auto_sessions 0 行 = 无 paper 会话载体；llm_compose 另需 coin_select 用途 LLM key
3. **鸡生蛋死锁**：`AUTO_COIN_IC_WEIGHTS_ENABLED=true` 但样本源 `auto_coin_selections` 0 行 → `load_ic_samples` 返回 [] 回退静态权重（E9）→ V3 rescore 即便开了也无真 IC 可算；而回填又依赖注入记录——循环断在回填一环

**推论（本文档核心决策）**：S2-9 的正确启用方式不是"原地开开关"（legacy 路径已无人走），而是**把 V3 rescore + llm_compose 移植到 board 跟投路径**——在 `_candidates_from_platform_board` 注入前做 IC 加权重排与组合取舍；同时先打通反馈回填解除 IC 死锁。

---

## §2 与 v6 逐项映射（修正版）

初稿引用"§7.3.2 / §7.3.3"不存在——v6 原文为 **§7.3 第 2 / 3 / 4 项**（L402-407）。逐项映射如下：

| v6 条目 | 位置 | 现状（2026-08-10） | 本稿承接 |
|---|---|---|---|
| §7.3 第 1 项 反馈闭环核验+深化 | L401 | 部分：`coin_rank/feedback.py` 已落地（写 price_after_24h/72h + hit_24h/72h、30 天聚合 hit_rate、衰减乘数），但**选币注入链路未回填**（E7） | §3.2 反馈回填任务 |
| §7.3 第 2 项 选币因子体系 | L402 | 未完成：当前打分 = factor_soft 单因子 + 市场分（E6）；ic_weights 基础设施在但无样本 | §3.4 6-7 维因子 IC 体系 |
| §7.3 第 3 项 LLM 从审核员到选币决策者 | L403-406 | 半实现：平台 LLM 已是"扫描者+决策包产出者"（E2），但非"组合决策者"；审核节流仍 1h（未改 30min） | §3.3 决策包契约化 + §3.5 S2-9 移植 |
| §7.3 第 4 项 组合视角选币 | L407 | 半实现：相关去重/同板块上限代码在 legacy 路径（CORR_DEDUP_THRESHOLD=0.85 默认开但 board 路径未消费） | §3.5 S2-9 移植 |
| §7.3 第 5 项 换手与节奏自适应 | L408 | 未实施：扫描间隔固定 30min，无 regime 驱动 | 阶段3 后续 |
| §7.3 第 6 项 AI 审核校准闭环 | L409 | 未实施：通过率 47% 无校准（E4） | §3.6 通过率校准 |
| §7.4 验收标准 | L411-416 | 命中率不可算（0 样本）；通过率 47% 越界；一致率未度量 | §8 验收 |
| 阶段2 第 12 项 AI 选币升级 | L633 | 未落地（S2-9 未启用） | §3.5 |
| 阶段3 "选币池经 4 周验证后供中长线币源" | L641 | **未满足**：初稿 mid≤3 方案缺 4 周验证前置 | §3.7 mid 注入通道 |

---

## §3 CoinAgent 方案（三层：已运行升级 / 已实现未启用核验 / 真实缺口新建）

### 3.1 架构总览

```
[管理员 LLM 双视野扫描] ──30min──▶ coin_select_candidates（决策包落库 E2）
                                      │
        ┌─────────────────────────────┤
        ▼                             ▼
board_scalp(15) ◀──跟投──▶ auto_coin_selector（board 路径 L3176）
        │                             ├─ 注入（fixed_now 排除 + V5.3 隔离 E8）
        │                             ├─ 分层淘汰 / 冷却 / 黑名单（运行中 E5）
        │                             └─ 【S2-9 移植点】注入前 IC 重排 + 组合取舍 ← 本稿核心
board_midlong(15) ──【P3 缺口】──▶ auto_coin_mid_symbols（≤3 槽 + 4 周验证门）
                                        │
                                        ▼
                              中长线币源（v6 阶段3，mid_view 子结构并入长线）
```

- **已运行（只升级不重建）**：平台双视野扫描、决策包落库、看板跟投注入、隔离/淘汰/冷却
- **已实现未启用（核验+移植）**：V3 rescore、llm_compose、IC 权重基础设施、相关去重
- **真实缺口（新建）**：反馈回填、决策包契约化、6-7 维因子 IC 体系、mid 注入通道、QAA 注册、通过率校准

### 3.2 反馈回填任务（P0，解除 IC 死锁）

- **注入联动回填**：会话注入 auto coin 时写 `auto_coin_selections`（symbol / session_id / injected_at / price_at_inject / factor_snapshot_json），复用 `coin_rank/feedback.py` 的 `write_price_feedback` 语义：24h/72h 后 K 线取价回填 price_after_24h/72h + hit_24h/hit_72h —— **执行状态：已在运行**（注入断点不存在，3252 行实证，§0.1）
- **历史基线回填**：对 coin_select_candidates 中 8-06 以来 approve 且已注入的币用历史 K 线回填基线命中率 —— **执行状态：已完成**（24h=61.1% / 72h=69.6%，§0.1）
- **验收**：`auto_coin_selections` 行数持续增长且 hit_24h/hit_72h 填充率 ≥95%；`load_ic_samples` 返回真实样本而非 []（E9 解除）

### 3.3 决策包契约化升级（P1）

平台已产出决策包字段（E2），契约化 = 定 schema + 扩落库 + 联动注入：

```json
// 决策包 v1（在现有列基础上固化）
{
  "symbol": "X", "horizon": "scalp|midlong",
  "verdict": "approve|watch|reject",
  "confidence": 0.0-1.0,
  "direction": "long|short",
  "reason": "LLM 证据链摘要",
  "risk_notes": "风险提示（含冷却/黑名单命中情况）",
  "invalidation": "失效条件（何时出池）",
  "tier": "flash|pro",
  "evidence": { "factor_score": ..., "market_score": ..., "news": ..., "track_record": ... }
}
```

- 落库扩展：`coin_select_candidates` 增加 `evidence` JSONB、`retired_at`（失效时间，配合 invalidation）
- 注入联动：会话跟投只消费 verdict=approve 且未失效的行；注入时把决策包 id 写入 `auto_coin_selections`，形成"决策→注入→回填"全链可追溯
- **初稿增量价值保留**：EvidencePack/Decision Pack 双包分层（EvidencePack = LLM 证据输入聚合，Decision Pack = 输出契约）——但落地形式改为"在平台既有字段上固化"，而非从零建

### 3.4 选币 6-7 维因子 IC 体系（P2，唯一真实研究层缺口）

现状：`score.py` 五维启发式 `base = 0.45*liq + 0.25*cs_mom + 0.20*ts_mom + 0.10*uni` + factor_soft 单因子软融合 35%——**未直接消费 ic_weights**。

升级（v6 §7.3 第 2 项）：
1. **因子集**：factor_pool 子集 6-7 维——方向动量 / 规模 / 流动性 / 资金费率 / 链上 / 波动率甜区（+ 可选新闻情绪）
2. **IC 评估卡**：复用第五章评估器（Spearman IC / 分层命中率），对齐 v6 阶段2 第 6 项因子级回测器落库；输出"选币预测力"报告卡
3. **权重落地**：`ic_weights.py` 已有 Spearman IC + 负 IC 弃用 + 45 天回看 + TTL 900s 基础设施（消费方 L1189/2031/2085/2267），补样本后自然生效；缺维重新归一化沿用 `_compose_v3_score`（L1121）
4. **因子衰减自动下线**：非 v6 §7.3 第 2 项末尾要求，沿用 IC 非正的因子权重置 0 机制

### 3.5 S2-9 移植决策（P1，核心）

**结论：把 V3 rescore + llm_compose 从 legacy 路径移植到 board 跟投路径，而非原地开开关。**

具体移植点（`auto_coin_selector.py` board 路径 L3176 `_candidates_from_platform_board` 之后、注入之前）：
1. **注入前 IC 重排**：对 candidates 用 `get_ic_weights`（E9 样本就绪后）打分重排，替代“看板顺序直投”；`_score_v3_enabled` 在 board 路径生效 —— ✅ 已落地（L2109-2131），日志 `V3 rescore applied: 5 candidates kept`
2. **LLM 组合取舍**：`llm_compose` 解除 `if not board_src:` 排除 —— ✅ 已落地（L2021-2057）；**启用后修复 asyncio 缺陷**（事件循环内同步调用 → 独立线程执行，`_llm_compose_caller` L4235-4248），日志 `LLM 组合决策: 11 -> 5`
3. **组合视角**：相关去重 `dedup_by_correlation` + 同板块上限 `enforce_max_per_sector` 迁移到 board 注入前 —— ✅ 相关去重已落地（L2109-2131，阈值 0.85），日志 `S2-9 board 相关去重: 5 -> 2`；同板块上限已落地但 `AUTO_COIN_SECTOR_SIGNAL_ENABLED=false` 默认未启用
4. **灰度顺序**：~~先 paper 观察 2 周再实盘~~ → **执行决策：直接全局开 V3**（用户拍板，.env `AUTO_COIN_SCORE_V3_ENABLED=true`）；IC 权重因无正 IC（vola 未入 FACTOR_KEYS）回退静态，管线生效为 P2 提供实证
5. **物理安全网不动**：黑名单/冷却/流动性硬地板/同簇冗余仍由规则层兜底（v6 §7.3 第 3 项原则）

### 3.6 通过率校准（P1）

- 现状 47%（E4）vs 目标 20-40%（v6 §7.4）
- 机制：verdict 阈值自适应——用回填后的命中率数据（§3.2）拟合 confidence→命中率曲线，校准 approve 边界；审核战绩（谁 approve 的币命中率如何）入周报并注入 prompt（v6 §7.3 第 6 项）
- 短期动作：LLM prompt 明确“宁可错过不可错杀”—— **✅ 已落地**（`_build_dual_horizon_prompt` 追加第 7/8 条，§0.1）；审核节流 ~~1h→30min（待改）~~ → **已核验为已落地**：`AUTO_COIN_AI_REVIEW_INTERVAL_SEC` 默认 1800s（L1438-1439 注释“阶段C：3600→1800”，审查时误写“待改 30min”）

### 3.7 midlong 看板→注入池通道（P3）

- **前置条件（v6 L641 硬性要求）**：选币池经 **4 周验证**（hit_24h/hit_72h 基线达标）后才可作中长线币源——初稿"mid≤3 直接建池"违反该阶段序
- 通道：`full_auto_sessions` 增加 `auto_coin_mid_symbols` 列（E11 无此列）→ 会话内 mid 注入（≤3 槽）→ 中线并入长线通道（mid_view 子结构，SwingAgent 独立路径已废弃）
- 风控沿用：禁 long 已由 `AUTO_COIN_FORBID_LONG` 覆盖（E8）；mid 槽位也走固定白名单隔离（open_gate.py `_is_auto_coin` 正向白名单）
- 验收：4 周后 mid 池命中率 ≥ 基线 → 才允许进中长线币源

### 3.8 QAA AgentCard 注册（P4）

- 新建 QAA AgentCard：输入 = 决策包（E2 字段）+ 回填结果；审计项 = approve→注入一致率（v6 §7.4 ≥95%）、verdict 漂移、失效条件执行率
- 与 Hermes wisdom 衔接：按币 pattern 沉淀 wisdom（初稿增量价值），经既有 QAA pipeline（阶段2 第 13 项）调度

---

## §4 LLM 角色（扫描者 → 组合决策者 → 审核校准）

| 角色 | 现状 | 升级目标 | 模型档位 |
|---|---|---|---|
| 双视野扫描者 | ✅ 运行中（E1） | 保持，prompt 注入历史战绩（§3.6） | 现用档（保留） |
| 组合决策者 | 未启用（llm_compose 关） | §3.5 移植后启用，30min 批处理不进热路径 | quick 档（Flash）即可 |
| 审核校准 | 无 | §3.6 阈值自适应 + 战绩周报 | deep 档（Pro）周报 |
| 因子生成 CodegenCritic | 已接真实 LLM（alpha_miner L345-351） | 不动 | quick（Flash，v6 §12.3 表格 L684 要求） |

对齐 v6 §12.3 四层利用方案：选币链路全部 quick（Flash）档，周报/校准 deep（Pro）档；审核节流 1h→30min（v6 §7.3 第 3 项）。

---

## §5 算力协同（沿《双显卡分工与运行要求》）

沿用既有文档分工，新增仅一处：S2-9 移植后 llm_compose 为 30min 批处理调用，归入 GPU 卡 LLM 服务队列（非热路径）；IC 权重计算为 CPU 轻量任务（Spearman 秩相关，45 天回看 7-15 币样本）归 CPU 卡；无新增训练负载。

---

## §6 开源/论文选型（修正现状表述）

保留初稿选型方向，按 2026-08-06 开源框架审计结论修正现状表述：

| 框架 | 初稿表述 | 修正后现状 | 本稿决策 |
|---|---|---|---|
| Qlib | "待集成" | 无 import；研究层以自有管线（WFO/DSR/PBO）**移植式等效替代**，审计曾误判为未集成 | 不引入，沿用等效替代 |
| FinRL | "待评估" | 无 import（DRL 整体下线）；但 **rl_optimizer.py L53 真实 import stable_baselines3.PPO**——按 v6 10.3 设计"借 SB3"执行 | 维持借 SB3 现状，不扩 DRL |
| PyPortfolioOpt | "待评估" | 无 import；组合约束**思想式集成**（同簇去重/池上限已实现） | 不引入，沿用思想式实现 |
| vectorbt | "待评估" | 无 import | 维持不引入（真未集成，保留可选项） |
| joblib | — | gp_miner/mcts_miner loky 真实使用（32 workers 并行） | 维持 |
| Ray | — | 无 import；符合"Windows 受限 joblib 优先"取舍 | 维持 |
| FreqAI | — | 仅对标注释，符合"不引入"取舍 | 维持 |

红线不变：**不开新实盘链路**；产出全走 purge → lifecycle → shadow_judge → online_weights。

---

## §7 实施序（P0-P4）

| 阶段 | 任务 | 对应 | 前置 | 状态 |
|---|---|---|---|---|
| P0 | 反馈回填打通（注入联动 + 历史基线） | §3.2 | 无（立即） | ✅ 已完成（2026-08-10）：闭环已在运行，基线 61.1%/69.6%（§0.1） |
| P1a | 通过率校准（prompt 收紧 + 节流核验） | §3.6 | P0 部分样本 | ✅ 已完成（2026-08-10）：prompt 收紧 + 节流核验已落地 1800s |
| P1b | S2-9 移植（board 路径 IC 重排 + llm_compose + 相关去重），全局开 V3 | §3.5 | P0 样本就绪 | ✅ 已完成（2026-08-10）：三处移植 + 开关启用 + llm_compose asyncio 修复（§0.1） |
| P2 | 6-7 维因子 IC 体系 + 评估卡（**vola_score=+0.162 应纳入 FACTOR_KEYS**） | §3.4 | P1b 灰度数据 | ⏳ 待执行（正 IC 实证已备） |
| P3 | midlong 注入通道（列 + ≤3 槽 + 4 周验证门） | §3.7 | 池 4 周验证达标（v6 L641） | ⏳ 待执行 |
| P4 | QAA AgentCard + wisdom | §3.8 | P0-P3 全链路数据 | ⏳ 待执行 |

---

## §8 验收标准

**v6 §7.4 六项（对齐原文 L411-416）——P0+P1 执行后进度**：
1. 命中率可算：✅ 基线已回填（24h=61.1% / 72h=69.6%，§0.1）
2. LLM 选币通过率 20-40%：🔄 收敛中（46.7% → 31.9%，新 prompt 完整小时数据待 10 时观察）
3. 新评分（IC 加权）命中率 > V2/V3 基线：🔄 V3 管线已生效（静态权重回退），待样本对照（P2）
4. 组合视角：✅ 相关去重已生效（`S2-9 board 相关去重: 5 -> 2`），LLM 组合已生效（`11 -> 5`）
5. AI 决策包 approve 与最终注入一致率 ≥95%：⏳ 未度量（§3.8 审计未建）
6. 审核节流 1h→30min：✅ 核验为已落地（1800s，§0.1）

**本稿新增**：
- S2-9 灰度验收：~~paper 2 周~~ → 直接全局开 V3（用户拍板）；对照起点 = 基线 61.1%/69.6%，IC 正因子样本（vola=+0.162）供 P2
- mid 池验收：4 周验证期 hit_24h/hit_72h ≥ 基线后放行中长线币源；mid 注入 ≤3 槽且全程禁 long

---

## §9 代码锚点（2026-08-10 核查）

| 组件 | 位置 | 状态 |
|---|---|---|
| 平台双视野扫描 | `coin_select_platform_service.py`（L316 双视野 prompt） | 运行中 |
| 决策包落库 | `coin_select_candidates` 表 | 运行中 |
| board 跟投主路径 | `auto_coin_selector.py` L3171-3178（AUTO_COIN_SOURCE=platform_board） | 运行中 |
| V3 rescore | `auto_coin_selector.py` L1020 `_score_v3_enabled` / L1121 `_compose_v3_score` / L1176 `_apply_v3_rescore` / L2109（board 路径） | ✅ 已启用（日志 `V3 rescore applied`） |
| llm_compose | `auto_coin_selector.py` L2021-2057（board 路径，已解除排除）；`_llm_compose_caller` L4213-4256（asyncio 修复：running loop → 独立线程） | ✅ 已启用（日志 `LLM 组合决策: 11 -> 5`） |
| 相关去重 | `auto_coin_selector.py` L2109-2131（board 路径消费，CORR_DEDUP_THRESHOLD=0.85） | ✅ 已消费（日志 `S2-9 board 相关去重: 5 -> 2`） |
| 同板块上限 | `auto_coin_selector.py` L2177-2187 `enforce_max_per_sector` | ✅ 已移植（`AUTO_COIN_SECTOR_SIGNAL_ENABLED=false` 默认未启用） |
| IC 权重 | `coin_rank/ic_weights.py`（Spearman IC / 负 IC 弃用 / 45 天回看 / TTL 900s） | ✅ 样本就绪（n=515）；`get_ic_weights` enabled=False（无正 IC，vola=+0.162 待入 FACTOR_KEYS） |
| 打分现状 | `coin_rank/score.py`（五维启发式 + factor_soft 35%） | 运行中，未接 IC（P2） |
| 反馈回填 | `coin_rank/feedback.py`（L125 write_price_feedback / 900s 周期）+ `auto_coin_selections`（3252 行） | ✅ 闭环运行中（基线 61.1%/69.6%） |
| 隔离/风控 | `AUTO_COIN_FORBID_LONG`（settings L2214）/ V5.3 孤儿恢复（L363-389）/ 分层淘汰 / 冷却黑名单 | 运行中 |
| mid 池 | `full_auto_sessions` 无 `auto_coin_mid_symbols` 列 | 缺口（P3） |
| QAA | qaa 目录 0 命中 | 缺口（P4） |
| LLM 因子生成 | `alpha_miner.py` L345-351 CodegenCritic 已接真实 LLM | 运行中 |
| 审核节流 | `AUTO_COIN_AI_REVIEW_INTERVAL_SEC` 默认 1800s（`coin_select_platform_service.py` L1438-1439 注释“阶段C：3600→1800”） | ✅ 已落地（核验） |
