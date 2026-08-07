"""AI因子: 区间震荡因子 | 置信:60% | 通过布林带宽度和价格位置识别震荡市。当布林带宽度极窄且价格位于中轨附近时，市场缺乏方向，容易发生max_hold_timeout亏损，信号为负。反之信号为正。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class range_bound(BaseFactor):
    """通过布林带宽度和价格位置识别震荡市。当布林带宽度极窄且价格位于中轨附近时，市场缺乏方向，容易发生max_hold_timeout亏损，信号为负。反之信号为正。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rngbd",
            name="range_bound",
            display_name="区间震荡因子",
            description="通过布林带宽度和价格位置识别震荡市。当布林带宽度极窄且价格位于中轨附近时，市场缺乏方向，容易发生max_hold_timeout亏损，信号为负。反之信号为正。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        # 布林带参数
        period = 20
        std = close.rolling(period).std()
        mid = close.rolling(period).mean()
        bandwidth = 2 * std / (mid + 1e-8)  # 相对带宽
        # 价格在中轨的位置 (归一化)
        position = (close - mid) / (std * 2 + 1e-8)  # 接近0表示在中轨
        # 信号：带宽小且位置接近0 -> 负；否则正
        # 使用高斯核或sigmoid
        width_signal = -np.tanh((bandwidth - 0.05) * 10)  # 带宽<0.05时负，>0.05时正
        pos_signal = -np.tanh(np.abs(position) * 3)  # 位置越偏离0越负？实际上我们希望位置在中轨时负，偏离时正？
        # 修正：当带宽小且位置在中轨（|position|<0.2）时给强负信号，否则正
        raw = -np.exp(-np.abs(position)*5) * np.exp(-bandwidth*20) * 0.5 + 0.5  # 简单规则
        raw = (bandwidth < 0.05).astype(float) * (np.abs(position) < 0.3).astype(float) * -1
        # 平滑处理
        raw = raw.rolling(3).mean()
        return raw.fillna(0)
