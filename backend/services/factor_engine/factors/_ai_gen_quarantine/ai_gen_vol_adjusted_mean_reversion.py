"""AI因子: 波动率调整均值回复 | 置信:70% | 使用指数加权移动平均计算偏离度，并用ATR归一化，再乘以波动率变化方向，识别在高波动环境下被过度拉伸的价格回归机会。旨在避免regime=unknown时的逆势陷阱。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityAdjustedMeanReversion(BaseFactor):
    """使用指数加权移动平均计算偏离度，并用ATR归一化，再乘以波动率变化方向，识别在高波动环境下被过度拉伸的价格回归机会。旨在避免regime=unknown时的逆势陷阱。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_adjusted_mean_reversion",
            name="Volatility-Adjusted Mean Reversion",
            display_name="波动率调整均值回复",
            description="使用指数加权移动平均计算偏离度，并用ATR归一化，再乘以波动率变化方向，识别在高波动环境下被过度拉伸的价格回归机会。旨在避免regime=unknown时的逆势陷阱。",
            category="mean_reversion",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        close = data['close']
        high = data['high']
        low = data['low']
        volume = data['volume']
    
        # 计算ATR（14周期）
        tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.ewm(span=14, adjust=False).mean()
    
        # 计算价格偏离度：价格偏离其50周期指数移动均线的百分比
        ema50 = close.ewm(span=50, adjust=False).mean()
        deviation = (close - ema50) / ema50
    
        # 用ATR归一化偏离度，并限制范围
        normalized_dev = deviation / (atr / close)  # 相对ATR的倍数
        normalized_dev = normalized_dev.clip(-4, 4)  # 极端值截断
    
        # 波动率变化方向：近期ATR变化率
        atr_change = atr.pct_change(5)
        # 高波动增加时强化均值回复信号（负向），低波动时减弱
        vol_factor = atr_change.fillna(0).clip(-0.5, 0.5)
    
        # 组合：偏离度负值 → 做多信号（+1），正值→做空信号（-1），波动率放大信号强度
        signal = -normalized_dev * (1 + vol_factor)
        # 映射到[-1,1]
        result = signal / 4.0  # 因为normalized_dev最大4，所以信号范围[-1,1]左右
        result = result.clip(-1, 1)
        return result
