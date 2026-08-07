"""AI因子: 量价背离因子 | 置信:70% | 检测价格趋势与成交量趋势的背离。当价格上涨而成交量下降（或价格下跌而成交量上升）时，趋势可能不可持续，属于高风险状态。使用价格动量（短期收益率）与成交量动量的相关系数，系数为负表示背离。输出[-1,1]，负值表示背离，应避免做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class volume_price_divergence(BaseFactor):
    """检测价格趋势与成交量趋势的背离。当价格上涨而成交量下降（或价格下跌而成交量上升）时，趋势可能不可持续，属于高风险状态。使用价格动量（短期收益率）与成交量动量的相关系数，系数为负表示背离。输出[-1,1]，负值表示背离，应避免做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vpdiv",
            name="volume_price_divergence",
            display_name="量价背离因子",
            description="检测价格趋势与成交量趋势的背离。当价格上涨而成交量下降（或价格下跌而成交量上升）时，趋势可能不可持续，属于高风险状态。使用价格动量（短期收益率）与成交量动量的相关系数，系数为负表示背离。输出[-1,1]，负值表示背离，应避免做多。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        close = data['close']
        volume = data['volume']
        # 计算短期价格动量（5日收益率）
        price_ret = close.pct_change(5)
        # 计算成交量变化率（5日变化）
        vol_chg = volume / volume.shift(5) - 1
        # 计算滚动20日相关系数
        corr = price_ret.rolling(20).corr(vol_chg)
        # 映射到[-1,1]：负相关系数表示背离，直接取负值
        result = -corr
        return result.fillna(0)
