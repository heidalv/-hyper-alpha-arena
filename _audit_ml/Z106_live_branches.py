import re
from pathlib import Path
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
MODS = [
 "backend/services/full_auto/midlong_helpers.py",
 "backend/services/full_auto/midlong_executor.py",
 "backend/services/full_auto/midlong_position_manager.py",
 "backend/services/full_auto/proposal_execution.py",
 "backend/services/full_auto/paper_execution.py",
 "backend/services/full_auto/master_execution.py",
 "backend/services/mlto/brain.py",
 "backend/services/mlto/decision_hub.py",
 "backend/services/mlto/mlto_cycle.py" ,
 "backend/services/full_auto/mlto_cycle.py",
 "backend/services/paper_trading_engine.py",
 "backend/services/unified_exit_executor.py",
 "backend/services/mlto/midlong_portfolio_risk.py",
 "backend/services/factor_engine/midlong_factor_route.py",
]
PAT = re.compile(r'trading_mode|is_live|==\s*[\'"]live[\'"]|!=\s*[\'"]paper[\'"]|session_mode|lane_mode')
for m in MODS:
    p = ROOT / m
    if not p.exists():
        print(f"  (缺失) {m}")
        continue
    hits = []
    for i, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
        if PAT.search(line) and not line.strip().startswith("#"):
            hits.append((i, line.strip()[:150]))
    print(f"\n=== {m}  命中 {len(hits)} ===")
    for i, l in hits[:14]:
        print(f"   {i}: {l}")
