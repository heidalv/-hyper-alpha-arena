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

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ═══════════════════════ 报价 ═══════════════════════

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
    k_vol: float = 0.5            # 波动放大系数
    k_inv: float = float(os.getenv("MM_K_INV", "1.0"))            # 库存偏斜系数
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


@dataclass(frozen=True)
class Quote:
    symbol: str
    mid: float
    bid: float
    ask: float
    w_bid_bp: float
    w_ask_bp: float
    reason: str = ""


def compute_quote(
    *,
    symbol: str,
    mid: float,
    sigma_norm: float = 0.0,
    inv_ratio: float = 0.0,
    slow_range_bp: float = 0.0,
    params: QuoteParams = QuoteParams(),
) -> Optional[Quote]:
    """计算双边报价。

    Args:
        mid: 中间价（<=0 直接返回 None）
        sigma_norm: 波动归一值（近 20 期振幅 / 长期均值 - 1，>=0 表示比平时波动大）
        inv_ratio: 库存偏离度 ∈ [-1, 1]（正=多头库存，需偏向卖出）
        slow_range_bp: 近 240 期中价全幅（bp）——[F80] 冻结行情检测信号；
            >0 且 < frozen_max_move_bp 时挂宽切到 frozen_width_bp
    """
    if not mid or mid <= 0:
        return None
    # [F80] 冻结行情：全幅小于阈值 ⇒ 波动坍缩，窄挂宽捕获微小振荡
    # （w3 在冻结日唯一为正的实测档位；无冻结信号时行为与旧版逐字一致）
    if (params.frozen_width_bp is not None and float(params.frozen_width_bp) > 0
            and 0 < float(slow_range_bp) < float(params.frozen_max_move_bp)):
        base = float(params.frozen_width_bp)
    else:
        base = params.w_base_bp * (1.0 + params.k_vol * max(0.0, sigma_norm))
    # 库存偏斜：多头库存 → 买价挂更远、卖价挂更近（鼓励减仓）
    w_bid = base * (1.0 + params.k_inv * inv_ratio)
    w_ask = base * (1.0 - params.k_inv * inv_ratio)
    # 宽度下限分侧：加仓侧守 min_width_bp（贴盘口实测为负），减仓侧可贴到
    # min_width_reduce_bp——否则库存到顶只能等超时用 taker 平仓，成本高一个量级。
    bid_floor = params.min_width_reduce_bp if inv_ratio < 0 else params.min_width_bp
    ask_floor = params.min_width_reduce_bp if inv_ratio > 0 else params.min_width_bp
    w_bid = min(params.max_width_bp, max(bid_floor, w_bid))
    w_ask = min(params.max_width_bp, max(ask_floor, w_ask))
    return Quote(
        symbol=symbol, mid=float(mid),
        bid=float(mid) * (1.0 - w_bid / 1e4),
        ask=float(mid) * (1.0 + w_ask / 1e4),
        w_bid_bp=round(w_bid, 4), w_ask_bp=round(w_ask, 4),
        reason=f"base={base:.2f}bp sigma={sigma_norm:.2f} inv={inv_ratio:.2f}",
    )


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
            pos.qty, pos.avg_px, pos.avg_mid, pos.opened_ts = signed, fill_px, mid_px, now_ts
        elif pos.qty * signed > 0:
            # 同向加仓 → 更新均价与平均中价
            total = abs(pos.qty) + abs(signed)
            pos.avg_px = (pos.avg_px * abs(pos.qty) + fill_px * abs(signed)) / total
            pos.avg_mid = (pos.avg_mid * abs(pos.qty) + mid_px * abs(signed)) / total
            pos.qty += signed
        else:
            # 反向 → 平掉 min(|pos|, |signed|)
            close_qty = min(abs(pos.qty), abs(signed))
            direction = 1.0 if pos.qty > 0 else -1.0
            base_mid = pos.avg_mid or pos.avg_px or mid_px
            realized_price_usd = direction * (mid_px - base_mid) * close_qty
            pos.qty += signed
            if abs(pos.qty) < 1e-12:
                pos.qty, pos.avg_px, pos.avg_mid, pos.opened_ts = 0.0, 0.0, 0.0, 0.0
            elif pos.qty * direction < 0:
                # 反手：剩余部分以本次成交价/中价开新仓
                pos.avg_px, pos.avg_mid, pos.opened_ts = fill_px, mid_px, now_ts

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
        }

    # ── 汇总 ──
    def realized_total_usd(self) -> float:
        return self.realized_spread_usd + self.realized_price_usd + self.realized_fee_usd


# ═══════════════════════ 成交判定（与 F52 同口径） ═══════════════════════

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

    max_symbol_notional_ratio: float = 0.05     # 单币库存 ≤ 该币 10 档深度 ×5%
    max_net_exposure_ratio: float = 0.30        # 总净敞口 ≤ 权益 30%
    # [F94b 2026-09-14] 总敞口上限（Σ|仓位| / 权益）。0 = 关闭（旧行为逐字一致）。
    # **唯一能严格兜住真实风险的口径**：平仓会让对冲腿消失从而放大净敞口
    # （实测 3 空 1 多净 −$900 → 平掉多单后净 −$1286），却只会减小总敞口
    # ⇒ 下单侧约束对净敞口只能「尽力而为」，对总敞口是硬约束。
    max_gross_notional_ratio: float = 0.0
    max_net_directional_ratio: float = 0.10     # 单边方向敞口 ≤ 权益 10%
    max_one_side_seconds: float = float(os.getenv("MM_MAX_ONE_SIDE_SEC", "300"))  # 单边持仓上限
    vol_pause_sigma: float = 1.5                # 波动 > 1.5× → 暂停该币
    toxic_streak: int = 3                       # 连续 3 次逆选择 > 阈值 → 暂停
    toxic_bp: float = 15.0
    daily_loss_stop_pct: float = 1.0            # 日亏 > 权益 1% → 全停
    # ── [F71] 针对「超时平仓吃掉全部利润」的两道闸门 ──
    # 实测：挂单成交 +1.77bp/笔（与模型一致），但超时平仓 −26.7bp/笔、占 28.8%，
    # 净期望因此转负。这两项直接削掉尾部亏损。
    stop_loss_bp: float = float(os.getenv("MM_STOP_LOSS_BP", "0"))  # 浮亏 > 此值 → 立即平仓（0=关闭）
    trend_pause_bp: float = 0.0                 # 近 N 期中价单向移动 > 此值 → 禁止逆势侧（0=关闭）
    trend_lookback: int = 20
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
    # [F86] 流向择时平仓：持仓年龄 > hold × min_age_ratio 且 OFI **顺离场方向**
    # （多头遇买压=高价卖出、空头遇卖压=低价回补）⇒ 提前 taker 平仓，替代盲等超时。
    # 逻辑依据：平仓腿是最大成本项（实测均 -9.4bp、占 16-25%）；把平仓时点从
    # 「固定 900 秒」改为「流向顺风时」，理论上是执行时机的改进（最优执行文献）。
    ofi_flatten_threshold: float = 0.0         # 0=关闭；0.5=|OFI|>0.5 视为顺风
    ofi_flatten_min_age_ratio: float = 0.5     # 持仓超过 hold 的该比例后才考虑择时平仓
    # ── [F89a 2026-09-14] 陈旧挂单保护 ──
    # 现场事故：币种重新加入宇宙时，运行态里残留着**数天前的挂单**（quote_bid/ask），
    # 首个 tick 把「当前区间成交」判成这些旧价位的成交——4 笔幻影成交、净敞口冲到
    # -$739（上限 $300），且账本用旧 ref_mid 记成 +8~+12bp 假盈利。
    # 保护：挂单年龄 > max_quote_age_sec（默认 90s = 6 个 tick）⇒ 直接丢弃挂单、
    # 不做成交判定（与「数据陈旧撤单」同源，但覆盖「状态陈旧」场景）。
    max_quote_age_sec: float = 90.0


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
    if sigma_norm > limits.vol_pause_sigma:
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
    if one_side > equity * limits.max_net_directional_ratio:
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


def should_stop_loss(qty: float, avg_mid: float, mid: float,
                     stop_loss_bp: float) -> bool:
    """浮亏超过阈值 → 立即平仓（0/负值表示关闭）。

    为什么需要：实测超时平仓平均 −26.7bp/笔（持有 15 分钟等不到对手盘），
    而挂单成交只有 +1.77bp。把尾部截断比「等对手盘」更划算。
    """
    if stop_loss_bp is None or stop_loss_bp <= 0:
        return False
    return unrealized_bp(qty, avg_mid, mid) < -abs(float(stop_loss_bp))


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


def trend_blocked_side(mid_hist: List[float], trend_pause_bp: float,
                       lookback: int = 20) -> str:
    """趋势过强时禁止**逆势侧**挂单，返回 "buy"/"sell"/""。

    逻辑：中价单边下跌时，买单会持续被逆选择（买了继续跌）→ 禁买；
    单边上涨时，卖单被逆选择（卖了继续涨）→ 禁卖。
    实测亏损平仓全部来自「下跌中买入后被迫平仓」，所以这道闸门正对着病灶。
    """
    if not trend_pause_bp or trend_pause_bp <= 0:
        return ""
    mv = trend_move_bp(mid_hist, lookback)
    if mv <= -abs(float(trend_pause_bp)):
        return "buy"
    if mv >= abs(float(trend_pause_bp)):
        return "sell"
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
    if new_one_side > equity * limits.max_net_directional_ratio:
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
) -> Dict[str, float]:
    """把六维累计值折成每笔净期望（前端 EdgeBadge / 晋升判定输入）。"""
    k = max(1, int(n))
    return {
        "gross_bp": round((spread_bp_sum + funding_bp_sum) / k, 4),
        "cost_bp": round(abs(fee_bp_sum + slippage_bp_sum) / k, 4),
        "net_bp": round((spread_bp_sum + funding_bp_sum + price_bp_sum
                         + fee_bp_sum + slippage_bp_sum) / k, 4),
        "n": int(n),
        "folds": folds or [],
    }
