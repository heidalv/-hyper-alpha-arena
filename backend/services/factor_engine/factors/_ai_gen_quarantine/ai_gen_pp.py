"""AI因子: 价格位置 | 置信:60% | 计算收盘价在最近20周期最高最低点之间的相对位置。当价格处于高位(>0.8)且近期动量减弱时，信号偏向-1（避免做多）；当价格处于低位(<0.2)且出现止跌迹象时，信号偏向+1。平滑处理以避免噪声。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PricePosition(BaseFactor):
    """计算收盘价在最近20周期最高最低点之间的相对位置。当价格处于高位(>0.8)且近期动量减弱时，信号偏向-1（避免做多）；当价格处于低位(<0.2)且出现止跌迹象时，信号偏向+1。平滑处理以避免噪声。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pp",
            name="PricePosition",
            display_name="价格位置",
            description="计算收盘价在最近20周期最高最低点之间的相对位置。当价格处于高位(>0.8)且近期动量减弱时，信号偏向-1（避免做多）；当价格处于低位(<0.2)且出现止跌迹象时，信号偏向+1。平滑处理以避免噪声。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        period = 20
        rolling_high = data['high'].rolling(period, min_periods=1).max()
        rolling_low = data['low'].rolling(period, min_periods=1).min()
        range_ = rolling_high - rolling_low
        # 防止除以零
        range_ = range_.replace(0, np.nan)
        position = (data['close'] - rolling_low) / range_
        # 动量：过去5周期价格变化
        momentum = data['close'].pct_change(5)
        # 高位且动量负 -> 看空；低位且动量正 -> 看多；其他情况中性
        long_signal = (position < 0.2) & (momentum > 0.02)
        short_signal = (position > 0.8) & (momentum < -0.02)
        result = pd.Series(0.0, index=data.index)
        result[long_signal] = 1.0
        result[short_signal] = -1.0
        # 平滑：取3周期均值
        result = result.rolling(3, min_periods=1).mean()
        return result.clip(-1, 1)
