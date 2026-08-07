"""执行层门禁 — 编排器硬门控、决策价一致性（从 monolith 迁出）。"""
from __future__ import annotations

import logging
import os
from typing import Any, Tuple

logger = logging.getLogger(__name__)


def orchestrator_blocks_open(
    sym: str,
    action: str,
    market_summary: dict,
    tier: str = "",
    confidence: float = 0,
    trading_mode: str = "paper",
) -> Tuple[bool, str]:
    """编排器硬门控：frozen 时禁止新开仓；wait 时除非 DualAgent 高置信覆盖。"""
    if action not in ("buy", "sell", "pyramid", "dca"):
        return False, ""
    try:
        from backend.config.settings import (
            ORCHESTRATOR_WAIT_OVERRIDE_CONF,
            get_orchestrator_hard_gate,
        )

        if not get_orchestrator_hard_gate(trading_mode):
            return False, ""
        _mkt = (market_summary or {}).get(sym, {})
        _orch = _mkt.get("orchestrator", {}) if isinstance(_mkt, dict) else {}
        if not isinstance(_orch, dict):
            return False, ""
        _orch_action = (
            _orch.get("action") or _orch.get("final_action") or ""
        ).strip().lower()
        if _orch_action == "frozen":
            _reason = str(_orch.get("reasoning", "") or _orch_action)[:120]
            return True, _reason
        if _orch_action == "wait":
            try:
                _conf = float(confidence or 0)
            except (TypeError, ValueError):
                _conf = 0
            if _conf >= float(ORCHESTRATOR_WAIT_OVERRIDE_CONF):
                return False, ""
            _reason = str(_orch.get("reasoning", "") or _orch_action)[:120]
            return True, _reason
    except Exception:
        pass
    return False, ""


def decision_price_consistency_ok(sym: str, mkt: dict, proposal: Any, mode: str) -> Tuple[bool, str]:
    """决策价一致性门禁：放行后、下单前校验决策价与实时价偏离。"""
    if os.getenv("DECISION_PRICE_GATE_ENABLED", "false").lower() not in ("1", "true", "yes", "on"):
        return True, ""
    try:
        _mkt = mkt if isinstance(mkt, dict) else {}
        p_dec = float(_mkt.get("current_price") or _mkt.get("price") or 0)
        if p_dec <= 0:
            return True, ""
        from backend.services.strategy_coordinator import StrategyCoordinator
        from backend.services.exchange_config import get_active_exchange

        p_now = float(StrategyCoordinator._get_realtime_price_robust(sym, get_active_exchange()) or 0)
        if p_now <= 0:
            return True, ""
        dev = abs(p_now - p_dec) / p_dec
        if mode == "live":
            max_dev = float(os.getenv("DECISION_PRICE_MAX_DEVIATION_PCT_LIVE", "0.005") or 0.005)
        else:
            max_dev = float(os.getenv("DECISION_PRICE_MAX_DEVIATION_PCT_PAPER", "0.010") or 0.010)
        if dev > max_dev:
            return (
                False,
                f"decision_price_stale dev={dev:.4f}>{max_dev:.4f} p_dec={p_dec:.6g} p_now={p_now:.6g}",
            )
        return True, ""
    except Exception as _e:
        logger.debug("[DecisionPriceGate] 校验异常，fail-open: %s", _e)
        return True, ""
