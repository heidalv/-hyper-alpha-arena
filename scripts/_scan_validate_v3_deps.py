"""Scan deps for validate_ai_decisions, analyst_v3, strategy cleanup."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
lines = (ROOT / "backend/services/full_auto_trading_service.py").read_text(encoding="utf-8").splitlines()

ranges = [
    ("validate_ai", "def _validate_ai_decisions(self", "def _register_qaa_agents(self"),
    ("analyst_v3", "def _run_analyst_system_v3(", "def _write_qaa_v3_forced_decision_logs("),
    ("cleanup_stale", "def cleanup_stale_strategies(self", "def merge_duplicate_strategies(self"),
    ("merge_dup", "def merge_duplicate_strategies(self", "def _pause_all_strategies(self"),
]
for name, sp, ep in ranges:
    start = next(i for i, l in enumerate(lines) if sp in l)
    end = next(i for i, l in enumerate(lines) if i > start and ep in l)
    chunk = "\n".join(lines[start:end])
    attrs = sorted(set(re.findall(r"self\.(_[A-Z][A-Z0-9_]*)", chunk)))
    methods = sorted(set(re.findall(r"self\.(_[a-z][a-zA-Z0-9_]*)", chunk)))
    print(f"\n{name} lines={end-start} class_attrs={attrs} methods={methods}")
