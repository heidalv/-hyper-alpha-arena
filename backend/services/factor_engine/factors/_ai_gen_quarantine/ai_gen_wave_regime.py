"""AI因子: 波动状态识别器 | 置信:60% | 基于近期波动率与历史波动率的比值以及价格序列的自相关性，区分趋势/震荡/噪音状态。当市场处于无序波动（regime=unknown）时输出负值，有序趋势时输出正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Wave_Regime_Detector(BaseFactor):
    """基于近期波动率与历史波动率的比值以及价格序列的自相关性，区分趋势/震荡/噪音状态。当市场处于无序波动（regime=unknown）时输出负值，有序趋势时输出正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_wave_regime",
            name="Wave Regime Detector",
            display_name="波动状态识别器",
            description="基于近期波动率与历史波动率的比值以及价格序列的自相关性，区分趋势/震荡/噪音状态。当市场处于无序波动（regime=unknown）时输出负值，有序趋势时输出正值。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        period = 14
        # 波动率指标: 近期ATR / 长期ATR
        tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
        atr_short = tr.rolling(period).mean()
        atr_long = tr.rolling(period*4).mean()
        vol_ratio = atr_short / (atr_long + 1e-10)
        # 价格序列自相关性: 过去5根K线收益率序列的自相关(滞后1)
        ret = close.pct_change()
        autocorr = ret.rolling(period).apply(lambda x: x.autocorr() if len(x.dropna()) >= 5 else np.nan, raw=False)
        # 综合: 高自相关且波动率稳定 -> 趋势; 低自相关且波动率放大 -> 噪音
        composite = autocorr.fillna(0) * (1 - abs(vol_ratio - 1))
        composite = composite.clip(-1, 1)
        return composite.fillna(0)
