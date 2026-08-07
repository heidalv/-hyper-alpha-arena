"""
ATAS V2 因子计算引擎 - 因子注册表

管理所有可用因子，支持动态注册和查询
"""
from typing import Dict, List, Optional, Type, Set
from collections import defaultdict
import inspect
import logging

from .factor_base import BaseFactor, FactorMetadata

logger = logging.getLogger(__name__)


class FactorRegistry:
    """
    因子注册表
    
    单例模式，管理所有可用的因子类
    """
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self._factors: Dict[str, Type[BaseFactor]] = {}
        self._metadata_cache: Dict[str, FactorMetadata] = {}
        self._category_index: Dict[str, Set[str]] = defaultdict(set)
        self._dependency_graph: Dict[str, Set[str]] = defaultdict(set)
        self._alias_index: Dict[str, str] = {}  # 别名 -> 规范 factor_id
        
        self._initialized = True
        logger.info("FactorRegistry initialized")
    
    def register(self, factor_class: Type[BaseFactor], override: bool = False) -> None:
        """
        注册因子类
        
        Args:
            factor_class: 因子类（必须继承BaseFactor）
            override: 是否允许覆盖已存在的因子
            
        Raises:
            TypeError: 如果不是BaseFactor的子类
            ValueError: 如果因子ID已存在且不允许覆盖
        """
        # 验证是否为BaseFactor子类
        if not inspect.isclass(factor_class) or not issubclass(factor_class, BaseFactor):
            raise TypeError(f"{factor_class} must be a subclass of BaseFactor")
        
        # 获取因子元数据
        try:
            temp_instance = factor_class()
            metadata = temp_instance.metadata
        except Exception as e:
            # 单个因子实例化失败（如抽象方法未实现）不应抛异常导致整个模块导入失败，
            # 否则同文件的其他可用因子会一起丢失。跳过该因子即可。
            print(f"Skip registering {factor_class.__name__}: {e}")
            return
        
        factor_id = metadata.factor_id
        
        # 检查是否已存在
        if factor_id in self._factors and not override:
            raise ValueError(
                f"Factor '{factor_id}' already registered. "
                f"Use override=True to replace it."
            )
        
        # 注册因子
        self._factors[factor_id] = factor_class
        self._metadata_cache[factor_id] = metadata
        
        # 更新别名索引（v6: 历史/跨版本 ID 可检索，不破坏历史数据对齐）
        for alias in metadata.aliases or []:
            self._alias_index[alias] = factor_id
        
        # 更新分类索引
        self._category_index[metadata.category].add(factor_id)
        if metadata.subcategory:
            self._category_index[f"{metadata.category}.{metadata.subcategory}"].add(factor_id)
        
        # 更新依赖图
        if metadata.dependencies:
            self._dependency_graph[factor_id] = set(metadata.dependencies)
        
        logger.info(
            f"Registered factor: {factor_id} "
            f"({metadata.category}/{metadata.subcategory})"
        )
    
    def unregister(self, factor_id: str) -> None:
        """
        注销因子
        
        Args:
            factor_id: 因子ID
        """
        if factor_id not in self._factors:
            logger.warning(f"Factor '{factor_id}' not found, skip unregister")
            return
        
        metadata = self._metadata_cache[factor_id]
        
        # 移除注册
        del self._factors[factor_id]
        del self._metadata_cache[factor_id]
        
        # 清理别名索引
        for alias, fid in list(self._alias_index.items()):
            if fid == factor_id:
                del self._alias_index[alias]
        
        # 更新分类索引
        self._category_index[metadata.category].discard(factor_id)
        if metadata.subcategory:
            self._category_index[f"{metadata.category}.{metadata.subcategory}"].discard(factor_id)
        
        # 更新依赖图
        if factor_id in self._dependency_graph:
            del self._dependency_graph[factor_id]
        
        logger.info(f"Unregistered factor: {factor_id}")
    
    def get(self, factor_id: str, params: Optional[Dict] = None) -> BaseFactor:
        """
        创建因子实例
        
        Args:
            factor_id: 因子ID（支持别名）
            params: 因子参数
            
        Returns:
            因子实例
            
        Raises:
            KeyError: 如果因子不存在
        """
        factor_id = self.resolve(factor_id)
        if factor_id not in self._factors:
            raise KeyError(f"Factor '{factor_id}' not found in registry")
        
        factor_class = self._factors[factor_id]
        return factor_class(params=params)
    
    def get_metadata(self, factor_id: str) -> FactorMetadata:
        """
        获取因子元数据
        
        Args:
            factor_id: 因子ID（支持别名）
            
        Returns:
            因子元数据
        """
        factor_id = self.resolve(factor_id)
        if factor_id not in self._metadata_cache:
            raise KeyError(f"Factor '{factor_id}' not found in registry")
        
        return self._metadata_cache[factor_id]
    
    def list_factors(
        self, 
        category: Optional[str] = None,
        subcategory: Optional[str] = None,
        include_metadata: bool = False
    ) -> List:
        """
        列出所有因子
        
        Args:
            category: 筛选分类
            subcategory: 筛选子分类
            include_metadata: 是否包含元数据
            
        Returns:
            因子ID列表或(factor_id, metadata)元组列表
        """
        # 筛选
        if category and subcategory:
            factor_ids = self._category_index.get(f"{category}.{subcategory}", set())
        elif category:
            factor_ids = self._category_index.get(category, set())
        else:
            factor_ids = set(self._factors.keys())
        
        # 返回结果
        if include_metadata:
            return [(fid, self._metadata_cache[fid]) for fid in sorted(factor_ids)]
        else:
            return sorted(factor_ids)
    
    def get_categories(self) -> Dict[str, List[str]]:
        """
        获取所有分类及其包含的因子
        
        Returns:
            分类字典 {category: [factor_ids]}
        """
        result = {}
        for category, factor_ids in self._category_index.items():
            if '.' not in category:  # 只返回主分类
                result[category] = sorted(factor_ids)
        return result
    
    def get_dependencies(self, factor_id: str, recursive: bool = False) -> List[str]:
        """
        获取因子依赖
        
        Args:
            factor_id: 因子ID
            recursive: 是否递归获取所有依赖
            
        Returns:
            依赖的因子ID列表
        """
        if factor_id not in self._dependency_graph:
            return []
        
        if not recursive:
            return sorted(self._dependency_graph[factor_id])
        
        # 递归获取所有依赖
        all_deps = set()
        to_process = list(self._dependency_graph[factor_id])
        
        while to_process:
            dep = to_process.pop()
            if dep not in all_deps:
                all_deps.add(dep)
                if dep in self._dependency_graph:
                    to_process.extend(self._dependency_graph[dep])
        
        return sorted(all_deps)
    
    def resolve_dependencies(self, factor_ids: List[str]) -> List[str]:
        """
        解析因子依赖，返回执行顺序
        
        Args:
            factor_ids: 需要计算的因子ID列表
            
        Returns:
            按依赖顺序排列的因子ID列表
            
        Raises:
            ValueError: 如果存在循环依赖
        """
        # 收集所有需要计算的因子（包括依赖）
        all_factors = set(factor_ids)
        for fid in factor_ids:
            all_factors.update(self.get_dependencies(fid, recursive=True))
        
        # 拓扑排序
        in_degree = {fid: 0 for fid in all_factors}
        for fid in all_factors:
            for dep in self._dependency_graph.get(fid, []):
                if dep in all_factors:
                    in_degree[fid] += 1
        
        # BFS
        queue = [fid for fid in all_factors if in_degree[fid] == 0]
        result = []
        
        while queue:
            current = queue.pop(0)
            result.append(current)
            
            # 更新依赖此因子的其他因子
            for fid in all_factors:
                if current in self._dependency_graph.get(fid, []):
                    in_degree[fid] -= 1
                    if in_degree[fid] == 0:
                        queue.append(fid)
        
        # 检查循环依赖
        if len(result) != len(all_factors):
            missing = all_factors - set(result)
            raise ValueError(f"Circular dependency detected in factors: {missing}")
        
        return result
    
    def resolve(self, factor_id: str) -> str:
        """
        解析因子ID（别名 → 规范ID）
        
        Args:
            factor_id: 因子ID或别名
            
        Returns:
            规范因子ID；未注册的ID原样返回
        """
        if factor_id in self._alias_index:
            return self._alias_index[factor_id]
        return factor_id
    
    def exists(self, factor_id: str) -> bool:
        """
        检查因子是否存在
        
        Args:
            factor_id: 因子ID（支持别名）
            
        Returns:
            是否存在
        """
        return self.resolve(factor_id) in self._factors
    
    def count(self) -> int:
        """返回注册的因子总数"""
        return len(self._factors)
    
    def clear(self) -> None:
        """清空注册表（慎用）"""
        self._factors.clear()
        self._metadata_cache.clear()
        self._category_index.clear()
        self._dependency_graph.clear()
        self._alias_index.clear()
        logger.warning("FactorRegistry cleared")
    
    def __repr__(self) -> str:
        return (
            f"<FactorRegistry: {self.count()} factors, "
            f"categories={list(self.get_categories().keys())}>"
        )


# 全局单例
registry = FactorRegistry()


# 装饰器：自动注册因子
def register_factor(override: bool = False):
    """
    因子注册装饰器
    
    使用示例:
        @register_factor()
        class MyFactor(BaseFactor):
            ...
    """
    def decorator(factor_class: Type[BaseFactor]):
        registry.register(factor_class, override=override)
        return factor_class
    
    return decorator
