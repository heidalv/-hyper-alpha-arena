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
        return max(0.012, min(0.020, atr_pct * 1.0))

    def compute_sl_tp(
        self,
        market_data: Dict[str, Any],
        side: str = "long",
        entry: float = 0.0,
        swing_low: float = 0.0,
        swing_high: float = 0.0,
        buffer_pct: Optional[float] = None,
    ) -> Tuple[float, float, float, float]:
        """返回 (sl_pct, tp_pct, sl_price, tp_price)。

        sl_pct/tp_pct 是【价格波动百分比】。逐仓模式下保证金盈亏=价格%×杠杆。
        行业实践（参考 Altrady/ATR回测研究/学术论文）：
        - 日内交易(Day Trading)：SL=1.5-2×ATR，TP=SL的2-3倍（盈亏比1:2~1:3）
        - ATR自适应：波动大时 sl/tp 自动放宽，波动小时收紧
        - 不用固定百分比，用 ATR 倍数（业界标准做法）
        """
        buffer = buffer_pct if buffer_pct is not None else SCALP_STRUCTURE_SL_BUFFER_PCT
        atr_pct = self.compute_atr_pct(market_data)
        # ATR 倍数法（行业标准）：sl=1.5×ATR% 作为初始值，带 1%-5% 上下限保护。
        sl_pct = max(0.01, min(0.05, atr_pct * 1.5))

        price = entry or float(
            market_data.get("price", 0) or market_data.get("mark_price", 0) or 0
        )
        if price <= 0:
            # 无价格时给一个保守 tp 兜底（盈亏比≈2.5）
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

        # ── 短线 TP/SL：regime 自适应 ──
        # [P1-1 2026-07-30] 固定RR=2.5改为随regime切换
        # 震荡市薄利多开(RR=1.5, SL宽)，趋势市追势(RR=2.5, SL紧)，崩盘不开
        _regime = ""
        try:
            _regime_data = market_data.get("regime") or {}
            _regime = (_regime_data.get("name") or _regime_data.get("regime") or "").lower() if isinstance(_regime_data, dict) else str(_regime_data or "").lower()
        except Exception:
            pass

        if _regime == "ranging":
            _rr_mult = 1.5; _sl_min, _sl_max = 0.012, 0.020
        elif _regime == "trending":
            # [2026-07-31 research] trending SL 下限 0.8%→1.2%（对齐 TIER_SHORT_SL）
            _rr_mult = 2.5; _sl_min, _sl_max = 0.012, 0.018
        else:
            _rr_mult = 2.0; _sl_min, _sl_max = 0.012, 0.018  # 默认(含volatile/crash/unknown)

        sl_pct = max(_sl_min, min(_sl_max, abs(sl_pct)))
        if side_l in ("buy", "long"):
            sl_price = price * (1 - sl_pct)
        else:
            sl_price = price * (1 + sl_pct)
        tp_pct = max(0.015, min(0.04, sl_pct * _rr_mult))
        tp_price = price * (1 + tp_pct) if side_l in ("buy", "long") else price * (1 - tp_pct)
        return sl_pct, tp_pct, sl_price, tp_price


structure_stop_calculator = StructureStopCalculator()
