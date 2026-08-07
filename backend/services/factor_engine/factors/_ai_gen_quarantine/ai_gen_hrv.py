"""AI因子: 波动率峰值反转 | 置信:68% | 真实波幅达到近期高点后回落，结合价格偏离均线程度，捕捉波动率爆发后的均值回归。超时亏损常发生在波动率异常后趋势衰竭阶段。正值看多，负值看空。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityPeakReversal(BaseFactor):
    """真实波幅达到近期高点后回落，结合价格偏离均线程度，捕捉波动率爆发后的均值回归。超时亏损常发生在波动率异常后趋势衰竭阶段。正值看多，负值看空。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_hrv",
            name="Volatility Peak Reversal",
            display_name="波动率峰值反转",
            description="真实波幅达到近期高点后回落，结合价格偏离均线程度，捕捉波动率爆发后的均值回归。超时亏损常发生在波动率异常后趋势衰竭阶段。正值看多，负值看空。",
            category="volatility",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        # ATR (14)
        tr = np.maximum(high - low, np.abs(high - close.shift()), np.abs(low - close.shift()))
        atr = tr.rolling(14).mean()
        # ATR近期峰值：20日滚动最大值
        atr_max = atr.rolling(20).max()
        atr_ratio = atr / (atr_max + 1e-9)  # 0~1
        # ATR从峰值回落程度
        atr_fall = 1 - atr_ratio
        # 价格偏离20日均线的标准化程度
        ma = close.rolling(20).mean()
        std = close.rolling(20).std()
        zscore = (close - ma) / (std + 1e-9)
        # 信号：波动率回落且价格在高位 -> 看空；波动率回落且价格在低位 -> 看多
        signal = -np.sign(zscore) * atr_fall
        # 仅在atr_fall显著时生效 (如 > 0.2)
        signal = np.where(atr_fall > 0.2, signal, 0.0)
        result = pd.Series(signal, index=data.index).clip(-1, 1).fillna(0)
        return result.rolling(2).mean().fillna(0).clip(-1, 1)
