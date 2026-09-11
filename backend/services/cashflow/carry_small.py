# -*- coding: utf-8 -*-
"""Carry 小资金（研究桶 ~5%，v3 方向 5 / 方向 7 上线协议，p2-arb-infra）。

与 E2b Paper 模拟的关系
----------------------
- `e2b_carry_sim`：任意名义、只模拟，不谈晋升。
- 本模块：上线协议的「小资金」档——名义锁定在研究桶占比（默认权益的 5%），
  且必须过套利 scorecard 晋升门；默认仍走 Paper 执行器。

安全默认（类比 CASHFLOW_IDLE_EARN_LIVE）
--------------------------------------
  CASHFLOW_CARRY_SMALL_ENABLED=true   # 任务/API 可跑
  CASHFLOW_CARRY_SMALL_LIVE=false     # false=只 Paper；true 才尝试真下单
  Live 还要过：scorecard.promotion_gate + arb_switches.live_trading_enabled
  （后者当前硬关 False → 即使 LIVE=true 也拒真单，只记 blocked）
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "cashflow" / "carry_small"


def _env_true(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def small_enabled() -> bool:
    return _env_true("CASHFLOW_CARRY_SMALL_ENABLED", True)


def live_requested() -> bool:
    """用户显式请求小资金 Live；仍受 live_trading_enabled 硬关。"""
    return _env_true("CASHFLOW_CARRY_SMALL_LIVE", False)


def research_equity_usd() -> float:
    """研究桶权益基准。优先 env；否则回落模拟盘默认 5000。"""
    return max(1.0, _env_float("CASHFLOW_CARRY_RESEARCH_EQUITY_USD", 5000.0))


def research_bucket_pct() -> float:
    """方案：小资金 = 研究桶 5%。可调，硬帽 ≤ 20% 防误配。"""
    pct = _env_float("CASHFLOW_CARRY_RESEARCH_BUCKET_PCT", 0.05)
    return max(0.01, min(pct, 0.20))


def capped_notional_usd() -> float:
    return round(research_equity_usd() * research_bucket_pct(), 2)


def _scorecard_gate() -> Dict[str, Any]:
    try:
        from backend.services.arbitrage.scorecard import latest_scorecard, compute_scorecard
        sc = latest_scorecard()
        if not sc:
            sc = compute_scorecard(days=int(os.getenv("ARB_SCORECARD_DAYS", "30") or 30))
        gate = (sc or {}).get("promotion_gate") or {}
        return {"passed": bool(gate.get("passed")), "gate": gate, "kpi": (sc or {}).get("kpi")}
    except Exception as exc:
        return {"passed": False, "gate": {"reasons": [f"scorecard 不可用: {exc}"]}, "kpi": None}


def _live_trading_hard_off() -> bool:
    """True = 硬关，禁止真下单。"""
    try:
        from backend.services.rebate_arb.arb_switches import get_arb_switch_status
        return not bool(get_arb_switch_status().live_trading_enabled)
    except Exception:
        return True  # 读不到开关 → 保守拒真单


def run_carry_small(*, force_refresh_gate: bool = False) -> Dict[str, Any]:
    """跑一趟小资金 carry：门控 → 帽名义 → Paper（或被硬关挡住的 Live 意图）。"""
    if not small_enabled():
        return {"ok": False, "skipped": True, "reason": "CASHFLOW_CARRY_SMALL_ENABLED=false"}

    notional = capped_notional_usd()
    out: Dict[str, Any] = {
        "ok": False,
        "ts_ms": int(time.time() * 1000),
        "research_equity_usd": research_equity_usd(),
        "bucket_pct": research_bucket_pct(),
        "notional_usd": notional,
        "live_requested": live_requested(),
        "mode": "paper",
        "notes": [],
    }

    if force_refresh_gate:
        try:
            from backend.services.arbitrage.scorecard import compute_scorecard
            compute_scorecard(days=int(os.getenv("ARB_SCORECARD_DAYS", "30") or 30))
        except Exception as exc:
            out["notes"].append(f"强制刷新 scorecard 失败: {exc}")

    gate_info = _scorecard_gate()
    out["promotion_gate"] = gate_info.get("gate")
    out["kpi"] = gate_info.get("kpi")
    if not gate_info.get("passed"):
        out["reason"] = "未过 scorecard 晋升门"
        out["notes"].append("小资金资格 = scorecard.promotion_gate.passed；过门 ≠ 自动开 Live")
        _persist(out)
        return out

    # 过门后：默认仍 Paper（复用 E2b 执行器，名义换成研究桶帽）
    want_live = live_requested()
    if want_live and _live_trading_hard_off():
        out["notes"].append(
            "CASHFLOW_CARRY_SMALL_LIVE=true 但 arb_switches.live_trading_enabled=false（硬关）→ 降级 Paper"
        )
        want_live = False
        out["live_blocked"] = True

    if want_live:
        out["mode"] = "live"
        out["ok"] = False
        out["reason"] = "Live 执行器尚未接线（p2 只开资格与帽名义；真腿下单留 P3）"
        out["notes"].append("本阶段不发真单，避免在无 OMS 腿对账完备前裸开 delta-neutral")
        _persist(out)
        return out

    from backend.services.cashflow.e2b_carry_sim import run_carry_sim
    sim = run_carry_sim(notional_usd=notional, execute_paper=True)
    out["ok"] = bool(sim.get("ok"))
    out["mode"] = "paper"
    out["sim"] = {
        "venues": sim.get("venues"),
        "multi_venue_coverage": sim.get("multi_venue_coverage"),
        "n_combos": len(sim.get("combos") or []),
        "n_executions": len(sim.get("executions") or []),
        "reason": sim.get("reason"),
        "notes": sim.get("notes") or [],
    }
    if not out["ok"]:
        out["reason"] = sim.get("reason") or "carry sim 未产出"
    _persist(out)
    return out


def _persist(payload: Dict[str, Any]) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / "latest.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    except Exception as exc:
        logger.warning("[carry_small] 落盘失败: %s", exc)


def carry_small_latest() -> Optional[Dict[str, Any]]:
    p = DATA_DIR / "latest.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def carry_small_status() -> Dict[str, Any]:
    return {
        "enabled": small_enabled(),
        "live_requested": live_requested(),
        "live_hard_off": _live_trading_hard_off(),
        "research_equity_usd": research_equity_usd(),
        "bucket_pct": research_bucket_pct(),
        "capped_notional_usd": capped_notional_usd(),
        "latest": carry_small_latest(),
        "note": "小资金 = 研究桶占比帽名义 + scorecard 门；Live 另受 live_trading_enabled 硬关",
    }


def scheduled_carry_small() -> Dict[str, Any]:
    logger.info("[cashflow.carry_small] tick notional=%s", capped_notional_usd())
    return run_carry_small()
