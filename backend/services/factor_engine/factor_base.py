"""
ATAS V2 因子计算引擎 - 因子基类

提供所有因子的基础接口和通用功能
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from datetime import datetime
import pandas as pd
import numpy as np
from dataclasses import dataclass


@dataclass
class FactorMetadata:
    """因子元数据"""
    factor_id: str
    name: str
    display_name: str
    description: str
    category: str  # 'technical', 'fundamental', 'sentiment', 'behavioral'
    subcategory: str  # 'trend', 'momentum', 'volatility', 'volume'
    version: str = "1.0.0"
    author: str = "ATAS System"
    created_at: datetime = None
    
    # 计算配置
    lookback_period: int = 20
    required_data_fields: List[str] = None  # ['open', 'high', 'low', 'close', 'volume']
    dependencies: List[str] = None  # 依赖的其他因子
    aliases: List[str] = None  # 别名（历史/跨版本因子ID，可检索不破坏历史数据对齐）
    
    # 性能配置
    cache_enabled: bool = True
    cache_ttl: int = 3600  # 缓存有效期(秒)
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()
        if self.required_data_fields is None:
            self.required_data_fields = ['close']
        if self.dependencies is None:
            self.dependencies = []
        if self.aliases is None:
            self.aliases = []


@dataclass
class FactorResult:
    """因子计算结果"""
    factor_id: str
    symbol: str
    timeframe: str
    value: Any  # 可以是单值、数组或DataFrame
    timestamp: datetime
    compute_time_ms: float
    data_points: int
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class BaseFactor(ABC):
    """
    因子基类
    
    所有自定义因子必须继承此类并实现 calculate() 方法
    """
    
    def __init__(self, params: Optional[Dict[str, Any]] = None):
        """
        初始化因子
        
        Args:
            params: 因子参数，会覆盖默认参数
        """
        self.params = self.get_default_params()
        if params:
            self.params.update(params)
        
        self._metadata = self.get_metadata()
    
    @abstractmethod
    def get_metadata(self) -> FactorMetadata:
        """
        返回因子元数据
        
        必须实现此方法以提供因子的基本信息
        """
        pass
    
    @abstractmethod
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        """
        计算因子值
        
        Args:
            data: 市场数据DataFrame，包含 open/high/low/close/volume 等字段
                 索引为时间戳
        
        Returns:
            因子值的Series，索引与输入数据对齐
        
        注意:
            - 数据已经按时间排序
            - 可能包含NaN值，需要处理
            - 返回值长度必须与输入数据长度一致
        """
        pass
    
    def get_default_params(self) -> Dict[str, Any]:
        """
        返回因子的默认参数
        
        子类可以重写此方法以提供自定义默认参数
        """
        return {}
    
    def validate_data(self, data: pd.DataFrame) -> bool:
        """
        验证输入数据是否满足计算要求
        
        Args:
            data: 输入的市场数据
            
        Returns:
            数据是否有效
        """
        # 检查必需字段
        required_fields = self._metadata.required_data_fields
        missing_fields = [f for f in required_fields if f not in data.columns]
        
        if missing_fields:
            raise ValueError(f"Missing required fields: {missing_fields}")
        
        # 检查数据长度
        if len(data) < self._metadata.lookback_period:
            raise ValueError(
                f"Insufficient data: need at least {self._metadata.lookback_period} bars, "
                f"got {len(data)}"
            )
        
        return True
    
    def preprocess_data(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        预处理数据
        
        子类可以重写此方法以添加自定义预处理逻辑
        """
        # 移除NaN行
        data = data.dropna(subset=self._metadata.required_data_fields)
        
        # 确保按时间排序
        if not data.index.is_monotonic_increasing:
            data = data.sort_index()
        
        return data
    
    def postprocess_result(self, result: pd.Series) -> pd.Series:
        """
        后处理结果
        
        子类可以重写此方法以添加自定义后处理逻辑
        """
        # 替换无穷值
        result = result.replace([np.inf, -np.inf], np.nan)
        
        return result
    
    def get_cache_key(self, symbol: str, timeframe: str, timestamp: datetime) -> str:
        """
        生成缓存键
        
        Args:
            symbol: 交易对
            timeframe: 时间周期
            timestamp: 时间戳
            
        Returns:
            缓存键字符串
        """
        ts_str = timestamp.strftime("%Y%m%d%H%M%S")
        params_str = "_".join([f"{k}={v}" for k, v in sorted(self.params.items())])
        return f"{symbol}_{self._metadata.factor_id}_{timeframe}_{ts_str}_{params_str}"
    
    @property
    def metadata(self) -> FactorMetadata:
        """获取因子元数据"""
        return self._metadata
    
    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__}("
            f"id={self._metadata.factor_id}, "
            f"category={self._metadata.category}, "
            f"params={self.params})>"
        )


class VectorizedFactor(BaseFactor):
    """
    向量化因子基类
    
    用于需要高性能批量计算的因子
    使用NumPy向量化操作或Numba JIT编译
    """
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        """
        使用向量化计算
        """
        # 提取必需的数据列
        arrays = {field: data[field].values for field in self._metadata.required_data_fields}
        
        # 调用向量化计算方法
        result_array = self.vectorized_calculate(**arrays)
        
        # 转换回Series
        return pd.Series(result_array, index=data.index, name=self._metadata.factor_id)
    
    @abstractmethod
    def vectorized_calculate(self, **arrays) -> np.ndarray:
        """
        向量化计算方法
        
        Args:
            **arrays: 输入的NumPy数组，键为字段名
            
        Returns:
            计算结果的NumPy数组
        """
        pass


class RollingWindowFactor(BaseFactor):
    """
    滚动窗口因子基类
    
    用于需要滚动窗口计算的因子（如移动平均、标准差等）
    """
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        """
        使用滚动窗口计算
        """
        window = self.params.get('window', self._metadata.lookback_period)
        
        # 调用滚动计算方法
        result = self.rolling_calculate(data, window)
        
        return result
    
    @abstractmethod
    def rolling_calculate(self, data: pd.DataFrame, window: int) -> pd.Series:
        """
        滚动窗口计算方法
        
        Args:
            data: 输入数据
            window: 窗口大小
            
        Returns:
            计算结果的Series
        """
        pass


class CompositeFactor(BaseFactor):
    """
    组合因子基类
    
    用于需要组合多个其他因子的复合因子
    """
    
    def __init__(self, sub_factors: List[BaseFactor], params: Optional[Dict[str, Any]] = None):
        """
        初始化组合因子
        
        Args:
            sub_factors: 子因子列表
            params: 因子参数
        """
        self.sub_factors = sub_factors
        super().__init__(params)
    
    def calculate(self, data: pd.DataFrame) -> pd.Series:
        """
        计算组合因子
        """
        # 计算所有子因子
        sub_results = {}
        for factor in self.sub_factors:
            sub_results[factor.metadata.factor_id] = factor.calculate(data)
        
        # 调用组合方法
        result = self.combine(sub_results, data)
        
        return result
    
    @abstractmethod
    def combine(self, sub_results: Dict[str, pd.Series], data: pd.DataFrame) -> pd.Series:
        """
        组合子因子结果
        
        Args:
            sub_results: 子因子计算结果字典 {factor_id: result_series}
            data: 原始数据
            
        Returns:
            组合后的因子值
        """
        pass


# 便捷装饰器
def factor_metadata(**kwargs):
    """
    因子元数据装饰器
    
    使用示例:
        @factor_metadata(
            factor_id='ma_20',
            name='MA20',
            category='technical',
            subcategory='trend'
        )
        class MA20Factor(BaseFactor):
            pass
    """
    def decorator(cls):
        original_get_metadata = cls.get_metadata
        
        def new_get_metadata(self):
            base_metadata = original_get_metadata(self)
            for key, value in kwargs.items():
                setattr(base_metadata, key, value)
            return base_metadata
        
        cls.get_metadata = new_get_metadata
        return cls
    
    return decorator
