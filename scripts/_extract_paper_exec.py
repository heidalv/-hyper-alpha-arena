"""One-off: extract _execute_paper_trade body for paper_execution.py migration."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
src = (ROOT / "backend/services/full_auto_trading_service.py").read_text(encoding="utf-8")
start = src.index("    def _execute_paper_trade(self")
end = src.index("    def _execute_defensive_analysis(self")
chunk = src[start:end]
# inner try body
m = re.search(r"        try:\n(.*)\n        except Exception as e:\n            logger.error", chunk, re.DOTALL)
if not m:
    raise SystemExit("try block not found")
body = m.group(1)
replacements = [
    (r"\bself\.", "host.", "regex"),
    ("host._template_recent_opens", "host.template_recent_opens"),
    ("host._recovery_until", "host.recovery_until"),
    ("host._RECOVERY_POSITION_SCALE", "host.recovery_position_scale"),
    ("host._sub_mgr", "host.sub_mgr"),
    ("host._VALID_TRADE_NATURES", "host.valid_trade_natures"),
    ("host._market_scan_cache", "host.market_scan_cache"),
    ("if not hasattr(self, \"_template_recent_opens\"):\n                            self._template_recent_opens = {}", ""),
    ("if not hasattr(host, \"_template_recent_opens\"):\n                            host.template_recent_opens = {}", ""),
]
for item in replacements:
    if len(item) == 3 and item[2] == "regex":
        body = re.sub(item[0], item[1], body)
    else:
        body = body.replace(item[0], item[1])

out = ROOT / "backend/services/full_auto/_paper_exec_body.tmp"
out.write_text(body, encoding="utf-8")
print(f"wrote {out} ({body.count(chr(10))} lines)")
