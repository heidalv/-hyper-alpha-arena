import py_compile
from pathlib import Path
files = [
 "backend/services/full_auto/midlong_chart_gate.py",
 "backend/services/full_auto/midlong_position_manager.py",
 "backend/services/full_auto/paper_execution.py",
 "backend/services/full_auto/master_execution.py",
 "backend/services/decision_core/pipeline.py",
 "backend/services/factor_engine/midlong_factor_route.py",
 "backend/services/risk_constitution.py",
 "backend/services/full_auto/proposal_execution.py",
]
for f in files:
    py_compile.compile(f, doraise=True)
print("compile ok:", len(files))
