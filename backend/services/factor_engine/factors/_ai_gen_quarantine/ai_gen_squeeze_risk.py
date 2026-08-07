"""AI因子: 空头挤压风险因子 | 置信:60% | 识别潜在的短期轧空风险。当价格从低点快速反弹且成交量显著放大时，做空风险高。计算当前价格相对过去N日最低点的涨幅，并乘以成交量比率。值越大表示做空风险越高（看涨信号）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ShortSqueezeRiskIndicator(BaseFactor):
    """识别潜在的短期轧空风险。当价格从低点快速反弹且成交量显著放大时，做空风险高。计算当前价格相对过去N日最低点的涨幅，并乘以成交量比率。值越大表示做空风险越高（看涨信号）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_squeeze_risk",
            name="Short Squeeze Risk Indicator",
            display_name="空头挤压风险因子",
            description="识别潜在的短期轧空风险。当价格从低点快速反弹且成交量显著放大时，做空风险高。计算当前价格相对过去N日最低点的涨幅，并乘以成交量比率。值越大表示做空风险越高（看涨信号）。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        # 参数
        lookback = 20
        # 计算近期最低价
        low_min = data['low'].rolling(lookback).min()
        # 价格反弹幅度
        bounce = (data['close'] - low_min) / (low_min + 1e-8)
        # 成交量比率：当前成交量与过去均值的比值
        vol_avg = data['volume'].rolling(lookback).mean()
        vol_ratio = data['volume'] / (vol_avg + 1e-8)
        # 组合信号，限制在[-1,1]
        raw = bounce * vol_ratio
        # 归一化到[-1,1] (使用tanh或clip)
        raw = raw / (raw.abs().max() + 1e-8)
        result = np.tanh(raw * 0.5)  # 平滑
        return result
