"""AI因子: 趋势弱势因子 | 置信:65% | 衡量当前价格相对于长期均线的位置和动量方向，当价格低于均线且短期均线斜率向下时，表示趋势弱势。用于识别不适合做多的市场状态，避免在未知regime下追涨。通过比较收盘价与20日均线、以及20日均线斜率来生成信号，归一化至[-1,1]区间。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Trend_Weakness_Indicator(BaseFactor):
    """衡量当前价格相对于长期均线的位置和动量方向，当价格低于均线且短期均线斜率向下时，表示趋势弱势。用于识别不适合做多的市场状态，避免在未知regime下追涨。通过比较收盘价与20日均线、以及20日均线斜率来生成信号，归一化至[-1,1]区间。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_trend_weakness",
            name="Trend Weakness Indicator",
            display_name="趋势弱势因子",
            description="衡量当前价格相对于长期均线的位置和动量方向，当价格低于均线且短期均线斜率向下时，表示趋势弱势。用于识别不适合做多的市场状态，避免在未知regime下追涨。通过比较收盘价与20日均线、以及20日均线斜率来生成信号，归一化至[-1,1]区间。",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        df = data.copy()
        close = df['close']
        ma20 = close.rolling(20).mean()
        # 价格偏离度
        price_dev = (close - ma20) / ma20
        # 20日均线斜率（单位：变化率）
        ma20_slope = ma20.diff(5) / ma20.shift(5)  # 5周期斜率
        # 合成信号：价格低于均线且斜率向下则为负值
        raw = - (price_dev.clip(-0.1, 0.1) * 10 + ma20_slope.clip(-0.05, 0.05) * 20) / 2
        # 归一化到[-1,1]
        result = raw.clip(-1, 1)
        return result
