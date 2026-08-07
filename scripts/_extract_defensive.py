"""Extract defensive methods from monolith."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
lines = (ROOT / "backend/services/full_auto_trading_service.py").read_text(encoding="utf-8").splitlines(True)
start = next(i for i, l in enumerate(lines) if "def _execute_defensive_analysis(self" in l)
end = next(i for i, l in enumerate(lines) if i > start and "def _execute_live_trade(self" in l)
chunk = "".join(lines[start:end])

splits = [
    ("_execute_defensive_analysis", "_execute_defensive_verdicts"),
    ("_execute_defensive_verdicts", "_rule_based_defensive"),
    ("_rule_based_defensive", None),
]

def extract_body(full: str, name: str, next_name: str | None) -> str:
    a = full.index(f"def {name}")
    b = full.index(f"def {next_name}") if next_name else len(full)
    piece = full[a:b]
    m = re.search(r'"""[\s\S]*?"""\n(.*)', piece.split(f"def {name}", 1)[1], re.DOTALL)
    if not m:
        raise SystemExit(f"no body for {name}")
    return m.group(1).rstrip() + "\n"

def apply(text: str) -> str:
    text = re.sub(r"\bself\.", "host.", text)
    text = text.replace("host.TIER_PROTECTION", "host.tier_protection")
    text = text.replace("host.DEFAULT_PROTECTION", "host.default_protection")
    for n in ("append_event", "get_trading_account_id"):
        text = text.replace(f"host._{n}", f"host.{n}")
    text = text.replace("host._rule_based_defensive(", "run_rule_based_defensive(")
    text = text.replace("host._execute_defensive_verdicts(", "run_defensive_verdicts(")
    text = text.replace(
        "run_rule_based_defensive(db, session, positions_list, market_summary)",
        "run_rule_based_defensive(db, session, positions_list, market_summary, host)",
    )
    text = text.replace(
        "run_defensive_verdicts(db, session, _trading_acct_id, verdicts, positions_list)",
        "run_defensive_verdicts(db, session, _trading_acct_id, verdicts, positions_list, host)",
    )
    text = text.replace(
        "run_defensive_verdicts(db, session, host.get_trading_account_id(db, session), verdicts, positions_list)",
        "run_defensive_verdicts(db, session, host.get_trading_account_id(db, session), verdicts, positions_list, host)",
    )
    return text

for name, nxt in splits:
    body = apply(extract_body(chunk, name, nxt))
    out = ROOT / f"backend/services/full_auto/_defensive_{name.removeprefix('_')}_body.tmp"
    out.write_text(body, encoding="utf-8")
    print(f"wrote {out.name} ({body.count(chr(10))} lines)")
