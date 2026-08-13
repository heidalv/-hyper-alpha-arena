"""AI因子: 趋势效率比 | 置信:60% | 衡量价格运动的效率，低值表示市场来回拉锯、趋势微弱。亏损样本中多次出现小额止损和盈利回撤，与低效率、无趋势状态高度相关。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class EfficiencyRatio(BaseFactor):
    """衡量价格运动的效率，低值表示市场来回拉锯、趋势微弱。亏损样本中多次出现小额止损和盈利回撤，与低效率、无趋势状态高度相关。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_effratio",
            name="Efficiency Ratio",
            display_name="趋势效率比",
            description="衡量价格运动的效率，低值表示市场来回拉锯、趋势微弱。亏损样本中多次出现小额止损和盈利回撤，与低效率、无趋势状态高度相关。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        N = 10
        close = data['close']
        move = close.diff(N).abs()
        noise = close.diff().abs().rolling(N).sum().replace(0, np.nan)
        er = move / noise
        result = (2 * er - 1).fillna(0)
        return result
