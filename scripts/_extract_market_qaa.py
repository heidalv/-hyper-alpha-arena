"""Extract market scan, qaa v3 tick, forced logs."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "backend/services/full_auto_trading_service.py"
lines = path.read_text(encoding="utf-8").splitlines(True)

def extract(start_pat, end_pat, out_name, method_name=None):
    start = next(i for i, l in enumerate(lines) if start_pat in l)
    end = next(i for i, l in enumerate(lines) if i > start and end_pat in l)
    chunk = "".join(lines[start:end])
    mname = method_name or start_pat.replace("def ", "").split("(")[0].strip()
    m = re.search(r'"""[\s\S]*?"""\n(.*)', chunk.split(f"def {mname}", 1)[1], re.DOTALL)
    if not m:
        raise SystemExit(f"no body for {mname}")
    return m.group(1).rstrip() + "\n", start, end

def apply_market(text: str) -> str:
    text = re.sub(r"\bself\.", "host.", text)
    text = text.replace("host._MARKET_SCAN_CACHE_TTL", "host.market_scan_cache_ttl")
    text = text.replace("host._market_scan_cache_ts", "host.market_scan_cache_ts")
    text = text.replace("host._market_scan_cache", "host.market_scan_cache")
    text = text.replace("host._scan_markets(", "run_scan_markets(")
    text = text.replace("host._bg_scan_running", "host.bg_scan_running")
    text = text.replace(
        "run_scan_markets(db, symbols)",
        "run_scan_markets(db, symbols, host)",
    )
    return text

def apply_qaa_tick(text: str) -> str:
    text = re.sub(r"\bself\.", "host.", text)
    text = text.replace("host._active_db_sessions", "host.active_db_sessions")
    text = text.replace("host._active_positions_cache", "host.active_positions_cache")
    text = text.replace("host._market_scan_cache_ts", "host.market_scan_cache_ts")
    text = text.replace("host._market_scan_cache", "host.market_scan_cache")
    text = text.replace("host._pre_screen_results", "host.pre_screen_results")
    text = text.replace("host._pre_screen_passed", "host.pre_screen_passed")
    text = text.replace("host._unified_tick_count", "host.unified_tick_count")
    text = text.replace("host._orch_bg_thread", "host.orch_bg_thread")
    text = text.replace('getattr(host, "_qaa_ctx"', 'getattr(host, "qaa_ctx"')
    text = text.replace('hasattr(host, \'_qaa_last_decision\')', 'hasattr(host, "qaa_last_decision")')
    text = text.replace("host._qaa_last_decision", "host.qaa_last_decision")
    text = text.replace("host._qaa_ctx", "host.qaa_ctx")
    for n in (
        "get_trading_account_id", "bootstrap_market_summary", "get_or_capture_unified_snapshot",
        "sanitize_market_summary_for_qaa", "safe_commit", "run_analyst_system_v3",
    ):
        text = text.replace(f"host._{n}", f"host.{n}")
    text = text.replace("host.bootstrap_qaa_v3_context(", "host.bootstrap_qaa_v3_context(")
    return text

bg_body, _, _ = extract("def _bg_market_scan(self", "def _scan_markets(self", "_bg.tmp", "_bg_market_scan")
scan_body, _, _ = extract("def _scan_markets(self", "def _is_champion_strategy(self", "_scan.tmp", "_scan_markets")
tick_body, tick_start, tick_end = extract("def _run_qaa_v3_tick(self", "def _run_analyst_system_v3(", "_tick.tmp", "_run_qaa_v3_tick")
logs_start = next(i for i, l in enumerate(lines) if "def _write_qaa_v3_forced_decision_logs(" in l)
logs_end = next(i for i, l in enumerate(lines) if i > logs_start and "full_auto_service =" in l)
logs_chunk = "".join(lines[logs_start:logs_end])
m = re.search(r'"""[\s\S]*?"""\n(.*)', logs_chunk.split("def _write_qaa_v3_forced_decision_logs(", 1)[1], re.DOTALL)
logs_body = m.group(1).rstrip() + "\n"

(ROOT / "backend/services/full_auto/_bg_market_scan_body.tmp").write_text(apply_market(bg_body), encoding="utf-8")
(ROOT / "backend/services/full_auto/_scan_markets_body.tmp").write_text(apply_market(scan_body), encoding="utf-8")
(ROOT / "backend/services/full_auto/_qaa_v3_tick_body.tmp").write_text(apply_qaa_tick(tick_body), encoding="utf-8")
(ROOT / "backend/services/full_auto/_qaa_v3_logs_body.tmp").write_text(logs_body, encoding="utf-8")
print("extracted 4 bodies")
