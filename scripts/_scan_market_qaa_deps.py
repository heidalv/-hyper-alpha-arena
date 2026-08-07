"""Scan deps for market scan and qaa v3 tick."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
lines = (ROOT / "backend/services/full_auto_trading_service.py").read_text(encoding="utf-8").splitlines()
ranges = [
    ("bg_scan", "def _bg_market_scan(self", "def _scan_markets(self"),
    ("scan_markets", "def _scan_markets(self", "def _is_champion_strategy(self"),
    ("qaa_v3_tick", "def _run_qaa_v3_tick(self", "def _run_analyst_system_v3("),
    ("write_logs", "def _write_qaa_v3_forced_decision_logs(", "full_auto_service ="),
]
for name, sp, ep in ranges:
    start = next(i for i, l in enumerate(lines) if sp in l)
    end = next(i for i, l in enumerate(lines) if i > start and ep in l)
    chunk = "\n".join(lines[start:end])
    print(name, end - start, sorted(set(re.findall(r"self\.(_[a-zA-Z0-9_]+)", chunk))))
