"""Extract validate_ai_decisions from monolith."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
lines = (ROOT / "backend/services/full_auto_trading_service.py").read_text(encoding="utf-8").splitlines(True)
start = next(i for i, l in enumerate(lines) if "def _validate_ai_decisions(self" in l)
end = next(i for i, l in enumerate(lines) if i > start and "def _register_qaa_agents(self" in l)
chunk = "".join(lines[start:end])
m = re.search(r'"""[\s\S]*?"""\n(.*)', chunk.split("def _validate_ai_decisions(self", 1)[1], re.DOTALL)
body = m.group(1).rstrip() + "\n"

def apply(text: str) -> str:
    text = re.sub(r"\bself\.", "host.", text)
    text = text.replace("host._VALID_RISK_LEVELS", "VALID_RISK_LEVELS")
    text = text.replace("host._VALID_ACTIONS", "VALID_ACTIONS")
    text = text.replace("host._NATURE_TO_TIER_MAP", "host.nature_to_tier_map")
    text = text.replace("host._health_status", "host.health_status")
    text = text.replace('getattr(host, "_last_unified_snapshot"', 'getattr(host, "last_unified_snapshot"')
    for n in ("append_event", "event_scope_label"):
        text = text.replace(f"host._{n}", f"host.{n}")
    return text

(ROOT / "backend/services/full_auto/_validate_ai_body.tmp").write_text(apply(body), encoding="utf-8")
print(f"wrote validate body ({body.count(chr(10))} lines)")
