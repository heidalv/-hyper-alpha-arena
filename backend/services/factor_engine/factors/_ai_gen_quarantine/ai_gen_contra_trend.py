"""AI因子: 反趋势动量衰减因子 | 置信:63% | 捕捉短期价格动量衰竭迹象，当连续阳线后出现阴线且成交量缩小，或价格在布林带上轨附近回落，意味着短期上涨动能不足，此时做多容易亏损。使用过去3日价格涨跌一致性以及相对强弱指数（RSI）来构建信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Contrarian_Momentum_Decay(BaseFactor):
    """捕捉短期价格动量衰竭迹象，当连续阳线后出现阴线且成交量缩小，或价格在布林带上轨附近回落，意味着短期上涨动能不足，此时做多容易亏损。使用过去3日价格涨跌一致性以及相对强弱指数（RSI）来构建信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_contra_trend",
            name="Contrarian Momentum Decay",
            display_name="反趋势动量衰减因子",
            description="捕捉短期价格动量衰竭迹象，当连续阳线后出现阴线且成交量缩小，或价格在布林带上轨附近回落，意味着短期上涨动能不足，此时做多容易亏损。使用过去3日价格涨跌一致性以及相对强弱指数（RSI）来构建信号。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        df = data.copy()
        close = df['close']
        high = df['high']
        low = df['low']
        # RSI (14)
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean().replace(0, 1e-8)
        rsi = 100 - (100 / (1 + avg_gain / avg_loss))
        # 过去3日连续上涨次数
        up_days = (close.diff() > 0).rolling(3).sum()
        # 当前是否出现阴线（close < open）
        is_bear = (close < df['open']).astype(int)
        # 布林带上轨阻力
        ma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        upper_band = ma20 + 2 * std20
        near_upper = (close >= upper_band * 0.98).astype(int)
        # 合成信号：连续上涨后出现阴线或rsi高于70且接近上轨 -> 负向
        condition = (up_days >= 2) & (is_bear == 1)
        condition2 = (rsi > 70) & (near_upper == 1)
        raw = - (condition.astype(float) * 0.5 + condition2.astype(float) * 0.5)
        # 平滑并归一化
        result = raw.rolling(3).mean().fillna(0).clip(-1, 1)
        return result
