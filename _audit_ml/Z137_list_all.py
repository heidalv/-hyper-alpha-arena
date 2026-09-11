import re
from pathlib import Path
rep = Path(r"D:\001Alpha\Hyper-Alpha-Arena\_中长线负期望根因报告_20260909.md")
lines = rep.read_text(encoding="utf-8", errors="ignore").splitlines()
ROW = re.compile(r"^\|\s*~{0,2}(\d+)\s*~{0,2}\s*\|\s*(🔴|🟠|🟡|⚪)\s*\|?\s*([^|]*)\|(.*)$")
ROW2 = re.compile(r"^\|\s*~{0,2}(\d+)\s*~{0,2}\s*\|\s*(🔴|🟠|🟡|⚪)\s*([^|]*)\|(.*)$")
head = ""
out = []
for ln in lines:
    if ln.startswith("#"):
        head = ln.strip()[:34]
        continue
    m = ROW2.match(ln)
    if not m:
        continue
    out.append((head, int(m.group(1)), m.group(2), m.group(3).strip(), m.group(4).strip()))
print(f"共 {len(out)} 行\n")
for h, i, sev, mid, rest in out:
    title = mid if len(mid) > 3 else rest.split("|")[0]
    print(f"{sev} #{i:<3} [{h[:22]:<22}] {title[:88]}")
