# -*- coding: utf-8 -*-
"""PositionConstruction — 仓位构造**单一权威**（v3 方向 1，p1-trend-engine，2026-09-03）。

背景：仓库里并存 8 条定仓轨（AI 置信度百分比表 / ATR risk-per-trade / 动态杠杆 5–20x / Kelly 上限 /
PositionSizingAgent / 短线动态名义 / 中长线 ATR 乘子 / 编排器建议），权威不清。对标 Lean 的单一
PortfolioConstruction，本模块把"最终名义与杠杆"收敛到一处：

    名义 = 权益 × 目标波动 / 该币实现波动          （vol-target；趋势车道 35%）
    单币名义 ≤ 35% 权益
    单笔风险 = 名义 × 初始止损距离 ≤ risk_per_trade × 权益（默认 0.75%；趋势车道 1.25%，见 trend_core 注释）
    杠杆 = 名义 / 保证金，由波动反推且 ≤ 3x（RiskEngine 另有黑天鹅 1x 压制）
    同相关簇名义 ≤ 50% 权益；车道总名义 ≤ gross_cap × 权益
    置信度只做 0.5–1.0 的缩放，不再决定仓位

两个入口：
  construct(...)   从零构造（E1 日任务等"新式"车道直接调用）→ PositionPlan
  clamp(...)       对上游任何一条旧轨给出的 (notional, leverage) 做硬帽夹紧（paper_engine.place_order 收口处调用，
                   所有车道必经，旧轨从此只是"提议"）→ ClampResult

开关：POSITION_CONSTRUCTION_ENFORCE（默认 true）；PC_MAX_LEVERAGE / PC_MAX_WEIGHT_PER_SYMBOL / PC_RISK_PER_TRADE_PCT /
PC_CLUSTER_CAP / PC_GROSS_CAP 全局默认，`PC_<PARAM>_<LANE>`（LANE=SHORT/MID/LONG/RESEARCH/ARB）按车道覆盖。
纯函数 + 只读 DB（读同账户在手名义）；不下单、不改状态。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

LANES = ("short", "mid", "long", "research", "arb")

# 相关簇（静态；Phase 2 Anomaly/Timing Agent 可改为 60 日相关矩阵驱动）
DEFAULT_CLUSTERS: Dict[str, Sequence[str]] = {
    "majors": ("BTC", "ETH"),
    "l1_beta": ("SOL", "BNB", "AVAX", "ADA", "SUI", "APT", "NEAR", "TON"),
    "payments": ("XRP", "XLM", "LTC", "BCH"),
    "meme": ("DOGE", "SHIB", "PEPE", "WIF", "BONK", "FLOKI"),
    "defi": ("LINK", "UNI", "AAVE", "MKR", "CRV", "LDO"),
}


def _env_f(key: str, default: float) -> float:
    try:
        v = os.getenv(key, "")
        return float(v) if str(v).strip() != "" else default
    except Exception:
        return default


def _lane_param(param: str, lane: Optional[str], default: float) -> float:
    """PC_<PARAM>_<LANE> → PC_<PARAM> → default。"""
    base = _env_f(f"PC_{param}", default)
    if lane:
        return _env_f(f"PC_{param}_{str(lane).upper()}", base)
    return base


def enforce_enabled() -> bool:
    return str(os.getenv("POSITION_CONSTRUCTION_ENFORCE", "true")).strip().lower() in ("1", "true", "yes", "on")


def normalize_lane(tier: Optional[str], nature: Optional[str] = None) -> str:
    t = (tier or "").strip().lower()
    n = (nature or "").strip().lower()
    if n == "arbitrage" or t in ("arb", "arbitrage"):
        return "arb"
    if t in LANES:
        return t
    # tier 别名（各旧轨叫法不一）
    if t in ("scalp", "intraday", "shortterm", "short_term"):
        return "short"
    if t in ("swing", "midterm", "mid_term", "medium"):
        return "mid"
    if t in ("trend", "longterm", "long_term", "position"):
        return "long"
    if n in ("scalp", "intraday"):
        return "short"
    if n == "swing":
        return "mid"
    if n in ("trend_follow", "position"):
        return "long"
    if n in ("research", "pair_research"):
        return "research"
    return "mid"


@dataclass
class LaneLimits:
    lane: str
    vol_target: float            # 目标年化波动（vol-target 定仓用）
    max_weight_per_symbol: float # 单币名义 / 权益
    risk_per_trade_pct: float    # 名义 × 止损距离 / 权益
    max_leverage: float          # 杠杆兜底上限（实际取币种档位，见 _resolve_leverage）
    cluster_cap: float           # 同簇名义 / 权益
    gross_cap: float             # 车道总名义 / 权益
    conf_scale_min: float = 0.5  # 置信度缩放下限（上限 1.0）

    @classmethod
    def for_lane(cls, lane: str) -> "LaneLimits":
        lane = normalize_lane(lane)
        is_long = lane == "long"
        return cls(
            lane=lane,
            vol_target=_lane_param("VOL_TARGET", lane, 0.35),
            max_weight_per_symbol=_lane_param("MAX_WEIGHT_PER_SYMBOL", lane, 0.35),
            # 趋势车道默认 1.25%（对照表 A3：CAGR 28% / MDD −31.5%）；其余车道按方案 0.75%
            risk_per_trade_pct=_lane_param("RISK_PER_TRADE_PCT", lane, 0.0125 if is_long else 0.0075),
            # 兜底上限（防 SYMBOL_LEVERAGE_MAP 配置写错），不是主约束：
            # 实际杠杆取币种档位，见 _resolve_leverage。
            max_leverage=_lane_param("MAX_LEVERAGE", lane, 10.0),
            cluster_cap=_lane_param("CLUSTER_CAP", lane, 0.50),
            gross_cap=_lane_param("GROSS_CAP", lane, 1.0),
            conf_scale_min=_lane_param("CONF_SCALE_MIN", lane, 0.5),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PositionPlan:
    lane: str
    symbol: str
    equity: float
    price: float
    notional: float
    quantity: float
    leverage: float
    margin: float
    weight: float
    risk_pct: float
    stop_distance_pct: Optional[float]
    confidence_scale: float
    caps_applied: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    limits: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.notional > 0 and self.quantity > 0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["ok"] = self.ok
        return d


@dataclass
class ClampResult:
    notional: float
    leverage: float
    quantity: float
    changed: bool
    caps_applied: List[str] = field(default_factory=list)
    limits: Dict[str, Any] = field(default_factory=dict)
    blocked: bool = False
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def cluster_of(symbol: str, clusters: Optional[Dict[str, Sequence[str]]] = None) -> Optional[str]:
    base = _base(symbol)
    for name, members in (clusters or DEFAULT_CLUSTERS).items():
        if base in {m.upper() for m in members}:
            return name
    return None


def _base(symbol: str) -> str:
    s = (symbol or "").upper().replace("-", "").replace("/", "").replace(":", "")
    for suf in ("USDT", "USDC", "USD", "PERP"):
        while s.endswith(suf) and len(s) > len(suf):
            s = s[: -len(suf)]
    return s


def _resolve_leverage(symbol: str, limits: LaneLimits) -> float:
    """杠杆取该 **币种** 的统一档位（BTC/ETH 5x、二线 4x、小币 3x）。

    [2026-09-04 币种杠杆] 原实现由 realized_vol 反推杠杆，同一币不同时点会算出不同
    倍数，而交易所同币只认一个 set_leverage 值——多套杠杆记的是假账。波动控制已经在
    权重（vol-target）里做过一次，落到杠杆上属于重复计量，故退役。

    limits.max_leverage 降级为防配置写错的兜底上限，不再是主约束。
    """
    try:
        from backend.services.leverage_authority import symbol_leverage
        lev = float(symbol_leverage(symbol))
    except Exception:
        lev = 3.0
    return max(1.0, min(float(limits.max_leverage), lev))


def construct(
    *,
    lane: str,
    symbol: str,
    equity: float,
    price: float,
    realized_vol: Optional[float],
    stop_distance_pct: Optional[float],
    confidence: Optional[float] = None,
    symbol_open_notional: float = 0.0,
    cluster_open_notional: float = 0.0,
    lane_open_notional: float = 0.0,
    base_weight: Optional[float] = None,
    limits: Optional[LaneLimits] = None,
) -> PositionPlan:
    """从零构造一笔新开仓。

    base_weight   可选：上游已按 vol-target（如 trend_core.size_one / target_weights）算好的目标权重；
                  给了就以它为起点（再套帽），不给就用 vol_target / realized_vol。
    confidence    0~1 或 0~100；只做 [conf_scale_min, 1.0] 的缩放。
    *_open_notional  同币 / 同簇 / 同车道已有名义（用于增量帽）。
    """
    lim = limits or LaneLimits.for_lane(lane)
    eq = max(0.0, float(equity or 0.0))
    px = float(price or 0.0)
    caps: List[str] = []
    reasons: List[str] = []
    if eq <= 0 or px <= 0:
        return PositionPlan(lane=lim.lane, symbol=symbol, equity=eq, price=px, notional=0.0, quantity=0.0, leverage=1.0,
                            margin=0.0, weight=0.0, risk_pct=0.0, stop_distance_pct=stop_distance_pct, confidence_scale=1.0,
                            caps_applied=["no_equity_or_price"], reasons=["权益或价格缺失"], limits=lim.to_dict())
    # 1) 起点权重
    if base_weight is not None and base_weight > 0:
        w = float(base_weight)
        reasons.append(f"base_weight={w:.4f}")
    elif realized_vol and realized_vol > 0:
        w = lim.vol_target / float(realized_vol)
        reasons.append(f"vol_target {lim.vol_target:.2f}/{realized_vol:.3f}={w:.4f}")
    else:
        w = lim.max_weight_per_symbol * 0.5
        caps.append("no_vol_fallback_half_cap")
        reasons.append("无实现波动，退化为单币帽的一半")
    # 2) 置信度缩放 0.5–1.0
    cs = 1.0
    if confidence is not None:
        c = float(confidence)
        if c > 1.0:
            c = c / 100.0
        c = max(0.0, min(1.0, c))
        cs = lim.conf_scale_min + (1.0 - lim.conf_scale_min) * c
        w *= cs
    # 3) 单币帽（含已有同币名义）
    room_sym = max(0.0, lim.max_weight_per_symbol - max(0.0, symbol_open_notional) / eq)
    if w > room_sym:
        w = room_sym
        caps.append(f"max_weight_per_symbol({lim.max_weight_per_symbol:.2f})")
    # 4) 单笔风险帽
    if stop_distance_pct and stop_distance_pct > 0:
        rc = lim.risk_per_trade_pct / float(stop_distance_pct)
        if w > rc:
            w = rc
            caps.append(f"risk_per_trade({lim.risk_per_trade_pct:.4f}/{stop_distance_pct:.4f})")
    else:
        reasons.append("无止损距离：跳过单笔风险帽（RiskEngine 仍要求有 SL）")
    # 5) 簇帽
    room_cl = max(0.0, lim.cluster_cap - max(0.0, cluster_open_notional) / eq)
    if w > room_cl:
        w = room_cl
        caps.append(f"cluster_cap({lim.cluster_cap:.2f})")
    # 6) 车道总敞口帽
    room_gross = max(0.0, lim.gross_cap - max(0.0, lane_open_notional) / eq)
    if w > room_gross:
        w = room_gross
        caps.append(f"gross_cap({lim.gross_cap:.2f})")
    w = max(0.0, w)
    notional = eq * w
    lev = _resolve_leverage(symbol, lim)
    margin = notional / lev if lev > 0 else notional
    qty = notional / px if px > 0 else 0.0
    risk_pct = w * float(stop_distance_pct or 0.0)
    return PositionPlan(lane=lim.lane, symbol=symbol, equity=eq, price=px, notional=round(notional, 6), quantity=qty,
                        leverage=round(lev, 4), margin=round(margin, 6), weight=w, risk_pct=round(risk_pct, 6),
                        stop_distance_pct=stop_distance_pct, confidence_scale=cs, caps_applied=caps, reasons=reasons,
                        limits=lim.to_dict())


def clamp(
    *,
    lane: str,
    symbol: str,
    equity: float,
    price: float,
    notional: float,
    leverage: float,
    stop_distance_pct: Optional[float] = None,
    symbol_open_notional: float = 0.0,
    cluster_open_notional: float = 0.0,
    lane_open_notional: float = 0.0,
    is_add: bool = False,
    limits: Optional[LaneLimits] = None,
) -> ClampResult:
    """对上游提议的名义做硬帽：单币 ≤35%、单笔风险、簇 ≤50%、车道 gross。杠杆原样透传（见上）。

    只缩不放；缩到 0 → blocked=True（调用方拒单）。加仓（is_add）同样受帽，但同币已有名义计入 room。
    """
    lim = limits or LaneLimits.for_lane(lane)
    eq = max(0.0, float(equity or 0.0))
    px = float(price or 0.0)
    n0 = max(0.0, float(notional or 0.0))
    l0 = max(1.0, float(leverage or 1.0))
    caps: List[str] = []
    if eq <= 0 or px <= 0 or n0 <= 0:
        return ClampResult(notional=n0, leverage=l0, quantity=(n0 / px if px > 0 else 0.0), changed=False,
                           limits=lim.to_dict(), blocked=(n0 <= 0), reason=("名义为 0" if n0 <= 0 else ""))
    n = n0
    room_sym = max(0.0, lim.max_weight_per_symbol * eq - max(0.0, symbol_open_notional))
    if n > room_sym:
        n = room_sym
        caps.append(f"max_weight_per_symbol({lim.max_weight_per_symbol:.2f})")
    if stop_distance_pct and stop_distance_pct > 0:
        rc = lim.risk_per_trade_pct * eq / float(stop_distance_pct)
        if n > rc:
            n = rc
            caps.append(f"risk_per_trade({lim.risk_per_trade_pct:.4f}/{stop_distance_pct:.4f})")
    room_cl = max(0.0, lim.cluster_cap * eq - max(0.0, cluster_open_notional))
    if n > room_cl:
        n = room_cl
        caps.append(f"cluster_cap({lim.cluster_cap:.2f})")
    room_gross = max(0.0, lim.gross_cap * eq - max(0.0, lane_open_notional))
    if n > room_gross:
        n = room_gross
        caps.append(f"gross_cap({lim.gross_cap:.2f})")
    # [2026-09-04 币种杠杆] 杠杆权威在 leverage_authority/TradeGate（含"同币已有仓必须
    # 沿用"的 adopt 逻辑），此处不再二次决策——否则会把 adopt 来的存量杠杆改掉，同币又
    # 变回两套值。PC 只管名义/份额，仅保留防御性兜底以拦住异常高倍。
    lev = l0
    if lev > lim.max_leverage:
        lev = lim.max_leverage
        caps.append(f"leverage_guard({lim.max_leverage:.1f})")
    # 名义太小（< 权益 0.2% 或 < 5 USDT）视为无效 → 拒
    min_notional = max(5.0, 0.002 * eq)
    blocked = n < min_notional
    reason = f"夹紧后名义 {n:.2f} < 最小 {min_notional:.2f}（{'/'.join(caps) or 'room=0'}）" if blocked else ""
    return ClampResult(notional=round(n, 6), leverage=round(lev, 4), quantity=(n / px), changed=(abs(n - n0) > 1e-9 or abs(lev - l0) > 1e-9),
                       caps_applied=caps, limits=lim.to_dict(), blocked=blocked, reason=reason)


# ─────────────────────────── DB 辅助：在手名义 ───────────────────────────

def open_notionals(db, account_id: int, symbol: str, lane: Optional[str] = None,
                   clusters: Optional[Dict[str, Sequence[str]]] = None) -> Dict[str, float]:
    """同账户在手名义：同币 / 同簇 / 同车道 / 全部（paper_positions status=open）。"""
    from sqlalchemy import text

    out = {"symbol": 0.0, "cluster": 0.0, "lane": 0.0, "total": 0.0}
    try:
        rows = db.execute(text(
            "SELECT symbol, timeframe_tier, trade_nature, size, mark_price, entry_price FROM paper_positions "
            "WHERE account_id = :a AND status = 'open'"), {"a": int(account_id)}).fetchall()
    except Exception as exc:
        logger.debug("[PositionConstruction] 读取在手名义失败: %s", exc)
        return out
    base = _base(symbol)
    cl = cluster_of(symbol, clusters)
    for r in rows:
        try:
            n = float(r[3] or 0) * float(r[4] or r[5] or 0)
        except Exception:
            continue
        out["total"] += n
        rb = _base(r[0])
        if rb == base:
            out["symbol"] += n
        if cl and cluster_of(rb, clusters) == cl:
            out["cluster"] += n
        if lane and normalize_lane(r[1], r[2]) == normalize_lane(lane):
            out["lane"] += n
    return out


def describe_limits() -> Dict[str, Any]:
    return {"enforce": enforce_enabled(), "lanes": {l: LaneLimits.for_lane(l).to_dict() for l in LANES},
            "clusters": {k: list(v) for k, v in DEFAULT_CLUSTERS.items()}}
