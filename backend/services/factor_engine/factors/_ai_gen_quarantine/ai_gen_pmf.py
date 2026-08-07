"""AI因子: 价格动量疲劳因子 | 置信:60% | 衡量价格相对于移动平均线的偏差是否出现动能衰减，当价格远高于均线但短期动量转负（或远低于均线但反弹无力）时返回负值，表征趋势疲劳，容易导致止损或持仓超时。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PriceMomentumFatigue(BaseFactor):
    """衡量价格相对于移动平均线的偏差是否出现动能衰减，当价格远高于均线但短期动量转负（或远低于均线但反弹无力）时返回负值，表征趋势疲劳，容易导致止损或持仓超时。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pmf",
            name="Price Momentum Fatigue",
            display_name="价格动量疲劳因子",
            description="衡量价格相对于移动平均线的偏差是否出现动能衰减，当价格远高于均线但短期动量转负（或远低于均线但反弹无力）时返回负值，表征趋势疲劳，容易导致止损或持仓超时。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        # 指数移动均线
        ema_fast = close.ewm(span=12).mean()
        ema_slow = close.ewm(span=26).mean()
        # 价格偏离度：close / ema_slow - 1
        deviation = close / ema_slow - 1
        # 短期动量：5日收益率
        momentum = close.pct_change(5)
        # 构建疲劳信号：偏离度 * 动量，当二者同号时趋势健康，异号时疲劳
        fatigue = deviation * momentum
        # 取反方向，使得疲劳越严重因子值越低
        signal = -fatigue
        # 归一化：使用滚动z-score，并clip到[-1,1]
        zscore = (signal - signal.rolling(60).mean()) / signal.rolling(60).std().replace(0, np.nan)
        result = (zscore / 2).clip(-1, 1)
        return result
