"""Extract _evaluate_and_execute_proposal from monolith."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "backend/services/full_auto_trading_service.py"
lines = path.read_text(encoding="utf-8").splitlines(True)

start = next(i for i, l in enumerate(lines) if "def _evaluate_and_execute_proposal(" in l)
end = next(i for i, l in enumerate(lines) if i > start and "def _try_execute_independent_agent_open(" in l)
chunk = "".join(lines[start:end])
m = re.search(r'"""[\s\S]*?"""\n(.*)', chunk.split("def _evaluate_and_execute_proposal(", 1)[1], re.DOTALL)
body = m.group(1).rstrip() + "\n"

def apply(text: str) -> str:
    text = re.sub(r"\bself\.", "host.", text)
    for fn in (
        "midlong_persistence_allow", "resolve_independent_strategy", "session_trading_mode",
        "persist_tcp_snapshot", "build_portfolio_for_agents", "decision_price_consistency_ok",
        "append_event", "live_constitutional_pre_trade_check", "execute_live_trade",
        "safe_commit", "execute_paper_trade", "record_midlong_factor_snapshots",
    ):
        text = text.replace(f"host._{fn}", f"host.{fn}")
    return text

out = ROOT / "backend/services/full_auto/_proposal_exec_body.tmp"
out.write_text(apply(body), encoding="utf-8")
print("extracted proposal exec body", len(body.splitlines()), "lines")
