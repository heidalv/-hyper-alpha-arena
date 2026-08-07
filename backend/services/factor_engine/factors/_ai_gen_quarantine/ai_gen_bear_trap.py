"""AI因子: 空头陷阱检测 | 置信:65% | 当价格突破近期低点后迅速反弹，且突破时成交量放大但随后萎缩，表明空头陷阱，做空风险高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class BearTrapDetector(BaseFactor):
    """当价格突破近期低点后迅速反弹，且突破时成交量放大但随后萎缩，表明空头陷阱，做空风险高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_bear_trap",
            name="Bear_Trap_Detector",
            display_name="空头陷阱检测",
            description="当价格突破近期低点后迅速反弹，且突破时成交量放大但随后萎缩，表明空头陷阱，做空风险高。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # data: OHLCV DataFrame
        # 计算近期低点（过去20根K线最低价）
        low_20 = data['low'].rolling(20).min()
        # 突破信号：当前收盘价低于低点1%
        breakout = data['close'] < low_20 * 0.99
        # 突破后反弹：下一根K线收盘价回到突破前低点之上
        rebound = data['close'].shift(-1) > low_20
        # 成交量放大条件：突破时成交量是前5日均量的1.5倍
        vol_avg = data['volume'].rolling(5).mean()
        vol_surge = data['volume'] > vol_avg * 1.5
        # 反弹时成交量萎缩：下一根K线成交量小于均量
        vol_shrink = data['volume'].shift(-1) < vol_avg
        # 综合信号
        signal = (breakout & rebound & vol_surge & vol_shrink).astype(float)
        # 映射到[-1,1]，正表示空头陷阱（为空头不利）
        result = signal * -1  # 负值表示做空风险大
        # 平滑处理
        result = result.rolling(3).mean().fillna(0).clip(-1, 1)
        return result
