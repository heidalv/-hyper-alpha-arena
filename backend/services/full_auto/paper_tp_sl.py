"""Paper 开仓 TP/SL 兜底与比率校正 — 从 monolith 迁出。"""
from __future__ import annotations

import logging
from typing import Callable, Optional, Tuple

logger = logging.getLogger(__name__)

# [2026-07-30 crypto-native] scalp 默认 TP 1.2%/SL 2.5% → TP<SL 完全反了!
# 这导致 MIN_TP_SL_RATIO=2.5 强制把 TP 拉远到 SL×2.5=6.25%，不切实际。
# 修正为 TP 2%/SL 1.2%（TP>SL，RR=1.67，适合 crypto 5m scalp）。
DEFAULT_TP_SL_BY_NATURE = {
    "scalp": (0.020, 0.012),
    "intraday": (0.018, 0.040),
    "swing": (0.025, 0.060),
    "trend_follow": (0.040, 0.120),
    "position": (0.050, 0.200),
}

# [2026-07-30 crypto-native] 2.5 太高，强制拉远 TP 导致 breakeven 频繁触发。
# crypto scalp RR 1.5-2.0 即可正期望。
MIN_TP_SL_RATIO = 1.8


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
) -> Tuple[float, float]:
    """强制 TP/SL 兜底、精选币 SL 收紧、最低 2.5:1 比率校正。"""
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
