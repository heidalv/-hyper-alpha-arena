"""AI因子: 布林带宽度 | 置信:60% | 计算布林带相对宽度，当带宽极窄时市场处于低波动震荡状态（regime=unknown），易被微小波动触发止损或平仓。因子值负向提示震荡风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class BollingerBandsWidth(BaseFactor):
    """计算布林带相对宽度，当带宽极窄时市场处于低波动震荡状态（regime=unknown），易被微小波动触发止损或平仓。因子值负向提示震荡风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_bbwidth",
            name="BollingerBandsWidth",
            display_name="布林带宽度",
            description="计算布林带相对宽度，当带宽极窄时市场处于低波动震荡状态（regime=unknown），易被微小波动触发止损或平仓。因子值负向提示震荡风险。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

        def calculate(self, data):
            import numpy as np
            close = data['close']
            window = 20
            sma = close.rolling(window).mean()
            std = close.rolling(window).std()
            upper = sma + 2 * std
            lower = sma - 2 * std
            bandwidth = (upper - lower) / sma * 100
            # 标准化带宽，窄带宽为负值，宽带宽为正值
            z = (bandwidth - bandwidth.rolling(100).mean()) / (bandwidth.rolling(100).std() + 1e-10)
            result = -np.clip(z, -3, 3) / 3.0  # 负值表示窄带宽风险
            return result
