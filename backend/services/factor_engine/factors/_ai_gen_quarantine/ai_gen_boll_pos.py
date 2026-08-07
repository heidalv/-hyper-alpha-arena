"""AI因子: 布林带相对位置 | 置信:55% | 计算当前收盘价在布林带（20日，2倍标准差）中的相对位置。当价格接近上轨（位置>0.8）时，做多风险高，容易回调；当接近下轨时，做多反弹可能。输出为[-1,1]，接近1表示在上轨，-1在下轨。对于做多而言，正值越大越危险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Bollinger_Position(BaseFactor):
    """计算当前收盘价在布林带（20日，2倍标准差）中的相对位置。当价格接近上轨（位置>0.8）时，做多风险高，容易回调；当接近下轨时，做多反弹可能。输出为[-1,1]，接近1表示在上轨，-1在下轨。对于做多而言，正值越大越危险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_boll_pos",
            name="Bollinger_Position",
            display_name="布林带相对位置",
            description="计算当前收盘价在布林带（20日，2倍标准差）中的相对位置。当价格接近上轨（位置>0.8）时，做多风险高，容易回调；当接近下轨时，做多反弹可能。输出为[-1,1]，接近1表示在上轨，-1在下轨。对于做多而言，正值越大越危险。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        ma = close.rolling(20).mean()
        std = close.rolling(20).std()
        upper = ma + 2 * std
        lower = ma - 2 * std
        # 相对位置：0到1，但可能超出
        pos = (close - lower) / (upper - lower)  # 正常在0~1，可能超过
        # 映射到[-1,1]：0->-1, 0.5->0, 1->1。使用双曲正切型，或直接线性裁剪
        # 直接线性，并限制在[-1,1]
        result = 2 * pos - 1
        # 处理极端值
        result = result.clip(-1, 1)
        return result
