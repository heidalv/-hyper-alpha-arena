"""带体积轮转的 JSONL 追加写入，防止审计日志无限撑满磁盘。

默认单文件约 20MB，最多保留 5 个备份（约 100MB/类）。
超过体积时：``name.jsonl`` → ``name.jsonl.1`` … 再开新文件。
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict, Optional

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(path: str) -> threading.Lock:
    with _locks_guard:
        if path not in _locks:
            _locks[path] = threading.Lock()
        return _locks[path]


def _rotate_if_needed(path: str, max_bytes: int, backup_count: int) -> None:
    try:
        if max_bytes <= 0 or backup_count <= 0:
            return
        if not os.path.isfile(path):
            return
        if os.path.getsize(path) < max_bytes:
            return
        # name.jsonl.(n) → delete oldest; shift down
        oldest = f"{path}.{backup_count}"
        if os.path.isfile(oldest):
            try:
                os.remove(oldest)
            except OSError:
                pass
        for i in range(backup_count - 1, 0, -1):
            src = f"{path}.{i}"
            dst = f"{path}.{i + 1}"
            if os.path.isfile(src):
                try:
                    os.replace(src, dst)
                except OSError:
                    pass
        try:
            os.replace(path, f"{path}.1")
        except OSError:
            pass
    except OSError:
        pass


def append_jsonl(
    path: str,
    record: Dict[str, Any],
    *,
    max_bytes: Optional[int] = None,
    backup_count: Optional[int] = None,
) -> None:
    """追加一行 JSON；按体积轮转。失败抛给调用方决定是否吞掉。"""
    try:
        from backend.config.settings import (
            AUDIT_JSONL_BACKUP_COUNT,
            AUDIT_JSONL_MAX_BYTES,
        )
        mb = int(max_bytes if max_bytes is not None else AUDIT_JSONL_MAX_BYTES)
        bc = int(backup_count if backup_count is not None else AUDIT_JSONL_BACKUP_COUNT)
    except Exception:
        mb = int(max_bytes if max_bytes is not None else 20 * 1024 * 1024)
        bc = int(backup_count if backup_count is not None else 5)

    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
    with _lock_for(path):
        _rotate_if_needed(path, mb, bc)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
