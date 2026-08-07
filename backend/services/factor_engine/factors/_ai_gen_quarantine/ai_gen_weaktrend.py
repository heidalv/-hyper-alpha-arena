"""AI因子: 弱趋势强度因子 | 置信:70% | 基于ADX指标，当ADX低于20时市场无趋势，易发生亏损模式中的'unknown'状态。因子值为负表示趋势弱，应谨慎。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class WeakTrendADX(BaseFactor):
    """基于ADX指标，当ADX低于20时市场无趋势，易发生亏损模式中的'unknown'状态。因子值为负表示趋势弱，应谨慎。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_weaktrend",
            name="WeakTrendADX",
            display_name="弱趋势强度因子",
            description="基于ADX指标，当ADX低于20时市场无趋势，易发生亏损模式中的'unknown'状态。因子值为负表示趋势弱，应谨慎。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

        def calculate(self, data):
            import numpy as np
            high = data['high']
            low = data['low']
            close = data['close']
            # 计算ADX
            tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
            atr = tr.rolling(14).mean()
            # 方向变动
            up = high - high.shift(1)
            down = low.shift(1) - low
            pos_dm = np.where((up > down) & (up > 0), up, 0)
            neg_dm = np.where((down > up) & (down > 0), down, 0)
            avg_pos = pos_dm.rolling(14).mean()
            avg_neg = neg_dm.rolling(14).mean()
            di_pos = 100 * avg_pos / atr
            di_neg = 100 * avg_neg / atr
            dx = 100 * np.abs(di_pos - di_neg) / (di_pos + di_neg + 1e-9)
            adx = dx.rolling(14).mean()
            # 弱趋势：ADX<20 => factor接近-1    ADX>40 => 接近1
            raw = (adx - 20) / 20.0
            result = np.clip(raw, -1, 1)
            return -result  # 返回负值表示弱趋势时看空因子
