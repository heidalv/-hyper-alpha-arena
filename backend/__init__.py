"""
Backend package - 添加项目根目录到sys.path以支持相对导入
"""
import sys
import os

# 确保backend目录在Python路径中
_backend_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(_backend_dir)

if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# 也添加backend目录本身
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

# [P16 / §66 修复 2026-09-10] 上面的两次 sys.path 注入**正是**双模块身份的来源：
# 同一份代码可被 `services.x` 与 `backend.services.x` 两个包名导入 ⇒ 两套模块对象、
# `BaseFactor` 两个类、`issubclass` 静默为假（实测因子加载 0 个却报"就绪"）。
# 这里立刻装上身份重定向器：既保留所有既有导入写法，又保证拿到同一个模块对象。
try:
    from ._module_alias import install as _install_module_alias

    _install_module_alias()
except Exception as _alias_err:  # noqa: BLE001
    # 不能静默：身份统一失败本身就是"静默失效"的高危来源
    import logging as _logging

    _logging.getLogger(__name__).warning(
        "[ModuleAlias] 模块身份统一安装失败（双身份风险回归）: %s", _alias_err,
    )
