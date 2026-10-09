"""AI因子: 未知行情逆势因子 | 置信:55% | 针对regime=unknown时频繁出现的小幅亏损，使用短期动量反转逻辑。当价格在低波动环境下连续小幅上涨（表明可能超时持仓）时，做空；连续下跌时做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RegimeUnknownContrarian(BaseFactor):
    """针对regime=unknown时频繁出现的小幅亏损，使用短期动量反转逻辑。当价格在低波动环境下连续小幅上涨（表明可能超时持仓）时，做空；连续下跌时做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regime_unknown_contrarian",
            name="Regime_Unknown_Contrarian",
            display_name="未知行情逆势因子",
            description="针对regime=unknown时频繁出现的小幅亏损，使用短期动量反转逻辑。当价格在低波动环境下连续小幅上涨（表明可能超时持仓）时，做空；连续下跌时做多。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(3)
        vol = data['close'].pct_change().rolling(10).std()
        result = (-ret * (1 / (vol + 1e-9))).clip(-1, 1)
        return result
