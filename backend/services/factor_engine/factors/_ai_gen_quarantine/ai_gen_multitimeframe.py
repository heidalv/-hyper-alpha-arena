"""AI因子: 多周期趋势一致因子 | 置信:50% | 计算短周期（5期）、中周期（20期）、长周期（60期）SMA的排序一致性，如果三个均线方向不一致（多头排列或空头排列不明确），因子为负，表示市场环境不明朗，做多风险高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Multi_Timeframe_Alignment(BaseFactor):
    """计算短周期（5期）、中周期（20期）、长周期（60期）SMA的排序一致性，如果三个均线方向不一致（多头排列或空头排列不明确），因子为负，表示市场环境不明朗，做多风险高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_multitimeframe",
            name="Multi-Timeframe Alignment",
            display_name="多周期趋势一致因子",
            description="计算短周期（5期）、中周期（20期）、长周期（60期）SMA的排序一致性，如果三个均线方向不一致（多头排列或空头排列不明确），因子为负，表示市场环境不明朗，做多风险高。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close'].values
        sma5 = pd.Series(close).rolling(5, min_periods=5).mean().values
        sma20 = pd.Series(close).rolling(20, min_periods=20).mean().values
        sma60 = pd.Series(close).rolling(60, min_periods=60).mean().values
        # 计算每个时间点的顺序：若sma5 > sma20 > sma60为1（多头排列），反之为-1（空头排列），否则为0
        # 使用np.sign判断相对大小
        order = np.zeros_like(close)
        mask = ~(np.isnan(sma5) | np.isnan(sma20) | np.isnan(sma60))
        # 多头排列：5>20>60
        bull = (sma5 > sma20) & (sma20 > sma60)
        # 空头排列：5<20<60
        bear = (sma5 < sma20) & (sma20 < sma60)
        order[bull] = 1.0
        order[bear] = -1.0
        # 其他情况（混乱）保留0，映射到-1? 我们希望混乱时因子为负，所以给混乱赋值-0.5
        # 但为了连续，我们使用指数加权：实际使用排列强度
        # 简单方法：order已经为0时返回-0.5
        result = np.where(order == 0, -0.5, order)
        return pd.Series(result, index=data.index)
