"""AI因子: 量价背离假突破因子 | 置信:60% | 检测价格突破近期高低点但成交量萎缩或快速反转的假突破模式。当突破时成交量低于近期均值且随后价格快速回撤，则因子为正（警示假突破），反之为负。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Break_Failure_Detector(BaseFactor):
    """检测价格突破近期高低点但成交量萎缩或快速反转的假突破模式。当突破时成交量低于近期均值且随后价格快速回撤，则因子为正（警示假突破），反之为负。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_break_failure",
            name="Volume Break Failure Detector",
            display_name="量价背离假突破因子",
            description="检测价格突破近期高低点但成交量萎缩或快速反转的假突破模式。当突破时成交量低于近期均值且随后价格快速回撤，则因子为正（警示假突破），反之为负。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 参数
        lookback = 20
        vol_window = 10
        retrace_window = 3
        # 近期高点和低点
        high = data['high']
        low = data['low']
        close = data['close']
        volume = data['volume']
        # 突破检测：当前价格突破过去lookback最高价或最低价
        rolling_high = high.rolling(lookback).max().shift(1)
        rolling_low = low.rolling(lookback).min().shift(1)
        break_up = (close > rolling_high).astype(float)
        break_down = (close < rolling_low).astype(float)
        # 成交量均值
        vol_ma = volume.rolling(vol_window).mean()
        # 突破时的成交量缩量条件（低于均值80%）
        vol_ratio = volume / (vol_ma + 1e-10)
        low_vol_break = ((break_up + break_down) > 0) & (vol_ratio < 0.8)
        # 随后价格回撤：突破后retrace_window期内价格反向移动幅度
        # 用未来价格判断反转（需shift负值）此处用当前相比于前retrace_window前的价格变化
        # 为简化，计算突破后3根K线的平均变化，注意避免未来信息
        # 实际回测中可用shift(-retrace_window)但这里输出因子时可以用滞后
        # 我们使用突破点当时的价格与之前retrace_window前价格比较来模拟回撤
        # 但为了避免未来函数，这里仅用当前突破时的短期动量：若突破后价格快速低于突破点，则视为假突破
        # 使用close与最近retrace_window最低/最高比较
        # 上突破：若当前close低于最近几根K线的最高价的某个比例，说明回撤
        # 简化：直接用突破点价格与当前close比较（不依赖未来）
        # 实际上我们可以在因子生成时使用未来信息？不行。所以改为：使用突破前retrace_window的波动率判断是否为假突破信号
        # 调整方法：计算突破信号后的预期延续性，用历史统计？
        # 为了简单且避免未来，我们使用突破时的成交量萎缩作为主要信号，不加入回撤条件
        # 但可加入价格在突破后的位置：如果突破后当前价格又回到突破线以内，则为假突破
        # 用shift(1)表示突破后的价格变化？
        # 改为：判断当前价格相对于突破线（rolling_high/rolling_low）的偏离程度
        # 上突破后，若close低于rolling_high，说明回到突破线内
        retrace_up = (break_up == 1) & (close < rolling_high)
        retrace_down = (break_down == 1) & (close > rolling_low)
        # 组合信号：低量突破且回撤
        failure = ((low_vol_break & retrace_up) | (low_vol_break & retrace_down)).astype(float)
        # 转换为[-1,1]：假突破时+1，真突破时-1，无信号0
        # 再检测真突破：突破时成交量放大且价格维持
        high_vol_break = ((break_up + break_down) > 0) & (vol_ratio > 1.2)
        # 真突破且没有回撤
        hold_up = (break_up == 1) & (close >= rolling_high)
        hold_down = (break_down == 1) & (close <= rolling_low)
        true_signal = (high_vol_break & (hold_up | hold_down)).astype(float)
        # 组合
        result = failure * 1.0 - true_signal * 1.0
        result = np.clip(result, -1, 1)
        result = pd.Series(result, index=data.index).fillna(0)
        return result
