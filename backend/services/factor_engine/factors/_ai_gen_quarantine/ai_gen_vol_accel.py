"""AI因子: 波动率加速度 | 置信:60% | 衡量波动率变化的速度，捕捉突发剧烈波动。计算短期波动率（5周期）相对中期波动率（20周期）的变化率，并结合价格方向做惩罚。高加速度且与趋势反向时发出信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Acceleration(BaseFactor):
    """衡量波动率变化的速度，捕捉突发剧烈波动。计算短期波动率（5周期）相对中期波动率（20周期）的变化率，并结合价格方向做惩罚。高加速度且与趋势反向时发出信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_accel",
            name="Volatility Acceleration",
            display_name="波动率加速度",
            description="衡量波动率变化的速度，捕捉突发剧烈波动。计算短期波动率（5周期）相对中期波动率（20周期）的变化率，并结合价格方向做惩罚。高加速度且与趋势反向时发出信号。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        high = data['high']
        low = data['low']
        close = data['close']
        # 计算真实波幅
        tr = pd.concat([high - low, abs(high - close.shift(1)), abs(low - close.shift(1))], axis=1).max(axis=1)
        # 短期和中期波动率（标准差）
        vol_short = tr.rolling(5).std()
        vol_med = tr.rolling(20).std()
        # 波动率变化率
        vol_change = (vol_short - vol_med) / vol_med.replace(0, np.nan)
        # 价格方向（短期动量）
        momentum = close.pct_change(5) * 100
        # 如果波动率急剧上升且价格与先前趋势相反，则负向信号
        trend = close.rolling(10).mean().diff(5)
        signal = -np.sign(momentum) * vol_change
        # 用tanh映射到[-1,1]
        result = np.tanh(signal * 2)
        return pd.Series(result, index=data.index).fillna(0)
