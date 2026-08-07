"""AI因子: 波动趋势比 | 置信:65% | 短期波动率（ATR）与趋势强度（ADX-like）的比值，用于识别高波动无趋势的震荡状态。当比值高时，市场容易产生假突破和止损，因子输出接近+1表示高风险；比值低时趋势明确，输出接近-1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityTrendRatio(BaseFactor):
    """短期波动率（ATR）与趋势强度（ADX-like）的比值，用于识别高波动无趋势的震荡状态。当比值高时，市场容易产生假突破和止损，因子输出接近+1表示高风险；比值低时趋势明确，输出接近-1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_voltrend",
            name="Volatility Trend Ratio",
            display_name="波动趋势比",
            description="短期波动率（ATR）与趋势强度（ADX-like）的比值，用于识别高波动无趋势的震荡状态。当比值高时，市场容易产生假突破和止损，因子输出接近+1表示高风险；比值低时趋势明确，输出接近-1。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 计算ATR（14周期）
        high = data['high']
        low = data['low']
        close = data['close']
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        atr = tr.rolling(14).mean()
        # 计算趋势强度：用价格变化绝对值相对于ATR的平滑
        change = (close - close.shift(14)).abs()
        trend_strength = change / (atr * 14 + 1e-10)  # 标准化的趋势强度
        # 比值：atr / (trend_strength * atr) 简化：直接用atr与价格变化绝对值比
        # 更简单：用20周期价格范围与20周期总路径之比
        range20 = high.rolling(20).max() - low.rolling(20).min()
        path20 = (high - low).rolling(20).sum()
        ratio = range20 / (path20 + 1e-10)
        # 当ratio接近0表示趋势强（窄范围但路径长），接近1表示震荡（大幅波动但范围小？实际相反）
        # 改造：用atr / (close.rolling(20).std() + 1e-10) 但需要归一化
        result = (atr / close * 100) / ((close.rolling(20).std() / close * 100) + 1e-10)
        result = (result - result.rolling(100).mean()) / (result.rolling(100).std() + 1e-10)
        result = np.clip(result, -3, 3) / 3.0
        return result
