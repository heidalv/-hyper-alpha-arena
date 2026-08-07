"""AI因子: 波动趋势背离 | 置信:65% | 衡量短期波动率与趋势强度的背离程度。当波动率高而趋势强度低时（ADX<25且ATR/MA比率高），表明市场处于无方向的高波动状态，易触发假突破止损。因子值正向为风险信号（波动大趋势弱），负向为安全信号（趋势明确波动小）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityTrendDivergence(BaseFactor):
    """衡量短期波动率与趋势强度的背离程度。当波动率高而趋势强度低时（ADX<25且ATR/MA比率高），表明市场处于无方向的高波动状态，易触发假突破止损。因子值正向为风险信号（波动大趋势弱），负向为安全信号（趋势明确波动小）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_voltrend_div",
            name="Volatility-Trend Divergence",
            display_name="波动趋势背离",
            description="衡量短期波动率与趋势强度的背离程度。当波动率高而趋势强度低时（ADX<25且ATR/MA比率高），表明市场处于无方向的高波动状态，易触发假突破止损。因子值正向为风险信号（波动大趋势弱），负向为安全信号（趋势明确波动小）。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算ATR（14周期）
        high = data['high']
        low = data['low']
        close = data['close']
        tr = np.maximum(high - low, np.maximum(np.abs(high - close.shift(1)), np.abs(low - close.shift(1))))
        atr = tr.rolling(14).mean()
        # 计算短期波动率（5周期ATR与MA的比率）
        ma = close.rolling(20).mean()
        vol_ratio = atr / ma
        # 计算ADX（14周期）作为趋势强度
        plus_dm = high.diff()
        minus_dm = low.diff()
        plus_dm[plus_dm < 0] = 0
        minus_dm[minus_dm > 0] = 0
        tr_smooth = tr.rolling(14).mean()
        plus_di = 100 * plus_dm.rolling(14).mean() / tr_smooth
        minus_di = 100 * (-minus_dm).rolling(14).mean() / tr_smooth
        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di)
        adx = dx.rolling(14).mean()
        # 组合信号：vol_ratio高且adx低 → 正值
        norm_vol = (vol_ratio - vol_ratio.rolling(100).min()) / (vol_ratio.rolling(100).max() - vol_ratio.rolling(100).min() + 1e-10)
        norm_adx = 1 - (adx - adx.rolling(100).min()) / (adx.rolling(100).max() - adx.rolling(100).min() + 1e-10)
        divergence = norm_vol * norm_adx
        # 映射到[-1,1]
        result = 2 * (divergence - 0.5)
        return result.fillna(0).clip(-1, 1)
