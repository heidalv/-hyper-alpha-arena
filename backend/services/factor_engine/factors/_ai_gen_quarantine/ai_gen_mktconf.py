"""AI因子: 市场混乱指数 | 置信:65% | 通过统计过去N根K线价格方向的变化频率和幅度，衡量市场当前的不确定性。频繁的反向波动（正负交替）表明市场处于方向不明的混乱状态，易导致反转策略失败。值接近+1表示高度混乱，接近-1表示趋势明确。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MarketConfusionIndex(BaseFactor):
    """通过统计过去N根K线价格方向的变化频率和幅度，衡量市场当前的不确定性。频繁的反向波动（正负交替）表明市场处于方向不明的混乱状态，易导致反转策略失败。值接近+1表示高度混乱，接近-1表示趋势明确。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mktconf",
            name="Market Confusion Index",
            display_name="市场混乱指数",
            description="通过统计过去N根K线价格方向的变化频率和幅度，衡量市场当前的不确定性。频繁的反向波动（正负交替）表明市场处于方向不明的混乱状态，易导致反转策略失败。值接近+1表示高度混乱，接近-1表示趋势明确。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        lookback = 10
        close = data['close']
        returns = close.pct_change()
        direction = np.sign(returns)
        # 计算符号变化次数
        sign_changes = (direction.diff() != 0).astype(int)
        # 滚动求和变化次数，表示混乱程度
        confusion_raw = sign_changes.rolling(window=lookback, min_periods=lookback).sum() / lookback
        # 乘以波动率调整：用ATR归一化
        high = data['high']
        low = data['low']
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(window=14, min_periods=14).mean()
        # 用波动率缩放，但保持值域[-1,1]
        # 先标准化波动率到0~1
        atr_norm = (atr - atr.rolling(window=50, min_periods=50).min()) / (atr.rolling(window=50, min_periods=50).max() - atr.rolling(window=50, min_periods=50).min() + 1e-10)
        # 将混乱度与波动率结合，激进部分使用clip
        result = confusion_raw * (1 + atr_norm) * 0.5  # 范围在0~1
        # 映射到[-1,1]，用中心化方法：减去0.5后乘以2
        result = (result - 0.5) * 2
        return result.fillna(0).clip(-1, 1)
