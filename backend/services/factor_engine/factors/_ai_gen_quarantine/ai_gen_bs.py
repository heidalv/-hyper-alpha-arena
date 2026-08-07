"""AI因子: 带宽收缩因子 | 置信:60% | 基于布林带带宽（上轨-下轨）/中轨，当带宽明显收缩时表示市场进入低波动盘整，容易触发持仓超时亏损，输出负值；扩张时输出正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class BandwidthSqueeze(BaseFactor):
    """基于布林带带宽（上轨-下轨）/中轨，当带宽明显收缩时表示市场进入低波动盘整，容易触发持仓超时亏损，输出负值；扩张时输出正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_bs",
            name="BandwidthSqueeze",
            display_name="带宽收缩因子",
            description="基于布林带带宽（上轨-下轨）/中轨，当带宽明显收缩时表示市场进入低波动盘整，容易触发持仓超时亏损，输出负值；扩张时输出正值。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        high = data['high']
        low = data['low']
        # 计算布林带（20,2）
        sma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        upper = sma20 + 2 * std20
        lower = sma20 - 2 * std20
        bandwidth = (upper - lower) / sma20
        # 计算带宽的历史百分位或标准化
        # 对带宽取负，使得收缩时为负值，扩张时为正值
        # 用z-score归一化后取负
        bw_mean = bandwidth.rolling(60).mean()
        bw_std = bandwidth.rolling(60).std()
        z = (bandwidth - bw_mean) / (bw_std + 1e-8)
        # 使用tanh限制在[-1,1]
        result = -np.tanh(z * 0.5)
        return result.fillna(0)
