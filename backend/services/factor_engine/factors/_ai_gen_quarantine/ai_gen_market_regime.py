"""AI因子: 市场状态识别 | 置信:70% | 结合ADX趋势强度与波动率比率，识别市场处于强趋势还是震荡状态。当ADX>25且波动率（ATR/收盘价）处于近期低位时输出接近+1（趋势市），当ADX<20且波动率上升时输出接近-1（震荡市）。用于规避在未知状态下进行趋势或反转交易。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MarketRegimeIndicator(BaseFactor):
    """结合ADX趋势强度与波动率比率，识别市场处于强趋势还是震荡状态。当ADX>25且波动率（ATR/收盘价）处于近期低位时输出接近+1（趋势市），当ADX<20且波动率上升时输出接近-1（震荡市）。用于规避在未知状态下进行趋势或反转交易。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_market_regime",
            name="Market Regime Indicator",
            display_name="市场状态识别",
            description="结合ADX趋势强度与波动率比率，识别市场处于强趋势还是震荡状态。当ADX>25且波动率（ATR/收盘价）处于近期低位时输出接近+1（趋势市），当ADX<20且波动率上升时输出接近-1（震荡市）。用于规避在未知状态下进行趋势或反转交易。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 计算ADX
        high = data['high']
        low = data['low']
        close = data['close']
        period = 14
        # TR
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(period).mean()
        # +DM, -DM
        up_move = high - high.shift()
        down_move = low.shift() - low
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
        plus_di = 100 * pd.Series(plus_dm).rolling(period).mean() / atr
        minus_di = 100 * pd.Series(minus_dm).rolling(period).mean() / atr
        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
        adx = dx.rolling(period).mean() / 100
        # 波动率比率
        vol_ratio = (atr / close).rolling(20).rank(pct=True)
        # 结合信号：趋势时adx高且波动率低，震荡时adx低且波动率高
        regime = 2 * (adx - 0.5) + (vol_ratio - 0.5) * (-2)
        result = regime.clip(-1, 1)
        return result.fillna(0)
