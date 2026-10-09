# -*- coding: utf-8 -*-
"""[R14] IC 权重归因：被归零的因子是"样本太少被冤枉"还是"IC 确实为负"？

数据源：`data/factor_runtime_weights.json`
  - `weights`: 运行时权重（226 个，中位 0.10、36% 归零）
  - `stats`   : 逐因子统计（216 个，含 n / win_rate / ic / weight / pred_ic / dyn_adj）

判定含义：
  - 若归零因子集中在**小样本档** ⇒ 用不足的样本把因子打零 = 评估口径问题（对应 R13 的 C 方案）；
  - 若归零因子在**各样本档**都多、且 IC 中位为负 ⇒ 降权是合理的（对应 A 方案）。
只读。
"""
from __future__ import annotations

import io
import json
import statistics as st
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

d = json.load(open("data/factor_runtime_weights.json", encoding="utf-8"))
W = d["weights"]
S = d["stats"]

rows = []
for f, s in S.items():
    if not isinstance(s, dict):
        continue
    w = W.get(f)
    rows.append((f, float("nan") if w is None else float(w), int(s.get("n") or 0),
                 s.get("ic"), s.get("win_rate"), s.get("pred_ic")))

print("可关联 stats 的因子 %d ｜ 只在 weights 里的 %d ｜ updated_at=%s"
      % (len(rows), len(W) - len(rows), d.get("updated_at")))
z = [r for r in rows if r[1] == 0.0]
nz = [r for r in rows if r[1] > 0]
print("归零 n=%d ｜ 非零 n=%d" % (len(z), len(nz)))


def seg_stats(seg, label):
    if not seg:
        print("  %-8s 无样本" % label)
        return
    ns = [r[2] for r in seg]
    ics = [r[3] for r in seg if r[3] is not None]
    wr = [r[4] for r in seg if r[4] is not None]
    print("  %-8s n=%-4d 样本数中位=%-7.0f IC中位=%+8.4f 胜率中位=%.3f"
          % (label, len(seg), st.median(ns),
             st.median(ics) if ics else float("nan"),
             st.median(wr) if wr else float("nan")))


print("")
seg_stats(z, "归零")
seg_stats(nz, "非零")

print("")
print("  按样本数分档（看是否「样本少就被归零」）：")
print("  %-12s %5s %7s %8s %9s %10s" % ("样本数档", "n", "归零数", "归零占比", "权重中位", "IC中位"))
for lo, hi, lab in ((0, 30, "n<30"), (30, 100, "30-100"), (100, 300, "100-300"), (300, 10 ** 9, ">=300")):
    seg = [r for r in rows if lo <= r[2] < hi]
    if not seg:
        continue
    zc = sum(1 for r in seg if r[1] == 0.0)
    ics = [r[3] for r in seg if r[3] is not None]
    print("  %-12s %5d %7d %7.0f%% %9.3f %+10.4f"
          % (lab, len(seg), zc, 100 * zc / len(seg), st.median([r[1] for r in seg]),
             st.median(ics) if ics else float("nan")))

print("")
print("  按 IC 分档（看权重是否跟着 IC 走）：")
print("  %-14s %5s %9s %10s" % ("IC 档", "n", "权重中位", "归零占比"))
for lo, hi, lab in ((-1, -0.05, "IC<-0.05"), (-0.05, 0, "-0.05~0"), (0, 0.05, "0~0.05"),
                    (0.05, 0.15, "0.05~0.15"), (0.15, 1, ">=0.15")):
    seg = [r for r in rows if r[3] is not None and lo <= r[3] < hi]
    if not seg:
        continue
    zc = sum(1 for r in seg if r[1] == 0.0)
    print("  %-14s %5d %9.3f %9.0f%%"
          % (lab, len(seg), st.median([r[1] for r in seg]), 100 * zc / len(seg)))

# 小样本且 IC 尚可、却被归零的"疑似冤枉"清单
susp = [r for r in rows if r[1] == 0.0 and r[2] < 100 and (r[3] is None or r[3] > 0)]
print("")
print("  ⇒ 疑似「样本不足却被归零」（n<100 且 IC>0）: %d 个" % len(susp))
for f, w, n, ic, wr, pi in sorted(susp, key=lambda x: x[2])[:10]:
    print("     %-34s n=%-5d ic=%s win=%s" % (f[:34], n, ("%+.4f" % ic) if ic is not None else "n/a", wr))
