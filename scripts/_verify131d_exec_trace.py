"""轮131 副作用定位（二）：AAVE rec_open=True 却没成交 —— 执行侧到底卡在哪。"""
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

LO = "2026-09-20 11:44"
HI = "2026-09-20 11:56"
PAT = re.compile(r"AAVE|risk_officer|RiskOfficer|SizeFloor|size_below_floor|PROBE-CLAMP|"
                 r"midlong_portfolio|portfolio_budget_block|probe_entry|sweep")

for f in ("logs/backend.log",):
    fp = Path(f)
    if not fp.exists():
        continue
    lines = fp.read_text(encoding="utf-8", errors="replace").splitlines()
    hits = []
    for ln in lines:
        m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", ln)
        if not m:
            continue
        ts = m.group(1)
        if LO <= ts <= HI and PAT.search(ln):
            hits.append(ln)
    print(f"[{fp.name}] {LO}~{HI} 命中 {len(hits)} 行（只显示含 AAVE / 风控 / 尺寸地板 的）")
    for ln in hits[-30:]:
        body = ln.split(" - ", 1)[-1]
        print("  ", ln[:19], body[:150])
