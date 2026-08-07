"""AI因子: 反转风险 | 置信:60% | 综合价格动量与RSI极端值，识别短期反转风险。当价格远离20日均线且RSI进入超买/超卖区域时，预示可能出现类似'ai_reverse'的反转亏损。计算价格相对20日均线的归一化偏离与RSI偏移的乘积，取负值表达风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ReversalRisk(BaseFactor):
    """综合价格动量与RSI极端值，识别短期反转风险。当价格远离20日均线且RSI进入超买/超卖区域时，预示可能出现类似'ai_reverse'的反转亏损。计算价格相对20日均线的归一化偏离与RSI偏移的乘积，取负值表达风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_revrisk",
            name="Reversal Risk",
            display_name="反转风险",
            description="综合价格动量与RSI极端值，识别短期反转风险。当价格远离20日均线且RSI进入超买/超卖区域时，预示可能出现类似'ai_reverse'的反转亏损。计算价格相对20日均线的归一化偏离与RSI偏移的乘积，取负值表达风险。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        sma20 = data['close'].rolling(20).mean()
        dev = (data['close'] - sma20) / (data['close'].rolling(20).std() + 1e-10)
        delta = data['close'].diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / (avg_loss + 1e-10)
        rsi = 100 - 100/(1+rs)
        rsi_signal = (rsi - 50) / 50.0
        factor = -0.5 * dev * rsi_signal
        factor = factor.clip(-1.0, 1.0)
        return factor
