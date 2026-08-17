"""long_trend_v2 — 长线趋势策略 V2 实盘集成（设计 V2 的接线层）。

把 Phase A-E 产出的组件接到长线开仓/持仓路径：
- 入场闸：tier=long 时要求 trend_layer L1=up（多头单边，禁做空）。
- 每日管理：Chandelier 止损（周线 ATR）+ 结构破坏唯一退出 + 新高金字塔。

安全：env LONG_TREND_V2=1 才启用（默认关=完全不影响旧路径）。
启用后：
  1) execute_midlong_open 对 tier=long 加 L1=up 入场闸；
  2) _run_midlong_active_exit / midlong_position_manager 对 long 仓改走本模块，
     跳过旧的 bias 反转 / no_progress / 分档 TP / 15min 复查（那些是短中线口径）。

数据：L1 用**实盘交易所**的 1d K 线（不是 binance 回测源）——保证判的是当前市况。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

import pandas as pd

from backend.services.trend_layer import classify
from backend.services.long_tier_manager import weekly_atr, is_new_high

logger = logging.getLogger(__name__)


def long_v2_enabled() -> bool:
    import os
    return os.getenv("LONG_TREND_V2", "0").strip().lower() in ("1", "true", "yes", "on")


def _cfg_int(key: str, default: int) -> int:
    import os
    try:
        return int(float(os.environ.get(key, default)))
    except Exception:
        return default


def _cfg_float(key: str, default: float) -> float:
    import os
    try:
        return float(os.environ.get(key, default))
    except Exception:
        return default


def _cfg_bool(key: str, default: bool) -> bool:
    import os
    return str(os.environ.get(key, "true" if default else "false")).strip().lower() in ("1", "true", "yes", "on")


def _l1_up(c: Dict[str, Any]) -> bool:
    """L1 是否 up（用可配置阈值 LONG_V2_L1_UP_SCORE，默认 3，替代硬编码 ±3）。"""
    return float(c.get("score") or 0.0) >= _cfg_int("LONG_V2_L1_UP_SCORE", 3)


def _live_1d(symbol: str, limit: int = 1200) -> Optional[pd.DataFrame]:
    """实盘交易所 1d K 线（决策用途，active_exchange 同源）。"""
    try:
        from backend.services.kline_data_service import kline_service
        rows = kline_service.get_klines_from_db(symbol.upper(), "1d", limit)
        if not rows or len(rows) < 260:
            return None
        df = pd.DataFrame(rows)
        for c in ("open", "high", "low", "close"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        return df.dropna(subset=["close", "high", "low"]).reset_index(drop=True)
    except Exception as e:
        logger.debug("[LongTrendV2] 1d 加载失败 %s: %s", symbol, e)
        return None


def entry_gate(symbol: str, action: str, market_summary: Optional[dict] = None) -> Tuple[bool, str]:
    """长线入场闸：只允许 L1=up 的多头；空头一律禁；数据不足/非 up 禁开。

    返回 (allowed, reason)。
    """
    if not long_v2_enabled():
        return True, ""
    act = str(action or "").lower()
    if act == "sell":
        return False, "long_trend_v2 多头单边：禁做空"
    if act != "buy":
        return False, f"long_trend_v2 非买信号({act})"
    df = _live_1d(symbol)
    if df is None:
        return False, "long_trend_v2 1d 数据不足(<260根)"
    c = classify(df)
    if not _l1_up(c):
        return False, f"long_trend_v2 L1={c['state']}(score={c['score']})，非 up 禁开"
    return True, f"long_trend_v2 L1=up(score={c['score']}) 放行"


def entry_signal(symbol: str, market_summary: Optional[dict] = None) -> Dict[str, Any]:
    """V2 长线入场信号（规则化，替代 LLM thesis 的 long 开仓决策）。

    L1=up 且数据充足 → should_open=True/action=buy；否则 hold。
    返回字段与下游 MLTO/TrendAgent 消费方对齐：
      should_open / action / direction / score(0~100) / hold_reason /
      suggested_sl_pct(Chandelier 2×1w ATR) / reason。
    """
    sym = str(symbol or "").upper()
    if not long_v2_enabled():
        return {"should_open": False, "action": "hold", "direction": "neutral",
                "score": 0, "hold_reason": "v2_disabled", "suggested_sl_pct": 0.08,
                "reason": ""}
    df = _live_1d(sym)
    if df is None:
        return {"should_open": False, "action": "hold", "direction": "neutral",
                "score": 0, "hold_reason": "1d 数据不足(<260根)",
                "suggested_sl_pct": 0.08, "reason": ""}
    c = classify(df)
    score_raw = float(c.get("score") or 0.0)
    if not _l1_up(c):
        return {"should_open": False, "action": "hold", "direction": "neutral",
                "score": 0, "hold_reason": f"L1={c['state']}(score={score_raw}) 非 up",
                "suggested_sl_pct": 0.08, "reason": ""}
    # L1=up → 规则化 buy（多头单边）。SL 用 Chandelier 初值：entry - 2×1w ATR。
    close = float(c.get("close") or 0.0)
    if close <= 0:
        close = float(df["close"].iloc[-1])
    atr_w = 0.0
    try:
        atr_w = float(weekly_atr(df).iloc[-1])
    except Exception:
        atr_w = 0.0
    sl_pct = 0.08
    if atr_w and atr_w > 0 and close > 0:
        sl_pct = max(0.02, min(0.20, (_cfg_float("LONG_V2_CHANDELIER_ATR", 2.0) * atr_w) / close))
    conf = max(50, min(95, 50 + int(round(score_raw * 10))))
    return {
        "should_open": True,
        "action": "buy",
        "direction": "long",
        "score": conf,
        "hold_reason": "",
        "suggested_sl_pct": round(sl_pct, 4),
        "reason": f"L1=up(score={score_raw}) 规则化入场，SL=2×1wATR({sl_pct * 100:.1f}%)",
    }


def manage_long_position(
    db, *, account_id: int, position: Dict[str, Any],
    market_summary: Optional[dict] = None,
) -> Dict[str, Any]:
    """长线仓每日管理（替代旧 bias 反转/no_progress/分档 TP/15min 复查）。

    position: paper_positions 的 dict（含 id/symbol/side/entry_price/mark_price/
              opened_at/unrealized_pnl/margin/sl_price/tp_price）。

    返回 {"action": hold/tighten_sl/add/close, "reason": ..., "new_sl": ...}。
    """
    if not long_v2_enabled():
        return {"action": "hold", "reason": "v2 未启用"}

    sym = str(position.get("symbol") or "").upper()
    side = str(position.get("side") or "").lower()
    if side != "long":
        return {"action": "close", "reason": "long_trend_v2 多头单边：空仓平掉"}

    df = _live_1d(sym)
    if df is None:
        return {"action": "hold", "reason": "1d 数据不足，跳过"}

    c = classify(df)
    atr_w = weekly_atr(df).iloc[-1]
    if atr_w is None or float(atr_w) <= 0:
        return {"action": "hold", "reason": "ATR(1w) 不可用"}

    close_now = float(df["close"].iloc[-1])
    entry = float(position.get("entry_price") or 0)
    mult = _cfg_float("LONG_V2_CHANDELIER_ATR", 2.0)
    init_stop = entry - mult * float(atr_w)

    # 结构破坏（唯一主动退出）
    if not _l1_up(c):
        return {"action": "close", "reason": f"结构破坏(L1={c['state']},score={c['score']})"}

    # Chandelier：入场后最高收盘 - mult×ATR(1w)
    # 近似：用 1d 序列里自开仓以来的最高收盘（opened_at 截断），再与 entry 取大。
    opened = position.get("opened_at")
    closes = df["close"].astype(float)
    highest = entry
    if opened is not None:
        try:
            ts = pd.Timestamp(opened)
            if "timestamp" in df.columns:
                closes_since = closes[df["timestamp"].astype(str) >= str(ts.date())]
            else:
                closes_since = closes
            if len(closes_since):
                highest = max(entry, float(closes_since.max()))
        except Exception:
            highest = max(entry, float(closes.max()))
    chand_stop = max(init_stop, highest - mult * float(atr_w))

    if close_now < chand_stop:
        return {"action": "close", "reason": f"Chandelier止损(close={close_now:.2f}<stop={chand_stop:.2f})"}

    # 新高金字塔（创 60 日新高且浮盈 ≥1R）
    try:
        _new_high = bool(is_new_high(df).iloc[-1])
    except Exception:
        _new_high = False
    r_mult = (close_now - entry) / (mult * float(atr_w)) if entry > 0 else 0.0
    _pyr_on = _cfg_bool("LONG_V2_PYRAMID_ENABLED", True)
    _pyr_r = _cfg_float("LONG_V2_PYRAMID_R", 1.0)
    if _pyr_on and _new_high and r_mult >= _pyr_r:
        return {"action": "add", "ratio": _cfg_float("LONG_V2_PYRAMID_RATIO", 0.25), "reason": f"新高加仓(r={r_mult:.2f}R)"}

    # 止损上移：当前 SL 低于 Chandelier 则收紧
    cur_sl = float(position.get("sl_price") or 0)
    if cur_sl > 0 and chand_stop > cur_sl:
        return {"action": "tighten_sl", "reason": f"Chandelier上移 SL→{chand_stop:.4f}",
                "new_sl": round(chand_stop, 6)}

    return {"action": "hold", "reason": f"持有(L1=up, chand_stop={chand_stop:.4f})"}
