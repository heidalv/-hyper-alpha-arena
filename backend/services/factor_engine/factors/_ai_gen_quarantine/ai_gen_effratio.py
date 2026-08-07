"""AI因子: 价格效率比 | 置信:65% | 基于Kaufman自适应移动平均的效率比，衡量价格方向性与噪音的比例。低效率比（接近0）表明价格在窄幅震荡或频繁反转，不适合趋势跟踪策略；高效率比（接近1）表明趋势明确。返回[-1,1]，实际映射为2*效率比-1，低效率对应负值，提示未知状态风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class EfficiencyRatioKaufman(BaseFactor):
    """基于Kaufman自适应移动平均的效率比，衡量价格方向性与噪音的比例。低效率比（接近0）表明价格在窄幅震荡或频繁反转，不适合趋势跟踪策略；高效率比（接近1）表明趋势明确。返回[-1,1]，实际映射为2*效率比-1，低效率对应负值，提示未知状态风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_effratio",
            name="Efficiency Ratio (Kaufman)",
            display_name="价格效率比",
            description="基于Kaufman自适应移动平均的效率比，衡量价格方向性与噪音的比例。低效率比（接近0）表明价格在窄幅震荡或频繁反转，不适合趋势跟踪策略；高效率比（接近1）表明趋势明确。返回[-1,1]，实际映射为2*效率比-1，低效率对应负值，提示未知状态风险。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        period = 10
        change = data['close'].diff(period).abs()
        volatility = data['close'].diff().abs().rolling(period).sum()
        er = change / (volatility + 1e-10)
        result = 2 * er - 1
        return result.fillna(0)
