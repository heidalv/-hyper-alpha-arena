# -*- coding: utf-8 -*-
"""[P10 / §73 执行 2026-09-10] 审计类 jsonl 备份的独立保留期。

背景（§57.5 A / 缺陷 #36）：
  * `data/*.jsonl.*`（被轮转切下来的审计取证数据，如 `midlong_direction_audit.jsonl.1`）
    原先与普通日志共用 `LOG_RETENTION_DAYS`（默认 30）⇒ **静默删除**；
  * §57 已经吃过一次"活动文件被清空导致 96% 漏斗历史丢失"的亏，保留期再短会持续削弱可追溯性；
  * 实测现状：`midlong_direction_audit.jsonl.1` 40MB（2.1 天）、`training_audit.jsonl.1` 20MB（16.4 天）。

修法：新增 `AUDIT_BACKUP_KEEP_DAYS`（默认 **180**，`0` = 永不删除），只作用于 `*.jsonl.*`；
`*.log.*` 仍走 `LOG_RETENTION_DAYS`。删除动作已有 WARNING（`_purge_old_files`）。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services import log_retention_service as lrs  # noqa: E402


def _touch(p: Path, age_days: float) -> None:
    p.write_text("x\n", encoding="utf-8")
    ts = time.time() - age_days * 86400
    os.utime(p, (ts, ts))


def _run(tmp_path, monkeypatch, *, audit_days: int, log_days: int = 30) -> dict:
    """配置走 `settings`（`log_retention_service._env_int` 的实际读取路径）。"""
    from backend.config import settings

    monkeypatch.setattr(settings, "AUDIT_BACKUP_KEEP_DAYS", audit_days, raising=False)
    monkeypatch.setattr(settings, "LOG_RETENTION_DAYS", log_days, raising=False)
    monkeypatch.setattr(lrs, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(lrs, "_force_rotate_huge_jsonl", lambda *a, **k: False)
    return lrs.run_log_retention(dry_run=False)


def test_audit_backups_survive_longer_than_logs(tmp_path, monkeypatch):
    data = tmp_path / "data"
    logs = tmp_path / "logs"
    data.mkdir()
    logs.mkdir()
    fresh = data / "midlong_direction_audit.jsonl.1"
    mid = data / "training_audit.jsonl.1"
    stale = data / "very_old.jsonl.1"
    log_old = logs / "backend.log.9"
    _touch(fresh, 2.1)
    _touch(mid, 16.4)
    _touch(stale, 200.0)
    _touch(log_old, 45.0)

    stats = _run(tmp_path, monkeypatch, audit_days=180)

    assert stats["audit_backup_keep_days"] == 180
    assert fresh.exists() and mid.exists(), "180 天内的审计备份不该被删（原为 30 天）"
    assert not stale.exists(), "超过独立保留期的审计备份应被删除"
    assert not log_old.exists(), "普通日志仍按 LOG_RETENTION_DAYS 清理"


def test_zero_means_never_delete(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    ancient = data / "midlong_direction_audit.jsonl.1"
    _touch(ancient, 900.0)
    stats = _run(tmp_path, monkeypatch, audit_days=0)
    assert stats["audit_backup_keep_days"] == 0
    assert ancient.exists(), "AUDIT_BACKUP_KEEP_DAYS=0 表示永不删除"


def test_key_registered_and_declared_in_settings():
    """新键必须 ①登记 ②在 settings 声明（`_env_int` 经 settings 读取 ⇒ 不声明则 .env 无效）。"""
    import re

    from backend.config import settings
    from backend.config.env_registry import KNOWN_FLAGS

    assert "AUDIT_BACKUP_KEEP_DAYS" in KNOWN_FLAGS, "新键未登记"
    assert hasattr(settings, "AUDIT_BACKUP_KEEP_DAYS"), "settings 未声明 ⇒ .env 改了也不生效"
    src = (ROOT / "backend/config/settings.py").read_text(encoding="utf-8-sig", errors="replace")
    assert re.search(r'^AUDIT_BACKUP_KEEP_DAYS:\s*int\s*=\s*int\(os\.getenv\("AUDIT_BACKUP_KEEP_DAYS",\s*"180"\)',
                     src, re.M), "声明形态异常"


def test_default_is_180_days():
    """未设 key 时默认 180（而不是沿用 30）。"""
    src = (ROOT / "backend/services/log_retention_service.py").read_text(encoding="utf-8")
    assert '_env_int("AUDIT_BACKUP_KEEP_DAYS", 180)' in src
