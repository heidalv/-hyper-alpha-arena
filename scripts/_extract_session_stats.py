"""Extract _update_session_stats body from monolith."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
lines = (ROOT / "backend/services/full_auto_trading_service.py").read_text(encoding="utf-8").splitlines(True)
start = next(i for i, l in enumerate(lines) if "def _update_session_stats(self" in l)
end = next(i for i, l in enumerate(lines) if i > start and "def cleanup_stale_strategies(self" in l)
chunk = "".join(lines[start:end])
m = re.search(r'"""[\s\S]*?"""\n(.*)', chunk.split("def _update_session_stats(self", 1)[1], re.DOTALL)
if not m:
    raise SystemExit("body not found")
body = m.group(1).rstrip() + "\n"
body = re.sub(r"\bself\.", "host.", body)
body = body.replace("host._get_trading_account_id", "host.get_trading_account_id")
(ROOT / "backend/services/full_auto/_session_stats_body.tmp").write_text(body, encoding="utf-8")
print(f"wrote body ({body.count(chr(10))} lines)")
