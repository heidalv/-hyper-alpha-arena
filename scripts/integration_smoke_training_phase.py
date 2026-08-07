#!/usr/bin/env python3
"""训练期模块 — 离线集成冒烟（无需重启后端）。"""

from __future__ import annotations

import sys


def main() -> int:
    errors = []

    # 1. 模块导入
    try:
        from backend.services.training_orchestrator import (
            rebalance_portfolio,
            run_validated_merge,
            sync_golden_tags,
            boot_training_phase,
            JOB_REBALANCE,
        )
        from backend.services.training_phase_service import status_snapshot, is_active
        from backend.services.champion_recovery_service import run_champion_recovery
        from backend.services.training_graduation_service import scan_graduation
        from backend.services.training_live_promote_service import scan_live_promote, scan_live_demote
        print("OK   模块导入")
    except Exception as err:
        errors.append(f"import: {err}")
        print(f"FAIL 模块导入: {err}")

    # 2. 状态快照
    try:
        snap = status_snapshot()
        assert "active" in snap and "symbols" in snap
        print(f"OK   status_snapshot active={snap['active']} symbols={snap.get('symbols')}")
    except Exception as err:
        errors.append(f"snapshot: {err}")
        print(f"FAIL status_snapshot: {err}")

    # 3. DB 集成 job（只跑一轮，不崩溃即可）
    from backend.database.connection import SessionLocal

    db = SessionLocal()
    jobs = [
        ("rebalance_portfolio", lambda: rebalance_portfolio(db)),
        ("run_validated_merge", lambda: run_validated_merge(db)),
        ("sync_golden_tags", lambda: sync_golden_tags(db)),
        ("champion_recovery", lambda: run_champion_recovery(db)),
        ("graduation_scan", lambda: scan_graduation(db)),
        ("live_promote", lambda: scan_live_promote(db)),
        ("live_demote", lambda: scan_live_demote(db)),
    ]
    try:
        for name, fn in jobs:
            try:
                out = fn()
                print(f"OK   {name}: {out}")
            except Exception as err:
                errors.append(f"{name}: {err}")
                print(f"FAIL {name}: {err}")
    finally:
        db.close()

    # 4. boot（可能已 booted）
    try:
        out = boot_training_phase()
        print(f"OK   boot_training_phase: {out}")
    except Exception as err:
        errors.append(f"boot: {err}")
        print(f"FAIL boot: {err}")

    print(f"\n{'ALL PASS' if not errors else f'{len(errors)} FAILURES'}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
