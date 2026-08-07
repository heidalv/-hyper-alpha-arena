"""AI因子: 动量稳定性指数 | 置信:50% | 衡量近期动量的一致性与稳定性。计算过去10日收益率的标准差与平均绝对收益率的比值，比值越大表示动量越不稳定，市场方向不明，返回负值；比值小则表示趋势稳定，返回正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Momentum_Stability_Index(BaseFactor):
    """衡量近期动量的一致性与稳定性。计算过去10日收益率的标准差与平均绝对收益率的比值，比值越大表示动量越不稳定，市场方向不明，返回负值；比值小则表示趋势稳定，返回正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momentum_stability",
            name="Momentum Stability Index",
            display_name="动量稳定性指数",
            description="衡量近期动量的一致性与稳定性。计算过去10日收益率的标准差与平均绝对收益率的比值，比值越大表示动量越不稳定，市场方向不明，返回负值；比值小则表示趋势稳定，返回正值。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        ret = close.pct_change()
        # 计算过去10天收益率的标准差
        std_ret = ret.rolling(10).std()
        # 平均绝对收益率
        abs_ret = ret.abs().rolling(10).mean()
        # 避免除零
        ratio = std_ret / (abs_ret + 1e-10)
        # 归一化到[-1,1]：ratio通常大于等于0，大于1.5时认为不稳定
        # 使用阈值1.5，高于则-1，低于则1
        unstable = ratio > 1.5
        result = np.where(unstable, -1.0, 1.0)
        return pd.Series(result, index=data.index)
