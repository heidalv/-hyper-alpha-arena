"""AI因子: 市场状态不确定性 | 置信:60% | 衡量多个时间周期趋势方向的不一致性以及波动率的突然变化，当长期和短期趋势相悖且波动率增大时表明市场处于未知/混乱状态，容易导致各类止损。值接近+1表示高度不确定性（应避免交易或采取缩小仓位），接近-1表示确定性高（趋势一致）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RegimeUncertainty(BaseFactor):
    """衡量多个时间周期趋势方向的不一致性以及波动率的突然变化，当长期和短期趋势相悖且波动率增大时表明市场处于未知/混乱状态，容易导致各类止损。值接近+1表示高度不确定性（应避免交易或采取缩小仓位），接近-1表示确定性高（趋势一致）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regimeuncertainty",
            name="Regime Uncertainty",
            display_name="市场状态不确定性",
            description="衡量多个时间周期趋势方向的不一致性以及波动率的突然变化，当长期和短期趋势相悖且波动率增大时表明市场处于未知/混乱状态，容易导致各类止损。值接近+1表示高度不确定性（应避免交易或采取缩小仓位），接近-1表示确定性高（趋势一致）。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 短期趋势（5日均线斜率）
        short_ma = data['close'].rolling(5).mean()
        short_slope = short_ma.diff(3) / short_ma.shift(3)
        # 长期趋势（20日均线斜率）
        long_ma = data['close'].rolling(20).mean()
        long_slope = long_ma.diff(5) / long_ma.shift(5)
        # 趋势方向一致性：正负号不同则不一致
        sign_short = np.sign(short_slope)
        sign_long = np.sign(long_slope)
        conflict = (sign_short != sign_long).astype(float)
        # 波动率变化（20日波动率相对于5日波动率的比率）
        vol_short = data['close'].pct_change().rolling(5).std()
        vol_long = data['close'].pct_change().rolling(20).std()
        vol_ratio = vol_long / (vol_short + 1e-10)
        # 结合：冲突且波动率放大则不确定性高
        uncertainty = conflict * (vol_ratio > 1.5).astype(float)
        result = uncertainty * 2 - 1  # 映射到[-1,1]
        return result.fillna(0.0)
