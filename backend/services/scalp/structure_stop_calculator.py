"""StructureStopCalculator — 结构止损：SL 必须在 swing low/high 外侧。"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

import pandas as pd

from backend.config.settings import SCALP_STRUCTURE_SL_BUFFER_PCT

logger = logging.getLogger(__name__)


class StructureStopCalculator:
    """ATR 止损与 5m 结构 swing 取更宽一侧。"""

    def swing_levels(self, klines) -> Tuple[float, float, float]:
        """返回 (swing_low, swing_high, range_position)。"""
        if klines is None:
            return 0.0, 0.0, 0.5
        try:
            df = klines if isinstance(klines, pd.DataFrame) else pd.DataFrame(klines)
        except Exception:
            return 0.0, 0.0, 0.5
        if df.empty or "low" not in df.columns or "high" not in df.columns:
            return 0.0, 0.0, 0.5

        lookback = min(48, len(df))
        window = df.tail(lookback)
        swing_low = float(window["low"].min())
        swing_high = float(window["high"].max())
        close = float(window["close"].iloc[-1]) if "close" in window.columns else swing_low
        span = swing_high - swing_low
        range_pos = (close - swing_low) / span if span > 0 else 0.5
        return swing_low, swing_high, max(0.0, min(1.0, range_pos))

    def compute_atr_pct(self, market_data: Dict[str, Any]) -> float:
        atr_pct = float(
            market_data.get("volatility_value", 0)
            or market_data.get("atr_pct", 0.015)
            or 0.015
        )
        # [2026-07-31 research] 下限 0.8%→1.2%，与 ranging_mr / TIER_SHORT_SL 对齐
        # [S6 2026-08-21] 夹幅放宽 [0.6%, 3.0%]（原 [1.2%, 2.0%] 压平学习值）
        return max(0.006, min(0.030, atr_pct * 1.0))

    def compute_sl_tp(
        self,
        market_data: Dict[str, Any],
        side: str = "long",
        entry: float = 0.0,
        swing_low: float = 0.0,
        swing_high: float = 0.0,
        buffer_pct: Optional[float] = None,
        market_aware: Optional[bool] = None,
        symbol: str = "",
    ) -> Tuple[float, float, float, float]:
        """返回 (sl_pct, tp_pct, sl_price, tp_price)。

        默认走行情出场管家（放宽夹幅，按 regime/ATR/结构分配）。
        market_aware=False：旧窄夹幅，供中长线结构止损与回滚测试。
        """
        use_ma = market_aware
        if use_ma is None:
            try:
                from backend.services.exit.market_aware_tpsl import planner_enabled
                use_ma = planner_enabled()
            except Exception:
                use_ma = True
        if use_ma:
            from backend.services.exit.market_aware_tpsl import plan_scalp_tp_sl
            atr_pct = self.compute_atr_pct(market_data or {})
            if swing_low <= 0 or swing_high <= 0:
                swing_low, swing_high, _ = self.swing_levels(
                    (market_data or {}).get("klines")
                )
            plan = plan_scalp_tp_sl(
                market_data,
                side=side,
                entry=entry,
                swing_low=swing_low,
                swing_high=swing_high,
                atr_pct=atr_pct,
                buffer_pct=buffer_pct,
                symbol=symbol,
            )
            return plan.sl_pct, plan.tp_pct, plan.sl_price, plan.tp_price
        return self._compute_sl_tp_legacy(
            market_data, side=side, entry=entry,
            swing_low=swing_low, swing_high=swing_high, buffer_pct=buffer_pct,
        )

    def _compute_sl_tp_legacy(
        self,
        market_data: Dict[str, Any],
        side: str = "long",
        entry: float = 0.0,
        swing_low: float = 0.0,
        swing_high: float = 0.0,
        buffer_pct: Optional[float] = None,
    ) -> Tuple[float, float, float, float]:
        """2026-08-23 窄夹幅路径（回滚 / 中长线原料）。"""
        buffer = buffer_pct if buffer_pct is not None else SCALP_STRUCTURE_SL_BUFFER_PCT
        atr_pct = self.compute_atr_pct(market_data)
        sl_pct = max(0.01, min(0.05, atr_pct * 1.5))

        price = entry or float(
            market_data.get("price", 0) or market_data.get("mark_price", 0) or 0
        )
        if price <= 0:
            return atr_pct, max(0.02, sl_pct * 2.5), 0.0, 0.0

        klines = market_data.get("klines")
        if swing_low <= 0 or swing_high <= 0:
            swing_low, swing_high, _ = self.swing_levels(klines)

        side_l = (side or "long").lower()
        if side_l in ("buy", "long"):
            atr_sl = price * (1 - atr_pct)
            struct_sl = swing_low * (1 - buffer) if swing_low > 0 else atr_sl
            sl_price = min(atr_sl, struct_sl) if struct_sl > 0 else atr_sl
            sl_pct = (price - sl_price) / price if price > 0 else atr_pct
        else:
            atr_sl = price * (1 + atr_pct)
            struct_sl = swing_high * (1 + buffer) if swing_high > 0 else atr_sl
            sl_price = max(atr_sl, struct_sl) if struct_sl > 0 else atr_sl
            sl_pct = (sl_price - price) / price if price > 0 else atr_pct

        import os as _os_ab
        _sl_min_p = float(_os_ab.getenv("SCALP_SL_MIN_PCT", "0.007") or 0.007)
        _sl_max_p = float(_os_ab.getenv("SCALP_SL_MAX_PCT", "0.0115") or 0.0115)
        _tp_min_p = float(_os_ab.getenv("SCALP_TP_MIN_PCT", "0.009") or 0.009)
        _tp_max_p = float(_os_ab.getenv("SCALP_TP_MAX_PCT", "0.015") or 0.015)
        _rr_p = float(_os_ab.getenv("SCALP_TP_SL_RR", "1.5") or 1.5)

        sl_pct = max(_sl_min_p, min(_sl_max_p, abs(sl_pct)))
        if side_l in ("buy", "long"):
            sl_price = price * (1 - sl_pct)
        else:
            sl_price = price * (1 + sl_pct)
        tp_pct = max(_tp_min_p, min(_tp_max_p, sl_pct * _rr_p))
        tp_price = price * (1 + tp_pct) if side_l in ("buy", "long") else price * (1 - tp_pct)
        return sl_pct, tp_pct, sl_price, tp_price


structure_stop_calculator = StructureStopCalculator()
