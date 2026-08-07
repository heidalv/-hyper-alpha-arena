"""AI因子: 趋势完整性失效指标 | 置信:55% | 检测价格在短期趋势方向上的动量衰减和反转风险。通过比较短期均线与长期均线的斜率一致性，以及价格与均线的偏离度。当短期均线斜率与长期均线斜率背离且价格出现反向穿透时，因子输出负值，预示趋势可能结束而产生止损亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendIntegrityFailureIndicator(BaseFactor):
    """检测价格在短期趋势方向上的动量衰减和反转风险。通过比较短期均线与长期均线的斜率一致性，以及价格与均线的偏离度。当短期均线斜率与长期均线斜率背离且价格出现反向穿透时，因子输出负值，预示趋势可能结束而产生止损亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_integrity",
            name="Trend Integrity Failure Indicator",
            display_name="趋势完整性失效指标",
            description="检测价格在短期趋势方向上的动量衰减和反转风险。通过比较短期均线与长期均线的斜率一致性，以及价格与均线的偏离度。当短期均线斜率与长期均线斜率背离且价格出现反向穿透时，因子输出负值，预示趋势可能结束而产生止损亏损。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        ma_fast = close.rolling(5).mean()
        ma_slow = close.rolling(20).mean()
        # 斜率: 当前值相对于N期前的差值
        slope_fast = ma_fast - ma_fast.shift(3)
        slope_slow = ma_slow - ma_slow.shift(3)
        # 斜率方向一致性: 两者同号为正，异号为负
        slope_sign = np.sign(slope_fast) * np.sign(slope_slow)
        # 价格偏离度: 当前价格相对于ma_fast的百分比
        deviation = (close - ma_fast) / ma_fast
        # 信号: 斜率异号且偏离过大（>2%）时趋势脆弱
        fragile = (slope_sign < 0) & (abs(deviation) > 0.02)
        signal = -fragile.astype(float)
        # 使用绝对值归一化
        result = signal * 1.0
        return result.fillna(0).clip(-1, 1)
