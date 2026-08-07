"""AI因子: 均值回复距离衰减 | 置信:60% | 计算当前价格相对于20日简单移动平均的偏离程度，并乘以一个基于时间衰减的权重（价格远离均线的时间越长，衰减越强）。高偏离且快速衰减表示短期均值回复概率大，低偏离或缓慢衰减表示趋势延续。正值表示回归倾向，负值表示趋势强化。有助于过滤掉 regime=unknown 下的无效回调。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MeanReversionDistanceDecay(BaseFactor):
    """计算当前价格相对于20日简单移动平均的偏离程度，并乘以一个基于时间衰减的权重（价格远离均线的时间越长，衰减越强）。高偏离且快速衰减表示短期均值回复概率大，低偏离或缓慢衰减表示趋势延续。正值表示回归倾向，负值表示趋势强化。有助于过滤掉 regime=unknown 下的无效回调。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mean_revert_decay",
            name="Mean Reversion Distance Decay",
            display_name="均值回复距离衰减",
            description="计算当前价格相对于20日简单移动平均的偏离程度，并乘以一个基于时间衰减的权重（价格远离均线的时间越长，衰减越强）。高偏离且快速衰减表示短期均值回复概率大，低偏离或缓慢衰减表示趋势延续。正值表示回归倾向，负值表示趋势强化。有助于过滤掉 regime=unknown 下的无效回调。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        ma20 = close.rolling(20).mean()
        zscore = (close - ma20) / close.rolling(20).std()
        # 计算价格在均线外持续的天数（>1个标准差视为偏离）
        above = (zscore > 1).astype(int)
        below = (zscore < -1).astype(int)
        # 累计偏离天数
        days_above = above * (above.groupby((above.diff() != 0).cumsum()).cumcount() + 1)
        days_below = below * (below.groupby((below.diff() != 0).cumsum()).cumcount() + 1)
        decay = np.exp(-0.2 * (days_above + days_below))
        # 符号与zscore一致，强度经衰减调整
        result = -zscore * decay
        return result.clip(-1, 1).fillna(0)
