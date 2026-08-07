"""
ATAS V2 - 资金流向类基本面因子

包含5个资金流向相关因子
"""
import pandas as pd
import numpy as np
from typing import Dict, Any
from ...factor_base import BaseFactor, VectorizedFactor, FactorMetadata
from ...factor_registry import register_factor


@register_factor()
class MoneyFlowIndexFactor(BaseFactor):
    """资金流量指标MFI"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='mfi_14',
            name='MFI14',
            display_name='14日资金流量指标',
            description='结合价格和成交量的动量指标',
            category='fundamental',
            subcategory='flow',
            lookback_period=14,
            required_data_fields=['high', 'low', 'close', 'volume']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        period = self.params.get('period', 14)
        
        # 典型价格
        tp = (data['high'] + data['low'] + data['close']) / 3
        # 原始资金流量
        rmf = tp * data['volume']
        
        # 正负资金流量
        positive_flow = rmf.where(tp > tp.shift(1), 0)
        negative_flow = rmf.where(tp < tp.shift(1), 0)
        
        # 资金流量比率
        positive_sum = positive_flow.rolling(window=period).sum()
        negative_sum = negative_flow.rolling(window=period).sum()
        
        mfr = positive_sum / negative_sum
        mfi = 100 - (100 / (1 + mfr))
        
        return mfi


@register_factor()
class AccumulationDistributionFactor(BaseFactor):
    """累积派发线"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='ad_line',
            name='ADLine',
            display_name='累积派发线',
            description='资金流向累积指标',
            category='fundamental',
            subcategory='flow',
            lookback_period=1,
            required_data_fields=['high', 'low', 'close', 'volume']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        # 资金流量乘数
        mfm = ((data['close'] - data['low']) - (data['high'] - data['close'])) / (data['high'] - data['low'])
        mfm = mfm.fillna(0)
        
        # 资金流量成交量
        mfv = mfm * data['volume']
        
        # 累积
        ad = mfv.cumsum()
        return ad


@register_factor()
class BuyVolumeFactor(BaseFactor):
    """买入成交量估算"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='buy_volume',
            name='BuyVolume',
            display_name='买入成交量',
            description='根据价格变化估算买入成交量',
            category='fundamental',
            subcategory='flow',
            lookback_period=1,
            required_data_fields=['open', 'close', 'volume']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        # 简化估算：价格上涨视为买入主导
        price_change = data['close'] - data['open']
        buy_ratio = (price_change / (data['close'] + 1e-10)).clip(-1, 1)
        buy_ratio = (buy_ratio + 1) / 2  # 归一化到[0,1]
        return data['volume'] * buy_ratio


@register_factor()
class SellVolumeFactor(BaseFactor):
    """卖出成交量估算"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='sell_volume',
            name='SellVolume',
            display_name='卖出成交量',
            description='根据价格变化估算卖出成交量',
            category='fundamental',
            subcategory='flow',
            lookback_period=1,
            required_data_fields=['open', 'close', 'volume']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        # 简化估算：价格下跌视为卖出主导
        price_change = data['close'] - data['open']
        sell_ratio = (-price_change / (data['close'] + 1e-10)).clip(-1, 1)
        sell_ratio = (sell_ratio + 1) / 2  # 归一化到[0,1]
        return data['volume'] * sell_ratio


@register_factor()
class BuySellRatioFactor(BaseFactor):
    """买卖比率"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='buy_sell_ratio',
            name='BuySellRatio',
            display_name='买卖比率',
            description='买入成交量与卖出成交量的比率',
            category='fundamental',
            subcategory='flow',
            lookback_period=1,
            required_data_fields=['open', 'close', 'volume']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        price_change = data['close'] - data['open']
        buy_ratio = ((price_change / (data['close'] + 1e-10)).clip(-1, 1) + 1) / 2
        sell_ratio = 1 - buy_ratio
        
        buy_vol = data['volume'] * buy_ratio
        sell_vol = data['volume'] * sell_ratio
        
        return buy_vol / (sell_vol + 1e-10)
