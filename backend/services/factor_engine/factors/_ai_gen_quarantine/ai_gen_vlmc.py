"""AI因子: 量价累积背离因子 | 置信:60% | 计算价格累积变化与成交量累积变化的相关系数，使用过去10个交易日滚动窗口。若价格累积上涨但成交量累积下降(背离)，表明上涨动能不足，regime=unknown下易触发止损或超时亏损，因子输出负值；反之量价齐升输出正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceCumulativeDivergence(BaseFactor):
    """计算价格累积变化与成交量累积变化的相关系数，使用过去10个交易日滚动窗口。若价格累积上涨但成交量累积下降(背离)，表明上涨动能不足，regime=unknown下易触发止损或超时亏损，因子输出负值；反之量价齐升输出正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vlmc",
            name="VolumePriceCumulativeDivergence",
            display_name="量价累积背离因子",
            description="计算价格累积变化与成交量累积变化的相关系数，使用过去10个交易日滚动窗口。若价格累积上涨但成交量累积下降(背离)，表明上涨动能不足，regime=unknown下易触发止损或超时亏损，因子输出负值；反之量价齐升输出正值。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        import numpy as np
        close = data['close']
        volume = data['volume']
        # 累积变化
        price_change = close.pct_change()
        vol_change = volume.pct_change().replace([np.inf, -np.inf], np.nan)
        # 滚动10日相关系数
        def rolling_corr(x, y, window=10):
            return x.rolling(window).corr(y)
        corr = rolling_corr(price_change, vol_change, window=10)
        # 量价背离：正相关是健康，负相关是背离
        # 如果相关系数为负，则因子为负，表示风险
        result = corr.clip(-1, 1)
        # 填充NaN为0
        result = result.fillna(0)
        return result
