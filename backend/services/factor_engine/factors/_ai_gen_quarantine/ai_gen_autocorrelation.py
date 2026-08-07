"""AI因子: 收益率自相关 | 置信:60% | 计算过去15个周期收益率的一阶自相关系数。接近0表示随机游走（未知状态），正值表示趋势延续，负值表示反转。映射到[-1,1]（绝对值越大方向越明确）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Return_Autocorrelation(BaseFactor):
    """计算过去15个周期收益率的一阶自相关系数。接近0表示随机游走（未知状态），正值表示趋势延续，负值表示反转。映射到[-1,1]（绝对值越大方向越明确）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_autocorrelation",
            name="Return Autocorrelation",
            display_name="收益率自相关",
            description="计算过去15个周期收益率的一阶自相关系数。接近0表示随机游走（未知状态），正值表示趋势延续，负值表示反转。映射到[-1,1]（绝对值越大方向越明确）。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        window = 15
        ret = data['close'].pct_change().fillna(0)
        def acf1(series):
            if len(series) < window:
                return np.nan
            s = series.values[-window:]
            if np.std(s) == 0:
                return 0.0
            return np.corrcoef(s[:-1], s[1:])[0,1]
        result = ret.rolling(window, min_periods=window).apply(acf1, raw=False)
        result = pd.Series(result, index=data.index).fillna(0)
        return result.clip(-1, 1)
