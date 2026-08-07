"""Extract MLTO maintain + execute lane from monolith."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "backend/services/full_auto_trading_service.py"
lines = path.read_text(encoding="utf-8").splitlines(True)
FA = ROOT / "backend/services/full_auto"


def extract_body(start_pat: str, end_pat: str, method: str) -> str:
    start = next(i for i, l in enumerate(lines) if start_pat in l)
    end = next(i for i, l in enumerate(lines) if i > start and end_pat in l)
    chunk = "".join(lines[start:end])
    m = re.search(r'"""[\s\S]*?"""\n(.*)', chunk.split(f"def {method}", 1)[1], re.DOTALL)
    if not m:
        # execute_mlto_lane may lack docstring
        after = chunk.split(f"def {method}", 1)[1]
        # skip signature until first indented body line after ):
        m2 = re.search(r"\)\s*->[^:]*:\n(.*)", after, re.DOTALL)
        if not m2:
            m2 = re.search(r"\)\s*:\n(.*)", after, re.DOTALL)
        if not m2:
            raise SystemExit(f"no body for {method}")
        return m2.group(1).rstrip() + "\n"
    return m.group(1).rstrip() + "\n"


def apply(text: str) -> str:
    text = re.sub(r"\bself\.", "host.", text)
    for attr in (
        "mlto_handled_keys", "mlto_handled_lock", "midlong_persistence_state",
        "current_ai_tiers", "last_orch_decisions", "last_orch_decisions_ts",
    ):
        text = text.replace(f"host._{attr}", f"host.{attr}")
    for fn in (
        "inject_midlong_indicators", "append_event", "format_agent_event_detail",
        "try_execute_independent_agent_open", "persist_independent_scan_log",
        "build_midlong_agent_envelope",
    ):
        text = text.replace(f"host._{fn}", f"host.{fn}")
    return text


maintain = extract_body(
    "def _maintain_mlto_theses_for_session(",
    "def _execute_mlto_lane(",
    "_maintain_mlto_theses_for_session",
)
# execute has no docstring — extract manually
start = next(i for i, l in enumerate(lines) if "def _execute_mlto_lane(" in l)
end = next(i for i, l in enumerate(lines) if i > start and "def _build_midlong_agent_envelope(" in l)
chunk = "".join(lines[start:end])
after = chunk.split("def _execute_mlto_lane(", 1)[1]
m2 = re.search(r"\)\s*->[^:]*:\n(.*)", after, re.DOTALL)
if not m2:
    m2 = re.search(r"\)\s*:\n(.*)", after, re.DOTALL)
execute = m2.group(1).rstrip() + "\n"

(FA / "_mlto_maintain_body.tmp").write_text(apply(maintain), encoding="utf-8")
(FA / "_mlto_execute_body.tmp").write_text(apply(execute), encoding="utf-8")
print("extracted MLTO bodies", len(maintain.splitlines()), len(execute.splitlines()))
