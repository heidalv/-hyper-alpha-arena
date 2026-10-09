import re, pathlib, collections
lines=pathlib.Path("logs/backend.error.log").read_text(encoding="utf-8",errors="replace").splitlines()
night=[ln for ln in lines if re.match(r"^2026-09-2[12] (1[89]|2[0-3]|0[0-9]):", ln)]
print("夜间行数:", len(night))
mm=[ln for ln in night if "market_maker" in ln or "mm_" in ln or "LaneLedger" in ln or "mm-worker" in ln]
print("其中涉及 market_maker/mm_ 的行数:", len(mm))
for ln in mm[:15]: print("  ", ln[:180])
c=collections.Counter()
for ln in night:
    m=re.search(r"-\s+\[([A-Za-z_]+)\]", ln) or re.search(r"backend\.services\.([a-z_]+)", ln)
    if m: c[m.group(1)]+=1
print("\n夜间日志按模块 top12:", c.most_common(12))
