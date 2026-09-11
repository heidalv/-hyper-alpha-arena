# -*- coding: utf-8 -*-
"""[2026-09-10 §57] 漏斗审计「跨轮转」读取契约测试。

背景（§57.1 实证）：`log_retention_service._force_rotate_huge_jsonl` 在 `data/` 下把
超过 `2 × AUDIT_JSONL_MAX_BYTES`（默认 40MB）的 `.jsonl` **整体移走并清空**活动文件：

    data/midlong_direction_audit.jsonl      →  4,523 行（轮转后新写）
    data/midlong_direction_audit.jsonl.1    →  112,041 行（轮转前历史，42MB）

而统计/报表函数此前只读活动文件 ⇒ **读者可见比例仅 3.9%**（我此前关于"拒仓构成"的
若干比例也因此只在轮转后的窗口上成立）。本测试锁住：读取必须覆盖 `.N` 备份 + 活动文件，
且按时间顺序（旧 → 新）。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.mlto import midlong_direction_audit as mda  # noqa: E402


def _row(**kw):
    base = {
        "ts": "2026-09-10T00:00:00+00:00", "epoch": time.time(), "outcome": "skip",
        "stage": "exec", "symbol": "ETH", "reason": "unit_test_reason",
        "tier": "mid", "session_id": "s1",
    }
    base.update(kw)
    return json.dumps(base, ensure_ascii=False)


def _setup(tmp_path, monkeypatch, n_old=3, n_new=2):
    live = tmp_path / "audit.jsonl"
    old1 = tmp_path / "audit.jsonl.1"
    old1.write_text("\n".join(_row(reason="pre_rotation", symbol=f"S{i}") for i in range(n_old)) + "\n",
                    encoding="utf-8")
    live.write_text("\n".join(_row(reason="post_rotation", symbol=f"P{i}") for i in range(n_new)) + "\n",
                    encoding="utf-8")
    monkeypatch.setenv("MIDLONG_DIRECTION_AUDIT_PATH", str(live))
    return live, old1


def test_audit_paths_is_oldest_first_and_includes_backups(tmp_path, monkeypatch):
    live, _ = _setup(tmp_path, monkeypatch)
    (tmp_path / "audit.jsonl.2").write_text(_row(reason="oldest") + "\n", encoding="utf-8")
    ps = mda.audit_paths()
    assert [Path(p).name for p in ps] == ["audit.jsonl.2", "audit.jsonl.1", "audit.jsonl"], ps


def test_funnel_summary_includes_pre_rotation_rows(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, n_old=3, n_new=2)
    s = mda.summarize_decision_funnel(lookback_hours=24 * 30)
    assert s["files"] == 2, s
    assert s["n"] == 5, s
    reasons = {r["reason"]: r["count"] for r in s["top_skip_reasons"]}
    assert reasons.get("pre_rotation") == 3, reasons
    assert reasons.get("post_rotation") == 2, reasons


def test_funnel_summary_single_file_when_no_backup(tmp_path, monkeypatch):
    """无备份时行为与修复前一致（只读活动文件）。"""
    live = tmp_path / "audit.jsonl"
    live.write_text(_row(reason="only_live") + "\n", encoding="utf-8")
    monkeypatch.setenv("MIDLONG_DIRECTION_AUDIT_PATH", str(live))
    s = mda.summarize_decision_funnel(lookback_hours=24 * 30)
    assert s["files"] == 1 and s["n"] == 1, s


def test_nibble_probe_counter_reads_backups(tmp_path, monkeypatch):
    live, old1 = _setup(tmp_path, monkeypatch, n_old=2, n_new=1)
    old1.write_text(_row(outcome="opened", reason="nibble_probe") + "\n", encoding="utf-8")
    live.write_text(_row(outcome="open_attempt", reason="nibble_probe") + "\n", encoding="utf-8")
    assert mda.count_nibble_probes_today() == 2


def test_consistency_summary_reads_backups(tmp_path, monkeypatch):
    live, old1 = _setup(tmp_path, monkeypatch, n_old=1, n_new=1)
    old1.write_text(_row(outcome="opened", reason="filled", consistent=True) + "\n", encoding="utf-8")
    live.write_text(_row(outcome="opened", reason="filled", consistent=False) + "\n", encoding="utf-8")
    s = mda.summarize_consistency(lookback_hours=24 * 30)
    assert s["n"] == 2 and s["comparable"] == 2 and s["consistent"] == 1, s
    assert s["rate"] == 0.5


def test_purge_logs_large_file_removal(tmp_path, caplog):
    """[§57] 删除 ≥2MB 的过期文件必须留痕（否则审计历史静默消失）。"""
    import logging
    from backend.services.log_retention_service import _purge_old_files

    big = tmp_path / "audit.jsonl.1"
    big.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    old = time.time() - 100 * 86400
    import os
    os.utime(big, (old, old))
    with caplog.at_level(logging.WARNING):
        n = _purge_old_files(tmp_path, patterns=["*.jsonl.*"], keep_days=30)
    assert n == 1
    msgs = [r.getMessage() for r in caplog.records]
    assert any("删除" in m and "audit.jsonl.1" in m for m in msgs), msgs
