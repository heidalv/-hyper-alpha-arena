"""AI因子: 抛压指数因子 | 置信:60% | 结合日内价格位置（收盘相对低点的偏向）与成交量相比均量的变化，衡量持续的抛售压力。高正值表示抛压轻（买盘支撑），适合做多；低负值表示抛压沉重，对应于亏损模式中的逆势持仓风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Selling_Pressure_Index(BaseFactor):
    """结合日内价格位置（收盘相对低点的偏向）与成交量相比均量的变化，衡量持续的抛售压力。高正值表示抛压轻（买盘支撑），适合做多；低负值表示抛压沉重，对应于亏损模式中的逆势持仓风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pres_idx",
            name="Selling Pressure Index",
            display_name="抛压指数因子",
            description="结合日内价格位置（收盘相对低点的偏向）与成交量相比均量的变化，衡量持续的抛售压力。高正值表示抛压轻（买盘支撑），适合做多；低负值表示抛压沉重，对应于亏损模式中的逆势持仓风险。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        volume = data['volume']

        # 日内价格位置：0~1，1表示收盘在最低（抛压大）
        daily_pos = 1 - (close - low) / (high - low + 1e-8)

        # 成交量相对20日均量
        avg_vol = volume.rolling(20).mean()
        vol_ratio = volume / avg_vol

        # 抛压指标：价格低位且放量时负值大
        raw = (0.5 - daily_pos) * vol_ratio
        # 平滑并归一化
        smoothed = raw.rolling(5).mean()
        clipped = np.clip(smoothed, -2, 2)
        result = clipped / 2.0
        result = result.fillna(0)
        return result
