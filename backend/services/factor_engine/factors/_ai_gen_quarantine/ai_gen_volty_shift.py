"""AI因子: 波动率状态突变因子 | 置信:55% | 检测短期波动率相对于长期波动率的异常变化，高比值表示市场进入未知状态，容易导致止损或超时亏损。使用20周期和120周期真实波幅的比率，标准化后输出[-1,1]区间，正值表示波动率上升风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Regime_Change(BaseFactor):
    """检测短期波动率相对于长期波动率的异常变化，高比值表示市场进入未知状态，容易导致止损或超时亏损。使用20周期和120周期真实波幅的比率，标准化后输出[-1,1]区间，正值表示波动率上升风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volty_shift",
            name="Volatility Regime Change",
            display_name="波动率状态突变因子",
            description="检测短期波动率相对于长期波动率的异常变化，高比值表示市场进入未知状态，容易导致止损或超时亏损。使用20周期和120周期真实波幅的比率，标准化后输出[-1,1]区间，正值表示波动率上升风险。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        short_tr = tr.rolling(20).mean()
        long_tr = tr.rolling(120).mean()
        ratio = short_tr / (long_tr + 1e-10)
        # 将ratio标准化到[-1,1]，通常ratio在0.5~2之间，取对数后tanh
        result = np.tanh((ratio - 1) * 2)
        return result
