"""AI因子: 趋势清晰度(R²) | 置信:60% | 对最近20个收盘价进行线性回归，计算R²衡量趋势清晰度。R²越高表示趋势越明确，越低表示市场混乱（regime unknown），映射到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RegimeClarityRSquared(BaseFactor):
    """对最近20个收盘价进行线性回归，计算R²衡量趋势清晰度。R²越高表示趋势越明确，越低表示市场混乱（regime unknown），映射到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regime_r2",
            name="Regime Clarity (R-squared)",
            display_name="趋势清晰度(R²)",
            description="对最近20个收盘价进行线性回归，计算R²衡量趋势清晰度。R²越高表示趋势越明确，越低表示市场混乱（regime unknown），映射到[-1,1]。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        window = 20
        def r2(series):
            import numpy as np
            x = np.arange(len(series))
            y = series.values
            if len(y) < 2:
                return np.nan
            corr = np.corrcoef(x, y)[0, 1]
            return corr * corr
        rolling_r2 = close.rolling(window).apply(r2, raw=False)
        result = 2 * rolling_r2 - 1
        return result.fillna(0)
