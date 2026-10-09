# -*- coding: utf-8 -*-
"""[R15] 归零是否统计上站得住？IC 的 t = ic × √n 分布。

背景（R14）：权重是 IC 符号的阶梯函数 —— IC<0 一律归零（82 个），IC>0 一律非零。
但 IC 的标准误 ≈ 1/√n：n≈80 时 SE≈0.112 ⇒ **IC=−0.06 的 t 只有 −0.54，属噪声**。

本脚本回答：
  1) 被归零的 82 个因子里，|t| < 1（与 0 无法区分）的有多少？
  2) 非零组里，IC>0 但 |t| < 1 的有多少（对称问题）？
  3) 若按 |t| ≥ T 才允许"打零"，受影响面有多大（会保留多少因子的投票）？

用途：判断"改零权规则"是否有据、改动面多大。只读。
"""
from __future__ import annotations

import io
import json
import math
import statistics as st
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

d = json.load(open("data/factor_runtime_weights.json", encoding="utf-8"))
W, S = d["weights"], d["stats"]

rows = []
for f, s in S.items():
    if not isinstance(s, dict):
        continue
    ic, n = s.get("ic"), int(s.get("n") or 0)
    if ic is None or n <= 0:
        continue
    rows.append({"f": f, "w": float(W.get(f) or 0.0), "n": n, "ic": float(ic),
                 "t": float(ic) * math.sqrt(n)})

z = [r for r in rows if r["w"] == 0.0]
nz = [r for r in rows if r["w"] > 0]
print("可算 t 的因子 %d ｜ 归零 %d ｜ 非零 %d" % (len(rows), len(z), len(nz)))
print("样本数：归零组中位 %.0f ｜ 非零组中位 %.0f" % (st.median([r["n"] for r in z]),
                                                st.median([r["n"] for r in nz])))


def tband(seg, label):
    print("\n  %s（n=%d）" % (label, len(seg)))
    for lo, hi, lab in ((-99, -2, "t<=-2 显著负"), (-2, -1, "-2<t<=-1"), (-1, 0, "-1<t<0（噪声内）"),
                        (0, 1, "0<=t<1（噪声内）"), (1, 2, "1<=t<2"), (2, 99, "t>=2 显著正")):
        s2 = [r for r in seg if lo < r["t"] <= hi or (lo == -99 and r["t"] <= hi) or (hi == 99 and r["t"] > lo)]
        s2 = [r for r in seg if (r["t"] <= hi and r["t"] > lo)]
        if s2:
            print("    %-16s %4d  (%.0f%%)  权重中位 %.3f" % (lab, len(s2), 100 * len(s2) / len(seg),
                                                             st.median([r["w"] for r in s2])))


tband(z, "归零组")
tband(nz, "非零组")

noise = [r for r in z if abs(r["t"]) < 1.0]
print("\n  ⇒ 归零组里 |t|<1（与 0 无法区分）: %d / %d = %.0f%%"
      % (len(noise), len(z), 100 * len(noise) / max(1, len(z))))
sig_neg = [r for r in z if r["t"] <= -2.0]
print("  ⇒ 归零组里 t<=-2（显著负）        : %d / %d = %.0f%%"
      % (len(sig_neg), len(z), 100 * len(sig_neg) / max(1, len(z))))

print("\n  若改成「仅 |t|>=1 才允许打零」：")
print("     会保留投票的因子数 = %d（当前被零掉的噪声项）" % len(noise))
print("     占全体比例 = %.0f%%" % (100 * len(noise) / len(rows)))
if noise:
    print("     这些因子的 ic 中位 = %+.4f，样本中位 = %.0f"
          % (st.median([r["ic"] for r in noise]), st.median([r["n"] for r in noise])))
