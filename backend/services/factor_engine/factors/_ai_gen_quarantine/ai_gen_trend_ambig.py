"""AI因子: 趋势模糊指标 | 置信:60% | 计算短期(5日)、中期(20日)、长期(60日)简单移动平均线之间的最大距离与最小距离之比，比值接近1表示均线缠绕，趋势模糊，因子值接近-1；发散时接近+1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trend_Ambiguity_Indicator(BaseFactor):
    """计算短期(5日)、中期(20日)、长期(60日)简单移动平均线之间的最大距离与最小距离之比，比值接近1表示均线缠绕，趋势模糊，因子值接近-1；发散时接近+1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_ambig",
            name="Trend Ambiguity Indicator",
            display_name="趋势模糊指标",
            description="计算短期(5日)、中期(20日)、长期(60日)简单移动平均线之间的最大距离与最小距离之比，比值接近1表示均线缠绕，趋势模糊，因子值接近-1；发散时接近+1。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ma5 = data['close'].rolling(5).mean()
        ma20 = data['close'].rolling(20).mean()
        ma60 = data['close'].rolling(60).mean()
        # 计算三条均线之间的最大和最小距离（绝对值）
        diff_5_20 = (ma5 - ma20).abs()
        diff_5_60 = (ma5 - ma60).abs()
        diff_20_60 = (ma20 - ma60).abs()
        max_diff = pd.concat([diff_5_20, diff_5_60, diff_20_60], axis=1).max(axis=1)
        min_diff = pd.concat([diff_5_20, diff_5_60, diff_20_60], axis=1).min(axis=1)
        # 防止除零
        min_diff = min_diff.replace(0, np.nan)
        ratio = max_diff / min_diff
        # ratio大表示发散，小表示缠绕，映射到[-1,1]：缠绕时接近-1
        score = 2 * (ratio / (ratio + 1)) - 1  # ratio从0到无穷映射到[-1,1]，ratio=0时score=-1，ratio=inf时score=1
        score = np.where(ratio.isna(), 0, score)
        return pd.Series(score, index=data.index).fillna(0)
