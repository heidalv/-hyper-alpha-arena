"""
A/B Analyzer — compare close-request sources by win rate, avg PnL, block rate.

Reads decision_arbiter.jsonl and ai_decision_logs to produce per-source 
statistics for evaluating which decision layer performs best.

Usage:
    from backend.analytics.ab_analyzer import AbAnalyzer
    analyzer = AbAnalyzer()
    stats = analyzer.compute(days=30)
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_ARBITER_LOG = os.path.join("data", "decision_arbiter.jsonl")

# Source labels for display
_SOURCE_LABELS: Dict[str, str] = {
    "engine_hard": "Engine (SL/TP)",
    "profit_protection": "Profit Protection",
    "staged_tp": "Staged TP",
    "master": "Master Controller",
    "defensive": "Defensive Verdict",
    "ai_reverse": "AI Reverse",
    "manual": "Manual (API/UI)",
}


@dataclass
class SourceStats:
    """Aggregated statistics for one decision source."""
    source: str
    label: str
    total_requests: int = 0
    would_block_count: int = 0
    blocked_count: int = 0
    win_count: int = 0
    loss_count: int = 0
    total_pnl_pct: float = 0.0
    sl_breach_sum: float = 0.0
    avg_confidence: float = 0.0
    confidence_count: int = 0

    @property
    def block_rate(self) -> float:
        return self.would_block_count / self.total_requests if self.total_requests else 0

    @property
    def win_rate(self) -> float:
        total = self.win_count + self.loss_count
        return self.win_count / total if total else 0

    @property
    def avg_pnl_pct(self) -> float:
        total = self.win_count + self.loss_count
        return self.total_pnl_pct / total if total else 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "label": self.label,
            "total_requests": self.total_requests,
            "would_block_count": self.would_block_count,
            "block_rate": round(self.block_rate, 4),
            "win_rate": round(self.win_rate, 4),
            "avg_pnl_pct": round(self.avg_pnl_pct, 4),
            "avg_confidence": round(self.avg_confidence, 4) if self.confidence_count else None,
        }


class AbAnalyzer:
    """Compute per-source performance statistics from decision logs."""

    def compute(self, days: int = 30) -> List[Dict[str, Any]]:
        """Return per-source stats for the last N days."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        sources: Dict[str, SourceStats] = {}

        if not os.path.exists(_ARBITER_LOG):
            logger.warning("[A/B] decision_arbiter.jsonl not found — returning empty stats")
            return []

        with open(_ARBITER_LOG, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Timestamp check
                ts_str = entry.get("timestamp", "")
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if ts < cutoff:
                        continue
                except (ValueError, TypeError):
                    pass

                source = entry.get("source", "unknown")
                if source not in sources:
                    sources[source] = SourceStats(
                        source=source,
                        label=_SOURCE_LABELS.get(source, source),
                    )

                stats = sources[source]
                stats.total_requests += 1

                if entry.get("would_block"):
                    stats.would_block_count += 1
                if entry.get("block_rule"):
                    stats.blocked_count += 1

                pnl = float(entry.get("pnl_pct", 0) or 0)
                if pnl > 0:
                    stats.win_count += 1
                    stats.total_pnl_pct += pnl
                elif pnl < 0:
                    stats.loss_count += 1
                    stats.total_pnl_pct += pnl

                conf = entry.get("confidence")
                if conf is not None:
                    stats.avg_confidence += float(conf)
                    stats.confidence_count += 1

        # Finalize
        for stats in sources.values():
            if stats.confidence_count:
                stats.avg_confidence /= stats.confidence_count

        # Sort by total requests descending
        result = sorted(
            [s.to_dict() for s in sources.values()],
            key=lambda x: x["total_requests"],
            reverse=True,
        )

        logger.info("[A/B] Computed stats for %d sources over %d days", len(result), days)
        return result


# Module-level instance
ab_analyzer = AbAnalyzer()
