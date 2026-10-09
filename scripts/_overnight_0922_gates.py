import re, ast, pathlib
from datetime import datetime, timedelta, timezone
CST = timezone(timedelta(hours=8))
OUT=[]
def p(*a): OUT.append(" ".join(str(x) for x in a))
rows = []
for line in pathlib.Path("logs/mm_lane_worker.log").read_text(encoding="utf-8", errors="replace").splitlines():
    m = re.match(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \[mm-worker\] n=(\d+) ok=(\w+) reason=(\S*) ticks=(\d+) fills=(\d+) last_tick=([\d.]+) skip=(\{.*\})", line)
    if not m: continue
    ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=CST)
    rows.append((ts, int(m.group(2)), m.group(3), m.group(4), int(m.group(5)), int(m.group(6)), float(m.group(7)), ast.literal_eval(m.group(8))))
p(f"解析行数 {len(rows)}  {rows[0][0]:%m-%d %H:%M} → {rows[-1][0]:%m-%d %H:%M}")
w0 = datetime(2026,9,21,18,0,tzinfo=CST)
inw = [r for r in rows if r[0] >= w0]
p(f"窗口内采样 {len(inw)} 条；n 回退(重启) 次数 {sum(1 for i,r in enumerate(inw) if i and r[1] < inw[i-1][1])}")
keys = sorted({k for r in inw for k in r[7]})
p(f"\n  {'时间':<9}{'Δtick':>7}{'Δfill':>7}{'fill/tick':>10}" + "".join(f"{k[:12]:>14}" for k in keys))
tot = {k:0 for k in keys}; tot_t=tot_f=0; prev=None
for r in inw:
    if prev is None or r[1] < prev[1]:
        prev = r; continue
    dt = r[4]-prev[4]; df = r[5]-prev[5]
    d = {k: r[7].get(k,0)-prev[7].get(k,0) for k in keys}
    for k in keys: tot[k]+=d[k]
    tot_t+=dt; tot_f+=df
    p(f"  {r[0]:%H:%M:%S}{dt:>7}{df:>7}{(df/dt if dt else 0):>10.2f}" + "".join(f"{d[k]:>14}" for k in keys))
    prev = r
p(f"  {'合计':<9}{tot_t:>7}{tot_f:>7}{(tot_f/tot_t if tot_t else 0):>10.2f}" + "".join(f"{tot[k]:>14}" for k in keys))
s = sum(tot.values())
p("\n闸门拦截占比（窗口内增量）：")
for k,v in sorted(tot.items(), key=lambda x:-x[1]):
    p(f"  {k:<22}{v:>8}  {100*v/s:>6.1f}%")
pathlib.Path("logs/_tmp_timeline/gates.txt").write_text("\n".join(OUT), encoding="utf-8")
print("saved", len(OUT))
