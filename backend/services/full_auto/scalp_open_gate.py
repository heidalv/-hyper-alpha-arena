"""短线新开硬闸（纸盘 + 实盘共用）。

已有短线仓只许平/减，不许加仓或新开。独立循环不注册不等于没有漏开：
任何 place_order 入口都必须过这一闸。
"""
from __future__ import annotations

from typing import Optional, Tuple


def scalp_new_open_blocked(
    add_type: Optional[str] = None,
    trade_nature: Optional[str] = None,
    timeframe_tier: Optional[str] = None,
    *,
    reduce_only: bool = False,
) -> Tuple[bool, str]:
    """返回 (blocked, reason)。平仓/减仓永远放行。"""
    if reduce_only or str(add_type or "").lower() in ("reduce", "close"):
        return False, ""
    try:
        from backend.config.settings import SCALP_OPEN_DISABLED
        if not SCALP_OPEN_DISABLED:
            return False, ""
    except Exception:
        import os
        if os.getenv("SCALP_OPEN_DISABLED", "true").strip().lower() not in (
            "1", "true", "yes", "on",
        ):
            return False, ""
    nature = str(trade_nature or "").strip().lower()
    tier = str(timeframe_tier or "").strip().lower()
    if nature in ("scalp", "intraday") or tier == "short":
        # [2026-09-07] LLM 日内波段车道豁免：nature=intraday 且 INTRADAY_LLM_ENABLED
        # 时放行（旧因子 scalp 路径 nature=scalp 永远拦截）。
        if nature == "intraday":
            try:
                from backend.config.settings import INTRADAY_LLM_ENABLED
                if INTRADAY_LLM_ENABLED:
                    return False, ""
            except Exception:
                pass
        return True, "scalp_open_disabled"
    return False, ""
