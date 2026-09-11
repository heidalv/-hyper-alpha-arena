from pathlib import Path
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
targets = {
 "backend/services/full_auto/midlong_chart_gate.py": [69, 110],
 "backend/services/full_auto/midlong_position_manager.py": [333],
 "backend/services/full_auto/paper_execution.py": [338],
 "backend/services/full_auto/master_execution.py": [1620, 2034, 3339, 3606],
 "backend/services/decision_core/pipeline.py": [49, 186, 259, 317, 343],
 "backend/services/factor_engine/midlong_factor_route.py": [455],
 "backend/services/risk_constitution.py": [117],
 "backend/services/full_auto/proposal_execution.py": [219],
}
for rel, lns in targets.items():
    p = ROOT / rel
    lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    print(f"\n=== {rel} ===")
    for ln in lns:
        for j in (ln - 2, ln - 1, ln):
            if 0 <= j - 1 < len(lines):
                print(f"  {j}: {lines[j-1].rstrip()[:150]}")
        print("  ---")
