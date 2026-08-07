"""Shim light trading, v3 factor, strategy lifecycle into monolith."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "backend/services/full_auto_trading_service.py"
text = path.read_text(encoding="utf-8")
lines = text.splitlines(True)


def replace_block(start_pat: str, end_pat: str, shim: str) -> None:
    global lines
    start = next(i for i, l in enumerate(lines) if start_pat in l)
    end = next(i for i, l in enumerate(lines) if i > start and end_pat in l)
    lines = lines[:start] + [shim] + lines[end:]
    print(f"shim {start_pat!r}: removed {end - start} lines")


# 1) v3 factor (earlier in file)
v3_shim = '''    def _run_v3_factor_pipeline(
        self,
        db: Session = None,
        session=None,
        symbols: List[str] = None,
        market_summary: Dict[str, Any] = None,
        unified_snapshot=None,
        force: bool = False,
    ) -> tuple:
        from backend.services.full_auto.v3_factor_pipeline import (
            build_v3_factor_host,
            run_v3_factor_pipeline,
        )
        host = build_v3_factor_host(self)
        result = run_v3_factor_pipeline(
            host=host,
            db=db,
            session=session,
            symbols=symbols,
            market_summary=market_summary,
            unified_snapshot=unified_snapshot,
            force=force,
        )
        self._v3_factor_cache = host.v3_factor_cache
        return result

'''
replace_block("def _run_v3_factor_pipeline(", "def _run_with_timeout(self", v3_shim)

# 2) strategy lifecycle: champion through terminate, keep REGIME section then shim get_regime+adapt
life_shim = '''    def _is_champion_strategy(self, mem) -> bool:
        from backend.services.full_auto.strategy_lifecycle import is_champion_strategy
        return is_champion_strategy(mem)

    def _should_terminate_strategy(self, db: Session, strategy, session) -> tuple:
        from backend.services.full_auto.strategy_lifecycle import should_terminate_strategy
        return should_terminate_strategy(db, strategy, session)

    def _pause_champion_strategy(self, db: Session, strategy, reason: str):
        from backend.services.full_auto.strategy_lifecycle import pause_champion_strategy
        return pause_champion_strategy(db, strategy, reason)

    def _snapshot_strategy_genome(self, db: Session, strategy, memory):
        from backend.services.full_auto.strategy_lifecycle import snapshot_strategy_genome
        return snapshot_strategy_genome(db, strategy, memory)

    def _terminate_strategy(self, db: Session, strategy, reason: str):
        from backend.services.full_auto.strategy_lifecycle import (
            build_strategy_lifecycle_host,
            terminate_strategy,
        )
        return terminate_strategy(db, strategy, reason, build_strategy_lifecycle_host(self))

'''
replace_block(
    "def _is_champion_strategy(self",
    "# ══════════════════════════════════════════════════════════════",
    life_shim,
)

# After life shim, REGIME section still there. Replace from REGIME_PARAM_PROFILES through adapt end.
# Re-find indices after previous edits.
start = next(i for i, l in enumerate(lines) if "REGIME_PARAM_PROFILES = {" in l)
# go back to comment block if present
while start > 0 and (
    lines[start - 1].strip().startswith("#")
    or lines[start - 1].strip() == ""
    or "═══" in lines[start - 1]
):
    start -= 1
    if "策略参数自适应" in lines[start]:
        # include the banner line above
        if start > 0 and "═══" in lines[start - 1]:
            start -= 1
        break

end = next(i for i, l in enumerate(lines) if i > start and "def _try_create_from_template(" in l)
adapt_shim = '''    # REGIME_PARAM_PROFILES / adapt 已迁至 full_auto.strategy_lifecycle

    def _get_regime_profile(self, regime: str) -> dict:
        from backend.services.full_auto.strategy_lifecycle import get_regime_profile
        return get_regime_profile(regime)

    def _adapt_strategy_params(self, db: Session, strategy, market_info: dict):
        from backend.services.full_auto.strategy_lifecycle import adapt_strategy_params
        return adapt_strategy_params(db, strategy, market_info)

'''
lines = lines[:start] + [adapt_shim] + lines[end:]
print(f"shim adapt/regime: removed {end - start} lines")

# 3) light trading
light_shim = '''    def _run_light_trading_cycle(self, session_id: str):
        from backend.services.full_auto.light_trading_cycle import (
            build_light_trading_host,
            run_light_trading_cycle,
        )
        host = build_light_trading_host(self)
        run_light_trading_cycle(session_id, host)
        self._last_unified_snapshot = host.last_unified_snapshot

'''
replace_block(
    "def _run_light_trading_cycle(self",
    "def _run_quick_orchestrator_eval(self",
    light_shim,
)

path.write_text("".join(lines), encoding="utf-8")
print("shim batch done")
