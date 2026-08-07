"""Replace _expand_multi_tier_decisions in monolith with thin shim."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "backend/services/full_auto_trading_service.py"
lines = path.read_text(encoding="utf-8").splitlines(True)
start = next(i for i, l in enumerate(lines) if "def _expand_multi_tier_decisions" in l)
end = next(i for i, l in enumerate(lines) if i > start and l.strip().startswith("def _factor_veto_check(self"))

shim = '''    def _expand_multi_tier_decisions(
        self,
        decisions: List[Dict],
        strat_tier_map: dict,
        orch_directions: dict,
        session,
    ) -> List[Dict]:
        from backend.services.full_auto.tier_fanout import (
            build_tier_fanout_host,
            expand_multi_tier_decisions,
        )
        return expand_multi_tier_decisions(
            decisions, strat_tier_map, orch_directions, session,
            build_tier_fanout_host(self),
        )

'''

new_lines = lines[:start] + [shim] + lines[end:]
path.write_text("".join(new_lines), encoding="utf-8")
print(f"replaced lines {start+1}-{end} with shim ({end-start} -> {shim.count(chr(10))} lines)")
