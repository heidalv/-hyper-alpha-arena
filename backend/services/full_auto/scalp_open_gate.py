"""短线新开硬闸（纸盘 + 实盘共用）。

已有短线仓只许平/减，不许加仓或新开。独立循环不注册不等于没有漏开：
任何 place_order 入口都必须过这一闸。

[调研轮9 2026-09-16] **窄口径解封 AI 受管标的**（恢复 AI 选币的交易能力）：
`SCALP_OPEN_DISABLED=true` 是 2026-09-05 因「旧因子 scalp 无边际」而设的整体停开，
副作用是 **AI 选币的结果一条也开不出来**（auto-coin 只能走短线车道）。轮7 审计的
建议是「窄口径解封」——只放行 AI 受管标的，不整体打开（否则会连带放开旧因子 scalp 路径）。

三个开关的语义：
  * `INTRADAY_LLM_ENABLED`：LLM 日内波段车道（nature=intraday / tier=short）总闸；
  * `SCALP_AI_ONLY_OPEN=true`（默认）：**只有 AI 受管标的**能在短线车道新开；
    置 false 则回到「intraday 全宇宙放行、scalp 仍禁」的中口径；
  * 旧因子路径（nature=scalp）**永远拦截**，不受上面两个开关影响。
"""
from __future__ import annotations

import logging
import os
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

#: 判定「AI 受管」时的会话缓存（短 TTL，避免热路径反复查库）
_AI_SYM_CACHE: dict = {}
_AI_SYM_TTL_SEC = 30.0


def ai_only_open_enabled() -> bool:
    """[调研轮9] 短线车道是否**只**对 AI 受管标的开放（默认开）。"""
    return str(os.getenv("SCALP_AI_ONLY_OPEN", "true")).strip().lower() in (
        "1", "true", "yes", "on",
    )


def intraday_lane_enabled() -> bool:
    """LLM 日内波段车道总闸（settings 优先，缺省读 env）。"""
    try:
        from backend.config.settings import INTRADAY_LLM_ENABLED
        return bool(INTRADAY_LLM_ENABLED)
    except Exception:
        return str(os.getenv("INTRADAY_LLM_ENABLED", "false")).strip().lower() in (
            "1", "true", "yes", "on",
        )


def is_ai_managed_symbol(symbol: Optional[str], session_id: Optional[str] = None) -> bool:
    """该 symbol 是否是 AI 选币的受管标的（会话 AI 池 / sticky / 统一状态层）。

    判定失败一律返回 False（**fail-closed**：宁可不开，不可误开非 AI 标的）。
    """
    sym = str(symbol or "").strip().upper()
    if not sym:
        return False
    import time as _t
    key = f"{sym}|{session_id or '*'}"
    hit = _AI_SYM_CACHE.get(key)
    now = _t.time()
    if hit and now - hit[0] < _AI_SYM_TTL_SEC:
        return bool(hit[1])
    ok = False
    try:
        from backend.services.auto_coin_selector import is_auto_coin_symbol as _iacs
        ok = bool(_iacs(sym, session_id))
    except Exception as exc:  # noqa: BLE001
        logger.debug("[ScalpOpenGate] AI 受管判定失败(按非 AI 处理) %s: %s", sym, exc)
        ok = False
    _AI_SYM_CACHE[key] = (now, ok)
    return ok


def scalp_new_open_blocked(
    add_type: Optional[str] = None,
    trade_nature: Optional[str] = None,
    timeframe_tier: Optional[str] = None,
    *,
    reduce_only: bool = False,
    symbol: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Tuple[bool, str]:
    """返回 (blocked, reason)。平仓/减仓永远放行。"""
    if reduce_only or str(add_type or "").lower() in ("reduce", "close"):
        return False, ""
    if not _scalp_open_disabled():
        return False, ""
    nature = str(trade_nature or "").strip().lower()
    tier = str(timeframe_tier or "").strip().lower()
    if nature in ("scalp", "intraday") or tier == "short":
        # 旧因子 scalp 路径：永远拦截（本轮不做任何解封）
        if nature == "scalp":
            return True, "scalp_open_disabled"
        # LLM 日内波段车道（nature=intraday / tier=short）
        _ai_only = ai_only_open_enabled()
        _is_ai = is_ai_managed_symbol(symbol, session_id)
        if _is_ai:
            # 窄口径解封：AI 受管标的放行（不要求 intraday 总闸，因为 AI 池本身就是
            # 选币器的产出；总闸只约束"非 AI 的日内波段宇宙"）
            return False, "ai_managed_open_allowed"
        if nature == "intraday" and intraday_lane_enabled() and not _ai_only:
            return False, ""
        if _ai_only:
            return True, "scalp_ai_only_open_non_ai_symbol"
        return True, "scalp_open_disabled"
    return False, ""


def _scalp_open_disabled() -> bool:
    try:
        from backend.config.settings import SCALP_OPEN_DISABLED
        return bool(SCALP_OPEN_DISABLED)
    except Exception:
        return str(os.getenv("SCALP_OPEN_DISABLED", "true")).strip().lower() in (
            "1", "true", "yes", "on",
        )
