import re, statistics
from collections import Counter, defaultdict
from pathlib import Path
p = Path(r"D:\001Alpha\Hyper-Alpha-Arena\logs\data-center.log")
lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()[-150000:]
done = re.compile(r"\[P0\] done: (\d+)ok/(\d+)err[^\n]*?symbols=(\d+)[^\n]*?exchange=(\w+)")
ok_ratio = []
zero = 0; total = 0
by_ex = defaultdict(lambda: {"rounds":0,"zero":0,"ok":0,"err":0})
for l in lines:
    m = done.search(l)
    if not m: continue
    ok, err, syms, ex = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)
    total += 1
    by_ex[ex]["rounds"] += 1; by_ex[ex]["ok"] += ok; by_ex[ex]["err"] += err
    if ok == 0: zero += 1; by_ex[ex]["zero"] += 1
    if ok + err: ok_ratio.append(ok/(ok+err))
print(f"=== [P0] done 轮次统计（data-center.log 尾部 {len(lines)} 行）===")
print(f"  总轮次={total}  其中 0ok（整轮全灭）={zero}  ({zero/max(1,total)*100:.1f}%)")
if ok_ratio:
    print(f"  单轮成功率：中位={statistics.median(ok_ratio)*100:.1f}%  均值={statistics.mean(ok_ratio)*100:.1f}%  最低={min(ok_ratio)*100:.0f}%  最高={max(ok_ratio)*100:.0f}%")
print(f"  {'exchange':12s} {'轮次':>5s} {'0ok轮':>6s} {'成功率':>7s}  ok/err 合计")
for ex, d in sorted(by_ex.items(), key=lambda kv: -kv[1]["rounds"]):
    rate = d["ok"]/max(1, d["ok"]+d["err"])*100
    print(f"  {ex:12s} {d['rounds']:5d} {d['zero']:6d} {rate:6.1f}%  {d['ok']}/{d['err']}")
# 429 真伪：只认非时间戳位置的 429
ts429 = sum(1 for l in lines if re.search(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,429", l))
real429 = sum(1 for l in lines if re.search(r"(HTTP|status|code|限流|rate)[^\n]{0,20}429|429[^\n]{0,10}(Too Many|限流)", l))
print(f"\n=== '429' 计数真伪 ===\n  时间戳毫秒位正好是 429 的行 = {ts429}（假命中）\n  真正的限流 429 行 = {real429}")
