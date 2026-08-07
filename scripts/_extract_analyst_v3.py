"""Extract _run_analyst_system_v3 from monolith."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
lines = (ROOT / "backend/services/full_auto_trading_service.py").read_text(encoding="utf-8").splitlines(True)
start = next(i for i, l in enumerate(lines) if "def _run_analyst_system_v3(" in l)
end = next(i for i, l in enumerate(lines) if i > start and "def _write_qaa_v3_forced_decision_logs(" in l)
chunk = "".join(lines[start:end])
m = re.search(r'"""[\s\S]*?"""\n(.*)', chunk.split("def _run_analyst_system_v3(", 1)[1], re.DOTALL)
body = m.group(1).rstrip() + "\n"

def apply(text: str) -> str:
    text = re.sub(r"\bself\.", "host.", text)
    text = text.replace("host._active_db_sessions", "host.active_db_sessions")
    text = text.replace("host._mlto_handled_keys", "host.mlto_handled_keys")
    for n in (
        "annotate_auto_coin_meta", "build_fast_stability_result", "run_with_timeout",
        "inject_orch_scheduled_stubs", "execute_master_decisions",
        "maintain_mlto_theses_for_session", "write_qaa_v3_forced_decision_logs",
    ):
        text = text.replace(f"host._{n}", f"host.{n}")
    return text

(ROOT / "backend/services/full_auto/_analyst_v3_body.tmp").write_text(apply(body), encoding="utf-8")
print(f"wrote analyst v3 body ({body.count(chr(10))} lines)")
