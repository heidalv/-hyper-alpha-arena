"""决策一致性仲裁 Gate（2026-08-17 新增 Agent，审计缺口 #1）。

问题：方向决策多源并存——MasterController（总控）、MTOrchestrator（多周期）、
scalp 独立循环——各自对同一 (symbol, tier) 可能给出相反方向，且 scalp 独立
路径绕过 master_execution 直接下单，此前无任何交叉校验。

规则（fail-closed，只拦开仓不拦风控退出）：
1. 每个来源对 (symbol, tier) 的最近开仓观点登记在带 TTL 的视图表；
2. 某来源要开仓时，若存在【方向相反 + 双方置信度都 ≥ CONFIDENCE_GATE】的其他
   来源观点 → 判冲突 → 拒绝开仓（hold），计冲突数并打日志；
3. 退出/减仓动作（close/reduce）永远放行（风控优先于一致性）；
4. 无冲突或置信度不足 → 放行。

只读内存状态（TTL 120s），无 DB 依赖；随进程重启清零，安全。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# env 可调
import os as _os

try:
    _CONFIDENCE_GATE = float(_os.getenv("ARB_CONFIDENCE_GATE", "55") or 55)
except (TypeError, ValueError):
    _CONFIDENCE_GATE = 55.0
try:
    _TTL_SEC = float(_os.getenv("ARB_VIEW_TTL_SEC", "120") or 120)
except (TypeError, ValueError):
    _TTL_SEC = 120.0

_ENTRY_ACTIONS = {"buy", "sell", "pyramid", "dca"}
_EXIT_ACTIONS = {"close", "reduce"}
_LOCK = threading.Lock()
# (symbol, tier, source) -> {"action", "confidence", "ts"}
_views: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
_stats = {"checks": 0, "conflicts_blocked": 0, "last_conflict": ""}


def _now() -> float:
    return time.time()


def register_view(symbol: str, tier: str, source: str, action: str, confidence: float) -> None:
    """登记一个来源对 (symbol, tier) 的最近观点（含 hold，用于冲突判定时排除同源）。"""
    key = (str(symbol).upper(), str(tier or "short").lower(), str(source))
    with _LOCK:
        _views[key] = {
            "action": str(action or "hold").lower(),
            "confidence": float(confidence or 0),
            "ts": _now(),
        }
        # 惰性清理过期条目（O(n) 但 n 很小）
        cutoff = _now() - _TTL_SEC
        for k in [k for k, v in _views.items() if v["ts"] < cutoff]:
            _views.pop(k, None)


def check_entry(
    symbol: str,
    tier: str,
    source: str,
    action: str,
    confidence: float,
) -> Tuple[bool, str]:
    """开仓前一致性校验。返回 (allowed, reason)。"""
    act = str(action or "").lower()
    if act in _EXIT_ACTIONS or act == "hold":
        return True, "exit_or_hold"
    if act not in _ENTRY_ACTIONS:
        return True, "non_entry"
    sym = str(symbol).upper()
    tier_l = str(tier or "short").lower()
    conf = float(confidence or 0)
    with _LOCK:
        _stats["checks"] += 1
        cutoff = _now() - _TTL_SEC
        conflicts: List[str] = []
        for (vs, vt, vsrc), v in list(_views.items()):
            if vs != sym or vt != tier_l or vsrc == source:
                continue
            if v["ts"] < cutoff:
                _views.pop((vs, vt, vsrc), None)
                continue
            other_act = str(v.get("action") or "").lower()
            if other_act not in _ENTRY_ACTIONS:
                continue
            same_dir = (act in ("buy", "pyramid", "dca")) == (other_act in ("buy", "pyramid", "dca"))
            if same_dir:
                continue
            if conf >= _CONFIDENCE_GATE and float(v.get("confidence") or 0) >= _CONFIDENCE_GATE:
                conflicts.append(f"{vsrc}:{other_act}@{v['confidence']:.0f}")
        if conflicts:
            _stats["conflicts_blocked"] += 1
            _stats["last_conflict"] = f"{sym}/{tier_l} {source}:{act} vs " + ",".join(conflicts)
            logger.warning(
                "[ArbGate] 方向冲突，fail-closed 拒绝开仓 %s/%s %s:%s conf=%.0f 冲突=%s",
                sym, tier_l, source, act, conf, ",".join(conflicts),
            )
            return False, "arb_conflict:" + ",".join(conflicts)
    return True, "ok"


def status() -> Dict[str, Any]:
    with _LOCK:
        return {
            "views": len(_views),
            "checks": _stats["checks"],
            "conflicts_blocked": _stats["conflicts_blocked"],
            "last_conflict": _stats["last_conflict"][:160],
            "confidence_gate": _CONFIDENCE_GATE,
            "ttl_sec": _TTL_SEC,
        }
