"""Extract strategy maintenance methods."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
lines = (ROOT / "backend/services/full_auto_trading_service.py").read_text(encoding="utf-8").splitlines(True)

def extract(start_pat, end_pat, out_name):
    start = next(i for i, l in enumerate(lines) if start_pat in l)
    end = next(i for i, l in enumerate(lines) if i > start and end_pat in l)
    chunk = "".join(lines[start:end])
    name = start_pat.replace("def ", "").split("(")[0].strip()
    m = re.search(r'"""[\s\S]*?"""\n(.*)', chunk.split(f"def {name}", 1)[1], re.DOTALL)
    body = m.group(1).rstrip() + "\n"
    body = re.sub(r"\bself\.", "host.", body)
    for n in ("safe_commit", "get_trading_account_id", "clear_master_strat_cache"):
        body = body.replace(f"host._{n}", f"host.{n}")
    body = body.replace(
        "if hasattr(host, \"_master_strat_cache\"):\n            host._master_strat_cache.clear()",
        "host.clear_master_strat_cache()",
    )
    body = body.replace(
        'if hasattr(self, "_master_strat_cache"):\n            self._master_strat_cache.clear()',
        "host.clear_master_strat_cache()",
    )
    (ROOT / f"backend/services/full_auto/{out_name}").write_text(body, encoding="utf-8")
    print(f"wrote {out_name} ({body.count(chr(10))} lines)")

extract("def cleanup_stale_strategies(self", "def merge_duplicate_strategies(self", "_cleanup_stale_body.tmp")
extract("def merge_duplicate_strategies(self", "def _pause_all_strategies(self", "_merge_dup_body.tmp")
