"""AI因子: 流动性扭曲因子 | 置信:60% | 检测价格是否在近期高低点附近出现异常上下影线或窄幅运动，可能反映流动性陷阱。计算上下影线长度与实体的比值，并结合价格接近极值的状态。当比值异常且价格在极值处时，预示流动性磁力反转风险，输出负值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidityDistortion(BaseFactor):
    """检测价格是否在近期高低点附近出现异常上下影线或窄幅运动，可能反映流动性陷阱。计算上下影线长度与实体的比值，并结合价格接近极值的状态。当比值异常且价格在极值处时，预示流动性磁力反转风险，输出负值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liq_distort",
            name="Liquidity Distortion",
            display_name="流动性扭曲因子",
            description="检测价格是否在近期高低点附近出现异常上下影线或窄幅运动，可能反映流动性陷阱。计算上下影线长度与实体的比值，并结合价格接近极值的状态。当比值异常且价格在极值处时，预示流动性磁力反转风险，输出负值。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        open, high, low, close = data['open'], data['high'], data['low'], data['close']
        upper_shadow = high - np.maximum(open, close)
        lower_shadow = np.minimum(open, close) - low
        body = np.abs(close - open)
        # 上下影线相对实体的比例
        shadow_ratio = (upper_shadow + lower_shadow) / (body + 1e-10)
        # 价格接近近期高点或低点
        high_10 = data['high'].rolling(10).max()
        low_10 = data['low'].rolling(10).min()
        near_high = (close / high_10).clip(0, 1)
        near_low = (low_10 / close).clip(0, 1) if (low_10==0).any() else (low_10 / close)
        # 综合信号：高影线比且价格在极值
        distort = shadow_ratio * np.maximum(near_high, near_low)
        result = -np.clip(distort / 3.0, 0, 1)
        return result.fillna(0).clip(-1, 1)
