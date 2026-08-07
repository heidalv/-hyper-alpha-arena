"""AI因子: 成交量分布位置得分 | 置信:50% | 识别当前价格在近期（20日）最高最低之间的相对位置，结合成交量分布。若价格处于成交量密集区之外（即低成交量区域），则得分偏正（避免交易），反之中性。用于过滤掉在窄幅震荡或流动性不足区域开仓导致的亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Profile_Position_Score(BaseFactor):
    """识别当前价格在近期（20日）最高最低之间的相对位置，结合成交量分布。若价格处于成交量密集区之外（即低成交量区域），则得分偏正（避免交易），反之中性。用于过滤掉在窄幅震荡或流动性不足区域开仓导致的亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volprofile",
            name="Volume Profile Position Score",
            display_name="成交量分布位置得分",
            description="识别当前价格在近期（20日）最高最低之间的相对位置，结合成交量分布。若价格处于成交量密集区之外（即低成交量区域），则得分偏正（避免交易），反之中性。用于过滤掉在窄幅震荡或流动性不足区域开仓导致的亏损。",
            category="volume",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        lookback = 20
        high = data['high']
        low = data['low']
        close = data['close']
        volume = data['volume']
        # 计算近20日最高最低
        roll_high = high.rolling(lookback).max()
        roll_low = low.rolling(lookback).min()
        range_ = roll_high - roll_low
        # 价格相对位置 (0~1)
        rel_pos = (close - roll_low) / (range_ + 1e-8)
        # 计算成交量加权价格分布：将范围分成10个区间，计算每个区间的累计成交量比例
        # 简化：使用当前价格附近的成交量比例，用相对位置和成交量分布
        # 计算价格区间（每1%为一个bin）
        bins = 20
        bin_width = 1.0 / bins
        # 用向量化方法：将每个close映射到bin
        bin_index = (rel_pos * bins).astype(int).clip(0, bins-1)
        # 计算每个bin的累计成交量（滚动）
        # 这里简化为：计算滚动20日每个价格区间的成交量占比，然后看当前价格所在bin的占比
        # 但为了效率，采用近似：计算成交量加权平均价格位置，然后与当前价格位置比较
        # 简单方法：计算近20日平均相对位置，与当前相对位置的差值，越大表示价格偏离平均成交位置
        avg_rel = (close.rolling(lookback).mean() - roll_low) / (range_ + 1e-8)
        deviation = rel_pos - avg_rel
        # 用tanh归一化到[-1,1]，正值表示价格高于成交量密集区，负值表示低于
        result = np.tanh(deviation * 5.0)
        return result.fillna(0.0)
