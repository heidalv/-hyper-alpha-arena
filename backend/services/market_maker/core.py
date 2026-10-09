# -*- coding: utf-8 -*-
"""[F58] 做市核心（纯函数 + 纯状态）—— 可单测、无 IO。

设计依据：《复合策略与交易系统全面改造设计_V2》§1.2 / 《短线正收益改造设计_V1》§4.2

实测约束（F52/F53）：
  - **挂宽 ≥5bp 才转正**；贴盘口（w≤2bp）在多数币上为负；
  - 盈利集中在流动性最好的主流币（BTC/ETH/BNB/XRP/SOL/DOGE）；
  - 逆选择约为捕获价差的 2 倍 → 必须靠「挂宽 + 快速管理库存」而非抢队列。

本模块只做三件事：
  1. 报价计算（宽度自适应 + 库存偏斜）；
  2. 库存账本（净敞口、已实现捕获、单边持仓时长）；
  3. 成交判定（与 F52 离线模拟**同口径**，保证线上与离线可比）。
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ═══════════════════════ 报价 ═══════════════════════

def symbol_lookup(raw: object, symbol: str, default: float) -> float:
    """从**逐币字典**里取该币的倍数/比例；缺失或非法 ⇒ `default`（旧行为逐字不变）。

    [h527 2026-09-29] 为什么需要：引擎的挂宽与单币敞口**只有全局一份**
    （`QuoteParams.spread_mult`、`LaneRiskLimits.max_net_directional_ratio`），
    而 h524/h512 实测单币 bp/腿 相差 4 倍（XRP +0.13 vs NEAR −3.53）、
    逐币 taker 占比差 3 倍（BNB 14% vs ENA 42%）⇒ "要么全做要么全不做"，
    而腿量硬约束（≥60/h）恰好由亏损币撑着 ⇒ 必须有逐币旋钮。

    取值优先级：精确币名 → 后缀剥离（`XRP-USDT` / `XRP/USDT:USDT` → `XRP`）→
    大小写回退。**任何异常都返回 default**（`None` / 非 dict / 非数值 / ≤0），
    因此登记表里不写这个键时，所有调用点与改动前**逐字一致** ✓。
    """
    if not isinstance(raw, dict) or not raw:
        return default
    key = str(symbol or "")
    cands: List[str] = [key]
    for sep in ("-", "/", ":"):
        if sep in key:
            cands.append(key.split(sep)[0])
    cands += [key.upper(), key.lower(), key.upper().replace("-", "").replace("/", "")]
    for c in cands:
        if c and c in raw:
            try:
                v = float(raw[c])          # type: ignore[arg-type]
            except (TypeError, ValueError):
                return default
            return v if v > 0 else default
    return default


@dataclass(frozen=True)
class QuoteParams:
    """报价参数（默认值来自 F52 实测 + 2026-09-09 第十一轮参数扫描）。

    扫描结论（`backend/scripts/sweep_mm_params.py`，8 币 × 30 天回放）：
      - w_base_bp=5、k_inv=1.0、hold=300s、sl=0 → 净 **+0.643bp/笔、+140 USD**
      - w_base_bp=8、k_inv=1.0、hold=300s、sl=0 → 净 +0.985bp/笔（笔数减半）
    即：**更强库存偏斜（k_inv 0.3→1.0）+ 更长的单边持有（300s）+ 关闭超时止损**
    把影子跑从 -5.75bp/笔 翻正。默认取 w=5/k=1.0（金额最大方案）。
    回滚：MM_W_BASE_BP=5、MM_K_INV=0.3 还原旧口径。
    """

    w_base_bp: float = float(os.getenv("MM_W_BASE_BP", "5.0"))        # 基础挂单距离（实测 5–8bp 为正）
    min_width_bp: float = 3.0     # 硬下限：低于此值实测为负（仅约束**加仓**侧）
    min_width_reduce_bp: float = 1.0  # 减仓侧下限：允许贴盘口，避免靠 taker 平仓
    max_width_bp: float = 60.0    # 上限，防止极端波动挂到天外
    # [h729 R2 2026-10-02] 期望值引擎挂宽上限(bp,每侧):概率框架的 δ* 实现。
    # 影子对比(R1)证明 δ*=2.5bp 时 λ(δ)×net_band(0.42δ) 期望净最大
    # (捕获 1.05bp 入 c 档 +5.5bp/腿、填单率 155~252/h)。本参数把两侧半宽
    # 钳到 ≤δ*(覆盖在 max_width_bp 之后,优先级更高);0=关(旧行为)。
    ev_width_cap_bp: float = 0.0
    k_vol: float = 0.5            # 波动放大系数
    # [F97] σ 上限（0=不截断，旧行为逐字一致）：σ 无上界时半宽可被推到 14bp+，
    # 远超真实边缘（实测当前 5 分钟波动可达基准的 5.4 倍）。
    k_vol_sigma_cap: float = 0.0
    k_inv: float = float(os.getenv("MM_K_INV", "1.0"))            # 库存偏斜系数
    # [F204 2026-09-15] 趋势**反向**偏斜（trend-fade skew）——与库存偏斜是**两个独立维度**。
    #
    # 为什么加它（账本实证，2915 笔、严格无未来函数）：
    #   顺势成交（买在上涨 / 卖在下跌）实际净 **−4.053bp** ✗
    #   逆势成交（买在下跌 / 卖在上涨）实际净 **+2.109bp** ✓   差 **6.16bp**
    #   （重度逆势 +3.198bp；反事实：只剔除 |trend|>10bp 的顺势成交，保留 65% 名义，
    #    账面从 −1.07bp 翻到 **+1.11bp** ✓）
    # ⇒ 被动做市赚钱的方式是"**提供流动性吃回复**"，被"顺着走势打中"时才亏 ✗。
    #   `k_inv` 按**库存**偏斜，管不到这一层；这里按**近期走势**偏斜：
    #     上涨 ⇒ 买单挂更远（别追买 ✗）、卖单挂更近（顺势出货 ✓）
    #     下跌 ⇒ 反过来
    # 语义：`k_trend`=0（默认）= **逐字关闭**（旧行为完全不变 ✓）。
    k_trend: float = 0.0
    trend_skew_lookback: int = 60        # 回看期数（快照≈15s ⇒ 60 期 ≈ 15 分钟，与实证口径一致）
    trend_skew_scale_bp: float = 20.0    # 净移动达到该值 ⇒ 偏斜打满（±1）
    # [F217 2026-09-15] **侧选择**（结构性旋钮）：
    #   "both"（默认 = 旧行为）/ "counter_trend"（**只**在逆势侧挂单：
    #   涨了只挂卖、跌了只挂买 —— 依据见 runner.py 的 F217 注释与 F216/F198b/F199）。
    # `side_trend_min_bp`：|近期净移动| 低于它时仍双边挂（防噪声把车道切成单边 ✗）。
    side_mode: str = "both"
    side_trend_min_bp: float = 0.0
    # [F80 2026-09-13] 冻结行情自适应挂宽：近 frozen_lookback 期的**单步最大移动**
    # （max|Δmid|）< frozen_max_move_bp ⇒ 判定行情冻结（微幅振荡、无穿越行情），
    # 挂宽切到 frozen_width_bp。
    # 证据（09-13 冻结日回放）：单段 |Δmid| ≤2bp 时 w7/5/4 全 ≤0、w3 唯一为正
    # （+0.24bp/140 fills）；活跃块 w3 会被逆选择屠杀 ⇒ 必须按行情档位切换。
    # 用单步移动而非全幅：冻结行情是「±1-2bp 高频往返」而非「无移动」——
    # 全幅指标在漂移型冻结下永远超阈值（2h 区间 16bp），单步指标才与
    # 「挂单能否被穿越」的真实条件对应。None = 关闭（旧行为逐字一致）。
    frozen_width_bp: Optional[float] = None
    frozen_max_move_bp: float = 4.0
    frozen_lookback: int = 60
    # [F85 2026-09-14] 复利比例：>0 时每腿名义 = 该比例 × 模拟账户当前权益
    # （权益随已实现盈亏滚动，做市收益自动再投资）。0 = 固定腿量（旧行为）。
    compound_ratio: float = 0.0
    # ══ [F280 2026-09-21] **价差相对挂宽** —— 修「挂宽是绝对 bp，而各币价差相差 1629 倍」✗
    #
    # 根因（H53b/H55 实测，book_ticker 与 20 档 depth 快照交叉核对一致）：
    #   Aster 近 2h 有盘口的 32 个币，**价差 p50 从 0.0124bp（BTC）到 20.19bp（VIRTUAL）**
    #   —— 跨度 **1629 倍**。而引擎的挂宽是**相对中价的绝对 bp**（w_base_bp / min_width_bp）。
    #
    #   用实盘同一个宽度 `avg_width_bp=1.425` 去套：
    #     BTC  半价差 0.0062bp ⇒ 报价在盘口外 **229.7 倍半价差** ⇒ **永远打不到** ✗
    #     ETH  半价差 0.0194bp ⇒ **73.5 倍** ⇒ 打不到 ✗
    #     SOL  半价差 0.4623bp ⇒ 3.1 倍 ⇒ 勉强可竞争
    #     SEI  半价差 9.4687bp ⇒ 0.2 倍 ⇒ 报价**已经在价差内**，被逆选择屠杀 ✗
    #   32 个币里：**可竞争 13 个、太靠外 3 个、太贴 16 个**。
    #   ⇒ **一个绝对 bp 宽度不可能同时适配跨度 1629 倍的价差；任何固定值都必然在一半币上错。**
    #
    # 正确参数化：`挂宽 = spread_mult × (半价差)`，此时：
    #   spread_mult = 1.0 ⇒ 贴最优价（队尾，QP≈1）
    #   spread_mult < 1.0 ⇒ **改善最优价**（进到价差内）⇒ 我们**就是**最优价
    #                       ⇒ 在一个**新建的队列里位于队首（QP≈0）**
    #   这一步很关键：Albers et al. arXiv:2502.18625v2 §5 Table 1 在 Binance BTCUSDT 永续
    #   实盘测得 **队首 −0.058bp vs 队尾 −0.775bp（大 near/小 opp 账本）**，落差 0.72bp；
    #   大/大账本落差达 1.16bp —— **比我们全部边际还大**。
    #   而 15 秒节奏的参与者**唯一**能结构性拿到队首位置的机制就是「改善最优价」
    #   （Arroyo et al. QF 24(1):35–57, 2024 Table 3：改善最优价使成交概率 0.054→0.443，8.2 倍）。
    #
    # 语义：`spread_mult <= 0` ⇒ **逐字关闭**，完全走旧的绝对 bp 路径（旧行为 100% 不变 ✓）。
    spread_mult: float = float(os.getenv("MM_SPREAD_MULT", "0.0"))
    # ── [F286 2026-09-21] **减仓侧**独立的价差倍数 ────────────────────────────
    # 依据（H77，24h，21,527 笔入场，判据已过极端情形单调性测试）：
    #   出库报价 = ask − f×价差 时的**整往返**每笔净额（含强平，分母=全部入场）：
    #     f=0.00（挂 best_ask）  hold60s −1.7624bp   成交率 78.8%
    #     f=0.50                hold60s −1.7795bp   成交率 80.9%
    #     f=1.00（挂 mid）       hold60s **−0.8398bp**  成交率 **93.3%**
    #   ⇒ 出库腿挂 **mid**（f=1）比挂 best_ask 好 **+0.92bp**，
    #     机制自洽：半价差≈0.9bp，而改善量≈0.9bp ⇒ 正是"挂 touch 让掉的价差"。
    #   而引擎当前出库用 `spread_mult=0.9` ⇒ 报价在 `bid + 0.9h` ≈ 0.45×价差处
    #   ⇒ 对应 H77 的 f≈0.5 行（−1.78bp），**不是最优**。
    #
    # 为什么单独开一个参数：`spread_mult` 同时管进场腿与减仓腿，
    # 改它会连进场一起动 —— 而进场侧 H56/H77 已确认 0.9 是合适的。
    # 语义：`<=0` ⇒ 退回 `spread_mult`（不改变任何既有行为 ✓）。
    spread_mult_reduce: float = float(os.getenv("MM_SPREAD_MULT_REDUCE", "0.0"))
    # ── [F288 2026-09-21] **最小挂单距离下限（以半价差为单位）**──────────────
    # 依据（H88 **实盘 561 个真实周期**，无模拟；H89 排除币种混杂）：
    #
    #   edge_bp 区间（= 报价半宽）  周期   强平率    每周期真实净额
    #     (-inf, 0.1071)           112   64.3%    **−0.08610 USD**  ← 最差
    #     [0.1071, 0.3202)         109   10.1%    **+0.00410 USD**  ← 最好
    #     [0.3202, 0.5120)         115    8.7%     −0.00039
    #     [0.5120, 0.6413)         111   11.7%     −0.01498
    #     [0.6413, inf)            114   25.4%     −0.03165
    #   最好−最差 = **+0.09020 USD/周期，3.21 个标准误**（显著）
    #
    #   **H89 的混杂检验**（关键，否则可能只是"窄价差币不行"）：
    #     · 最差档最大单一币占比仅 **33.0%**（XRP）⇒ 跨多个币，不是单一币
    #     · **币内**检验 **6/6 全部同向**（高 edge 更好）：
    #         ASTER 强平 26.1%→7.4%   SOL 31.0%→9.8%   UNI 85.7%→0.0%
    #     ⇒ **edge 是独立因素**，不是币种属性 ⇒ 可调参 ✓
    #
    # 语义：`edge >= min_edge_frac × 半价差`（`0` = 关闭，恢复旧行为 ✓）。
    # 为什么需要**下限**而不是调大 `spread_mult`：H88 显示最优是**内部解**
    # （最宽档也不好，25.4% 强平），所以只能"抬地板"，不能整体放宽。
    min_edge_frac: float = float(os.getenv("MM_MIN_EDGE_FRAC", "0.0"))
    # 报价**绝不穿越**对侧最优价的保护边距（以半价差为单位）。
    # H54 实测：δ 用绝对 bp 时，BTC 上 δ=0.05bp 落在卖一**上方 0.0376bp**，
    # **穿越率 86.89%** ⇒ 那是可成交单（marketable），却拿了 maker 返佣 ⇒ 虚增收益 ✗✗。
    # 有了这个钳制，`spread_mult <= 1-margin` 时报价在数学上不可能穿越。
    spread_cross_margin: float = 0.05
    # ── [h432 2026-09-28] v2 出场架构 · 波动张开价差 ─────────────────────────
    # 文献：HFT 做市盈利集中于高波动期（BBHK 2019），价差 ∝ σ²。现行 spread_mult
    # 固定 ⇒ 尖峰 regime 里"被动成交的价差收益"没有随 σ 放大，趋势段被动出场又
    # 失败 ⇒ 系统在文献最赚钱的 regime 里最亏（审计实证）。本参数把 σ_norm
    # 乘进价差倍数：smult_eff = smult × (1 + vol_spread_k × σ_capped)。
    # 0 = 旧行为逐字一致 ✓。
    vol_spread_k: float = 0.0
    # ── [h432] v2 出场架构 · 减仓侧偏斜消融 ──────────────────────────────────
    # A-S 范式：库存靠报价不对称连续消融，而非"计时器到点 taker 砸单"。本参数把
    # 持仓的**逆向漂移**写进减仓侧宽度：adverse_bp 越大，减仓侧越向 mid 收拢，
    # 到 40bp（旧止损线）且 k=1 时减仓侧挂到 mid 本身 ⇒ 被动成交在 mid（≈免费），
    # 取代"触发止损→宽限追价→taker 在更差价位强平"（实测均 −62bp 的出血点）。
    # 0 = 旧行为逐字一致 ✓。adverse_bp 由 runner 按 (avg_mid, mid, 持仓方向) 计算。
    exit_skew_k: float = 0.0
    exit_skew_scale_bp: float = 40.0   # adverse 达到该 bp 时偏斜到 100%
    # [h527 2026-09-29] **逐币挂宽倍数**：`{"XRP": 0.7, "ARB": 1.5}`（缺失 ⇒ 全 1.0）。
    # 用途：对**唯一为正**的币收窄抢腿量、对负 EV 币加宽降毒性——两者都不需要改
    # 全局 `spread_mult`（那会同时动 5 个币、把腿量一起打掉）。
    # ⚠️ 放在 dataclass **末尾**：`QuoteParams(**{...})` 全是关键字构造，
    #    但放末尾可彻底排除任何位置构造被字段插入错位的风险 ✓。
    per_symbol_spread_mult: Optional[Dict[str, float]] = None
    # ── [h623 2026-09-29] **库存偏斜改绝对基点** ─────────────────────────────
    #
    # 病根：`k_inv` 乘在挂宽上。价差模式下半宽只有 ~0.5bp，满库存偏移缩成
    # ±0.5bp，对 40bp 止损等于没有方向盘（账本：BNB 入场净 −0.80bp）。
    # 语义：>0 时，加仓侧宽度 += 本值×|inv_ratio|，减仓侧宽度 -= 同值
    # （绝对 bp，不乘半价差）。此时**关闭**旧的乘性 `k_inv`，避免双计。
    # 0 = 关闭 = 仍走乘性 k_inv（旧行为逐字一致）。
    inv_skew_abs_bp: float = 0.0


@dataclass(frozen=True)
class Quote:
    symbol: str
    mid: float
    bid: float
    ask: float
    w_bid_bp: float
    w_ask_bp: float
    reason: str = ""
    # [F205 2026-09-15] 报价**分支**与**基准半宽**：观测用。
    # 为什么必须有：模型（回放）与实盘用的都是同一套 `compute_quote` ✓，但
    # `frozen_width_bp` 那个冻结档会让 `w_base_bp` **完全不参与**报价 ——
    # F189 的教训就是"模型以为挂 12bp（w_base=12 生效），实盘因冻结档实际只挂 5.5/4.7bp" ✗✗，
    # 结果拿着一个"改了但没生效"的配置承担了真实敞口（−$63.8）✗。
    # ⇒ 把分支名与基准宽度显式带出来，体检里就能直接核对"参数→行为"✓。
    mode: str = "normal"          # "normal" | "frozen"
    base_bp: float = 0.0          # 该分支产出的基准半宽（未经库存/趋势偏斜与地板钳制）


def compute_quote(
    *,
    symbol: str,
    mid: float,
    sigma_norm: float = 0.0,
    inv_ratio: float = 0.0,
    slow_range_bp: float = 0.0,
    trend_bp: float = 0.0,
    spread_bp: float = 0.0,
    best_bid: float = 0.0,
    best_ask: float = 0.0,
    adverse_bp: float = 0.0,
    params: QuoteParams = QuoteParams(),
) -> Optional[Quote]:
    """计算双边报价。

    Args:
        mid: 中间价（<=0 直接返回 None）
        sigma_norm: 波动归一值（近 20 期振幅 / 长期均值 - 1，>=0 表示比平时波动大）
        inv_ratio: 库存偏离度 ∈ [-1, 1]（正=多头库存，需偏向卖出）
        slow_range_bp: 近 240 期中价全幅（bp）——[F80] 冻结行情检测信号；
            >0 且 < frozen_max_move_bp 时挂宽切到 frozen_width_bp
        spread_bp: [F280] 当前**真实买卖价差**（bp，= (ask-bid)/mid*1e4）。
            `params.spread_mult > 0` 时必须提供且 >0，否则退回绝对 bp 路径。
        best_bid: [F280] 真实最优买价（用于**不穿越**钳制）。非正 ⇒ 跳过钳制。
        best_ask: [F280] 真实最优卖价（同上）。
    """
    if not mid or mid <= 0:
        return None
    # [F80] 冻结行情：全幅小于阈值 ⇒ 波动坍缩，窄挂宽捕获微小振荡
    # （w3 在冻结日唯一为正的实测档位；无冻结信号时行为与旧版逐字一致）
    if (params.frozen_width_bp is not None and float(params.frozen_width_bp) > 0
            and 0 < float(slow_range_bp) < float(params.frozen_max_move_bp)):
        base = float(params.frozen_width_bp)
        _mode = "frozen"
    else:
        # [F97 2026-09-14] σ 上限：`k_vol×σ` 原先**无上界**，实测 σ 可达 4~6
        # （XRP 当前 5 分钟波动 = 基准的 5.4 倍）⇒ 半宽被推到 14bp（双边 28bp），
        # 远超真实边缘。`k_vol_sigma_cap>0` 时先截断 σ 再放大（0=旧行为，逐字一致）。
        _vol = max(0.0, sigma_norm)
        _cap = float(getattr(params, "k_vol_sigma_cap", 0.0) or 0.0)
        if _cap > 0:
            _vol = min(_vol, _cap)
        base = params.w_base_bp * (1.0 + params.k_vol * _vol)
        _mode = "normal"
    # ── [F280] 价差相对挂宽（**覆盖**上面的绝对 bp 基准）─────────────────────────
    # 只有当 `spread_mult > 0` **且** 拿到有效 `spread_bp` 时才生效；
    # 否则逐字走旧路径（spread_mult 默认 0.0 ⇒ 旧行为 100% 不变 ✓）。
    _smult = float(getattr(params, "spread_mult", 0.0) or 0.0)
    # [F286] 减仓侧可独立指定（`spread_mult_reduce<=0` ⇒ 与 `spread_mult` 相同）
    _smult_red = float(getattr(params, "spread_mult_reduce", 0.0) or 0.0)
    if _smult_red <= 0:
        _smult_red = _smult
    # [h432] 波动张开价差：smult_eff = smult × (1 + vol_spread_k × σ_capped)。
    # 文献：HFT 做市盈利集中于高波动期（价差收益 ∝ σ）；k=0 时逐字不变 ✓。
    _vsk = float(getattr(params, "vol_spread_k", 0.0) or 0.0)
    if _vsk > 0:
        _vc = max(0.0, float(sigma_norm))
        _cap2 = float(getattr(params, "k_vol_sigma_cap", 0.0) or 0.0)
        if _cap2 > 0:
            _vc = min(_vc, _cap2)
        _vfac = 1.0 + _vsk * _vc
        _smult *= _vfac
        _smult_red *= _vfac
    # [h527 2026-09-29] **逐币挂宽倍数**（在 `_half_spread` 之前，故同时影响
    # 进场侧与减仓侧、并参与 `min_edge_frac` 地板与"不穿越"钳制）。
    # 缺失 ⇒ 1.0 ⇒ 与改动前逐字一致 ✓（见 `symbol_lookup` 的说明）。
    _psm = symbol_lookup(getattr(params, "per_symbol_spread_mult", None), symbol, 1.0)
    if _psm != 1.0:
        _smult *= _psm
        _smult_red *= _psm
    _half_spread = float(spread_bp) / 2.0 if float(spread_bp or 0.0) > 0 else 0.0
    _spread_mode = False
    if _smult > 0 and _half_spread > 0:
        base = _smult * _half_spread
        _mode = "spread"
        _spread_mode = True
    # 减仓侧的基准宽度（在库存偏斜之前就分开，见下方 w_bid/w_ask）
    _base_reduce = (_smult_red * _half_spread) if _spread_mode else base
    # [h432] 减仓侧偏斜消融：adverse_bp 越大，减仓侧越向 mid 收拢（A-S 范式——
    # 库存靠报价不对称连续消融，而非计时器到点 taker 砸单）。k=0 时逐字不变 ✓。
    _esk = float(getattr(params, "exit_skew_k", 0.0) or 0.0)
    if _esk > 0 and float(adverse_bp or 0.0) > 0:
        _esk_scale = float(getattr(params, "exit_skew_scale_bp", 40.0) or 40.0)
        _u = min(1.0, max(0.0, float(adverse_bp) / _esk_scale))
        _esk_factor = max(0.0, 1.0 - _esk * _u)
        _base_reduce *= _esk_factor
    # [F288] 最小挂单距离下限：抬地板，不整体放宽
    # （H88/H89：edge 太小 ⇒ 难成交 ⇒ 撞超时 ⇒ 兜底市价平，实盘 561 周期实证）
    _mef = float(getattr(params, "min_edge_frac", 0.0) or 0.0)
    if _spread_mode and _mef > 0:
        _floor_bp = _mef * _half_spread
        base = max(base, _floor_bp)
        _base_reduce = max(_base_reduce, _floor_bp)
    # [F204] 趋势反向偏斜（见 QuoteParams.k_trend 的证据说明）。
    # 符号务必记住：**涨 ⇒ bid 挂远、ask 挂近**（= 双边整体下移 = 顺势出货 ✓）；
    # 跌 ⇒ 反过来（= 整体上移 = 低吸 ✓）。写成反的会把边际从 +2bp 变成 −4bp ✗✗。
    _kt = float(getattr(params, "k_trend", 0.0) or 0.0)
    # [F286] 减仓侧的基准宽度：哪一侧在减仓由 `inv_ratio` 决定
    # （inv_ratio>0 = 多头库存 ⇒ 卖侧在减仓；inv_ratio<0 ⇒ 买侧在减仓）。
    # [F286] 减仓侧的基准宽度。
    # ⚠️ 符号：`inv_ratio > 0` = **多头库存** ⇒ 需要**卖出**减仓 ⇒ **卖侧（ask）是减仓侧**。
    #    首版写反了（把 reduce 给了 bid），单测直接抓到 ⇒ 记在这里防回归。
    _red_bid = inv_ratio < 0     # 空头 ⇒ 买侧减仓
    _red_ask = inv_ratio > 0     # 多头 ⇒ 卖侧减仓
    _base_bid = _base_reduce if _red_bid else base
    _base_ask = _base_reduce if _red_ask else base
    if _kt > 0 and trend_bp:
        _scale = float(getattr(params, "trend_skew_scale_bp", 20.0) or 20.0)
        _u = max(-1.0, min(1.0, float(trend_bp) / _scale)) if _scale > 0 else 0.0
        _skew = _kt * _u
        base_bid = _base_bid * max(0.05, 1.0 + _skew)
        base_ask = _base_ask * max(0.05, 1.0 - _skew)
    else:
        base_bid = _base_bid
        base_ask = _base_ask
    # 库存偏斜：多头 → 买更远、卖更近（鼓励减仓）。
    # [h623] `inv_skew_abs_bp>0` ⇒ 用绝对 bp（加仓侧加宽、减仓侧收窄），
    # 不再乘在已经贴盘口的半宽上；同时关掉乘性 k_inv，避免双计。
    _abs_inv = float(getattr(params, "inv_skew_abs_bp", 0.0) or 0.0)
    if _abs_inv > 0 and abs(float(inv_ratio or 0.0)) > 1e-12:
        _d = _abs_inv * float(inv_ratio)
        w_bid = float(base_bid) + _d
        w_ask = float(base_ask) - _d
        w_bid = max(0.0, w_bid)
        w_ask = max(0.0, w_ask)
    else:
        w_bid = base_bid * (1.0 + params.k_inv * inv_ratio)
        w_ask = base_ask * (1.0 - params.k_inv * inv_ratio)
    # 宽度下限分侧：加仓侧守 min_width_bp（贴盘口实测为负），减仓侧可贴到
    # min_width_reduce_bp——否则库存到顶只能等超时用 taker 平仓，成本高一个量级。
    bid_floor = params.min_width_reduce_bp if inv_ratio < 0 else params.min_width_bp
    ask_floor = params.min_width_reduce_bp if inv_ratio > 0 else params.min_width_bp
    # [F280] 价差相对模式下**不用绝对 bp 地板** —— 那正是病根：
    # `min_width_bp=3.0` 在 BTC（半价差 0.0062bp）上是 **484 倍半价差**，
    # 会把刚算好的价差相对宽度直接拔到天外 ⇒ 报价回到"永远打不到"✗。
    # 该模式下的下限由 `spread_cross_margin` 的**不穿越钳制**承担（见下），
    # 那是物理约束（不能越过对侧最优价），比任何拍出来的 bp 数字都可靠 ✓。
    if not _spread_mode:
        w_bid = min(params.max_width_bp, max(bid_floor, w_bid))
        w_ask = min(params.max_width_bp, max(ask_floor, w_ask))
    else:
        # 上限仍用 max_width_bp 兜底（防极端行情把报价甩到天外）
        w_bid = min(params.max_width_bp, max(0.0, w_bid))
        w_ask = min(params.max_width_bp, max(0.0, w_ask))
        # [F288] 最小挂单距离下限必须在**上限之后**再抬一次 ——
        # 否则极限价差（>100bp）下 `max_width_bp=60` 会把下限直接夹掉 ✗
        # （单测 `test_floor_raises_narrow_quote` 抓到了这个顺序错误）。
        # 语义上"下限可以超过上限"是刻意的：宁可贵一点、也不要挂在几乎不可能成交的距离，
        # 因为 H88 证明窄 edge 是尾部亏损的主要来源。
        if _mef > 0:
            w_bid = max(w_bid, _mef * _half_spread)
            w_ask = max(w_ask, _mef * _half_spread)
        # [F288] 让 `base_bp`（报给观测/status 的基准宽度）**与实际报价一致** ——
        # 否则减仓侧明明按 `spread_mult_reduce` 挂，却报 `spread_mult` 的值，
        # 正是 F189「报的值 ≠ 实际挂的值」那一类 ✗
        base = _base_reduce if (_red_bid or _red_ask) else base
    _bid = float(mid) * (1.0 - w_bid / 1e4)
    _ask = float(mid) * (1.0 + w_ask / 1e4)
    # [h729 R2] 期望值引擎挂宽上限:两侧半宽钳到 δ*(覆盖一切上游公式,包括
    # 波动张开/偏斜的放大项)。0=关。放在不穿越钳制**之前**:δ* 本身是"期望值
    # 最优"的绝对目标,物理钳制(不穿越)在其后仍生效(δ*<hs 时无冲突)。
    _evc = float(getattr(params, "ev_width_cap_bp", 0.0) or 0.0)
    if _evc > 0:
        w_bid = min(w_bid, _evc)
        w_ask = min(w_ask, _evc)
        _bid = float(mid) * (1.0 - w_bid / 1e4)
        _ask = float(mid) * (1.0 + w_ask / 1e4)
    # ── [F280] 硬不变量：报价**绝不穿越**对侧最优价 ────────────────────────────
    # 有了真实盘口（bid_px/ask_px）时，把报价**钳进** [best_bid, best_ask] 区间内侧；
    # 没有盘口时退化为"不得越过 mid"这一弱约束（仍保证 bid<mid<ask）。
    # 为什么必须有：H54 实测 δ 用绝对 bp 时，BTC 上报价落在卖一**上方 0.0376bp**、
    # **穿越率 86.89%** —— 那是可成交单却按 maker 计费，虚增收益 ✗✗（第 19 条教训）。
    _bb = float(best_bid or 0.0)
    _ba = float(best_ask or 0.0)
    _m = float(params.spread_cross_margin or 0.05)
    if _ba > _bb > 0:
        # 允许进到 [bb + m*(ba-bb)/2, ba - m*(ba-bb)/2] 之内，绝不越过
        _lo = _bb
        _hi = _bb + (1.0 - _m) * (_ba - _bb) if _ba > _bb else _bb
        _bid = min(max(_bid, _lo), _hi)
        _lo2 = _ba - (1.0 - _m) * (_ba - _bb) if _ba > _bb else _ba
        _ask = max(min(_ask, _ba), _lo2)
        # 极端兜底：保证严格 bid < ask
        if _bid >= _ask:
            _c = 0.5 * (_bb + _ba)
            _bid, _ask = min(_bid, _c), max(_ask, _c)
    else:
        _bid = min(_bid, float(mid))
        _ask = max(_ask, float(mid))
    # 回写实际生效的宽度（钳制后），否则 status 报的宽度是**改前**的值
    # —— F189 的教训正是"模型以为挂 12bp、实盘实际只挂 5.5bp" ✗
    w_bid = max(0.0, (float(mid) - _bid) / float(mid) * 1e4)
    w_ask = max(0.0, (_ask - float(mid)) / float(mid) * 1e4)
    return Quote(
        symbol=symbol, mid=float(mid),
        bid=float(_bid),
        ask=float(_ask),
        w_bid_bp=round(w_bid, 6), w_ask_bp=round(w_ask, 6),
        reason=(f"base={base:.4f}bp sigma={sigma_norm:.2f} inv={inv_ratio:.2f} "
                f"mode={_mode}" + (f" spread={float(spread_bp):.4f}bp" if _spread_mode else "")),
        mode=_mode, base_bp=round(float(base), 4),
    )


def seg_slice(labels, watermark_ms: int, snap_ms: int, *, created_ms=None,
              tick_wall_ms=None) -> Tuple[int, int]:
    """[F107 2026-09-14] 成交桶分片选择（**实盘与回放唯一口径**，纯函数）。

    返回 `(i0, i1)`：本次判定可用的成交桶 = `labels[i0:i1]`。

    为什么必须只用一个实现：这套口径先后错过两次，都造成实盘/模型系统性分叉——
      - v1 `[ots[i], ots[i+1]]`：**前瞻**（拿挂单被刷新之后的成交判定它）；
      - v2 `[ots[i-1], ots[i]]` **两端闭合**：每个桶同时落在相邻两轮分片里
        ⇒ 同一批成交获得两次撞单机会（实测多算 ~1.5×）；
      - v3（本实现，与实盘 tick 同构）：**下界半开、上界闭合**。

    规则：
      1. `labels > watermark_ms`（半开）——每个已落库的桶**恰好判定一次**；水位只在
         **真读到桶**时前进 ⇒ 空分片不丢成交量（下一轮自动补收被跳过的标签）；
      2. `labels <= snap_ms`（闭合）——只消费到本次决策所依据的快照那一桶，绝不把
         "晚于该快照"的成交算进本次判定；
      3. 可选**可见性**：`created_ms[k] <= tick_wall_ms` 的桶才算"当时已落库"。成交桶
         是按**落库时刻**分桶的（`floor(flush/15s)`）且空桶不落行 ⇒ 一个桶在它自己的
         标签时刻可能还不存在；实盘 tick 只能看到已落库的桶（实测落库滞后 1~13s、
         网格填充率仅 47.5%）。不过滤就会用上"实盘当时还看不到"的成交 ⇒ 偏乐观。
    """
    import bisect

    _wm = int(watermark_ms or 0)
    i0 = bisect.bisect_right(labels, _wm)
    i1 = bisect.bisect_right(labels, int(snap_ms or 0))
    if created_ms is not None and tick_wall_ms is not None:
        _tw = int(tick_wall_ms)
        while i1 > i0 and int(created_ms[i1 - 1]) > _tw:
            i1 -= 1
    return i0, i1


def sigma_norm_from_ranges(recent_ranges: List[float], baseline_range: float) -> float:
    """波动归一：近 N 期平均振幅 / 基准振幅 − 1，下限 0。"""
    if not recent_ranges or baseline_range <= 0:
        return 0.0
    avg = sum(abs(float(x)) for x in recent_ranges) / len(recent_ranges)
    return max(0.0, avg / baseline_range - 1.0)


# ═══════════════════════ 库存 ═══════════════════════

@dataclass
class Position:
    qty: float = 0.0            # 正=多，负=空
    avg_px: float = 0.0
    avg_mid: float = 0.0        # 开仓时的中间价（用于「中间价对中间价」的价格盈亏）
    opened_ts: float = 0.0
    last_ts: float = 0.0
    # [F279 2026-09-16] 库存周期标识（position episode）。
    # 为什么需要：此前 `lane_ledger.position_id` **从不写入**（实测 5255/5255 全 NULL），
    # 后果是开仓腿与平仓腿无法配对，「往返级归因」做不了 —— 而这正是轮 7/轮 8
    # 反复卡住的地方（入场 +6.4bp / 平仓 −9.2bp 只能按"腿"看，看不到一次往返）。
    # 语义：一次「库存周期」= 从空仓建仓开始，到回到空仓（或反手）为止；
    # 同周期内所有成交共享同一个 id，因此可以精确配对。
    position_id: str = ""


@dataclass
class InventoryBook:
    """按 symbol 维护做市库存与已实现盈亏。

    所有金额单位为「计价货币」（USD），qty 为标的数量。
    """

    positions: Dict[str, Position] = field(default_factory=dict)
    realized_spread_usd: float = 0.0
    realized_price_usd: float = 0.0
    realized_fee_usd: float = 0.0
    fills: int = 0
    # [F279] 周期序号（按 book 实例单调递增），用于生成可读且唯一的 position_id。
    episode_seq: int = 0

    # ── 库存周期 ──
    def next_position_id(self, symbol: str, now_ts: float = 0.0) -> str:
        """生成一个新的库存周期 id。

        格式 `mm:{symbol}:{seq}`；真实时钟下再加开仓秒 `mm:{symbol}:{seq}:{epoch}`。

        为什么要加秒：进程重启后 `episode_seq` 从 0 再计，旧账里的 `mm:BTC:1`
        会被下一笔开仓复用。近 14 天 188 个 id 里有 115 个「平完又开」用了同一个
        字符串，按 id 归组的策略分析会把两笔交易粘成一笔。
        同一秒内序号仍递增，所以同一进程里也不会撞。
        `now_ts` 不是真实时钟（测试、回放的小数字）时保持旧格式，避免改测试夹具。

        长度受控（lane_ledger.position_id 为 VARCHAR(64)）。
        """
        self.episode_seq += 1
        base = f"mm:{symbol}:{self.episode_seq}"
        ts = int(float(now_ts or 0.0))
        if ts > 1_000_000_000:
            return f"{base}:{ts}"
        return base

    def ensure_position_id(self, symbol: str) -> str:
        """取当前周期的 id；若为空（如外部注入的 Position）则补发一个。"""
        pos = self.positions.setdefault(symbol, Position())
        if not pos.position_id:
            pos.position_id = self.next_position_id(symbol)
        return pos.position_id

    # ── 查询 ──
    def qty(self, symbol: str) -> float:
        p = self.positions.get(symbol)
        return p.qty if p else 0.0

    def notional(self, symbol: str, mark_px: float) -> float:
        return self.qty(symbol) * float(mark_px or 0.0)

    def net_notional(self, marks: Dict[str, float]) -> float:
        return sum(self.qty(s) * float(marks.get(s) or 0.0) for s in self.positions)

    def inv_ratio(self, symbol: str, mark_px: float, limit_notional: float) -> float:
        """库存偏离度 ∈ [-1,1]（用于报价偏斜）。"""
        if limit_notional <= 0:
            return 0.0
        r = self.notional(symbol, mark_px) / limit_notional
        return max(-1.0, min(1.0, r))

    def holding_seconds(self, symbol: str, now_ts: float) -> float:
        p = self.positions.get(symbol)
        if not p or abs(p.qty) < 1e-12 or p.opened_ts <= 0:
            return 0.0
        return max(0.0, float(now_ts) - p.opened_ts)

    # ── 成交入账 ──
    def apply_fill(
        self,
        *,
        symbol: str,
        side: str,
        qty: float,
        fill_px: float,
        mid_px: float,
        fee_rate: float = 0.0,
        now_ts: float = 0.0,
    ) -> Dict[str, float]:
        """按成交更新库存，并分解已实现收益（价差 / 价格 / 费用）。

        **口径（重要）**：
          - `spread_usd`：本次成交价相对**当期中价**的优势（买低于中价为正）；
          - `price_usd`：平仓部分的**中价对中价**变动 × 方向 × 数量（库存风险盈亏）；
          - `fee_usd`：本次成交费用。

        价格盈亏必须用「中价 → 中价」而不是「成交价 → 成交均价」，否则开仓时
        已计入 spread 的那部分价差会在平仓时被再计一次（实测会虚增 +1.2bp/笔）。

        [F279 2026-09-16] 同时维护 `position_id`（库存周期）。返回值多一个
        `position_id` 键，供调用方写入 `lane_ledger`。
        """
        side_l = str(side or "").lower()
        signed = abs(float(qty)) if side_l in ("buy", "long", "b") else -abs(float(qty))
        fill_px = float(fill_px)
        mid_px = float(mid_px or fill_px)
        notional = abs(signed * fill_px)
        fee_usd = -abs(fee_rate) * notional
        pos = self.positions.setdefault(symbol, Position())

        # 价差捕获：成交价相对**中价**的价格优势 × 数量（精确，不用 bp×名义近似）
        edge_bp = ((mid_px - fill_px) if signed > 0 else (fill_px - mid_px)) / mid_px * 1e4 if mid_px > 0 else 0.0
        spread_usd = ((mid_px - fill_px) if signed > 0 else (fill_px - mid_px)) * abs(signed)

        realized_price_usd = 0.0
        if abs(pos.qty) < 1e-12:
            # 空仓 → 建仓：开一个新的库存周期
            pos.qty, pos.avg_px, pos.avg_mid, pos.opened_ts = signed, fill_px, mid_px, now_ts
            pos.position_id = self.next_position_id(symbol, now_ts)
        elif pos.qty * signed > 0:
            # 同向加仓 → 更新均价与平均中价；**同一周期**，id 不变
            total = abs(pos.qty) + abs(signed)
            pos.avg_px = (pos.avg_px * abs(pos.qty) + fill_px * abs(signed)) / total
            pos.avg_mid = (pos.avg_mid * abs(pos.qty) + mid_px * abs(signed)) / total
            pos.qty += signed
            if not pos.position_id:
                pos.position_id = self.next_position_id(symbol, now_ts)
        else:
            # 反向 → 平掉 min(|pos|, |signed|)；平仓腿**继承本周期 id**（配对的关键）
            close_qty = min(abs(pos.qty), abs(signed))
            direction = 1.0 if pos.qty > 0 else -1.0
            base_mid = pos.avg_mid or pos.avg_px or mid_px
            realized_price_usd = direction * (mid_px - base_mid) * close_qty
            pos.qty += signed
            if abs(pos.qty) < 1e-12:
                # 回到空仓：本周期结束。id 暂时保留在 pos 上，供本笔平仓腿记账；
                # 下一次建仓（abs(pos.qty)<1e-12 分支）会覆盖为新 id。
                pos.qty, pos.avg_px, pos.avg_mid, pos.opened_ts = 0.0, 0.0, 0.0, 0.0
            elif pos.qty * direction < 0:
                # 反手：剩余部分开**新周期**
                pos.avg_px, pos.avg_mid, pos.opened_ts = fill_px, mid_px, now_ts
                pos.position_id = self.next_position_id(symbol, now_ts)

        pos.last_ts = now_ts
        self.realized_spread_usd += spread_usd
        self.realized_price_usd += realized_price_usd
        self.realized_fee_usd += fee_usd
        self.fills += 1
        return {
            "notional": notional, "edge_bp": round(edge_bp, 4),
            "spread_usd": round(spread_usd, 6),
            "price_usd": round(realized_price_usd, 6),
            "fee_usd": round(fee_usd, 6),
            "net_usd": round(spread_usd + realized_price_usd + fee_usd, 6),
            "position_id": pos.position_id or self.ensure_position_id(symbol),
        }

    # ── 汇总 ──
    def realized_total_usd(self) -> float:
        return self.realized_spread_usd + self.realized_price_usd + self.realized_fee_usd


# ═══════════════════════ 成交判定（与 F52 同口径） ═══════════════════════

def queue_fill_side(
    *,
    side: str,
    quote_px: float,
    cum_opposite_volume: float,
    queue_ahead_qty: float,
    our_qty: float = 0.0,
    max_distance_bp: float = 0.0,
    best_px: float = 0.0,
) -> bool:
    """[F255 2026-09-20] **队列消耗制**成交判定（论文口径）。

    ## 为什么需要它（我们现在的规则在两个方向上都错）

    现行 `fill_side()` 的判据是 `seg_low < bid`（价格**穿过**挂单价），
    且 `penetration_bp=0` 的 docstring 自认"**等价于假设我们排在队列最前面**"。

    论文 Albers et al. (arXiv:2502.18625v2) 第 612–618 行给出真实机制：

        挂在某档位的单子成交，条件是
            「自挂单以来**累计的对手方主动成交量**」 > 「该档位我们**前面的挂量** LA」
        —— **价格不需要穿过我们的档位。**

    这个区别是实质性的：
      · 我们**漏掉**了"价格停在我们档位、但队列被一笔大单吃穿"的成交
        （这是最常见的 maker 成交形态）；
      · 我们**多算**了本该排在队尾的那些（把 LA 当 0）。

    H19 实测（tick 级真实数据，12h，15s 报价节奏，4 个有效样本币）量化了这个偏差：

        队尾(LA=全部挂量) 成交率 24–37%   mk@1s −0.22 ~ −0.62bp
        队首(LA=0)        成交率 85–95%   mk@1s −0.12 ~ +0.27bp
        ⇒ 队首 − 队尾 = **+0.42bp @1s**

    而我们实测净边际 = **−0.60bp** ⇒ 成交口径的偏差与整个策略盈亏**同量级**。
    DeLise (arXiv:2407.16527) 进一步证明 `P(成交│中价逆向移动) = 1`，
    即**成交必然伴随逆向选择** ⇒ 在"价格穿过 + 零队列"上成交的模型**系统性偏乐观**。

    ## 参数语义

        side                   "buy" / "sell"
        quote_px               我们的挂单价
        cum_opposite_volume    自挂单以来，**在 quote_px 这个价位**成交的对手方主动量
                               （买单看主动卖量；卖单看主动买量）
        queue_ahead_qty        挂单时刻该价位**我们前面**的挂量（LA）
        our_qty                我们自己的挂量（可选；用于判断"完全成交"）
        max_distance_bp        >0 时要求成交价与 quote_px 的距离在此范围内才算数
                               （防止把"价格早已远离、后来才路过"的成交算进来）
        best_px                当前最优价（配合 max_distance_bp 使用；0=不校验）

    返回 True 表示**成交**（累计对手量已吃穿我们前面的队列）。

    ## 与 `fill_side` 的关系

    `fill_side` 保留（回放/历史口径，且是线上现役路径）。
    本函数是**新增的更真实口径**，由 `MM_QUEUE_FILL_MODEL` 控制是否启用。
    两者**不可混用**：混用会让同一笔成交被判定两次或一次都不判。
    """
    if quote_px <= 0:
        return False
    la = max(0.0, float(queue_ahead_qty or 0.0))
    cum = max(0.0, float(cum_opposite_volume or 0.0))
    # 队列消耗：前排吃完的部分算我们的成交
    if cum <= la:
        return False
    if our_qty and our_qty > 0 and (cum - la) < our_qty:
        # 前排吃完了，但还没把我们自己的量吃完 ⇒ 部分成交。
        # 这里按"有成交"返回 True（调用方按库存处理），真实下单需拆成部分成交。
        pass
    # 距离校验：成交必须在我们的价位附近发生（0 = 不校验）
    md = float(max_distance_bp or 0.0)
    if md > 0 and best_px and best_px > 0:
        if abs(float(best_px) - quote_px) / quote_px * 1e4 > md:
            return False
    return True


def queue_position_ratio(queue_ahead_qty: float, queue_behind_qty: float) -> float:
    """[F255] 队列位置 `QP = LA / (LA + LB)` ∈ [0,1]。

    0 = 队首，1 = 队尾。H19 / 论文 Table 1 显示这是 markout 的第二个轴
    （同格内队首 vs 队尾差 0.12–0.86bp）。**必须记账**，否则永远无法回答
    "我们赚的是队首的钱还是队尾的钱"。

    `LA + LB == 0`（空队列，只有我们）时返回 0.0 —— 语义上我们就是队首。
    """
    la = max(0.0, float(queue_ahead_qty or 0.0))
    lb = max(0.0, float(queue_behind_qty or 0.0))
    tot = la + lb
    return (la / tot) if tot > 0 else 0.0


def tick_trade_hi_ms(wall_ms: int, snap_ms: int, lag_ms: int = 3000) -> int:
    """逐笔成交窗口的上界。锚在墙钟减去落库余量，不锚 15 秒盘口快照。

    15 秒快照比墙钟慢约 20 秒。若把上界拉回快照，而下界是刚刚挂出的报价时刻，
    窗口下界大于上界，逐笔一条都读不进来。只有快照时间戳跑到墙钟前面
    （时钟跳变）时才改用快照，避免去读还没发生的成交。
    """
    hi = int(wall_ms) - int(lag_ms)
    if int(snap_ms) > int(wall_ms):
        return int(snap_ms)
    return hi


def quote_visible_at(hist: List[dict], hi_ms: int) -> Optional[dict]:
    """取「挂出时刻 ≤ hi_ms」的最近一张报价（含撤单后的零单标记）。

    实时报价每秒重挂，但逐笔落库要晚几秒。成交只能记在当时真的挂着的那张单上，
    不能记在此刻刚算出来、成交还没入库的新价上。没有够老的记录就返回 None。
    """
    best = None
    best_ts = -1
    for q in hist or []:
        try:
            ts_ms = int(float(q.get("ts") or 0.0) * 1000.0)
        except (TypeError, ValueError):
            continue
        if ts_ms <= 0 or ts_ms > int(hi_ms):
            continue
        if ts_ms >= best_ts:
            best = q
            best_ts = ts_ms
    return best


def aggregate_trades(
    ts: List[float],
    px: List[float],
    qty: List[float],
    is_buyer_maker: List[bool],
    *,
    quote_bid: float = 0.0,
    quote_ask: float = 0.0,
) -> Dict[str, float]:
    """[h621 2026-09-29] **tick 级成交聚合**（纯函数，无 IO；runner/replay 同一实现）。

    ## 为什么需要（用户 2026-09-29 指正：「有单独的实时盘口，不能用滞后的 15s」）

    现行成交判定读 `market_trades_aggregated`（15s 桶，**按落库时刻分桶**）：
      · 桶比 tick 晚 15~30s 可见（F171/F176 不得不引入"延迟一档判定"来补偿）；
      · 网格填充率历史仅 47.5%（空桶不落行），F107 还要做落库可见性过滤；
      · 桶内只有 low/high/总量 ⇒ 判定退化为 `seg_low < bid`（队首假设）。
    而逐笔表 `asterdex_trades`（p50 1.5s 落库）一直在采集，`tick_feed.py`
    早就为它建好了访问层，却**从未接进判定路径**（runner 零引用）。

    本函数从逐笔数组直接产出判定所需的全部量：
      seg_low/seg_high —— 区间真实最低/最高**成交价**（不是桶聚合，无对齐歧义）；
      taker_sell/taker_buy —— 主动卖/买量（is_buyer_maker=True = 主动卖，打 bid）；
      vol_le_bid / vol_ge_ask —— **打到我们价位**的对手方主动量：
          vol_le_bid = Σ 主动卖量(price ≤ quote_bid)   （价格穿越/触及我方买单）
          vol_ge_ask = Σ 主动买量(price ≥ quote_ask)
        这是队列消耗口径（Albers et al. arXiv:2502.18625 的 LA 机制）在
        "我方报价改善最优价"场景下的等价形式：我方在**新档位的队首**，
        前方只有更优价的挂量，而逐笔里 price ≤ 我价的主动卖必然已吃掉
        那些更优价 ⇒ Σ(≤我价的主动卖量) 就是"能轮到我们的量"。

    实测校准（2026-09-29，300 条 maker 入场腿，桶对齐后用逐笔复核）：
      · 9.9% 的纸面成交在逐笔里**找不到任何打到价位的对手方主动成交**（幻影）；
      · 名义口径真实可吃量只覆盖纸面记入的 **82%**，p90 腿量/真实量 = 2.32。
    ⇒ 用 vol_le_bid/ge_ask 替代"桶总量×QUEUE_SHARE"同时修**发生率**与**数量**两层偏差。

    `quote_bid/quote_ask <= 0`（无挂单）⇒ 对应 vol 记 0。空数组 ⇒ 全 0（n=0）。
    """
    n = len(ts)
    out = {"seg_low": 0.0, "seg_high": 0.0, "taker_sell": 0.0, "taker_buy": 0.0,
           "vol_le_bid": 0.0, "vol_ge_ask": 0.0, "n": n}
    if not n:
        return out
    lo = float("inf")
    hi = 0.0
    sv = bv = vlb = vga = 0.0
    qb = float(quote_bid or 0.0)
    qa = float(quote_ask or 0.0)
    for k in range(n):
        p = float(px[k])
        q = float(qty[k])
        if p <= 0 or q <= 0:
            continue
        if p < lo:
            lo = p
        if p > hi:
            hi = p
        if bool(is_buyer_maker[k]):          # 主动卖（打 bid）
            sv += q
            if qb > 0 and p <= qb:
                vlb += q
        else:                                 # 主动买（打 ask）
            bv += q
            if qa > 0 and p >= qa:
                vga += q
    if lo == float("inf"):
        return out
    out.update(seg_low=lo, seg_high=hi, taker_sell=sv, taker_buy=bv,
               vol_le_bid=vlb, vol_ge_ask=vga, n=n)
    return out


def tick_fill_legs(
    agg: Dict[str, float],
    *,
    quote_bid: float,
    quote_ask: float,
    allow_buy: bool,
    allow_sell: bool,
    queue_share: float,
) -> Dict[str, float]:
    """[h621] 从 `aggregate_trades` 的结果直接产出可成交腿（exact-hit 口径）。

    返回 `{"buy": 可吃量, "sell": 可吃量}`（均已乘 `queue_share`；不可成交的侧不在
    dict 里）。判定条件只有一个：**vol_at_price > 0** —— 真实逐笔里确有对手方
    主动成交打到我们的价位。这与"价格穿越"（seg_low < bid）在 tick 源下数学等价
    （seg_low 就是逐笔最低成交价），但把**数量**也同时钉死在"价位上的真实量"，
    不再用"整桶总量"外推。

    回放（`replay.py`）在 `MM_SEG_SOURCE=tick` 下用本函数替代 `fill_side`；
    runner 的判定内嵌在 `plan_tick`（要与陈旧挂单/延迟一档逻辑耦合），口径与此一致。
    """
    legs: Dict[str, float] = {}
    vlb = float(agg.get("vol_le_bid") or 0.0)
    vga = float(agg.get("vol_ge_ask") or 0.0)
    if allow_buy and quote_bid > 0 and vlb > 0:
        legs["buy"] = vlb * float(queue_share)
    if allow_sell and quote_ask > 0 and vga > 0:
        legs["sell"] = vga * float(queue_share)
    return legs


def fill_side(
    *,
    bid: float,
    ask: float,
    seg_low: float,
    seg_high: float,
    seg_taker_sell: float,
    seg_taker_buy: float,
    penetration_bp: float = 0.0,
) -> Optional[str]:
    """用区间成交明细判定挂单是否成交。

    与 F52 离线模拟**完全同口径**：
      - 买单价被卖方打穿（seg_low < bid 且区间有 taker 卖出）→ buy 成交；
      - 卖单价被买方打穿（seg_high > ask 且区间有 taker 买入）→ sell 成交；
      - 同区间两侧都成交时返回 "both"（调用方按库存偏斜决定处理顺序）。

    `penetration_bp` 是**队列保守假设**：价格为 0 时只要求「触及/穿过挂单价」，
    这等价于假设我们排在队列最前面；设 >0 表示价格必须再穿过该宽度才算成交，
    用于估计「排队在别人后面」时的成交衰减（F59 敏感性检验）。

    ⚠️ [F255 2026-09-20] 本函数要求价格**穿过**挂单价，且默认假设零队列前量。
    论文口径的成交不需要穿过（只需队列被吃穿），见 `queue_fill_side()`。
    两者是**不同的成交模型**，不要混用。
    """
    pen = max(0.0, float(penetration_bp or 0.0)) / 1e4
    hit_buy = seg_taker_sell > 0 and seg_low < bid * (1.0 - pen)
    hit_sell = seg_taker_buy > 0 and seg_high > ask * (1.0 + pen)
    if hit_buy and hit_sell:
        return "both"
    if hit_buy:
        return "buy"
    if hit_sell:
        return "sell"
    return None


@dataclass(frozen=True)
class LaneRiskLimits:
    """车道风控阈值（设计文档 §4.3 + 2026-09-09 参数扫描默认）。

    [第十一轮] 默认 stop_loss_bp 25→**0**：扫描显示关闭超时止损（300s 持有 + 更
    强库存偏斜）把净边际从 -0.23bp 翻到 +0.64bp（w=5/k=1.0）。超时平仓是
    taker 腿 + 吃价差，是此前 -5.75bp/笔 的主要来源。
    """

    # [F121 2026-09-14] **未接线（保留兼容，切勿据它判断风险）**：
    # 全仓仅此一处（定义），无任何读取点；行为扫描实测 1.0/1.5/2.0/3.0 四档在 24h
    # 回放上输出完全相同（成交/h、净额、回撤、峰值敞口一字不差）。
    # 真正生效的**单币**敞口上限是 `max_net_directional_ratio`（1.0 = 每币 1 腿 = $300），
    # 组合口径是 `max_net_exposure_ratio`（3.0 = 最坏情形净敞口 ≤ $900）。
    max_symbol_notional_ratio: float = 0.05     # 未接线，见上（历史字段）
    max_net_exposure_ratio: float = 0.30        # 总净敞口 ≤ 权益 30%
    # [h800 2026-10-04 用户"腿速不够 + 还是做市对冲思想"] 主动流交易模式:
    # 1 = 流脉冲市价进场、30s-5min 离场、不挂双边不对冲(超短交易原始设计);
    # 0 = 旧做市循环。进场/离场阈值见 runner 的 active_flow 分支。
    active_flow_mode: float = 0.0
    # [h801] 进场流阈值(|OFI_60s|):实测 0.3 时 78% 的 tick 判"无流" ⇒ 腿速过低。
    active_flow_thresh: float = 0.15
    # [h775 2026-10-03 用户指令"加这一刀"] 老仓**减仓侧贴盘口**(秒;0=关闭,逐字旧行为):
    # 持仓年龄超过它 ⇒ 减仓侧报价贴到盘口最优价(仍 maker、不穿价),用更快被动成交
    # 换掉 900s 硬顶 taker 平仓的过路费(实测每腿 −12.2bp 吃价差 + −4.0bp 手续费)。
    reduce_touch_after_sec: float = 0.0
    # [h766b 2026-10-03 用户指令] 毒化熔断在**测试阶段只提示**:
    #   "alert" = 连续 2 轮 toxic 只推事件/写日志(默认,不干预交易);
    #   "pause" = 连续 2 轮 toxic ⇒ 停一个 daily_loss_cooldown_sec 冷却周期。
    # 等实盘验证过 KPI 口径的可靠性与漏报/误报率后再切 "pause"。
    kpi_toxic_action: str = "alert"    # [F94b 2026-09-14] 总敞口上限（Σ|仓位| / 权益）。0 = 关闭（旧行为逐字一致）。
    # **唯一能严格兜住真实风险的口径**：平仓会让对冲腿消失从而放大净敞口
    # （实测 3 空 1 多净 −$900 → 平掉多单后净 −$1286），却只会减小总敞口
    # ⇒ 下单侧约束对净敞口只能「尽力而为」，对总敞口是硬约束。
    max_gross_notional_ratio: float = 0.0
    max_net_directional_ratio: float = 0.10     # 单边方向敞口 ≤ 权益 10%
    # ── [F338 2026-09-22] 单腿名义硬上限（相对目标腿量的倍数）─────────────────
    #
    # 缺陷现场（H215/H216，14 天 23,603 腿实测）：
    #   名义分位  P50 $163　P99 $861　P99.9 $2,000　**max $11,661 = 71.5× 中位**
    #   集中度    **前 10 腿 = 全部亏损的 41%**；前 50 腿 = 62%；前 100 腿 = 71%
    #   逐日前 1% 占当日净额 29%~161%，12 天里 11 天为负
    # 机制：`plan_tick` 里 `qty = min(_target_qty, _avail * _QUEUE_SHARE)`，
    # `_avail` = 该 15s 桶的**主动成交量** ⇒ **行情越剧烈、桶越大，我们的腿越大**，
    # 而剧烈行情正是 `price_bp` 最差的地方 ⇒ 一个**逆选择放大器**。
    # 实测被截掉部分的 bp 均值随阈值单调恶化：
    #   1×中位 −1.31bp／2× −3.21bp／3× −4.39bp／5× −7.64bp／8× −10.74bp
    # 反事实（同一批腿按 `min(1, cap/notional)` 缩放）：
    #   上限     被截名义   净额         vs 原状
    #   1.0×中位  41.7%   −$128.63   **+$264.47**
    #   3.0×中位   7.3%   −$237.96   **+$155.14**
    #   无上限      0%    −$393.10        —
    # ⇒ 3× 只动 **7.3% 的名义**却回收 $155（09-15 单日 −$93.29 → **+$13.66**）。
    #
    # 语义：**只约束加仓腿**（`qty = min(target, avail×share, mult × fill_notional/mid)`）；
    # **减仓腿不受限**（`_reducing` 走 `min(|现仓|, avail×share)`）——否则会留下
    # 永远平不掉的残仓，把亏损从价差搬到 taker 强平上（本会话已见过的失败模式）。
    # 0 或负 = **关闭**（与旧行为逐字一致，可一键回退）。
    # ⚠️ 这只是**尾部大小**的约束，不改变每腿的 bp：它不制造正收益，
    #    只把「剧烈行情里自动放大仓位」这条放大器拆掉。
    max_leg_notional_mult: float = 0.0
    # ── [F342 2026-09-22] 突发事件闸（暴涨/暴跌）───────────────────────────
    #
    # 用户要求建立「突发事件机制」。检测用 `sudden_move_bp`（**快档**，
    # 与 `trend_move_bp` 的 20 期 6.7 分钟窗口相对），判据是**绝对幅度**。
    #
    # `sudden_move_bp=0` ⇒ **关闭**（与旧行为逐字一致，可一键回退）。
    #
    # 命中后的动作**刻意只做两件不依赖方向的事**（理由见 `sudden_move_hit`
    # 的 docstring：实测事件后 fwd 从 −47 到 +43bp 全谱，方向不可预测）：
    #   ① `dec.skip = "sudden_move"` + `action = "pause"` ⇒ **本 tick 不新挂单**
    #   ② 但**不清空已有挂单的成交判定** ⇒ 已有的减仓侧挂单仍可被动成交
    #      （出库靠 maker，不靠 taker）
    # 实测建议值：ASTER 的 P99.9 逐秒移动是 10.87bp，而 10s 窗口的闪动阈值
    # 50~60bp 在 24h 只有 10 次 ⇒ **40~60 是合理量级**（不要设成 5，那会天天触发）。
    sudden_move_bp: float = 0.0
    # 快档窗口（期数）。1 = 最近一期 vs 上一期；2~3 = 略微平滑。
    sudden_move_k: int = 1
    # 命中后的**冷却期**（秒）：期间持续"停加仓但允许被动成交"。
    # 为什么需要：单 tick 的暂停太短，而急动的余波（点差变宽、毒性流）持续更久。
    # 0 = 无冷却（只暂停命中那一 tick）。
    sudden_move_cooldown_sec: float = 0.0
    max_one_side_seconds: float = float(os.getenv("MM_MAX_ONE_SIDE_SEC", "300"))  # 单边持仓上限
    # ── [F258 2026-09-20] 交易时长窗口下限（见 `hold_window_ok`）─────────────
    #
    # 用户给定：「交易时间锁定在 **30秒到5分钟** 之内，这个是有验证过的」。
    #
    # 上界由 `max_one_side_seconds` 承担（默认 300s = 5min，与用户窗口一致）。
    # 本字段是**下限**：持仓未满该秒数时，**禁止引擎主动发起**的四条强制出口
    # （止损 / 超时 / OFI 择时 / 反手）。
    #
    # 实测依据（H26，8h，66 个往返）：
    #     持仓 中位 70s、p25 45s、p75 167s、max 3612s
    #     出场腿 −$0.032/往返  =  入场腿盈利（+$0.011/往返）的 3 倍
    # 出场成本几乎与持有时长无关（穿越点差 + 对手方逆向选择）⇒
    # 刚建仓就平掉是**白付一次出场成本**，且没给对手腿任何成交机会。
    #
    # **不影响被动成交**：对手方主动打过来任何时候都允许（那是自然出库）。
    #   · 0 或负 ⇒ 关闭下限（与旧行为逐字一致，可一键回退）
    min_hold_seconds: float = 30.0
    vol_pause_sigma: float = 1.5                # 波动 > 1.5× → 暂停该币
    toxic_streak: int = 3                       # 连续 3 次逆选择 > 阈值 → 暂停
    toxic_bp: float = 15.0
    daily_loss_stop_pct: float = 1.0            # 日亏 > 权益 1% → 全停
    # ── [F71] 针对「超时平仓吃掉全部利润」的两道闸门 ──
    # 实测：挂单成交 +1.77bp/笔（与模型一致），但超时平仓 −26.7bp/笔、占 28.8%，
    # 净期望因此转负。这两项直接削掉尾部亏损。
    stop_loss_bp: float = float(os.getenv("MM_STOP_LOSS_BP", "0"))  # 浮亏 > 此值 → 立即平仓（0=关闭）
    # [h442 2026-09-28] 止损参考价 = "最差入场"（最近加仓腿中价）：
    # >0 时多头用 max(avg_mid, last_entry_mid)、空头用 min(...) ⇒ 更快触发，
    # 减少多腿摊低后 avg 拖后造成的深亏与"盈利仓被止损"偏差。0 = 旧行为逐字一致 ✓。
    stop_ref_last_leg: float = 0.0
    # [h454 2026-09-28] 自适应趋势门槛：门槛 = max(trend_only_bp, 近 1h |r300| 的
    # q 分位)。h450/h451 实测 |r300| 越大边际越强（≥30bp 段 +2.52bp t=14.7，OOS 双窗
    # 同号），但固定绝对阈值在安静时段会把频率打到 0（h452 实测近 10min 0 腿）⇒ 改分位，
    # 通过率 ≈ 1−q 与市场活跃度无关。0 = 关闭（旧行为逐字一致 ✓）。
    trend_only_q: float = 0.0
    # [F231 2026-09-15] 波动条件止损：>0 时只有 σ_norm ≥ 该值才启用 `stop_loss_bp`
    # （0 = 恒启用 = 旧行为逐字一致 ✓）。依据 F230 四场景扫描（同窗口回放）：
    #   10bp 止损在阴跌（-14.5→-5.4bp）/高波动（-10.0→-4.3bp）胜出 ✓，
    #   但在正常日（-0.9→-2.6bp）反而多亏 ✗（震荡里被反复打止损、白付 taker 腿）⇒
    #   止损必须只在**高波动 regime** 生效，不能是常数开关 ✗。
    #   σ_norm 口径与 vol_pause_sigma 一致（当前已实现波动 / 基准 − 1）✓。
    stop_loss_vol_min: float = 0.0
    # [F264 2026-09-16] **快武装止损**：>0 时，本步（上一个快照→当前）中价移动
    # ≥ mult × vol_baseline_bp 且浮亏 ≥ stop_loss_bp ⇒ 跳过慢速 σ_norm 波动闸直接
    # 武装止损。动机：F250#4 实测 σ_norm 用 20 样本（≈10 分钟）窗口，快跌中武装
    # 太晚（XRP 09:31–09:35 瀑布 −75bp，止损在 −75bp 才触发 vs 触发线 10bp ✗）。
    # 0 = 关闭（旧行为逐字一致）。
    stop_loss_fast_mult: float = 0.0
    # [F235 2026-09-15] 止损"先 maker 后 taker"宽限（秒）：止损条件触发后，先让
    # 减仓侧 maker 单继续挂 N 秒（正常报价流程每 tick 都在挂减仓侧），超时才 taker
    # 平仓。0 = 触发即 taker（旧行为逐字一致 ✓）。依据 F234 结构事实：平仓腿
    # -17~-30bp 是唯一亏损源，而其中一部分本可在均值回归段以 maker 价差出场 ✓。
    stop_maker_grace_sec: float = 0.0
    # [h700 2026-10-02] 止损硬距离 fail-fast:maker 宽限期内浮亏越过此值(bp)
    # ⇒ 跳过宽限立即 taker(0=关闭)。依据:23:47 WLD 崩盘实测 −127bp(阈值 40bp),
    # maker 地板单追价下移只在反弹成交 ⇒ 止损距离无上限(15:26 ENA −310bp 同族)。
    stop_taker_bp: float = 0.0
    # [h668 2026-10-01] 变盘先减仓:300s 趋势反向越过该阈值(bp)⇒ 持仓纳入与
    # 止损同一套"maker 宽限→taker"减仓流程(不等 40bp 止损/600s 超时)。
    # 0 = 关(旧行为)。
    trend_flip_flatten_bp: float = 0.0
    # [F296 2026-09-21] **超时出库改为"只挂单不 taker"**（True = 启用）。
    #
    # 动机（用户 2026-09-21 指出「挂单交易没有手续费」，本轮账本实测印证）：
    #   · 入场腿（maker）12,269 笔 平均 fee = **0.0000 bp** —— 完全免费
    #   · 强平腿（taker）   917 笔 平均 fee = **−3.9956 bp**
    #   · 强平腿累计 taker 费 **−$50.87**，而整夜亏损 −$52.20 ⇒ **97% 的亏损＝taker 费**
    #   · 917 笔强平里 `price_bp ≤ −40bp` 只占 **10.9%**、中位漂移仅 **−7.39bp**
    #     ⇒ 近 90% 的强平发生在"行情几乎没动"时 —— 纯粹为「时间到了」付费
    #   · 被动出库成功样本 96.1% 在 120s 内、100% 在 300s 内完成
    #     ⇒ **300s 不是瓶颈**，价格回不来不是因为等不够
    #
    # True ⇒ 超时只登记 `skip="timeout_maker_only"` 并撤加仓侧，把出库交给
    #        减仓侧挂单；taker 权只留给 ①′ 价格止损（真·价格不利）。
    # False ⇒ 旧行为逐字一致（可一键回退 ✓）。
    #
    # ⚠️ 代价：持仓变长 ⇒ 敞口占用更久，可能顶净/总敞口上限而阻塞新仓。
    #    监控：`states.*.timeout_exit_blocked` + `skip_counts.symbol_exposure/net_exposure`。
    timeout_exit_maker_only: bool = False
    # [F301 2026-09-21] **止盈主动平仓阈值（bp）**。0 = 关闭（旧行为逐字一致）。
    #
    # 动机（用户 2026-09-21 提出「检测盈利过多少之后是不是可以进行主动平仓？」，
    # 用真实盘口做了两轮模拟验证后确认可行）：
    #
    # 现况是出库**只走被动**（maker 免费）⇒ 每笔净 +0.1374bp（本时代实测），
    # 但持仓慢、单边行情里会长持并累积方向性库存。而入场后的**最大有利偏移**很大：
    #
    #     MFE（180s 窗口，n=300）  p25 +2.71  中位 **+6.77**  p75 +14.84  p90 +22.30 bp
    #
    # ⇒ 若在浮盈达到 T 时主动（taker）平仓，净得 `T − taker费(4bp)`；
    #   未触发则照旧走被动。**规则期望**（严格口径：价格必须被对手方打到才算成交）：
    #
    #     T=5   触发 55.6%  净 +0.556 bp/笔     T=20  触发 12.0%  净 +1.920
    #     T=8   触发 44.0%  净 +1.760           T=30  触发  5.6%  净 +1.456
    #     **T=12 触发 32.0%  净 +2.560**  ← 最优，为现实基准的 **18.6×**
    #
    # 关键验证：严格口径触发率（32.0%）与乐观口径（31.3%）**几乎相同**
    # ⇒ "价格到过就能成交"，不是"擦到但没被吃"，规则可落地。
    #
    # ⚠️ 代价与风险：
    #   · 触发时**付 4bp taker 费** ⇒ T 必须 > 4bp 才有意义（T=3 时净为负，实测已证）
    #   · 触发率过高（>80%）等于把出库全换成 taker，必亏 ⇒ 阈值不能定太低
    #   · 会**提前砍掉赢家**的右尾（本该跑到 +30bp 的仓位在 +12bp 就被平）
    #     ⇒ 落地后必须实测"触发率 + 每笔净额"，与 +0.1374bp 基准对比
    #   · 与 `stop_loss_bp` 对称：一个是亏损侧硬上界，一个是盈利侧落袋线
    take_profit_bp: float = 0.0
    # 止盈腿的"先 maker 后 taker"宽限（秒）：0 = 触发即 taker。
    # >0 时先让**减仓侧 maker 单**尝试 N 秒（正常报价流程每 tick 都在挂），
    # 超时才主动平 —— 与 `stop_maker_grace_sec` 同一套语义，方向相反。
    take_profit_maker_grace_sec: float = 0.0
    # ── [F346 2026-09-23] 反转衰减离场（H284 P3 证据）──────────────────────
    #
    # H284 出场政策模拟（48h、8.2k 腿/政策，mid-to-mid + taker 出场 4bp）：
    #   P0 固定 6bp 止损    净 −3.34bp/腿  MAE −3.9
    #   P3 反转衰减离场     净 −0.80bp/腿  MAE −1.7（最优且最浅）
    # ⇒ 固定硬止损在均值回归策略里反复卖在坑底还付 4bp 费；衰减离场
    #   用「30s 趋势重新反向延伸 ≥ 4bp」当离场信号，把深止损消灭。
    # 语义：持仓满 `min_age` 后，30s 趋势朝不利方向延伸 ≥ `reversal_decay_bp`
    #   即触发离场：先减仓侧 maker 挂 `grace` 秒（0 费），超时 taker 兜底。
    # 0 = 关闭（旧行为逐字一致）；落地时配套把 stop_loss_bp 提到宽幅兜底值。
    reversal_decay_bp: float = 0.0
    reversal_decay_min_age_sec: float = 15.0
    reversal_decay_grace_sec: float = 30.0
    # [h325 2026-09-26] 衰减判定窗口（mid_hist 期数，每期≈15s）。
    # 原来硬编码 `trend_move_bp(mid_hist, 2)` = 30s。h284 出口网格（168h/48h 双窗复现）：
    #   窗口 30s → 净 −0.852/−0.883bp、MAE −1.80/−1.81
    #   窗口 60s → 净 −0.821/−0.798bp、MAE −1.34/−1.31（更浅的逆行深度 = 尾部风险更小）
    # ⇒ 4 期（60s）在净收益与 MAE 两个口径上都更优。0/缺省 = 2 期（旧行为）。
    reversal_decay_window_periods: float = 2.0
    # [F348 2026-09-23] 跳变速退：单 tick 跳变（一个 15s 快照内 >tick 阈值的逆动）
    # 直接 taker 速退。衰减离场需要"30s 持续延伸"看不见这种跳变；
    # 40bp 兜底接住时已 −33bp（含滑点+费）。速退阈值 12bp、触发条件
    # 含"最近一 tick 逆动 ≥ 8bp"（只打跳变，不打慢磨——慢磨归衰减离场管）。
    jump_exit_bp: float = 0.0
    jump_exit_tick_bp: float = 8.0
    # [F349 2026-09-23] model 模式的 θ 门槛分数：|r60| ≥ frac × sd 才挂单。
    # 0.25 ≈ 3.1bp（默认）；调小提频、调大提质。
    model_thr_frac: float = 0.25
    # [F350 2026-09-23] 流衰竭确认（H307 双窗验证 −0.74→−0.56bp/腿）：
    # 逆势方向与驱动该趋势的主动流同向（ofi×trend>0 且 |ofi|≥thr）→ 封加仓侧。
    # 0 = 关闭（旧行为一致）。减仓侧永不受限。
    ofi_confirm_threshold: float = 0.0
    # [R201 2026-09-29] **饱和要求闸**（用户裁决 A：忠实实现 h483 的"要求"口径）。
    #
    # 与上面两个 OFI 闸的**方向相反**，这正是它存在的理由：
    #   · `ofi_block_threshold` / `ofi_confirm_threshold` 是**禁令**：`|ofi| > θ` ⇒ 封逆势加仓侧
    #     ⇒ **调高 = 放松**；且两者都带 `and allow_*` 前置守卫 ⇒ 被更早的趋势闸
    #     （`trend_pause_bp=15`）抢先 ⇒ 实测窗口内 3500 次拦截里 confirm 闸 **0 次** ✗（R201）
    #   · h483 的样本外结论用的是**要求**口径：只保留 `fo ≥ θ` 的腿（+1.91bp、腿量 ×0.92、
    #     两半独立复现）⇒ 现成参数**一个都实现不了**它 ⇒ 必须新加这个字段 ✓
    # 语义：`|ofi| < 此值` ⇒ **不建仓**（加仓侧两侧都封，因为空仓时两侧都是加仓）；
    #       **减仓侧永不受限**（F76：封减仓会让库存只能等超时 taker 平，是历史亏损主因 ✗）。
    # 0 = 关闭 ⇒ 与改动前**逐字一致** ✓；它是**要求**族，所以**调高 = 收紧**（与上面两个相反）。
    ofi_require_threshold: float = 0.0
    # [F302 2026-09-21] **撤掉减仓侧出库挂单**（True = 不挂）。默认 False（旧行为一致）。
    #
    # 动机（用户选定方案 ①「撤掉减仓侧出库单，让止盈成为主要出库路径」）：
    #   实测（真实账本）止盈腿 **+7.44bp/笔**，而普通 maker 出库腿只有 **+0.3bp/笔**。
    #   而出库挂单与止盈**在同一条"有利偏移"轴上竞争，出库单总在更近处**
    #   （出库赚 `r×半价差` ≈ 0.2bp，止盈要 +12bp）⇒ 挂着出库单时止盈几乎永不触发
    #   （H172 实测止盈占比恒 0.0%）。
    #
    # True ⇒ 有仓位时**不挂减仓侧报价**，出库只能靠：
    #   ①′ 价格止损（40bp，taker）
    #   ①″ 止盈（+12bp，taker）
    #   ②  超时（`timeout_exit_maker_only=False` 时 taker；True 时继续等）
    # ⚠️ 单独使用会把仓位拖很久 ⇒ **必须配套 `timeout_exit_maker_only=False`**，
    #    否则到不了止盈线的仓位会永远占着敞口（H176 实测这类占 38%，
    #    且它们在 300s 时的中位浮亏是 **−15.89bp**）。
    reduce_quote_disabled: bool = False
    trend_pause_bp: float = 0.0                 # 近 N 期中价单向移动 > 此值 → 禁止逆势侧（0=关闭）
    trend_lookback: int = 20
    # [h697 2026-10-01] 趋势闸方向模式:0=h324(跌禁买/涨禁卖);2=h697
    # (当日 1304 腿实测:顺势 −1.55bp/逆势 +1.15bp ⇒ 跌放 fade 买/涨禁双边)
    trend_block_mode: float = 0.0
    # [h652 §9.3 候选] 方向卡 min_n 自适配开关:0=关闭(固定 15);
    # 1=按该币该边 60 分钟腿频 clamp(round(0.25×rate), 12, 30)。上线须走独立单变量试跑。
    direction_min_n_adaptive: float = 0.0
    # [h657] Q 速控总开关:0=影子(只算只记,不动交易);1=减速侧启用
    # (Q<0.4 停加仓 / 0.4~0.7 半速 / ≥0.7 全速,滞后再武装)。默认 0。
    q_speed_gate: float = 0.0
    # [h664] 微价闸排除清单(逗号分隔币名,h365:BNB 微价反向,须排除)。
    # 空串 = 不排除(旧行为)。
    mp_block_exclude: str = ""
    # [h664/h688] 方向分数融合(三个综合:微价+流+趋势):
    #   0 = 影子(只算只记,不动交易)
    #   2 = **中段带否决(已上线)**:|D| < dir_min_abs ⇒ 加仓腿量 ×0(=停加仓)
    # 依据(12h 影子分桶,2026-10-01):|D| 最低桶 −0.25bp,其余桶 +0.38~+0.66bp;
    # 尾部桶 +0.04 不可预测 ⇒ **只用已验证的低分否决,不用尾部分数放大仓位**。
    dir_score_fusion: float = 0.0
    # [h688] 否决阈值(仅 fusion>=2 生效):|D| 低于此值 = 三路不一致 ⇒ 停加仓
    dir_min_abs: float = 0.35
    # [h692] 尺寸倾斜强度(仅 fusion>=3 生效):bid_mult=1+k·D, ask_mult=1−k·D,
    # 夹紧 [0.25,1.75]。两侧都挂、流量不减,只把仓位往 D 方向倾。
    # 依据:h690 实测 IC≈0.03 的信号不足以封锁挂单(否决版/侧封锁版都 0 成交),
    # 正确用法是尺寸倾斜(保流量 + 用信号)。
    dir_skew_k: float = 0.5
    # [h333 2026-09-26] 平缓时段全停（0=关闭）：|近 N 期趋势| < 此值 ⇒ 暂停全部**加仓侧**
    # 挂单（减仓侧豁免，F76 语义）。依据：h323 真实成交 markout 96h/24h 双窗复验
    # （中位数口径）——强趋势顺势腿 mk30 +4.4~+5.3bp（n=576~928），而其余成交
    # −1.4~−2.9bp（占总成交 ~90%）。平缓时段两侧都挂 = 系统性放血；
    # 只在强趋势做顺势腿 = 车道唯一稳健的正期望子集。
    trend_only_bp: float = 0.0
    # [h338 2026-09-26] VPIN 毒性暂停（0=关闭）：滚动 20 桶 |OFI| 均值 > 此值
    # ⇒ 暂停该币全部加仓侧（减仓侧豁免，F76 语义）。依据：h333 用我们自己的
    # 4,835 笔成交实测——VPIN_20 低分位 mk30 −0.014bp、高分位 −0.482bp、
    # 斜率 −1.27bp/单位VPIN（单调）。低 VPIN 环境逆选择≈0。
    # 文献：Easley-López de Prado-O'Hara (2012) VPIN（CDF>0.9 毒性预警）。
    vpin_pause_threshold: float = 0.0
    # [h341 2026-09-26] 15 分钟反转（备选规则）：side_mode="slow_rev" 时，
    # |r900|（60 期×15s）≥ 此值 ⇒ 只挂反向（fade）侧，顺势加仓侧封（减仓豁免）。
    # 依据：h339 多窗口验证（h284 chase 模型）168h +0.10 / 96h +0.72 /
    # 48h +3.57 / 24h +6.67 bp/腿 —— 研究内唯一全窗为正的信号配置。
    slow_rev_min_bp: float = 40.0
    # [h354 2026-09-27] P2 形态（VWAP 回归，30s-5min 高频形态库第 2 号，0=关闭）：
    # |现价 − 60s 成交VWAP| ≥ 此值 ⇒ 只挂向 VWAP 回归的一侧（偏离上方只挂卖、
    # 下方只挂买），减仓侧豁免。h350 事件研究：+0.57bp@120s（t=2.2，7.4次/h/币）。
    # 实盘试跑框架（h353）：参数热采用 + 时代判决 + 回退置 0。
    vwap_revert_bp: float = 0.0
    # [h359 2026-09-27] P2 v2：流驱动偏离回避（0=关闭；上线值 0.3）。
    # h358 条件模型（n=95,726）：P2 回归侧期望收益由 OFI 流向决定——OFI 已回归
    # （与回归方向同向）f30 +0.79(t=16.1)/f120 +0.80；OFI 在推离（流驱动偏离）
    # f30 −0.79(t=−23.9)/f120 −0.65。⇒ |偏离60s VWAP|≥vwap_revert_bp 且 OFI
    # 推离 VWAP（|ofi|≥此值）时，回归侧也封（不 fade 流）；减仓侧豁免（F76）。
    vwap_flow_block: float = 0.0
    # [h389 2026-09-27] 尾随锁利（0=关闭；上线值 20）：浮盈（MFE 口径）≥ 此值
    # ⇒ 止损线从 −40 抬至 −5（保本），此后每再 +10bp 抬 10bp（+30⇒+10、+40⇒+20…）。
    # 依据：h388 影子测验——16 笔止损腿现行 −1046.7bp vs 尾随 −47.1bp（净回收
    # +999.6bp）；且这些腿 MFE 普遍 +30~+108bp（静态 TP30 在快行情从未锁利）。
    # 触发离场复用止损的 maker 宽限→taker 流程，exit_path=trail_lock_taker。
    trail_lock_bp: float = 0.0
    # [h395 2026-09-27] #16③ 止损后降腿量（0=关闭；上线值 0.5）：该币在 30 分钟内
    # 发生过强制止损离场（stop_loss_taker / trail_lock_taker）⇒ 其**加仓腿**名义
    # ×(1−此值)。依据 h394 影子测验：#2 窗口连环止损 13 对，止损后 30min 内同币
    # 107 腿净 USD −2.76（NEAR/SUI 主导），降半档净效果 +$1.38/5h；不损腿速
    # （腿数不变，只缩名义）。减仓腿不受影响（否则残仓平不掉，F338 同理由）。
    post_stop_decay: float = 0.0
    # [h356 2026-09-27] P1 薄流确认（0=关闭；上线值 0.3）：h355 条件模型（n=12,925，
    # 168h）——顺势回调的期望收益由当前 OFI 流向决定：OFI 顺势（薄流回调）
    # f30 +1.06(t=6.6)/f120 +1.40；OFI 逆势（流驱动回调）f30 −0.98(t=−7.3)/
    # f60 −0.72（h351 流延续律主导）。⇒ |300s 趋势|≥15bp（P1 触发区）且 OFI
    # 逆势流动 |ofi| ≥ 此值 ⇒ 封趋势同向加仓侧（放弃流驱动回调），减仓侧豁免
    # （F76）。注意：300s 趋势独立计算（side_mode=model 时 _trend_bp 是 60s 模型方向）。
    pullback_flow_block: float = 0.0
    # [h400 2026-09-27] P1 触发区下限（bp，|300s 趋势| ≥ 此值 = P1 触发；默认 15）。
    # 试跑 #10：15→10——h366 频率 ×1.8、总 edge/h +59%（+1.22@15.1/h vs +1.40@8.3/h）。
    # 只影响 pullback_flow_block（#3）的触发区判定；trend_pause_bp 是独立闸不受影响。
    p1_trigger_bp: float = 15.0
    # [h363 2026-09-27] P4 双触突破 with 闸（0=关；上线 0.3）：120s 窗口两次触及 60s
    # 极值且现价贴极值（P4 触发）且 OFI 与突破方向同向（|ofi|≥此值）⇒ 封逆突破侧。
    # h361: with f30 +0.66(t=17.6)/f300 +1.11；against f30 −0.58(t=−10.1)。
    # 减仓侧豁免（F76）。
    p4_breakout_gate: float = 0.0
    # [h363 2026-09-27] P5 挤压突破 with 闸（0=关；上线 0.3）：60s 波动 < 滚动 30 分位
    # 且 |r15|≥2bp（P5 触发）且 OFI 与动量方向同向 ⇒ 封逆动量侧。
    # h361: with f30 +0.77(t=22.5)；against f30 −0.76(t=−14.6)。减仓侧豁免（F76）。
    p5_squeeze_gate: float = 0.0
    # [h401 2026-09-27] P3 尖峰 fade with 闸（0=关；上线 0.3）：|75s 移动|≥3bp（尖峰）
    # 且 OFI 与 fade 方向同向（|ofi|≥此值）⇒ 封追尖峰侧（上尖峰封买/下尖峰封卖），
    # 减仓侧豁免（F76）。h361: with f30 +0.857(t=10.2)/f60 +0.822；
    # against f30 −0.972(t=−18.2)。live 映射：trend_move_bp(mid_hist, 5) ≈ 75s。
    p3_spike_gate: float = 0.0
    # [h403 2026-09-27] #8/#15 分形态持有期（0=用 max_one_side_seconds，旧行为逐字）：
    # 持仓开仓时刻被标记为 P1 / P45 形态时，其超时上界改用对应值。
    # · p1_hold_sec：P1 回调腿持有期（试跑 #15：→60s；h360 fixed60 +0.80 ≈
    #   fixed120 +1.08，h373 第一半窗红利）。
    # · p45_hold_sec：P4/P5 突破腿持有期（试跑 #8：→300s；h360b fixed300
    #   +1.085(P4)/+0.889(P5) vs 现行栈 +0.752/+0.689）。
    # 标记在开仓时刻由 mid_hist 形态检测得出（研究口径固定阈值），与闸门参数无关。
    p1_hold_sec: float = 0.0
    p45_hold_sec: float = 0.0
    # [h411 2026-09-27] 持仓硬上限（秒；0=关闭=旧行为）：`timeout_exit_maker_only=true`
    # 时 90s 超时只"停止加仓+挂减仓地板单"，行情不回头则持仓可无限拖长
    # （实测 ADA 空单 78min、史上孤儿仓 4.8 天）⇒ 违反用户"30s~5min 时域"约定。
    # 此值 >0 时：持仓年龄超过它 ⇒ 无条件 taker 平仓（exit_path=timeout_hard_taker）。
    # 用户时域约定映射 = 300（5 分钟硬上限）。
    timeout_hard_taker_sec: float = 0.0
    # [F71b] 已实现波动闸门：高波动时平仓成本吞掉价差（实测当前市场波动
    # 约为回放窗口 2.3 倍，超时平仓 −26.7bp/笔）→ 超过基准倍数即暂停该币。
    vol_pause_mult: float = 0.0                 # 0=关闭；1.5 表示 >1.5×基准即停
    vol_window: int = 20
    # ── [F86 2026-09-14 学术升级] 流向毒性闸 ──
    # 依据：Lu & Abergel 2018（队列反应模型：**市价单驱动的移动会延续**——实测
    # 其样本中 84.3% 延续 vs 撤单驱动仅 27%）+ Barzykin/Bergault/Guéant/Lemmel 2025
    # 《Optimal Quoting under Adverse Selection and Price Reading》逆向选择框架。
    # 本项目实证（asterdex BTC 近 3 天 1.5 万快照）：主动流失衡
    # OFI=(买主动-卖主动)/(买+卖) 对**下一期**中价收益 corr=+0.084；OFI<-0.5 后
    # 下一期 86.5% 继续下跌（均值 -0.257bp）；OFI>+0.5 后 +0.195bp（右偏长尾）。
    # ⇒ 逆势侧挂单会在信息流之后成交（典型逆选择）⇒ 按 OFI 封锁逆势**加仓侧**；
    # 减仓侧永不受限（F76 库存感知语义）。
    ofi_block_threshold: float = 0.0            # 0=关闭；0.5=上一桶 |OFI|>0.5 即封锁逆势加仓侧
    # [F272 2026-09-16 · E2 选择性成交] microprice 偏离闸：>0 时，若
    # |microprice − mid| / mid ≥ 该值（bp）⇒ 封锁**会被逆向选择的那一侧**：
    #   microprice 高于 mid（买压，价大概率上行）⇒ 封卖（挂卖会被抬起后价格继续涨）
    #   microprice 低于 mid（卖压，价大概率下行）⇒ 封买（挂买会被砸中后价格继续跌）
    # 减仓侧豁免（与 F76/F228 同语义）。0 = 关闭（旧行为逐字一致）。
    # 依据：调研 HFT-ML §8.3 第 2 步「用法 B（毒性流闸门）——最可能赚钱的用法」；
    # 动机：F271 实测 markout 与净额反向 ⇒ 单纯收闸门无法同时改善，必须"同价位筛成交"。
    mp_block_bp: float = 0.0
    # [F274 2026-09-16 · E2' 持续性单边流闸] >0 时：若**连续 N 个判定桶**的主动流
    # 同向且强度 ≥ `flow_persist_floor`，判定为「持续性单边流」（趋势推进中，
    # 逆势挂单会被系统性逆选择）⇒ **该币站开不挂**（两侧都撤）。
    # 与 ofi_block_threshold 的区别：后者只看**单桶**毒性，本闸看**多桶持续性**
    # （单桶可能被噪声打断，而真正的趋势推进是连续同向的 —— 这正是 09-15 夜
    # 那种"慢漂移连续吃掉挂单"的形态）。
    # 0 = 关闭（旧行为逐字一致）。
    flow_persist_pause: int = 0
    flow_persist_floor: float = 0.2         # 单桶计入连续性的最小强度 |OFI|
    # [F279 2026-09-16 停机断点补齐] True 时：快照间隔 > `MID_SPLICE_MIN_GAP_MS`
    # （默认 90s）⇒ 在追加新中价前，把停机期间**缺失的快照**从行情库补回 `mid_hist`。
    # 动机（本轮实测）：重启后持久化历史停在停机前，第一条新快照直接接上 ⇒ 中间
    # 5~20 分钟的真实路径被压成**一条伪收益** ⇒ `realized_vol_bp` 抬高 ⇒
    # `vol_regime_blocked` 把该币站开一整个窗口（15s × 20 ≈ 5 分钟）× 每次重启。
    # 模型侧数据连续、没有这条伪收益 ⇒ 实盘报价时间系统性少于模型 ✗。
    # False = 关闭（旧行为逐字一致 ✓，可一键回退）。
    mid_splice_on_gap: bool = False
    # [F86] 流向择时平仓：持仓年龄 > hold × min_age_ratio 且 OFI **顺离场方向**
    # （多头遇买压=高价卖出、空头遇卖压=低价回补）⇒ 提前 taker 平仓，替代盲等超时。
    # 逻辑依据：平仓腿是最大成本项（实测均 -9.4bp、占 16-25%）；把平仓时点从
    # 「固定 900 秒」改为「流向顺风时」，理论上是执行时机的改进（最优执行文献）。
    ofi_flatten_threshold: float = 0.0         # 0=关闭；0.5=|OFI|>0.5 视为顺风
    ofi_flatten_min_age_ratio: float = 0.5     # 持仓超过 hold 的该比例后才考虑择时平仓
    # ── [h472 2026-09-29] ofi_flatten 的**执行方式**：taker 穿价 vs 被动减仓 ──
    #
    # 现场证据（h471，近 12h、795 腿、名义 101,716$、净 −12.55$）：
    #   · 手续费 = −9.25$ = **亏损的 74%**；而本场馆 `edge_source` 明写
    #     "spread+mkr(asterdex maker 0%)" ⇒ **maker 费率 0，任何 taker 都是净支出**；
    #   · 近 12h 的 192 笔出场**全部是 taker**（ofi_flatten 102 / 止损 35 / 硬顶 35
    #     / reversal_decay 10 / trail_lock 9 / take_profit 1）⇒ 被动回摆出场为 0 笔；
    #   · ofi_flatten 这条路径：102 腿已实现 **+0.17bp/腿**（价格项 +4.89bp/腿，说明
    #     "顺风离场"的判断是对的），却付 **−3.53bp/腿 taker 费 + −1.19bp/腿穿价差**
    #     ⇒ 单条路径吞掉全部手续费的 49%（−4.56$），净贡献 −3.13$。
    #   · h470 反事实：ofi_flatten 出场后 300s 的**离场方向有利漂移 +9.91bp（t=2.15）**
    #     ⇒ 价格继续朝离场方向走 ⇒ 此时挂**被动**减仓单（不穿价）本就容易被对手方吃掉。
    #
    # 结论：信号（何时该出）不动，只把**执行**由"立即 taker"改为"撤加仓侧 + 挂被动减仓"
    # （复用 h413 已接线的 `_tmo_add_block` 机制，减仓侧豁免 F76）。预期每腿省下
    # 穿价差 + taker 费 ≈ 4.7bp；极端情况下仍未成交则由 300s 合规硬顶兜底。
    #
    # 0 = 关闭 = 旧行为逐字一致（立即 taker，`exit_path=ofi_flatten_taker`）；
    # 1 = 被动减仓（不落 taker 腿，`exit_path=ofi_flatten_maker`，只记遥测）。
    ofi_flatten_maker_only: float = 0.0
    # ── [F89a 2026-09-14] 陈旧挂单保护 ──
    # 现场事故：币种重新加入宇宙时，运行态里残留着**数天前的挂单**（quote_bid/ask），
    # 首个 tick 把「当前区间成交」判成这些旧价位的成交——4 笔幻影成交、净敞口冲到
    # -$739（上限 $300），且账本用旧 ref_mid 记成 +8~+12bp 假盈利。
    # 保护：挂单年龄 > max_quote_age_sec（默认 90s = 6 个 tick）⇒ 直接丢弃挂单、
    # 不做成交判定（与「数据陈旧撤单」同源，但覆盖「状态陈旧」场景）。
    max_quote_age_sec: float = 90.0
    # ── [F254 2026-09-20] 队列优先保持：价位没变就不重挂 ──────────────────
    #
    # 真实交易所里"重挂" = 撤单 + 重新排队，**队列位置清零**（排到当前挂量最后）。
    # H19（`scripts/h19_queue_fill_model.py`，tick 级真实数据、15s 报价节奏、12h）
    # 量化了这个代价：
    #     队尾（LA=全部挂量）成交率 24–37%，mk@1s −0.22 ~ −0.62bp
    #     队首（LA=0）       成交率 85–95%，mk@1s −0.12 ~ +0.27bp
    #     ⇒ **队首 − 队尾 = +0.42bp @1s**（跨 4 个有效样本币）
    # 我们实测净边际只有 **−0.60bp** ⇒ 队列位置与整个策略盈亏同量级。
    # 论文 Table 1 独立给出同格内差 0.12–0.86bp，量级一致。
    #
    # 本参数 = 视为"同一价位"的相对容差（bp）。新报价与当前挂单相对差小于它时，
    # **保留原价与原 `quote_ts`**，不回队列。
    #   · 0.25（默认）：覆盖 tick 间中价的正常微抖，同时不会把真实的价格移动当成"没动"
    #   · 0 或负：**关闭**，与旧行为逐字一致（可一键回退）
    #
    # ⚠️ 对纸面回测**恒等于 0 收益**：我们的成交模型不建队列，"保持"与"重挂"
    #    产出完全相同的成交。本参数是**纯实盘准备**——一旦真实下单，
    #    这个语义差异就是直接的钱，且事后从账本里看不出我们有没有保持位置。
    quote_hold_tol_bp: float = 0.25
    # ── [h527 2026-09-29] **逐币单币敞口比例**：`{"NEAR": 0.03, "XRP": 0.15}` ──────
    # 覆盖 `max_net_directional_ratio`（该字段是**全局一份**，所有币同一个上限）。
    # 为什么需要（h524，当前时代 15.79h，按名义加权）：
    #   币    腿/h   净bp/腿    止损腿   费用bp
    #   BNB   30.8   −1.263      2     −0.658
    #   XRP   12.8   +0.134      2     −0.629
    #   NEAR  11.5   −3.526     22     −1.550
    #   ARB    5.8   −3.436      8     −1.692
    #   ENA    3.4   −2.777      8     −1.801
    # NEAR+ARB+ENA 占 32% 的腿、却占 90% 的止损腿；但直接删币会让 64.5 腿/h
    # 掉到 43.9（破 ≥60 硬约束）。**腿数是"事件数"、不是名义额** ⇒ 按币缩小
    # 单笔规模可以**在腿数不变的前提下**把尾部 USD 亏损等比压下去，
    # 且敞口闸更晚触顶 ⇒ 该币的可挂 tick 更多（腿数不减甚至增）。
    #
    # 语义：`one_side > equity × ratio(symbol)` 时挡加仓侧（减仓侧照旧豁免）；
    # 同时作为 `inv_ratio` 的分母（与 runner/replay 一致）。
    # 缺失/非法 ⇒ 回退 `max_net_directional_ratio`（**旧行为逐字一致** ✓）。
    # ⚠️ 放在 dataclass **末尾**：排除位置构造被字段插入错位的风险。
    per_symbol_max_notional_ratio: Optional[Dict[str, float]] = None
    # ── [h527] **逐币单笔规模倍数**：`{"NEAR": 0.35, "ARB": 0.35, "ENA": 0.35}` ─────
    # 作用于**加仓腿目标名义**（`_add_notional = fill_notional(=compound_ratio×
    # equity) × (1−post_stop_decay) × mult`），与 `max_net_directional_ratio` 的区别：
    #   · `max_net_directional_ratio` 只是**上限**与 `inv_ratio` 分母，
    #     实测上限 $565 ≫ 单笔 $71（P50）⇒ 它对"单笔多大"几乎不起作用；
    #   · 本字段直接缩**目标量** ⇒ 才是真正的"规模"旋钮。
    # 为什么必须只作用加仓腿：减仓腿按 `min(|现仓|, 队列份额)` 精确平仓（F91/F338），
    # 跟着缩小会留下永远平不掉的残仓，把亏损从价差搬到 taker 强平（已见过两次）。
    # 缺失/非法/≤0 ⇒ 1.0（**旧行为逐字一致** ✓）。
    per_symbol_size_mult: Optional[Dict[str, float]] = None
    # ── [h621 2026-09-29] **规模波动衰减**：加仓腿目标量 ÷ (1 + k × σ_capped) ──────
    #
    # 根因（本轮取证 `_audit_hft_math_model.py`，当前时代 470 腿）：
    #   现行 `qty = min(目标, 桶主动量×0.30)` —— 桶量只是**上限**，于是
    #   · 平静桶（好腿）被削到 $10~50，
    #   · 剧烈桶（逆选择最重、price_bp 最差的桶）吃**满目标** $139
    #   ⇒ 规模与桶剧烈度**正相关** = 结构性逆选择放大器（F338 的宏观版）。
    # 实证（A2）：满额腿（≥90% 目标）**−1.34 bp/腿** vs 被削腿 **+0.79 bp/腿**，
    #   差 2.14bp；A1（币内名义中位分半）：**5/5 币**小半更优（均值 +0.58 vs −6.78）。
    # σ 是"桶剧烈度"的事前可观测量 ⇒ 用它把目标量在剧烈期压下来：
    #   σ=0 ⇒ 不变；σ=1、k=1 ⇒ 目标减半；σ 截断同 `k_vol_sigma_cap`（防 σ 无界）。
    # 0 = 关闭（**旧行为逐字一致** ✓）。只作用**加仓腿**（减仓腿精确平仓，F91）。
    size_vol_decay: float = 0.0
    # ── [h622 2026-09-29] 大波动停止新建仓 ──────────────────────────────────
    #
    # 账本（09-28 13:00Z 起）：止损腿 −$12.90，是净亏的最大一块。
    # 止损发生前约 1 分钟，这 5 分钟的价格已经动了 **44bp**（名义加权）；
    # 同期普通被动出场只有 **15–20bp**。h504 同向：5 分钟波动 0–10bp 的往返
    # 约打平，≥40bp 的往返 −4.69bp。
    # 语义：|近 lookback 期净移动| ≥ 本值 ⇒ **两侧都不新开/不加仓**，减仓侧照旧。
    # 0 = 关闭（旧行为逐字一致）。
    trend_add_block_bp: float = 0.0
    # [h709 2026-10-02] 自适应趋势门槛的**下限**(bp):分位数退化(样本少/分布被
    # 近期高波动污染)时不得低于此值,否则加仓被全部封死(实测 90s 拦 214 次、
    # 腿速 40/h→8/h)。山寨 300s 常规波动 5-15bp ⇒ 默认 8bp。
    trend_add_block_min_bp: float = 8.0
    # [h709 2026-10-02 腿速] 宇宙槽位数:4 → 6。当市场处于毒性行情时,三道流/波动
    # 闸(ofi_toxic / vpin / trend)会按设计挡掉大部分加仓侧(实测 1.69+0.70+0.50
    # 次/tick)⇒ 腿速由**币的广度**决定,而不是靠放松已验证的闸门阈值。
    universe_slots: int = 4
    # ── [h622 废止] 曾用于「名单币只减仓」。报价层已不再读取。──────────────
    #
    # 淘汰只通过当前宇宙 `symbols`：这一轮没被选中 = 不进交易序列。
    # 下一轮选回来就必须能交易。不要再往这个字段写永久禁开仓名单。
    entry_block_symbols: Optional[List[str]] = None
    # ── [h623] 分币尾部停加仓（分位数，不是全局固定 bp）────────────────────
    #
    # 全局 `trend_add_block_bp=30` 会把正常波动的山寨一起停光（瘫痪事故）。
    # 语义：用该币自己近 1h 的 |r300| 分布，达到分位数 q 才停加仓；
    # `trend_add_block_bp` 仍作绝对下限（安静时段兜底）。0 = 关闭分位数。
    # [h711 2026-10-02] 趋势闸滞后保持(秒):检测到趋势后,5 分钟口径闪烁期
    # (慢牛里约 5% tick)仍保持封锁,挡住漏网的逆势腿(实测 -2.89bp/腿)。
    # 0 = 关(旧行为)。
    trend_block_min_hold_sec: float = 0.0
    # [h734 R2b 2026-10-03] 概率调制乘子:trend_block_mode>=3 时,趋势闸不再
    # 封侧,只把被拦侧**加仓腿量** × 此乘子(永不归零)。病根:硬开关在慢牛/
    # 单边流里把 83% tick 判成无单(市场 660 笔/时我方 0 腿),而影子数据显示
    # 方向信号只有 3~5pt(≈50%)⇒ 正确响应是减量不是封死。0=关(旧硬封锁)。
    trend_soft_size_mult: float = 0.0
    # [h749 2026-10-03] 冷却式日亏熔断(秒):触发后暂停 N 秒,**到点自动复开**;
    # 每次触发只在"跌破记录地板"时发生 ⇒ 每个冷却周期至多亏一个限额档。
    # 0=旧行为(暂停到日界)。状态持久化在 data/dl_fuse_state.json。
    daily_loss_cooldown_sec: float = 0.0
    # [h752 C1a 2026-10-03] 资金偏斜软闸阈值(|funding_rate| 分数,0=关):
    # 加密特化——极端资金费率=拥挤头寸信号(正=多头拥挤)。软模式下对拥挤
    # 方向的加仓腿 ×trend_soft_size_mult(概率调制,不是封单)。数据源
    # perp_funding(asterdex 实时采集)。
    funding_skew_thresh: float = 0.0
    trend_add_block_q: float = 0.0
    # ── [h623] 库存到此比例 ⇒ 停加仓侧（减仓照旧）──────────────────────────
    #
    # 绝对偏斜仍会被盘口钳制把加仓侧拉回最优价；有库存时必须显式撤加仓侧，
    # 否则继续在贴盘口接单。0 = 关闭。建议 0.2~0.35。
    inv_add_block_ratio: float = 0.0
    # ── [h624] Markout 探针 + 毒性停加仓（设计 §4.1）────────────────────────
    #
    # `markout_horizon_sec`>0 ⇒ 成交后延迟解析 mid，写入滚动样本（探针始终可观测）。
    # `markout_window_n`>0 ⇒ 启用停加仓闸：样本数≥n 且 m+cap < markout_halt_bp。
    #   halt_bp 可为 0（和为负就停）；窗口 n=0 时闸关闭（与改动前一致）。
    markout_horizon_sec: float = 0.0
    markout_window_n: int = 0
    markout_halt_bp: float = 0.0
    # ── [h624] 盈亏平衡开仓闸（设计 §4.2）：be_mult>0 启用───────────────────
    #
    # be = max(0,−m30) + taker_fee_bp × stop_share；捕获 < be×be_mult ⇒ 停加仓。
    be_mult: float = 0.0
    be_taker_fee_bp: float = 4.0
    # ── [h624] 跳价暂停（设计 §4.3）：jump_pause_bp>0 启用───────────────────
    #
    # 单步 |Δmid| ≥ bp ⇒ 双边撤单 jump_pause_sec 秒。建议 12~20 / 60。
    jump_pause_bp: float = 0.0
    jump_pause_sec: float = 0.0
    # ── [h625] 20 档深度选档：价差够宽才新开，不够就先撤加仓──────────────
    #
    # 只在「有新鲜 20 档」时生效。没有深度 = 不干预（照旧挂）。
    # 停留 book_slot_dwell_sec 秒内不来回换，避免一跳价就改挂谁。
    # 0 = 关闭。已有持仓的减仓侧照旧。
    book_slot_min_bp: float = 0.0
    book_slot_dwell_sec: float = 180.0
    book_slot_max_age_sec: float = 15.0


def lane_pause_reason(
    *,
    equity: float,
    limits: "LaneRiskLimits" = None,
    sigma_norm: float = 0.0,
    toxic_streak: int = 0,
    day_pnl_usd: float = 0.0,
) -> Tuple[bool, str]:
    """**车道级**暂停判据：返回 `(是否整车道暂停报价, 原因)`。

    [§82 执行 2026-09-11 / 决策 P5-A —— 清单第 19 条]
    与 `check_side_allowed` 的分工必须严格区分：后者是**单侧**许可，且"减仓方向
    永远允许"（库存到顶后仍要能挂减仓腿；F59 首轮回放 30 天仅 10 笔成交就是这个
    原因）。因此本函数**只收整车道级别的判据**（权益、波动、毒性流、日亏），
    **不含任何敞口判据** —— 敞口一律交给单侧闸门，否则一旦净敞口触顶就会把
    "减仓腿"一起停掉，库存只能靠超时砸单，把亏损从价差搬到 taker 成本上。

    日亏闸（`daily_loss_stop_pct`）在本次之前**全仓无任何消费方**（从未实现）：
    现在口径 = `day_pnl_usd <= -equity * pct / 100`，`pct<=0` 或 `equity<=0` 时关闭。
    """
    limits = limits if limits is not None else LaneRiskLimits()
    if equity <= 0:
        return True, "equity<=0"
    # [F98 2026-09-14] σ 闸口径必须与 `check_side_allowed` 一致：**0 = 显式禁用**。
    # 事故隐患：此前这里缺 `> 0` 守卫，而线上配置 `vol_pause_sigma=0.0`（表示关闭）
    # ⇒ 一旦有人打开 `MM_LANE_LIMITS_ENFORCE`（配置里的 daily_loss_stop_pct=10、
    # toxic_streak=3 都在暗示应当打开），σ>0 几乎恒成立 ⇒ **整车道永久停摆**，
    # 表现为「策略突然不成交」，排查成本极高。同一参数在两道闸门里含义相反 = bug。
    if limits.vol_pause_sigma > 0 and sigma_norm > limits.vol_pause_sigma:
        return True, f"vol_pause(sigma={sigma_norm:.2f})"
    if limits.toxic_streak and int(toxic_streak or 0) >= int(limits.toxic_streak):
        return True, f"toxic_streak({int(toxic_streak or 0)})"
    _pct = float(getattr(limits, "daily_loss_stop_pct", 0.0) or 0.0)
    if _pct > 0:
        _limit_usd = -abs(equity * _pct / 100.0)
        if float(day_pnl_usd or 0.0) <= _limit_usd:
            return True, f"daily_loss({float(day_pnl_usd or 0.0):.2f}<={_limit_usd:.2f})"
    return False, ""


def check_lane_limits(
    *,
    symbol: str,
    book: InventoryBook,
    marks: Dict[str, float],
    equity: float,
    limits: LaneRiskLimits = LaneRiskLimits(),
    now_ts: float = 0.0,
    sigma_norm: float = 0.0,
    toxic_streak: int = 0,
    day_pnl_usd: float = 0.0,
) -> Tuple[bool, str]:
    """车道级风控检查。返回 (allow_new_quotes, reason)。

    [§51.2 2026-09-10 三准则核查结论——**本函数当前无生产调用点**]
    核查（复现：`_audit_ml/Z60_mm_lane_probe.py` + 全仓 grep）：
      1. 零调用点：仅 `backend/tests/unit/test_f58_market_maker_core.py` 调用；
         `runner.py` / `replay.py` 走的是更细粒度的 `check_side_allowed`
         （单侧报价许可）而非本函数。
      2. 真实消费方确实存在：`runner.plan_tick` 每个 tick 都维护
         `state.toxic_streak`（`runner.py:311-314`，成交后逆行 >`toxic_bp` 则 +1）
         并落库（`runner.py:75/95`），影子车道 `mm_asterdex` 状态 **active**
         （`lane_registry`，2026-09-10 10:46 仍在推进，`lane_ledger` 323 行）。
      3. 真实数据确认：因此「毒性流暂停」这一安全属性在运行中的影子里**从未生效**；
         等价地缺失的还有 `max_one_side_seconds` 的车道级版本（该条在
         `runner.py:340` 有等价内联实现）与 `daily_loss_stop_pct`
         （`grep` 全仓**无任何消费方**，从未实现）。
    判定：属"静默死闸"，但**不影响实盘**（该车道 mode=paper 影子期），影响的是
    影子证据链的完整性（影子 PnL 缺两道闸门 → 偏乐观）。

    [§82 执行 2026-09-11 / 决策 P5-A] **已接线**：`runner.plan_tick` 现在通过
    `lane_pause_reason()`（本函数的前半段，只含车道级判据）在 `MM_LANE_LIMITS_ENFORCE=true`
    时真正执行 toxic_streak / 日亏暂停；本函数保留为"车道级 + 敞口"的完整契约，
    供测试与将来的整体闸门使用（生产路径不整段调用，避免把减仓腿一起停掉）。
    若将来接线，请让 `runner.plan_tick` 调用本函数并把 `state.toxic_streak` 作为入参传入。
    """
    _paused, _why = lane_pause_reason(
        equity=equity, limits=limits, sigma_norm=sigma_norm,
        toxic_streak=toxic_streak, day_pnl_usd=day_pnl_usd,
    )
    if _paused:
        return False, _why
    net = abs(book.net_notional(marks))
    if net > equity * limits.max_net_exposure_ratio:
        return False, f"net_exposure({net:.0f}>{equity*limits.max_net_exposure_ratio:.0f})"
    one_side = abs(book.notional(symbol, marks.get(symbol, 0.0)))
    _ratio = symbol_lookup(getattr(limits, "per_symbol_max_notional_ratio", None),
                           symbol, limits.max_net_directional_ratio)
    if one_side > equity * _ratio:
        return False, f"symbol_exposure({one_side:.0f})"
    if book.holding_seconds(symbol, now_ts) > limits.max_one_side_seconds:
        return False, "one_side_too_long"
    return True, ""


# ═══════════════════════ [F71] 尾部亏损闸门 ═══════════════════════

def unrealized_bp(qty: float, avg_mid: float, mid: float) -> float:
    """持仓浮动盈亏（bp，正=有利）。按中价对中价计，与已实现口径一致。"""
    if abs(float(qty)) < 1e-12 or avg_mid <= 0 or mid <= 0:
        return 0.0
    return ((mid - avg_mid) if qty > 0 else (avg_mid - mid)) / avg_mid * 1e4


def add_blocked_underwater(qty: float, avg_mid: float, mid: float,
                           max_underwater_bp: float) -> bool:
    """[h897 2026-10-07] 加仓摊平 guard:持仓浮亏超过阈值 ⇒ 不再加(只加赢家)。

    用户:「发现买入点就买入」——加仓上限已放开(inv_add_block_ratio 0.5),
    但亏损加仓(摊平)是 h623 库存堆积大亏的根因,必须除外。
    返回 True = 禁止加仓(该侧);False = 允许。空仓/数据无效 ⇒ False(不拦)。
    max_underwater_bp ≤ 0 ⇒ 关闭(不拦)。
    """
    if max_underwater_bp <= 0:
        return False
    if abs(float(qty)) < 1e-12 or avg_mid <= 0 or mid <= 0:
        return False
    return unrealized_bp(qty, avg_mid, mid) < -float(max_underwater_bp)


# ═══════════════ [F258 2026-09-20] 交易时长窗口（30s ~ 5min） ═══════════════

# 本模块把**持仓时长**当作一条硬策略边界来管，而不是只靠"超时强平"一个上界。
#
# ## 用户给定的窗口
#
#   用户 2026-09-20：「交易时间进行锁定吧，既然都是把时间锁定在 **30秒到5分钟之内**，
#   这个是有验证过的」
#
#   这正是本模块最早的定位（「中短期高频交易 · 30s–5min · 点差捕获」），
#   但引擎此前**只实现了上界**（`max_one_side_seconds`），而且上界被改到了 900s，
#   **下限完全没有**。实测（H26，8h，66 个往返）：
#       持仓时长 中位 70s   p25 45s   p75 167s   max **3612s**
#       ⇒ 14% 的往返超出 5 分钟，最长 60 分钟
#
# ## 为什么下限同样重要（而不是"越早出库越好"）
#
# 出场腿的成本几乎是固定的（穿越点差 + 对手方逆向选择），与持有 1 秒还是 60 秒无关。
# 刚建仓就平掉 = 白付一次出场成本、且没有给对手腿任何成交机会。
# 实测（H26）：出场腿 −$0.032/往返，是入场腿盈利（+$0.011/往返）的 **3 倍** ——
# 每次强制出库都在付这笔固定成本。
#
# ## 这条窗口**只管强制出口**，不管被动成交
#
# 本方**被动**成交（对手方主动打过来）在任何时刻都被允许 —— 那是自然出库，
# 不是我们主动付成本。被拦住的只有**引擎主动发起**的四条出口路径：
#     ①′ 止损  ② 超时  ②′ OFI 择时  ③ 反手
# 这样下限不会妨碍"行情真来的时候赶紧走"（止损在下限内仍可挂被动减仓单）。
MIN_HOLD_SEC_DEFAULT = 30.0
MAX_HOLD_SEC_DEFAULT = 300.0


def microprice_skew_bp(bids: list, asks: list) -> float:
    """[h397 2026-09-27] #9 微价偏离（bp）：盘口量加权 microprice 相对中价的偏移。

    microprice = (bid_px·ask_qty + ask_px·bid_qty) / (bid_qty + ask_qty)（顶档口径，
    与 h365 研究一致）。返回 (microprice − mid)/mid × 1e4；输入为 asterdex 深度
    快照的 bids/asks 列表（[price, qty] 对，price 在前）。空/坏输入 ⇒ 0.0（不干预）。
    """
    if not bids or not asks:
        return 0.0
    try:
        b_px, b_q = float(bids[0][0]), float(bids[0][1])
        a_px, a_q = float(asks[0][0]), float(asks[0][1])
    except (IndexError, TypeError, ValueError):
        return 0.0
    if b_px <= 0 or a_px <= b_px or (b_q + a_q) <= 0:
        return 0.0
    mid = (b_px + a_px) / 2.0
    mp = (b_px * a_q + a_px * b_q) / (b_q + a_q)
    return (mp - mid) / mid * 1e4


def order_book_imbalance(bids: list, asks: list, levels: int = 5) -> float:
    """[h899 2026-10-07] 盘口订单簿失衡 OBI ∈ [−1,1](趋势概率模型的王牌特征)。

    OBI = (Σbid_qty − Σask_qty) / (Σbid_qty + Σask_qty),取前 `levels` 档。
    正 = 买方挂单厚(支撑强,短期偏涨);负 = 卖方厚。空/坏输入 ⇒ 0.0(中性)。
    """
    try:
        bq = sum(float(b[1]) for b in list(bids)[: max(1, int(levels))])
        aq = sum(float(a[1]) for a in list(asks)[: max(1, int(levels))])
    except (IndexError, TypeError, ValueError):
        return 0.0
    if bq + aq <= 0:
        return 0.0
    return (bq - aq) / (bq + aq)


def hold_window_ok(
    *,
    opened_ts: float,
    now_ts: float,
    min_hold_sec: float = MIN_HOLD_SEC_DEFAULT,
    max_hold_sec: float = MAX_HOLD_SEC_DEFAULT,
) -> Tuple[bool, str]:
    """[F258] 判断当前是否落在允许**强制出库**的时长窗口内。

    返回 `(是否允许, 原因)`。原因串直接用于 `dec.skip`，便于事后归因：
        `hold_too_young`  —— 未到下限，**不得**主动平仓（避免白付出场成本）
        `hold_too_old`    —— 已过上限，**必须**平仓（这是上界的语义）
        `""`              —— 窗口内，允许

    边界语义（刻意写成"含端点"）：
        · `age < min_hold_sec` ⇒ 拒绝
        · `age > max_hold_sec` ⇒ 拒绝（但调用方应据此**强制**平仓，见 runner）
        · 其余 ⇒ 允许

    `min_hold_sec <= 0` 或 `max_hold_sec <= 0` ⇒ 该侧闸门关闭（与旧行为逐字一致，
    可一键回退）。**两个都关 = 完全不限时长。**

    `opened_ts <= 0`（无持仓或未知建仓时刻）⇒ 视为窗口内（fail-open）：
    宁可放行也不要因为缺数据把库存永久卡死。
    """
    if opened_ts is None or opened_ts <= 0:
        return True, ""
    age = float(now_ts) - float(opened_ts)
    if min_hold_sec and min_hold_sec > 0 and age < float(min_hold_sec):
        return False, "hold_too_young"
    if max_hold_sec and max_hold_sec > 0 and age > float(max_hold_sec):
        return False, "hold_too_old"
    return True, ""


def should_stop_loss(qty: float, avg_mid: float, mid: float,
                     stop_loss_bp: float) -> bool:
    """浮亏超过阈值 → 立即平仓（0/负值表示关闭）。

    为什么需要：实测超时平仓平均 −26.7bp/笔（持有 15 分钟等不到对手盘），
    而挂单成交只有 +1.77bp。把尾部截断比「等对手盘」更划算。
    """
    if stop_loss_bp is None or stop_loss_bp <= 0:
        return False
    return unrealized_bp(qty, avg_mid, mid) < -abs(float(stop_loss_bp))


# ═══════════════════════ [F278] 步长/最小名义合规 ═══════════════════════
# 现场（A 路线调研 + 本人 exchangeInfo 实测复核）：Aster BTCUSDT stepSize=0.001 BTC
# ≈ **$75.83**，而本车道单腿 = 权益 × compound_ratio = $300 × 0.1 = **$30**
# ⇒ `$30/75800 = 0.000396 BTC` **不是 stepSize 的整数倍**（只能取 0 或 0.001），
# 实盘会被拒单 ✗。纸面车道若照常记录这类成交，等于把**现实中不存在的订单**计入
# 盈亏（"参数无逻辑错误"要求纸面与实盘同物理约束）。
# 实测值（fapi.asterdex.com/fapi/v1/exchangeInfo，2026-09-16）：
SYMBOL_STEP: Dict[str, Dict[str, float]] = {
    "BTC":  {"step": 0.001, "min_qty": 0.001, "min_notional": 5.0},
    "ETH":  {"step": 0.001, "min_qty": 0.001, "min_notional": 5.0},
    "BNB":  {"step": 0.01,  "min_qty": 0.01,  "min_notional": 5.0},
    "SOL":  {"step": 0.01,  "min_qty": 0.01,  "min_notional": 5.0},
    "XRP":  {"step": 0.1,   "min_qty": 0.1,   "min_notional": 5.0},
    "DOGE": {"step": 1.0,   "min_qty": 1.0,   "min_notional": 5.0},
    # [F291 2026-09-16] 宽价差候选（实测 exchangeInfo，$30 腿全部可下单 ✓）：
    # F290 普查里它们的中位价差是 ETH 的 70~130 倍（ADA 5.15bp / UNI 4.73 / AVAX 4.11 /
    # LINK 2.76 vs ETH 0.04bp）⇒ 必须进表，否则 F278 闸对它们 fail-open，
    # 将来换宇宙时会漏掉步长检查 ✗。
    "ADA":  {"step": 1.0,   "min_qty": 1.0,   "min_notional": 5.0},
    "UNI":  {"step": 1.0,   "min_qty": 1.0,   "min_notional": 5.0},
    "AVAX": {"step": 1.0,   "min_qty": 1.0,   "min_notional": 5.0},
    "LINK": {"step": 0.01,  "min_qty": 0.01,  "min_notional": 5.0},
    # [F300 2026-09-16] 事件研究选中的新宇宙（实测 exchangeInfo 复核，$30 腿全部可下单 ✓）。
    # 入库理由：缺失 ⇒ F278 闸 fail-open（等于没有步长保护），而这三只正是
    # 事件研究里漂移中性化边际为正、且点差 ≥1bp 的标的（ZEC 2.53bp / ASTER 1.47bp /
    # TAO 3.22bp）⇒ 会被放进宇宙，必须先把步长补上。
    # 注意 BTC 也在候选里，但其 min leg = 0.001 × $75,818 = **$75.82 > $30 单腿预算**
    # ⇒ BTC **不可用**（不是参数问题，是合约规格问题），故本次未纳入。
    "ZEC":   {"step": 0.001, "min_qty": 0.001, "min_notional": 5.0},
    "ASTER": {"step": 0.01,  "min_qty": 0.01,  "min_notional": 5.0},
    "TAO":   {"step": 0.001, "min_qty": 0.001, "min_notional": 5.0},
    # [F344 2026-09-22] 反转框架候选（实测 exchangeInfo 复核 2026-09-23）：
    # P1 选币改造要把 ASTER（反转最弱 +2.51bp）换成 PENDLE（反转最强 −0.1357 corr）。
    # 缺失 ⇒ `leg_qty_compliant` 对它们 fail-open（纸面会记"实盘必被拒"的成交，F278 的坑）。
    # 一并把验证 3（H262 反转 corr 排序）里的强反转候选都补上，为未来换币做准备。
    # 注意 XMR step=0.001、价格 ~150 ⇒ 单腿 $250/150≈1.7 枚，合规 ✓。
    "PENDLE": {"step": 1.0,   "min_qty": 1.0,   "min_notional": 5.0},
    "SUI":    {"step": 0.1,   "min_qty": 0.1,   "min_notional": 5.0},
    "XLM":    {"step": 1.0,   "min_qty": 1.0,   "min_notional": 5.0},
    "XMR":    {"step": 0.001, "min_qty": 0.001, "min_notional": 5.0},
    "AAVE":   {"step": 0.1,   "min_qty": 0.1,   "min_notional": 5.0},
    "SEI":    {"step": 1.0,   "min_qty": 1.0,   "min_notional": 5.0},
}


def leg_qty_compliant(symbol: str, leg_notional: float, px: float) -> Tuple[bool, float, str]:
    """按交易所步长/最小名义检查腿量是否**可下单**。

    返回 (是否合规, 向下取整后的合规数量, 原因)。表外币种 ⇒ 不约束（fail-open，
    避免因缺表把车道整死；表内币种严格执行）。
      · 数量 = floor(leg_notional / px / step) × step
      · 数量 < min_qty 或 数量×px < min_notional ⇒ 不合规（交易所会拒单）
    """
    meta = SYMBOL_STEP.get(str(symbol or "").upper())
    if not meta or px <= 0 or leg_notional <= 0:
        return True, leg_notional / px if px > 0 else 0.0, "no_table"
    step = float(meta["step"])
    raw = float(leg_notional) / float(px)
    qty = math.floor(raw / step) * step
    if qty < float(meta["min_qty"]) - 1e-12:
        return False, qty, f"below_step(需≥{meta['min_qty']} {symbol} ≈ ${meta['min_qty']*px:,.2f})"
    if qty * float(px) < float(meta["min_notional"]) - 1e-9:
        return False, qty, f"below_min_notional({qty*px:.2f}<{meta['min_notional']})"
    return True, qty, ""


def realized_vol_bp(mid_hist: List[float], window: int = 20) -> float:
    """已实现波动（bp）：近 `window` 期 1 步中价收益率的标准差。

    与 `sigma_norm_from_ranges`（价差宽度）不同——它衡量**价格真正动了多少**，
    而这正是做市逆选择成本的来源。
    """
    if not mid_hist or len(mid_hist) < 3:
        return 0.0
    n = max(3, min(int(window or 20) + 1, len(mid_hist)))
    xs = [float(x) for x in mid_hist[-n:]]
    rets = [(xs[i] - xs[i - 1]) / xs[i - 1] * 1e4
            for i in range(1, len(xs)) if xs[i - 1] > 0]
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return var ** 0.5


def vol_regime_blocked(mid_hist: List[float], vol_baseline_bp: float,
                       vol_pause_mult: float, window: int = 20) -> Tuple[bool, float]:
    """波动状态闸门：返回 (是否暂停, 当前已实现波动bp)。

    `vol_baseline_bp` 由运行态在窗口填满时锁定（与价差基准同思路），
    因此不依赖任何跨时段的价格水平。
    """
    cur = realized_vol_bp(mid_hist, window)
    if not vol_pause_mult or vol_pause_mult <= 0 or vol_baseline_bp <= 0:
        return False, cur
    return cur > vol_baseline_bp * (1.0 + float(vol_pause_mult)), cur


def trend_move_bp(mid_hist: List[float], lookback: int = 20) -> float:
    """近 `lookback` 期中价净移动（bp，正=上涨）。历史不足时返回 0。"""
    if not mid_hist or len(mid_hist) < 2:
        return 0.0
    n = max(2, min(int(lookback or 20), len(mid_hist)))
    a = float(mid_hist[-n])
    b = float(mid_hist[-1])
    if a <= 0:
        return 0.0
    return (b - a) / a * 1e4


# ═══════════════ [F342 2026-09-22] 突发事件检测（暴涨/暴跌） ═══════════════
#
# 用户要求建立「突发事件机制」（暴涨/暴跌/断网）。
#
# ── 现状缺口（全部已核实）────────────────────────────────────────────────
#   · `trend_pause_bp=15` 用 `trend_move_bp(mid_hist, 20)`：20 期 × 实测 tick 周期
#     18~20s ≈ **6.7 分钟**。而暴涨暴跌发生在**几秒到几十秒**内。
#   · `vol_pause_sigma` 同样建在 20 期窗口上 ⇒ 同样 6.7 分钟。
#   · `check_data_freshness` 的 `MAX_DATA_AGE_SEC=180` ⇒ 断网后要 **3 分钟**才判陈旧。
#   · 现有闸门**只封加仓侧**，不撤已有挂单、不处理已有持仓。
#   ⇒ 突发事件里引擎是"闭着眼睛继续挂单"。
#
# ── 为什么本检测器**不预测方向**（这是设计核心，不是省略）───────────────
# 实测（H244，24 小时逐秒 tick，去重叠 + 减基线）：
#   闪动事件（10s 内 ≥50bp）共 10 个独立事件，事件后 60s 的 fwd
#   **从 −47bp 到 +43bp 全谱都有，中位仅 +2.5bp** ⇒ 方向**不可预测**。
# 本会话已有 6 次"找方向信号"的尝试全部跨窗口翻转（OFI / 成交规模 /
# 成交笔数 / 阈值扫描 / 比值结构性 / 名义-盈亏）。
# ⇒ **不重复第 7 次**。检测器只做两件有确定性收益的事：
#     ① 在急动中**停止加仓**——因为"在急动里挂单必被逆向选择"是**已知**的
#        （实测 maker 腿 `price_bp = −1.18bp`，而 `spread_bp` 只有 +0.19bp）；
#     ② 让持仓走**宽挂 maker** 出库，而不是 taker 砸单
#        （taker = 4bp fee + 过价，且正好付在最宽的点差上）。
#
# ── 观测意义 ────────────────────────────────────────────────────────────
# 实测 24 小时里 **ASTER 独占 7/10 个闪动事件**（其余 3 个是 XRP/多币）。
# ⇒ 这个机制对 ASTER 的收益影响最大，上线后要**分币**看效果。

def sudden_move_bp(mid_hist: List[float], k: int = 1) -> float:
    """近 `k` 期的中价净移动（bp，正=上涨）。

    与 `trend_move_bp` 的区别：那个默认 20 期（6.7 分钟，太慢）；
    本函数是它的**快档**，`k=1` 表示"最近一期相对上一期"。
    """
    if not mid_hist or len(mid_hist) < 2:
        return 0.0
    n = max(2, min(int(k or 1) + 1, len(mid_hist)))
    a = float(mid_hist[-n])
    b = float(mid_hist[-1])
    if a <= 0:
        return 0.0
    return (b - a) / a * 1e4


def sudden_move_hit(mid_hist: List[float], thresh_bp: float,
                    k: int = 1) -> Tuple[bool, float]:
    """突发事件（暴涨/暴跌）检测：返回 `(是否命中, 移动 bp)`。

    `thresh_bp <= 0` ⇒ **关闭**（与旧行为逐字一致，可一键回退）。

    ⚠️ 语义边界（必须写清，否则会被误用）：
      · 它测的是**价格移动的幅度**，不是方向 ⇒ 命中后**不要**据此押方向；
      · 它**不区分**"真行情"与"单币插针"——实测前者延续、后者回摆
        （H244：三币同时急动 fwd +2.3~+2.7；单币孤立急动 fwd −40~+30）
        ⇒ 命中后的**统一动作**必须是"停加仓 + 让持仓走 maker"，
        **两种情况都安全**（真行情里不追高、插针里不在极值点卖出）。
    """
    mv = sudden_move_bp(mid_hist, k)
    if not thresh_bp or float(thresh_bp) <= 0:
        return False, mv
    return abs(mv) >= abs(float(thresh_bp)), mv


def trend_move_bp_legacy(mid_hist: List[float], lookback: int = 20) -> float:
    """[兼容] 旧名保留：与 `trend_move_bp` 逐字相同（防外部引用断裂）。"""
    return trend_move_bp(mid_hist, lookback)



def adds_blocked_by_trend(mid_hist: List[float], thresh_bp: float,
                          lookback: int = 20) -> bool:
    """|近 lookback 期净移动| ≥ 阈值 ⇒ 停止新建仓。阈值 ≤0 时恒为 False。"""
    if not thresh_bp or float(thresh_bp) <= 0:
        return False
    return abs(trend_move_bp(mid_hist, lookback)) >= float(thresh_bp)


def symbol_entry_blocked(symbol: str, raw: object) -> bool:
    """该币是否在「只减仓、不开新仓」名单里。名单空 ⇒ False。"""
    if not raw:
        return False
    if isinstance(raw, str):
        items = [raw]
    elif isinstance(raw, (list, tuple, set)):
        items = list(raw)
    elif isinstance(raw, dict):
        items = [k for k, v in raw.items() if v]
    else:
        return False

    def _bare(s: str) -> str:
        u = str(s or "").upper().split("-")[0].split("/")[0].split(":")[0]
        for suf in ("USDT", "USDC", "USD"):
            if u.endswith(suf) and len(u) > len(suf):
                return u[: -len(suf)]
        return u

    want = _bare(symbol)
    if not want:
        return False
    return any(_bare(x) == want for x in items)


def trend_blocked_side(mid_hist: List[float], trend_pause_bp: float,
                       lookback: int = 20, mode: float = 0.0) -> str:
    """趋势过强时禁止的**加仓侧**,返回 "buy"/"sell"/"both"/""。

    历史:F204 曾翻成"禁顺势侧"(2915 笔:顺势 −4.05/逆势 +2.11),h324 三重复测
    翻回"禁逆势侧"。**h697(2026-10-01)当日 1304 腿再次证实 F204 方向**
    (顺势 −1.55 t=−7.8 / 逆势 +1.15 t=+5.7),并进一步给出不对称结构:
    跌势 fade 买赢(+0.87~+1.98)、涨势两边都亏(顺势买 −1.19 / fade 卖 −1.73)。
    ⇒ 新增 mode:0=h324 旧行为;2=h697(跌禁卖放买、涨禁双边)。
    按新规矩上线后 1~2h 快判,数据反转即回滚 mode=0。
    """
    if not trend_pause_bp or trend_pause_bp <= 0:
        return ""
    mv = trend_move_bp(mid_hist, lookback)
    # [h697 2026-10-01] mode 开关:当日 1304 腿实测(scripts/h697_winning_legs.py)
    #   顺势 −1.55bp(t=−7.8) vs 逆势 +1.15bp(t=+5.7)⇒ fade 才是盈利方向;
    #   不对称:强涨后入场 −1.19bp、强跌后 +0.18bp、15 分钟大跌后 +1.98bp
    #   ⇒ 跌势放 fade 买、涨势双边全封(顺势买与 fade 卖双亏)。
    #   0=h324 旧行为(跌禁买、涨禁卖);2=h697(推荐)。
    if mv <= -abs(float(trend_pause_bp)):
        return "sell" if float(mode) >= 2.0 else "buy"
    if mv >= abs(float(trend_pause_bp)):
        return "both" if float(mode) >= 2.0 else "sell"
    return ""


def check_side_allowed(
    *,
    symbol: str,
    side: str,
    book: InventoryBook,
    marks: Dict[str, float],
    equity: float,
    add_notional: float = 0.0,
    limits: LaneRiskLimits = LaneRiskLimits(),
    now_ts: float = 0.0,
    sigma_norm: float = 0.0,
    pending_up_usd: float = 0.0,
    pending_down_usd: float = 0.0,
    pending_gross_usd: float = 0.0,
) -> Tuple[bool, str]:
    """单侧报价许可（做市必需：库存到顶后仍要能挂减仓腿）。

    `check_lane_limits` 是「整车道是否继续」的粗闸门；本函数是「这一侧这一腿能不能挂」
    的细闸门。区别在于：
      - 减仓方向（与现有库存反向）永远允许——否则库存一旦到顶就永久卡死，
        只能等对手腿偶然成交（F59 首轮回放 30 天仅 10 笔成交即此原因）；
      - 加仓方向才受单币/净敞口约束，且按**加入后**的敞口判断。

    [F94 2026-09-14] `pending_up_usd` / `pending_down_usd` = **已挂在场的同向腿名义**
    （其余币在当前 tick 仍有效的挂单）。此前闸门只看「已成交持仓 + 本腿」，
    完全不计在挂单 ⇒ 挂单在下一 tick 判定成交时不再过闸，5 个币同向同时在挂就会
    在同一个成交桶里一起成交：回放实测 |净敞口| 峰值 **$2367 = 上限的 7.9 倍**、
    48.9% 的快照超上限；实盘实测 $1021（3.4 倍）。风险上限形同虚设。
    这里按**最坏情形**预留：买单成交会把净敞口推高 `pending_up`，卖单推低
    `pending_down`（只预留同向；对手向成交只会减小该方向敞口）。
    """
    if equity <= 0:
        return False, "equity<=0"
    # [F82 2026-09-14] vol_pause_sigma ≤ 0 = 显式禁用这道 sigma 闸。
    # 现场：崩盘后实盘 sigma=1.6（30 天旧基准 1.14 虚高放大）> 1.5 ⇒ 双侧被
    # 封数小时 0 成交，而回放用窗口自适应基准（sigma≈0.5）不受影响——
    # 同窗口 212 笔 vs 实盘 4 笔的第二根因。进攻型配置显式置 0。
    if limits.vol_pause_sigma > 0 and sigma_norm > limits.vol_pause_sigma:
        return False, f"vol_pause(sigma={sigma_norm:.2f})"

    q = book.qty(symbol)
    signed = 1.0 if str(side or "").lower() in ("buy", "long", "b") else -1.0
    if abs(q) > 1e-12 and q * signed < 0:
        return True, "reduce"

    mark = float(marks.get(symbol) or 0.0)
    add_qty = (add_notional / mark) if mark > 0 else 0.0
    new_one_side = abs(q + signed * add_qty) * mark
    _ratio = symbol_lookup(getattr(limits, "per_symbol_max_notional_ratio", None),
                           symbol, limits.max_net_directional_ratio)
    if new_one_side > equity * _ratio:
        return False, f"symbol_exposure({new_one_side:.0f})"
    # [F94b 2026-09-14] **总敞口上限（严格可约束的那个量）**。
    # 为什么必须有：净敞口在「对冲腿被平掉」时会**变大**（例如 3 空 1 多净 −$900，
    # 把那条多单平掉后净变 −$1286）——所以任何**下单侧**约束都无法严格界定净敞口。
    # 只有总敞口 Σ|仓位| 不会被平仓推高（平仓只会减小它），因此它是唯一能严格
    # 兜住真实风险的口径。实测：配置净上限 $900 时真实净敞口峰值 $2569（2.85×）。
    if limits.max_gross_notional_ratio > 0:
        _gross = sum(abs(book.notional(s, marks.get(s, 0.0) or 0.0))
                     for s in set(list(book.positions) + [symbol]))
        if _gross + max(0.0, pending_gross_usd) + add_notional > \
                equity * limits.max_gross_notional_ratio:
            return False, f"gross_exposure({_gross + pending_gross_usd + add_notional:.0f})"
    # [F94] 净敞口按「在挂同向腿全部成交」的最坏情形判定（见 docstring）
    _net = book.net_notional(marks)
    _worst = (_net + max(0.0, pending_up_usd) + add_notional if signed > 0
              else _net - max(0.0, pending_down_usd) - add_notional)
    if abs(_worst) > equity * limits.max_net_exposure_ratio:
        return False, f"net_exposure({_worst:.0f})"
    return True, ""


def edge_metric_from_ledger(
    *,
    spread_bp_sum: float,
    fee_bp_sum: float,
    price_bp_sum: float,
    funding_bp_sum: float,
    slippage_bp_sum: float,
    n: int,
    folds: Optional[List[Dict[str, float]]] = None,
    notional_sum: float = 0.0,
    net_usd_sum: Optional[float] = None,
) -> Dict[str, float]:
    """把六维累计值折成每笔净期望（前端 EdgeBadge / 晋升判定输入）。

    [h621 2026-09-29] **等权 bp/腿对决策是误导**：它把 $0.26 的碎腿与 $90 的整腿
    同等对待（`/fills` 端点早已承认"等权平均 bp 没有金融含义"，但本函数——晋升
    判定的输入——一直是等权）。本轮取证（A1/A2）显示**边际随规模单调恶化**
    （5/5 币小半更优）⇒ 等权口径会系统性**高估**策略边际（大腿的亏损被碎腿稀释）。

    新增可选参数：`notional_sum`（Σ逐笔名义）+ `net_usd_sum`（Σ net_bp/1e4×notional，
    **必须逐行折算后求和**，绝不能 Σbp×Σnotional——`/fills` 的 71~103 倍虚报教训）。
    两者齐备 ⇒ 额外返回 `net_bp_w`（名义加权净 bp，唯一可跨行比较的口径）与
    `notional_sum`。缺省不传 ⇒ 输出与旧版**逐字一致**（旧行为 ✓）。
    """
    k = max(1, int(n))
    out = {
        "gross_bp": round((spread_bp_sum + funding_bp_sum) / k, 4),
        "cost_bp": round(abs(fee_bp_sum + slippage_bp_sum) / k, 4),
        "net_bp": round((spread_bp_sum + funding_bp_sum + price_bp_sum
                         + fee_bp_sum + slippage_bp_sum) / k, 4),
        "n": int(n),
        "folds": folds or [],
    }
    if float(notional_sum or 0.0) > 0 and net_usd_sum is not None:
        out["net_bp_w"] = round(float(net_usd_sum) / float(notional_sum) * 1e4, 4)
        out["notional_sum"] = round(float(notional_sum), 2)
    return out
