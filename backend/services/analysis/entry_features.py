# -*- coding: utf-8 -*-
"""[轮112 2026-09-19] 入场时特征留档（**纯观测**，不参与任何判定）。

## 为什么需要它

轮110/111 想把"入场质量"做成可分析的门槛时，两次都被**特征缺失**挡住：

  · `open_metadata` 里没有波动率/ATR 字段 ⇒ 无法回答"止损是不是落在噪音带内"；
  · 30 天前的 1h/4h K 线虽然取得到，但**必须事后重算**，且入场那一刻的 regime、
    与上次同币平仓的间隔、AI 置信度都散在别的表里（且只有 39% 的仓位连得上 thesis）。

结论：**先把入场那一刻的特征落到 `open_metadata.entry_features`**，
以后的归因直接用库内数据，不必再依赖"事后重算 + 跨表 join"。

纯观测：本模块只读，异常一律吞掉（由调用方 try/except 兜底），不改变任何交易行为。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _atr_pct(symbol: str, tf: str, period: int = 14, count: int = 120) -> Optional[float]:
    """ATR(period) ÷ close，单位 %。取不到返回 None。"""
    try:
        import numpy as np
        from backend.services.data_center import data_center

        res = data_center.get_klines(symbol, tf, count=count)
        df = res.to_dataframe() if hasattr(res, "to_dataframe") else None
        if df is None or not len(df) or not {"high", "low", "close"} <= set(df.columns):
            return None
        h = df["high"].astype(float)
        l = df["low"].astype(float)
        c = df["close"].astype(float)
        pc = c.shift(1)
        tr = np.maximum(np.maximum(h - l, (h - pc).abs()), (l - pc).abs())
        atr = float(tr.rolling(period).mean().iloc[-1])
        last = float(c.iloc[-1])
        if not (atr > 0 and last > 0):
            return None
        return round(atr / last * 100.0, 4)
    except Exception as exc:      # noqa: BLE001
        logger.debug("[EntryFeatures] %s %s ATR 取数跳过: %s", symbol, tf, exc)
        return None


def entry_feature_snapshot(
    symbol: str,
    *,
    tier: str = "mid",
    sl_pct: Optional[float] = None,
    market_summary: Optional[Dict[str, Any]] = None,
    db=None,
    conviction: Optional[float] = None,
    dip: bool = False,
) -> Dict[str, Any]:
    """组装入场特征字典（全部可失败，缺项就是缺项，不编造）。

    返回键：
      atr_1h_pct / atr_4h_pct / atr_1d_pct —— 入场时的波动率尺度（%）
      sl_pct                               —— 本次止损距离（%）
      noise_cover_x                        —— sl_pct ÷ ATR(1h)%（"止损覆盖几个 1h 噪音带"）
      regime                               —— 入场时的 regime（取自 market_summary）
      price                                —— 入场参考价
      reentry_gap_h                        —— 距上次同币同 tier 平仓的小时数（无前次 = None）
      conviction                           —— AI 自评信心（θ 传入时）
    """
    sym_u = str(symbol or "").upper()
    out: Dict[str, Any] = {"tier": str(tier or ""), "symbol": sym_u}
    if dip:
        out["_source"] = "dip_probe"

    try:
        a1 = _atr_pct(sym_u, "1h")
        a4 = _atr_pct(sym_u, "4h")
        a1d = _atr_pct(sym_u, "1d")
    except Exception as exc:      # noqa: BLE001 — 本模块承诺"异常一律吞掉"
        logger.debug("[EntryFeatures] %s ATR 段整体跳过: %s", sym_u, exc)
        a1 = a4 = a1d = None
    for k, v in (("atr_1h_pct", a1), ("atr_4h_pct", a4), ("atr_1d_pct", a1d)):
        if v is not None:
            out[k] = v

    try:
        _sl = float(sl_pct) if sl_pct is not None else None
    except (TypeError, ValueError):
        _sl = None
    if _sl is not None and _sl > 0:
        # 入场端 sl_pct 常以小数传入（0.015 = 1.5%）→ 统一成百分数再比
        _sl_pct = _sl * 100.0 if _sl < 1.0 else _sl
        out["sl_pct"] = round(_sl_pct, 4)
        if a1:
            out["noise_cover_x"] = round(_sl_pct / a1, 3)

    ms = market_summary if isinstance(market_summary, dict) else {}
    _row = ms.get(sym_u) or ms.get(sym_u.lower()) or {}
    if isinstance(_row, dict):
        for src_key, dst_key in (("regime", "regime"), ("market_cycle", "regime"),
                                 ("current_price", "price"), ("price", "price"),
                                 ("volatility_value", "volatility_value")):
            if dst_key not in out and _row.get(src_key) is not None:
                out[dst_key] = _row.get(src_key)

    if db is not None:
        try:
            # 距上次同币同 tier 平仓的小时数（无前次 = None）。
            # 直接查库而不是复用冷却 helper：那个 helper 的返回文本在
            # "冷却内/外"两种情形下格式不同，靠解析文本取数太脆。
            from datetime import datetime as _dt

            from sqlalchemy import text as _sa_text

            _row = db.execute(_sa_text(
                "SELECT MAX(closed_at) FROM paper_positions "
                "WHERE UPPER(symbol) = :s AND status = 'closed' "
                "  AND COALESCE(timeframe_tier, '') = :t"
            ), {"s": sym_u, "t": str(tier or "mid")}).first()
            _last = _row[0] if _row else None
            out["reentry_gap_h"] = (
                round((_dt.now() - _last).total_seconds() / 3600.0, 3) if _last else None
            )
        except Exception as exc:      # noqa: BLE001
            logger.debug("[EntryFeatures] %s 冷却间隔读取跳过: %s", sym_u, exc)

    if conviction is not None:
        try:
            out["conviction"] = float(conviction)
        except (TypeError, ValueError):
            pass
    return out
