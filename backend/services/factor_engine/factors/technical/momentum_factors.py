"""
ATAS V2 - 动量类技术指标因子

包含7个动量类因子
"""
import pandas as pd
import numpy as np
from typing import Dict, Any
from ...factor_base import BaseFactor, VectorizedFactor, FactorMetadata
from ...factor_registry import register_factor


@register_factor()
class RSI7Factor(BaseFactor):
    """7日相对强弱指标"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='rsi_7',
            name='RSI7',
            display_name='7日RSI',
            description='7日相对强弱指标，短期超买超卖信号',
            category='technical',
            subcategory='momentum',
            lookback_period=7,
            required_data_fields=['close']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        period = self.params.get('period', 7)
        delta = data['close'].diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        
        avg_gain = gain.rolling(window=period).mean()
        avg_loss = loss.rolling(window=period).mean()
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi


@register_factor()
class StochasticKFactor(BaseFactor):
    """随机指标K线"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='stoch_k',
            name='StochK',
            display_name='随机指标K线',
            description='KDJ指标的K线，衡量价格在一定周期内的相对位置',
            category='technical',
            subcategory='momentum',
            lookback_period=14,
            required_data_fields=['high', 'low', 'close']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        period = self.params.get('period', 14)
        
        low_min = data['low'].rolling(window=period).min()
        high_max = data['high'].rolling(window=period).max()
        
        k = 100 * (data['close'] - low_min) / (high_max - low_min)
        return k


@register_factor()
class StochasticDFactor(BaseFactor):
    """随机指标D线"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='stoch_d',
            name='StochD',
            display_name='随机指标D线',
            description='KDJ指标的D线，K线的3日移动平均',
            category='technical',
            subcategory='momentum',
            lookback_period=14,
            required_data_fields=['high', 'low', 'close']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        period = self.params.get('period', 14)
        smooth = self.params.get('smooth', 3)
        
        low_min = data['low'].rolling(window=period).min()
        high_max = data['high'].rolling(window=period).max()
        
        k = 100 * (data['close'] - low_min) / (high_max - low_min)
        d = k.rolling(window=smooth).mean()
        return d


@register_factor()
class CCIFactor(BaseFactor):
    """顺势指标"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='cci_14',
            name='CCI14',
            display_name='14日CCI',
            description='顺势指标，衡量价格偏离统计平均值的程度',
            category='technical',
            subcategory='momentum',
            lookback_period=14,
            required_data_fields=['high', 'low', 'close']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        period = self.params.get('period', 14)
        
        tp = (data['high'] + data['low'] + data['close']) / 3
        ma = tp.rolling(window=period).mean()
        md = tp.rolling(window=period).apply(lambda x: np.abs(x - x.mean()).mean())
        
        cci = (tp - ma) / (0.015 * md)
        return cci


@register_factor()
class WilliamsRFactor(BaseFactor):
    """威廉指标"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='williams_r',
            name='WilliamsR',
            display_name='威廉指标',
            description='威廉%R指标，反向随机指标，衡量超买超卖',
            category='technical',
            subcategory='momentum',
            lookback_period=14,
            required_data_fields=['high', 'low', 'close']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        period = self.params.get('period', 14)
        
        high_max = data['high'].rolling(window=period).max()
        low_min = data['low'].rolling(window=period).min()
        
        wr = -100 * (high_max - data['close']) / (high_max - low_min)
        return wr


@register_factor()
class ROCFactor(VectorizedFactor):
    """变动率指标"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='roc_12',
            name='ROC12',
            display_name='12日变动率',
            description='12日价格变动率，衡量价格变化速度',
            category='technical',
            subcategory='momentum',
            lookback_period=12,
            required_data_fields=['close']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        period = self.params.get('period', 12)
        roc = ((data['close'] - data['close'].shift(period)) / data['close'].shift(period)) * 100
        return roc


@register_factor()
class UltimateOscillatorFactor(BaseFactor):
    """终极振荡器"""
    
    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id='uo',
            name='UltimateOscillator',
            display_name='终极振荡器',
            description='多时间框架动量指标，综合7/14/28日动量',
            category='technical',
            subcategory='momentum',
            lookback_period=28,
            required_data_fields=['high', 'low', 'close']
        )
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        period1 = self.params.get('period1', 7)
        period2 = self.params.get('period2', 14)
        period3 = self.params.get('period3', 28)
        
        # 计算买压和真实波幅
        prev_close = data['close'].shift(1)
        bp = data['close'] - np.minimum(data['low'], prev_close)
        tr = np.maximum(data['high'], prev_close) - np.minimum(data['low'], prev_close)
        
        # 计算各周期平均值
        avg_bp1 = bp.rolling(window=period1).sum()
        avg_tr1 = tr.rolling(window=period1).sum()
        
        avg_bp2 = bp.rolling(window=period2).sum()
        avg_tr2 = tr.rolling(window=period2).sum()
        
        avg_bp3 = bp.rolling(window=period3).sum()
        avg_tr3 = tr.rolling(window=period3).sum()
        
        # 计算终极振荡器
        uo = 100 * ((avg_bp1 / avg_tr1) * 4 + (avg_bp2 / avg_tr2) * 2 + (avg_bp3 / avg_tr3)) / 7
        return uo
