"""Paper 开仓 TP/SL 兜底与比率校正 — 从 monolith 迁出。"""
from __future__ import annotations

import logging
import os
from typing import Callable, Optional, Tuple

logger = logging.getLogger(__name__)

# [2026-07-30 crypto-native] scalp 默认 TP 1.2%/SL 2.5% → TP<SL 完全反了!
# 这导致 MIN_TP_SL_RATIO=2.5 强制把 TP 拉远到 SL×2.5=6.25%，不切实际。
# 修正为 TP 2%/SL 1.2%（TP>SL，RR=1.67，适合 crypto 5m scalp）。
# [2026-08-23 改造A] scalp 兜底对齐新口径 TP 1.5%/SL 1.0%（信号 30-45min 边际
# ±0.3% 的兑现尺度），且 MIN_TP_SL_RATIO 1.8→1.3：旧比率会把新参数 TP(SL×1.5)
# 强制拉远到 SL×1.8，重新制造"够不到的 TP"。
DEFAULT_TP_SL_BY_NATURE = {
    "scalp": (0.015, 0.010),
    "intraday": (0.018, 0.040),
    "swing": (0.025, 0.060),
    "trend_follow": (0.040, 0.120),
    "position": (0.050, 0.200),
}

# [2026-07-30 crypto-native] 2.5 太高，强制拉远 TP 导致 breakeven 频繁触发。
# crypto scalp RR 1.5-2.0 即可正期望。
# [2026-08-23 改造A] →1.3：与 V5_SCALP_MIN_RR_PAPER(1.3) 对齐，避免与新
# TP/SL 口径（RR 1.3-1.5）互相拉扯。
MIN_TP_SL_RATIO = 1.3


def finalize_open_tp_sl(
    *,
    symbol: str,
    trade_nature: str,
    side: str,
    price: float,
    plan_sl: Optional[float],
    plan_tp: Optional[float],
    is_auto_coin: bool = False,
    on_event: Optional[Callable[..., None]] = None,
    volatility_pct: Optional[float] = None,
) -> Tuple[float, float]:
    """强制 TP/SL 兜底、精选币 SL 收紧、最低 2.5:1 比率校正、
    [P3 大轮回 2026-09-27] 波动止损下限（§6.1：SL 距离 ≥ 2.2σ_1h，封顶 3%）。
    """
    _def_sl_pct, _def_tp_pct = DEFAULT_TP_SL_BY_NATURE.get(trade_nature, (0.025, 0.060))
    _is_long = side in ("long", "buy")
    _final_sl = plan_sl if (plan_sl and plan_sl > 0) else None
    _final_tp = plan_tp if (plan_tp and plan_tp > 0) else None

    def _emit(event_type: str, msg: str) -> None:
        if on_event:
            on_event(event_type, msg)

    if not _final_sl:
        _final_sl = round(price * (1 - _def_sl_pct) if _is_long else price * (1 + _def_sl_pct), 6)
        logger.warning(
            f"[FullAuto] {symbol}[{trade_nature}] 缺失 SL，强制兜底 SL={_final_sl} "
            f"(default {_def_sl_pct:.1%})"
        )
        _emit("sl_autofill", f"⚠️ {symbol}[{trade_nature}] 自动填 SL=${_final_sl:.4f} (无 SL 不允许开仓)")

    if not _final_tp:
        _final_tp = round(price * (1 + _def_tp_pct) if _is_long else price * (1 - _def_tp_pct), 6)
        logger.warning(
            f"[FullAuto] {symbol}[{trade_nature}] 缺失 TP，强制兜底 TP={_final_tp} "
            f"(default {_def_tp_pct:.1%})"
        )

    if is_auto_coin and _final_sl and price > 0:
        _sl_dist_pct = abs(price - _final_sl) / price
        if _sl_dist_pct > _def_sl_pct:
            _old_sl = _final_sl
            _final_sl = round(
                price * (1 - _def_sl_pct) if _is_long else price * (1 + _def_sl_pct), 6
            )
            logger.info(
                f"[FullAuto] AI精选币SL收紧: {symbol}[{trade_nature}] "
                f"{_sl_dist_pct:.1%}→{_def_sl_pct:.1%} ({_old_sl}→{_final_sl})"
            )
            _emit(
                "auto_coin_sl_clamp",
                f"🌟 {symbol} 精选币止损收紧 {_sl_dist_pct:.1%}→{_def_sl_pct:.1%}",
            )

    # [P3 大轮回 2026-09-27] 波动止损下限（§6.1）：SL 距离 = max(计划/结构位, 2.2σ_1h)，
    # 封顶 3%（现 cap）——过紧的止损会被噪音扫掉（赢家 p90 MAE 1.04%，2.2σ 通常 1~2%）。
    # 仅在当前距离 < 2.2σ 且 2.2σ ≤ cap 时加宽到 2.2σ；2.2σ > cap 或已有结构位更宽 → 不动。
    try:
        _vol_floor_on = (os.getenv("MIDLONG_VOL_STOP_FLOOR_ENABLED", "true") or "true"
                         ).strip().lower() in ("1", "true", "yes", "on")
    except Exception:
        _vol_floor_on = True
    if _vol_floor_on and _final_sl and price > 0:
        try:
            _vol = float(volatility_pct or 0)
        except (TypeError, ValueError):
            _vol = 0.0
        if _vol > 0:
            try:
                _mult = float(os.getenv("MIDLONG_SL_SIGMA_MULT", "2.2") or 2.2)
                _cap = float(os.getenv("MIDLONG_SL_MAX_PCT", "0.03") or 0.03)
            except (TypeError, ValueError):
                _mult, _cap = 2.2, 0.03
            _sigma_dist = min(_mult * _vol, _cap)
            _sl_dist_pct = abs(price - _final_sl) / price
            if _sl_dist_pct < _sigma_dist:
                _old_sl = _final_sl
                _final_sl = round(
                    price * (1 - _sigma_dist) if _is_long else price * (1 + _sigma_dist), 6
                )
                logger.info(
                    "[VolStopFloor] %s[%s] SL 距离 %.2f%% < 2.2σ=%.2f%% → 加宽到 %.2f%%"
                    "（%s→%s，封顶 %.0f%%）",
                    symbol, trade_nature, _sl_dist_pct * 100, _mult * _vol * 100,
                    _sigma_dist * 100, _old_sl, _final_sl, _cap * 100,
                )
                _emit(
                    "vol_stop_floor",
                    f"📏 {symbol} 波动止损下限：SL 距离加宽到 {_sigma_dist:.1%}"
                    f"（2.2σ 保护，防噪音扫损）",
                )

    if _final_sl and _final_tp and price > 0:
        _sl_dist = abs(price - _final_sl)
        _tp_dist = abs(_final_tp - price)
        if _sl_dist > 0 and _tp_dist > 0:
            _current_ratio = _tp_dist / _sl_dist
            if _current_ratio < MIN_TP_SL_RATIO:
                _new_tp_dist = _sl_dist * MIN_TP_SL_RATIO
                _orig_tp = _final_tp
                _final_tp = round(price + _new_tp_dist if _is_long else price - _new_tp_dist, 6)
                logger.info(
                    f"[FullAuto] TP/SL比率调整: {symbol}[{trade_nature}] "
                    f"{_current_ratio:.1f}x→{MIN_TP_SL_RATIO:.1f}x "
                    f"TP {_orig_tp:.4f}→{_final_tp:.4f} "
                    f"(SL={_final_sl:.4f}, dist_sl={_sl_dist:.4f})"
                )
                _emit(
                    "tp_sl_adjusted",
                    f"📐 {symbol}[{trade_nature}] TP/SL {_current_ratio:.1f}→{MIN_TP_SL_RATIO:.1f}x "
                    f"(扩宽TP以保证正期望)",
                )

    return _final_sl, _final_tp
