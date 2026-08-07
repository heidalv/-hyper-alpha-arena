"""AI因子: 量价背离因子 | 置信:60% | 通过滚动相关系数衡量价格与成交量的同步性。当价格波动小但成交量异常放大（负相关或低相关）时，市场可能缺乏方向，容易发生亏损。计算20期价格变化与成交量变化的Spearman秩相关，取绝对值后取负值。绝对值低时相关弱，因子接近-1指示震荡/未知状态；绝对值高时相关强，因子接近+1指示趋势状态。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Price_Divergence_Factor(BaseFactor):
    """通过滚动相关系数衡量价格与成交量的同步性。当价格波动小但成交量异常放大（负相关或低相关）时，市场可能缺乏方向，容易发生亏损。计算20期价格变化与成交量变化的Spearman秩相关，取绝对值后取负值。绝对值低时相关弱，因子接近-1指示震荡/未知状态；绝对值高时相关强，因子接近+1指示趋势状态。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volume_diverge",
            name="Volume-Price Divergence Factor",
            display_name="量价背离因子",
            description="通过滚动相关系数衡量价格与成交量的同步性。当价格波动小但成交量异常放大（负相关或低相关）时，市场可能缺乏方向，容易发生亏损。计算20期价格变化与成交量变化的Spearman秩相关，取绝对值后取负值。绝对值低时相关弱，因子接近-1指示震荡/未知状态；绝对值高时相关强，因子接近+1指示趋势状态。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 价格变化率
        price_ret = data['close'].pct_change()
        volume_ret = data['volume'].pct_change()
        # 滚动20期Spearman相关
        def spearman_corr(x, y):
            if len(x) < 2 or np.std(x)==0 or np.std(y)==0:
                return 0.0
            return x.corr(y, method='spearman')

        # 滚动计算相关系数（使用apply需要处理对齐）
        corr = pd.Series(index=data.index, dtype=float)
        for i in range(20, len(data)+1):
            sub_p = price_ret.iloc[i-20:i]
            sub_v = volume_ret.iloc[i-20:i]
            corr.iloc[i-1] = spearman_corr(sub_p, sub_v)
        # 取相关系数的绝对值，表示相关强度
        abs_corr = corr.abs()
        # 映射：相关强度高 -> +1 (趋势明确)，低 -> -1 (无方向震荡)
        # 使用线性映射：2 * (abs_corr - 0.5) 将[0,1]映射到[-1,1]
        result = 2 * (abs_corr - 0.5)
        result = result.fillna(0).clip(-1, 1)
        return result
