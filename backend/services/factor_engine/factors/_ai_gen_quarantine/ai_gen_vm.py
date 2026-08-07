"""AI因子: 波动率动量背离因子 | 置信:60% | 当波动率收缩而价格动量出现背离（价格新高但RSI走低）时，预示趋势难以持续，因子给出负值以避免多头持仓。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityMomentumDivergence(BaseFactor):
    """当波动率收缩而价格动量出现背离（价格新高但RSI走低）时，预示趋势难以持续，因子给出负值以避免多头持仓。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vm",
            name="Volatility-Momentum Divergence",
            display_name="波动率动量背离因子",
            description="当波动率收缩而价格动量出现背离（价格新高但RSI走低）时，预示趋势难以持续，因子给出负值以避免多头持仓。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        # ATR 波动率
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        atr_ratio = atr / atr.rolling(100).mean().replace(0, np.nan)  # 当前ATR相对长期均值
        vol_score = np.tanh((1.0 - atr_ratio) * 3)  # 波动率收缩时为正
        # RSI 14
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100.0 - (100.0 / (1.0 + rs))
        rsi_scaled = (rsi - 50) / 50  # -1 到 +1 范围
        # 价格新高检测 (5日最高价创20日新高)
        highest5 = close.rolling(5).max()
        highest20 = close.rolling(20).max()
        new_high = (highest5 == highest20) & (highest20 > highest20.shift(1))
        # RSI背离：价格新高但RSI未新高
        rsi_high = rsi.rolling(5).max()
        rsi_highest20 = rsi.rolling(20).max()
        divergence = new_high & (rsi_high < rsi_highest20.shift(1))  # 背离信号
        # 将背离信号转换为连续值：背离出现后持续为-1，否则由vol_score决定
        signal = divergence.astype(float) * -1.0
        # 平滑，背离时偏负，波动率收缩时偏正，综合
        raw = vol_score * 0.5 + signal * 0.5
        result = raw.clip(-1, 1)
        return result
