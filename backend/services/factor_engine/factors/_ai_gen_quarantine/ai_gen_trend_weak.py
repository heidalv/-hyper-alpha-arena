"""AI因子: 趋势强度弱势 | 置信:60% | 基于ADX和价格斜率，评估当前趋势的强度。当趋势很弱时（ADX低且价格震荡），策略更容易因盘整而亏损。因子输出正值表示趋势弱（应避免交易），负值表示趋势强。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendStrengthWeakness(BaseFactor):
    """基于ADX和价格斜率，评估当前趋势的强度。当趋势很弱时（ADX低且价格震荡），策略更容易因盘整而亏损。因子输出正值表示趋势弱（应避免交易），负值表示趋势强。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_weak",
            name="Trend Strength Weakness",
            display_name="趋势强度弱势",
            description="基于ADX和价格斜率，评估当前趋势的强度。当趋势很弱时（ADX低且价格震荡），策略更容易因盘整而亏损。因子输出正值表示趋势弱（应避免交易），负值表示趋势强。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high = data['high']
        low = data['low']
        close = data['close']
        # 计算正向波动 +DM, -DM
        high_diff = high.diff()
        low_diff = low.diff() * -1
        plus_dm = np.where((high_diff > low_diff) & (high_diff > 0), high_diff, 0.0)
        minus_dm = np.where((low_diff > high_diff) & (low_diff > 0), low_diff, 0.0)
        # TR
        tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
        # 平滑14期
        tr14 = tr.rolling(14).sum()
        plus_di = 100 * pd.Series(plus_dm).rolling(14).sum() / (tr14 + 1e-8)
        minus_di = 100 * pd.Series(minus_dm).rolling(14).sum() / (tr14 + 1e-8)
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-8)
        adx = dx.rolling(14).mean()
        # 价格斜率（6日线性回归斜率归一化）
        slope = close.diff(6) / close.shift(6)
        # 趋势弱：ADX低且斜率小
        weak = 1 - (adx / 100) * (1 - slope.abs().clip(0, 0.1)/0.1)
        # 归一化
        result = weak.rank(pct=True) * 2 - 1
        return result.fillna(0)
