"""AI因子: 波动噪音 | 置信:60% | 基于价格序列的短期波动率与中期波动率的比值以及价格序列的随机游走程度，高比值且低趋势强度时表示市场噪音大、方向不明确，输出负值；趋势明确时输出正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class volatility_noise(BaseFactor):
    """基于价格序列的短期波动率与中期波动率的比值以及价格序列的随机游走程度，高比值且低趋势强度时表示市场噪音大、方向不明确，输出负值；趋势明确时输出正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volnoi",
            name="volatility_noise",
            display_name="波动噪音",
            description="基于价格序列的短期波动率与中期波动率的比值以及价格序列的随机游走程度，高比值且低趋势强度时表示市场噪音大、方向不明确，输出负值；趋势明确时输出正值。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short_period = 5
        long_period = 20
        ret = data['close'].pct_change()
        short_vol = ret.rolling(short_period).std()
        long_vol = ret.rolling(long_period).std()
        vol_ratio = short_vol / (long_vol + 1e-10)
        # 用短期自相关系数衡量随机性
        autocorr = ret.rolling(20).apply(lambda x: x.autocorr() if len(x.dropna())>5 else 0, raw=False)
        noise = (vol_ratio - 1) * np.exp(-np.abs(autocorr))
        result = pd.Series(np.clip(-noise, -1, 1), index=data.index)
        return result
