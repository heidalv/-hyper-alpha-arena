"""AI因子: 流动性磁铁反转 | 置信:60% | 捕捉价格快速突破前N根K线的高点或低点后立即反转，同时成交量异常放大，暗示流动性吸引后的反向走势。因子值在-1（空头反转信号）到+1（多头反转信号）之间。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidityMagnetReversal(BaseFactor):
    """捕捉价格快速突破前N根K线的高点或低点后立即反转，同时成交量异常放大，暗示流动性吸引后的反向走势。因子值在-1（空头反转信号）到+1（多头反转信号）之间。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_lqm_rev",
            name="Liquidity Magnet Reversal",
            display_name="流动性磁铁反转",
            description="捕捉价格快速突破前N根K线的高点或低点后立即反转，同时成交量异常放大，暗示流动性吸引后的反向走势。因子值在-1（空头反转信号）到+1（多头反转信号）之间。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 输入data: DataFrame with columns ['open','high','low','close','volume']
        # 参数
        n = 5  # 前N根K线
        vol_mult = 2.0  # 成交量放大倍数阈值
    
        # 计算前N根K线的最高价、最低价
        high_n = data['high'].rolling(n, min_periods=n).max().shift(1)
        low_n = data['low'].rolling(n, min_periods=n).min().shift(1)
    
        # 成交量移动平均值
        vol_ma = data['volume'].rolling(20, min_periods=10).mean()
        vol_ratio = data['volume'] / vol_ma
    
        # 多头反转信号：当前价格突破前N根K线高点（高开或盘中突破），但收盘回落到高点以下，且成交量放大
        long_reversal = (data['high'] >= high_n) & (data['close'] < high_n) & (vol_ratio > vol_mult)
        # 空头反转信号：当前价格跌破前N根K线低点，但收盘回升到低点以上
        short_reversal = (data['low'] <= low_n) & (data['close'] > low_n) & (vol_ratio > vol_mult)
    
        # 信号强度：结合突破幅度和成交量比例
        long_strength = (high_n - data['close']) / (data['high'] - data['low'] + 1e-10)  # 反转幅度比例
        short_strength = (data['close'] - low_n) / (data['high'] - data['low'] + 1e-10)
    
        # 综合因子值 [-1,1]
        factor = pd.Series(0.0, index=data.index)
        factor[long_reversal] = long_strength[long_reversal] * (vol_ratio[long_reversal] / vol_mult).clip(0,1)
        factor[short_reversal] = -short_strength[short_reversal] * (vol_ratio[short_reversal] / vol_mult).clip(0,1)
    
        return factor.fillna(0.0)
