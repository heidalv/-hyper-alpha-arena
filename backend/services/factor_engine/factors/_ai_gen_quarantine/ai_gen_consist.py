"""AI因子: 多周期动量一致性 | 置信:60% | 检查短期(5日)、中期(20日)、长期(60日)简单移动平均线的方向是否一致。三者均向上则+1，均向下则+1（因方向一致），否则-1。用于识别趋势明确的已知状态。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Multi_Timeframe_Momentum_Consistency(BaseFactor):
    """检查短期(5日)、中期(20日)、长期(60日)简单移动平均线的方向是否一致。三者均向上则+1，均向下则+1（因方向一致），否则-1。用于识别趋势明确的已知状态。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_consist",
            name="Multi-Timeframe Momentum Consistency",
            display_name="多周期动量一致性",
            description="检查短期(5日)、中期(20日)、长期(60日)简单移动平均线的方向是否一致。三者均向上则+1，均向下则+1（因方向一致），否则-1。用于识别趋势明确的已知状态。",
            category="composite",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        close = data['close']
        ma5 = close.rolling(window=5).mean()
        ma20 = close.rolling(window=20).mean()
        ma60 = close.rolling(window=60).mean()

        # 判断各均线方向（上升=1, 下降=-1）
        up5 = (ma5 > ma5.shift(1)).astype(int) * 2 - 1
        up20 = (ma20 > ma20.shift(1)).astype(int) * 2 - 1
        up60 = (ma60 > ma60.shift(1)).astype(int) * 2 - 1

        # 一致时输出1，否则-1
        consistency = np.where((up5 == up20) & (up20 == up60), 1.0, -1.0)
        return pd.Series(consistency, index=data.index).fillna(0.0)
