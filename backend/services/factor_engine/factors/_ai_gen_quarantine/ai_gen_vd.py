"""AI因子: 成交量背离因子 | 置信:60% | 计算短期价格变化与成交量变化的相关系数，当两者负相关或弱相关时表示上涨缺乏成交量支持或下跌放量，输出负值，提示趋势不健康可能造成亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumeDivergence(BaseFactor):
    """计算短期价格变化与成交量变化的相关系数，当两者负相关或弱相关时表示上涨缺乏成交量支持或下跌放量，输出负值，提示趋势不健康可能造成亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vd",
            name="VolumeDivergence",
            display_name="成交量背离因子",
            description="计算短期价格变化与成交量变化的相关系数，当两者负相关或弱相关时表示上涨缺乏成交量支持或下跌放量，输出负值，提示趋势不健康可能造成亏损。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        volume = data['volume']
        # 计算价格收益率和成交量变化率
        ret = close.pct_change()
        vol_change = volume.pct_change()
        # 计算过去10天的滚动相关系数
        corr = ret.rolling(10).corr(vol_change)
        # 映射到[-1,1]：相关系数本身就在[-1,1]，但我们要强调负相关为负值，正相关为正值
        # 然而亏损模式中，弱相关或无方向可能也是问题，所以可以取绝对值后再反转？
        # 更合理：当相关性接近0时也认为风险，因此用1 - |corr| 再取负？
        # 设计：当|corr| < 0.3时输出负值表示背离或不明确，否则输出正相关方向
        result = np.where(corr.abs() < 0.3, -1, corr)
        # 但corr本身范围[-1,1]，直接填充即可
        result = pd.Series(result, index=close.index)
        # 用smooth避免突变
        return result.fillna(0)
