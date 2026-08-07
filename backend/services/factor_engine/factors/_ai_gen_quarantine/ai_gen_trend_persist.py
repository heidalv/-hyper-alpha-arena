"""AI因子: 趋势持续性预警因子 | 置信:50% | 衡量当前趋势的强弱与波动性之比，用于识别趋势持续性差、容易发生超时亏损（hold_timeout）的行情。当趋势强度低于波动率时，因子值偏向0或负值，提示应避免持有头寸过久。正值表示趋势稳固，适合持有。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendWeaknessHoldTimeoutWarning(BaseFactor):
    """衡量当前趋势的强弱与波动性之比，用于识别趋势持续性差、容易发生超时亏损（hold_timeout）的行情。当趋势强度低于波动率时，因子值偏向0或负值，提示应避免持有头寸过久。正值表示趋势稳固，适合持有。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_persist",
            name="Trend Weakness / Hold Timeout Warning",
            display_name="趋势持续性预警因子",
            description="衡量当前趋势的强弱与波动性之比，用于识别趋势持续性差、容易发生超时亏损（hold_timeout）的行情。当趋势强度低于波动率时，因子值偏向0或负值，提示应避免持有头寸过久。正值表示趋势稳固，适合持有。",
            category="composite",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 计算短期动量（5期价格变化率）
        ret_short = data['close'].pct_change(5)
        # 计算近期波动率（10期ATR标准化）
        high_low = data['high'] - data['low']
        atr = high_low.rolling(window=10, min_periods=5).mean()
        # 价格中枢
        mid = (data['high'] + data['low']) / 2
        norm_vol = atr / mid.replace(0, np.nan)  # 相对波动
        # 趋势强度：用动量绝对值与波动率比值，然后取符号
        strength = ret_short.abs() / (norm_vol + 1e-8)
        # 将strength标准化到0~1，然后乘以方向
        strength_norm = strength.clip(0, 2) / 2
        direction = np.sign(ret_short).fillna(0)
        result = direction * strength_norm
        # 填充缺失值
        return result.fillna(0)
