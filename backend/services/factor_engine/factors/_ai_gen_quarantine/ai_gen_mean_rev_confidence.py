"""AI因子: 均值回归信心因子 | 置信:60% | 结合RSI和布林带宽度，当RSI在中性区（40-60）且带宽较窄时，市场可能处于震荡，趋势不明确，此时多头风险大，因子偏向负值；当RSI极端或带宽扩大时，趋势可能明确，因子偏向正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Mean_Reversion_Confidence(BaseFactor):
    """结合RSI和布林带宽度，当RSI在中性区（40-60）且带宽较窄时，市场可能处于震荡，趋势不明确，此时多头风险大，因子偏向负值；当RSI极端或带宽扩大时，趋势可能明确，因子偏向正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mean_rev_confidence",
            name="Mean Reversion Confidence",
            display_name="均值回归信心因子",
            description="结合RSI和布林带宽度，当RSI在中性区（40-60）且带宽较窄时，市场可能处于震荡，趋势不明确，此时多头风险大，因子偏向负值；当RSI极端或带宽扩大时，趋势可能明确，因子偏向正值。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        high = data['high']
        low = data['low']
        period = 20
        # 计算RSI
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(period).mean()
        avg_loss = loss.rolling(period).mean()
        rs = avg_gain / (avg_loss + 1e-10)
        rsi = 100 - 100 / (1 + rs)
        # 计算布林带宽度
        ma = close.rolling(period).mean()
        std = close.rolling(period).std()
        bandwidth = 2 * std / ma
        # 当RSI在40-60且带宽小于中位数时，信号偏向-1
        rsi_mid = (rsi - 50) / 50  # [-1,1]
        bw_norm = (bandwidth - bandwidth.rolling(100).mean()) / bandwidth.rolling(100).std()
        # 结合：RSI极端时正，带宽异常大时正
        score = -0.5 * np.exp(-rsi_mid**2 / 0.5) * np.exp(-bw_norm**2 / 2) + 0.5 * (np.abs(rsi_mid) - 0.5).clip(0,1)
        result = np.clip(score, -1, 1)
        return result.fillna(0)
