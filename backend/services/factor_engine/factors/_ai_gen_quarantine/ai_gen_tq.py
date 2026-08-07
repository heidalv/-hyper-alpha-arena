"""AI因子: 趋势质量 | 置信:60% | 通过线性回归斜率与残差标准差之比衡量趋势稳定性和强度。斜率绝对值大且残差小表示强趋势，因子接近±1；斜率小或残差大表示弱趋势或震荡，因子接近0。结合斜率方向赋予符号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trendquality(BaseFactor):
    """通过线性回归斜率与残差标准差之比衡量趋势稳定性和强度。斜率绝对值大且残差小表示强趋势，因子接近±1；斜率小或残差大表示弱趋势或震荡，因子接近0。结合斜率方向赋予符号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tq",
            name="TrendQuality",
            display_name="趋势质量",
            description="通过线性回归斜率与残差标准差之比衡量趋势稳定性和强度。斜率绝对值大且残差小表示强趋势，因子接近±1；斜率小或残差大表示弱趋势或震荡，因子接近0。结合斜率方向赋予符号。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        period = 20
        close = data['close']
        x = np.arange(period)
        def trend_quality(series):
            if len(series) < period:
                return 0.0
            y = series.values[-period:]
            slope = np.polyfit(x, y, 1)[0]
            residuals = y - (slope * x + np.polyfit(x, y, 1)[1])
            std_resid = np.std(residuals)
            if std_resid == 0:
                return 0.0
            quality = slope / std_resid
            # 将quality映射到[-1,1]，使用tanh限制
            return np.tanh(quality * 0.1)
        result = close.rolling(period).apply(trend_quality, raw=False)
        return result.fillna(0).clip(-1,1)
