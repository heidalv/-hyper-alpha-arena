"""AI因子: 趋势波动比 | 置信:60% | 计算短期趋势强度（如最近10日收盘价线性回归斜率）与同期波动率（ATR）的比值，归一化到[-1,1]。当比值接近0时表示无明显趋势且波动较大，此时易出现假突破亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trend_Strength_vs_Volatility_Ratio(BaseFactor):
    """计算短期趋势强度（如最近10日收盘价线性回归斜率）与同期波动率（ATR）的比值，归一化到[-1,1]。当比值接近0时表示无明显趋势且波动较大，此时易出现假突破亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trendvol",
            name="Trend Strength vs Volatility Ratio",
            display_name="趋势波动比",
            description="计算短期趋势强度（如最近10日收盘价线性回归斜率）与同期波动率（ATR）的比值，归一化到[-1,1]。当比值接近0时表示无明显趋势且波动较大，此时易出现假突破亏损。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 计算ATR（14天）
        high = data['high']
        low = data['low']
        close = data['close']
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        atr = tr.rolling(14).mean()
        # 计算线性回归斜率（10天）
        def slope(series):
            if len(series) < 2:
                return np.nan
            x = np.arange(len(series))
            y = series.values
            if np.std(x) == 0:
                return 0.0
            return np.polyfit(x, y, 1)[0]
        trend = close.rolling(10).apply(slope, raw=False)
        # 归一化因子：趋势/ATR，然后压缩到[-1,1]
        ratio = trend / (atr + 1e-8)
        # 使用tanh压缩，并限制极端值
        result = np.tanh(ratio / 2.0)
        return result.fillna(0.0)
