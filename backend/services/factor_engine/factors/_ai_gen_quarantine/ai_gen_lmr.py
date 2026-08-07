"""AI因子: 流动性磁铁反转预警 | 置信:60% | 识别价格接近近期高点或低点且伴随成交量异常放大的情况，这类位置易发生流动性磁铁反转（价格先吸引止损/清算订单然后反转）。当价格位于近期区间上沿且成交量显著增加时，发出反转做空信号（负值）；位于下沿且成交量激增时，发出反转做多信号（正值）。可有效应对liq_magnet_reversal亏损模式。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidationMagnetReversalWarning(BaseFactor):
    """识别价格接近近期高点或低点且伴随成交量异常放大的情况，这类位置易发生流动性磁铁反转（价格先吸引止损/清算订单然后反转）。当价格位于近期区间上沿且成交量显著增加时，发出反转做空信号（负值）；位于下沿且成交量激增时，发出反转做多信号（正值）。可有效应对liq_magnet_reversal亏损模式。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_lmr",
            name="Liquidation Magnet Reversal Warning",
            display_name="流动性磁铁反转预警",
            description="识别价格接近近期高点或低点且伴随成交量异常放大的情况，这类位置易发生流动性磁铁反转（价格先吸引止损/清算订单然后反转）。当价格位于近期区间上沿且成交量显著增加时，发出反转做空信号（负值）；位于下沿且成交量激增时，发出反转做多信号（正值）。可有效应对liq_magnet_reversal亏损模式。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        high = data['high']
        low = data['low']
        volume = data['volume']
        period = 14
        # 计算近期最高最低
        recent_high = high.rolling(window=period).max()
        recent_low = low.rolling(window=period).min()
        # 价格在区间中的位置（0~1）
        range_width = recent_high - recent_low
        range_width = range_width.replace(0, np.nan)  # avoid div0
        pos = (close - recent_low) / range_width
        # 成交量相对变化：当前volume vs 前期均值
        vol_ma = volume.rolling(window=period).mean()
        vol_ratio = volume / vol_ma
        # 当价格接近上沿（>0.8）且成交量放大>1.5倍，则看跌信号（-1）；接近下沿（<0.2）且成交量放大>1.5倍，则看涨信号（+1）
        upper_condition = (pos > 0.8) & (vol_ratio > 1.5)
        lower_condition = (pos < 0.2) & (vol_ratio > 1.5)
        result = pd.Series(0.0, index=close.index)
        result[upper_condition] = -1.0
        result[lower_condition] = 1.0
        # 平滑处理，避免突变
        result = result.rolling(window=3).mean().fillna(0.0)
        return result
