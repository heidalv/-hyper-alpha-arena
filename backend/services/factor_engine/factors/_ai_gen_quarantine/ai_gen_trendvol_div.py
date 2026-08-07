"""AI因子: 趋势量能背离 | 置信:55% | 计算过去N周期内价格变动与成交量的相关性，当价格上升但成交量下降时，认为趋势脆弱，赋予负值。使用rolling窗口计算相关系数并归一化。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trend_Volume_Divergence(BaseFactor):
    """计算过去N周期内价格变动与成交量的相关性，当价格上升但成交量下降时，认为趋势脆弱，赋予负值。使用rolling窗口计算相关系数并归一化。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trendvol_div",
            name="Trend-Volume Divergence",
            display_name="趋势量能背离",
            description="计算过去N周期内价格变动与成交量的相关性，当价格上升但成交量下降时，认为趋势脆弱，赋予负值。使用rolling窗口计算相关系数并归一化。",
            category="composite",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        n = 20
        ret = data['close'].pct_change()
        vol_change = data['volume'].pct_change()
        corr = ret.rolling(n).corr(vol_change)
        result = -1 * corr.fillna(0)  # 负相关时为正（弱趋势），此处直接取负值使负相关输出正值？我们希望价格涨量缩为负，所以用-corr
        # 但corr为正表示同向，趋势健康；负表示背离。我们想当背离时给负值，所以用 -corr 则背离时corr负，-corr正？不对，我们希望背离时输出负值，所以直接用corr，背离时corr为负，输出负值。修正：直接返回corr，其值域[-1,1]正好符合要求，背离时负值表示弱势。
        return corr.fillna(0)
