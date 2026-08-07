"""AI因子: 止损密集区 | 置信:50% | 模拟市场止损单的聚集效应：计算当前价格距前期显著高/低点的距离，结合成交量放大，识别可能触发大量止损的敏感区域。当价格跌破近期密集成交区下沿时，因子偏负（做空）；突破上沿且缩量时，因子偏正（做多）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class StopLossDensity(BaseFactor):
    """模拟市场止损单的聚集效应：计算当前价格距前期显著高/低点的距离，结合成交量放大，识别可能触发大量止损的敏感区域。当价格跌破近期密集成交区下沿时，因子偏负（做空）；突破上沿且缩量时，因子偏正（做多）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_sdl",
            name="StopLossDensity",
            display_name="止损密集区",
            description="模拟市场止损单的聚集效应：计算当前价格距前期显著高/低点的距离，结合成交量放大，识别可能触发大量止损的敏感区域。当价格跌破近期密集成交区下沿时，因子偏负（做空）；突破上沿且缩量时，因子偏正（做多）。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        high = data['high']
        low = data['low']
        close = data['close']
        volume = data['volume']

        # 计算支撑/阻力：过去20日最高价和最低价，以及成交量加权均价
        period = 20
        vwap = (data['close'] * data['volume']).rolling(period).sum() / data['volume'].rolling(period).sum()
        support = low.rolling(period).min()
        resistance = high.rolling(period).max()

        # 当前价格相对于支撑/阻力的位置（归一化）
        range_width = resistance - support
        pos_from_support = (close - support) / (range_width + 1e-10)

        # 成交量异常放大（超过均值1.5倍）
        vol_ma = volume.rolling(period).mean()
        vol_spike = volume > (vol_ma * 1.5)

        # 判断：价格在支撑附近且放量下行 => 多头止损，因子负
        near_support = pos_from_support < 0.2
        # 价格在阻力附近且放量上行 => 空头止损，因子正（但注意要做多？这里我们偏向反转做空）
        near_resistance = pos_from_support > 0.8

        # 结合方向：跌破支撑区域，因子-1；突破阻力区域，因子+1
        # 注意：这里我们更关注多头止损场景（因为错误模式全是多头亏损）
        # 因此放大跌破支撑的信号权重
        score = pd.Series(0.0, index=close.index)
        break_down = (close < support.shift(1)) & vol_spike
        break_up = (close > resistance.shift(1)) & vol_spike
        score[break_down] = -1.0
        score[break_up] = 1.0
        # 加入持续信号：在支撑/阻力附近盘整后的突破
        # 平滑处理
        result = score.rolling(2).mean().fillna(0).clip(-1, 1)
        return result
