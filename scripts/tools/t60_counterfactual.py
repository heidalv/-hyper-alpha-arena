"""T60 counterfactual: what do positions held >150s actually realise?

T60 forces a taker exit once a maker exit has been pending past the threshold.
Its realised cost is a flat 4bp fee.  The benefit only exists if the
counterfactual -- holding longer -- is worse.

This measures the realised outcome of roundtrips by holding time, using the
roundtrip log (which stores hold_sec + y_bp), so the verdict does not depend
on waiting for more T60 firings.
"""
from __future__ import annotations

import io
import json
import math
import statistics as st
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = r"D:\001Alpha\Hyper-Alpha-Arena"

rows = []
for ln in open(ROOT + r"\data\flow_roundtrip_log.jsonl",
               encoding="utf-8", errors="replace"):
    ln = ln.strip()
    if not ln:
        continue
    try:
        d = json.loads(ln)
    except Exception:  # noqa: BLE001
        continue
    y, h = d.get("y_bp"), d.get("hold_sec")
    if y is None or h is None:
        continue
    try:
        rows.append((float(h), float(y)))
    except (TypeError, ValueError):
        continue

print("=" * 94)
print("T60 反事实：按持仓时长看最终实现（往返日志）")
print("=" * 94)
print(f"  往返样本总数 {len(rows)}")
print()
print(f"  {'持仓档':<22}{'n':>5}{'均 y_bp':>10}{'t':>8}{'最差':>10}{'胜率':>7}")
for label, lo, hi in (("<15s", 0, 15), ("15-60s", 15, 60),
                      ("60-150s", 60, 150), (">150s（T60 目标）", 150, 10 ** 9)):
    g = [r[1] for r in rows if lo <= r[0] < hi]
    if len(g) < 3:
        print(f"  {label:<22}{len(g):>5}  (太少)")
        continue
    mu, sd = st.mean(g), st.pstdev(g)
    se = sd / math.sqrt(len(g)) if len(g) else 0
    print(f"  {label:<22}{len(g):>5}{mu:>10.2f}{(mu/se if se else 0):>8.2f}"
          f"{min(g):>10.1f}{sum(1 for x in g if x > 0)/len(g)*100:>6.0f}%")

gt = [r[1] for r in rows if r[0] >= 150]
print()
if len(gt) >= 5:
    mu = st.mean(gt)
    deep = [x for x in gt if x < -60]
    print(f"  >150s 组：n={len(gt)}  均 {mu:+.2f}bp")
    print(f"  T60 把该组替换为实测 **-3.46bp**（4 笔）")
    print(f"  ⇒ 理论改善 **{mu - (-3.46):+.2f}bp/笔**")
    print()
    print(f"  该组里 <-60bp 的深亏腿 {len(deep)}/{len(gt)} = "
          f"{len(deep)/max(len(gt),1)*100:.1f}%")
    if deep:
        print(f"  深亏部分合计 {sum(deep):+.1f}bp，"
              f"占该组全部 {sum(gt):+.1f}bp 的 "
              f"{abs(sum(deep))/max(abs(sum(gt)),1e-9)*100:.0f}%")
    print()
    print("  注意：这是**历史分布**（含失控规模时代），不是同规模对照；")
    print("        只能用来判断'T60 的阈值是否落在坏区间'，不能当收益承诺。")
else:
    print(f"  >150s 样本不足（n={len(gt)}），无法给出反事实")
