"""AI因子: 波动率扭曲因子 | 置信:60% | 通过短期波动率（5周期）与长期波动率（20周期）的比值，减去1后归一化到[-1,1]，捕捉波动率结构异常（如短期剧烈波动但长期平稳），此类状态常对应regime未知的混乱行情。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityTwist(BaseFactor):
    """通过短期波动率（5周期）与长期波动率（20周期）的比值，减去1后归一化到[-1,1]，捕捉波动率结构异常（如短期剧烈波动但长期平稳），此类状态常对应regime未知的混乱行情。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_twist",
            name="Volatility Twist",
            display_name="波动率扭曲因子",
            description="通过短期波动率（5周期）与长期波动率（20周期）的比值，减去1后归一化到[-1,1]，捕捉波动率结构异常（如短期剧烈波动但长期平稳），此类状态常对应regime未知的混乱行情。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # data: DataFrame with columns open, high, low, close, volume
        returns = data['close'].pct_change()
        short_vol = returns.rolling(5).std()
        long_vol = returns.rolling(20).std()
        # 避免除零
        ratio = short_vol / (long_vol + 1e-10) - 1.0
        # 截断并归一化到[-1,1], 使用tanh
        result = np.tanh(ratio * 3)  # 将敏感度放大
        return result.fillna(0.0)
