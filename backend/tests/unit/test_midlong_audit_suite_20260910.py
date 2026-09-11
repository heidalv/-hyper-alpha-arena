# -*- coding: utf-8 -*-
"""[§76.5] 例行审计套件（`backend/scripts/midlong_audit_suite.py`）契约测试。

为什么需要：§23 里"每周重跑三个审计脚本"此前只是文字承诺、没有可执行入口；
本套件把它变成一条命令 + 落盘产物（`data/audit_reports/midlong_audit_*.json/.md`）。

锁定：①子项清单可枚举（`--list`）②每个子项指向的脚本**真实存在**（防止改名后静默空跑）
③产物目录与命名约定 ④失败即非零退出（可挂调度）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "backend/scripts/midlong_audit_suite.py"
sys.path.insert(0, str(ROOT))


def test_suite_script_exists_and_lists_checks():
    assert SCRIPT.exists(), "审计套件脚本不存在"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--list"], cwd=str(ROOT),
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-400:]
    ids = [ln.split()[0] for ln in proc.stdout.strip().splitlines() if ln.strip()]
    for expected in ("defect_inventory", "dead_keys", "profit_giveback",
                     "gate_edge", "position_event_consistency", "paper_live_divergence",
                     # [§83 2026-09-11 目标①③] 熔断误伤/漏报 + 出场抑制安全
                     "breaker_harm", "suppression_safety",
                     # [§84 / 缺陷 #69] 熔断证据新鲜度（自锁核查）
                     "breaker_evidence_age"):
        assert expected in ids, f"套件缺少子项 {expected}"


def test_every_check_target_script_exists_on_disk():
    """子项指向的脚本必须存在 —— 否则套件会"跑成功但什么都没查"。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location("_suite", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    assert mod.CHECKS, "CHECKS 为空"
    for cid, argv, desc in mod.CHECKS:
        target = ROOT / argv[0]
        assert target.exists(), f"{cid} 指向的脚本不存在: {argv[0]}"
        assert desc, f"{cid} 缺少说明"


def test_report_output_convention():
    import importlib.util

    spec = importlib.util.spec_from_file_location("_suite2", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.OUT_DIR == ROOT / "data" / "audit_reports", "产物目录应为 data/audit_reports"
    latest = sorted(mod.OUT_DIR.glob("midlong_audit_*.json"))
    if latest:
        payload = json.loads(latest[-1].read_text(encoding="utf-8"))
        for field in ("generated_at", "checks", "failed", "runtime_snapshot"):
            assert field in payload, f"产物缺少字段 {field}"
        assert payload["runtime_snapshot"].get("funnel_24h") or payload["runtime_snapshot"].get("funnel_error")
