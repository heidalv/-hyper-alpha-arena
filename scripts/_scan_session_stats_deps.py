"""List self._* usages in session stats + defensive methods."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
lines = (ROOT / "backend/services/full_auto_trading_service.py").read_text(encoding="utf-8").splitlines()

ranges = [
    ("update_session_stats", "def _update_session_stats(self", "def cleanup_stale_strategies(self"),
    ("defensive_analysis", "def _execute_defensive_analysis(self", "def _execute_defensive_verdicts(self"),
    ("defensive_verdicts", "def _execute_defensive_verdicts(self", "def _rule_based_defensive(self"),
    ("rule_based_defensive", "def _rule_based_defensive(self", "def _execute_live_trade(self"),
]
for name, start_pat, end_pat in ranges:
    start = next(i for i, l in enumerate(lines) if start_pat in l)
    end = next(i for i, l in enumerate(lines) if i > start and end_pat in l)
    chunk = "\n".join(lines[start:end])
    methods = sorted(set(re.findall(r"self\.(_[a-zA-Z0-9_]+)", chunk)))
    print(name, len(methods), methods)
