"""AI因子: 趋势强度指数 | 置信:65% | 基于线性回归斜率与残差标准差之比，衡量趋势的可靠性。斜率绝对值大且残差小表示强趋势，输出+1；斜率小或残差大表示弱趋势/噪声，输出-1。使用最近20个周期。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendStrengthIndex(BaseFactor):
    """基于线性回归斜率与残差标准差之比，衡量趋势的可靠性。斜率绝对值大且残差小表示强趋势，输出+1；斜率小或残差大表示弱趋势/噪声，输出-1。使用最近20个周期。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_strength",
            name="Trend Strength Index",
            display_name="趋势强度指数",
            description="基于线性回归斜率与残差标准差之比，衡量趋势的可靠性。斜率绝对值大且残差小表示强趋势，输出+1；斜率小或残差大表示弱趋势/噪声，输出-1。使用最近20个周期。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        window = 20
        x = np.arange(window)
        def trend_strength(series):
            y = series.values
            if len(y) < window:
                return 0
            slope = np.polyfit(x, y, 1)[0]
            residuals = y - (slope * x + np.mean(y))
            std_resid = np.std(residuals)
            if std_resid == 0:
                return 1.0
            return slope / std_resid
        roll = data['close'].rolling(window).apply(trend_strength, raw=False)
        # normalize to [-1,1]
        max_abs = roll.abs().rolling(50).max()
        result = roll / (max_abs + 1e-10)
        return result.clip(-1, 1)
