"""AI因子: 波动率调整RSI超买 | 置信:65% | 结合近期波动率与RSI超买信号，在高波动环境下识别价格处于高位的做多风险。当波动率高于近期均值且RSI大于70时，因子值为负（看空），反之为正。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Adjusted_RSI_Overbought(BaseFactor):
    """结合近期波动率与RSI超买信号，在高波动环境下识别价格处于高位的做多风险。当波动率高于近期均值且RSI大于70时，因子值为负（看空），反之为正。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volrsi",
            name="Volatility-Adjusted RSI Overbought",
            display_name="波动率调整RSI超买",
            description="结合近期波动率与RSI超买信号，在高波动环境下识别价格处于高位的做多风险。当波动率高于近期均值且RSI大于70时，因子值为负（看空），反之为正。",
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
        # 计算ATR（14日）
        tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        # 归一化ATR为近期比率
        atr_ratio = atr / close.rolling(20).mean()
        atr_ratio_ma = atr_ratio.rolling(20).mean()
        vol_signal = (atr_ratio > atr_ratio_ma * 1.2).astype(float)  # 高波动标记
        # 计算RSI(14)
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        # 超买信号
        overbought = (rsi > 70).astype(float)
        # 综合：高波动且超买时看空，否则看多
        factor = -1 * vol_signal * overbought + (1 - vol_signal * overbought)
        # 映射到[-1,1]
        factor = factor * 2 - 1
        return factor
