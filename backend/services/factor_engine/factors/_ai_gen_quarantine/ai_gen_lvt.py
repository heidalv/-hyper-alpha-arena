"""AI因子: 低波动陷阱因子 | 置信:58% | 识别市场处于低波动且价格接近近期区间边缘的状态，此类状态易发生假突破导致亏损。使用布林带宽度（标准差/中轨）的近期分位数和价格在布林带中的位置。输出-1表示高风险陷阱，+1表示安全。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LowVolatilityTrap(BaseFactor):
    """识别市场处于低波动且价格接近近期区间边缘的状态，此类状态易发生假突破导致亏损。使用布林带宽度（标准差/中轨）的近期分位数和价格在布林带中的位置。输出-1表示高风险陷阱，+1表示安全。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_lvt",
            name="LowVolatilityTrap",
            display_name="低波动陷阱因子",
            description="识别市场处于低波动且价格接近近期区间边缘的状态，此类状态易发生假突破导致亏损。使用布林带宽度（标准差/中轨）的近期分位数和价格在布林带中的位置。输出-1表示高风险陷阱，+1表示安全。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        high = data['high']
        low = data['low']
        period = 20
        # 布林带
        sma = close.rolling(period).mean()
        std = close.rolling(period).std()
        bandwidth = std / sma  # 相对带宽
        # 近期带宽分位数（过去40个周期）
        lookback = 40
        current_bw = bandwidth.iloc[-1] if len(bandwidth) > 0 else 0
        if len(bandwidth) < lookback:
            return pd.Series(0, index=close.index)
        rolling_bw = bandwidth.rolling(lookback).quantile(0.1).shift(1)  # 10%分位数
        # 价格在布林带中的位置: (close - sma) / std 归一化到[-1,1]
        position = (close - sma) / std.clip(lower=1e-10)
        position = position.clip(-3,3) / 3.0
        # 判断是否低波动且靠近边界
        low_vol_flag = (bandwidth < rolling_bw).astype(float) * 2 - 1  # -1低波动, 1正常
        # 最终因子：低波动且位置极端则负向，否则正向
        result = -1 * (low_vol_flag * position.abs()).clip(-1,1)
        # 平滑处理
        result = result.rolling(5).mean().fillna(0)
        return result
