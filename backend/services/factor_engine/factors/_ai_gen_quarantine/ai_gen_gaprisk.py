"""AI因子: 跳空风险指标 | 置信:60% | 基于开盘价相对于前收盘的跳空幅度和日内波动率，测量当前交易日的潜在跳空风险，在不确定环境下降低持仓意愿。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class GapRiskIndicator(BaseFactor):
    """基于开盘价相对于前收盘的跳空幅度和日内波动率，测量当前交易日的潜在跳空风险，在不确定环境下降低持仓意愿。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_gaprisk",
            name="Gap Risk Indicator",
            display_name="跳空风险指标",
            description="基于开盘价相对于前收盘的跳空幅度和日内波动率，测量当前交易日的潜在跳空风险，在不确定环境下降低持仓意愿。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        open_p = data['open']
        close = data['close']
        high = data['high']
        low = data['low']
        # 跳空幅度：开盘相对于前收盘
        gap = (open_p - close.shift(1)) / close.shift(1)
        # 日内振幅
        intra_range = (high - low) / close.shift(1)
        # 结合两者：跳空大且日内振幅小时，不确定性高
        gap_abs = np.abs(gap)
        # 归一化：过去20日均值
        gap_abs_ma = gap_abs.rolling(20).mean().replace(0, np.nan)
        intra_ma = intra_range.rolling(20).mean().replace(0, np.nan)
        # 信号：跳空幅度与日内振幅的比值，若比值过高视为高风险
        risk_ratio = gap_abs / intra_ma
        # 映射到[-1,0]：高风险负值，低风险0
        result = -np.clip(risk_ratio / 3.0, 0, 1)
        # 处理NaN
        result = result.fillna(0.0)
        return result
