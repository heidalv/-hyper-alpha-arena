"""AI因子: 成交量加权RSI | 置信:60% | 使用14日RSI，但用成交量加权计算价格变化，减少低量噪音。当RSI低于30且成交量放大时发出买入信号（+1）；高于70时发出卖出信号（-1）；其余为0。适用于震荡市中的超买超卖识别。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RSI_Volume_Weighted(BaseFactor):
    """使用14日RSI，但用成交量加权计算价格变化，减少低量噪音。当RSI低于30且成交量放大时发出买入信号（+1）；高于70时发出卖出信号（-1）；其余为0。适用于震荡市中的超买超卖识别。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_rsi_vol",
            name="RSI-Volume Weighted",
            display_name="成交量加权RSI",
            description="使用14日RSI，但用成交量加权计算价格变化，减少低量噪音。当RSI低于30且成交量放大时发出买入信号（+1）；高于70时发出卖出信号（-1）；其余为0。适用于震荡市中的超买超卖识别。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        volume = data['volume']
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        # 成交量加权平均
        avg_gain = (gain * volume).rolling(14).sum() / volume.rolling(14).sum()
        avg_loss = (loss * volume).rolling(14).sum() / volume.rolling(14).sum()
        rs = avg_gain / avg_loss.replace(0, 1e-10)
        rsi = 100 - (100 / (1 + rs))
        # 生成信号：超卖且有量 -> +1；超买带量 -> -1
        vol_ma = volume.rolling(20).mean()
        cond_buy = (rsi < 30) & (volume > 1.5 * vol_ma)
        cond_sell = (rsi > 70) & (volume > 1.5 * vol_ma)
        result = pd.Series(0.0, index=data.index)
        result[cond_buy] = 1.0
        result[cond_sell] = -1.0
        return result
