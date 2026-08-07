"""
ATAS V2 - 波动率类技术指标因子

包含5个波动率类因子
"""
import pandas as pd
import numpy as np
from typing import Dict, Any
from ...factor_base import BaseFactor, VectorizedFactor, FactorMetadata
from ...factor_registry import register_factor


@register_factor()
class BollingerUpperFactor(BaseFactor):
    """布林带上轨"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='bb_upper',
            name='BBUpper',
            display_name='布林带上轨',
            description='20日移动平均 + 2倍标准差',
            category='technical',
            subcategory='volatility',
            lookback_period=20,
            required_data_fields=['close']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        period = self.params.get('period', 20)
        std_dev = self.params.get('std_dev', 2)
        
        ma = data['close'].rolling(window=period).mean()
        std = data['close'].rolling(window=period).std()
        upper = ma + (std * std_dev)
        return upper


@register_factor()
class BollingerLowerFactor(BaseFactor):
    """布林带下轨"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='bb_lower',
            name='BBLower',
            display_name='布林带下轨',
            description='20日移动平均 - 2倍标准差',
            category='technical',
            subcategory='volatility',
            lookback_period=20,
            required_data_fields=['close']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        period = self.params.get('period', 20)
        std_dev = self.params.get('std_dev', 2)
        
        ma = data['close'].rolling(window=period).mean()
        std = data['close'].rolling(window=period).std()
        lower = ma - (std * std_dev)
        return lower


@register_factor()
class BollingerWidthFactor(BaseFactor):
    """布林带宽度"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='bb_width_raw',
            name='BBWidthRaw',
            display_name='布林带宽度',
            description='布林带上下轨之间的距离，衡量波动率',
            category='technical',
            subcategory='volatility',
            lookback_period=20,
            required_data_fields=['close']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        period = self.params.get('period', 20)
        std_dev = self.params.get('std_dev', 2)
        
        ma = data['close'].rolling(window=period).mean()
        std = data['close'].rolling(window=period).std()
        
        upper = ma + (std * std_dev)
        lower = ma - (std * std_dev)
        width = (upper - lower) / ma
        return width


@register_factor()
class KeltnerUpperFactor(BaseFactor):
    """肯特纳通道上轨"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='keltner_upper',
            name='KeltnerUpper',
            display_name='肯特纳通道上轨',
            description='20日EMA + 2倍ATR',
            category='technical',
            subcategory='volatility',
            lookback_period=20,
            required_data_fields=['high', 'low', 'close']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        period = self.params.get('period', 20)
        atr_mult = self.params.get('atr_mult', 2)
        
        ema = data['close'].ewm(span=period, adjust=False).mean()
        
        # 计算ATR
        high_low = data['high'] - data['low']
        high_close = np.abs(data['high'] - data['close'].shift())
        low_close = np.abs(data['low'] - data['close'].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean()
        
        upper = ema + (atr * atr_mult)
        return upper


@register_factor()
class KeltnerLowerFactor(BaseFactor):
    """肯特纳通道下轨"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='keltner_lower',
            name='KeltnerLower',
            display_name='肯特纳通道下轨',
            description='20日EMA - 2倍ATR',
            category='technical',
            subcategory='volatility',
            lookback_period=20,
            required_data_fields=['high', 'low', 'close']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        period = self.params.get('period', 20)
        atr_mult = self.params.get('atr_mult', 2)
        
        ema = data['close'].ewm(span=period, adjust=False).mean()
        
        # 计算ATR
        high_low = data['high'] - data['low']
        high_close = np.abs(data['high'] - data['close'].shift())
        low_close = np.abs(data['low'] - data['close'].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean()
        
        lower = ema - (atr * atr_mult)
        return lower
