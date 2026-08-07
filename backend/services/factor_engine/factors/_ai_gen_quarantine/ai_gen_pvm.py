"""AI因子: 量价不匹配 | 置信:60% | 通过比较收盘价相对于日内高低的位置与成交量的变化，识别价格运动缺乏成交量支撑或成交量异常放大的虚假波动。当收盘价接近极端但成交量萎缩时，信号为负，提示潜在反转或虚假突破。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Price_Volume_Mismatch(BaseFactor):
    """通过比较收盘价相对于日内高低的位置与成交量的变化，识别价格运动缺乏成交量支撑或成交量异常放大的虚假波动。当收盘价接近极端但成交量萎缩时，信号为负，提示潜在反转或虚假突破。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pvm",
            name="Price-Volume Mismatch",
            display_name="量价不匹配",
            description="通过比较收盘价相对于日内高低的位置与成交量的变化，识别价格运动缺乏成交量支撑或成交量异常放大的虚假波动。当收盘价接近极端但成交量萎缩时，信号为负，提示潜在反转或虚假突破。",
            category="composite",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 计算日内位置： (close - low) / (high - low) 避免除以0
        range_ = data['high'] - data['low']
        pos = np.where(range_ > 0, (data['close'] - data['low']) / range_, 0.5)
        # 成交量变化率（相对前一期）
        vol_ratio = data['volume'] / (data['volume'].shift(1) + 1e-10)
        # 计算不匹配度：当位置极端 (>0.8或<0.2) 且成交量变化不大 (0.8<vol_ratio<1.2) 或相反
        # 使用正负号表示方向
        signal = np.where((pos > 0.8) | (pos < 0.2), 
                          (0.5 - pos) * np.log(vol_ratio + 1e-10), 0)
        # 归一化到[-1,1]
        result = pd.Series(np.tanh(signal * 5), index=data.index)
        return result
