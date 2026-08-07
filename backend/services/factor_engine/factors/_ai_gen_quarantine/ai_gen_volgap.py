"""AI因子: 波动率缺口因子 | 置信:65% | 衡量短期波动率（过去5周期）与长期波动率（过去20周期）的比率，当比率过高或过低时表明市场处于异常状态，容易引发无序亏损。通过线性变换映射到[-1,1], 正值表示短期波动率异常偏高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Gap_Factor(BaseFactor):
    """衡量短期波动率（过去5周期）与长期波动率（过去20周期）的比率，当比率过高或过低时表明市场处于异常状态，容易引发无序亏损。通过线性变换映射到[-1,1], 正值表示短期波动率异常偏高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volgap",
            name="Volatility Gap Factor",
            display_name="波动率缺口因子",
            description="衡量短期波动率（过去5周期）与长期波动率（过去20周期）的比率，当比率过高或过低时表明市场处于异常状态，容易引发无序亏损。通过线性变换映射到[-1,1], 正值表示短期波动率异常偏高。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        # data has columns: open, high, low, close, volume
        returns = data['close'].pct_change()
        short_vol = returns.rolling(5).std()
        long_vol = returns.rolling(20).std()
        # avoid division by zero
        ratio = short_vol / (long_vol + 1e-10)
        # normalize to [-1,1] using tanh after scaling
        normalized = (ratio - 1.0) * 2.0  # center around 1, scale
        result = pd.Series(np.tanh(normalized), index=data.index)
        return result
