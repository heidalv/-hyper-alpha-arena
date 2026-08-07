"""AI因子: 多周期趋势一致性 | 置信:60% | 比较短期（例如5日）与长期（例如20日）趋势方向是否一致。当两者方向相反或均无明显趋势时（如震荡），做多胜率低。该因子计算两个时间框架的移动平均线斜率，取其一致性度量，输出[-1,1]：正值表示趋势一致向上，负值表示不一致或向下。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Multi_Timeframe_Trend_Consistency(BaseFactor):
    """比较短期（例如5日）与长期（例如20日）趋势方向是否一致。当两者方向相反或均无明显趋势时（如震荡），做多胜率低。该因子计算两个时间框架的移动平均线斜率，取其一致性度量，输出[-1,1]：正值表示趋势一致向上，负值表示不一致或向下。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mtc",
            name="Multi-Timeframe Trend Consistency",
            display_name="多周期趋势一致性",
            description="比较短期（例如5日）与长期（例如20日）趋势方向是否一致。当两者方向相反或均无明显趋势时（如震荡），做多胜率低。该因子计算两个时间框架的移动平均线斜率，取其一致性度量，输出[-1,1]：正值表示趋势一致向上，负值表示不一致或向下。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short_ma = data['close'].rolling(5).mean()
        long_ma = data['close'].rolling(20).mean()
        # 斜率：用线性回归或简单差分
        short_slope = short_ma.diff(3) / 3  # 近3期变化
        long_slope = long_ma.diff(5) / 5
        # 正负一致性：1表示同向，-1表示反向
        sign_short = np.sign(short_slope)
        sign_long = np.sign(long_slope)
        consistency = sign_short * sign_long  # 1:同向, -1:反向, 0:其中一个为0
        # 调整幅度：当两个斜率绝对值都较大时，信号更强
        magnitude = (short_slope.abs() / short_ma + long_slope.abs() / long_ma) / 2
        magnitude = magnitude / (magnitude.max() + 1e-10)
        result = consistency * magnitude
        return result.clip(-1, 1).fillna(0)
