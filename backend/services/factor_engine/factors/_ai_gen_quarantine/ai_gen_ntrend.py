"""AI因子: 归一化趋势强度 | 置信:65% | 基于历史价格变化和波动率的比值度量当前趋势强度，当趋势非常弱（市场无方向）时接近-1，强趋势时接近+1。适用于识别'unknown regime'低趋势环境。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Normalized_Trend_Strength(BaseFactor):
    """基于历史价格变化和波动率的比值度量当前趋势强度，当趋势非常弱（市场无方向）时接近-1，强趋势时接近+1。适用于识别'unknown regime'低趋势环境。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_ntrend",
            name="Normalized Trend Strength",
            display_name="归一化趋势强度",
            description="基于历史价格变化和波动率的比值度量当前趋势强度，当趋势非常弱（市场无方向）时接近-1，强趋势时接近+1。适用于识别'unknown regime'低趋势环境。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        # 计算价格变化率（20日）
        ret = close.pct_change(periods=20)
        # 平滑波动率（ATR 20日）
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        atr = tr.rolling(20).mean()
        # 相对趋势强度：价格变化绝对值 / ATR
        trend_strength = ret.abs() / (atr / close)
        trend_strength = trend_strength.replace([np.inf, -np.inf], np.nan)
        # 归一化到[-1,1]，使用z-score截断
        mean = trend_strength.rolling(60).mean()
        std = trend_strength.rolling(60).std()
        z = (trend_strength - mean) / std
        result = np.clip(z, -3, 3) / 3.0  # 映射到[-1,1]
        return result
