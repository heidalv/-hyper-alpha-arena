"""AI因子: 时间衰减动量 | 置信:50% | 考虑持仓时间窗口内的价格变化，但随时间衰减，惩罚长时间未产生利润的持仓。计算过去T1到T2区间内的收益率，乘以时间衰减权重，再与近期短期动量组合。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Time_Decay_Momentum(BaseFactor):
    """考虑持仓时间窗口内的价格变化，但随时间衰减，惩罚长时间未产生利润的持仓。计算过去T1到T2区间内的收益率，乘以时间衰减权重，再与近期短期动量组合。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_time_decay_momentum",
            name="Time Decay Momentum",
            display_name="时间衰减动量",
            description="考虑持仓时间窗口内的价格变化，但随时间衰减，惩罚长时间未产生利润的持仓。计算过去T1到T2区间内的收益率，乘以时间衰减权重，再与近期短期动量组合。",
            category="behavioral",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        T_long = 60  # 长期窗口
        T_short = 10
        decay = np.exp(-np.arange(T_long) / 20)  # 指数衰减
        # 计算加权收益率
        ret = data['close'].pct_change()
        weighted_ret = ret.rolling(T_long).apply(lambda x: np.dot(x, decay[::-1]) / decay.sum(), raw=True)
        # 短期动量
        short_ret = data['close'].pct_change(T_short)
        # 组合：当长期加权收益为正但短期为负时，可能回调风险
        composite = weighted_ret * short_ret  # 两者同号为正，异号为负
        # 归一化到[-1,1]
        result = composite / (composite.abs().rolling(50).mean() + 1e-10)
        result = result.clip(-1, 1)
        return result.fillna(0)
