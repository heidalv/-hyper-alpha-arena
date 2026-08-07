"""
ATAS V2 - 价格类基本面因子

包含5个价格相关因子
"""
import pandas as pd
import numpy as np
from typing import Dict, Any
from ...factor_base import BaseFactor, VectorizedFactor, FactorMetadata
from ...factor_registry import register_factor


@register_factor()
class PriceReturns1DFactor(VectorizedFactor):
    """1日收益率"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='returns_1d',
            name='Returns1D',
            display_name='1日收益率',
            description='当日收益率',
            category='fundamental',
            subcategory='price',
            lookback_period=1,
            required_data_fields=['close']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        return data['close'].pct_change()


@register_factor()
class PriceReturns7DFactor(VectorizedFactor):
    """7日收益率"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='returns_7d',
            name='Returns7D',
            display_name='7日收益率',
            description='7日累计收益率',
            category='fundamental',
            subcategory='price',
            lookback_period=7,
            required_data_fields=['close']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        return data['close'].pct_change(periods=7)


@register_factor()
class PriceReturns30DFactor(VectorizedFactor):
    """30日收益率"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='returns_30d',
            name='Returns30D',
            display_name='30日收益率',
            description='30日累计收益率',
            category='fundamental',
            subcategory='price',
            lookback_period=30,
            required_data_fields=['close']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        return data['close'].pct_change(periods=30)


@register_factor()
class HighLowRatioFactor(BaseFactor):
    """最高最低价比率"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='high_low_ratio',
            name='HighLowRatio',
            display_name='最高最低价比率',
            description='当日最高价与最低价的比率，衡量日内波动',
            category='fundamental',
            subcategory='price',
            lookback_period=1,
            required_data_fields=['high', 'low']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        return data['high'] / data['low']


@register_factor()
class PriceToMA20Factor(BaseFactor):
    """价格相对20日均线"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='price_to_ma20',
            name='PriceToMA20',
            display_name='价格相对MA20',
            description='当前价格与20日均线的比率，衡量偏离程度',
            category='fundamental',
            subcategory='price',
            lookback_period=20,
            required_data_fields=['close']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        ma20 = data['close'].rolling(window=20).mean()
        return data['close'] / ma20
