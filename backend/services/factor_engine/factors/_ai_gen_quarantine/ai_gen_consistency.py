"""AI因子: 价格行为一致性得分 | 置信:55% | 通过比较开盘价与收盘价的关系、上下影线长度、以及连续K线的方向一致性，评估当前价格行为是否有序。当连续出现反转K线（如长上下影线、收盘与开盘方向相反）时，市场处于混乱状态，容易导致止损或超时亏损；输出负值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PriceActionConsistencyScore(BaseFactor):
    """通过比较开盘价与收盘价的关系、上下影线长度、以及连续K线的方向一致性，评估当前价格行为是否有序。当连续出现反转K线（如长上下影线、收盘与开盘方向相反）时，市场处于混乱状态，容易导致止损或超时亏损；输出负值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_consistency",
            name="Price Action Consistency Score",
            display_name="价格行为一致性得分",
            description="通过比较开盘价与收盘价的关系、上下影线长度、以及连续K线的方向一致性，评估当前价格行为是否有序。当连续出现反转K线（如长上下影线、收盘与开盘方向相反）时，市场处于混乱状态，容易导致止损或超时亏损；输出负值。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        open_ = data['open']
        high = data['high']
        low = data['low']
        close = data['close']
        # 计算实体内核比例（收盘-开盘绝对值/高低范围）
        body = (close - open_).abs()
        range_ = high - low + 1e-10
        body_ratio = body / range_
        # 小实体（<0.3）表示犹豫
        small_body = body_ratio < 0.3
        # 上下影线比例
        upper_shadow = (high - close.where(close > open_, open_)).abs()
        lower_shadow = (close.where(close < open_, open_) - low).abs()
        shadow_ratio = (upper_shadow + lower_shadow) / range_
        long_shadow = shadow_ratio > 0.6
        # 连续方向的改变：最近3根K线的涨跌方向是否一致
        direction = (close - open_).apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
        dir_change = direction.diff().abs().rolling(2).sum() > 0  # 有方向变化
        # 综合：混乱状态 = (小实体且长影线) 或 (方向频繁变换)
        chaotic = (small_body & long_shadow) | dir_change
        # 因子：正常有序时+1，混乱时-1
        factor = pd.Series(1.0, index=data.index)
        factor[chaotic] = -1.0
        # 平滑
        return factor.rolling(3).mean().fillna(0.0).clip(-1, 1)
