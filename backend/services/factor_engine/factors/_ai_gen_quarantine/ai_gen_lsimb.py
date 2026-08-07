"""AI因子: 多空不平衡反转信号 | 置信:70% | 统计最近N笔交易中多空止损比例，当某一方向止损过多时，表明市场可能已过度反应，预期反转。例如，若空头止损次数远多于多头，则倾向于做多（正信号）。使用假设的多空损益序列模拟。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LongShortImbalanceReversal(BaseFactor):
    """统计最近N笔交易中多空止损比例，当某一方向止损过多时，表明市场可能已过度反应，预期反转。例如，若空头止损次数远多于多头，则倾向于做多（正信号）。使用假设的多空损益序列模拟。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_lsimb",
            name="Long-Short Imbalance Reversal",
            display_name="多空不平衡反转信号",
            description="统计最近N笔交易中多空止损比例，当某一方向止损过多时，表明市场可能已过度反应，预期反转。例如，若空头止损次数远多于多头，则倾向于做多（正信号）。使用假设的多空损益序列模拟。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 使用价格波动模拟多空损益：假设每次突破布林带外沿时开仓，记录止损次数
        close = data['close']
        high = data['high']
        low = data['low']
        # 计算布林带
        ma = close.rolling(20).mean()
        std = close.rolling(20).std()
        upper = ma + 2*std
        lower = ma - 2*std
        # 简单模拟：当价格突破上轨时做空（假设做空），突破下轨时做多
        # 止损条件：价格向不利方向移动超过1.5倍ATR（简化用1.5%硬止损）
        atr = (high - low).rolling(14).mean()
        stop_dist = 1.5 * atr / close  # 百分比止损
        # 记录最近N次（比如20次）触发信号及其盈亏
        N = 20
        # 用shift获取开仓信号后收益
        long_entry = (close < lower) & (close.shift(1) >= lower.shift(1))
        short_entry = (close > upper) & (close.shift(1) <= upper.shift(1))
        # 计算后续价格变动
        future_ret = close.pct_change().shift(-1)
        # 简化：直接统计每根K线是否达到止损（用历史数据模拟）
        long_stop = (future_ret < -stop_dist) & long_entry
        short_stop = (future_ret > stop_dist) & short_entry
        # 累计最近N根K线内的止损次数
        long_stop_count = long_stop.rolling(N).sum()
        short_stop_count = short_stop.rolling(N).sum()
        total = long_stop_count + short_stop_count + 1e-8
        # 不平衡指标：当空头止损占比高时（short_stop_count / total > 0.6），做多信号为正
        imbalance = (short_stop_count - long_stop_count) / total
        # 缩放至[-1,1]
        result = imbalance * 2  # 范围约[-2,2]，再clip
        result = result.clip(-1, 1).fillna(0)
        return result
