"""日志 / 审计文件清理：防止 JSONL、报告目录无限堆砌塞满硬盘。

- 删除过旧的 ``*.jsonl.N`` 备份与过期报告目录
- 超大未轮转 JSONL 强制截断轮转
- 可选清理过旧的 ``ai_decision_logs``（按天）
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _env_int(name: str, default: int) -> int:
    try:
        from backend.config import settings as S
        return int(getattr(S, name, default))
    except Exception:
        return default


def _purge_old_files(dir_path: Path, *, patterns: List[str], keep_days: int) -> int:
    if keep_days <= 0 or not dir_path.is_dir():
        return 0
    cutoff = time.time() - keep_days * 86400
    removed = 0
    for pat in patterns:
        for p in dir_path.glob(pat):
            try:
                if p.is_file() and p.stat().st_mtime < cutoff:
                    p.unlink(missing_ok=True)
                    removed += 1
            except OSError:
                continue
    return removed


def _force_rotate_huge_jsonl(path: Path, max_bytes: int) -> bool:
    """单个 JSONL 过大时移到 .1 并截断（与 jsonl_rotating 配合）。"""
    try:
        if not path.is_file() or max_bytes <= 0:
            return False
        if path.stat().st_size < max_bytes * 2:  # 只有严重超标才强制
            return False
        bak = path.with_name(path.name + ".1")
        if bak.exists():
            bak.unlink(missing_ok=True)
        path.replace(bak)
        path.write_text("", encoding="utf-8")
        return True
    except OSError as e:
        logger.debug("[LogRetention] force rotate %s: %s", path, e)
        return False


def run_log_retention(*, dry_run: bool = False) -> Dict[str, Any]:
    """执行一轮清理，返回统计。"""
    keep_days = _env_int("LOG_RETENTION_DAYS", 30)
    report_days = _env_int("REPORT_RETENTION_DAYS", 60)
    max_jsonl = _env_int("AUDIT_JSONL_MAX_BYTES", 20 * 1024 * 1024)
    decision_days = _env_int("AI_DECISION_LOG_RETENTION_DAYS", 90)

    root = _repo_root()
    data = root / "data"
    logs = root / "logs"
    midlong = root / "backend" / "data" / "midlong_reports"

    stats: Dict[str, Any] = {
        "keep_days": keep_days,
        "removed_files": 0,
        "rotated_jsonl": 0,
        "decision_rows_deleted": 0,
        "dry_run": dry_run,
    }

    if dry_run:
        return stats

    # 1) data/ 下 jsonl 备份与超大文件
    if data.is_dir():
        stats["removed_files"] += _purge_old_files(
            data, patterns=["*.jsonl.*", "*.log.*"], keep_days=keep_days
        )
        for p in data.glob("*.jsonl"):
            if _force_rotate_huge_jsonl(p, max_jsonl):
                stats["rotated_jsonl"] += 1
        # 训练报告
        tr = data / "training_reports"
        if tr.is_dir():
            stats["removed_files"] += _purge_old_files(
                tr, patterns=["*.json", "*.md"], keep_days=report_days
            )
        op = data / "opencode_reports"
        if op.is_dir():
            stats["removed_files"] += _purge_old_files(
                op, patterns=["*.jsonl.*", "*.md", "*.json"], keep_days=report_days
            )

    # 2) midlong 日报
    if midlong.is_dir():
        stats["removed_files"] += _purge_old_files(
            midlong, patterns=["*.md", "*.json"], keep_days=report_days
        )

    # 3) logs 轮转碎片（RotatingFileHandler 已控体积，只清异常残留）
    if logs.is_dir():
        stats["removed_files"] += _purge_old_files(
            logs, patterns=["*.log.*"], keep_days=keep_days
        )

    # 4) AI 决策表（可选，analytics 库）
    if decision_days > 0:
        try:
            from datetime import datetime, timedelta, timezone
            from backend.database.connection import AnalyticsSessionLocal
            from backend.database.models import AIDecisionLog

            cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=decision_days)
            db = AnalyticsSessionLocal()
            try:
                q = db.query(AIDecisionLog).filter(AIDecisionLog.created_at < cutoff)
                n = q.count()
                if n > 0:
                    q.delete(synchronize_session=False)
                    db.commit()
                    stats["decision_rows_deleted"] = n
            finally:
                db.close()
        except Exception as e:
            logger.info("[LogRetention] ai_decision_logs cleanup skipped: %s", e)

    logger.info("[LogRetention] done %s", stats)
    return stats
