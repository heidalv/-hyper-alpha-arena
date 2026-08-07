"""Assemble tier_fanout.py from extracted body."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
body = (ROOT / "backend/services/full_auto/_expand_tier_body.tmp").read_text(encoding="utf-8")

header = '''"""多周期扇出 — 从 monolith _expand_multi_tier_decisions 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, List

logger = logging.getLogger(__name__)


@dataclass
class TierFanoutHost:
    nature_to_tier_map: Dict[str, str]
    append_event: Callable = field(repr=False, default=lambda *a, **k: None)


def build_tier_fanout_host(svc) -> TierFanoutHost:
    return TierFanoutHost(
        nature_to_tier_map=svc._NATURE_TO_TIER_MAP,
        append_event=svc._append_event,
    )


def expand_multi_tier_decisions(
    decisions: List[Dict],
    strat_tier_map: dict,
    orch_directions: dict,
    session,
    host: TierFanoutHost,
) -> List[Dict]:
'''

dedented = []
for line in body.splitlines():
    if line.startswith("        "):
        dedented.append("    " + line[8:])
    else:
        dedented.append(line)
body = "\n".join(dedented)

out = ROOT / "backend/services/full_auto/tier_fanout.py"
out.write_text(header + body + "\n", encoding="utf-8")
print(f"wrote {out} ({out.read_text(encoding='utf-8').count(chr(10))} lines)")
