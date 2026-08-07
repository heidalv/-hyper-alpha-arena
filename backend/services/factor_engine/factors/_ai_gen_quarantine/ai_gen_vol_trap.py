"""AI因子: 量价陷阱检测 | 置信:60% | 识别成交量异常放大但价格未能有效突破（如收盘价在开盘价附近或涨幅不足）的情况，通常预示假突破后的反转，适用于止损亏损模式。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeSpikeTrapDetection(BaseFactor):
    """识别成交量异常放大但价格未能有效突破（如收盘价在开盘价附近或涨幅不足）的情况，通常预示假突破后的反转，适用于止损亏损模式。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_trap",
            name="Volume Spike Trap Detection",
            display_name="量价陷阱检测",
            description="识别成交量异常放大但价格未能有效突破（如收盘价在开盘价附近或涨幅不足）的情况，通常预示假突破后的反转，适用于止损亏损模式。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        open_p = data['open']
        close = data['close']
        volume = data['volume']
        # 成交量相对20日均值倍数
        vol_ma20 = volume.rolling(20).mean()
        vol_ratio = volume / vol_ma20
        # 价格区间跨度（相对开盘价）
        range_pct = (close - open_p) / open_p
        # 信号：成交量放大>2倍，但价格涨幅小于0.5%或跌幅小于-1%? 这里做多陷阱：放量但收盘价低于开盘价（弱势）
        trap = (vol_ratio > 2) & (range_pct < -0.005)  # 放量下跌弱势
        result = trap.astype(float) * -1  # 负信号表示看空陷阱
        # 平滑处理
        return result.rolling(3).mean().fillna(0).clip(-1, 1)
