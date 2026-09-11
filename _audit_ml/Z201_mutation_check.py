# -*- coding: utf-8 -*-
"""Z201：**变异测试** —— 证明今天的护栏"拆掉就会变红"（防止空断言/自我实现）。

对每个 (文件, 原片段, 变异片段, 目标测试)：
  备份文件 → 注入变异 → 跑该测试（期望**失败**）→ 还原 → 校验哈希一致。

用法：python _audit_ml/Z201_mutation_check.py
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
PY = str(ROOT / ".venv/Scripts/python.exe")

MUTATIONS = [
    {
        "name": "① 因子零注册告警（§65.5）",
        "file": ROOT / "backend/services/factor_engine/factor_loader.py",
        "old": "        self._check_zero_load(count, scanned_py)",
        "new": "        pass  # [MUTATION] 拆掉零注册告警",
        "test": "backend/tests/unit/test_runtime_diagnostics_use_logger_20260910.py::test_zero_load_guard_is_wired_into_discover",
    },
    {
        "name": "② P17 判据 stale 自愈（§73.4）",
        "file": ROOT / "backend/services/risk_management/portfolio_budget.py",
        "old": "            if dd_sigma is not None and dd_sigma > sigma_cap and not dd_is_stale:",
        "new": "            if dd_sigma is not None and dd_sigma > sigma_cap:  # [MUTATION] 去掉 stale 豁免",
        "test": "backend/tests/unit/test_dd_sigma_reject_semantics_20260910.py::test_stale_metric_does_not_reject",
    },
    {
        "name": "③ 回撤拒单可见性（§72.5）",
        "file": ROOT / "backend/services/risk_management/portfolio_budget.py",
        "old": "                self._warn_dd_reject_without_freeze(strategy, sym, dd_sigma, sigma_cap, worst)",
        "new": "                pass  # [MUTATION] 拆掉可见性告警",
        "test": "backend/tests/unit/test_dd_sigma_reject_semantics_20260910.py::test_stuck_fuse_is_visible_when_freeze_disabled",
    },
    {
        "name": "④ 测试进程不写生产日志（§75.1）",
        "file": ROOT / "backend/main.py",
        "old": '    if _os_log_init.getenv("PYTEST_CURRENT_TEST") or "pytest" in sys.modules:',
        "new": '    if False:  # [MUTATION] 去掉 pytest 守卫',
        "test": "backend/tests/unit/test_log_and_import_hygiene_20260910.py::test_test_process_does_not_attach_production_file_handler",
    },
]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def run_test(test_id: str) -> int:
    proc = subprocess.run(
        [PY, "-m", "pytest", test_id, "-q", "--no-header",
         "--basetemp=" + str(ROOT / "data/_pytest_tmp")],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900,
    )
    return proc.returncode


print(f"变异测试（{len(MUTATIONS)} 项；每项期望：注入后**失败**、还原后**通过**）\n")
all_ok = True
for m in MUTATIONS:
    f: Path = m["file"]
    backup = f.with_suffix(f.suffix + ".mutationbak")
    original_hash = sha(f)
    shutil.copy2(f, backup)
    try:
        src = f.read_text(encoding="utf-8")
        if m["old"] not in src:
            print(f"❌ {m['name']}: 找不到待变异片段（护栏可能已被改动，需人工复核）")
            all_ok = False
            continue
        f.write_text(src.replace(m["old"], m["new"], 1), encoding="utf-8")
        rc_mut = run_test(m["test"])
    finally:
        shutil.copy2(backup, f)
        backup.unlink(missing_ok=True)
    restored_hash = sha(f)
    rc_after = run_test(m["test"])
    ok = (rc_mut != 0) and (rc_after == 0) and (original_hash == restored_hash)
    all_ok &= ok
    print(f"{'✅' if ok else '❌'} {m['name']}")
    print(f"     注入后 rc={rc_mut}（期望≠0）｜还原后 rc={rc_after}（期望=0）｜文件哈希还原一致={original_hash == restored_hash}")

print("\n=== 结论 ===")
print("✅ 全部护栏都能变红——测试不是空断言" if all_ok else "❌ 有护栏拆掉也不变红（需修正测试）")
