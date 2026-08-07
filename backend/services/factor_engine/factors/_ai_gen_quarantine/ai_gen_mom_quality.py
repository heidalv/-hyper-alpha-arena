"""AI因子: 动量质量因子 | 置信:60% | 标准化动量因子：计算过去N日收益率除以其间平均真实波幅（ATR），衡量动量的显著性。当收益显著超过波动时（强趋势），输出接近±1；当收益被波动淹没时（噪声市），输出接近0。该因子能够过滤掉低信噪比的市场环境，避免在regime=unknown时入场。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Momentum_Quality_Factor(BaseFactor):
    """标准化动量因子：计算过去N日收益率除以其间平均真实波幅（ATR），衡量动量的显著性。当收益显著超过波动时（强趋势），输出接近±1；当收益被波动淹没时（噪声市），输出接近0。该因子能够过滤掉低信噪比的市场环境，避免在regime=unknown时入场。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_quality",
            name="Momentum Quality Factor",
            display_name="动量质量因子",
            description="标准化动量因子：计算过去N日收益率除以其间平均真实波幅（ATR），衡量动量的显著性。当收益显著超过波动时（强趋势），输出接近±1；当收益被波动淹没时（噪声市），输出接近0。该因子能够过滤掉低信噪比的市场环境，避免在regime=unknown时入场。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        high = data['high']
        low = data['low']
        volume = data['volume']

        period = 20  # 计算周期
        # 计算收益率
        ret = close.pct_change(period)
        # 计算ATR
        prev_close = close.shift(1)
        tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        atr = tr.rolling(period).mean()
        # 避免除零
        atr = atr.replace(0, close * 0.0001)  # 极小值
        # 标准化动量 = 收益率 / ATR（相对价格水平），再缩放
        norm_mom = ret / (atr / close.shift(period))  # 收益率除以ATR相对价格比例
        # 截断到[-1,1]范围
        result = norm_mom.clip(-1, 1)
        # 处理NaN
        result = result.fillna(method='ffill').fillna(0)
        return result
