"""AI因子: 波动率均值回复 | 置信:70% | 当价格大幅偏离短期均线且波动率处于近期高位时，预测短期反转。使用布林带宽度和价格偏离度的组合，在高波动环境下捕捉过度延伸后的回归。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityMeanReversion(BaseFactor):
    """当价格大幅偏离短期均线且波动率处于近期高位时，预测短期反转。使用布林带宽度和价格偏离度的组合，在高波动环境下捕捉过度延伸后的回归。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_rev",
            name="Volatility Mean Reversion",
            display_name="波动率均值回复",
            description="当价格大幅偏离短期均线且波动率处于近期高位时，预测短期反转。使用布林带宽度和价格偏离度的组合，在高波动环境下捕捉过度延伸后的回归。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # data: DataFrame with columns open, high, low, close, volume
        close = data['close']
        high = data['high']
        low = data['low']
    
        # 计算20周期布林带
        ma = close.rolling(20).mean()
        std = close.rolling(20).std()
        boll_width = (std / ma).fillna(0)  # 波动率
    
        # 价格偏离度: (close - ma) / std, 标准化
        zscore = (close - ma) / std
    
        # 高波动阈值: 选取近期80%分位数
        vol_threshold = boll_width.rolling(60).quantile(0.8).fillna(0)
    
        # 信号: 当|zscore|>2 且 boll_width > vol_threshold 时，反向操作
        signal = -zscore.copy()
        condition = (boll_width > vol_threshold) & (abs(zscore) > 2)
        signal = signal.where(condition, 0)
    
        # 限制到[-1,1]
        result = signal.clip(-1, 1)
        return result
