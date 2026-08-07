"""AI因子: 市场状态不确定性 | 置信:65% | 基于价格波动与趋势强度的比值，识别市场处于不确定（regime=unknown）状态。当趋势模糊且波动剧烈时，该因子接近-1，建议避免交易；当趋势明确时接近+1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RegimeUncertainty(BaseFactor):
    """基于价格波动与趋势强度的比值，识别市场处于不确定（regime=unknown）状态。当趋势模糊且波动剧烈时，该因子接近-1，建议避免交易；当趋势明确时接近+1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regime_unc",
            name="Regime Uncertainty",
            display_name="市场状态不确定性",
            description="基于价格波动与趋势强度的比值，识别市场处于不确定（regime=unknown）状态。当趋势模糊且波动剧烈时，该因子接近-1，建议避免交易；当趋势明确时接近+1。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 计算ATR(14)和ADX(14)
        high = data['high']
        low = data['low']
        close = data['close']
        tr = np.maximum(high - low, np.maximum(np.abs(high - close.shift(1)), np.abs(low - close.shift(1))))
        atr = tr.rolling(14).mean()
        # 方向性移动
        up = high.diff()
        down = -low.diff()
        up[up < 0] = 0
        down[down < 0] = 0
        sma_up = up.rolling(14).mean()
        sma_down = down.rolling(14).mean()
        dx = 100 * np.abs(sma_up - sma_down) / (sma_up + sma_down + 1e-10)
        adx = dx.rolling(14).mean()
        # 归一化波动率: atr相对于价格的比例
        atr_pct = atr / close * 100
        # 不确定性指标: 低ADX + 高ATR_pct
        uncertainty = (adx.rank(pct=True) * -1 + atr_pct.rank(pct=True)) / 2
        # 映射到[-1,1]
        result = (uncertainty - 0.5) * 2
        return result
