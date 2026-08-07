"""AI因子: 时间区间耗尽因子 | 置信:60% | 统计价格在近期高位与低位形成的窄幅区间内运行的时长占比，结合RSI极端值判断区间耗尽的突破后反转风险。正值预示向上反转概率高（空头陷阱），负值预示向下反转概率高（多头陷阱），用于规避因长时间区间整理后突破失败造成的超时亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TimeToExhaustion(BaseFactor):
    """统计价格在近期高位与低位形成的窄幅区间内运行的时长占比，结合RSI极端值判断区间耗尽的突破后反转风险。正值预示向上反转概率高（空头陷阱），负值预示向下反转概率高（多头陷阱），用于规避因长时间区间整理后突破失败造成的超时亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tte",
            name="Time To Exhaustion",
            display_name="时间区间耗尽因子",
            description="统计价格在近期高位与低位形成的窄幅区间内运行的时长占比，结合RSI极端值判断区间耗尽的突破后反转风险。正值预示向上反转概率高（空头陷阱），负值预示向下反转概率高（多头陷阱），用于规避因长时间区间整理后突破失败造成的超时亏损。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        # 计算近期区间：过去20根K线最高价和最低价
        highest = high.rolling(20).max()
        lowest = low.rolling(20).min()
        range_height = highest - lowest
        # 价格在区间内的位置 (0到1)
        position = (close - lowest) / range_height.replace(0, np.nan)
        # 价格在过去20根K线内处于区间中部的比例
        in_middle = ((position > 0.3) & (position < 0.7)).rolling(20).sum() / 20
        # RSI 14
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        # 条件：长时间在中部运行（>0.7）且RSI极端
        long_consolidation = in_middle > 0.7
        overbought = rsi > 70
        oversold = rsi < 30
        # 风险输出：当长时间盘整且超买时，向上突破容易衰竭 -> 负值；超卖时向下突破容易衰竭 -> 正值
        result = pd.Series(0.0, index=data.index)
        bearish_risk = long_consolidation & overbought
        bullish_risk = long_consolidation & oversold
        strength_bearish = ((rsi - 70) / 30).clip(0, 1)
        strength_bullish = ((30 - rsi) / 30).clip(0, 1)
        result[bearish_risk] = -strength_bearish[bearish_risk]
        result[bullish_risk] = strength_bullish[bullish_risk]
        result = result.rolling(3).mean().fillna(0)
        return result.clip(-1, 1)
