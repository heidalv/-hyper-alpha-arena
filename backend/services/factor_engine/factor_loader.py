"""
ATAS V2 - 因子自动加载器

自动扫描并注册所有因子类

[§64 修复 2026-09-10] 本模块此前用 `print()` 报告「因子文件导入失败 / 因子类加载失败」，
而 `print` 只进 `logs/backend-console.log`（启动脚本的 stdout 重定向），**不进 logging 流**：
  * 读 `backend.log` 的人看不到任何因子缺失；
  * console 文件无轮转（实测 540MB）；
  * 失败连计数都没有 —— 「Total factors loaded: 150」看不出少了几个。
现全部改走 `logger`，并新增 `failed_files` 汇总，让「因子静默缺失」变成可查事件。
"""
import logging
import os
import importlib
import inspect
from typing import List, Dict, Type
from pathlib import Path

from .factor_base import BaseFactor
from .factor_registry import FactorRegistry

logger = logging.getLogger(__name__)


# ── 目录指纹缓存（轮77 P1-10）─────────────────────────────────────────────
#
# 为什么需要：`discover_and_load_all()` 每次调用都要**重读并重导入 242 个模块**
# （实测 0.212s/次、344KB 源码、259 个因子以 override=True 重注册；新建
# `FactorEngine()` 同样 0.223s）。而调用方里有**按符号循环**构造引擎的写法
# （`strategy_intelligence_engine.py:72/244`），等于每个符号白付一次全量扫描。
#
# 缓存键 = 目录下所有因子文件的 (相对路径, mtime_ns, size) 指纹：
#   · 指纹不变 → 跳过重扫（同一进程内的重复构造因此接近免费）；
#   · 文件新增/修改/删除 → 指纹变化 → 正常重扫（不牺牲热加载正确性）。
# 缓存的是**类对象**（模块只导入一次），每个实例仍往自己的 registry 注册一份，
# 保持「每个 FactorLoader 拥有独立注册表」这一既有契约不变。
_DISCOVERY_CACHE: Dict[str, tuple] = {}
_DISCOVERY_CACHE_MAX = 4          # 正常只有 1 个 factors 目录；留余量防路径变体撑爆


def _dir_signature(factors_dir: Path) -> tuple:
    """目录指纹：所有因子 .py 的 (相对路径, mtime_ns, size)。"""
    items = []
    try:
        for category_dir in sorted(p for p in factors_dir.iterdir() if p.is_dir()):
            if category_dir.name.startswith('_'):
                continue          # 与加载逻辑一致：跳过 _ai_gen_quarantine 等
            for py_file in sorted(category_dir.glob('*.py')):
                if py_file.name.startswith('__'):
                    continue
                try:
                    st = py_file.stat()
                    items.append((str(py_file.relative_to(factors_dir)), st.st_mtime_ns, st.st_size))
                except OSError:
                    items.append((str(py_file.relative_to(factors_dir)), -1, -1))
    except OSError:
        return ()
    return tuple(items)


class FactorLoader:
    """因子自动加载器"""
    
    def __init__(self):
        self.registry = FactorRegistry()
        self.loaded_factors: Dict[str, Type[BaseFactor]] = {}
        # [§64] 加载失败清单：让"少了几个因子"可查（原先只在 stdout 里一闪而过）
        self.failed_files: List[str] = []
        # [轮77] 本次是否走了缓存（供测试/诊断观察）
        self.cache_hit: bool = False
    
    def discover_and_load_all(self) -> int:
        """
        自动发现并加载所有因子
        
        Returns:
            加载的因子数量
        """
        factors_dir = Path(__file__).parent / 'factors'
        
        if not factors_dir.exists():
            logger.error("[FactorLoader] 因子目录不存在: %s", factors_dir)
            return 0

        # ── 缓存命中：目录指纹未变就不重扫/重导入 ──
        _key = str(factors_dir.resolve())
        _sig = _dir_signature(factors_dir)
        _cached = _DISCOVERY_CACHE.get(_key)
        if _cached is not None and _cached[0] == _sig and _sig:
            _factors = _cached[1]
            for _fid, _cls in _factors.items():
                self.loaded_factors[_fid] = _cls
                self.registry.register(_cls, override=True)
            self.cache_hit = True
            logger.debug(
                "[FactorLoader] 目录指纹未变，复用已加载的 %d 个因子（跳过 242 个模块重扫）",
                len(_factors),
            )
            return len(_factors)
        
        count = 0
        scanned_py = 0
        
        # 扫描所有分类目录
        for category_dir in factors_dir.iterdir():
            if not category_dir.is_dir():
                continue
            
            # 跳过 __pycache__ 及下划线开头的辅助/隔离目录（如 _ai_gen_quarantine）
            if category_dir.name.startswith('_'):
                continue
            
            scanned_py += len([p for p in category_dir.glob('*.py') if not p.name.startswith('__')])
            # 加载该分类下的所有因子
            category_count = self._load_category(category_dir)
            count += category_count
            
            logger.info("[FactorLoader] 类别 %s 加载 %d 个因子", category_dir.name, category_count)
        
        self._check_zero_load(count, scanned_py)
        if self.failed_files:
            # 静默失效护栏：有文件失败时必须显式报出，不能只给一个"看起来正常"的总数
            logger.error(
                "[FactorLoader] 共 %d 个因子文件加载失败（因子将从注册表静默缺失）: %s",
                len(self.failed_files), ", ".join(sorted(set(self.failed_files))[:20]),
            )
        logger.info("[FactorLoader] 因子加载合计: %d（失败文件 %d）", count, len(self.failed_files))
        # [轮77] 仅当**没有失败文件**时写缓存：失败清单意味着这次加载不完整，
        # 缓存它会把这些因子在整个进程生命周期内静默锁死为缺失。
        if not self.failed_files and self.loaded_factors:
            try:
                if len(_DISCOVERY_CACHE) >= _DISCOVERY_CACHE_MAX:
                    _DISCOVERY_CACHE.clear()
                _DISCOVERY_CACHE[_key] = (_sig, dict(self.loaded_factors))
            except Exception as _cache_err:      # noqa: BLE001
                logger.debug("[FactorLoader] 写目录指纹缓存失败(非致命): %s", _cache_err)
        return count
    
    def _check_zero_load(self, count: int, scanned_py: int) -> None:
        """[§65] 零加载告警：扫了文件却一个都没注册，几乎必然是**模块身份分裂**。

        实测（`_audit_ml/Z163`）：进程同时通过 `backend/` 与仓库根两条 sys.path
        导入同一份代码 ⇒ `BaseFactor` 存在**两个类对象**，而因子文件写的是
        `from backend.services.factor_engine.factor_base import BaseFactor`：
        用顶格身份（`services.*`）构造的 FactorLoader 会 `issubclass(...)=False`
        ⇒ **加载 0 个、失败 0 个**，然后 `startup.py` 打印「因子体系就绪: 0因子」。
        这类"扫到文件却零注册"必须显式告警，不能当成正常空目录。
        """
        if count == 0 and scanned_py > 0 and not self.failed_files:
            logger.warning(
                "[FactorLoader] 扫描了 %d 个因子文件却注册 0 个（且无导入失败）—— 疑似"
                "**模块身份分裂**：本进程同时存在 `backend.services.*` 与 `services.*` "
                "两套模块对象，BaseFactor 类不一致导致 issubclass 静默为假。"
                "请统一导入写法（见报告 §65 / 决策 P16）。本模块 __name__=%s",
                scanned_py, __name__,
            )

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
                    logger.warning(
                        "[FactorLoader] 前视因子跳过加载: %s (%s)", py_file.name, _detail,
                    )
                    continue
                if _verdict == "review":
                    logger.warning(
                        "[FactorLoader] 变量型 shift 待复核: %s (%s)", py_file.name, _detail,
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
                            logger.warning(
                                "[FactorLoader] 因子类加载失败 %s @ %s: %s", name, py_file.name, e,
                            )
                        
            except Exception as e:
                self.failed_files.append(py_file.name)
                logger.error("[FactorLoader] 因子文件导入失败 %s: %s", py_file.name, e)
        
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
                logger.warning("[FactorLoader] 因子信息读取失败 %s: %s", factor_id, e)
        
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
    logger.info(
        "[FactorLoader] 因子初始化完成: %d 个（失败 %d）",
        len(loader.loaded_factors), len(loader.failed_files),
    )
    return loader
