"""AI因子: 相关性状态突变 | 置信:65% | 检测短期收益率与长期收益率的相关系数变化。当相关系数突降时，市场处于不稳定状态（regime unknown），因子输出负值；相关系数稳定时输出正值。用于识别趋势一致性断裂。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Correlationregimeshift(BaseFactor):
    """检测短期收益率与长期收益率的相关系数变化。当相关系数突降时，市场处于不稳定状态（regime unknown），因子输出负值；相关系数稳定时输出正值。用于识别趋势一致性断裂。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_corr_break",
            name="CorrelationRegimeShift",
            display_name="相关性状态突变",
            description="检测短期收益率与长期收益率的相关系数变化。当相关系数突降时，市场处于不稳定状态（regime unknown），因子输出负值；相关系数稳定时输出正值。用于识别趋势一致性断裂。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        # 短期收益率(3日)和长期收益率(15日)
        ret_short = close.pct_change(3).fillna(0)
        ret_long = close.pct_change(15).fillna(0)
        # 滚动20天相关系数
        def rolling_corr(x, y):
            return x.rolling(20).corr(y)
        corr = rolling_corr(ret_short, ret_long)
        # 计算相关系数的一阶差分（变化率）
        delta_corr = corr.diff().fillna(0)
        # 使用指数移动平均平滑
        smoothed = delta_corr.ewm(span=5).mean()
        # 映射到[-1,1]：正变化表示稳定，负变化表示断裂
        result = np.clip(smoothed * 20, -1, 1)  # 放大因子
        return result
