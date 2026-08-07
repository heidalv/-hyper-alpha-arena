"""
统一开仓门禁协调器 — 拦截归因 + 通过率统计 + 下次可开时间。

解决：7+ 道门串行无统筹、拦截归因不可解释、冷却独立叠加。
所有开仓门禁的拦截/通过记录经此模块，统一统计 + 统一冷却。
"""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class GateRecord:
    """单次门禁判定记录。"""
    gate_name: str          # unified_gate / mtf_constraint / ev_gate / open_gate / ...
    symbol: str
    tier: str               # short / mid / long
    passed: bool
    reason: str = ""
    ts: float = 0.0


class OpenGateCoordinator:
    """
    统一开仓门禁协调器（单例）。

    职责：
    1. 统计每道门的通过/拦截率（可观测，落盘）
    2. 统一冷却计算（下次可开时间）
    3. 极端降级（连续 N 次全被拦 → 降低阈值建议）

    用法：
        from backend.services.gate_coordinator import gate_coordinator
        gate_coordinator.record("mtf_constraint", "BTC", "long", passed=False, reason="veto")
        stats = gate_coordinator.stats()
        next_open = gate_coordinator.next_available("BTC", "long")
    """

    def __init__(self, max_records: int = 10000):
        self._records: deque[GateRecord] = deque(maxlen=max_records)
        self._lock = threading.Lock()
        # per (symbol, tier) 冷却追踪
        self._last_block_ts: dict[tuple, float] = {}
        self._consecutive_blocks: dict[tuple, int] = {}
        logger.info("[GateCoordinator] 统一开仓门禁协调器初始化")

    def record(self, gate_name: str, symbol: str, tier: str,
               passed: bool, reason: str = "") -> None:
        """记录一次门禁判定。"""
        ts = time.time()
        rec = GateRecord(gate_name=gate_name, symbol=symbol.upper(),
                         tier=tier, passed=passed, reason=reason, ts=ts)
        key = (symbol.upper(), tier)
        with self._lock:
            self._records.append(rec)
            if passed:
                self._consecutive_blocks.pop(key, None)
            else:
                self._consecutive_blocks[key] = self._consecutive_blocks.get(key, 0) + 1
                self._last_block_ts[key] = ts

    def consecutive_blocks(self, symbol: str, tier: str) -> int:
        """某品种某 tier 连续被拦次数。"""
        with self._lock:
            return self._consecutive_blocks.get((symbol.upper(), tier), 0)

    def should_degrade_threshold(self, symbol: str, tier: str,
                                  threshold: int = 10) -> bool:
        """连续被拦 ≥ threshold 次 → 建议降低阈值。"""
        return self.consecutive_blocks(symbol, tier) >= threshold

    def stats(self, window_sec: int = 3600) -> dict:
        """统计最近 window_sec 内各门禁的通过/拦截率。"""
        now = time.time()
        cutoff = now - window_sec
        gate_stats: dict[str, dict] = defaultdict(lambda: {"pass": 0, "block": 0, "reasons": defaultdict(int)})
        with self._lock:
            for rec in self._records:
                if rec.ts < cutoff:
                    continue
                if rec.passed:
                    gate_stats[rec.gate_name]["pass"] += 1
                else:
                    gate_stats[rec.gate_name]["block"] += 1
                    if rec.reason:
                        gate_stats[rec.gate_name]["reasons"][rec.reason[:50]] += 1
        # 计算通过率
        result = {}
        for gate, s in gate_stats.items():
            total = s["pass"] + s["block"]
            result[gate] = {
                "pass": s["pass"],
                "block": s["block"],
                "pass_rate": round(s["pass"] / total, 2) if total else 0,
                "top_reasons": dict(sorted(s["reasons"].items(), key=lambda x: -x[1])[:3]),
            }
        return result

    def blocked_symbols(self, tier: str = None) -> list[dict]:
        """当前被连续拦截的品种列表。"""
        with self._lock:
            result = []
            for (sym, t), count in self._consecutive_blocks.items():
                if tier and t != tier:
                    continue
                if count > 0:
                    last_ts = self._last_block_ts.get((sym, t), 0)
                    result.append({
                        "symbol": sym, "tier": t,
                        "consecutive_blocks": count,
                        "last_block_ago_sec": int(time.time() - last_ts) if last_ts else 0,
                    })
            return sorted(result, key=lambda x: -x["consecutive_blocks"])


# 全局单例
gate_coordinator = OpenGateCoordinator()
