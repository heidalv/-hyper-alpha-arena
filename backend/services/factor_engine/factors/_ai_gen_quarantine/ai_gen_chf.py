"""AI因子: 混沌度因子 | 置信:50% | 基于价格序列近似熵（ApEn），衡量市场有序性。高熵值表示无序混乱（regime=unknown），因子接近-1；低熵表示有序趋势，因子接近+1。使用简化的滚动相关系数作为替代。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Chaosfactor(BaseFactor):
    """基于价格序列近似熵（ApEn），衡量市场有序性。高熵值表示无序混乱（regime=unknown），因子接近-1；低熵表示有序趋势，因子接近+1。使用简化的滚动相关系数作为替代。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_chf",
            name="ChaosFactor",
            display_name="混沌度因子",
            description="基于价格序列近似熵（ApEn），衡量市场有序性。高熵值表示无序混乱（regime=unknown），因子接近-1；低熵表示有序趋势，因子接近+1。使用简化的滚动相关系数作为替代。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        # 计算价格变化序列
        ret = close.pct_change().dropna()
        # 用滚动自相关系数作为有序性指标：自相关绝对值大表示有趋势，小表示混乱
        window = 20
        # 计算滞后1的自相关
        autocorr = ret.rolling(window).corr(ret.shift(1))
        # 取绝对值，绝对值越小越混乱
        autocorr_abs = autocorr.abs().fillna(0.5)
        # 映射：0.2以下混乱=>-1，0.8以上有序=>+1
        factor = np.where(autocorr_abs < 0.2, -1, np.where(autocorr_abs > 0.8, 1, (autocorr_abs - 0.5) / 0.3 * 2))
        factor = pd.Series(factor, index=data.index).shift(1).fillna(0)  # 避免未来信息
        return factor
