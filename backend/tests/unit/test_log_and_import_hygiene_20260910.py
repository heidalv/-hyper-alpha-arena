# -*- coding: utf-8 -*-
"""[§75 执行 2026-09-10] 两条"日志/导入卫生"契约测试。

① **测试进程不得写生产日志**：`backend.main._bootstrap_logging()` 在 import 期就挂
   `logs/backend.log`(20MB×10) —— 任何 import `backend.main` 的测试都会把**合成**日志
   写进生产日志（实测：用例 `_resolve_reentry_cooldown(None,"45")` 的告警把我误判成
   "环境里真设了 REENTRY_COOLDOWN_SEC=45"），并且可能触发真实的 20MB 轮转。
   修法：检测到 pytest ⇒ 跳过文件 handler。

② **别名重定向不得劫持第三方模块**：`backend/.venv/.../onnxruntime/...` 有
   `from utils import ...`（指它自己的 utils）；若被重定向到 `backend.utils`，
   第三方会静默拿到错误模块。修法：调用方在 site-packages/.venv 下则不重定向。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import backend._module_alias as ma  # noqa: E402


def test_test_process_does_not_attach_production_file_handler():
    """在本 pytest 进程里 import backend.main 后，根 logger 不应有生产文件 handler。"""
    import importlib

    main = importlib.import_module("backend.main")
    main._bootstrap_logging()  # 幂等；测试进程应直接返回
    file_handlers = [h for h in logging.getLogger().handlers
                     if getattr(h, "_hyper_alpha_arena_file", False)]
    assert file_handlers == [], (
        f"测试进程挂了生产文件 handler: {file_handlers}（会污染 logs/backend.log）"
    )


def test_guard_exists_in_source():
    """源码级护栏：pytest 检测分支必须在挂文件 handler 之前。"""
    src = (ROOT / "backend/main.py").read_text(encoding="utf-8", errors="replace")
    i_guard = src.index("PYTEST_CURRENT_TEST")
    i_handler = src.index('_open_rotating("backend.log"')
    assert i_guard < i_handler, "pytest 守卫必须在文件 handler 之前"


def test_third_party_caller_detection():
    """第三方调用方判定：site-packages/.venv ⇒ True；仓库内代码 ⇒ False。"""
    assert ma.caller_is_third_party(
        r"D:\001Alpha\Hyper-Alpha-Arena\backend\.venv\Lib\site-packages\onnxruntime\x.py"
    ) is True
    assert ma.caller_is_third_party("/usr/lib/python3/dist-packages/foo/bar.py") is True
    assert ma.caller_is_third_party(
        r"D:\001Alpha\Hyper-Alpha-Arena\backend\services\factor_engine\base_factors.py"
    ) is False
    assert ma.caller_is_third_party("") is False


def test_hijack_judgement_is_precise(tmp_path):
    """**精确**判断：只有第三方目录里真有同名模块才算"会被劫持"。

    这条很重要 —— 初版只按"调用方在 site-packages"就放行，结果把 pytest 场景下的
    第一方映射也关掉了（双身份回归，`test_module_identity_unification` 立刻变红）。
    """
    (tmp_path / "utils.py").write_text("x = 1", encoding="utf-8")
    assert ma.caller_has_own_module(str(tmp_path / "pkg_mod.py"), "utils") is True
    assert ma.caller_has_own_module(str(tmp_path / "pkg_mod.py"), "services") is False
    assert ma.caller_has_own_module("", "utils") is False


def test_alias_roots_exclude_third_party_collision_names():
    """[§75] `utils`/`models` 必须**不在**别名白名单里：第三方（onnxruntime）自带同名模块。"""
    assert "utils" not in ma.ALIAS_ROOTS, "utils 会劫持第三方 `from utils import ...`"
    assert "models" not in ma.ALIAS_ROOTS, "models 会劫持第三方 `from models... import ...`"
    assert set(ma.EXCLUDED_ROOTS_FOR_THIRD_PARTY) == {"utils", "models"}
    # 高价值根名仍在（services/config 是 P16 的主战场）
    assert "services" in ma.ALIAS_ROOTS and "config" in ma.ALIAS_ROOTS


def test_frame_based_guard_is_conservative():
    """兜底判定（若未来把通用根名加回白名单）：必须同时满足"第三方"+"自带同名模块"。"""
    import inspect

    src = inspect.getsource(ma._AliasFinder.find_spec)
    assert "caller_is_third_party" in src and "caller_has_own_module" in src
    assert "EXCLUDED_ROOTS_FOR_THIRD_PARTY" in src


def test_first_party_mapping_still_works():
    """第三方放行不得影响第一方映射：本进程里 `services.*` 与 `backend.services.*` 仍同一对象。"""
    import importlib

    a = importlib.import_module("services.factor_engine.factor_base")
    b = importlib.import_module("backend.services.factor_engine.factor_base")
    assert a is b
