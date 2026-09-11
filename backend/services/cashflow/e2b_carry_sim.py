# -*- coding: utf-8 -*-
"""E2b — 多场所资金费 carry Paper 模拟（perp_funding → funding_matrix → 双腿执行器）。"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "cashflow" / "carry_sim"


def _env_true(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def carry_enabled() -> bool:
    return _env_true("CASHFLOW_CARRY_SIM_ENABLED", True)


def run_carry_sim(
    *,
    notional_usd: Optional[float] = None,
    min_net_apr: Optional[float] = None,
    horizon_days: Optional[float] = None,
    execute_paper: bool = True,
) -> Dict[str, Any]:
    """扫描 funding 矩阵；有多场所覆盖时选最优 combo 并可选 Paper 双腿模拟。"""
    if not carry_enabled():
        return {"ok": False, "skipped": True, "reason": "CASHFLOW_CARRY_SIM_ENABLED=false"}

    from backend.services.rebate_arb.funding_rate_matrix import scan_funding_matrix
    from backend.services.rebate_arb.funding_rate_provider import has_multi_venue_coverage, latest_funding_by_venue
    from backend.services.rebate_arb.strategies.s_delta_neutral_points import DeltaNeutralPointsStrategy
    from backend.services.rebate_arb.paper_delta_neutral_executor import PaperDeltaNeutralExecutor

    notional = float(notional_usd if notional_usd is not None else _env_float("CASHFLOW_CARRY_NOTIONAL_USD", 500.0))
    min_apr = float(min_net_apr if min_net_apr is not None else _env_float("CASHFLOW_CARRY_MIN_NET_APR", 0.08))
    horizon = float(horizon_days if horizon_days is not None else _env_float("CASHFLOW_CARRY_HORIZON_DAYS", 7.0))

    rates = latest_funding_by_venue(use_cache=False)
    venues = sorted(rates.keys())
    coverage = has_multi_venue_coverage(rates)
    out: Dict[str, Any] = {
        "ok": True,
        "venues": venues,
        "multi_venue_coverage": coverage,
        "symbol_counts": {ex: len(m) for ex, m in rates.items()},
        "combos": [],
        "executions": [],
        "notes": [],
    }
    if not coverage:
        out["ok"] = False
        out["reason"] = "perp_funding 仅单场所或无数据；需 MULTI_VENUE_FUNDING_COLLECTOR_ENABLED=true 且 ≥2 场所入库"
        _write_latest(out)
        return out

    combos = scan_funding_matrix(rates, horizon_days=horizon, min_net_apr=min_apr)
    out["combos"] = [c.to_dict() for c in combos[:20]]
    if not combos:
        out["notes"].append(f"无 combo 净 APR ≥ {min_apr:.0%}（扣费后）")
        _write_latest(out)
        return out

    strat = DeltaNeutralPointsStrategy()
    executor = PaperDeltaNeutralExecutor()
    max_exec = int(_env_float("CASHFLOW_CARRY_MAX_EXEC", 3))
    for combo in combos[:max_exec]:
        cd = combo.to_dict()
        plan = strat.build_execution_plan(notional, combo=cd, paper_mode=True)
        exec_row: Dict[str, Any] = {"combo": cd, "plan_symbol": plan.get("side_a", {}).get("symbol")}
        if execute_paper:
            try:
                res = executor.execute(plan, notional, combo=cd, horizon_days=horizon)
                exec_row["execution"] = res.to_dict()
            except Exception as exc:
                exec_row["execution"] = {"success": False, "error": str(exc)[:200]}
        out["executions"].append(exec_row)
    _write_latest(out)
    return out


def _write_latest(payload: Dict[str, Any]) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / "latest.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def carry_sim_latest() -> Optional[Dict[str, Any]]:
    p = DATA_DIR / "latest.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def scheduled_carry_sim() -> Dict[str, Any]:
    logger.info("[cashflow.e2b] carry sim tick")
    return run_carry_sim()
