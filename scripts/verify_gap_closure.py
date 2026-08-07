#!/usr/bin/env python3
"""GAP 闭环设计落地自检。

  python scripts/verify_gap_closure.py
  python scripts/verify_gap_closure.py --replay BTC mid
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
except Exception:
    pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay", nargs=2, metavar=("SYMBOL", "TIER"), default=None)
    args = parser.parse_args()

    fails = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal fails
        if not ok:
            fails += 1
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {name}" + (f" - {detail}" if detail else ""))

    print("=== verify_gap_closure ===\n")

    # 1. 核心模块导入
    try:
        from backend.services.decision_core.proposal import TradeProposal
        p = TradeProposal.from_agent(sym="BTC", tier="mid", action="buy", confidence=55, trade_nature="swing")
        check("TradeProposal", p.proposal_id and p.symbol == "BTC")
    except Exception as e:
        check("TradeProposal", False, str(e))

    try:
        from backend.services.decision_core.data_contract import check_data_contract
        r = check_data_contract("mid", {
            "price": 100,
            "indicators_1h": {"rsi": 50},
            "indicators_4h": {"rsi": 50},
            "indicators_1d": {"rsi": 50},
        })
        check("DataContract mid", r.ok, r.reason)
    except Exception as e:
        check("DataContract", False, str(e))

    try:
        from backend.services.constitutional_profile import get_profile
        live = get_profile("live")
        paper = get_profile("paper")
        check("ConstitutionalProfile live", not live.probe_enabled and not live.override_allowed)
        check("ConstitutionalProfile paper", paper.probe_enabled)
    except Exception as e:
        check("ConstitutionalProfile", False, str(e))

    try:
        from backend.services.budget_service import budget_service
        cap = budget_service.get_layer_cap("swing", 10000)
        check("BudgetService", cap > 0, f"swing_cap={cap:.0f}")
    except Exception as e:
        check("BudgetService", False, str(e))

    try:
        from backend.services.decision_snapshot_writer import decision_snapshot_writer
        snap = decision_snapshot_writer.build(
            session_id=1, symbol="ETH", tier="mid", action="hold", confidence=0,
            proposal={"proposal_id": "test"},
            evaluate_verdict={"allowed": False, "reason": "test"},
        )
        check("DecisionSnapshotWriter", snap.symbol == "ETH")
    except Exception as e:
        check("DecisionSnapshotWriter", False, str(e))

    try:
        from backend.services.runtime_governor import runtime_governor
        check("RuntimeGovernor", callable(runtime_governor.list_pending))
    except Exception as e:
        check("RuntimeGovernor", False, str(e))

    try:
        from backend.services.replay.replay_harness import replay_harness
        check("ReplayHarness", replay_harness is not None)
    except Exception as e:
        check("ReplayHarness", False, str(e))

    try:
        from backend.services.orchestrator_derivatives import enrich_orchestrator_derivatives
        d = enrich_orchestrator_derivatives("BTC", {"price": 100, "funding_rate": 0.0001})
        check("OrchestratorDerivatives", "funding_rate" in d)
    except Exception as e:
        check("OrchestratorDerivatives", False, str(e))

    try:
        from backend.services.decision_core.execute_proposal import evaluate_proposal
        from backend.services.decision_core.proposal import TradeProposal
        prop = TradeProposal.from_agent(sym="BTC", tier="mid", action="buy", confidence=55, trade_nature="swing")
        mkt = {"price": 100, "indicators_1h": {"rsi": 50}, "indicators_4h": {"rsi": 50}, "indicators_1d": {"rsi": 50}}
        v = evaluate_proposal(db=None, account_id=0, proposal=prop, market_data=mkt, mode="paper")
        check("evaluate_proposal", isinstance(v.allowed, bool), v.reason[:60])
    except Exception as e:
        check("evaluate_proposal", False, str(e))

    try:
        from backend.services.full_auto_trading_service import FullAutoTradingService
        svc = FullAutoTradingService()
        check("TCP _evaluate_and_execute_proposal", hasattr(svc, "_evaluate_and_execute_proposal"))
    except Exception as e:
        check("FullAuto TCP", False, str(e))

    from backend.config import settings as s
    check("MIDLONG_MASTER_DELEGATE", bool(getattr(s, "MIDLONG_MASTER_DELEGATE", False)))
    check("LIVE_CONSTITUTIONAL_RISK", bool(getattr(s, "LIVE_CONSTITUTIONAL_RISK_ENABLED", False)))

    if args.replay:
        sym, tier = args.replay[0].upper(), args.replay[1].lower()
        from backend.services.replay.replay_harness import replay_harness
        rep = replay_harness.run(symbol=sym, tier=tier)
        print(f"\n--- replay {sym} {tier} ---")
        print(rep.to_dict())
        check("Replay run", rep.bars > 0, f"bars={rep.bars} proposals={rep.proposals}")

    try:
        from backend.services.replay.atas_proposer import atas_factor_to_proposal
        prop, _ = atas_factor_to_proposal("BTC", {"price": 100, "factor_v3": {"direction": 0.5, "strength": 0.6}}, tier="mid")
        check("ATASProposer", prop is not None and prop.source_lane == "atas_replay")
    except Exception as e:
        check("ATASProposer", False, str(e))

    try:
        from backend.services.audit_chain_service import append_to_chain, verify_chain
        h = append_to_chain(
            {"symbol": "TEST", "tier": "mid", "action": "hold", "proposal": {}, "verdict": {}},
            mode="paper",
        )
        check("AuditChain", bool(h.get("content_hash")))
        records = [{
            "symbol": "TEST",
            "tier": "mid",
            "action": "hold",
            "content_hash": h["content_hash"],
            "prev_hash": h.get("prev_hash"),
            "proposal_json": {},
            "evaluate_verdict_json": {},
        }]
        v = verify_chain(records)
        check("AuditChain verify", v.get("valid") is True, str(v.get("errors")))
    except Exception as e:
        check("AuditChain", False, str(e))

    try:
        from backend.services.snapshot_reconcile_service import reconcile_recent_snapshots
        r = reconcile_recent_snapshots(hours=1)
        check("SnapshotReconcile", "checked" in r)
    except Exception as e:
        check("SnapshotReconcile", False, str(e))

    try:
        from backend.services.decision_core.execute_proposal import evaluate_scalp_proposal
        from backend.services.decision_core.proposal import TradeProposal
        sp = TradeProposal.from_agent(sym="BTC", tier="short", action="buy", confidence=55, trade_nature="scalp")
        sv = evaluate_scalp_proposal(
            db=None, account_id=0, proposal=sp,
            market_data={"price": 100, "volatility_value": 0.02},
            gate_allowed=False, gate_reason="test_block", mode="paper",
        )
        check("evaluate_scalp_proposal block", not sv.allowed)
    except Exception as e:
        check("evaluate_scalp_proposal", False, str(e))

    try:
        from backend.services.session_symbols import resolve_session_trade_symbols
        check("session_symbols", callable(resolve_session_trade_symbols))
    except Exception as e:
        check("session_symbols", False, str(e))

    print(f"\n=== result === FAIL={fails}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
