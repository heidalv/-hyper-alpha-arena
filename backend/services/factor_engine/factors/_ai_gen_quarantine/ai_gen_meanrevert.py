"""AI因子: RSI均值回归因子 | 置信:55% | 使用14日RSI判断超买超卖，当RSI高于70时做空，低于30时做多，中间区域线性输出。针对regime unknown状态，均值回归策略可能有效捕捉反转。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RSI_Mean_Reversion_Factor(BaseFactor):
    """使用14日RSI判断超买超卖，当RSI高于70时做空，低于30时做多，中间区域线性输出。针对regime unknown状态，均值回归策略可能有效捕捉反转。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_meanrevert",
            name="RSI Mean Reversion Factor",
            display_name="RSI均值回归因子",
            description="使用14日RSI判断超买超卖，当RSI高于70时做空，低于30时做多，中间区域线性输出。针对regime unknown状态，均值回归策略可能有效捕捉反转。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        df = data.copy()
        close = df['close']
        period = 14
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
        rs = avg_gain / (avg_loss + 1e-10)
        rsi = 100 - 100 / (1 + rs)
        # Map to [-1, 1]: RSI>70 => -1, RSI<30 => +1, linear in between
        factor = pd.Series(np.where(rsi > 70, -1.0, np.where(rsi < 30, 1.0, (70 - rsi) / 20 * 2 - 1)), index=df.index)
        factor = factor.fillna(0).clip(-1, 1)
        return factor
