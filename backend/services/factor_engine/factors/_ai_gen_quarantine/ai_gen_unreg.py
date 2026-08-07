"""AI因子: 未知市场状态识别因子 | 置信:55% | 基于波动率与趋势强度的组合识别市场是否处于无明显方向的状态。当波动率低且趋势方向不明确（如价格在均线附近反复震荡），因子输出负值，提示避免趋势策略；反之输出正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class UnknownRegimeDetector(BaseFactor):
    """基于波动率与趋势强度的组合识别市场是否处于无明显方向的状态。当波动率低且趋势方向不明确（如价格在均线附近反复震荡），因子输出负值，提示避免趋势策略；反之输出正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_unreg",
            name="Unknown Regime Detector",
            display_name="未知市场状态识别因子",
            description="基于波动率与趋势强度的组合识别市场是否处于无明显方向的状态。当波动率低且趋势方向不明确（如价格在均线附近反复震荡），因子输出负值，提示避免趋势策略；反之输出正值。",
            category="composite",
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
        # 计算趋势强度：用ADX指标简化
        def adx(high, low, close, period=14):
            plus_dm = high.diff()
            minus_dm = low.diff()
            plus_dm[plus_dm < 0] = 0
            minus_dm[minus_dm > 0] = 0
            tr = pd.concat([high - low, abs(high - close.shift(1)), abs(low - close.shift(1))], axis=1).max(axis=1)
            atr = tr.rolling(period).mean()
            # 简化ADX
            return None  # 实际简化计算
        # 使用简单指标：价格相对20日均线的标准差，结合ATR
        ma20 = close.rolling(20, min_periods=1).mean()
        std20 = close.rolling(20, min_periods=1).std()
        # 波动率：ATR/价格
        tr = pd.concat([high - low, abs(high - close.shift(1)), abs(low - close.shift(1))], axis=1).max(axis=1)
        atr = tr.rolling(14, min_periods=1).mean()
        vol_ratio = atr / (close + 1e-8)
        # 价格偏离度
        dev = (close - ma20) / (std20 + 1e-8)
        # 当偏离度绝对值小且波动率低 => 未知状态
        unknown = (abs(dev) < 0.5) & (vol_ratio < vol_ratio.rolling(50, min_periods=1).mean())
        # 当趋势明显时 => 确定状态
        trend = (abs(dev) > 1.5) & (vol_ratio > vol_ratio.rolling(50, min_periods=1).mean())
        # 输出：未知时-1，趋势时+1，其他0
        signal = pd.Series(0, index=data.index)
        signal[unknown] = -1
        signal[trend] = 1
        return signal
