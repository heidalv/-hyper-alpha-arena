import sys
from pathlib import Path
ROOT = Path(r"D:\001Alpha\Hyper-Alpha-Arena")
files = sorted([p for p in (ROOT/"logs").glob("*.log") if p.stat().st_mtime > 0], key=lambda p: -p.stat().st_mtime)[:12]
hits = []
for p in files:
    try:
        txt = p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        continue
    for line in txt.splitlines():
        if "[MidLongEvGate]" in line and ("影子放行**" in line or "开始硬拦" in line):
            hits.append((p.name, line.strip()[:190]))
for n, l in hits[-8:]:
    print(f"  [{n}] {l}")
print("  命中数:", len(hits))
