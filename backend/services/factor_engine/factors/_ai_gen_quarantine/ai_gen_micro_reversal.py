"""AI因子: 微观反转成交量因子 | 置信:60% | 检测短期价格反转信号，通过比较当前收盘价与过去N周期内价格极值的偏离度，并乘以成交量相对均值的异常倍数。当偏离度大且成交量放量时，暗示可能的反转（做空信号为正，做多信号为负）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MicroReversalWithVolume(BaseFactor):
    """检测短期价格反转信号，通过比较当前收盘价与过去N周期内价格极值的偏离度，并乘以成交量相对均值的异常倍数。当偏离度大且成交量放量时，暗示可能的反转（做空信号为正，做多信号为负）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_micro_reversal",
            name="Micro Reversal with Volume",
            display_name="微观反转成交量因子",
            description="检测短期价格反转信号，通过比较当前收盘价与过去N周期内价格极值的偏离度，并乘以成交量相对均值的异常倍数。当偏离度大且成交量放量时，暗示可能的反转（做空信号为正，做多信号为负）。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        # data columns: open, high, low, close, volume
        # 计算过去5周期最高价和最低价
        high_5 = data['high'].rolling(5).max()
        low_5 = data['low'].rolling(5).min()
        # 计算当前收盘价相对近期区间的归一化位置
        range_5 = high_5 - low_5
        # 避免除零
        range_5 = range_5.replace(0, np.nan)
        pos = (data['close'] - low_5) / range_5  # 0~1之间，接近1为高位，接近0为低位
        # 反转信号：当价格处于极端位置时预期反转
        extreme_signal = 2 * (pos - 0.5)  # 映射到[-1,1]，高位为正（预期下跌），低位为负（预期上涨）
        # 成交量相对20日均值的倍数
        vol_ma20 = data['volume'].rolling(20).mean()
        vol_ratio = data['volume'] / vol_ma20.replace(0, np.nan)
        # 将成交量放大因子缩放至[0,1]区间，用sigmoid
        vol_factor = 2 / (1 + np.exp(-0.5 * (vol_ratio - 1))) - 1  # 值域[-1,1]，但此处用线性更好
        # 更简单：用clip
        vol_factor = np.clip((vol_ratio - 1) / 3, -1, 1)
        # 复合信号：极端位置 + 成交量确认
        result = extreme_signal * np.sign(vol_factor) * np.abs(vol_factor)
        # 平滑处理
        result = result.rolling(3).mean()
        return result.fillna(0)
