"""AI因子: 相对成交量流动性缺口 | 置信:60% | 衡量当前成交量相对于近期均值的偏离程度，并结合价格涨跌方向判断流动性是否异常。当成交量异常放大但价格未有效突破时，表明市场流动性陷阱，容易触发止损或反转；因子输出负值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RelativeVolumeLiquidityGap(BaseFactor):
    """衡量当前成交量相对于近期均值的偏离程度，并结合价格涨跌方向判断流动性是否异常。当成交量异常放大但价格未有效突破时，表明市场流动性陷阱，容易触发止损或反转；因子输出负值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liquiditygap",
            name="Relative Volume-Liquidity Gap",
            display_name="相对成交量流动性缺口",
            description="衡量当前成交量相对于近期均值的偏离程度，并结合价格涨跌方向判断流动性是否异常。当成交量异常放大但价格未有效突破时，表明市场流动性陷阱，容易触发止损或反转；因子输出负值。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        volume = data['volume']
        close = data['close']
        # 成交量均值（20日）
        vol_ma = volume.rolling(20).mean()
        # 成交量相对偏离
        vol_ratio = volume / vol_ma
        # 价格变化率（5日）
        price_change = close.pct_change(5)
        # 计算流动性缺口：成交量放大（>1.5倍均值）但价格涨跌幅较小（<2%）
        high_vol = vol_ratio > 1.5
        low_price_move = price_change.abs() < 0.02
        gap_signal = high_vol & low_price_move
        # 成交量萎缩且价格剧烈波动也视为异常
        low_vol = vol_ratio < 0.5
        high_price_move = price_change.abs() > 0.05
        gap_signal2 = low_vol & high_price_move
        # 合并信号，输出-1（异常）或+1（正常）
        factor = pd.Series(1.0, index=data.index)
        factor[gap_signal | gap_signal2] = -1.0
        # 平滑
        return factor.rolling(2).mean().fillna(0.0).clip(-1, 1)
