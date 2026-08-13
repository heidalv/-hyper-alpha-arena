"""AI因子: 多周期趋势冲突 | 置信:65% | 不同时间框架趋势方向不一致时，交易容易反复亏损。该因子计算短期(5日)与长期(30日)移动平均线的斜率，当斜率相反时输出负值，表示高风险震荡环境。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MultiTimeframeTrendConflict(BaseFactor):
    """不同时间框架趋势方向不一致时，交易容易反复亏损。该因子计算短期(5日)与长期(30日)移动平均线的斜率，当斜率相反时输出负值，表示高风险震荡环境。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_conflict",
            name="Multi-Timeframe Trend Conflict",
            display_name="多周期趋势冲突",
            description="不同时间框架趋势方向不一致时，交易容易反复亏损。该因子计算短期(5日)与长期(30日)移动平均线的斜率，当斜率相反时输出负值，表示高风险震荡环境。",
            category="behavioral",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        close = data['close']
        # 短期斜率和长期斜率
        ma5 = close.rolling(5).mean()
        ma30 = close.rolling(30).mean()
        # 斜率用差分/当前值
        slope5 = (ma5 - ma5.shift(5)) / ma5.shift(5)
        slope30 = (ma30 - ma30.shift(5)) / ma30.shift(5)
        # 冲突度量：符号相反则风险高
        conflict = - np.sign(slope5) * np.sign(slope30)  # 同号时为1或-1? 实际同号得+/-1，异号得0
        # 我们需要将冲突程度映射到[-1,1]，正数表示同向趋势(安全)，负数表示冲突(危险)
        # 同时考虑斜率强度
        strength = (np.abs(slope5) + np.abs(slope30)) / 2
        # 冲突时乘以强度
        result = -conflict * strength.clip(0, 0.1) / 0.1
        # 当conflict=0(异号)时，result=0但实际应为负，修正
        mask = (np.sign(slope5) * np.sign(slope30)) < 0
        result[mask] = -strength[mask].clip(0, 0.1) / 0.1
        # 未定义时置0
        result = result.fillna(0).clip(-1, 1)
        return result
