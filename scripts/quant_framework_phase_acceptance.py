"""
§9.2 量化框架分阶段验收脚本。

用法（项目根目录）：
    python scripts/quant_framework_phase_acceptance.py [--phase all|1|2|2.5|3|3.5]

每阶段执行：模块可导入检查 + 关键 pytest 子集 + 环境开关核验。
退出码 0 = 全部 PASS，1 = 存在 FAIL。
"""
from __future__ import annotations

import argparse
import importlib
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT, ".env"))


def _ok(msg: str) -> None:
    print(f"  PASS  {msg}")


def _fail(msg: str) -> None:
    print(f"  FAIL  {msg}")


def _import_check(module: str) -> bool:
    try:
        importlib.import_module(module)
        _ok(f"import {module}")
        return True
    except Exception as exc:
        _fail(f"import {module}: {exc}")
        return False


def _env_check(key: str, expected: str | None = None) -> bool:
    val = os.environ.get(key, "")
    if expected is None:
        if val:
            _ok(f"env {key}={val}")
            return True
        _fail(f"env {key} 未设置")
        return False
    ok = val.lower() == expected.lower()
    ( _ok if ok else _fail)(f"env {key}={val} (期望 {expected})")
    return ok


def _pytest_subset(paths: list[str], label: str) -> bool:
    cmd = [sys.executable, "-m", "pytest", *paths, "-q", "--tb=no"]
    print(f"  RUN   pytest {label} ({len(paths)} files)")
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    tail = (proc.stdout or proc.stderr or "").strip().splitlines()[-1:] 
    line = tail[0] if tail else f"exit={proc.returncode}"
    if proc.returncode == 0:
        _ok(f"pytest {label}: {line}")
        return True
    _fail(f"pytest {label}: {line}")
    return False


PHASE_CHECKS = {
    "1": {
        "imports": [
            "backend.services.backtest_engine.slippage",
            "backend.services.backtest_engine.overfitting_metrics",
            "backend.services.backtest_engine.walk_forward",
        ],
        "pytest": [
            "tests/backend/unit/test_framework_upgrade_2026_07_09.py",
        ],
        "env": [],
    },
    "2": {
        "imports": [
            "backend.services.ml.training_pipeline",
            "backend.services.ml.activation_service",
            "backend.services.factor_engine.learned_weighting",
        ],
        "pytest": [
            "tests/backend/unit/test_ml_activation_wiring.py",
            "tests/backend/unit/test_factor_pipeline.py",
        ],
        "env": [("ML_PIPELINE_ENABLED", None)],
    },
    "2.5": {
        "imports": [
            "backend.services.learning_core.continual_learning",
            "qaa.knowledge.reranker",
        ],
        "pytest": [
            "tests/backend/unit/test_opencode_layer.py",
        ],
        "env": [],
    },
    "3": {
        "imports": [
            "backend.services.full_auto.orchestrator",
            "backend.services.event_sourcing.phase3",
            "backend.services.event_sourcing.phase4",
            "backend.services.promotion_gate_service",
            "backend.services.resource_guard",
        ],
        "pytest": [
            "tests/backend/unit/test_event_sourcing_phase2.py",
            "tests/backend/unit/test_event_sourcing_phase3.py",
            "tests/backend/unit/test_event_sourcing_phase4.py",
            "tests/backend/unit/test_full_auto_loop_c2_golden.py",
            "tests/backend/unit/test_framework_delivery_2026_07_09.py",
            "tests/backend/unit/test_promotion_scan_wiring.py",
        ],
        "env": [("EVENT_SOURCING_ENABLED", None)],
    },
    "3.5": {
        "imports": [
            "backend.services.learning_core.pbo_audit",
            "backend.services.learning_core.map_elites_archive",
            "backend.services.learning_core.cmaes_optimizer",
        ],
        "pytest": [
            "tests/backend/unit/test_framework_upgrade_2026_07_09.py",
        ],
        "env": [("PBO_AUDIT_ENABLED", None)],
    },
}


def run_phase(phase: str) -> bool:
    spec = PHASE_CHECKS.get(phase)
    if not spec:
        _fail(f"未知阶段 {phase}")
        return False
    print(f"\n=== 阶段 {phase} 验收 ===")
    ok = True
    for mod in spec.get("imports", []):
        ok = _import_check(mod) and ok
    for key, exp in spec.get("env", []):
        ok = _env_check(key, exp) and ok
    paths = spec.get("pytest", [])
    if paths:
        ok = _pytest_subset(paths, f"phase-{phase}") and ok
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="§9.2 量化框架分阶段验收")
    parser.add_argument("--phase", default="all", help="1|2|2.5|3|3.5|all")
    args = parser.parse_args()

    try:
        from backend.config.framework_rollout import apply_aggressive_rollout
        apply_aggressive_rollout()
        _ok("framework_rollout 已注入")
    except Exception as exc:
        _fail(f"framework_rollout: {exc}")

    phases = list(PHASE_CHECKS.keys()) if args.phase == "all" else [args.phase]
    all_ok = True
    for p in phases:
        all_ok = run_phase(p) and all_ok

    print("\n" + ("=== 全部 PASS ===" if all_ok else "=== 存在 FAIL ==="))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
