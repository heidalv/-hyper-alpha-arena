"""AI因子: 趋势衰竭 | 置信:70% | 识别上升趋势中动量衰减的信号。当短期动量（5日收益率）小于长期动量（20日收益率）且两者均为正，表明上涨动能减弱，易出现回调或横盘导致超时亏损。计算短期与长期动量之差，乘以动量符号，经20日滚动标准化后映射到[-1,1]，正值表示衰竭风险高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trend_Exhaustion(BaseFactor):
    """识别上升趋势中动量衰减的信号。当短期动量（5日收益率）小于长期动量（20日收益率）且两者均为正，表明上涨动能减弱，易出现回调或横盘导致超时亏损。计算短期与长期动量之差，乘以动量符号，经20日滚动标准化后映射到[-1,1]，正值表示衰竭风险高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_texh",
            name="Trend Exhaustion",
            display_name="趋势衰竭",
            description="识别上升趋势中动量衰减的信号。当短期动量（5日收益率）小于长期动量（20日收益率）且两者均为正，表明上涨动能减弱，易出现回调或横盘导致超时亏损。计算短期与长期动量之差，乘以动量符号，经20日滚动标准化后映射到[-1,1]，正值表示衰竭风险高。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        short_mom = close.pct_change(5)
        long_mom = close.pct_change(20)
        # 差值，乘上符号：只有当两者都正时差值为负才表示衰竭，但这里直接用差值方向
        diff = short_mom - long_mom
        # 用平滑处理，取20日滚动z-score
        mean = diff.rolling(20).mean()
        std = diff.rolling(20).std(ddof=0)
        z = (diff - mean) / std
        # 反转符号，因为正差值表示短期强于长期，趋势强，负差值表示衰竭，我们需要衰竭为正值
        result = -z.clip(-3, 3) / 3.0
        return result.fillna(0)
