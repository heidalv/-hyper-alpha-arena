"""AI因子: 均值回归偏离度因子 | 置信:60% | 计算价格相对均线的标准化偏离程度(使用ATR标准化)。正值表示价格远高于均线，负值表示远低于均线。该因子在价格过度延伸时给出反向信号，可防止在超买时追多或超卖时追空，减少因价格回归导致的止损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MeanReversionExtremeness(BaseFactor):
    """计算价格相对均线的标准化偏离程度(使用ATR标准化)。正值表示价格远高于均线，负值表示远低于均线。该因子在价格过度延伸时给出反向信号，可防止在超买时追多或超卖时追空，减少因价格回归导致的止损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mrext",
            name="Mean Reversion Extremeness",
            display_name="均值回归偏离度因子",
            description="计算价格相对均线的标准化偏离程度(使用ATR标准化)。正值表示价格远高于均线，负值表示远低于均线。该因子在价格过度延伸时给出反向信号，可防止在超买时追多或超卖时追空，减少因价格回归导致的止损。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        ma = close.rolling(20).mean()
        tr = np.maximum(high - low, np.maximum((high - close.shift(1)).abs(), (low - close.shift(1)).abs()))
        atr = tr.rolling(14).mean()
        distance = (close - ma) / atr.replace(0, np.nan)
        result = np.tanh(distance)
        return result.fillna(0).clip(-1, 1)
