"""AI因子: 清算反转强度 | 置信:65% | 结合价格反转动量与成交量异常，捕捉类似liq_magnet_reversal模式中清算引发快速反转的时点。当价格短期急跌后快速反弹且成交量放大时，因子为正；急涨后快速回落且放量为负。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidationReversalStrength(BaseFactor):
    """结合价格反转动量与成交量异常，捕捉类似liq_magnet_reversal模式中清算引发快速反转的时点。当价格短期急跌后快速反弹且成交量放大时，因子为正；急涨后快速回落且放量为负。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rev_liq",
            name="Liquidation Reversal Strength",
            display_name="清算反转强度",
            description="结合价格反转动量与成交量异常，捕捉类似liq_magnet_reversal模式中清算引发快速反转的时点。当价格短期急跌后快速反弹且成交量放大时，因子为正；急涨后快速回落且放量为负。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 计算短期反转指标
        close = data['close']
        high = data['high']
        low = data['low']
        volume = data['volume']
    
        # 过去3根K线的价格变化率
        ret_3 = close.pct_change(3)
        # 当前K线的振幅
        range_pct = (high - low) / close.shift(1)
        # 成交量相对过去20日均值
        vol_ma = volume.rolling(20).mean()
        vol_ratio = volume / vol_ma
    
        # 反转信号：价格先跌后涨 (或先涨后跌) 且伴随高成交量
        # 用短期均线偏离判断: 最近3根K线最低点低于前10根最低点均值，但收盘回升
        low_10 = low.rolling(10).min()
        cond_dip = (low < low_10.shift(1)) & (close > close.shift(1)) & (vol_ratio > 1.5)
        cond_spike = (high > high.rolling(10).max().shift(1)) & (close < close.shift(1)) & (vol_ratio > 1.5)
    
        factor = pd.Series(0, index=data.index)
        factor[cond_dip] = 1.0 * (range_pct / range_pct.rolling(20).mean()).clip(0, 2) / 2
        factor[cond_spike] = -1.0 * (range_pct / range_pct.rolling(20).mean()).clip(0, 2) / 2
        return factor
