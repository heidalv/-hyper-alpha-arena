"""AI因子: 波动率状态异常 | 置信:70% | 检测短期与长期波动率比值是否异常，异常时市场环境不稳定，容易引发止损或超时亏损。使用5日ATR与20日ATR之比，计算z-score并映射到[-1,1]，负值表示异常波动状态。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatilityregimeanomaly(BaseFactor):
    """检测短期与长期波动率比值是否异常，异常时市场环境不稳定，容易引发止损或超时亏损。使用5日ATR与20日ATR之比，计算z-score并映射到[-1,1]，负值表示异常波动状态。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_regime",
            name="VolatilityRegimeAnomaly",
            display_name="波动率状态异常",
            description="检测短期与长期波动率比值是否异常，异常时市场环境不稳定，容易引发止损或超时亏损。使用5日ATR与20日ATR之比，计算z-score并映射到[-1,1]，负值表示异常波动状态。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # ATR计算
        high = data['high']
        low = data['low']
        close = data['close']
        tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
        atr_short = tr.rolling(window=5, min_periods=5).mean()
        atr_long = tr.rolling(window=20, min_periods=20).mean()
        # 防止除零
        ratio = atr_short / atr_long
        # 标准化: 以1为中心, 取对数后标准化? 直接使用ratio的异常程度
        # 定义异常区间: <0.5或>1.5为异常, 映射到-1; 接近1为正常, 映射到1
        # 使用sigmoid型变换: score = -2*(ratio-1) 但范围受限
        # 采用 (1 - (ratio-1)^2 *4) 当ratio在[0.5,1.5]内, 否则-1
        norm = np.where((ratio >= 0.5) & (ratio <= 1.5), 1 - 4*(ratio-1)**2, -1.0)
        # 处理空值
        result = pd.Series(norm, index=data.index).fillna(0)
        return result
