"""AI因子: 微观结构脆弱性 | 置信:60% | 利用当日最高最低价差与收盘价位置判断市场脆弱性。计算公式：((high - low) / close) * (1 - 2 * abs(close - (high+low)/2)/(high-low)). 当收盘接近中点且波幅大时值高，结合隔夜跳空或成交量衰减信号。简化：先计算日内振幅相对收盘，再乘以方向性。输出归一化到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Microstructure_Weakness(BaseFactor):
    """利用当日最高最低价差与收盘价位置判断市场脆弱性。计算公式：((high - low) / close) * (1 - 2 * abs(close - (high+low)/2)/(high-low)). 当收盘接近中点且波幅大时值高，结合隔夜跳空或成交量衰减信号。简化：先计算日内振幅相对收盘，再乘以方向性。输出归一化到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_micro_weakness",
            name="Microstructure Weakness",
            display_name="微观结构脆弱性",
            description="利用当日最高最低价差与收盘价位置判断市场脆弱性。计算公式：((high - low) / close) * (1 - 2 * abs(close - (high+low)/2)/(high-low)). 当收盘接近中点且波幅大时值高，结合隔夜跳空或成交量衰减信号。简化：先计算日内振幅相对收盘，再乘以方向性。输出归一化到[-1,1]。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        range_ratio = (data['high'] - data['low']) / data['close']
        midpoint = (data['high'] + data['low']) / 2
        position = (data['close'] - midpoint) / (data['high'] - data['low'] + 1e-10)
        # 当收盘远离中心且波幅大时，可能趋势强；反之中心附近波幅大时脆弱
        weakness = range_ratio * (1 - 2 * abs(position))
        # 使用z-score归一化到[-1,1]
        mean = weakness.rolling(50).mean()
        std = weakness.rolling(50).std()
        result = (weakness - mean) / (std + 1e-10)
        result = result.clip(-1, 1)
        return result.fillna(0)
