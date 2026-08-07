"""AI因子: 尾部风险反转 | 置信:50% | 度量极端价格波动后的反转概率：当价格突破近期低点后迅速反弹（出现长下影线），且反弹幅度大于此前下跌幅度的某个比例，则空头面临反转风险。计算当前价格相对于最近N日最低点的距离，并用波动率调整。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TailRiskReversal(BaseFactor):
    """度量极端价格波动后的反转概率：当价格突破近期低点后迅速反弹（出现长下影线），且反弹幅度大于此前下跌幅度的某个比例，则空头面临反转风险。计算当前价格相对于最近N日最低点的距离，并用波动率调整。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tail_risk",
            name="Tail Risk Reversal",
            display_name="尾部风险反转",
            description="度量极端价格波动后的反转概率：当价格突破近期低点后迅速反弹（出现长下影线），且反弹幅度大于此前下跌幅度的某个比例，则空头面临反转风险。计算当前价格相对于最近N日最低点的距离，并用波动率调整。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        n = 10
        low_min = data['low'].rolling(n).min()
        dist_from_low = (data['close'] - low_min) / (data['high'] - low_min + 1e-10)
        atr = (data['high'] - data['low']).rolling(14).mean()
        vol_norm = atr / data['close']
        score = dist_from_low * (1 + vol_norm)
        z = (score - score.rolling(20).mean()) / score.rolling(20).std()
        result = z.clip(-3, 3) / 3
        return result
