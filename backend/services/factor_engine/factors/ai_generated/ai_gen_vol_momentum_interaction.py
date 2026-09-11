"""AI因子: 波动动量交互因子 | 置信:60% | 捕捉波动率突变与动量方向的一致性。当价格动量为正且波动率上升时，表明趋势加速；动量转负且波动率上升时，表明下跌动能增强。通过动量符号与波动率变化的乘积构造方向性因子。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolMomentumInteraction(BaseFactor):
    """捕捉波动率突变与动量方向的一致性。当价格动量为正且波动率上升时，表明趋势加速；动量转负且波动率上升时，表明下跌动能增强。通过动量符号与波动率变化的乘积构造方向性因子。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_momentum_interaction",
            name="Vol_Momentum_Interaction",
            display_name="波动动量交互因子",
            description="捕捉波动率突变与动量方向的一致性。当价格动量为正且波动率上升时，表明趋势加速；动量转负且波动率上升时，表明下跌动能增强。通过动量符号与波动率变化的乘积构造方向性因子。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        vol = data['close'].pct_change().rolling(10).std()
        vol_chg = vol - vol.shift(5)
        result = (ret * vol_chg).clip(-1, 1)
        return result
