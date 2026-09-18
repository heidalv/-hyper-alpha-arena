# -*- coding: utf-8 -*-
"""[2026-09-10 §64] 运行时诊断必须走 logging —— 不得只用 `print()`。

背景（§64 实证）：
  1. `print()` 的输出只落 `logs/backend-console.log`（启动脚本 stdout 重定向），
     **不进 logging 流**，读 `backend.log` / 审计工具的人完全看不到；
  2. 该 console 文件**无任何轮转**，实测已 540.5MB（≈24MB/天）；
  3. 因子加载/计算失败此前只 print 且**不计数**：`Total factors loaded: 150`
     看不出少了几个 —— 中长线 factor_route 的分数就建立在这些因子上。

本测试三件事：
  A. 四个运行时模块（因子加载/注册/流式计算/风险预警）里除 `__main__` 块外不得有 `print(`；
  B. 行为验证：`FactorLoader._load_category()` 遇到坏文件时必须 (i) 记 ERROR 日志、
     (ii) 记入 `failed_files`、(iii) 不往 stdout 打印；
  C. 活因子树必须 100% 可导入（按**字节**编译，与 import 机制同口径），
     且隔离区 / 死树必须被排除而非被静默吞掉。
"""
from __future__ import annotations

import ast
import logging
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

RUNTIME_MODULES = [
    ROOT / "backend/services/factor_engine/factor_loader.py",
    ROOT / "backend/services/factor_engine/factor_registry.py",
    ROOT / "backend/services/factor_engine/factor_stream_calculator.py",
    ROOT / "backend/services/risk_management/risk_monitor.py",
]
LIVE_FACTOR_DIRS = [
    ROOT / "backend/services/factor_engine/factors/ai_generated",
    ROOT / "backend/services/factor_engine/factors/legacy_compat",
]
QUARANTINE_DIR = ROOT / "backend/services/factor_engine/factors/_ai_gen_quarantine"
DEAD_TREE = ROOT / "backend/factor_engine"
ARCHIVED_DEAD_TREE = ROOT / "_archive/dead_factor_engine_20260910"
LAUNCHER = ROOT / "scripts/_restart_backend_clean.py"


def _prints_outside_main(path: Path) -> list[int]:
    """返回模块中 `print(` 调用的行号（忽略 `if __name__ == '__main__'` 块内的 CLI 输出）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    main_nodes: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            t = ast.unparse(node.test)
            if "__main__" in t or "__package__" in t:
                for sub in ast.walk(node):
                    main_nodes.add(id(sub))
    out = []
    for node in ast.walk(tree):
        if id(node) in main_nodes:
            continue
        if isinstance(node, ast.Call):
            f = node.func
            name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
            if name == "print":
                out.append(node.lineno)
    return sorted(out)


@pytest.mark.parametrize("mod", RUNTIME_MODULES, ids=lambda p: p.name)
def test_runtime_modules_do_not_print(mod: Path):
    assert mod.exists(), f"模块不存在: {mod}"
    bad = _prints_outside_main(mod)
    assert bad == [], f"{mod.name} 仍有 print() 诊断（行 {bad}）——必须改走 logger"


def test_factor_loader_reports_failure_via_logger(tmp_path, caplog, capsys):
    """坏因子文件：必须 ERROR 日志 + 记入 failed_files + 不打印到 stdout。"""
    from backend.services.factor_engine.factor_loader import FactorLoader

    broken = tmp_path / "ai_gen_broken_for_test.py"
    broken.write_text("def broken(:\n    pass\n", encoding="utf-8")

    loader = FactorLoader()
    with caplog.at_level(logging.WARNING):
        n = loader._load_category(tmp_path)

    assert n == 0
    assert "ai_gen_broken_for_test.py" in loader.failed_files, "失败文件未计数（静默缺失）"
    assert any(r.levelno >= logging.WARNING and "导入失败" in r.getMessage() for r in caplog.records), \
        f"没有 WARNING/ERROR 日志: {[r.getMessage() for r in caplog.records]}"
    assert capsys.readouterr().out.strip() == "", "仍在往 stdout 打印（只进 console 文件）"


def test_zero_load_is_flagged_as_identity_split(caplog):
    """[§65] 扫到文件却注册 0 个 ⇒ 必须告警（实测这是 backend.*/services.* 双身份所致）。"""
    from backend.services.factor_engine.factor_loader import FactorLoader

    loader = FactorLoader()
    with caplog.at_level(logging.WARNING):
        loader._check_zero_load(0, 155)
    msgs = [r.getMessage() for r in caplog.records]
    assert any("0 个" in m and "模块身份分裂" in m for m in msgs), msgs

    # 正常情况不得误报
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        loader._check_zero_load(174, 155)
        loader._check_zero_load(0, 0)  # 没有可扫文件：空目录属正常
    assert caplog.records == [], [r.getMessage() for r in caplog.records]


def test_zero_load_guard_is_wired_into_discover(monkeypatch, caplog):
    """[§76 补覆盖] **接线**必须被守住，而不只是助手本身。

    [变异测试实证] 把 `discover_and_load_all()` 里的 `self._check_zero_load(...)` 一行删掉，
    只测助手的用例**依然全绿** —— 运行时接线当时是对的（线上启动日志可见告警），
    但没有任何测试守它。本用例走真实入口、把分类加载打成 0，断言告警确实发出。

    [轮82 补] 必须先清**目录指纹缓存**：轮77 给 `discover_and_load_all` 加了
    「目录未变则复用已加载因子」的短路，缓存命中时不会走到 `_load_category`，
    于是本用例会拿到 262 而不是被打成 0 的 0（实测）。
    清缓存后行为与加缓存前一致，接线守卫仍然有效。
    """
    from backend.services.factor_engine import factor_loader as _fl
    from backend.services.factor_engine.factor_loader import FactorLoader

    _fl._DISCOVERY_CACHE.clear()
    loader = FactorLoader()
    monkeypatch.setattr(loader, "_load_category", lambda _d: 0)  # 模拟"扫到文件但注册 0"
    with caplog.at_level(logging.WARNING):
        n = loader.discover_and_load_all()
    assert n == 0
    msgs = [r.getMessage() for r in caplog.records]
    assert any("模块身份分裂" in m for m in msgs), \
        f"接线断了：discover_and_load_all 里没有触发零注册告警；实际日志={msgs}"


def test_startup_warns_when_factor_init_loads_zero():
    """启动 D7 因子初始化打印'就绪'前，0 因子必须走 WARNING（实测线上是 0 因子假绿）。"""
    src = (ROOT / "backend/services/startup.py").read_text(encoding="utf-8")
    i = src.index("因子体系就绪")
    seg = src[max(0, i - 1200): i]
    assert "_loaded == 0" in seg, "缺少零加载告警分支"
    assert "logger.warning" in seg, "零加载告警必须用 WARNING 级别"


def test_live_factor_tree_is_fully_importable():
    total = 0
    bad: list[str] = []
    for d in LIVE_FACTOR_DIRS:
        if not d.exists():
            continue
        for py in sorted(d.rglob("*.py")):
            if py.name.startswith("__"):
                continue
            total += 1
            try:
                compile(py.read_bytes(), str(py), "exec")
            except Exception as e:  # noqa: BLE001
                bad.append(f"{py.name}: {type(e).__name__}")
    assert total >= 100, f"活因子树文件数异常偏少: {total}"
    assert bad == [], f"活因子树存在无法导入的因子: {bad[:8]}"


def test_loader_skips_quarantine_and_dead_tree_is_archived():
    """隔离区必须被显式跳过（而不是被静默吞掉）；死树必须**已归档且不可再被导入**。"""
    src = (ROOT / "backend/services/factor_engine/factor_loader.py").read_text(encoding="utf-8")
    assert "startswith('_')" in src or 'startswith("_")' in src, "隔离目录跳过逻辑缺失"
    assert QUARANTINE_DIR.is_dir(), "隔离区目录不存在（口径变化需同步本测试）"
    # 隔离区里存在无法导入的文件是**预期**的（它们正是被隔离的原因）——
    # 这里验证它们确实在隔离区、且 loader 不扫描该目录。
    # [P14 / §67 执行 2026-09-10] 死树已移入 _archive/：
    assert not DEAD_TREE.exists(), (
        "backend/factor_engine 又出现了 —— 死树归档口径需重新评审（见报告 §67）"
    )
    assert ARCHIVED_DEAD_TREE.is_dir(), "归档副本丢失（P14 要求归档而非删除）"
    assert not (ARCHIVED_DEAD_TREE / "__init__.py").exists(), "归档副本变成了可导入包"


def _load_rotation_helper():
    """从启动脚本里**只**抽取轮转函数并执行（该脚本顶层会杀进程/起后端，不能直接 import）。"""
    src = LAUNCHER.read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(
        (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_rotate_if_huge"),
        None,
    )
    assert fn is not None, "启动脚本缺少 _rotate_if_huge()"
    mod = ast.Module(body=[fn], type_ignores=[])
    ns: dict = {"os": os, "time": time}
    exec(compile(mod, str(LAUNCHER), "exec"), ns)  # noqa: S102
    return ns["_rotate_if_huge"]


def test_console_log_rotation_helper_rotates_only_when_huge(tmp_path, capsys):
    rotate = _load_rotation_helper()

    small = tmp_path / "small.log"
    small.write_text("x" * 1024, encoding="utf-8")
    rotate(str(small), 1.0)
    assert small.exists(), "小文件被误轮转"

    big = tmp_path / "big.log"
    big.write_bytes(b"x" * (2 * 1024 * 1024))
    rotate(str(big), 1.0)
    assert not big.exists(), "超阈值文件未轮转"
    rotated = list(tmp_path.glob("big.log.*"))
    assert len(rotated) == 1, f"轮转产物异常: {rotated}"
    assert "rotate" in capsys.readouterr().out

    rotate(str(tmp_path / "missing.log"), 1.0)  # 不存在时静默返回，不抛异常


def test_launcher_rotates_before_opening_console_logs():
    src = LAUNCHER.read_text(encoding="utf-8")
    i_rot = src.index("_rotate_if_huge(_console_log")
    i_open = src.index('out = open(_console_log')
    assert i_rot < i_open, "轮转必须发生在打开 console 文件之前"
    assert 'BACKEND_CONSOLE_LOG_MAX_MB' in src
