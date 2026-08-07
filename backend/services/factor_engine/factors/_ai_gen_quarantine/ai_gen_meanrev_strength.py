"""AI因子: 均值回归强度 | 置信:55% | 基于RSI和布林带宽度评估价格回归均值的概率。当价格极端且布林带较宽时，均值回归可能性高；结合RSI方向给出信号。正值表示向上回归，负值向下回归。用于过滤hold_timeout和sl类亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MeanReversionStrength(BaseFactor):
    """基于RSI和布林带宽度评估价格回归均值的概率。当价格极端且布林带较宽时，均值回归可能性高；结合RSI方向给出信号。正值表示向上回归，负值向下回归。用于过滤hold_timeout和sl类亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_meanrev_strength",
            name="Mean Reversion Strength",
            display_name="均值回归强度",
            description="基于RSI和布林带宽度评估价格回归均值的概率。当价格极端且布林带较宽时，均值回归可能性高；结合RSI方向给出信号。正值表示向上回归，负值向下回归。用于过滤hold_timeout和sl类亏损。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        # 计算RSI(14)
        delta = close.diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        avg_gain = gain.rolling(window=14).mean()
        avg_loss = loss.rolling(window=14).mean()
        rs = avg_gain / (avg_loss + 1e-10)
        rsi = 100 - 100 / (1 + rs)
        # 布林带(20,2)
        ma = close.rolling(window=20).mean()
        std = close.rolling(window=20).std()
        upper = ma + 2*std
        lower = ma - 2*std
        # 宽度标准化
        bandwidth = (upper - lower) / (ma + 1e-10)
        # 位置：0在中间，-1在下轨，1在上轨
        position = (close - ma) / (2*std + 1e-10)
        # 均值回归信号：价格偏离时结合RSI超买超卖
        rsi_extreme = (rsi - 50) / 50  # -1到1
        # 当价格触及上轨且RSI>70时，预期向下回归（负信号），反之向上
        rev_signal = -position * np.clip(bandwidth * 2, 0, 1) * np.abs(rsi_extreme)
        # 补充：当短期动量与长期偏离反向时增强
        short_ret = close.pct_change(3)
        long_dev = (close - close.rolling(window=50).mean()) / (close.rolling(window=50).mean() + 1e-10)
        momentum_conf = -np.sign(short_ret) * np.sign(long_dev)  # 短期与长期反向时为正
        rev_signal = rev_signal * (1 + 0.3 * momentum_conf)
        result = np.clip(rev_signal, -1, 1)
        return result
