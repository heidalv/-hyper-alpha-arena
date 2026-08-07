"""AI因子: 趋势强度因子 | 置信:60% | 基于价格相对于多条移动平均线的位置计算趋势强度，值越高表示趋势越强，适合做多；值越低表示震荡或弱趋势，容易导致持仓超时或止损。使用EMA(10)和EMA(30)的归一化差异，结合价格位置。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendStrength(BaseFactor):
    """基于价格相对于多条移动平均线的位置计算趋势强度，值越高表示趋势越强，适合做多；值越低表示震荡或弱趋势，容易导致持仓超时或止损。使用EMA(10)和EMA(30)的归一化差异，结合价格位置。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trd_str",
            name="TrendStrength",
            display_name="趋势强度因子",
            description="基于价格相对于多条移动平均线的位置计算趋势强度，值越高表示趋势越强，适合做多；值越低表示震荡或弱趋势，容易导致持仓超时或止损。使用EMA(10)和EMA(30)的归一化差异，结合价格位置。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        ema10 = close.ewm(span=10, adjust=False).mean()
        ema30 = close.ewm(span=30, adjust=False).mean()
        diff = (ema10 - ema30) / close
        # 归一化到[-1,1]：使用tanh或clip
        import numpy as np
        result = np.tanh(diff * 20)  # 放大后tanh映射
        return result
