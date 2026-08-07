"""Extract light trading, v3 factor, strategy lifecycle from monolith."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "backend/services/full_auto_trading_service.py"
lines = path.read_text(encoding="utf-8").splitlines(True)
FA = ROOT / "backend/services/full_auto"


def extract_body(start_pat: str, end_pat: str, method: str, require_doc=True) -> str:
    start = next(i for i, l in enumerate(lines) if start_pat in l)
    end = next(i for i, l in enumerate(lines) if i > start and end_pat in l)
    chunk = "".join(lines[start:end])
    after = chunk.split(f"def {method}", 1)[1]
    m = re.search(r'"""[\s\S]*?"""\n(.*)', after, re.DOTALL)
    if m:
        return m.group(1).rstrip() + "\n", start, end
    if not require_doc:
        m2 = re.search(r"\)\s*(?:->[^:]*)?:\n(.*)", after, re.DOTALL)
        if m2:
            return m2.group(1).rstrip() + "\n", start, end
    raise SystemExit(f"no body for {method}")


def apply(text: str, attrs=(), fns=()) -> str:
    text = re.sub(r"\bself\.", "host.", text)
    for a in attrs:
        text = text.replace(f"host._{a}", f"host.{a}")
    for f in fns:
        text = text.replace(f"host._{f}", f"host.{f}")
    return text


# --- light trading ---
light, _, _ = extract_body(
    "def _run_light_trading_cycle(self",
    "def _run_quick_orchestrator_eval(self",
    "_run_light_trading_cycle",
)
light = apply(
    light,
    attrs=("active_db_sessions", "last_unified_snapshot"),
    fns=(
        "get_trading_account_id", "active_exchange", "orch_payload_from_decision",
        "run_analyst_system", "safe_commit",
    ),
)
(FA / "_light_trading_body.tmp").write_text(light, encoding="utf-8")

# --- v3 factor ---
v3, _, _ = extract_body(
    "def _run_v3_factor_pipeline(",
    "# ══════════════════════════════════════════════════",
    "_run_v3_factor_pipeline",
)
# end marker may match wrong — verify by checking next method
start = next(i for i, l in enumerate(lines) if "def _run_v3_factor_pipeline(" in l)
end = next(i for i, l in enumerate(lines) if i > start and "def _run_with_timeout(self" in l)
chunk = "".join(lines[start:end])
after = chunk.split("def _run_v3_factor_pipeline", 1)[1]
m = re.search(r'"""[\s\S]*?"""\n(.*)', after, re.DOTALL)
v3 = m.group(1).rstrip() + "\n"
v3 = apply(
    v3,
    attrs=("v3_factor_cache", "V3_FACTOR_CACHE_TTL"),
    fns=(),
)
(FA / "_v3_factor_body.tmp").write_text(v3, encoding="utf-8")

# --- strategy lifecycle: champion → terminate (before REGIME section) ---
# include from _is_champion through _terminate_strategy
start = next(i for i, l in enumerate(lines) if "def _is_champion_strategy(self" in l)
end = next(
    i for i, l in enumerate(lines)
    if i > start and "策略参数自适应" in l and "═══" in lines[i - 1]
)
# also extract adapt separately: from REGIME_PARAM_PROFILES / _get_regime through _adapt end
adapt_start = next(i for i, l in enumerate(lines) if "def _get_regime_profile(self" in l)
adapt_end = next(i for i, l in enumerate(lines) if i > adapt_start and "def _try_create_from_template(" in l)

life_chunk = "".join(lines[start:end])
# extract each method body
methods_life = [
    ("_is_champion_strategy", "def _should_terminate_strategy"),
    ("_should_terminate_strategy", "def _pause_champion_strategy"),
    ("_pause_champion_strategy", "def _snapshot_strategy_genome"),
    ("_snapshot_strategy_genome", "def _terminate_strategy"),
    ("_terminate_strategy", "# ══════════════════════════════════════════════════════════════"),
]
for method, end_pat in methods_life:
    body, _, _ = extract_body(f"def {method}", end_pat, method, require_doc=False)
    body = apply(
        body,
        attrs=("NATURE_TO_TIER_MAP",),
        fns=(),
    )
    (FA / f"_{method}_body.tmp").write_text(body, encoding="utf-8")

# adapt: get_regime + adapt_strategy_params; also need REGIME_PARAM_PROFILES class attr
regime_profiles_start = next(
    i for i, l in enumerate(lines) if "REGIME_PARAM_PROFILES = {" in l
)
# find closing of REGIME_PARAM_PROFILES dict — next def _get_regime
profiles_text = "".join(lines[regime_profiles_start:adapt_start])
(FA / "_regime_profiles.tmp").write_text(profiles_text, encoding="utf-8")

for method, end_pat in [
    ("_get_regime_profile", "def _adapt_strategy_params"),
    ("_adapt_strategy_params", "def _try_create_from_template"),
]:
    body, _, _ = extract_body(f"def {method}", end_pat, method, require_doc=False)
    body = apply(body, attrs=(), fns=("get_regime_profile",))
    # REGIME_PARAM_PROFILES on host
    body = body.replace("host.REGIME_PARAM_PROFILES", "REGIME_PARAM_PROFILES")
    body = body.replace("REGIME_PARAM_PROFILES", "REGIME_PARAM_PROFILES")
    (FA / f"_{method}_body.tmp").write_text(body, encoding="utf-8")

print("extracted light", len(light.splitlines()))
print("extracted v3", len(v3.splitlines()))
print("extracted life+adapt methods")
