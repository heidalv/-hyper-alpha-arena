# -*- coding: utf-8 -*-
"""[P16 / §66] 模块身份统一（消除 `services.*` 与 `backend.services.*` 双身份）。

## 问题（§65 实证）
`backend/__init__.py` 把**项目根**与 **backend/** 同时插入 `sys.path`，于是同一份代码
可以按两个包名导入，形成**两套互不相干的模块对象**：
  * `services.factor_engine.factor_base is backend.services.factor_engine.factor_base` → False；
  * `BaseFactor` 是两个类 ⇒ 跨身份的 `issubclass()` **静默为假**；
  * 因子文件写的是 `backend.services...`，而 `startup.py` 用 `services...` 构造 loader
    ⇒ 扫 157 个文件、注册 **0** 个、失败 **0** 个，然后打印「因子体系就绪: 0因子」；
  * 线上日志（logger 标签）显示至少 8 个模块**两套身份同时运行**：
    factor_registry / scheduler / base_factors / decay_monitor / onchain_data_collector /
    derivatives_analytics_service / learning.backend_registry / factor_loader；
  * 模块级单例/缓存（decay_monitor、price_cache、market_flow_collector…）各持一份状态。

## 修法（不改任何导入语句、可一键卸载）
在 `sys.meta_path` 头部装一个**重定向 finder**：凡是以白名单根名开头的导入，
一律解析到 `backend.<同名>` 并**复用同一个模块对象**（`sys.modules` 双向登记）。
这样：
  * 既有的 `from services.x import y` 写法**无需修改**，但拿到的是同一个对象；
  * 未来的新代码无论写哪种写法都不会再分裂；
  * 白名单外的名字（含第三方包）完全不受影响（Z169 已核对无同名第三方包）。

白名单由 `_audit_ml/Z169_alias_allowlist.py` 实测得出：只列**生产代码里真的出现过
非限定导入**、且**与 site-packages 无同名冲突**的名字（`alembic`/`schemas` 因此被排除）。
"""
from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

BACKEND_PREFIX = "backend."

#: 归一化/重定向时**跳过第三方调用方**：`backend/.venv/.../onnxruntime/...` 里有
#: `from utils import ...` / `from models... import ...` 这类"相对自身包"的写法，
#: 若被一并重定向到 `backend.utils` 就会**劫持第三方模块**（实测 §75）。
#: 判定口径：调用方文件路径包含 site-packages / .venv / dist-packages ⇒ 不重定向。
_THIRD_PARTY_MARKERS = ("site-packages", "dist-packages", ".venv", "node_modules")


def caller_is_third_party(caller_file: str) -> bool:
    """调用方是否属于第三方（site-packages/.venv）。纯函数，便于契约测试。"""
    p = (caller_file or "").replace("\\", "/").lower()
    return any(m in p for m in _THIRD_PARTY_MARKERS)


def caller_has_own_module(caller_file: str, root: str) -> bool:
    """调用方所在目录里是否存在**它自己**的同名模块（`<dir>/<root>.py|/`）。

    这是"劫持"的**精确**判据：只有当第三方包里真有 `utils.py` 之类的同名模块时，
    把 `import utils` 重定向到 `backend.utils` 才会给它错误模块。
    单纯"调用方在 site-packages"并不足以判定（很多库的导入由第三方插件发起，
    若一律放行会**关掉第一方映射**，导致双身份回归）。

    [§75 自查] 初版这里忘了 `from pathlib import Path`，而下面的宽 except 把 `NameError`
    **静默吞掉** ⇒ 函数恒返回 False（"精确判据"实际从未生效）。现在只捕真正的 IO 类异常，
    其余异常显式告警 —— 与本项目一直在审计的"fail-open 静默"保持一致纪律。
    """
    if not caller_file or not root:
        return False
    try:
        base = Path(caller_file).resolve().parent
    except OSError as exc:  # 路径不可解析才允许退化
        logger.warning("[ModuleAlias] 调用方路径解析失败(%s): %s", caller_file, exc)
        return False
    return (base / f"{root}.py").exists() or (base / root).is_dir()


def _first_party_frame():
    """取 meta_path 之前第一个非 importlib 帧（用于判断调用方来源）。"""
    f = sys._getframe(3)
    while f is not None:
        fn = f.f_code.co_filename or ""
        if "importlib" in fn or fn.startswith("<frozen") or "fromlist" in f.f_code.co_name:
            f = f.f_back
            continue
        return fn
    return ""

#: 需要统一身份的顶格根名（实测：括号内为生产代码非限定导入行数）
ALIAS_ROOTS: tuple = (
    "services",              # 263
    "config",                # 25
    "repositories",          # 5
    "version",               # 4
    "api",                   # 3
    "workers",               # 1
    "factors",               # 1
    "migrate_to_postgresql",  # 1
)
# [§75 执行 2026-09-10] **刻意不含 `utils` / `models`**：
# 实测 `backend/.venv` 里有第三方代码自带同名模块并做裸导入
#   * `onnxruntime/transformers/models/bart/export.py: from utils import (...)`
#   * `onnxruntime/transformers/models/llama/convert_to_onnx.py: from models... import ...`
# 若把这两个根名也纳入别名，第三方会**静默拿到 backend.utils / backend.models**（错误模块）。
# 它们的裸导入已由 P16 改写清零，且有"生产代码零裸导入"的契约测试兜底，
# 因此这里选择"**宁可少兜底，不可劫持第三方**"。
EXCLUDED_ROOTS_FOR_THIRD_PARTY = ("utils", "models")


class _AliasLoader(importlib.abc.Loader):
    """把 `X.Y` 的导入结果替换为 `backend.X.Y` 的**同一模块对象**。"""

    def __init__(self, alias: str, target: str) -> None:
        self.alias = alias
        self.target = target

    def create_module(self, spec):  # type: ignore[override]
        module = sys.modules.get(self.target) or importlib.import_module(self.target)
        sys.modules[spec.name] = module
        return module

    def exec_module(self, module) -> None:  # type: ignore[override]
        # 导入机器在 exec 前会把 sys.modules[alias] 写回它自己造的那个对象；
        # 这里再**归一化**一次，保证别名键始终指向 `backend.*` 的规范对象
        # （否则会出现"同名不同对象"的残留副本：实测 Z172/Z173/契约测试）。
        canonical = sys.modules.get(self.target)
        if canonical is not None and canonical is not module:
            sys.modules[self.alias] = canonical
        return None


class _AliasFinder(importlib.abc.MetaPathFinder):
    """顶格名 → `backend.*` 的重定向器（只处理白名单根名）。"""

    def __init__(self, debug: Optional[bool] = None) -> None:
        import os as _os

        self.debug = (
            bool(_os.getenv("MODULE_ALIAS_DEBUG"))
            if debug is None
            else debug
        )
        self._warned: set = set()

    def _dbg(self, msg: str) -> None:
        if self.debug:
            sys.stderr.write(f"[ModuleAlias] {msg}\n")
            import os as _os

            if _os.getenv("MODULE_ALIAS_STACK"):
                import traceback

                for ln in traceback.format_stack()[-8:-1]:
                    sys.stderr.write("      | " + ln.strip().replace("\n", " ")[:180] + "\n")

    def _warn_leftover(self, fullname: str) -> None:
        """[P16] 裸导入本该在 2026-09-10 的改写中被清零（78 文件 / 276 行）。

        仍有裸导入说明新增代码用了旧写法 —— 这里是**唯一**的可见信号，故用 WARNING，
        且每个根名只报一次（避免刷屏）。
        """
        root = fullname.split(".", 1)[0]
        if root in self._warned:
            return
        self._warned.add(root)
        logger.warning(
            "[ModuleAlias] 检测到裸导入 `%s`（已重定向到 backend.%s，身份一致）——"
            "请改写成 `backend.%s...`（P16 已统一 78 文件/276 行）",
            fullname, fullname, fullname,
        )

    def find_spec(self, fullname, path=None, target=None):  # type: ignore[override]
        root = fullname.split(".", 1)[0]
        if root not in ALIAS_ROOTS or fullname.startswith(BACKEND_PREFIX):
            return None
        # [§75] 第三方调用方**且它自己有同名模块**时不重定向：
        # 例如 onnxruntime 的 `from utils import ...` 指的是它自己的 utils，
        # 重定向到 backend.utils 会静默给它一个**错误的模块**。
        # 注意判据要精确：只按"在 site-packages"放行会误关第一方映射（双身份回归）。
        # （`utils`/`models` 已从 ALIAS_ROOTS 移除；此处保留兜底判定以防未来再加回。）
        _caller = _first_party_frame()
        if root not in EXCLUDED_ROOTS_FOR_THIRD_PARTY and caller_is_third_party(_caller) \
                and caller_has_own_module(_caller, root):
            self._dbg(f"{fullname} <- 第三方自带同名模块({_caller[-60:]})，放行")
            return None
        self._warn_leftover(fullname)
        target_name = BACKEND_PREFIX + fullname
        existing = sys.modules.get(target_name)
        if existing is not None:
            prev = sys.modules.get(fullname)
            sys.modules[fullname] = existing
            self._dbg(f"{fullname} -> (已载入) {target_name}  覆盖旧对象={prev is not None and prev is not existing}")
            return importlib.util.spec_from_loader(fullname, _AliasLoader(fullname, target_name))
        try:
            if importlib.util.find_spec(target_name) is None:
                self._dbg(f"{fullname} -> 无对应 {target_name}，放行给标准查找器")
                return None
        except (ImportError, ModuleNotFoundError, ValueError) as e:
            self._dbg(f"{fullname} -> 目标解析失败 {type(e).__name__}，放行")
            return None
        self._dbg(f"{fullname} -> {target_name}（首次导入）")
        return importlib.util.spec_from_loader(fullname, _AliasLoader(fullname, target_name))


def _alias_loaded_modules() -> List[str]:
    """把**已经**以 `backend.*` 身份载入的模块，同步登记一份顶格别名。"""
    aliased: List[str] = []
    for name, module in list(sys.modules.items()):
        if module is None or not name.startswith(BACKEND_PREFIX):
            continue
        rest = name[len(BACKEND_PREFIX):]
        if not rest or rest.split(".", 1)[0] not in ALIAS_ROOTS:
            continue
        if sys.modules.get(rest) is module:
            continue
        sys.modules[rest] = module
        aliased.append(rest)
    return aliased


def repair() -> List[str]:
    """把别名键重新指向**规范对象**（`backend.*`），丢弃历史/竞态产生的副本。

    为什么需要：导入机器在加载过程中会临时改写 `sys.modules`（`None` 占位、写入
    它自己造的对象），当裸导入与 `backend.*` 导入交错发生时，别名键上可能残留一个
    "同名不同对象"的副本（实测 Z172/Z173/契约测试）。这些副本一旦被下游按
    `sys.modules[name]` 取用就会重新制造双身份，因此安装与自检时都要归一化。
    """
    fixed: List[str] = []
    for name, module in list(sys.modules.items()):
        if module is None or name.startswith(BACKEND_PREFIX) or "." not in name:
            continue
        if name.split(".", 1)[0] not in ALIAS_ROOTS:
            continue
        canonical = sys.modules.get(BACKEND_PREFIX + name)
        if canonical is not None and canonical is not module:
            sys.modules[name] = canonical
            fixed.append(name)
    return fixed


def install() -> Dict[str, object]:
    """安装重定向器（幂等）。返回诊断信息，便于启动日志与测试断言。"""
    already = any(isinstance(f, _AliasFinder) for f in sys.meta_path)
    if not already:
        sys.meta_path.insert(0, _AliasFinder())
    aliased = _alias_loaded_modules()
    repaired = repair()
    info = {
        "finder_installed": True,
        "newly_aliased": aliased,
        "repaired": repaired,
        "roots": list(ALIAS_ROOTS),
    }
    if aliased:
        logger.info("[ModuleAlias] 已统一 %d 个模块身份（顶格 ↔ backend.*）", len(aliased))
    if repaired:
        logger.warning("[ModuleAlias] 归一化 %d 个残留副本: %s", len(repaired), repaired[:8])
    return info


def split_report(*, fix: bool = True) -> Dict[str, str]:
    """找出仍然**分裂**的模块对（同一名字两个不同对象）——供自检与测试使用。

    `fix=True`（默认）先做一次 `repair()`：导入过程中的临时副本不应算作真实分裂。
    """
    if fix:
        repair()
    splits: Dict[str, str] = {}
    for name, module in list(sys.modules.items()):
        if module is None or name.startswith(BACKEND_PREFIX) or "." not in name:
            continue
        if name.split(".", 1)[0] not in ALIAS_ROOTS:
            continue
        twin = sys.modules.get(BACKEND_PREFIX + name)
        if twin is not None and twin is not module:
            splits[name] = f"{id(module)} != {id(twin)}"
    return splits
