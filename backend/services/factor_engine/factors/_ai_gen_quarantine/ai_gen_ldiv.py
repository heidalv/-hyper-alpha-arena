"""AI因子: 量价背离因子 | 置信:65% | 计算近期收益率与成交量变化率的滚动相关性，取其负值。当量价负相关时（放量下跌或缩量上涨）预示潜在反转，值域[-1,1]，正值表示背离加剧。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """计算近期收益率与成交量变化率的滚动相关性，取其负值。当量价负相关时（放量下跌或缩量上涨）预示潜在反转，值域[-1,1]，正值表示背离加剧。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_ldiv",
            name="Volume-Price Divergence",
            display_name="量价背离因子",
            description="计算近期收益率与成交量变化率的滚动相关性，取其负值。当量价负相关时（放量下跌或缩量上涨）预示潜在反转，值域[-1,1]，正值表示背离加剧。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change()
        vol_chg = data['volume'].pct_change()
        corr = ret.rolling(window=20, min_periods=5).corr(vol_chg)
        factor = -corr
        factor = factor.clip(-1, 1)
        factor = factor.fillna(0)
        return factor
