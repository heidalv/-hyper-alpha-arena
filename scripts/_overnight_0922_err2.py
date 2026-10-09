import re, pathlib, collections
lines=pathlib.Path("logs/backend.error.log").read_text(encoding="utf-8",errors="replace").splitlines()
def cluster(prefix):
    c=collections.Counter()
    for ln in lines:
        if ln.startswith(prefix):
            m=re.search(r"\[(WARNING|ERROR|CRITICAL)\]\s+\[tr=[^\]]*\]\s+([^:]+:\d+)\s+-\s+(.{0,70})", ln)
            if m: c[f"{m.group(1)} {m.group(2)} | {m.group(3)}"]+=1
            else: c[ln[:90]]+=1
    return c
for h in ("2026-09-22 01","2026-09-22 04","2026-09-22 07"):
    print(f"\n===== {h} 点 前 8 类 =====")
    for k,v in cluster(h).most_common(8): print(f"  {v:>5}  {k}")
print("\n===== 整夜 ERROR/CRITICAL 计数（18:00 起） =====")
c=collections.Counter()
for ln in lines:
    if not re.match(r"^2026-09-2[12] (1[89]|2[0-3]|0[0-9]):", ln): continue
    if "[ERROR]" in ln or "[CRITICAL]" in ln:
        m=re.search(r"\[(ERROR|CRITICAL)\]\s+\[tr=[^\]]*\]\s+([^:]+:\d+)\s+-\s+(.{0,80})", ln)
        c[f"{m.group(1)} {m.group(2)} | {m.group(3)}" if m else ln[:90]]+=1
for k,v in c.most_common(12): print(f"  {v:>5}  {k}")
print("  ERROR/CRITICAL 总数:", sum(c.values()))
