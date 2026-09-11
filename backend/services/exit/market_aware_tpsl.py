"""短线行情出场管家 — 按市场状态分配开仓止盈止损。

短线开仓走 plan_scalp_tp_sl。中长线仍走 mid_long_structure_stop，
只复用 apply_market_overlays（资金费/OI/周末/清算），不用短线夹幅。

玩法（playbook）:
  trend_trail   趋势：止损给 ATR 呼吸，硬止盈只作天花板
  range_hard_tp 震荡：止损贴结构，止盈优先对面区间沿
  extreme_lock  极端：近止盈、尽快锁
  unknown_tight 不明：偏紧，认错快

夹幅是安全网，不再把所有币压成同一个百分比。
回滚：SCALP_MARKET_AWARE_TPSL=false → structure_stop 走旧窄夹幅。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_PLAYBOOKS = ("trend_trail", "range_hard_tp", "extreme_lock", "unknown_tight")


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


def planner_enabled() -> bool:
    try:
        from backend.config import settings as _settings
        return bool(getattr(_settings, "SCALP_MARKET_AWARE_TPSL", True))
    except Exception:
        return _env_bool("SCALP_MARKET_AWARE_TPSL", True)


def hold_dynamic_enabled() -> bool:
    """持仓期动态出场（认错/保本/追踪/翻脸）。false 则 min_hold 仍挡 1 小时。"""
    try:
        from backend.config import settings as _settings
        return bool(getattr(_settings, "SCALP_DYNAMIC_HOLD_TPSL", True))
    except Exception:
        return _env_bool("SCALP_DYNAMIC_HOLD_TPSL", True)


def _f(md: Dict[str, Any], *keys: str, default: float = 0.0) -> float:
    for key in keys:
        try:
            v = md.get(key)
            if v is None:
                continue
            return float(v)
        except (TypeError, ValueError):
            continue
    return default


@dataclass
class MarketAwarePlan:
    sl_pct: float
    tp_pct: float
    sl_price: float
    tp_price: float
    playbook: str
    regime: str
    atr_pct: float
    reason: str
    symbol: str = ""
    side: str = "long"
    audit: Dict[str, Any] = field(default_factory=dict)

    def to_audit(self) -> Dict[str, Any]:
        return {
            "sl_pct": round(self.sl_pct, 6),
            "tp_pct": round(self.tp_pct, 6),
            "sl_price": round(self.sl_price, 8),
            "tp_price": round(self.tp_price, 8),
            "playbook": self.playbook,
            "regime": self.regime,
            "atr_pct": round(self.atr_pct, 6),
            "reason": self.reason,
            "rr": round(self.tp_pct / self.sl_pct, 3) if self.sl_pct > 0 else 0.0,
            "symbol": self.symbol,
            "side": self.side,
            **{k: v for k, v in self.audit.items() if k not in ("reason",)},
        }


def _classify(md: Dict[str, Any], atr_pct: float) -> Tuple[str, str]:
    """用已有 RegimeAgent；缺字段时补波动率，避免全判 unknown。"""
    probe = dict(md or {})
    if _f(probe, "volatility_pct", "volatility_value", "atr_pct") <= 0 and atr_pct > 0:
        probe["volatility_pct"] = atr_pct
    try:
        from backend.services.decision_core.regime_agent import classify_regime
        result = classify_regime(probe)
        return str(result.regime or "unknown"), str(result.detail or "")
    except Exception as exc:
        return "unknown", f"classify_failed:{exc}"


def _structure_sl_pct(
    *,
    side: str,
    price: float,
    swing_low: float,
    swing_high: float,
    buffer: float,
) -> float:
    if price <= 0:
        return 0.0
    if side in ("buy", "long"):
        if swing_low <= 0 or swing_low >= price:
            return 0.0
        sl_price = swing_low * (1.0 - buffer)
        if sl_price <= 0 or sl_price >= price:
            return 0.0
        return (price - sl_price) / price
    if swing_high <= 0 or swing_high <= price:
        return 0.0
    sl_price = swing_high * (1.0 + buffer)
    if sl_price <= price:
        return 0.0
    return (sl_price - price) / price


def _opposite_band_tp_pct(
    *,
    side: str,
    price: float,
    swing_low: float,
    swing_high: float,
    buffer: float,
) -> float:
    if price <= 0:
        return 0.0
    if side in ("buy", "long"):
        if swing_high <= price:
            return 0.0
        target = swing_high * (1.0 - min(buffer, 0.002))
        if target <= price:
            return 0.0
        return (target - price) / price
    if swing_low <= 0 or swing_low >= price:
        return 0.0
    target = swing_low * (1.0 + min(buffer, 0.002))
    if target >= price:
        return 0.0
    return (price - target) / price


def _funding_against(side: str, funding: float) -> bool:
    if side in ("buy", "long"):
        return funding > 0
    return funding < 0


def apply_market_overlays(
    sl_pct: float,
    tp_pct: float,
    *,
    side: str,
    market_data: Optional[Dict[str, Any]] = None,
    sl_min: float = 0.005,
    sl_max: float = 0.20,
    tp_min: float = 0.006,
    tp_max: float = 0.20,
) -> Tuple[float, float, list]:
    """资金费 / OI 逼空 / 清算 / 周末：短线与中长线共用的行情加减。"""
    md = market_data if isinstance(market_data, dict) else {}
    side_l = "long" if (side or "long").lower() in ("buy", "long") else "short"
    notes: list = []
    funding = _f(md, "funding_rate")
    fund_warn = _env_float("SCALP_MA_FUNDING_ABS_WARN", 0.0005)
    if abs(funding) >= fund_warn and _funding_against(side_l, funding):
        tp_pct *= 0.90
        notes.append(f"资金费作对 {funding:.4%}，止盈收近 10%")

    oi_delta = _f(md, "oi_delta_pct", "oi_delta")
    if abs(oi_delta) > 1.0:
        oi_delta = oi_delta / 100.0
    if abs(funding) >= 0.001 and abs(oi_delta) >= 0.05:
        if side_l == "long":
            tp_pct *= 0.85
            notes.append("OI+资金费逼空，多单更快收")
        else:
            sl_pct *= 1.15
            notes.append("OI+资金费逼空，空单止损略放宽")

    pwin = _f(md, "pwin", "meta_pwin")
    if pwin >= 0.55:
        tp_pct *= 1.10
        notes.append(f"pwin={pwin:.2f} 高，止盈略放")
    elif 0 < pwin < 0.45:
        tp_pct *= 0.90
        notes.append(f"pwin={pwin:.2f} 低，止盈收近")

    if md.get("is_weekend"):
        sl_pct *= 1.10
        notes.append("周末流动性差，止损略放宽")

    liq = str(md.get("liq_severity") or md.get("liquidation_severity") or "").lower()
    if liq in ("high", "extreme"):
        sl_pct *= 1.10
        notes.append(f"清算磁铁 {liq}，止损略放宽躲扫")

    sl_pct = max(sl_min, min(sl_max, sl_pct))
    tp_pct = max(tp_min, min(tp_max, tp_pct))
    return sl_pct, tp_pct, notes


def _prices(side: str, price: float, sl_pct: float, tp_pct: float) -> Tuple[float, float]:
    if side in ("buy", "long"):
        return price * (1.0 - sl_pct), price * (1.0 + tp_pct)
    return price * (1.0 + sl_pct), price * (1.0 - tp_pct)


def _clamp_rr(
    sl_pct: float,
    tp_pct: float,
    *,
    sl_min: float,
    sl_max: float,
    tp_min: float,
    tp_max: float,
    min_rr: float,
) -> Tuple[float, float, str]:
    """安全网夹幅 + 最低盈亏比。先夹，再抬 TP；TP 顶死后才略收 SL。"""
    note = ""
    sl_pct = max(sl_min, min(sl_max, sl_pct))
    tp_pct = max(tp_min, min(tp_max, tp_pct))
    if sl_pct <= 0:
        return sl_pct, tp_pct, note
    if tp_pct / sl_pct + 1e-12 >= min_rr:
        return sl_pct, tp_pct, note
    lifted = min(tp_max, sl_pct * min_rr)
    if lifted / sl_pct + 1e-12 >= min_rr:
        return sl_pct, max(tp_pct, lifted), "tp_lift_rr"
    shrunk = max(sl_min, tp_max / min_rr)
    if tp_max / shrunk + 1e-12 >= min_rr:
        return shrunk, tp_max, "sl_shrink_rr"
    return sl_pct, tp_pct, "rr_unmet_cap"


def plan_scalp_tp_sl(
    market_data: Optional[Dict[str, Any]] = None,
    *,
    side: str = "long",
    entry: float = 0.0,
    swing_low: float = 0.0,
    swing_high: float = 0.0,
    atr_pct: float = 0.0,
    buffer_pct: Optional[float] = None,
    symbol: str = "",
) -> MarketAwarePlan:
    """按行情快照给出短线开仓止盈止损。"""
    md = market_data if isinstance(market_data, dict) else {}
    side_l = "long" if (side or "long").lower() in ("buy", "long") else "short"
    try:
        from backend.config.settings import SCALP_STRUCTURE_SL_BUFFER_PCT
        buffer = float(buffer_pct if buffer_pct is not None else SCALP_STRUCTURE_SL_BUFFER_PCT)
    except Exception:
        buffer = float(buffer_pct if buffer_pct is not None else 0.008)

    sl_min = _env_float("SCALP_MA_SL_MIN_PCT", 0.005)
    sl_max = _env_float("SCALP_MA_SL_MAX_PCT", 0.030)
    tp_min = _env_float("SCALP_MA_TP_MIN_PCT", 0.006)
    # [2026-09-02 P2.2] 上限 4.0%→5.5%：RR 提到 2.0-2.5 后，SL 只要宽于
    # 1.6% 就会顶到旧上限，被夹回去的实际 RR 反而降到 1.3 左右，等于新
    # RR 对宽止损单完全失效。SL 上限是 3.0%，取 2.2%×2.5≈5.5% 覆盖绝大
    # 多数情形；仍保留上限以防结构位异常时给出离谱止盈。
    tp_max = _env_float("SCALP_MA_TP_MAX_PCT", 0.055)

    # ── 风险回报比（RR = TP距离 / SL距离）─────────────────────────
    # [2026-09-02 P2.2] 上调依据：实测近 14 天 short tier 812 笔已平仓单，
    # 平均盈利 0.631 / 平均亏损 0.606 → 盈亏比仅 **1.042**，胜率 41.4%；
    # 该胜率下需盈亏比 > 1.417 才能打平。根因就在这里 —— 原 RR 设定
    # （trend 1.8 / ranging 1.3 / extreme 1.2 / unknown 1.3，兜底 1.3）
    # 让 TP 与 SL 距离几乎相等，再被分批止盈在 TP1 减仓 25% 一压，实现出
    # 来的盈亏比必然趋近 1，数学上不可能盈利。
    #
    # 离线回放定标（做多 + pwin>=0.55，2680 条，30 天，SL 固定 1.1%）：
    #     RR    净bp    TP命中   超时    持仓中位
    #     1.3   25.4    34.7%   40.5%    70min   ← 原设定
    #     2.0   38.4    24.5%   49.3%   118min
    #     2.3   44.0    21.0%   52.7%   120min   ← 采用
    #     3.0   52.1    13.4%   60.3%   120min
    #     4.0   57.6     8.2%   64.9%   120min   ← 弃用
    #
    # 不取更高 RR 的两个理由：
    # 1) RR>=3 时 TP 命中率跌到 13% 以下、持仓顶满 max_hold，收益来源已经
    #    从"止盈兑现"变成"拿满吃漂移"，这是持有 beta 不是策略 alpha；
    # 2) 样本期最后 14 天 BTC +19.3% / ETH +25.2% / SOL +28.8% 为强牛市，
    #    高 RR 做多在这种行情下天然占便宜，不可外推到震荡/下跌。
    #
    # 已验证不是纯 beta：RR=2.3 时按 pwin 分层，<0.45 为 -1.1bp、
    # 0.50-0.55 为 +33.1bp、0.55-0.60 为 +42.1bp、>0.60 为 +49.8bp，
    # 严格单调 —— 信号质量与收益正相关，说明存在真实区分度。
    #
    # 回退：把四个 SCALP_MA_RR_* 设回 1.8/1.3/1.2/1.3、MIN_RR 设回 1.3。
    min_rr = _env_float("SCALP_MA_MIN_RR", 2.0)
    rr_trend = _env_float("SCALP_MA_RR_TREND", 2.5)
    rr_ranging = _env_float("SCALP_MA_RR_RANGING", 2.0)
    rr_extreme = _env_float("SCALP_MA_RR_EXTREME", 2.0)
    rr_unknown = _env_float("SCALP_MA_RR_UNKNOWN", 2.0)

    price = float(entry or 0) or _f(md, "price", "mark_price")
    if atr_pct <= 0:
        atr_pct = _f(md, "volatility_value", "atr_pct", default=0.015)
    atr_pct = max(0.006, min(0.030, float(atr_pct or 0.015)))

    if swing_low <= 0 or swing_high <= 0:
        try:
            from backend.services.scalp.structure_stop_calculator import structure_stop_calculator
            swing_low, swing_high, _ = structure_stop_calculator.swing_levels(md.get("klines"))
        except Exception:
            pass

    regime, regime_detail = _classify(md, atr_pct)
    struct_sl = _structure_sl_pct(
        side=side_l, price=price, swing_low=swing_low, swing_high=swing_high, buffer=buffer,
    )
    struct_sl = min(struct_sl, sl_max) if struct_sl > 0 else 0.0

    why: list[str] = []
    if regime == "trend":
        playbook = "trend_trail"
        sl_mult, tp_rr = 1.5, rr_trend
        sl_pct = max(atr_pct * sl_mult, struct_sl or 0.0)
        tp_pct = sl_pct * tp_rr
        why.append(f"趋势市 ATR×{sl_mult:g} 给呼吸")
        if struct_sl > atr_pct * sl_mult:
            why.append(f"结构止损更宽 {struct_sl:.2%}")
        why.append("硬止盈作天花板，持仓靠追踪")
    elif regime == "ranging":
        playbook = "range_hard_tp"
        sl_mult, tp_rr = 1.0, rr_ranging
        atr_sl = atr_pct * sl_mult
        if struct_sl > 0:
            sl_pct = max(atr_sl, min(struct_sl, atr_pct * 1.5))
        else:
            sl_pct = atr_sl
        band_tp = _opposite_band_tp_pct(
            side=side_l, price=price, swing_low=swing_low, swing_high=swing_high, buffer=buffer,
        )
        if tp_min <= band_tp <= tp_max:
            tp_pct = band_tp
            why.append(f"震荡：止盈对面沿 {band_tp:.2%}")
        else:
            tp_pct = sl_pct * tp_rr
            why.append("震荡：对面沿不可用，用 RR×止损")
        why.append(f"止损 ATR×{sl_mult:g}（不扛趋势）")
    elif regime == "extreme":
        playbook = "extreme_lock"
        sl_mult, tp_rr = 1.2, rr_extreme
        sl_pct = atr_pct * sl_mult
        tp_pct = sl_pct * tp_rr
        why.append(f"极端行情近止盈锁利 ({regime_detail or 'chg/vol'})")
    else:
        playbook = "unknown_tight"
        sl_mult, tp_rr = 1.2, rr_unknown
        sl_pct = max(atr_pct * sl_mult, struct_sl or 0.0)
        tp_pct = sl_pct * tp_rr
        why.append("状态不明，偏紧止盈止损")

    sl_pct, tp_pct, overlay_notes = apply_market_overlays(
        sl_pct, tp_pct, side=side_l, market_data=md,
        sl_min=sl_min, sl_max=sl_max, tp_min=tp_min, tp_max=tp_max,
    )
    why.extend(overlay_notes)

    sl_pct, tp_pct, rr_note = _clamp_rr(
        sl_pct, tp_pct,
        sl_min=sl_min, sl_max=sl_max, tp_min=tp_min, tp_max=tp_max, min_rr=min_rr,
    )
    if rr_note:
        why.append(rr_note)

    sl_price, tp_price = (0.0, 0.0)
    if price > 0:
        sl_price, tp_price = _prices(side_l, price, sl_pct, tp_pct)

    reason = "；".join(why) or playbook
    plan = MarketAwarePlan(
        sl_pct=sl_pct,
        tp_pct=tp_pct,
        sl_price=sl_price,
        tp_price=tp_price,
        playbook=playbook if playbook in _PLAYBOOKS else "unknown_tight",
        regime=regime,
        atr_pct=atr_pct,
        reason=reason,
        symbol=str(symbol or md.get("symbol") or ""),
        side=side_l,
        audit={
            "struct_sl_pct": round(struct_sl, 6),
            "funding_rate": round(_f(md, "funding_rate"), 8),
            "oi_delta": round(_f(md, "oi_delta_pct", "oi_delta"), 6),
            "pwin": round(_f(md, "pwin", "meta_pwin"), 4),
            "regime_detail": regime_detail,
        },
    )
    if isinstance(market_data, dict):
        market_data["_tpsl_plan"] = plan.to_audit()
    logger.info(
        "[MarketAwareTpSl] %s %s regime=%s atr=%.3f%% sl=%.3f%% tp=%.3f%% "
        "play=%s rr=%.2f | %s",
        plan.symbol or "?",
        side_l,
        regime,
        atr_pct * 100.0,
        sl_pct * 100.0,
        tp_pct * 100.0,
        playbook,
        (tp_pct / sl_pct) if sl_pct > 0 else 0.0,
        reason,
    )
    return plan
