"""
BlockReportAggregator — v3 整改可观测性

聚合风控/手续费/防守等"阻断事件"，供前端 AI 学习中心展示 Top-N 原因：
帮助用户快速判断"为什么今天没开单"。

注意：仅进程内内存统计，重启后清空；不是合规/审计级日志，仅用于运营观测。
"""

from __future__ import annotations

import threading
import time
from collections import Counter, deque
from typing import Deque, Dict, List, Tuple

# 最多保留最近 24h，单进程约束 20000 条足够 —
_MAX_ENTRIES = 20000
_DEFAULT_WINDOW_SEC = 24 * 3600


class _BlockReportAggregator:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: Deque[Tuple[float, str, str]] = deque(maxlen=_MAX_ENTRIES)
        # (ts, code, detail)

    def record(self, code: str, detail: str = "") -> None:
        try:
            with self._lock:
                self._events.append((time.time(), code or "unknown", detail or ""))
        except Exception:
            pass

    def _trim(self, window_sec: int) -> List[Tuple[float, str, str]]:
        cutoff = time.time() - max(60, window_sec)
        with self._lock:
            return [e for e in self._events if e[0] >= cutoff]

    def top(self, n: int = 3, window_sec: int = _DEFAULT_WINDOW_SEC) -> Dict:
        events = self._trim(window_sec)
        counter: Counter = Counter(code for _, code, _ in events)
        total = sum(counter.values())
        top_items = counter.most_common(max(1, n))
        detail_samples: Dict[str, List[str]] = {}
        for ts, code, detail in reversed(events):
            if code in {c for c, _ in top_items} and detail:
                detail_samples.setdefault(code, [])
                if len(detail_samples[code]) < 3:
                    detail_samples[code].append(detail)
        return {
            "window_sec": window_sec,
            "total": total,
            "top": [
                {
                    "code": code,
                    "count": cnt,
                    "ratio": (cnt / total) if total else 0.0,
                    "samples": detail_samples.get(code, []),
                }
                for code, cnt in top_items
            ],
        }


block_report_aggregator = _BlockReportAggregator()


def record_block(code: str, detail: str = "") -> None:
    """短路入口，外部调用尽量 try/except 包裹以避免影响主流程。"""
    block_report_aggregator.record(code, detail)
