import json
from pathlib import Path
p = Path(r"D:\001Alpha\Hyper-Alpha-Arena\data\paper_live_divergence.json")
rows = json.loads(p.read_text(encoding="utf-8"))
print(f"共 {len(rows)} 条")
for r in sorted(rows, key=lambda x: (x["kind"], x["file"], x["line"])):
    flag = "  [via_flag]" if r.get("via_flag") else ""
    print(f"  {r['kind']:<11} {r['file'].split('/')[-1]}:{r['line']:<5} if {r['test'][:88]}{flag}")
