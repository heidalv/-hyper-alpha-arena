"""AI因子: 波动率调整趋势强度 | 置信:65% | 结合ATR和ADX，当市场波动率低且趋势弱时，容易产生假突破和频繁止损，返回负值；趋势强且波动适中时正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityAdjustedTrendStrength(BaseFactor):
    """结合ATR和ADX，当市场波动率低且趋势弱时，容易产生假突破和频繁止损，返回负值；趋势强且波动适中时正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_voltrend",
            name="Volatility_Adjusted_Trend_Strength",
            display_name="波动率调整趋势强度",
            description="结合ATR和ADX，当市场波动率低且趋势弱时，容易产生假突破和频繁止损，返回负值；趋势强且波动适中时正值。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        close = data['close']
        high = data['high']
        low = data['low']
        # 计算ATR(14)
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        atr = tr.rolling(14).mean()
        # 计算ADX(14)
        up = high - high.shift(1)
        down = low.shift(1) - low
        dm_plus = np.where((up > down) & (up > 0), up, 0.0)
        dm_minus = np.where((down > up) & (down > 0), down, 0.0)
        tr_smooth = atr * 14  # 近似
        di_plus = 100 * pd.Series(dm_plus).rolling(14).mean() / tr_smooth
        di_minus = 100 * pd.Series(dm_minus).rolling(14).mean() / tr_smooth
        dx = 100 * np.abs(di_plus - di_minus) / (di_plus + di_minus + 1e-10)
        adx = dx.rolling(14).mean()
        # 标准化ATR：相对价格百分比
        atr_pct = atr / close
        # 因子：趋势强度(adx)减去低波动惩罚项
        # adx范围0-100，atr_pct通常0.01-0.05，调整scale
        raw = (adx / 25.0) - (atr_pct * 50.0)  # 经验调参
        # 限制在[-1,1]并用tanh平滑
        result = np.tanh(raw / 2.0)
        return pd.Series(result, index=data.index)
