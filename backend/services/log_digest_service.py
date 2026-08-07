"""从 backend.log 聚合 ERROR/CRITICAL，供 OpenCode / Alpha 助手 L1 注入。"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

_LOG_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \[(\w+)\] ([^:]+):(\d+) - (.*)$"
)
_NUM_RE = re.compile(r"\b\d+(?:\.\d+)?\b")

_DEFAULT_LOG = os.path.join("logs", "backend.log")
_DEFAULT_ERROR_LOG = os.path.join("logs", "backend.error.log")

_SEVERITY_HINTS: Tuple[Tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"Run not found in tenant scope", re.I), "P0"),
    (re.compile(r"ForeignKeyViolation|strategy_trades", re.I), "P0"),
    (re.compile(r"All retries exhausted|Kline fetch exhausted", re.I), "P1"),
    (re.compile(r"Task exception was never retrieved", re.I), "P1"),
    (re.compile(r"websocket.*Expired|Expired.*websocket", re.I), "P2"),
)

# 已知可忽略/已修复的噪音 ERROR（不计入助手角标）
_BENIGN_ERROR_PATTERNS: Tuple[re.Pattern[str], ...] = (
    re.compile(r"cannot schedule new futures after shutdown", re.I),
    re.compile(r"Error submitting job.*to executor", re.I),
    re.compile(r"asyncio\.exceptions\.CancelledError", re.I),
    re.compile(r"KeyboardInterrupt", re.I),
    re.compile(r"fin=1 opcode=8.*Expired", re.I),
    re.compile(r"peer closed connection without sending complete message body", re.I),
    re.compile(r"incomplete chunked read", re.I),
    re.compile(r"\[AIFactor\] LLM失败:.*Unterminated string", re.I),
    re.compile(r"UniqueViolation.*crypto_klines", re.I),
    re.compile(r"ccxt TypeError in load_markets", re.I),
    re.compile(r"429 rate limit|429 Too Many Requests|Kline fetch exhausted", re.I),
    re.compile(r"urlopen error timed out|WinError 10054", re.I),
    re.compile(r"Domain '.*' already registered", re.I),
)


def _is_benign_error(message: str) -> bool:
    return any(p.search(message) for p in _BENIGN_ERROR_PATTERNS)


def _project_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", ".."))


def _resolve_log_path(log_path: str) -> str:
    if os.path.isabs(log_path):
        return log_path
    return os.path.join(_project_root(), log_path)


def _severity_hint(message: str) -> str:
    for pattern, hint in _SEVERITY_HINTS:
        if pattern.search(message):
            return hint
    return "P2"


def _normalize_message(message: str, *, max_len: int) -> str:
    text = _NUM_RE.sub("<N>", message.strip())
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


def _parse_ts(raw: str) -> Optional[datetime]:
    try:
        dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _iter_tail_lines(
    path: str,
    *,
    max_lines: int = 50_000,
    max_bytes: int = 10 * 1024 * 1024,
) -> Iterable[str]:
    if not os.path.isfile(path):
        return []
    size = os.path.getsize(path)
    read_size = min(size, max_bytes)
    with open(path, "rb") as f:
        if read_size < size:
            f.seek(-read_size, os.SEEK_END)
        chunk = f.read(read_size)
    text = chunk.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
    return lines


def _collect_log_paths(primary: str) -> List[str]:
    paths: List[str] = []
    for candidate in (primary, _DEFAULT_ERROR_LOG):
        resolved = _resolve_log_path(candidate)
        if os.path.isfile(resolved) and resolved not in paths:
            paths.append(resolved)
    return paths


def build_digest(
    *,
    log_path: str = _DEFAULT_LOG,
    window_hours: int = 24,
    levels: Tuple[str, ...] = ("ERROR", "CRITICAL"),
    group_by: str = "logger",
    top_n: int = 10,
    message_max_len: int = 200,
    dedupe_pattern: bool = True,
    max_scan_lines: int = 50_000,
    max_scan_bytes: int = 10 * 1024 * 1024,
) -> Dict[str, Any]:
    """扫描日志尾部，按 logger / pattern 聚合 ERROR。"""
    level_set = {lv.upper() for lv in levels}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, window_hours))
    paths = _collect_log_paths(log_path)

    groups: Dict[str, Dict[str, Any]] = {}
    total_errors = 0
    scanned_lines = 0
    oldest_ts: Optional[str] = None
    newest_ts: Optional[str] = None

    for path in paths:
        for line in _iter_tail_lines(path, max_lines=max_scan_lines, max_bytes=max_scan_bytes):
            scanned_lines += 1
            m = _LOG_LINE_RE.match(line.strip())
            if not m:
                continue
            ts_raw, level, logger_name, _line_no, message = m.groups()
            if level.upper() not in level_set:
                continue
            ts = _parse_ts(ts_raw)
            if ts is None or ts < cutoff:
                continue
            if _is_benign_error(message):
                continue

            total_errors += 1
            if oldest_ts is None or ts_raw < oldest_ts:
                oldest_ts = ts_raw
            if newest_ts is None or ts_raw > newest_ts:
                newest_ts = ts_raw

            pattern = _normalize_message(message, max_len=message_max_len) if dedupe_pattern else message[:message_max_len]
            if group_by == "message_pattern":
                key = hashlib.md5(pattern.encode("utf-8", errors="replace")).hexdigest()[:12]
                group_key = f"pattern:{key}"
            else:
                group_key = logger_name.strip() or "unknown"

            bucket = groups.setdefault(
                group_key,
                {
                    "logger": logger_name.strip(),
                    "count": 0,
                    "sample": message[:message_max_len],
                    "pattern": pattern,
                    "severity_hint": _severity_hint(message),
                    "last_seen": ts_raw,
                },
            )
            bucket["count"] += 1
            bucket["last_seen"] = ts_raw
            if bucket["count"] == 1:
                bucket["sample"] = message[:message_max_len]
                bucket["severity_hint"] = _severity_hint(message)

    entries = sorted(groups.values(), key=lambda x: (-x["count"], x["logger"]))
    top_entries = entries[: max(1, top_n)]
    p0_count = sum(1 for e in entries if e.get("severity_hint") == "P0")
    distinct_groups = len(groups)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_hours": window_hours,
        "log_paths": paths,
        "scanned_lines": scanned_lines,
        "total_errors": total_errors,
        "distinct_groups": distinct_groups,
        "p0_count": p0_count,
        "has_log_errors": total_errors > 0,
        "time_range": {"oldest": oldest_ts, "newest": newest_ts},
        "entries": top_entries,
        "group_by": group_by,
    }


def tail_log_lines(*, log_path: str = _DEFAULT_LOG, lines: int = 200) -> Dict[str, Any]:
    """返回日志文件尾部原始行（调试 / SystemLogs Tab）。"""
    path = _resolve_log_path(log_path)
    if not os.path.isfile(path):
        return {"path": path, "lines": [], "exists": False}
    raw_lines = list(_iter_tail_lines(path, max_lines=max(1, lines), max_bytes=512 * 1024))
    return {
        "path": path,
        "exists": True,
        "lines": raw_lines[-lines:],
        "count": min(len(raw_lines), lines),
    }
