"""AI因子: 波动调整RSI反转 | 置信:60% | 计算RSI并基于ATR调整阈值，当RSI在低波动环境下突破极值时产生反转信号。低波动环境（ATR相对较小）中，RSI超买/超卖更容易导致反转。输出范围[-1,1]，正值表示看多（超卖反转），负值看空（超买反转）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Adjusted_RSI_Reversal(BaseFactor):
    """计算RSI并基于ATR调整阈值，当RSI在低波动环境下突破极值时产生反转信号。低波动环境（ATR相对较小）中，RSI超买/超卖更容易导致反转。输出范围[-1,1]，正值表示看多（超卖反转），负值看空（超买反转）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vrsi",
            name="Volatility-Adjusted RSI Reversal",
            display_name="波动调整RSI反转",
            description="计算RSI并基于ATR调整阈值，当RSI在低波动环境下突破极值时产生反转信号。低波动环境（ATR相对较小）中，RSI超买/超卖更容易导致反转。输出范围[-1,1]，正值表示看多（超卖反转），负值看空（超买反转）。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        delta = data['close'].diff()
        gain = np.where(delta > 0, delta, 0)
        loss = np.where(delta < 0, -delta, 0)
        avg_gain = pd.Series(gain).rolling(14).mean()
        avg_loss = pd.Series(loss).rolling(14).mean()
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        atr = (data['high'] - data['low']).rolling(14).mean()
        atr_avg = atr.rolling(50).mean()
        vol_ratio = atr / atr_avg
        # 低波动时阈值更敏感
        threshold = 30 + 20 * (1 - vol_ratio.clip(0,1))
        upper = 100 - threshold
        lower = threshold
        signal = np.zeros(len(data))
        signal = pd.Series(np.where(rsi > upper, -1, np.where(rsi < lower, 1, 0)))
        return signal.fillna(0)
