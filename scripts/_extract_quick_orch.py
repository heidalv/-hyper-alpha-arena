"""Extract quick orchestrator eval from monolith."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "backend/services/full_auto_trading_service.py"
lines = path.read_text(encoding="utf-8").splitlines(True)
FA = ROOT / "backend/services/full_auto"

start = next(i for i, l in enumerate(lines) if "def _run_quick_orchestrator_eval(self" in l)
end = next(i for i, l in enumerate(lines) if i > start and "# ══════════════════════════════════════════════════" in l and "工具" in "".join(lines[i:i+3]))
chunk = "".join(lines[start:end])
m = re.search(r'"""[\s\S]*?"""\n(.*)', chunk.split("def _run_quick_orchestrator_eval", 1)[1], re.DOTALL)
body = m.group(1).rstrip() + "\n"


def apply(text: str) -> str:
    text = re.sub(r"\bself\.", "host.", text)
    for attr in (
        "active_db_sessions", "deadlock_rescue_count", "DEADLOCK_RESCUE_MAX",
        "NATURE_TO_TIER_MAP",
    ):
        text = text.replace(f"host._{attr}", f"host.{attr}")
    for fn in (
        "get_trading_account_id", "active_exchange", "paper_loss_locks_disabled",
        "get_lock_profile", "record_strategy_pause", "should_log_pause_event",
        "append_event", "clear_strategy_pause_meta", "can_resume_strategy",
        "safe_commit",
    ):
        text = text.replace(f"host._{fn}", f"host.{fn}")
    return text


(FA / "_quick_orch_body.tmp").write_text(apply(body), encoding="utf-8")
print("extracted quick orch", len(body.splitlines()), "lines", "end=", end)
