"""AI因子: 噪音强度 | 置信:50% | 计算当前K线的振幅（高-低）与平均振幅的比例，并结合成交量异常程度，衡量市场噪音水平。高噪音时容易触发小止损，因子值接近-1（高风险）；低噪音时接近+1（低风险）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class NoiseIntensity(BaseFactor):
    """计算当前K线的振幅（高-低）与平均振幅的比例，并结合成交量异常程度，衡量市场噪音水平。高噪音时容易触发小止损，因子值接近-1（高风险）；低噪音时接近+1（低风险）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_noi",
            name="Noise Intensity",
            display_name="噪音强度",
            description="计算当前K线的振幅（高-低）与平均振幅的比例，并结合成交量异常程度，衡量市场噪音水平。高噪音时容易触发小止损，因子值接近-1（高风险）；低噪音时接近+1（低风险）。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        # 计算振幅与均值比值
        high_low = data['high'] - data['low']
        avg_range = high_low.rolling(20).mean()
        range_ratio = high_low / avg_range
        # 计算成交量异常：当前成交量与20日均值比值
        vol_ma = data['volume'].rolling(20).mean()
        vol_ratio = data['volume'] / vol_ma
        # 组合噪音因子：振幅过大且成交量异常高时噪音高
        noise_raw = (range_ratio - 1) * (vol_ratio - 1)
        # 归一化到[-1,1]，使用tanh压缩
        normalized = np.tanh(noise_raw * 2)
        return normalized.fillna(0)
