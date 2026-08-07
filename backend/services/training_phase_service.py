"""TrainingPhase — 窄训练期配置与状态（全自动默认开启）。"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

STATE_FILE = os.path.join("data", "training_phase.json")
DEFAULT_SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "ASTER"]
_cache: dict = {"ts": 0.0, "data": {}}


def _env_bool(name: str, default: str = "true") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes", "on")


def _default_state() -> Dict[str, Any]:
    return {
        "active": _env_bool("TRAINING_PHASE_AUTO", "true"),
        "symbols": list(DEFAULT_SYMBOLS),
        "max_active_strategies": 10,
        "graduation_queue": [],
        "strategy_graduation": {},
        "champion_windows": {},
        "live_cooldown_until": {},
        "started_at": None,
    }


def load_state() -> Dict[str, Any]:
    now = time.time()
    if now - _cache["ts"] < 5 and _cache["data"]:
        return dict(_cache["data"])
    state = _default_state()
    if os.path.isfile(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f) or {}
            if isinstance(loaded, dict):
                state.update(loaded)
        except Exception as err:
            logger.warning("[TrainingPhase] load failed: %s", err)
    if state.get("active") and not state.get("started_at"):
        state["started_at"] = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat()
    _cache["ts"] = now
    _cache["data"] = state
    return dict(state)


def save_state(state: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(STATE_FILE) or ".", exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    _cache["ts"] = 0.0
    _cache["data"] = dict(state)


def is_active() -> bool:
    return bool(load_state().get("active"))


def target_symbols() -> List[str]:
    syms = load_state().get("symbols") or DEFAULT_SYMBOLS
    return [str(s).upper() for s in syms if s]


def min_analysis_closed() -> int:
    return 3 if is_active() else 5


def max_active_strategies() -> int:
    if is_active():
        return int(load_state().get("max_active_strategies") or 10)
    return 15


def set_graduation_status(strategy_id: str, status: str, **extra: Any) -> None:
    state = load_state()
    sg = state.setdefault("strategy_graduation", {})
    entry = sg.setdefault(str(strategy_id), {})
    entry["status"] = status
    entry.update(extra)
    save_state(state)


def get_graduation_status(strategy_id: str) -> Optional[str]:
    sg = load_state().get("strategy_graduation") or {}
    entry = sg.get(str(strategy_id)) or {}
    return entry.get("status")


def enqueue_graduation(strategy_id: str) -> None:
    state = load_state()
    q = state.setdefault("graduation_queue", [])
    sid = str(strategy_id)
    if sid not in q:
        q.append(sid)
    save_state(state)


def dequeue_graduation(strategy_id: str) -> None:
    state = load_state()
    q = [x for x in (state.get("graduation_queue") or []) if x != str(strategy_id)]
    state["graduation_queue"] = q
    save_state(state)


def status_snapshot() -> Dict[str, Any]:
    from backend.config.settings import (
        TRAINING_AUTO_LIVE,
        TRAINING_LIVE_ENV,
        TRAINING_LIVE_MAX_STRATEGIES,
    )

    state = load_state()
    return {
        "active": bool(state.get("active")),
        "symbols": target_symbols(),
        "max_active_strategies": max_active_strategies(),
        "graduation_queue_len": len(state.get("graduation_queue") or []),
        "strategy_graduation": state.get("strategy_graduation") or {},
        "auto_live": TRAINING_AUTO_LIVE,
        "live_env": TRAINING_LIVE_ENV,
        "live_max_strategies": TRAINING_LIVE_MAX_STRATEGIES,
        "started_at": state.get("started_at"),
    }
