import re, statistics
from collections import defaultdict
from pathlib import Path
p = Path(r"D:\001Alpha\Hyper-Alpha-Arena\logs\data-center.log")
lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()[-200000:]
done = re.compile(r"^(\d{4}-\d\d-\d\d \d\d):\d\d:\d\d[^\n]*\[P0\] done: (\d+)ok/(\d+)err[^\n]*?symbols=(\d+)")
buckets = defaultdict(lambda: {"n":0,"zero":0,"ok":0,"err":0,"rate":[]})
for l in lines:
    m = done.search(l)
    if not m: continue
    h = m.group(1); ok, err = int(m.group(2)), int(m.group(3))
    b = buckets[h]; b["n"] += 1; b["ok"] += ok; b["err"] += err
    if ok == 0: b["zero"] += 1
    if ok + err: b["rate"].append(ok/(ok+err))
print("=== [P0] done 按小时（data-center.log 尾部 200k 行）===")
print(f"{'hour':16s} {'轮次':>5s} {'0ok':>4s} {'成功率中位':>9s} {'合计成功率':>9s}")
for h in sorted(buckets)[-8:]:
    b = buckets[h]
    med = statistics.median(b["rate"])*100 if b["rate"] else 0
    tot = b["ok"]/max(1,b["ok"]+b["err"])*100
    print(f"{h:16s} {b['n']:5d} {b['zero']:4d} {med:8.1f}% {tot:8.1f}%")
