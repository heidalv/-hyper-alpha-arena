"""AI因子: 流动性冲击 | 置信:60% | 结合成交量萎缩与价格跳空幅度衡量流动性风险。当成交量相对近期均值大幅下降且同时出现较大价格波动时，认为流动性不足，给出负向信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class LiquidityShock(BaseFactor):
    """结合成交量萎缩与价格跳空幅度衡量流动性风险。当成交量相对近期均值大幅下降且同时出现较大价格波动时，认为流动性不足，给出负向信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_liq_shock",
            name="Liquidity Shock",
            display_name="流动性冲击",
            description="结合成交量萎缩与价格跳空幅度衡量流动性风险。当成交量相对近期均值大幅下降且同时出现较大价格波动时，认为流动性不足，给出负向信号。",
            category="behavioral",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 成交量相对萎缩指标：当前成交量 / 20日均量 -1
        vol_ratio = data['volume'] / data['volume'].rolling(20).mean() - 1
        # 价格波动（日内振幅）
        amp = (data['high'] - data['low']) / data['close'].shift(1)  # 相对前一收盘价的振幅
        # 流动性冲击分数：当量萎缩且振幅大时负值大
        # 将vol_ratio取负（萎缩为正），并与振幅相乘
        shock = -vol_ratio * amp  # 萎缩且振幅大 -> 正数
        # 滚动归一化
        roll_mean = shock.rolling(20).mean()
        roll_std = shock.rolling(20).std()
        z = (shock - roll_mean) / (roll_std + 1e-10)
        result = -np.tanh(z.clip(-3, 3))
        return result.fillna(0)
