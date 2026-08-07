"""AI因子: 流动性冲击因子 | 置信:70% | 衡量单位成交量下的价格波动幅度。当成交量萎靡而价格大幅波动时（流动性枯竭），容易引发非理性亏损。使用过去N根K线的价格变化绝对值与成交量比值的滚动z-score，正值表示流动性差、价格跳动风险高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Liquidity_Shock_Factor(BaseFactor):
    """衡量单位成交量下的价格波动幅度。当成交量萎靡而价格大幅波动时（流动性枯竭），容易引发非理性亏损。使用过去N根K线的价格变化绝对值与成交量比值的滚动z-score，正值表示流动性差、价格跳动风险高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liquid",
            name="Liquidity Shock Factor",
            display_name="流动性冲击因子",
            description="衡量单位成交量下的价格波动幅度。当成交量萎靡而价格大幅波动时（流动性枯竭），容易引发非理性亏损。使用过去N根K线的价格变化绝对值与成交量比值的滚动z-score，正值表示流动性差、价格跳动风险高。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 计算每根K线的价格波动（用高减低）
        price_range = data['high'] - data['low']
        volume = data['volume']
        # 防止除零
        volume_safe = volume.replace(0, 1e-6)
        # 冲击系数 = 价格波动 / 成交量（标准化到单位量）
        shock = price_range / volume_safe
        # 滚动窗口20取平均
        window = 20
        shock_mean = shock.rolling(window, min_periods=10).mean()
        shock_std = shock.rolling(window, min_periods=10).std().replace(0, 1e-6)
        z = (shock - shock_mean) / shock_std
        # 用tanh压缩到[-1,1]
        result = np.tanh(z)
        return result.fillna(0)
