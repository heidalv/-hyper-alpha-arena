"""
ATAS V2 - 趋势类技术指标因子

包含11个趋势类因子
"""
import pandas as pd
import numpy as np
from typing import Dict, Any
from ...factor_base import BaseFactor, VectorizedFactor, FactorMetadata
from ...factor_registry import register_factor


@register_factor()
class SMA5Factor(VectorizedFactor):
    """5日简单移动平均线"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='sma_5',
            name='SMA5',
            display_name='5日简单移动平均',
            description='5日收盘价简单移动平均',
            category='technical',
            subcategory='trend',
            lookback_period=5,
            required_data_fields=['close']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        period = self.params.get('period', 5)
        return data['close'].rolling(window=period).mean()


@register_factor()
class SMA10Factor(VectorizedFactor):
    """10日简单移动平均线"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='sma_10',
            name='SMA10',
            display_name='10日简单移动平均',
            description='10日收盘价简单移动平均',
            category='technical',
            subcategory='trend',
            lookback_period=10,
            required_data_fields=['close']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        period = self.params.get('period', 10)
        return data['close'].rolling(window=period).mean()


@register_factor()
class SMA30Factor(VectorizedFactor):
    """30日简单移动平均线"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='sma_30',
            name='SMA30',
            display_name='30日简单移动平均',
            description='30日收盘价简单移动平均',
            category='technical',
            subcategory='trend',
            lookback_period=30,
            required_data_fields=['close']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        period = self.params.get('period', 30)
        return data['close'].rolling(window=period).mean()


@register_factor()
class EMA12Factor(VectorizedFactor):
    """12日指数移动平均线"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='ema_12',
            name='EMA12',
            display_name='12日指数移动平均',
            description='12日收盘价指数移动平均',
            category='technical',
            subcategory='trend',
            lookback_period=12,
            required_data_fields=['close']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        period = self.params.get('period', 12)
        return data['close'].ewm(span=period, adjust=False).mean()


@register_factor()
class EMA26Factor(VectorizedFactor):
    """26日指数移动平均线"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='ema_26',
            name='EMA26',
            display_name='26日指数移动平均',
            description='26日收盘价指数移动平均',
            category='technical',
            subcategory='trend',
            lookback_period=26,
            required_data_fields=['close']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        period = self.params.get('period', 26)
        return data['close'].ewm(span=period, adjust=False).mean()


@register_factor()
class WMAFactor(VectorizedFactor):
    """加权移动平均线"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='wma_20',
            name='WMA20',
            display_name='20日加权移动平均',
            description='20日收盘价加权移动平均，近期权重更大',
            category='technical',
            subcategory='trend',
            lookback_period=20,
            required_data_fields=['close']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        period = self.params.get('period', 20)
        weights = np.arange(1, period + 1)
        
        def wma(prices):
            if len(prices) < period:
                return np.nan
            return np.dot(prices[-period:], weights) / weights.sum()
        
        return data['close'].rolling(window=period).apply(wma, raw=True)


@register_factor()
class DMAPlusFactor(VectorizedFactor):
    """DMA多头力量"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='dma_plus',
            name='DMAPlus',
            display_name='DMA多头力量',
            description='短期均线与长期均线的差值，衡量多头力量',
            category='technical',
            subcategory='trend',
            lookback_period=30,
            required_data_fields=['close']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        short_period = self.params.get('short_period', 10)
        long_period = self.params.get('long_period', 30)
        
        short_ma = data['close'].rolling(window=short_period).mean()
        long_ma = data['close'].rolling(window=long_period).mean()
        
        return short_ma - long_ma


@register_factor()
class TRIXFactor(BaseFactor):
    """TRIX三重指数平滑移动平均"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='trix_12',
            name='TRIX12',
            display_name='TRIX三重指数平滑',
            description='三重指数平滑移动平均的变化率，过滤短期波动',
            category='technical',
            subcategory='trend',
            lookback_period=12,
            required_data_fields=['close']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        period = self.params.get('period', 12)
        
        ema1 = data['close'].ewm(span=period, adjust=False).mean()
        ema2 = ema1.ewm(span=period, adjust=False).mean()
        ema3 = ema2.ewm(span=period, adjust=False).mean()
        
        trix = ema3.pct_change() * 100
        return trix


@register_factor()
class ParabolicSARFactor(BaseFactor):
    """抛物线转向指标"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='psar',
            name='ParabolicSAR',
            display_name='抛物线转向指标',
            description='追踪价格止损位，用于识别趋势反转点',
            category='technical',
            subcategory='trend',
            lookback_period=20,
            required_data_fields=['high', 'low', 'close']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        af_start = self.params.get('af_start', 0.02)
        af_increment = self.params.get('af_increment', 0.02)
        af_max = self.params.get('af_max', 0.2)
        
        high = data['high'].values
        low = data['low'].values
        close = data['close'].values
        
        n = len(data)
        sar = np.zeros(n)
        trend = np.zeros(n)
        af = np.zeros(n)
        ep = np.zeros(n)
        
        # 初始化
        sar[0] = low[0]
        trend[0] = 1  # 1=上升趋势, -1=下降趋势
        af[0] = af_start
        ep[0] = high[0]
        
        for i in range(1, n):
            # 更新SAR
            sar[i] = sar[i-1] + af[i-1] * (ep[i-1] - sar[i-1])
            
            # 判断趋势是否反转
            if trend[i-1] == 1:  # 上升趋势
                if low[i] < sar[i]:  # 价格突破SAR，转为下降趋势
                    trend[i] = -1
                    sar[i] = ep[i-1]  # SAR重置为前期EP
                    ep[i] = low[i]
                    af[i] = af_start
                else:
                    trend[i] = 1
                    if high[i] > ep[i-1]:  # 创新高
                        ep[i] = high[i]
                        af[i] = min(af[i-1] + af_increment, af_max)
                    else:
                        ep[i] = ep[i-1]
                        af[i] = af[i-1]
                    # SAR不能高于前两日最低价
                    sar[i] = min(sar[i], low[i-1])
                    if i > 1:
                        sar[i] = min(sar[i], low[i-2])
            else:  # 下降趋势
                if high[i] > sar[i]:  # 价格突破SAR，转为上升趋势
                    trend[i] = 1
                    sar[i] = ep[i-1]
                    ep[i] = high[i]
                    af[i] = af_start
                else:
                    trend[i] = -1
                    if low[i] < ep[i-1]:  # 创新低
                        ep[i] = low[i]
                        af[i] = min(af[i-1] + af_increment, af_max)
                    else:
                        ep[i] = ep[i-1]
                        af[i] = af[i-1]
                    # SAR不能低于前两日最高价
                    sar[i] = max(sar[i], high[i-1])
                    if i > 1:
                        sar[i] = max(sar[i], high[i-2])
        
        return pd.Series(sar, index=data.index)


@register_factor()
class IchimokuTenkanFactor(BaseFactor):
    """一目均衡表-转换线"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='ichimoku_tenkan',
            name='IchimokuTenkan',
            display_name='一目均衡-转换线',
            description='9日最高价和最低价的中点',
            category='technical',
            subcategory='trend',
            lookback_period=9,
            required_data_fields=['high', 'low']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        period = self.params.get('period', 9)
        high_max = data['high'].rolling(window=period).max()
        low_min = data['low'].rolling(window=period).min()
        return (high_max + low_min) / 2


@register_factor()
class IchimokuKijunFactor(BaseFactor):
    """一目均衡表-基准线"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='ichimoku_kijun',
            name='IchimokuKijun',
            display_name='一目均衡-基准线',
            description='26日最高价和最低价的中点',
            category='technical',
            subcategory='trend',
            lookback_period=26,
            required_data_fields=['high', 'low']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        period = self.params.get('period', 26)
        high_max = data['high'].rolling(window=period).max()
        low_min = data['low'].rolling(window=period).min()
        return (high_max + low_min) / 2
