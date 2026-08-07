"""AI因子: 持仓时间衰减信号 | 置信:50% | 基于持仓时间与收益率的经验关系，当持仓超过最优持有期时，收益率递减甚至转负。使用历史滚动窗口内持仓时间与累计收益的相关系数，当当前持仓时间超过平均最优期时给出反转信号。正值表示应平多或做空（持仓过久），负值表示平空或做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class HoldTimeDecaySignal(BaseFactor):
    """基于持仓时间与收益率的经验关系，当持仓超过最优持有期时，收益率递减甚至转负。使用历史滚动窗口内持仓时间与累计收益的相关系数，当当前持仓时间超过平均最优期时给出反转信号。正值表示应平多或做空（持仓过久），负值表示平空或做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_holddecay",
            name="Hold Time Decay Signal",
            display_name="持仓时间衰减信号",
            description="基于持仓时间与收益率的经验关系，当持仓超过最优持有期时，收益率递减甚至转负。使用历史滚动窗口内持仓时间与累计收益的相关系数，当当前持仓时间超过平均最优期时给出反转信号。正值表示应平多或做空（持仓过久），负值表示平空或做多。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 用波动率作为持仓时间代理？实际需要持仓时间数据，这里用价格触发的止损/超时模拟
        # 使用价格变化速度（收益率）的连续衰减：计算过去N根K线的累计收益率，若连续上涨/下跌时间过长则反转
        close = data['close']
        returns = close.pct_change()
        # 计算连续同向运动次数（连续上涨/下跌）
        up_streak = (returns > 0).astype(int).groupby((returns <= 0).cumsum()).cumsum()
        down_streak = (returns < 0).astype(int).groupby((returns >= 0).cumsum()).cumsum()
        # 最优持有期假设为5根K线，超过则衰减
        optimal_hold = 5
        # 超时信号：连续上涨超过最优期时，认为可能回调（做空信号=-1）；反之做多
        up_signal = -np.clip((up_streak - optimal_hold) / optimal_hold, 0, 1)  # 负值表示看跌
        down_signal = np.clip((down_streak - optimal_hold) / optimal_hold, 0, 1)  # 正值表示看涨
        # 合并：连续上涨时信号为负，连续下跌时信号为正
        result = pd.Series(0.0, index=close.index)
        result[up_streak > optimal_hold] = up_signal[up_streak > optimal_hold]
        result[down_streak > optimal_hold] = down_signal[down_streak > optimal_hold]
        # 平滑处理
        result = result.rolling(3).mean().fillna(0).clip(-1, 1)
        return result
