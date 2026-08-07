"""AI因子: 动量衰减因子 | 置信:60% | 检测价格上涨但成交量萎缩的动量衰减模式。计算过去20日收益率与成交量的Spearman秩相关系数，取负值后映射。当系数为强负相关（价升量缩）时因子接近-1，预示回调风险；当量价同步时因子为正。这种模式常见于假突破导致亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Momentum_Decay_Factor(BaseFactor):
    """检测价格上涨但成交量萎缩的动量衰减模式。计算过去20日收益率与成交量的Spearman秩相关系数，取负值后映射。当系数为强负相关（价升量缩）时因子接近-1，预示回调风险；当量价同步时因子为正。这种模式常见于假突破导致亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mdf",
            name="Momentum Decay Factor",
            display_name="动量衰减因子",
            description="检测价格上涨但成交量萎缩的动量衰减模式。计算过去20日收益率与成交量的Spearman秩相关系数，取负值后映射。当系数为强负相关（价升量缩）时因子接近-1，预示回调风险；当量价同步时因子为正。这种模式常见于假突破导致亏损。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        from scipy.stats import spearmanr
        period = 20
        # 计算日收益率
        returns = data['close'].pct_change()
        # 滚动计算秩相关系数
        def roll_corr(ret, vol):
            if len(ret) < period:
                return 0
            r, _ = spearmanr(ret, vol)
            return r
        # 应用滚动窗口
        corr = returns.rolling(window=period).apply(lambda x: roll_corr(x, data['volume'].loc[x.index]), raw=False)
        # 取负值并映射到[-1,1]（本身已在[-1,1]）
        factor = -corr
        # 极端值裁剪
        return factor.fillna(0).clip(-1, 1)
