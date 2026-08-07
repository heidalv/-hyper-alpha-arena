"""AI因子: 市场状态不确定性指标 | 置信:65% | 综合多个时间周期的趋势强度（ADX）和波动率（ATR/Close）来量化市场是否处于无明确趋势的未知状态。当短期趋势弱（ADX低）且波动率处于中等水平时，认为当前状态不稳定，容易触发止损或超时亏损，返回负值以提示避免做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Regime_Uncertainty_Indicator(BaseFactor):
    """综合多个时间周期的趋势强度（ADX）和波动率（ATR/Close）来量化市场是否处于无明确趋势的未知状态。当短期趋势弱（ADX低）且波动率处于中等水平时，认为当前状态不稳定，容易触发止损或超时亏损，返回负值以提示避免做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rgn_un",
            name="Regime Uncertainty Indicator",
            display_name="市场状态不确定性指标",
            description="综合多个时间周期的趋势强度（ADX）和波动率（ATR/Close）来量化市场是否处于无明确趋势的未知状态。当短期趋势弱（ADX低）且波动率处于中等水平时，认为当前状态不稳定，容易触发止损或超时亏损，返回负值以提示避免做多。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np

        # 计算ATR和ADX
        high = data['high']
        low = data['low']
        close = data['close']
        volume = data['volume']

        # ATR (14)
        tr = pd.concat([high - low,
                        abs(high - close.shift()),
                        abs(low - close.shift())], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()

        # ADX (14) 简化：使用+DI和-DI差值
        delta = close.diff()
        up = delta.where(delta > 0, 0)
        down = -delta.where(delta < 0, 0)
        tr_sma = tr.rolling(14).sum()
        up_sma = up.rolling(14).sum()
        down_sma = down.rolling(14).sum()
        pdi = 100 * up_sma / tr_sma
        ndi = 100 * down_sma / tr_sma
        dx = 100 * abs(pdi - ndi) / (pdi + ndi + 1e-10)
        adx = dx.rolling(14).mean()

        # 波动率比率：ATR / Close，标准化
        vol_ratio = atr / close
        vol_zscore = (vol_ratio - vol_ratio.rolling(20).mean()) / vol_ratio.rolling(20).std()

        # 趋势强度：ADX标准化（较低ADX表示微弱趋势）
        adx_zscore = (adx - adx.rolling(20).mean()) / adx.rolling(20).std()

        # 组合：当ADX偏低（zscore < -0.5）且波动率处于中等（zscore介于-1和1之间）时，不确定性高
        uncertainty = np.where((adx_zscore < -0.5) & (vol_zscore.between(-1, 1)), -1.0, 0.0)
        # 平滑以产生连续信号
        result = pd.Series(uncertainty, index=data.index).rolling(5).mean().fillna(0)
        return result
