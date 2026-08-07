"""Extract A–G method bodies from full_auto_trading_service monolith."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MONO = ROOT / "backend/services/full_auto_trading_service.py"
FA = ROOT / "backend/services/full_auto"
lines = MONO.read_text(encoding="utf-8").splitlines(True)


def find_range(method: str) -> tuple[int, int]:
    """Return [start, end) indices for a class-level method (exclude leading decorators)."""
    start = next(
        i for i, l in enumerate(lines) if re.match(rf"    def {re.escape(method)}\(", l)
    )
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("    @"):
            j = i + 1
            while j < len(lines) and (lines[j].strip() == "" or lines[j].startswith("    @")):
                j += 1
            if j < len(lines) and lines[j].startswith("    def "):
                end = i
                break
        elif lines[i].startswith("    def "):
            end = i
            break
    # safety: next class-level def must not be inside body
    for i in range(start + 1, end):
        if re.match(r"    def ", lines[i]):
            raise SystemExit(f"boundary overflow in {method} at line {i+1}")
    return start, end


def extract_body(method: str) -> str:
    start, end = find_range(method)
    chunk = "".join(lines[start:end])
    after = chunk.split(f"def {method}", 1)[1]
    m = re.search(r'"""[\s\S]*?"""\n(.*)', after, re.DOTALL)
    if m:
        return m.group(1).rstrip() + "\n"
    m2 = re.search(r"\)\s*(?:->[^:]*)?:\n(.*)", after, re.DOTALL)
    if not m2:
        raise SystemExit(f"no body for {method}")
    return m2.group(1).rstrip() + "\n"


def apply(text: str, attrs=(), fns=(), module_fns=()) -> str:
    text = re.sub(r"\bself\.", "host.", text)
    text = re.sub(r"\bgetattr\(self,", "getattr(host,", text)
    text = re.sub(r"\bhasattr\(self,", "hasattr(host,", text)
    for a in attrs:
        text = text.replace(f"host._{a}", f"host.{a}")
    for f in fns:
        text = text.replace(f"host._{f}", f"host.{f}")
    for f in module_fns:
        # self._foo -> host._foo; rewrite to module-level foo(
        text = text.replace(f"host._{f}(", f"{f}(")
        text = text.replace(f"host.{f}(", f"{f}(")
    # getattr(host, '_x' / "_x"
    for a in attrs:
        text = text.replace(f"getattr(host, '_{a}'", f"getattr(host, '{a}'")
        text = text.replace(f'getattr(host, "_{a}"', f'getattr(host, "{a}"')
        text = text.replace(f"hasattr(host, '_{a}'", f"hasattr(host, '{a}'")
        text = text.replace(f'hasattr(host, "_{a}"', f'hasattr(host, "{a}"')
    return text


# method -> (attrs, fns, module_fns)
SPECS: dict[str, tuple[tuple, tuple, tuple]] = {
    # A paper_session_helpers
    "_paper_auto_unlock_session": (
        ("defensive_entered_at", "recovery_until", "symbol_frozen_set", "strat_pause_meta"),
        (
            "paper_loss_locks_disabled",
            "clear_strategy_pause_meta",
            "get_trading_account_id",
            "invalidate_session_status_cache",
            "should_log_pause_event",
            "append_event",
        ),
        ("cap_paper_active_strategies",),
    ),
    "_cap_paper_active_strategies": (
        (),
        ("paper_loss_locks_disabled", "record_strategy_pause"),
        (),
    ),
    "_get_trade_history": (
        (),
        ("get_trading_account_id", "utc_iso"),
        (),
    ),
    "_cleanup_duplicate_strategies": (
        (),
        ("safe_commit",),
        (),
    ),
    # B orch_background
    "_build_fast_stability_result": ((), (), ()),
    "_purge_stale_caches": (
        (
            "last_cache_purge",
            "last_close_time",
            "last_reduce_time",
            "master_strat_cache",
            "partial_close_tracker",
            "market_scan_cache",
            "market_scan_cache_ts",
            "strategy_creation_ts",
            "health_status",
        ),
        (),
        (),
    ),
    "_inject_orch_scheduled_stubs": (
        ("current_ai_tiers", "last_orch_decisions", "last_orch_decisions_ts"),
        ("ensure_fresh_orch_decisions", "tier_confidence_pct"),
        (),
    ),
    "_ensure_orchestrator_bg_running": (
        (
            "orch_bg_thread",
            "orch_bg_session_id",
            "orch_bg_symbols",
            "orch_bg_running",
            "last_unified_snapshot",
            "market_scan_cache",
            "market_scan_cache_ts",
            "last_orch_decisions",
            "last_orch_decisions_ts",
        ),
        ("resolve_session_trade_symbols", "active_exchange", "orch_payload_from_decision"),
        (),
    ),
    # C decision_sizing
    "_ai_dynamic_position_pct": ((), (), ()),
    "_apply_tdi_position_advice": ((), (), ()),
    "_resolve_alignment_scale": (("last_orch_decisions",), (), ()),
    "_resolve_decision_leverage": ((), (), ()),
    "_resolve_decision_position_pct": (
        (),
        ("extract_ai_position_pct",),
        ("ai_dynamic_position_pct", "apply_tdi_position_advice"),
    ),
    "_calibrate_confidence": (("pre_screen_passed",), (), ()),
    # D tp_sl_gates
    "_factor_veto_check": (("v3_factor_cache",), (), ()),
    "_validate_tp_sl_by_nature": ((), (), ("compute_dynamic_min_sl",)),
    "_compute_dynamic_min_sl": ((), (), ()),
    # E live_trading
    "_live_constitutional_enabled": ((), ("is_live_trading_session",), ()),
    "_fetch_live_account_snapshot": ((), (), ()),
    "_live_constitutional_pre_trade_check": (
        (),
        (),
        ("live_constitutional_enabled", "fetch_live_account_snapshot"),
    ),
    "_check_live_constitutional_session_risk": (
        ("defensive_entered_at",),
        (
            "should_switch_mode",
            "append_event",
            "invalidate_session_status_cache",
            "should_log_pause_event",
        ),
        ("live_constitutional_enabled", "fetch_live_account_snapshot"),
    ),
    "_execute_live_trade": (
        (),
        ("is_unified_executor_on", "append_event"),
        ("live_constitutional_pre_trade_check",),
    ),
    # F midlong_helpers
    "_resolve_independent_strategy": ((), ("get_trading_account_id",), ()),
    "_try_execute_independent_agent_open": (
        (),
        ("append_event", "evaluate_and_execute_proposal"),
        (),
    ),
    "_record_midlong_factor_snapshots": ((), (), ()),
    "_persist_independent_scan_log": ((), (), ()),
    "_inject_midlong_indicators": ((), (), ()),
    # G data_health
    "_check_data_health": (
        ("symbol_frozen_set", "health_status"),
        ("freeze_symbol_strategies", "append_event"),
        (),
    ),
}

# After module_fns rewrite, inject host into same-module calls that need it.
HOST_CALL_FIXES = {
    "_paper_auto_unlock_session": [
        (
            "cap_paper_active_strategies(db, session, active_ids)",
            "cap_paper_active_strategies(db, session, active_ids, host)",
        ),
    ],
    "_validate_tp_sl_by_nature": [
        (
            "compute_dynamic_min_sl(symbol, trade_nature, entry_price, min_sl)",
            "compute_dynamic_min_sl(symbol, trade_nature, entry_price, min_sl)",
        ),
    ],
    "_live_constitutional_pre_trade_check": [
        (
            "live_constitutional_enabled(session)",
            "live_constitutional_enabled(session, host)",
        ),
        (
            "fetch_live_account_snapshot(db, account_id)",
            "fetch_live_account_snapshot(db, account_id)",
        ),
    ],
    "_check_live_constitutional_session_risk": [
        (
            "live_constitutional_enabled(session)",
            "live_constitutional_enabled(session, host)",
        ),
        (
            "fetch_live_account_snapshot(db, account_id)",
            "fetch_live_account_snapshot(db, account_id)",
        ),
    ],
    "_execute_live_trade": [
        (
            "live_constitutional_pre_trade_check(\n                db, session, strat, decision\n            )",
            "live_constitutional_pre_trade_check(\n                db, session, strat, decision, host\n            )",
        ),
    ],
    "_resolve_decision_position_pct": [
        (
            "ai_dynamic_position_pct(\n            confidence, vol_value, open_position_count,\n            tier=tier, tier_budget_pct=tier_budget_pct,\n        )",
            "ai_dynamic_position_pct(\n            confidence, vol_value, open_position_count,\n            tier=tier, tier_budget_pct=tier_budget_pct,\n        )",
        ),
        (
            "apply_tdi_position_advice(",
            "apply_tdi_position_advice(",
        ),
    ],
}

for method, (attrs, fns, module_fns) in SPECS.items():
    body = extract_body(method)
    body = apply(body, attrs=attrs, fns=fns, module_fns=module_fns)
    for old, new in HOST_CALL_FIXES.get(method, []):
        if old != new:
            body = body.replace(old, new)
    # tmp name: method already starts with _; Path uses __method_body.tmp
    out = FA / f"_{method}_body.tmp"
    out.write_text(body, encoding="utf-8")
    leftover_self = re.findall(r"\bself\.", body)
    leftover_priv = re.findall(r"host\._[A-Za-z0-9]+", body)
    print(
        f"{method}: {len(body.splitlines())} lines -> {out.name}"
        f" self={len(leftover_self)} priv={leftover_priv[:5]}"
    )

print("extract done")
