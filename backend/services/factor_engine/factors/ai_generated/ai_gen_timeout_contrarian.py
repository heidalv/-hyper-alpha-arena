"""AI因子: 超时反向均值回归 | 置信:65% | 针对max_hold_timeout亏损模式：当价格在持仓周期内缓慢阴跌（小实体阴线连续）但未触发止损，最终因超时平仓亏损。该因子识别超卖但未充分反弹的状态，通过短期收益率与长期均线偏离度构建反向信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Timeoutcontrarianbias(BaseFactor):
    """针对max_hold_timeout亏损模式：当价格在持仓周期内缓慢阴跌（小实体阴线连续）但未触发止损，最终因超时平仓亏损。该因子识别超卖但未充分反弹的状态，通过短期收益率与长期均线偏离度构建反向信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_timeout_contrarian",
            name="TimeoutContrarianBias",
            display_name="超时反向均值回归",
            description="针对max_hold_timeout亏损模式：当价格在持仓周期内缓慢阴跌（小实体阴线连续）但未触发止损，最终因超时平仓亏损。该因子识别超卖但未充分反弹的状态，通过短期收益率与长期均线偏离度构建反向信号。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short_ret = data['close'].pct_change(5)
        long_ret = data['close'].pct_change(20)
        body = (data['close'] - data['open']) / (data['high'] - data['low'] + 1e-9)
        body_neg = (body < 0).astype(float).rolling(10).mean()
        result = (long_ret - short_ret) * body_neg
        result = (result - result.rolling(30).mean()) / (result.rolling(30).std() + 1e-9)
        return result.clip(-1, 1)
