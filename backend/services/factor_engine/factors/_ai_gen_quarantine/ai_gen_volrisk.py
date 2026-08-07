"""AI因子: 波动率状态风险 | 置信:65% | 衡量短期波动率相对于中期波动率的异常水平。短期ATR（5日）与中期ATR（20日）比值过高意味着高波动风险，输出负值（看空）；比值低则输出正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Regime_Risk(BaseFactor):
    """衡量短期波动率相对于中期波动率的异常水平。短期ATR（5日）与中期ATR（20日）比值过高意味着高波动风险，输出负值（看空）；比值低则输出正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volrisk",
            name="Volatility Regime Risk",
            display_name="波动率状态风险",
            description="衡量短期波动率相对于中期波动率的异常水平。短期ATR（5日）与中期ATR（20日）比值过高意味着高波动风险，输出负值（看空）；比值低则输出正值。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']

        # 计算TR
        prev_close = close.shift(1)
        tr = np.maximum(high - low, 
                        np.abs(high - prev_close), 
                        np.abs(low - prev_close))
        # 短期ATR (5日)
        atr_short = tr.rolling(window=5, min_periods=5).mean()
        # 中期ATR (20日)
        atr_mid = tr.rolling(window=20, min_periods=20).mean()

        ratio = atr_short / atr_mid
        # 映射到[-1,1]：ratio >1.2 -> -1; ratio <0.8 -> +1; 中间线性映射
        result = pd.Series(0.0, index=data.index)
        low_thresh = 0.8
        high_thresh = 1.2
        result[ratio <= low_thresh] = 1.0
        result[(ratio > low_thresh) & (ratio < high_thresh)] = (1 - (ratio[(ratio > low_thresh) & (ratio < high_thresh)] - low_thresh) / (high_thresh - low_thresh) * 2) * -1
        result[ratio >= high_thresh] = -1.0
        # 处理NaN
        nan_mask = atr_short.isna() | atr_mid.isna()
        result[nan_mask] = 0.0
        return result
