# -*- coding: utf-8 -*-
"""[F304 2026-09-16] L2 carry 的**测量模块**（不在运行路径上；单所前提下不成立）。

━━━ 结论先行：单所无法做 carry ⇒ 本模块不注册、不 tick ━━━
carry 的机制**必然是跨所**：两腿分处两个场所，收益 = Σ(r_A − r_B)。
用户已明确「现在只做单所」。单所 Aster 上的三种 carry 变体逐条核查后都不成立：
  ① 现货−永续基差 —— 需现货通道，本项目没有（只有永续）；
  ② 只吃资金费的单腿 —— 不 delta 中性，那是方向赌，不是 carry；
  ③ 永续间日历价差 —— Aster 无同标的第二合约。
⇒ `lane_registry.carry_basis` 保持 `stopped`，本模块**不被任何 ticker / runner 调用**。
   它保留为一份**被证伪的测量记录**，避免后人再从那份自相矛盾的回测数字里读出希望。

━━━ 为什么重写旧 edge_json ━━━
`carry_basis.edge_json` 存着一份 `hedged_backtest`（gross_bp 5.15、cost_bp 28.67、
net_bp +33.81）——三个数**自相矛盾**（gross − cost 应为 −23.5）。它按"现货腿 taker
5bp + 永续腿 taker 4bp"建模，而本项目没有现货通道，Aster 永续 maker 实测 0bp。

━━━ 跨所机制（若将来做了跨所通道，本模块可直接复用）━━━
两条永续腿做 delta 中性：空 Aster + 多 Binance（当 Aster 付得更多时）。
持有期内资金费收 = Σ(结算时刻) (r_aster − r_binance)。
`perp_funding` 存的是**预测资金费**（premiumIndex），每 ~5 分钟轮询、窗口内持续漂移；
Binance USD-M 每 8h 结算、锚定 00/08/16 UTC（990 次结算中 981 次落在整点）。
因此按 8h 桶（epoch_ms // 28_800_000）取**桶内最后一条**作为该期结算值，
两边同桶相减 ⇒ 得到每期差价，无需精确知道结算时刻。

━━━ 跨所实测（决定了"即使跨所也不值得做"）━━━
30 个 Aster 可交易标的（Aster WS 只覆盖 32 个标的，差价大的冷门 alt 无盘口、无法成交）：
    · 每期差价 mean 多为**正**（Aster 流动性小 ⇒ 结构性付更高资金费）
    · 日收益 0.24 ~ 3.05 bp/天
    · realistic 摩擦（Aster maker 0 + Binance taker 4.5 + 滑点）下，7 天持有 **5/30** 标的能覆盖
    · 全腿 taker **0/30**
    · 绝对额：$60 配对名义 × 3.05 bp/天 ≈ **$0.009/天**（对比 mm 车道 $2.23/天）
⇒ 即使放开跨所，年化也只有 ~8–11%，在 $300 本金上不可能达成复利目标。
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

LANE_ID = "carry_basis"
WIN_MS = 28_800_000              # 8h 结算窗口
SETTLE_PER_DAY = 3.0             # 8h 结算 ⇒ 3 次/天

# ── 摩擦模型（永续双腿，本项目的真实成本结构）──
FRICTION_BP = {
    "aster_maker": 0.0,          # Aster 永续 maker 0%
    "aster_taker": 4.0,
    "binance_maker": 2.0,        # Binance USD-M base tier
    "binance_taker": 4.5,
    "aster_slip": 2.0,           # Aster 盘口较宽（新场所）
    "binance_slip": 0.5,
}


def round_trip_cost_bp(aster: str = "maker", binance: str = "taker") -> float:
    """一次开仓+平仓的总摩擦（两条腿、两个方向）。"""
    a = FRICTION_BP["aster_maker" if aster == "maker" else "aster_taker"]
    b = FRICTION_BP["binance_maker" if binance == "maker" else "binance_taker"]
    return (a + FRICTION_BP["aster_slip"] + b + FRICTION_BP["binance_slip"]) * 2.0


@dataclass
class CarryParams:
    """车道参数（写入 `lane_registry.meta.params`，与 mm 车道同构）。"""

    # 是否允许建仓。默认 False = 只测量。这是本车道的**总开关/回滚开关**。
    enabled: bool = False

    # 入场门槛：预期净收益必须 ≥ 此值（bp）
    min_edge_bp: float = 2.0

    # 持有期设定
    max_hold_days: float = 7.0
    min_hold_days: float = 0.5      # 至少要跨过 1 个结算才有意义

    # 持续性要求：最近 N 期里，符号与均值一致的比例（诊断口径）
    persist_windows: int = 6
    persist_min_frac: float = 0.5

    # 统计显著性（**真正的筛子**）：单期均值的单边置信下界必须超过盈亏平衡。
    # 动机：carry 差价噪声极大（ZEC 实测 mean 0.09 / sd 0.93），而持有期要横跨
    # 几十个结算期。只看均值符号等于把噪声当边际；要求下界超过盈亏平衡才是
    # "这个边际在统计上站得住"。
    require_significance: bool = True
    confidence_z: float = 1.645        # 单边 95%

    # 两条腿的执行方式（决定摩擦）
    aster_mode: str = "maker"
    binance_mode: str = "taker"

    # 场所尾部风险溢价（bp）：Aster 是新场所，需额外补偿清算/脱锚风险
    venue_risk_bp: float = 3.0

    # 数据新鲜度：最后一期距今超过此小时数 ⇒ 拒绝判定（fail-closed）
    max_stale_hours: float = 12.0

    # 单腿名义（受 F303 敞口护栏约束）
    leg_notional: float = 30.0

    def friction_bp(self) -> float:
        return round_trip_cost_bp(self.aster_mode, self.binance_mode)


@dataclass
class CarryWindow:
    """一个 8h 结算窗口的读数（纯数据，便于单测）。"""

    window: int
    rate_aster_bp: float
    rate_binance_bp: float

    @property
    def diff_bp(self) -> float:
        return self.rate_aster_bp - self.rate_binance_bp


@dataclass
class CarryMeasurement:
    """一次测量的完整结果。"""

    symbol: str
    windows: int
    mean_diff_bp: float
    abs_mean_diff_bp: float
    sd_diff_bp: float
    last_diff_bp: Optional[float]
    last_window_age_h: Optional[float]
    carry_bp_per_day: float
    friction_bp: float
    venue_risk_bp: float
    hold_days: float
    projected_gross_bp: float
    projected_net_bp: float
    persist_frac: float
    stale: bool
    decision: str          # "flat" | "would_enter"
    reason: str
    side: str              # "short_aster_long_binance" 等
    # 显著性诊断：均值的单边置信下界 vs 盈亏平衡的每期差价
    mean_lcb_bp: float = 0.0
    breakeven_per_settle_bp: float = 0.0
    significant: bool = False
    windows_detail: List[CarryWindow] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("windows_detail", None)
        return d


# ────────────────────────── 纯函数层（可单测，无 IO） ──────────────────────────

def bucket_settlements(
    obs: List[Tuple[int, float]],
) -> Dict[int, Tuple[int, float]]:
    """把 ~5min 的观测折叠成 {8h 窗口: (ts, rate)}。

    取**桶内最后一条**：窗口内预测值持续漂移，结算时生效的是最后那个值
    （BTC 实测窗口内漂移 ~0.25bp，对 bp 级判定可忽略但必须口径一致）。
    """
    out: Dict[int, Tuple[int, float]] = {}
    for ts, rate in obs:
        w = ts // WIN_MS
        cur = out.get(w)
        if cur is None or ts >= cur[0]:
            out[w] = (ts, rate)
    return out


def align_windows(
    aster: Dict[int, Tuple[int, float]],
    binance: Dict[int, Tuple[int, float]],
) -> List[CarryWindow]:
    """取两场所**共有**的窗口并相减（缺一边的窗口必须丢弃，否则是拿不同期相减）。"""
    out: List[CarryWindow] = []
    for w in sorted(set(aster) & set(binance)):
        out.append(CarryWindow(
            window=w,
            rate_aster_bp=aster[w][1] * 1e4,
            rate_binance_bp=binance[w][1] * 1e4,
        ))
    return out


def _mean(xs: List[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _sd(xs: List[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5


def decide(
    symbol: str,
    windows: List[CarryWindow],
    params: CarryParams,
    *,
    last_obs_ms: Optional[int] = None,
    now_ms: Optional[int] = None,
) -> CarryMeasurement:
    """核心判定（纯函数）。

    结构性方向：实测 Aster 相对 Binance 的资金费**长期偏高**（新场所、流动性小），
    所以方向固定为「空 Aster / 多 Binance」。若观测到均值为负（Aster 付得少），
    则方向翻转——这由数据决定，不写死。

    预期收益投影：`mean_diff × 3 × hold_days`。用**均值**而不是最优期，
    因为单期差价噪声极大（ZEC 实测 mean 0.09 / sd 0.93），用最优期会自我欺骗。
    """
    friction = params.friction_bp()
    if len(windows) < max(2, params.persist_windows):
        return CarryMeasurement(
            symbol=symbol, windows=len(windows), mean_diff_bp=0.0,
            abs_mean_diff_bp=0.0, sd_diff_bp=0.0, last_diff_bp=None,
            last_window_age_h=None, carry_bp_per_day=0.0, friction_bp=friction,
            venue_risk_bp=params.venue_risk_bp, hold_days=params.max_hold_days,
            projected_gross_bp=0.0, projected_net_bp=0.0, persist_frac=0.0,
            stale=False, decision="flat", reason="insufficient_windows",
            side="none",
        )

    diffs = [w.diff_bp for w in windows]
    mean = _mean(diffs)
    abs_mean = _mean([abs(d) for d in diffs])
    sd = _sd(diffs)
    last = windows[-1]

    # 方向按均值的符号定；收益按 |mean| 投影（做空付更多的那条腿）
    if mean >= 0:
        side = "short_aster_long_binance"
        signed = mean
    else:
        side = "long_aster_short_binance"
        signed = -mean

    carry_per_day = abs_mean * SETTLE_PER_DAY

    # 新鲜度（fail-closed：数据陈旧就不判定，避免拿停摆的数据放行）
    age_h: Optional[float] = None
    stale = False
    if last_obs_ms is not None and now_ms is not None:
        age_h = (now_ms - last_obs_ms) / 3_600_000.0
        stale = age_h > params.max_stale_hours

    # 持续性：最近 persist_windows 期里，符号与均值一致的比例
    recent = diffs[-params.persist_windows:]
    same_sign = sum(1 for d in recent if (d >= 0) == (mean >= 0))
    persist_frac = same_sign / len(recent) if recent else 0.0

    hold = min(max(params.min_hold_days, 0.0), params.max_hold_days)
    gross = signed * SETTLE_PER_DAY * hold
    net = gross - friction - params.venue_risk_bp

    # 盈亏平衡的**每期**差价：持有 hold 天共 hold×3 个结算期。
    settle_span = max(1.0, hold * SETTLE_PER_DAY)
    breakeven_per_settle = (friction + params.venue_risk_bp) / settle_span
    se = sd / (len(windows) ** 0.5)
    mean_lcb = signed - params.confidence_z * se
    significant = (not params.require_significance) or (
        mean_lcb > breakeven_per_settle)

    if stale:
        return CarryMeasurement(
            symbol=symbol, windows=len(windows), mean_diff_bp=mean,
            abs_mean_diff_bp=abs_mean, sd_diff_bp=sd, last_diff_bp=last.diff_bp,
            last_window_age_h=age_h, carry_bp_per_day=carry_per_day,
            friction_bp=friction, venue_risk_bp=params.venue_risk_bp,
            hold_days=hold, projected_gross_bp=gross, projected_net_bp=net,
            persist_frac=persist_frac, stale=True, decision="flat",
            reason="stale_data", side=side,
            mean_lcb_bp=mean_lcb, breakeven_per_settle_bp=breakeven_per_settle,
            significant=significant,
        )

    reasons = []
    if not params.enabled:
        reasons.append("lane_disabled")
    if net < params.min_edge_bp:
        reasons.append("edge_below_min")
    if persist_frac < params.persist_min_frac:
        reasons.append("not_persistent")
    if not significant:
        # 均值虽为正，但置信下界没过盈亏平衡 ⇒ 是在赌噪声，不是在赚边际
        reasons.append(
            "not_significant(LCB %.3f <= BE %.3f)" % (mean_lcb, breakeven_per_settle))

    if reasons:
        return CarryMeasurement(
            symbol=symbol, windows=len(windows), mean_diff_bp=mean,
            abs_mean_diff_bp=abs_mean, sd_diff_bp=sd, last_diff_bp=last.diff_bp,
            last_window_age_h=age_h, carry_bp_per_day=carry_per_day,
            friction_bp=friction, venue_risk_bp=params.venue_risk_bp,
            hold_days=hold, projected_gross_bp=gross, projected_net_bp=net,
            persist_frac=persist_frac, stale=False, decision="flat",
            reason="+".join(reasons), side=side,
            mean_lcb_bp=mean_lcb, breakeven_per_settle_bp=breakeven_per_settle,
            significant=significant,
        )

    return CarryMeasurement(
        symbol=symbol, windows=len(windows), mean_diff_bp=mean,
        abs_mean_diff_bp=abs_mean, sd_diff_bp=sd, last_diff_bp=last.diff_bp,
        last_window_age_h=age_h, carry_bp_per_day=carry_per_day,
        friction_bp=friction, venue_risk_bp=params.venue_risk_bp,
        hold_days=hold, projected_gross_bp=gross, projected_net_bp=net,
        persist_frac=persist_frac, stale=False, decision="would_enter",
        reason="edge_gt_min", side=side,
        mean_lcb_bp=mean_lcb, breakeven_per_settle_bp=breakeven_per_settle,
        significant=significant,
    )


def evaluate_edge(measurements: List[CarryMeasurement],
                  params: CarryParams) -> Dict[str, Any]:
    """把一次全宇宙测量折算成 `lane_registry.edge_json`（与晋升判定同口径）。

    `net_bp` 取**全宇宙名义加权**的预期净收益（未建仓时为投影值），
    `n` = 参与测量的窗口总数，`source='paper_shadow'`（可信来源，
    见 lane_registry.EDGE_SOURCES）。四折用窗口序列切分。
    """
    live = [m for m in measurements if m.windows > 0]
    if not live:
        return {}
    n_windows = sum(m.windows for m in live)
    # 名义加权：每个标的同等配置（leg_notional 相同）⇒ 简化为均值
    net = _mean([m.projected_net_bp for m in live])
    gross = _mean([m.projected_gross_bp for m in live])

    # 四折：按标的顺序等分，每折给 net 与 t（t = net / se）
    folds = []
    k = max(1, len(live) // 4)
    for i in range(4):
        chunk = live[i * k:(i + 1) * k] if i < 3 else live[3 * k:]
        if not chunk:
            continue
        xs = [m.projected_net_bp for m in chunk]
        se = (_sd(xs) / (len(xs) ** 0.5)) if len(xs) > 1 else 0.0
        folds.append({
            "fold": i, "n": len(chunk),
            "net_bp": round(_mean(xs), 4),
            "t": round(_mean(xs) / se, 3) if se > 0 else 0.0,
        })

    return {
        "source": "paper_shadow",
        "n": n_windows,
        "gross_bp": round(gross, 4),
        "cost_bp": round(_mean([m.friction_bp for m in live]), 4),
        "net_bp": round(net, 4),
        "folds": folds,
        "max_dd_pct": None,
        "fill_rate_ratio": None,
        "as_of": None,
        "note": ("carry 投影口径：Σ(8h 窗口差价) 减摩擦与场所风险溢价；"
                 "未建仓时为投影值，非已实现"),
    }


# ────────────────────────── IO 层 ──────────────────────────

def _load_observations(symbols: List[str], max_windows: int) -> Dict[str, Dict[str, List]]:
    """从 `alpha_market.perp_funding` 读两个场所的近期观测（跨库，用 MarketSessionLocal）。"""
    from sqlalchemy import text as _sa

    from backend.database.connection import MarketSessionLocal

    # 只取最近 max_windows 个 8h 窗口，避免全表扫描（表有 1600 万行）
    cutoff_ms = None
    out: Dict[str, Dict[str, List]] = {}
    with MarketSessionLocal() as db:
        row = db.execute(_sa(
            "SELECT MAX(timestamp) FROM perp_funding WHERE exchange IN"
            " ('asterdex','binance')")).scalar()
        if row is None:
            return {}
        cutoff_ms = int(row) - max_windows * WIN_MS
        rows = db.execute(_sa(
            "SELECT exchange, symbol, timestamp, funding_rate FROM perp_funding"
            " WHERE exchange IN ('asterdex','binance') AND symbol = ANY(:syms)"
            " AND timestamp >= :cut ORDER BY symbol, timestamp"
        ), {"syms": symbols, "cut": cutoff_ms}).mappings().all()

    for r in rows:
        sym = r["symbol"]
        d = out.setdefault(sym, {"asterdex": [], "binance": []})
        d[r["exchange"]].append((int(r["timestamp"]), float(r["funding_rate"])))
    return out


def measure_symbols(
    symbols: Optional[List[str]] = None,
    params: Optional[CarryParams] = None,
    *,
    max_windows: int = 60,
) -> List[CarryMeasurement]:
    """测量一组标的的 carry 机会并给出判定。符号用 `perp_funding` 口径（无 USDT 后缀）。"""
    p = params or CarryParams()
    import time as _t

    syms = [str(s).upper().replace("USDT", "") for s in (symbols or [])]
    if not syms:
        return []
    obs = _load_observations(syms, max_windows)
    now_ms = int(_t.time() * 1000)

    results: List[CarryMeasurement] = []
    for sym in syms:
        d = obs.get(sym)
        if not d:
            continue
        a = bucket_settlements(d["asterdex"])
        b = bucket_settlements(d["binance"])
        wins = align_windows(a, b)
        last_ms = None
        if wins:
            last_ms = max(a[wins[-1].window][0], b[wins[-1].window][0])
        results.append(decide(sym, wins, p, last_obs_ms=last_ms, now_ms=now_ms))
    return results


def tick(lane_id: str = LANE_ID) -> Dict[str, Any]:
    """**已停用**：单所前提下 carry 不成立，本函数不再被任何 ticker 调用。

    保留函数体是为了将来若真开了跨所通道，能直接接上；现在它在第一步就
    拒绝执行，避免有人误挂一个 ticker 上去把一条已知不成立的车道跑起来。
    """
    return {
        "ok": False,
        "reason": ("单所前提下 carry 不成立（需跨所两条腿）。本模块不在运行路径上；"
                   "若要重启请先实现跨所通道并改写此处的拒绝逻辑。"),
        "disabled": True,
    }


def report(lane_id: str = LANE_ID) -> Dict[str, Any]:
    """只读报告（离线诊断用，不写库；不被运行路径调用）。"""
    try:
        from backend.services import lane_registry as reg

        lane = reg.get_lane(lane_id) or {}
        meta = lane.get("meta") or {}
        stored = meta.get("params") or {}
        p = CarryParams(**{k: v for k, v in stored.items()
                           if k in CarryParams.__dataclass_fields__})
        ms = measure_symbols(meta.get("symbols") or [], p)
        ms_sorted = sorted(ms, key=lambda m: -m.projected_net_bp)
        return {
            "lane_id": lane_id,
            "status": lane.get("status"), "mode": lane.get("mode"),
            "params": asdict(p), "friction_bp": p.friction_bp(),
            "measurements": [m.to_dict() for m in ms_sorted],
            "would_enter": [m.symbol for m in ms if m.decision == "would_enter"],
            "health": lane.get("health"),
        }
    except Exception as e:
        return {"lane_id": lane_id, "error": f"{type(e).__name__}: {e}"}


if __name__ == "__main__":      # pragma: no cover - 手工诊断入口
    import sys
    print(json.dumps(report(sys.argv[1] if len(sys.argv) > 1 else LANE_ID),
                     ensure_ascii=False, indent=2, default=str))
