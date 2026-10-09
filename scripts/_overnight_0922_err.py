import re, pathlib, collections
OUT=[]
def p(*a): OUT.append(" ".join(str(x) for x in a))
f=pathlib.Path("logs/backend.error.log")
lines=f.read_text(encoding="utf-8",errors="replace").splitlines()
p(f"backend.error.log 行数 {len(lines)}，mtime {__import__('datetime').datetime.fromtimestamp(f.stat().st_mtime)}")
pat=re.compile(r"^(2026-\d\d-\d\d \d\d):")
cnt=collections.Counter(); kinds=collections.Counter(); last=None
for ln in lines:
    m=pat.match(ln)
    if m: cnt[m.group(1)]+=1; last=m.group(1)
    s=ln.strip()
    if s.startswith(("Traceback","File \"")):
        kinds[s[:60]]+=1
p("\n按小时计数（全部）：")
for k in sorted(cnt)[-30:]: p(f"  {k}  {cnt[k]}")
p("\n最后一条带时间戳的行的小时:", last)
p("\nTraceback 头 20 种:")
for k,v in kinds.most_common(20): p(f"  {v:>5}  {k}")
p("\n最后 20 行原文:")
for ln in lines[-20:]: p("  "+ln[:200])
pathlib.Path("logs/_tmp_timeline/backend_err.txt").write_text("\n".join(OUT), encoding="utf-8")
print("saved")
