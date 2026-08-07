"""AI因子: 波动率风险因子 | 置信:60% | 使用ATR(14)与收盘价的比值衡量近期波动率，当比值处于历史高位时预示止损风险加大，给出负向信号。将比值z-score后截断至[-1,1]区间。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Risk_Indicator(BaseFactor):
    """使用ATR(14)与收盘价的比值衡量近期波动率，当比值处于历史高位时预示止损风险加大，给出负向信号。将比值z-score后截断至[-1,1]区间。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_risk",
            name="Volatility Risk Indicator",
            display_name="波动率风险因子",
            description="使用ATR(14)与收盘价的比值衡量近期波动率，当比值处于历史高位时预示止损风险加大，给出负向信号。将比值z-score后截断至[-1,1]区间。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 计算ATR(14)
        tr = np.maximum(data['high'] - data['low'], 
                        np.abs(data['high'] - data['close'].shift(1)),
                        np.abs(data['low'] - data['close'].shift(1)))
        atr = tr.rolling(window=14).mean()
        # 波动率比率
        vol_ratio = atr / data['close']
        # z-score归一化
        mean = vol_ratio.rolling(window=50).mean()
        std = vol_ratio.rolling(window=50).std().replace(0, np.nan)
        z = (vol_ratio - mean) / std
        # 截断到[-1,1]并填充空值
        result = z.clip(-3, 3) / 3.0
        result = result.fillna(0)
        return result
