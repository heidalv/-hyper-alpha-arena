"""AI因子: 波动率突变突破 | 置信:50% | 已实现波动率的突变往往伴随趋势启动或反转。用短期波动率与长期波动率之比衡量波动率聚集状态，并结合价格突破方向，捕捉波动率放大时的动量延续alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityRegimeBreakout(BaseFactor):
    """已实现波动率的突变往往伴随趋势启动或反转。用短期波动率与长期波动率之比衡量波动率聚集状态，并结合价格突破方向，捕捉波动率放大时的动量延续alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volatility_breakout",
            name="Volatility Regime Breakout",
            display_name="波动率突变突破",
            description="已实现波动率的突变往往伴随趋势启动或反转。用短期波动率与长期波动率之比衡量波动率聚集状态，并结合价格突破方向，捕捉波动率放大时的动量延续alpha。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change()
        vol_short = ret.rolling(5).std()
        vol_long = ret.rolling(30).std()
        vol_ratio = vol_short / (vol_long + 1e-9)
        mom = data['close'].pct_change(10)
        result = (vol_ratio * mom).clip(-1, 1)
        return result
