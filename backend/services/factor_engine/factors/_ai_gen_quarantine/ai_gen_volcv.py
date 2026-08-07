"""AI因子: 波动一致性因子 | 置信:60% | 计算过去14根K线真实波幅（ATR）的变异系数（CV = 标准差/均值），若CV高说明波动率不稳定，市场状态混乱（regime=unknown），因子为负；反之稳定趋势则正。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityConsistency(BaseFactor):
    """计算过去14根K线真实波幅（ATR）的变异系数（CV = 标准差/均值），若CV高说明波动率不稳定，市场状态混乱（regime=unknown），因子为负；反之稳定趋势则正。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volcv",
            name="VolatilityConsistency",
            display_name="波动一致性因子",
            description="计算过去14根K线真实波幅（ATR）的变异系数（CV = 标准差/均值），若CV高说明波动率不稳定，市场状态混乱（regime=unknown），因子为负；反之稳定趋势则正。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 计算真实波幅
        high = data['high']
        low = data['low']
        close = data['close']
        prev_close = close.shift(1)
        tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        window = 14
        if len(tr) < window:
            return pd.Series(0.0, index=tr.index)
        # 滚动变异系数
        def cv(series):
            if series.mean() == 0:
                return 0
            return series.std() / series.mean()
        cv_series = tr.rolling(window).apply(cv, raw=False)
        # 映射：CV < 0.5 -> +1, 0.5~1.5线性, >1.5 -> -1
        lower = 0.5
        upper = 1.5
        factor = 1 - 2 * (cv_series - lower) / (upper - lower)
        factor = factor.clip(-1, 1)
        return factor
