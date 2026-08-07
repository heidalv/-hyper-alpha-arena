"""AI因子: 下跌趋势强度 | 置信:60% | 通过短期均线（5）与长期均线（20）的差值除以收盘价，再乘以波动率调整因子，衡量下跌趋势的强度。正值表示下跌趋势强劲，空头信号；负值表示上涨趋势。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Downside_Trend_Strength(BaseFactor):
    """通过短期均线（5）与长期均线（20）的差值除以收盘价，再乘以波动率调整因子，衡量下跌趋势的强度。正值表示下跌趋势强劲，空头信号；负值表示上涨趋势。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_dtrend",
            name="Downside Trend Strength",
            display_name="下跌趋势强度",
            description="通过短期均线（5）与长期均线（20）的差值除以收盘价，再乘以波动率调整因子，衡量下跌趋势的强度。正值表示下跌趋势强劲，空头信号；负值表示上涨趋势。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        high = data['high']
        low = data['low']
        # 计算均线
        ma_short = close.rolling(5).mean()
        ma_long = close.rolling(20).mean()
        # 趋势差值
        trend = (ma_short - ma_long) / close
        # 波动率调整（ATR百分比）
        tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        atr_pct = atr / close
        # 避免除零
        atr_pct = atr_pct.replace(0, np.nan)
        factor = -trend / (atr_pct + 1e-10)  # 负号使得下跌趋势时因子为正
        # 归一化到[-1,1]
        factor = factor.rolling(50).apply(lambda x: np.clip((x - x.mean()) / (x.std() + 1e-10), -1, 1), raw=True)
        return factor.fillna(0).clip(-1, 1)
