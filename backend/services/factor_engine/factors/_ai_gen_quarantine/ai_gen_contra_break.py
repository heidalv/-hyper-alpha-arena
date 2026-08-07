"""AI因子: 反向突破反转 | 置信:50% | 检测价格突破近期支撑/阻力后迅速回落的假突破模式。计算当前价格是否突破过去N根K线的最高/最低，且随后收盘价回到区间内，同时成交量放大确认。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ContrarianBreakoutReversal(BaseFactor):
    """检测价格突破近期支撑/阻力后迅速回落的假突破模式。计算当前价格是否突破过去N根K线的最高/最低，且随后收盘价回到区间内，同时成交量放大确认。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_contra_break",
            name="Contrarian Breakout Reversal",
            display_name="反向突破反转",
            description="检测价格突破近期支撑/阻力后迅速回落的假突破模式。计算当前价格是否突破过去N根K线的最高/最低，且随后收盘价回到区间内，同时成交量放大确认。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        N = 14
        lookback = 5
        # 过去N根K线的最高与最低
        high_roll = data['high'].rolling(N).max()
        low_roll = data['low'].rolling(N).min()
        # 突破检测：当前最高价上穿过去N根最高则视为向上假突破候选
        break_up = (data['close'] > high_roll.shift(1)) & (data['close'] < high_roll)  # 突破后收盘回落？
        break_down = (data['close'] < low_roll.shift(1)) & (data['close'] > low_roll)
        # 更精确：检查突破后是否在接下来几根内反转
        # 简化：使用当前bar的收盘价是否远离突破点
        # 计算突破强度
        up_dist = (data['high'] - high_roll.shift(1)) / (high_roll.shift(1) + 1e-10)
        down_dist = (low_roll.shift(1) - data['low']) / (low_roll.shift(1) + 1e-10)
        # 量确认
        vol_surge = data['volume'] > data['volume'].rolling(lookback).mean() * 1.5
        # 信号：向上假突破（看跌）负值，向下假突破（看涨）正值
        signal_up = np.where((up_dist > 0.01) & (data['close'] < high_roll.shift(1)) & vol_surge, -1, 0)
        signal_down = np.where((down_dist > 0.01) & (data['close'] > low_roll.shift(1)) & vol_surge, 1, 0)
        signal = np.maximum(signal_up, signal_down)
        return pd.Series(signal, index=data.index)
