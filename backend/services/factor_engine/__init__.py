"""
因子引擎模块 - 提供因子计算、信号生成、权重管理等功能
"""

# 导出核心组件
from backend.services.factor_engine.base_factors import factor_engine, FactorEngine
from backend.services.factor_engine.factor_registry import register_factor, FactorRegistry
from backend.services.factor_engine.factor_signal_generator import FactorSignalGenerator
from backend.services.factor_engine.factor_weighting import DynamicFactorWeighting
# factor_bridge 只有函数，没有类
from backend.services.factor_engine.factor_cache_manager import FactorCacheManager
from backend.services.factor_engine.decision_fusion_engine import DecisionFusionEngine

__all__ = [
    'factor_engine',
    'FactorEngine',
    'register_factor',
    'FactorRegistry',
    'FactorSignalGenerator',
    'DynamicFactorWeighting',
    # 'FactorBridge',  # factor_bridge只有函数，无类
    'FactorCacheManager',
    'DecisionFusionEngine',
]
