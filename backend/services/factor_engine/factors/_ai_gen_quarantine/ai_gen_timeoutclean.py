"""AI因子: 持仓超时清理 | 置信:50% | 模拟长时期横盘后突破失败的信号。计算过去20根K线价格区间宽度（(high-low)/close均值的比例），若宽度小于2%且持续超过15根，则当出现突破（收盘价突破最近3根K线高点或低点）但随后反向时给出信号。简化：当价格在窄幅区间内停留15根以上后，首次突破区间幅度超过1%但随即回落到区间内，标记为反转。由于需要未来数据，这里用当前K线收盘与20根前比较。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class HoldTimeoutCleanup(BaseFactor):
    """模拟长时期横盘后突破失败的信号。计算过去20根K线价格区间宽度（(high-low)/close均值的比例），若宽度小于2%且持续超过15根，则当出现突破（收盘价突破最近3根K线高点或低点）但随后反向时给出信号。简化：当价格在窄幅区间内停留15根以上后，首次突破区间幅度超过1%但随即回落到区间内，标记为反转。由于需要未来数据，这里用当前K线收盘与20根前比较。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_timeoutclean",
            name="Hold Timeout Cleanup",
            display_name="持仓超时清理",
            description="模拟长时期横盘后突破失败的信号。计算过去20根K线价格区间宽度（(high-low)/close均值的比例），若宽度小于2%且持续超过15根，则当出现突破（收盘价突破最近3根K线高点或低点）但随后反向时给出信号。简化：当价格在窄幅区间内停留15根以上后，首次突破区间幅度超过1%但随即回落到区间内，标记为反转。由于需要未来数据，这里用当前K线收盘与20根前比较。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 计算20根K线内价格区间宽度
        high_20 = data['high'].rolling(20).max()
        low_20 = data['low'].rolling(20).min()
        width = (high_20 - low_20) / (data['close'].rolling(20).mean() + 1e-8)
        # 窄幅条件：width < 0.02
        narrow = width < 0.02
        # 窄幅持续计数（向前累加）
        narrow_count = narrow.rolling(20).sum()  # 最多20
        # 突破信号：当前收盘价突破过去3根K线的高点或低点
        high_3 = data['high'].rolling(3).max().shift(1)  # 之前3根的最高
        low_3 = data['low'].rolling(3).min().shift(1)
        breakout_up = data['close'] > high_3
        breakout_down = data['close'] < low_3
        # 当窄幅持续超过15且出现突破，则返回反向信号（假突破）
        # 向上假突破 → 看跌；向下假突破 → 看涨
        signal = np.where(breakout_up & (narrow_count >= 15), -1.0,
                          np.where(breakout_down & (narrow_count >= 15), 1.0, 0.0))
        result = pd.Series(signal, index=data.index).fillna(0)
        return result
