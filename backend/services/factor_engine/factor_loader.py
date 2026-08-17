"""
ATAS V2 - 因子自动加载器

自动扫描并注册所有因子类
"""
import os
import importlib
import inspect
from typing import List, Dict, Type
from pathlib import Path

from .factor_base import BaseFactor
from .factor_registry import FactorRegistry


class FactorLoader:
    """因子自动加载器"""
    
    def __init__(self):
        self.registry = FactorRegistry()
        self.loaded_factors: Dict[str, Type[BaseFactor]] = {}
    
    def discover_and_load_all(self) -> int:
        """
        自动发现并加载所有因子
        
        Returns:
            加载的因子数量
        """
        factors_dir = Path(__file__).parent / 'factors'
        
        if not factors_dir.exists():
            print(f"Warning: Factors directory not found: {factors_dir}")
            return 0
        
        count = 0
        
        # 扫描所有分类目录
        for category_dir in factors_dir.iterdir():
            if not category_dir.is_dir():
                continue
            
            # 跳过 __pycache__ 及下划线开头的辅助/隔离目录（如 _ai_gen_quarantine）
            if category_dir.name.startswith('_'):
                continue
            
            # 加载该分类下的所有因子
            category_count = self._load_category(category_dir)
            count += category_count
            
            print(f"Loaded {category_count} factors from category: {category_dir.name}")
        
        print(f"Total factors loaded: {count}")
        return count
    
    def _load_category(self, category_dir: Path) -> int:
        """加载指定分类目录下的所有因子"""
        count = 0
        
        # 扫描Python文件
        for py_file in category_dir.glob('*.py'):
            if py_file.name.startswith('__'):
                continue
            
            # [P1-10] 前视审计：源码含负向 shift（引未来数据）的因子禁止加载进注册表，
            # 变量型 shift 仅标记人工复核（不拦截，避免误杀正常动态窗口因子）。
            try:
                with open(py_file, "r", encoding="utf-8", errors="replace") as _f:
                    _src = _f.read()
                from backend.services.factor_engine.lookahead_audit import audit_lookahead
                _verdict, _detail = audit_lookahead(_src)
                if _verdict == "blocked":
                    print(
                        f"[FactorLoader] 前视因子跳过加载: {py_file.name} ({_detail})",
                    )
                    continue
                if _verdict == "review":
                    print(
                        f"[FactorLoader] 变量型 shift 待复核: {py_file.name} ({_detail})",
                    )
            except Exception:
                pass  # 审计失败不阻断加载（compile 预筛兜底）
            
            try:
                # 构建模块路径
                module_path = self._get_module_path(py_file)
                
                # 动态导入模块
                module = importlib.import_module(module_path)
                
                # 扫描模块中的因子类。单个类失败不应拖垮整个文件，否则
                # 一个抽象/导入的辅助类会导致同文件其他可用因子全部丢失。
                for name, obj in inspect.getmembers(module):
                    if self._is_factor_class(obj):
                        try:
                            factor_id = obj({}).get_metadata().factor_id
                            self.loaded_factors[factor_id] = obj
                            self.registry.register(obj, override=True)
                            count += 1
                        except Exception as e:
                            print(f"Error loading factor {name} from {py_file.name}: {str(e)}")
                        
            except Exception as e:
                print(f"Error loading {py_file.name}: {str(e)}")
        
        return count
    
    def _get_module_path(self, file_path: Path) -> str:
        """获取模块导入路径"""
        # 将文件路径转换为模块路径
        parts = file_path.parts
        
        # 找到backend目录的索引
        try:
            backend_idx = parts.index('backend')
            module_parts = parts[backend_idx + 1:]
            
            # 移除.py扩展名
            module_parts = list(module_parts)[:-1] + [file_path.stem]
            
            return 'backend.' + '.'.join(module_parts)
        except ValueError:
            # 如果找不到backend，使用相对路径
            return f"services.factor_engine.factors.{file_path.parent.name}.{file_path.stem}"
    
    def _is_factor_class(self, obj) -> bool:
        """判断是否是因子类"""
        return (
            inspect.isclass(obj) and
            issubclass(obj, BaseFactor) and
            obj is not BaseFactor and
            not inspect.isabstract(obj)
        )
    
    def get_all_factors(self) -> Dict[str, Type[BaseFactor]]:
        """获取所有已加载的因子"""
        return self.loaded_factors
    
    def get_factors_by_category(self, category: str) -> List[Type[BaseFactor]]:
        """按分类获取因子"""
        result = []
        for factor_class in self.loaded_factors.values():
            metadata = factor_class({}).get_metadata()
            if metadata.category == category:
                result.append(factor_class)
        return result
    
    def get_factor_info(self) -> Dict[str, Dict]:
        """获取所有因子的信息摘要"""
        info = {}
        
        for factor_id, factor_class in self.loaded_factors.items():
            try:
                metadata = factor_class({}).get_metadata()
                info[factor_id] = {
                    'name': metadata.name,
                    'display_name': metadata.display_name,
                    'description': metadata.description,
                    'category': metadata.category,
                    'subcategory': metadata.subcategory,
                    'lookback_period': metadata.lookback_period,
                    'required_fields': metadata.required_data_fields or []
                }
            except Exception as e:
                print(f"Error getting info for {factor_id}: {str(e)}")
        
        return info


# 全局因子加载器实例
_factor_loader = None


def get_factor_loader() -> FactorLoader:
    """获取全局因子加载器实例"""
    global _factor_loader
    if _factor_loader is None:
        _factor_loader = FactorLoader()
        _factor_loader.discover_and_load_all()
    return _factor_loader


def initialize_factors():
    """初始化所有因子（应用启动时调用）"""
    loader = get_factor_loader()
    print(f"Factor initialization complete. Total factors: {len(loader.loaded_factors)}")
    return loader
