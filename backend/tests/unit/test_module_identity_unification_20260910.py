# -*- coding: utf-8 -*-
"""[2026-09-10 P16 / §66] 模块身份统一：`services.*` 与 `backend.services.*` 必须是**同一个对象**。

背景（§65 实证）：`backend/__init__.py` 把仓库根与 `backend/` 都塞进 `sys.path`，
同一份代码因此有两套模块对象 ⇒ `BaseFactor` 是两个类、`issubclass()` 跨身份静默为假、
`startup.py` 用裸路径构造的 loader **扫 157 文件注册 0 个却打印"就绪"**、
`decay_monitor`/`scheduler`/`price_cache` 等模块级单例各持一份状态。

修复（§66）：
  A. 机械改写 —— 生产代码里的非限定导入统一成 `backend.*`（78 文件 / 276 行，可还原）；
  B. 兜底 —— `backend/__init__` 安装 `_module_alias` 重定向器，任何遗留/新增的裸导入
     都解析到同一个对象，并在 `backend.log` 里留 WARNING（静默 → 可见）。

本测试锁定四条不变量：
  1. 两种写法导入同一模块、同一类；
  2. `split_report()` 必须为空（没有任何"同名不同对象"的模块）；
  3. 生产代码里**不得再出现**非限定导入语句（改写完整性；新增即变红）；
  4. 重定向器必须已安装，且 `backend/__init__.py` 负责安装它。
"""
from __future__ import annotations

import ast
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
BE = ROOT / "backend"
sys.path.insert(0, str(ROOT))
sys.path.append(str(BE))

import backend._module_alias as ma  # noqa: E402  （导入即完成安装）

# 覆盖 §65.3 线上双身份清单里的模块 + settings 类模块
IDENTITY_TARGETS = [
    "services.factor_engine.factor_base",
    "services.factor_engine.factor_loader",
    "services.factor_engine.factor_registry",
    "services.factor_engine.base_factors",
    "services.factor_engine.factor_decay_monitor",
    "services.factor_engine.midlong_cold_pool",
    "services.price_cache",
    "services.scheduler",
    "services.onchain_data_collector",
    "services.market_flow_collector",
    "services.signal_detection_service",
    "services.kline_cache_service",
    "services.unified_data_pool",
    "config.settings",
    # 注意：不再包含裸 `utils`/`models` —— §75 起这两个根名**刻意不纳入别名白名单**
    # （第三方 onnxruntime 自带同名模块并裸导入，重定向会劫持它）。
]

EXCLUDE_PARTS = ("__pycache__", "_ai_gen_archive", "_ai_gen_quarantine", "_pytest_tmp",
                 ".venv", "site-packages", "node_modules")
EXCLUDE_PREFIXES = (str(BE / "factor_engine") + os.sep,)  # 死树（§64.4），不参与


def _bare_import_lines(py: Path) -> list[int]:
    src = py.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    lines = src.splitlines()
    out: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module and node.module.split(".", 1)[0] in ma.ALIAS_ROOTS:
                out.append(node.lineno)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] in ma.ALIAS_ROOTS:
                    out.append(node.lineno)
                    break
    return [ln for ln in out if ln <= len(lines)]


@pytest.mark.parametrize("name", IDENTITY_TARGETS)
def test_import_spellings_resolve_to_same_object(name):
    # 导入机器在加载途中可能留下"同名不同对象"的临时副本（§66 已说明）；
    # repair() 是随修复一起交付的归一化步骤，先归一化再断言。
    ma.repair()
    bare = importlib.import_module(name)
    pkg = importlib.import_module("backend." + name)
    assert bare is pkg, f"{name} 仍是两套模块对象（双身份回归）"


def test_factor_classes_are_identical_across_spellings():
    # 导入机器在加载过程中会临时改写 sys.modules（None 占位 / 写入自造对象），
    # 交错导入时可能留下"同名不同对象"的临时副本 —— repair() 是随修复一起交付的
    # 归一化步骤（install() 同样会调用它），先归一化再断言规范对象一致。
    ma.repair()
    fb_a = importlib.import_module("services.factor_engine.factor_base")
    fb_b = importlib.import_module("backend.services.factor_engine.factor_base")
    assert fb_a.BaseFactor is fb_b.BaseFactor, "BaseFactor 跨身份不是同一个类 ⇒ issubclass 会静默为假"
    fl_a = importlib.import_module("services.factor_engine.factor_loader")
    fl_b = importlib.import_module("backend.services.factor_engine.factor_loader")
    assert fl_a.FactorLoader is fl_b.FactorLoader


def test_factor_loading_works_end_to_end_in_clean_interpreter():
    """最有意义的断言：按**真实启动路径**（import backend.main）在干净解释器里跑一遍。

    为什么必须用子进程：pytest 进程会以各种顺序反复导入/重入模块，可能留下
    "同名模块被重新执行"的历史副本，而某些模块早前已把旧的类对象捕获进自己的
    全局命名空间（Python 导入机器的固有行为）。真实运行路径是 uvicorn 启动的
    单进程，这里用子进程复现它，并同时断言：
      * 因子引擎容量正常（修复前这种分裂会表现成"少了 154 个因子"或加载 0 个）；
      * 启动期**没有**"疑似模块身份分裂"告警；
      * 启动期**没有**裸导入告警（证明改写完整、运行时不再走别名兜底）。
    """
    code = (
        "import os,sys,json,logging;"
        "sys.stdout.reconfigure(encoding='utf-8');"
        f"root=r'{ROOT}';sys.path.insert(0,root);sys.path.append(os.path.join(root,'backend'));"
        "os.chdir(root);"
        "recs=[];"
        "h=logging.Handler();h.emit=lambda r:recs.append(r.getMessage());"
        "logging.getLogger().addHandler(h);logging.getLogger().setLevel(logging.INFO);"
        "import backend.main;"
        "import backend.services.factor_engine as fe;"
        "eng=getattr(fe,'factor_engine',None);"
        "msgs=' || '.join(recs);"
        "print(json.dumps({'factors':len(getattr(eng,'FACTORS',{}) or {}),"
        "'split_warn': '模块身份分裂' in msgs,"
        "'bare_warn': '检测到裸导入' in msgs}))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=900,
    )
    assert proc.returncode == 0, f"子进程失败: {proc.stderr[-800:]}"
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["factors"] >= 150, f"因子引擎容量异常: {payload['factors']}"
    assert payload["split_warn"] is False, "启动期出现身份分裂告警"
    assert payload["bare_warn"] is False, "运行时仍有裸导入（改写不完整）"


def test_no_module_splits_remain():
    # 主动再按两种写法导入一遍，确保比较覆盖到
    for name in IDENTITY_TARGETS:
        importlib.import_module(name)
        importlib.import_module("backend." + name)
    splits = ma.split_report()
    assert splits == {}, f"仍存在分裂模块: {splits}"


def test_production_code_has_no_bare_imports():
    """改写完整性：生产代码不得再出现非限定导入（新增即红，防止双身份回归）。"""
    offenders: list[str] = []
    for py in sorted(BE.rglob("*.py")):
        p = str(py)
        if any(part in p for part in EXCLUDE_PARTS) or any(p.startswith(x) for x in EXCLUDE_PREFIXES):
            continue
        for ln in _bare_import_lines(py):
            text = py.read_text(encoding="utf-8", errors="replace").splitlines()[ln - 1].strip()
            offenders.append(f"{py.relative_to(ROOT)}:{ln}: {text[:100]}")
    assert offenders == [], f"仍有非限定导入 {len(offenders)} 处: {offenders[:10]}"


def test_alias_finder_installed_by_backend_package():
    finder = [f for f in sys.meta_path if isinstance(f, ma._AliasFinder)]
    assert finder, "身份重定向器未安装（裸导入会再次产生双身份）"
    init_src = (BE / "__init__.py").read_text(encoding="utf-8")
    assert "_module_alias" in init_src and "install" in init_src, "backend/__init__.py 未安装重定向器"


def test_alias_roots_are_justified_and_not_third_party():
    """白名单不能吞掉第三方包：每个根名要么在 backend/ 下存在，要么明确不存在。"""
    for root in ma.ALIAS_ROOTS:
        assert (BE / root).exists() or (BE / f"{root}.py").exists(), f"{root} 在 backend/ 下不存在"
    # 与 site-packages 同名者必须显式排除（Z169 实测 alembic / schemas）
    third_party = []
    for p in sys.path:
        if "site-packages" not in p:
            continue
        for root in ma.ALIAS_ROOTS:
            if (Path(p) / root).exists() or (Path(p) / f"{root}.py").exists():
                third_party.append(root)
    assert third_party == [], f"白名单与第三方包冲突: {third_party}"
