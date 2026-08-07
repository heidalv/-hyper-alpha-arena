"""AI因子: 波动时间风险因子 | 置信:63% | 结合价格运动效率与波动率评估持仓过久风险。效率低且波幅大的市场容易频繁触发止损或耗尽持仓时间。因子值低（负）表示高风险震荡环境，应避免趋势跟踪。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityTimeRisk(BaseFactor):
    """结合价格运动效率与波动率评估持仓过久风险。效率低且波幅大的市场容易频繁触发止损或耗尽持仓时间。因子值低（负）表示高风险震荡环境，应避免趋势跟踪。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vtr",
            name="Volatility Time Risk",
            display_name="波动时间风险因子",
            description="结合价格运动效率与波动率评估持仓过久风险。效率低且波幅大的市场容易频繁触发止损或耗尽持仓时间。因子值低（负）表示高风险震荡环境，应避免趋势跟踪。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        high = data['high']
        low = data['low']
        atr = (high - low).rolling(14).mean()
        efficiency = abs(close.diff(14)) / (high.rolling(14).max() - low.rolling(14).min() + 1e-9)
        noise = 1 - efficiency
        risk = noise * (atr / close)
        zscore = (risk - risk.rolling(60, min_periods=30).mean()) / risk.rolling(60, min_periods=30).std()
        result = -zscore.clip(-3, 3) / 3.0
        return result.fillna(0)
