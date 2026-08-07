"""AI因子: 止损集群触发因子 | 置信:60% | 捕捉价格快速突破近期支撑/阻力位时，因触发大量止损单而加剧的波动反转。通过计算价格与布林带下轨/上轨的距离、成交量激增以及价格加速度变化，给出反向信号。正值表示做多反转（下轨反弹），负值表示做空反转（上轨回落）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class StopLossClusterTrigger(BaseFactor):
    """捕捉价格快速突破近期支撑/阻力位时，因触发大量止损单而加剧的波动反转。通过计算价格与布林带下轨/上轨的距离、成交量激增以及价格加速度变化，给出反向信号。正值表示做多反转（下轨反弹），负值表示做空反转（上轨回落）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_stoploss",
            name="Stop Loss Cluster Trigger",
            display_name="止损集群触发因子",
            description="捕捉价格快速突破近期支撑/阻力位时，因触发大量止损单而加剧的波动反转。通过计算价格与布林带下轨/上轨的距离、成交量激增以及价格加速度变化，给出反向信号。正值表示做多反转（下轨反弹），负值表示做空反转（上轨回落）。",
            category="behavioral",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        volume = data['volume']
        n = 20
    
        # 布林带
        sma = close.rolling(n, min_periods=1).mean()
        std = close.rolling(n, min_periods=1).std() + 1e-10
        upper = sma + 2 * std
        lower = sma - 2 * std
    
        # 价格距离下轨/上轨的比例 (0~1)
        dist_lower = (close - lower) / (sma - lower + 1e-10)  # 下轨附近为0
        dist_upper = (upper - close) / (upper - sma + 1e-10)  # 上轨附近为0
    
        # 成交量放大倍数 (相对过去n日均值)
        vol_ratio = volume / volume.rolling(n, min_periods=1).mean()
    
        # 价格加速度 (3日收益率变化)
        ret1 = close.pct_change(1)
        accel = ret1 - ret1.shift(2)
    
        # 下轨反弹信号: 价格接近下轨(dist_lower<0.1), 成交量放大(>2倍), 加速度由负转正
        long = (dist_lower < 0.15) & (vol_ratio > 1.8) & (accel > 0.01)
        # 上轨回落信号: 价格接近上轨(dist_upper<0.1), 成交量放大, 加速度由正转负
        short = (dist_upper < 0.15) & (vol_ratio > 1.8) & (accel < -0.01)
    
        result = np.where(long, 1.0, np.where(short, -1.0, 0.0))
        return pd.Series(result, index=data.index)
