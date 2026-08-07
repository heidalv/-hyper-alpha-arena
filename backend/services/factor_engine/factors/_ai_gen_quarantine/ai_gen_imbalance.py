"""AI因子: 订单失衡反转 | 置信:65% | 利用收盘价在K线中的相对位置与成交量关系，判断多空力量失衡后的反转。当出现长上下影线且收盘在极端位置，同时成交量异常放大，预示订单簿失衡即将反转。输出正值表示空头失衡（看多），负值表示多头失衡（看空）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class OrderImbalanceReversal(BaseFactor):
    """利用收盘价在K线中的相对位置与成交量关系，判断多空力量失衡后的反转。当出现长上下影线且收盘在极端位置，同时成交量异常放大，预示订单簿失衡即将反转。输出正值表示空头失衡（看多），负值表示多头失衡（看空）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_imbalance",
            name="Order Imbalance Reversal",
            display_name="订单失衡反转",
            description="利用收盘价在K线中的相对位置与成交量关系，判断多空力量失衡后的反转。当出现长上下影线且收盘在极端位置，同时成交量异常放大，预示订单簿失衡即将反转。输出正值表示空头失衡（看多），负值表示多头失衡（看空）。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        open_ = data['open']
        volume = data['volume']
        # 计算K线实体与影线
        body = np.abs(close - open_)
        upper_shadow = high - np.maximum(close, open_)
        lower_shadow = np.minimum(close, open_) - low
        # 相对长度
        total_range = high - low
        # 避免除零
        total_range = np.where(total_range == 0, 0.001, total_range)
        # 上影线占比, 下影线占比
        upper_ratio = upper_shadow / total_range
        lower_ratio = lower_shadow / total_range
        # 成交量异常（20日均值倍数）
        avg_vol = volume.rolling(20).mean()
        vol_factor = volume / avg_vol
        # 多头失衡：下影线极长（>0.6）且收盘在底部，成交量放大 -> 看涨反转
        long_signal = (lower_ratio > 0.6) & (close <= open_) & (vol_factor > 1.5)
        # 空头失衡：上影线极长（>0.6）且收盘在顶部，成交量放大 -> 看跌反转
        short_signal = (upper_ratio > 0.6) & (close >= open_) & (vol_factor > 1.5)
        signal = np.where(long_signal, 1, np.where(short_signal, -1, 0))
        return pd.Series(signal, index=data.index).clip(-1, 1)
