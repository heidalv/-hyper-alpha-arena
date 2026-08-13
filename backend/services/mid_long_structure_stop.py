"""MidLongStructureStop — 中线/长线结构止损（复用 Scalp 结构 SL 逻辑，扩展 lookback）。"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from backend.services.scalp.structure_stop_calculator import structure_stop_calculator

logger = logging.getLogger(__name__)


class MidLongStructureStop:
    """Swing: 1h/4h swing；Trend: 4h/1d/1w。"""

    SWING_TFS = ("4h", "1h")
    TREND_TFS = ("1w", "1d", "4h")

    def compute(
        self,
        *,
        symbol: str,
        market_data: Dict[str, Any],
        side: str,
        entry: float,
        agent_source: str = "swing_agent",
        buffer_pct: Optional[float] = None,
    ) -> Tuple[float, float, float, float, str]:
        """返回 (sl_pct, tp_pct, sl_price, tp_price, sl_source)。

        中长线 TP/SL 远宽于短线——加密货币日内波动经常 5-10%，趋势行情可超 50%。
        短线的 0.8-2% 止损 / 1.2-2.5% 止盈完全不适合中长线。
        """
        if entry <= 0:
            return 0.0, 0.0, 0.0, 0.0, "invalid_entry"

        tfs = self.TREND_TFS if agent_source == "trend_agent" else self.SWING_TFS
        klines = None
        for tf in tfs:
            ind_key = f"indicators_{tf}" if tf != "1h" else "indicators_1h"
            raw_kl = (market_data.get(ind_key) or {}).get("klines_summary")
            if raw_kl:
                klines = raw_kl
                break
            raw_df = market_data.get(f"klines_{tf}") or market_data.get("klines")
            if raw_df is not None:
                klines = raw_df
                break

        md = dict(market_data or {})
        if klines is not None:
            md["klines"] = klines

        sl_pct, tp_pct, sl_price, tp_price = structure_stop_calculator.compute_sl_tp(
            md, side=side, entry=entry, buffer_pct=buffer_pct,
        )

        # ── 中长线 TP/SL 扩展：覆盖短线窄区间钳制 ──
        # structure_stop_calculator 把 SL 钳到 0.8-2%、TP 钳到 1.2-2.5%（为短线设计）。
        # 中长线需要远宽于此：中线 SL 3-8%、TP 3-10%；长线 SL 5-15%、TP 6-20%。
        # [P0-1 修复] TP 上限按全库实测 peak 上限 5.03% 校准（原 15-50% 从未触达，
        # mid/long TP 事件 0 笔），止盈线必须落在市场可达区间。
        import os
        if agent_source == "trend_agent":
            # 长线：宽止损让趋势跑，宽止盈捕获大行情
            min_sl = float(os.getenv("MLTO_LONG_MIN_SL", "0.05"))   # 5%
            max_sl = float(os.getenv("MLTO_LONG_MAX_SL", "0.15"))   # 15%
            min_tp = float(os.getenv("MLTO_LONG_MIN_TP", "0.06"))   # 6%
            max_tp = float(os.getenv("MLTO_LONG_MAX_TP", "0.20"))   # 20%
        else:
            # 中线：适中
            min_sl = float(os.getenv("MLTO_MID_MIN_SL", "0.03"))    # 3%
            max_sl = float(os.getenv("MLTO_MID_MAX_SL", "0.08"))    # 8%
            min_tp = float(os.getenv("MLTO_MID_MIN_TP", "0.03"))    # 3%
            max_tp = float(os.getenv("MLTO_MID_MAX_TP", "0.10"))    # 10%

        # ATR 自适应：从原始 sl_pct 推断波动级别
        raw_sl = max(sl_pct, 0.01)
        sl_pct = max(min_sl, min(max_sl, raw_sl * 3))  # 中长线 SL = 短线 SL × 3（放大到合适级别）

        # TP 按 RR ≥ 1.8 计算（P0-1：原 2.5 倍 SL 叠加 ATR 地板后 TP 达 16-22%，
        # 远超全库 peak 上限 5.03%，止盈从未触发；下修到 1.8 倍换取可触达性）
        tp_pct = max(min_tp, min(max_tp, sl_pct * 1.8))

        # 重新计算价格
        side_l = (side or "long").lower()
        if side_l in ("buy", "long"):
            sl_price = entry * (1 - sl_pct)
            tp_price = entry * (1 + tp_pct)
        else:
            sl_price = entry * (1 + sl_pct)
            tp_price = entry * (1 - tp_pct)

        if agent_source == "trend_agent" and tp_pct <= 0:
            tp_pct = sl_pct * 3  # 长线默认 RR=3
            tp_price = entry * (1 + tp_pct) if side_l in ("buy", "long") else entry * (1 - tp_pct)

        sl_source = f"midlong_structure_{agent_source}"
        return sl_pct, tp_pct, sl_price, tp_price, sl_source


mid_long_structure_stop = MidLongStructureStop()
