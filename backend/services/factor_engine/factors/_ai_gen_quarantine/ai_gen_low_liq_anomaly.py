"""AI因子: 低流动性异常 | 置信:50% | 针对小市值或低流动性资产，检测价格在成交量极低时出现的异常波动，随后回归均值。利用价格偏离布林带和成交量排名，当价格突破但成交量极度萎缩时产生反向信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LowLiquidityAnomaly(BaseFactor):
    """针对小市值或低流动性资产，检测价格在成交量极低时出现的异常波动，随后回归均值。利用价格偏离布林带和成交量排名，当价格突破但成交量极度萎缩时产生反向信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_low_liq_anomaly",
            name="Low Liquidity Anomaly",
            display_name="低流动性异常",
            description="针对小市值或低流动性资产，检测价格在成交量极低时出现的异常波动，随后回归均值。利用价格偏离布林带和成交量排名，当价格突破但成交量极度萎缩时产生反向信号。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        volume = data['volume']
        # 布林带参数
        window = 20
        std = close.rolling(window).std()
        mid = close.rolling(window).mean()
        upper = mid + 2*std
        lower = mid - 2*std
        # 成交量分位数（过去100天）
        vol_percentile = volume.rolling(100).apply(lambda x: (x.iloc[-1] < x).mean(), raw=True)
        # 价格突破布林带但成交量处于最低10%
        breakout_up = (close > upper) & (vol_percentile < 0.1)
        breakout_down = (close < lower) & (vol_percentile < 0.1)
        # 预期回归，做空上方突破，做多下方突破
        signal = np.where(breakout_up, 1, np.where(breakout_down, -1, 0))
        return pd.Series(signal, index=data.index)
