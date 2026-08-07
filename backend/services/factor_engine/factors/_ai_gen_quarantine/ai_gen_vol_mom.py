"""AI因子: 波动率调整动量 | 置信:65% | 计算短期价格动量除以近期波动率，当动量微弱且波动率较高时，趋势不明确，容易导致做多亏损。因子值正向表示强趋势，负向表示弱趋势或震荡，建议在负值时避免做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Adjusted_Momentum(BaseFactor):
    """计算短期价格动量除以近期波动率，当动量微弱且波动率较高时，趋势不明确，容易导致做多亏损。因子值正向表示强趋势，负向表示弱趋势或震荡，建议在负值时避免做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_mom",
            name="Volatility-Adjusted Momentum",
            display_name="波动率调整动量",
            description="计算短期价格动量除以近期波动率，当动量微弱且波动率较高时，趋势不明确，容易导致做多亏损。因子值正向表示强趋势，负向表示弱趋势或震荡，建议在负值时避免做多。",
            category="composite",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        high = data['high']
        low = data['low']
        # 短期动量: 过去5日价格变化百分比
        mom = close.pct_change(5)
        # 波动率: 过去20日ATR除以收盘价
        atr = (high - low).rolling(20).mean()
        vol = atr / close
        # 波动率调整动量: 动量除以波动率，再使用tanh压缩到[-1,1]
        raw = mom / (vol + 1e-8)
        result = np.tanh(raw)
        return result
