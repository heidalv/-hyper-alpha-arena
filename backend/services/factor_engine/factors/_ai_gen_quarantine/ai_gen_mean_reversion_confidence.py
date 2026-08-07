"""AI因子: 均值回归质量因子 | 置信:55% | 结合RSI、价格偏离均线程度和波动率，评估当前均值回归信号的可信度。当价格过度偏离（如RSI超买/超卖）且波动率较低时，回归概率高，输出正值；当市场无序震荡时，回归信号易失效，输出负值。对应dust_cleanup和ai_reverse等模式的亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MeanReversionQuality(BaseFactor):
    """结合RSI、价格偏离均线程度和波动率，评估当前均值回归信号的可信度。当价格过度偏离（如RSI超买/超卖）且波动率较低时，回归概率高，输出正值；当市场无序震荡时，回归信号易失效，输出负值。对应dust_cleanup和ai_reverse等模式的亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mean_reversion_confidence",
            name="Mean Reversion Quality",
            display_name="均值回归质量因子",
            description="结合RSI、价格偏离均线程度和波动率，评估当前均值回归信号的可信度。当价格过度偏离（如RSI超买/超卖）且波动率较低时，回归概率高，输出正值；当市场无序震荡时，回归信号易失效，输出负值。对应dust_cleanup和ai_reverse等模式的亏损。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        period = 14
        # RSI
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(window=period).mean()
        avg_loss = loss.rolling(window=period).mean()
        rs = avg_gain / (avg_loss + 1e-10)
        rsi = 100 - (100 / (1 + rs))
        # 偏离度：价格相对于200周期均线
        ma200 = close.rolling(window=200).mean()
        deviation = (close - ma200) / (ma200 + 1e-10)
        # 波动率
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        atr = tr.rolling(window=14).mean()
        atr_ma = atr.rolling(window=50).mean()
        vol_ratio = atr / (atr_ma + 1e-10)
        # 超买超卖条件（RSI>70或<30）且偏离度大
        extreme = ((rsi > 70) | (rsi < 30)).astype(float)
        # 波动率低时回归信号更可靠
        low_vol = (vol_ratio < 1.0).astype(float)
        # 计算得分：极端+低波动 -> 正信号；否则负信号
        score = 0.7 * extreme * low_vol - 0.3 * (1 - extreme) * (vol_ratio > 1.2)
        score = np.clip(score, -1, 1)
        return pd.Series(score, index=data.index)
