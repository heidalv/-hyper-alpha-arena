"""AI因子: 持仓超时风险 | 置信:60% | 度量价格在窄幅区间内持续震荡的时间，长窄幅震荡容易导致定时平仓亏损。通过计算价格在布林带内停留的连续柱数以及波动率收缩幅度来生成风险信号。值越接近+1表示超时风险越高（适合做空或离场），越接近-1表示风险低（适合持仓）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class HoldTimeoutRisk(BaseFactor):
    """度量价格在窄幅区间内持续震荡的时间，长窄幅震荡容易导致定时平仓亏损。通过计算价格在布林带内停留的连续柱数以及波动率收缩幅度来生成风险信号。值越接近+1表示超时风险越高（适合做空或离场），越接近-1表示风险低（适合持仓）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_holdtimeout",
            name="Hold Timeout Risk",
            display_name="持仓超时风险",
            description="度量价格在窄幅区间内持续震荡的时间，长窄幅震荡容易导致定时平仓亏损。通过计算价格在布林带内停留的连续柱数以及波动率收缩幅度来生成风险信号。值越接近+1表示超时风险越高（适合做空或离场），越接近-1表示风险低（适合持仓）。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 布林带中轨与带宽
        mid = data['close'].rolling(20).mean()
        std = data['close'].rolling(20).std()
        upper = mid + 2*std
        lower = mid - 2*std
        # 价格在带内
        in_band = (data['close'] >= lower) & (data['close'] <= upper)
        # 连续在带内天数计数
        streak = in_band.groupby((~in_band).cumsum()).cumcount() + 1
        streak = streak * in_band  # 仅在带内时有效
        # 归一化到[-1,1]
        result = (streak / 20.0).clip(0, 1) * 2 - 1
        return result.fillna(0.0) * -1  # 负值表示低风险，正值表示高风险（超时信号）
