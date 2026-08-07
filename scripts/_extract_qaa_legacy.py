"""Extract legacy QAA tick + agent handlers from monolith."""
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


def apply_common(text: str) -> str:
    text = re.sub(r"\bself\.", "host.", text)
    for attr in (
        "market_scan_cache", "active_positions_cache", "pre_screen_results",
        "pre_screen_passed", "qaa_last_decision", "qaa_agents_registered",
        "last_unified_snapshot", "risk_assessor",
    ):
        text = text.replace(f"host._{attr}", f"host.{attr}")
    for fn in (
        "get_or_capture_unified_snapshot", "run_with_timeout", "run_v3_factor_pipeline",
        "run_analyst_system", "safe_commit", "clear_master_strat_cache",
    ):
        text = text.replace(f"host._{fn}", f"host.{fn}")
    text = text.replace("host._register_qaa_agents()", "register_qaa_agents(host)")
    text = text.replace(
        "host._build_qaa_snapshot(session_id)",
        "build_qaa_snapshot(session_id, host)",
    )
    text = text.replace("hasattr(host, \"_risk_assessor\")", "host.risk_assessor is not None")
    return text


def apply_handler(text: str, fn_name: str) -> str:
    text = apply_common(text)
    text = text.replace(
        f"host._qaa_compute_signals(payload)",
        f"qaa_compute_signals(payload, host)",
    )
    text = text.replace(
        f"host._qaa_compute_unified(payload)",
        f"qaa_compute_unified(payload, host)",
    )
    return text


register_body, _, _ = extract(
    "def _register_qaa_agents(self", "def _get_qaa_handler(self", "_reg.tmp", "_register_qaa_agents"
)
tick_body, _, _ = extract(
    "def _run_qaa_tick(self", "def _build_qaa_snapshot(self", "_tick.tmp", "_run_qaa_tick"
)
snap_body, _, _ = extract(
    "def _build_qaa_snapshot(self", "# ── QAA Agent Handler", "_snap.tmp", "_build_qaa_snapshot"
)

handlers = [
    ("_qaa_market_data", "def _qaa_risk_control(self"),
    ("_qaa_risk_control", "def _qaa_factor_engine(self"),
    ("_qaa_factor_engine", "def _qaa_compute_signals(self"),
    ("_qaa_compute_signals", "def _qaa_compute_unified(self"),
    ("_qaa_compute_unified", "def _qaa_intel_signal(self"),
    ("_qaa_intel_signal", "def _qaa_mt_orchestrator(self"),
    ("_qaa_mt_orchestrator", "def _qaa_master_controller(self"),
    ("_qaa_master_controller", "def _qaa_trade_execution(self"),
    ("_qaa_trade_execution", "def _qaa_genetic_optimizer(self"),
    ("_qaa_genetic_optimizer", "def _qaa_signal_bus(self"),
    ("_qaa_signal_bus", "# ═══════════════════════════════════════════════════════════════════"),
]

out_dir = ROOT / "backend/services/full_auto"
out_dir.mkdir(parents=True, exist_ok=True)
(out_dir / "_qaa_register_body.tmp").write_text(apply_common(register_body), encoding="utf-8")
(out_dir / "_qaa_tick_body.tmp").write_text(apply_common(tick_body), encoding="utf-8")
(out_dir / "_qaa_snap_body.tmp").write_text(apply_common(snap_body), encoding="utf-8")

for mname, end_pat in handlers:
    body, _, _ = extract(f"def {mname}(self", end_pat, f"{mname}.tmp", mname)
    pub = mname.lstrip("_")
    out_dir.joinpath(f"_qaa_{pub}_body.tmp").write_text(apply_handler(body, pub), encoding="utf-8")

print("extracted legacy QAA bodies")
