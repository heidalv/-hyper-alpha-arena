"""AI因子: 成交量流动性状态 | 置信:60% | 结合成交量比率和价格波动率，识别低流动性窄幅震荡的未知市场状态。低流动性时输出负值，正常流动性时输出正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volume_Liquidity_Regime(BaseFactor):
    """结合成交量比率和价格波动率，识别低流动性窄幅震荡的未知市场状态。低流动性时输出负值，正常流动性时输出正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volume_liquidity",
            name="Volume Liquidity Regime",
            display_name="成交量流动性状态",
            description="结合成交量比率和价格波动率，识别低流动性窄幅震荡的未知市场状态。低流动性时输出负值，正常流动性时输出正值。",
            category="composite",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        close = data['close']
        volume = data['volume']
        high = data['high']
        low = data['low']
        # 成交量相对均值比率
        vol_ma = volume.rolling(20).mean()
        vol_ratio = volume / (vol_ma + 1e-10)
        # 价格波动率（每日振幅/收盘价）
        daily_range = (high - low) / close
        range_ma = daily_range.rolling(20).mean()
        # 低流动性条件：成交量低且振幅小
        liquidity_score = (vol_ratio - 1) * (daily_range / range_ma - 1)
        # 归一化
        z = liquidity_score.rolling(60).mean() / (liquidity_score.rolling(60).std() + 1e-10)
        result = np.tanh(z)
        return result.fillna(0)
