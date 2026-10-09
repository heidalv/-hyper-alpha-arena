# AI 选币 · 超短交易专用设计（L1 高频车道）

> 版本 v1 · 2026-09-20
> 适用范围：`mm_asterdex` 车道（及后续任何 30s–5min 尺度的高速车道）
> 前提阅读：`L1赛道重构设计_中短期高频交易_20260920.md`、`docs/MM_实时深度看板设计_F246补全.md`
>
> **✅ 实现状态（2026-09-20）：三层链路已落地并跑通**
> `backend/services/coin_select_hft.py`（hard_gates / score / llm_veto / apply_to_lane）
> `scripts/hft_universe_select.py` + `.bat`（调度入口）
> 计划任务 `DSH_HFT_UNIVERSE_SELECT`（每 30 分钟自动调配）
> 当前宇宙：固定 `ASTER/XRP/SOL/DOGE/UNI` ∪ AI `SEI/VIRTUAL/PENDLE/1000SHIB/ARB` = 10 币

---

## §0 为什么不能复用现有 AI 选币

现有选币链（`coin_select_platform_service.py`）是为**方向性中长线**设计的：

| 维度 | 现有选币 | 超短做市需要 |
|---|---|---|
| 标的角色 | 方向性开仓（做多做空） | 被动双边挂单赚点差 |
| 核心排序量 | `horizon ∈ {scalp, midlong, long}` 的方向性动量 | **点差宽度 / 成交吞吐 / 队列可达性** |
| 数据粒度 | 24h–数日的 K 线量价 | **20 档盘口 + 逐笔（105ms）** |
| LLM 角色 | **选币**（`ai_verdict` / `confidence` / `direction_bias`） | **只做风险否决**（见 §3） |
| 边际来源 | 价格方向 | **点差 − 逆选择**（方向无关） |

**关键反转（这是本设计的第一原则）**：

> 已证：做市的往返净 ≈ **整个点差**（H8/H10 双实现复现：ASTER +1.383/+1.375bp），
> 而**方向预测不能产生净利润**（H13：`spread`/`gap` 的高 IC 无法转成每笔进场正净额，
> 无条件均值 −0.426bp，t=−23.8；最好的子集 +0.047bp，t=+0.7）。
>
> ⇒ **超短做市的选币量必须是机械量（点差/深度/吞吐），LLM 不能参与排序。**
> 让 LLM 按"方向动量"选币喂给做市车道，等于用错误的维度选标的。

**现状佐证**：`board_scalp` **恒为 0**（`coin_select_platform_service.py:963-965` 自述「短线已停」），
近 3 次扫描只产出 `board_midlong` 28–30 条。**超短档不存在。**

---

## §1 超短选币的硬约束（数据现实）

选币**输出必须是可交易的**——不可交易的标的即使边际再好也不能进宇宙。

| 约束 | 依据 | 判定 |
|---|---|---|
| **必须有 20 档深度采集** | 无 20 档就无法评估队列可达性；Aster 上游是 100ms 全量簿，模拟仓必须同源 | **硬闸**：不在 `asterdex_depth_snapshots` 覆盖内的标的直接排除 |
| 必须有逐笔成交 | 无逐笔无法做队列感知成交模拟 | **硬闸** |
| 数据新鲜 | 停采标的的成交率会被严重高估（ZEC 实测点差 2.4bp 但成交率仅 **1.2%**） | **硬闸**：depth age > 60s 排除 |
| 最小名义可满足 | Aster min notional **$5** | 用 `asterdex_instruments` 校验 |

**实测覆盖（决定可选集合）**：

| 数据源 | 覆盖 | 备注 |
|---|---|---|
| `asterdex_depth_snapshots`（20 档，105ms） | **10 币**：BTC, ETH, SOL, XRP, ASTER, HYPE, ZEC, ARB, ONDO, SEI | 选币的**唯一可选池** |
| `asterdex_trades`（逐笔） | 32 币 | |
| `asterdex_book_ticker`（top，36ms） | 32 币 | |
| `market_orderbook_snapshots`（15s，带 5bp 深度） | BTC/ETH/SOL/UNI/DOGE/XRP/BNB 新鲜；**ASTER/VIRTUAL/XPL/WIF/SYN 无**；**ZEC/TAO 停在 09-17** | 43 天历史，用于跨期稳健性 |
| `market_trades_aggregated`（15s） | 19 币 | 30 天历史 |

⇒ **可选池 = 10 币**（有 20 档深度）。这是硬上限，扩容必须先改
`research_l1/services/aster_ws_ingest.py --depth-symbols`。

---

## §2 机械层：超短评分（纯规则，无 LLM）

**为什么纯机械**：这些量全部是客观可测的盘口统计量，用 LLM 只会引入噪声与不可复现性。
每天（或每 6h）重算一次，写入 `lane_registry.meta.universe`。

### 2.1 评分输入（全部来自 20 档深度 + 逐笔，窗口 24h 滚动）

| 符号 | 含义 | 数据源 |
|---|---|---|
| `spread_bp` | 点差中位数（bp） | `asterdex_depth_snapshots` |
| `fill_rate` | 队列感知模拟成交率（30s HOLD，贴盘口） | 深度 + 逐笔（复用 `tick_feed.py` + H6/H10 口径） |
| `q_ahead_usd` | 最优价档位前方量的中位数 | 深度（我方挂上后的预计排队额） |
| `depth_usd_5` | 前 5 档名义总额 | 深度 |
| `rv_1m` / `rv_5m` | 1/5 分钟实现波动（bp） | `book_ticker` |
| `age_s` | 深度最新快照年龄 | `asterdex_depth_snapshots` |

### 2.2 评分公式

```
预期每笔往返毛收益      E_bp      = spread_bp                          # H8：往返净 ≈ 整个点差
预期吞吐                λ         = fill_rate × 每小时候选数            # 候选数由 tick 频率决定
预期每笔超时损失        L_bp      = (1 − p_revert) × rv_hold            # rv_hold = 持仓期波动
                                       ^^^^^^^^^ 无信号时为 1（现状）

score = w1 · E_bp · λ  −  w2 · L_bp · λ  −  w3 · q_ahead_usd / 1000
```

**现状（无 `P(revert)` 信号）下 `p_revert` 取实测完成率**，故：

```
score ≈ λ × (spread_bp − (1−fill_completion) × rv_hold) − w3 · q_ahead/1000
```

⚠️ **这套公式的诚实边界**：H13 已证 `spread`/`gap` 条件化**不能把每笔进场净额拉正**。
所以 `score` 的用途**不是"选出能赚钱的标的"**，而是：
1. **排序**——在同样为负的候选里，选出最不差的（H10 实测 ASTER 最好 −0.11 USD vs HYPE −3.27 USD，差 30 倍）；
2. **剔除明显不可交易的**（低成交率 + 高停采风险）。

**必须记住**：`score > 0` 不代表可盈利，只代表**相对占优**。在找到 `P(revert)` 信号之前，
这条车道是**研究/观测性质**，不是盈利性质。

### 2.3 硬闸（任一不过直接排除，不参与排序）

1. `symbol ∉ DEPTH_SYMBOLS` → 排除
2. `age_s > 60` → 排除
3. `fill_rate < 5%` → 排除（样本量不足，无法评估）
4. `spread_bp < 0.8` → 排除（**BTC 0.013bp / ETH 0.166bp 实测往返净为负**，点差必须够宽）

---

## §3 LLM 层：只做风险否决，不做打分

**职责边界**：LLM **不参与排序、不产生分数、不决定方向**。它只回答一个问题：
「未来数小时内，这个标的是否会发生**会让被动挂单被系统性逆向选择**的事件？」

### 3.1 输入（给 LLM 的上下文）

每个候选币一段结构化简报：
```jsonc
{
  "symbol": "ASTER",
  "spread_bp": 1.48, "fill_rate": 0.341, "rv_1m": 12.3, "rv_5m": 8.1,
  "funding_next_8h": 0.00031,        // 资金费率（若有）
  "depth_usd_5": 412000,
  "recent_news": [...],               // 新闻标题（若已有管线）
  "window": "近 6 小时"
}
```

### 3.2 输出契约（**窄**，便于校验）

```jsonc
{ "symbol": "ASTER", "veto": false, "severity": "low",
  "reason": "无异常资金费率与事件，盘口深度稳定" }
```

- `veto: true` ⇒ **仅当**存在明确事件风险（重大公告、资金费率极端、上线/下线、链上异常）；
- `severity ∈ {low, medium, high}`；`high` 必须 `veto: true`（机器强制）；
- `reason` 必填且**必须引用简报中的具体数字**（防止 LLM 空泛发挥）。

### 3.3 为什么这样切分

| 如果让 LLM 排序 | 如果只让 LLM 否决 |
|---|---|
| 不可复现、不可审计 | 可复现（否决是二值） |
| 与机械量重复且引入噪声 | 补足机械量看不见的信息（事件） |
| H13 已证方向/动力量无预测力，LLM 无从下手 | 事件风险是 LLM 的真实优势（读新闻） |

---

## §4 车道接入：固定币 + AI 选币

### 4.1 宇宙结构

沿用现有约定（`_platform_fixed_symbols()` 从运行中 `FullAutoSession` 取固定币，
AI 看板是"固定池之外的新机会发现"），但**为超短车道独立配置**：

```
mm_asterdex.meta.symbols = 固定币(5) ∪ AI选币(N)
                            ↑               ↑
                     人工指定、不参与评分  机械层排序 + LLM 否决后取前 N
```

### 4.2 现状问题（必须修）

| 问题 | 实测 | 影响 |
|---|---|---|
| 固定币含**无深度**的 DOGE/TAO | DOGE n=0；TAO 停采 | 无法做队列模拟；DOGE 成交率数据不可得 |
| 固定币含**停采**的 ZEC | 最后 09-17 | 点差 2.4bp 但成交率仅 1.2%，**看起来最宽实际最不可交易** |
| 宇宙 = 5 个 | 与"前5固定 + 后5 AI"的目标不符 | 需要扩到 10 |

**建议的固定币**（从 10 个有深度的币里，按 §2.2 的实测排序初选）：

| 币 | 点差(bp) | 成交率 | 建议 |
|---|---|---|---|
| ASTER | 1.477 | 34.1% | ✅ 固定（点差 × 吞吐双优，H10 实测最好） |
| XRP | 1.553 | 16.4% | ✅ 固定 |
| SOL | 1.031 | 13.2% | ✅ 固定 |
| BTC | 0.013 | 16.6% | ⚠️ **点差过窄，往返净 −0.058bp**，建议不固定 |
| ETH | 0.166 | 11.3% | ⚠️ 同上（+0.113bp，太薄） |
| ZEC | 2.401 | **1.2%** | ❌ 成交率过低 |
| HYPE | 0.901 | 4.9% | ❌ 成交率过低 + markout −0.267 |
| ARB/ONDO/SEI | — | **0.2%/2.5%/0%** | ❌ 成交率过低 |

⇒ **实测可用的固定币其实只有 3 个**（ASTER/XRP/SOL）。这是数据说的，不是设计偏好。
**扩池的唯一办法是扩深度采集**（§1 硬约束）。

---

## §5 与现有选币链的关系

**不改动** `coin_select_platform_service.py` 的现有 `mid/long` 逻辑（它在为方向性车道服务）。
**新增一条并行链路**：

```
新模块 backend/services/coin_select_hft.py
  ├── compute_hft_stats(symbols, window_h=24) -> Dict[symbol, HftStats]
  │     数据：asterdex_depth_snapshots + asterdex_trades + asterdex_book_ticker
  │     复用：market_maker/tick_feed.py（队列感知成交模拟）
  ├── score(hft_stats) -> List[(symbol, score, reasons)]      # §2.2 + §2.3 硬闸
  └── llm_veto(candidates) -> Dict[symbol, {veto, severity, reason}]  # §3
```

**落库**：`lane_registry.meta.universe`（不改表）：
```jsonc
"universe": {
  "fixed": ["ASTER", "XRP", "SOL"],
  "ai": ["ZEC"],
  "as_of": "...", "score_table": {...},
  "vetoed": {"HYPE": "funding spike"},
  "excluded": {"DOGE": "no depth", "TAO": "stale 72h"}
}
```
**前端**：车道详情页的深度看板（F247，已实现）按 `meta.symbols` 渲染；
新增一个「选币评分」小块显示 `score_table` 与排除原因。

---

## §6 实现顺序

| # | 任务 | 状态 |
|---|---|---|
| **C1** | `compute_hft_stats`（点差/更新数/深度状态；**点差用近 2h** 而非 3.9 天全历史） | ✅ 已完成 |
| **C2** | 硬闸 + 机械评分（§2.3/§2.2） | ✅ 已完成 |
| **C3** | `llm_veto`（窄契约 + 失败 fail-open） | ✅ 已完成（LLM 客户端接口需按实际适配） |
| **C4** | 写入 `lane_registry.meta.universe`；`meta.symbols` 由 fixed ∪ ai 派生 | ✅ 已完成 |
| **C5** | 调度入口 + 计划任务（每 30 分钟自动调配） | ✅ 已完成 |
| **C6** | 扩深度采集 13 → 23 币 | ✅ 已完成 |
| **C7** | 前端「选币评分」块（显示 score / 排除原因 / 是否有 veto） | ⏸ 待做 |

## §6.1 实测结果（2026-09-20）

**深度采集**：`book/trades` 覆盖 32 币，`20 档深度` 覆盖 **23 币**（由 13 扩容）。
新增 10 币：`VIRTUAL/PENDLE/1000SHIB/PUMP/NEAR/ADA/ENA/SUI/AAVE/WLD`
（依据：近 2h 点差 ≥ 5bp 且盘口活跃）。

**选币结果**：

| 位 | 币 | score | 点差(bp) | book 更新/2h |
|---|---|---|---|---|
| 固定 | ASTER / XRP / SOL / DOGE / UNI | — | 1.18–6.87 | 22.9k–113k |
| AI | **SEI** | 104.98 | 20.85 | 117,848 |
| AI | **VIRTUAL** | 93.25 | 20.13 | 46,596 |
| AI | **PENDLE** | 79.42 | 16.97 | 52,627 |
| AI | **1000SHIB** | 64.11 | 12.99 | 98,563 |
| AI | **ARB** | 47.31 | 10.14 | 54,503 |

**被硬闸排除 12 个**（这是设计生效的证据）：
- `BTC`(0.012bp) / `ETH`(0.039bp) / `BNB`(0.534bp) —— **点差过窄，往返净为负**
- `LINK/LTC/AVAX/XLM/XMR/TAO/WLFI/LIT/1000PEPE` —— 无 20 档深度（未纳入扩容）

## §6.2 ⚠️ 三个必须知道的实现坑（已修，勿回退）

1. **`compute_hft_stats` 约 20s**（全表 `percentile_disc`）。**不要放进 HTTP 请求路径**；
   由计划任务每 30 分钟跑一次即可。
2. **计划任务 stdout 默认 GBK(cp936)**：脚本输出任何非 GBK 字符（如 `✓`）会抛
   `UnicodeEncodeError` **并中断脚本**，导致后续**落库步骤被跳过**（实测发生）。
   ⇒ `.bat` 里必须 `set PYTHONIOENCODING=utf-8`，且输出只用 ASCII 标记。
3. **`schtasks /TR` 上限 261 字符**：带引号长路径 + 重定向会超限被拒；
   而"先 /Delete 再 /Create 失败"会**把任务弄丢**。⇒ 用 `.bat` 包一层。

另：**`.bat` 注释必须纯 ASCII** —— 中文 `REM` 在 cmd 的 GBK 代码页下会被截断成杂散命令。

---

## §7 待确认

1. **固定币到底哪几个？** 数据说只有 ASTER/XRP/SOL 可用（BTC/ETH 点差太窄、ZEC/TAO 停采、DOGE 无深度）。是否接受固定币 = 这 3 个？
2. **是否扩深度采集？** 当前 10 币；要凑够"前5固定 + 后5 AI"需要至少 ASTER/XRP/SOL 之外再确认 2 个可用的（ARB/ONDO/SEI 成交率 0–2.5%，HYPE 4.9%，都不达标）。**要么扩采集，要么接受更少的币。**
3. LLM 否决是否需要接新闻源？（当前 `recent_news` 管线是否存在需先核实；若没有，LLM 只能基于盘口/资金费率做否决，价值下降。）
