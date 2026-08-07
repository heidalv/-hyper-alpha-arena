"""AI因子: 趋势稳定性因子 | 置信:65% | 通过最近N根K线的线性回归R平方衡量趋势强度，R平方低表示市场缺乏明确趋势，容易进入regime=unknown状态，因子值接近-1时提示应避免趋势交易。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendStability(BaseFactor):
    """通过最近N根K线的线性回归R平方衡量趋势强度，R平方低表示市场缺乏明确趋势，容易进入regime=unknown状态，因子值接近-1时提示应避免趋势交易。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_stab",
            name="Trend Stability",
            display_name="趋势稳定性因子",
            description="通过最近N根K线的线性回归R平方衡量趋势强度，R平方低表示市场缺乏明确趋势，容易进入regime=unknown状态，因子值接近-1时提示应避免趋势交易。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        n = 20
        close = data['close']
        # 滚动线性回归R平方
        def rolling_r2(series):
            x = np.arange(len(series))
            y = series.values
            if len(y) < 2 or np.std(y) == 0:
                return 0.0
            corr = np.corrcoef(x, y)[0, 1]
            return corr ** 2
        r2 = close.rolling(n).apply(rolling_r2, raw=False)
        # 标准化到[-1,1]，以0.5为阈值，低于0.5为负向
        r2 = r2.fillna(0.5)
        result = 2 * (r2 - 0.5)  # 映射到[-1,1]
        return result.clip(-1, 1)
