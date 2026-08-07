"""AI因子: 市场状态清晰度 | 置信:65% | 结合ADX趋势强度与ATR波动率比率，识别市场处于趋势或震荡状态。ADX>25且当前ATR/长期ATR<1.2视为趋势清晰（正），否则为震荡/未知（负）。输出经tanh压缩至[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Market_Regime_Clarity(BaseFactor):
    """结合ADX趋势强度与ATR波动率比率，识别市场处于趋势或震荡状态。ADX>25且当前ATR/长期ATR<1.2视为趋势清晰（正），否则为震荡/未知（负）。输出经tanh压缩至[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_marketregime",
            name="Market Regime Clarity",
            display_name="市场状态清晰度",
            description="结合ADX趋势强度与ATR波动率比率，识别市场处于趋势或震荡状态。ADX>25且当前ATR/长期ATR<1.2视为趋势清晰（正），否则为震荡/未知（负）。输出经tanh压缩至[-1,1]。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        # ADX
        period = 14
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        atr = tr.rolling(period).mean()
        up = high - high.shift(1)
        down = low.shift(1) - low
        dm_plus = np.where((up > down) & (up > 0), up, 0.0)
        dm_minus = np.where((down > up) & (down > 0), down, 0.0)
        sma_dm_plus = pd.Series(dm_plus).rolling(period).mean()
        sma_dm_minus = pd.Series(dm_minus).rolling(period).mean()
        di_plus = 100 * sma_dm_plus / atr
        di_minus = 100 * sma_dm_minus / atr
        dx = 100 * np.abs(di_plus - di_minus) / (di_plus + di_minus + 1e-10)
        adx = dx.rolling(period).mean()
        # 波动率比
        atr_long = tr.rolling(period*3).mean()
        vol_ratio = atr / (atr_long + 1e-10)
        # 合成信号
        trend_score = np.where((adx > 25) & (vol_ratio < 1.2), 1.0, -1.0)
        # 平滑
        result = pd.Series(trend_score, index=close.index).rolling(3).mean().fillna(0)
        result = np.tanh(result * 0.5)
        return result
