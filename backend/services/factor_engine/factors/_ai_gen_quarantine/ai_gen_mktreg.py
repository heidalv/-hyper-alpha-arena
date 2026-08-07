"""AI因子: 市场状态识别因子 | 置信:65% | 结合布林带宽度（波动率）和ADX（趋势强度）判断市场状态。在趋势明确且波动率适中时看多，在震荡高波动或低波动时看空。旨在规避regime=unknown的模糊区间。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MarketRegimeDetector(BaseFactor):
    """结合布林带宽度（波动率）和ADX（趋势强度）判断市场状态。在趋势明确且波动率适中时看多，在震荡高波动或低波动时看空。旨在规避regime=unknown的模糊区间。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mktreg",
            name="MarketRegimeDetector",
            display_name="市场状态识别因子",
            description="结合布林带宽度（波动率）和ADX（趋势强度）判断市场状态。在趋势明确且波动率适中时看多，在震荡高波动或低波动时看空。旨在规避regime=unknown的模糊区间。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        high = data['high']
        low = data['low']
        # 布林带宽度 (20,2)
        ma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        bb_width = (2 * std20) / ma20  # 相对宽度
        # ADX (14)
        tr = pd.concat([high - low, abs(high - close.shift(1)), abs(low - close.shift(1))], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
        plus_di = 100 * (pd.Series(plus_dm).rolling(14).mean() / atr)
        minus_di = 100 * (pd.Series(minus_dm).rolling(14).mean() / atr)
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
        adx = dx.rolling(14).mean() / 100  # 归一化到0-1
        # 综合信号：希望趋势强(adx>0.3)且波动率适中(bb_width在0.05-0.2之间)
        trend_ok = (adx > 0.3).astype(float)
        vol_ok = ((bb_width > 0.05) & (bb_width < 0.2)).astype(float)
        signal = trend_ok * vol_ok - (1 - trend_ok) * (1 - vol_ok) * 0.5  # 模糊区负值
        result = np.clip(signal, -1, 1)
        return result
