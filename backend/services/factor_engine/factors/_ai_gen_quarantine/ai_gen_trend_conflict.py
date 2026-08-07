"""AI因子: 趋势冲突指标 | 置信:50% | 检测短期（5周期）与长期（30周期）趋势方向是否一致。分别计算短期和长期简单移动平均线的斜率（使用线性回归或差分），若两者方向相反则表示市场状态不确定。输出为-1（一致）到+1（冲突）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendConflictIndicator(BaseFactor):
    """检测短期（5周期）与长期（30周期）趋势方向是否一致。分别计算短期和长期简单移动平均线的斜率（使用线性回归或差分），若两者方向相反则表示市场状态不确定。输出为-1（一致）到+1（冲突）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_conflict",
            name="Trend Conflict Indicator",
            display_name="趋势冲突指标",
            description="检测短期（5周期）与长期（30周期）趋势方向是否一致。分别计算短期和长期简单移动平均线的斜率（使用线性回归或差分），若两者方向相反则表示市场状态不确定。输出为-1（一致）到+1（冲突）。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 计算短期和长期均线
        short_ma = data['close'].rolling(window=5, min_periods=3).mean()
        long_ma = data['close'].rolling(window=30, min_periods=10).mean()
        # 斜率：使用当前值与N周期前的差值作为斜率近似
        short_slope = short_ma - short_ma.shift(3)
        long_slope = long_ma - long_ma.shift(10)
        # 符号
        short_sign = np.sign(short_slope).fillna(0)
        long_sign = np.sign(long_slope).fillna(0)
        # 冲突程度：如果符号相反则为+1，相同则为-1，其中一个为零则为0
        conflict = np.where(short_sign == 0, 0, np.where(long_sign == 0, 0, np.where(short_sign != long_sign, 1, -1)))
        # 平滑一下，使用滚动平均避免频繁跳变
        result = pd.Series(conflict, index=data.index).rolling(3, min_periods=1).mean()
        return result
