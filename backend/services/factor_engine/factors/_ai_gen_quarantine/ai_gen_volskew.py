"""AI因子: 成交量RSI背离 | 置信:50% | 当成交量异常放大且RSI处于超买/超卖区域时，价格容易发生剧烈反转，常导致止损或移动止盈被触发。因子在成交量突增且RSI极值时输出接近-1，正常状态输出0。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeRSIDivergence(BaseFactor):
    """当成交量异常放大且RSI处于超买/超卖区域时，价格容易发生剧烈反转，常导致止损或移动止盈被触发。因子在成交量突增且RSI极值时输出接近-1，正常状态输出0。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volskew",
            name="Volume_RSI_Divergence",
            display_name="成交量RSI背离",
            description="当成交量异常放大且RSI处于超买/超卖区域时，价格容易发生剧烈反转，常导致止损或移动止盈被触发。因子在成交量突增且RSI极值时输出接近-1，正常状态输出0。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # RSI
        delta = data['close'].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / (avg_loss + 1e-10)
        rsi = 100 - (100 / (1 + rs))
        # 成交量异常：当前成交量相比过去20日均值的比率
        vol_ma20 = data['volume'].rolling(20).mean()
        vol_ratio = data['volume'] / (vol_ma20 + 1e-10)
        # 定义极端区域：RSI > 70 或 RSI < 30，且成交量放大超过1.5倍
        extreme_rsi = ((rsi > 70) | (rsi < 30)).astype(float)
        high_vol = (vol_ratio > 1.5).astype(float)
        # 风险信号
        raw = extreme_rsi * high_vol
        # 平滑并翻转符号
        smoothed = raw.rolling(3).max()
        result = -smoothed  # 使得风险时段为-1
        return result.fillna(0)
