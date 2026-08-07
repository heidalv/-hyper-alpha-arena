"""AI因子: 突破有效性 | 置信:45% | 检测近期价格突破是否伴随成交量放大与价格站稳，避免假突破导致的止损。计算收盘价突破N日高点/低点后，观察突破后成交量是否高于过去M日均值，以及价格是否回撤不超过一定比例。输出基于突破方向与确认强度的信号，范围[-1,1]。正值表示有效向上突破，负值表示有效向下突破。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class BreakoutValidityIndicator(BaseFactor):
    """检测近期价格突破是否伴随成交量放大与价格站稳，避免假突破导致的止损。计算收盘价突破N日高点/低点后，观察突破后成交量是否高于过去M日均值，以及价格是否回撤不超过一定比例。输出基于突破方向与确认强度的信号，范围[-1,1]。正值表示有效向上突破，负值表示有效向下突破。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_breakout_valid",
            name="Breakout Validity Indicator",
            display_name="突破有效性",
            description="检测近期价格突破是否伴随成交量放大与价格站稳，避免假突破导致的止损。计算收盘价突破N日高点/低点后，观察突破后成交量是否高于过去M日均值，以及价格是否回撤不超过一定比例。输出基于突破方向与确认强度的信号，范围[-1,1]。正值表示有效向上突破，负值表示有效向下突破。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np

        lookback = 10  # 突破周期
        confirm_bars = 2  # 确认等待K线数
        vol_ma = 5  # 成交量均线周期
        retrace_thresh = 0.3  # 最大回撤比例（相对于突破幅度）

        # 过去N日高低点
        high_n = data['high'].rolling(lookback).max().shift(1)
        low_n = data['low'].rolling(lookback).min().shift(1)

        # 成交量均值
        vol_avg = data['volume'].rolling(vol_ma).mean()

        # 突破信号 (当前收盘价突破前高/前低)
        up_break = data['close'] > high_n
        down_break = data['close'] < low_n

        # 确认条件: 突破后连续confirm_bars根K线价格不回到突破价以下/以上，且成交量放大
        # 使用shift实现未来对齐
        up_confirm = up_break.shift(confirm_bars-1)  # 至少提前confirm_bars-1根确认
        down_confirm = down_break.shift(confirm_bars-1)

        # 计算突破后成交量放大：突破当日成交量 > 均值，后续也维持
        vol_surge = data['volume'] > vol_avg * 1.2
        vol_confirm = vol_surge.rolling(confirm_bars).min() > 0  # 至少连续confirm_bars根

        # 回撤确认: 对于向上突破，之后最低价不能低于突破价*(1-retrace_thresh)
        # 用rolling min
        future_low = data['low'].rolling(confirm_bars).min().shift(-confirm_bars+1)
        future_high = data['high'].rolling(confirm_bars).max().shift(-confirm_bars+1)

        up_retrace_ok = future_low > data['close'] * (1 - retrace_thresh)
        down_retrace_ok = future_high < data['close'] * (1 + retrace_thresh)

        # 综合信号
        up_signal = up_break & (vol_confirm.shift(confirm_bars-1)) & up_retrace_ok
        down_signal = down_break & (vol_confirm.shift(confirm_bars-1)) & down_retrace_ok

        result = pd.Series(0.0, index=data.index)
        result[up_signal] = 1.0
        result[down_signal] = -1.0

        # 未来数据会产生前瞻偏差，但作为因子设计允许；实际回测需小心
        # 这里用shift(-confirm_bars+1)模拟未来确认，但因子应基于已知数据
        # 更严谨的做法：信号在突破后第confirm_bars根K线产生，即对确认条件用shift
        # 修正：信号延迟confirm_bars-1根产生
        result = result.shift(confirm_bars-1)  # 使得信号在确认后发出

        return result.fillna(0)
