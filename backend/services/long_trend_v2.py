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

import numpy as np

import pandas as pd

from backend.services.trend_layer import classify
from backend.services.long_tier_manager import weekly_atr, weekly_atr_causal, is_new_high

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


def _initial_fill() -> float:
    """[A4] 首仓比例（设计 §4.3.1：试探仓 50%，24h 内补足到 100%）。"""
    _f = _cfg_float("LONG_V2_INITIAL_FILL", 0.5)
    return max(0.2, min(1.0, _f))


def _today_utc_midnight_ts() -> float:
    """今天 00:00 UTC 的 epoch 秒（判断最后一根 1d bar 是否已收盘用）。"""
    import datetime
    try:
        t = datetime.datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        return float(t.timestamp())
    except Exception:
        return 0.0


def _bar_epoch_ts(v) -> Optional[float]:
    """bar 时间戳统一转 epoch 秒（兼容数字/字符串/日期格式）。"""
    if v is None:
        return None
    try:
        if isinstance(v, (int, float)):
            f = float(v)
            return f if f > 1e9 else f  # 秒级 epoch
        s = str(v)
        import datetime
        if s.isdigit() and len(s) >= 10:
            return float(s)
        return float(pd.Timestamp(s).timestamp())
    except Exception:
        return None


def _live_1d(symbol: str, limit: int = 1200) -> Optional[pd.DataFrame]:
    """实盘交易所 1d K 线（决策用途，active_exchange 同源）。

    [A1 2026-08-19 日频修复] 丢弃最后一根未收盘 bar（ts >= 今天 00:00 UTC 的 bar
    仍在盘中实时跳动）——classify/Chandelier 只用已收盘数据，盘中价格不改变决策，
    只在新的已收盘 bar 出现后更新（配合 _get_l1_classification 缓存）。
    """
    try:
        from backend.services.kline_data_service import kline_service
        rows = kline_service.get_klines_from_db(symbol.upper(), "1d", limit)
        if not rows or len(rows) < 260:
            return None
        df = pd.DataFrame(rows)
        for c in ("open", "high", "low", "close"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["close", "high", "low"]).reset_index(drop=True)
        # 丢弃未收盘 bar：最后一根的 ts >= 今天 00:00 UTC
        if "timestamp" in df.columns and len(df) > 0:
            last_ts = _bar_epoch_ts(df["timestamp"].iloc[-1])
            today = _today_utc_midnight_ts()
            if last_ts is not None and today > 0 and last_ts >= today:
                df = df.iloc[:-1].reset_index(drop=True)
        if len(df) < 260:
            return None
        return df
    except Exception as e:
        logger.debug("[LongTrendV2] 1d 加载失败 %s: %s", symbol, e)
        return None


# [A1 日频缓存] 键=symbol，值=(最后一根已收盘 bar ts, classify 结果)。
# 同一已收盘 bar 内（240s tick 反复调用）直接返回缓存，不重算。
_L1_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def _get_l1_classification(symbol: str):
    """取「已收盘 1d 数据 + L1 分类（带缓存）」：返回 (df, classification) 或 (None, None)。"""
    sym = str(symbol or "").upper()
    df = _live_1d(sym)
    if df is None:
        return None, None
    last_ts = _bar_epoch_ts(df["timestamp"].iloc[-1]) if "timestamp" in df.columns else 0.0
    hit = _L1_CACHE.get(sym)
    if hit is not None and hit[0] == last_ts:
        return df, hit[1]
    c = classify(df)
    _L1_CACHE[sym] = (last_ts or 0.0, c)
    return df, c


def _entry_idx_for(df: pd.DataFrame, opened_at) -> int:
    """从已收盘 1d 序列定位入场 bar 索引（第一个 ts >= 开仓日 00:00 的 bar）。"""
    try:
        if opened_at is None or "timestamp" not in df.columns or len(df) == 0:
            return 0
        _day0 = float(pd.Timestamp(opened_at).replace(
            hour=0, minute=0, second=0, microsecond=0).timestamp())
        for i, v in enumerate(df["timestamp"]):
            t = _bar_epoch_ts(v)
            if t is not None and t >= _day0:
                return int(i)
        return len(df) - 1
    except Exception:
        return 0


def _topup_done(position: Dict[str, Any]) -> bool:
    """首仓补足是否已完成（exit_state_json.topup_done 标记）。"""
    try:
        import json as _json
        st = position.get("exit_state_json")
        if isinstance(st, str) and st:
            d = _json.loads(st)
            if isinstance(d, dict):
                return bool(d.get("topup_done"))
    except Exception:
        pass
    return False


def _pyramid_batch(position: Dict[str, Any]) -> int:
    """已完成的 pyramid 加仓批次数（存于 exit_state_json.pyramid_batch，add 成功后由执行方写入）。"""
    try:
        import json as _json
        st = position.get("exit_state_json")
        if isinstance(st, str) and st:
            d = _json.loads(st)
            if isinstance(d, dict):
                return max(0, int(d.get("pyramid_batch") or 0))
    except Exception:
        pass
    return 0


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
    df, c = _get_l1_classification(symbol)
    if df is None or c is None:
        return False, "long_trend_v2 1d 数据不足(<260根)"
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
                "size_hint_mult": _initial_fill(), "reason": ""}
    df, c = _get_l1_classification(sym)
    if df is None or c is None:
        return {"should_open": False, "action": "hold", "direction": "neutral",
                "score": 0, "hold_reason": "1d 数据不足(<260根)",
                "suggested_sl_pct": 0.08, "size_hint_mult": _initial_fill(), "reason": ""}
    score_raw = float(c.get("score") or 0.0)
    if not _l1_up(c):
        return {"should_open": False, "action": "hold", "direction": "neutral",
                "score": 0, "hold_reason": f"L1={c['state']}(score={score_raw}) 非 up",
                "suggested_sl_pct": 0.08, "size_hint_mult": _initial_fill(), "reason": ""}
    # [A4] 尖峰过滤：pullback_z |z|>3σ 的单日暴涨 bar 不追（设计 §4.2）
    try:
        from backend.services.entry_timing import timing_features
        _feat = timing_features(df)
        _pz = float(_feat["pullback_z"].iloc[-1])
        if np.isfinite(_pz) and abs(_pz) > 3.0:
            return {"should_open": False, "action": "hold", "direction": "neutral",
                    "score": 0, "hold_reason": f"尖峰过滤(pullback_z={_pz:.1f}σ)不追",
                    "suggested_sl_pct": 0.08, "size_hint_mult": _initial_fill(), "reason": ""}
    except Exception:
        pass
    # L1=up → 规则化 buy（多头单边）。SL 用 Chandelier 初值：entry - 2×1w ATR。
    close = float(c.get("close") or 0.0)
    if close <= 0:
        close = float(df["close"].iloc[-1])
    atr_w = 0.0
    try:
        atr_w = float(weekly_atr_causal(df).iloc[-1])
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
        # [A4] 首仓 50%：试探仓，满 24h 且 L1 仍 up 未触止损由 manage 补足
        "size_hint_mult": _initial_fill(),
        "reason": f"L1=up(score={score_raw}) 规则化入场，SL=2×1wATR({sl_pct * 100:.1f}%)，首仓{_initial_fill() * 100:.0f}%",
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

    df, c = _get_l1_classification(sym)
    if df is None or c is None:
        return {"action": "hold", "reason": "1d 数据不足，跳过"}
    atr_series = weekly_atr_causal(df)  # [A7] 因果版（上一完整周 ATR，无前视）
    atr_w = float(atr_series.iloc[-1])
    if not atr_w or atr_w <= 0:
        return {"action": "hold", "reason": "ATR(1w) 不可用"}

    close_now = float(df["close"].iloc[-1])
    entry = float(position.get("entry_price") or 0)
    if entry <= 0:
        return {"action": "hold", "reason": "入场价缺失"}
    mult = _cfg_float("LONG_V2_CHANDELIER_ATR", 2.0)

    # [A2 同核] Chandelier 用回测同款 chandelier_long_stop（全序列、从入场 bar 起、真实入场价），
    # 删除原 opened_at 截断近似实现。决策统一走 decide_long（回测/实盘同核唯一函数）。
    from backend.services.long_tier_manager import chandelier_long_stop, decide_long
    _entry_idx = _entry_idx_for(df, position.get("opened_at"))
    _stops = chandelier_long_stop(
        df["close"].astype(float), atr_series,
        mult=mult, entry_idx=_entry_idx, entry_price=entry,
    )
    stop = float(_stops.iloc[-1]) if (_stops is not None and pd.notna(_stops.iloc[-1])) else None

    _cur_sl = float(position.get("sl_price") or 0)
    cur_sl = _cur_sl if _cur_sl > 0 else None
    r_mult = (close_now - entry) / (mult * atr_w)
    # [修复] is_new_high 期望 high 序列；原实现误传整个 df 使新高判定异常回退 False，
    # 导致实盘金字塔加仓从未触发。改为传 high 列。
    try:
        _new_high = bool(is_new_high(df["high"].astype(float)).iloc[-1])
    except Exception:
        _new_high = False

    # [A3] 峰值 R / 持有天数 / 相对峰值回撤（供 decide_long 兜底判定）
    peak_r = None
    try:
        _risk_pct = (mult * atr_w) / entry
        if _risk_pct > 0:
            peak_r = float(position.get("peak_pnl_pct") or 0) / _risk_pct
    except Exception:
        pass
    hold_days = None
    try:
        _opened = position.get("opened_at")
        if _opened is not None:
            hold_days = float((pd.Timestamp.utcnow() - pd.Timestamp(_opened)).total_seconds()) / 86400.0
    except Exception:
        pass
    drawdown = None
    try:
        _margin = float(position.get("margin") or 0)
        _upnl = float(position.get("unrealized_pnl") or 0)
        _cur_pct = (_upnl / _margin) if _margin > 0 else 0.0
        _peak_pct = float(position.get("peak_pnl_pct") or 0) or 0.0
        if _peak_pct > -1.0:
            drawdown = max(0.0, 1.0 - (1.0 + _cur_pct) / (1.0 + _peak_pct))
    except Exception:
        pass
    pyr_batch = _pyramid_batch(position)
    if not _cfg_bool("LONG_V2_PYRAMID_ENABLED", True):
        pyr_batch = 99  # 金字塔禁用：批次打满禁止 add

    # [A4] 结构目标（trend_layer.classify 的 target：h60+ATR 投影）
    _target = None
    try:
        _t = c.get("target")
        _target = float(_t) if _t is not None else None
    except Exception:
        pass
    # [A4] 首仓补足判定：env 首仓比例 <1 且未补足（exit_state_json.topup_done 标记）
    _fill = _initial_fill()
    needs_topup = (_fill < 1.0) and not _topup_done(position)
    topup_ratio = 1.0 - _fill

    return decide_long(
        l1_state=str(c.get("state") or "sideways"),
        close=close_now, stop=stop, new_high=_new_high, r_multiple=r_mult,
        in_position=True, cur_sl=cur_sl, peak_r=peak_r, hold_days=hold_days,
        drawdown_pct=drawdown, pyr_batch=pyr_batch,
        target=_target, needs_topup=needs_topup, topup_ratio=topup_ratio,
    )
