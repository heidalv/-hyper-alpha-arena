"""AI因子: 市场状态不确定性 | 置信:60% | 检测市场处于“未知状态”（regime=unknown）的特征，通常表现为低波动率、成交量不活跃、价格窄幅震荡等。该因子在不确定性高时输出接近0的值，避免开仓；在趋势明确时输出方向信号。正值看多，负值看空。通过过滤掉不确定性高的时段，减少实盘亏损模式中的无效交易。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MarketUncertaintyRegime(BaseFactor):
    """检测市场处于“未知状态”（regime=unknown）的特征，通常表现为低波动率、成交量不活跃、价格窄幅震荡等。该因子在不确定性高时输出接近0的值，避免开仓；在趋势明确时输出方向信号。正值看多，负值看空。通过过滤掉不确定性高的时段，减少实盘亏损模式中的无效交易。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mur",
            name="Market Uncertainty Regime",
            display_name="市场状态不确定性",
            description="检测市场处于“未知状态”（regime=unknown）的特征，通常表现为低波动率、成交量不活跃、价格窄幅震荡等。该因子在不确定性高时输出接近0的值，避免开仓；在趋势明确时输出方向信号。正值看多，负值看空。通过过滤掉不确定性高的时段，减少实盘亏损模式中的无效交易。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 参数
        volatility_window = 10
        volume_window = 20
        atr_threshold = 0.005  # ATR相对价格比例阈值
        vol_ratio_thresh = 0.5
    
        close = data['close']
        high = data['high']
        low = data['low']
        volume = data['volume']
    
        # 真实波幅ATR
        tr = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        atr = tr.rolling(volatility_window).mean()
        atr_pct = atr / close  # 相对ATR
    
        # 成交量相对均值
        vol_ma = volume.rolling(volume_window).mean()
        vol_ratio = volume / vol_ma
    
        # 价格动量（短期趋势强度）
        momentum = close.pct_change(5)
    
        # 不确定性条件：低波动 + 低成交量 + 低动量
        low_vol = atr_pct < atr_threshold
        low_vol_vol = vol_ratio < vol_ratio_thresh
        low_mom = np.abs(momentum) < 0.01
    
        # 综合不确定性分数（0~1）
        uncertainty = (low_vol.astype(int) + low_vol_vol.astype(int) + low_mom.astype(int)) / 3.0
    
        # 趋势方向信号：用均线交叉或动量
        ma_fast = close.rolling(10).mean()
        ma_slow = close.rolling(30).mean()
        trend_signal = np.sign(ma_fast - ma_slow)  # 正值看多，负值看空
    
        # 最终因子：趋势信号乘以（1-不确定性），使不确定性高时信号趋近0
        factor = trend_signal * (1 - uncertainty)
        # 限制在[-1,1]
        factor = np.clip(factor, -1, 1)
        return factor
