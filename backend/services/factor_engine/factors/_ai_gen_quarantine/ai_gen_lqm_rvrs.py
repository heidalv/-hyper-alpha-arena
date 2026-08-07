"""AI因子: 流动性磁铁反转陷阱 | 置信:60% | 检测价格快速冲击近期极值后立即反转且伴随成交量异常放大的行为。计算近期高点与低点，标记价格创出n周期新高（或新低）后1-2根K线内反向突破，同时成交量相对近期均值放大超过阈值。返回[-1,1]，负值表示空头陷阱信号，正值表示多头陷阱信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidityMagnetReversalTrap(BaseFactor):
    """检测价格快速冲击近期极值后立即反转且伴随成交量异常放大的行为。计算近期高点与低点，标记价格创出n周期新高（或新低）后1-2根K线内反向突破，同时成交量相对近期均值放大超过阈值。返回[-1,1]，负值表示空头陷阱信号，正值表示多头陷阱信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_lqm_rvrs",
            name="Liquidity Magnet Reversal Trap",
            display_name="流动性磁铁反转陷阱",
            description="检测价格快速冲击近期极值后立即反转且伴随成交量异常放大的行为。计算近期高点与低点，标记价格创出n周期新高（或新低）后1-2根K线内反向突破，同时成交量相对近期均值放大超过阈值。返回[-1,1]，负值表示空头陷阱信号，正值表示多头陷阱信号。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        n = 5
        lookback = 20
        vol_mult = 1.5
        high = data['high']
        low = data['low']
        close = data['close']
        volume = data['volume']
        rolling_high = high.rolling(n).max()
        rolling_low = low.rolling(n).min()
        # 创近期新高且下一根收盘低于该高点一定比例
        new_high = (high == rolling_high) & (rolling_high.shift(1) < rolling_high)
        new_low = (low == rolling_low) & (rolling_low.shift(1) > rolling_low)
        # 反转判断：创高后收盘低于前一根收盘？更稳健：创高后下一根K线开盘低于前高且收盘低于前高
        reversal_down = new_high.shift(1) & (close < close.shift(1)) & (low < low.shift(1))
        reversal_up = new_low.shift(1) & (close > close.shift(1)) & (high > high.shift(1))
        # 成交量放大条件
        avg_vol = volume.rolling(lookback).mean()
        vol_surge = volume > vol_mult * avg_vol
        signal = pd.Series(0.0, index=data.index)
        signal[reversal_down & vol_surge] = -1.0
        signal[reversal_up & vol_surge] = 1.0
        return signal
