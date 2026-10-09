# -*- coding: utf-8 -*-
"""H451 |r300| 下界规则的 OOS 双窗验证（防单窗过拟合）。用 h449 缓存。"""
import json
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

j = json.loads((ROOT / "research_l1" / "out" / "h449_features_cache.json")
               .read_text(encoding="utf-8"))
s = j["s"]
# 缓存无时间戳，按顺序前后对半（时间序）
half = len(s) // 2
h0, h1 = s[:half], s[half:]
COST = 2.3
LIVE = 160.0
base_all = sum(1 for r in s if r[0] >= 0.5) / len(s)


def stat(xs):
    n = len(xs)
    if n < 20:
        return None
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    return m, (m / math.sqrt(var / n) if var > 0 else 0.0), n


print("== |r300| 下界规则：双半窗对照（θ≥0.5）==")
print(f"{'x':>5}{'h0 边际':>10}{'h0 t':>7}{'h1 边际':>10}{'h1 t':>7}{'同号':>6}"
      f"{'净/腿(全)':>11}{'腿速/h':>8}")
rows = []
for x in (0.0, 15.0, 30.0, 40.0, 60.0):
    a = [r[3] for r in h0 if r[0] >= 0.5 and r[2] >= x]
    b = [r[3] for r in h1 if r[0] >= 0.5 and r[2] >= x]
    sa, sb = stat(a), stat(b)
    if not sa or not sb:
        continue
    allx = a + b
    mall = sum(allx) / len(allx)
    p = len(allx) / len(s)
    legs = LIVE * (p / base_all)
    same = "✓" if (sa[0] > 0) == (sb[0] > 0) else "✗"
    rows.append({"x": x, "h0": round(sa[0], 3), "h0_t": round(sa[1], 2),
                 "h1": round(sb[0], 3), "h1_t": round(sb[1], 2), "same": same,
                 "net": round(mall - COST, 3), "legs_h": round(legs, 1)})
    print(f"{x:>5.0f}{sa[0]:>+10.2f}{sa[1]:>+7.1f}{sb[0]:>+10.2f}{sb[1]:>+7.1f}"
          f"{same:>6}{mall-COST:>+11.2f}{legs:>8.1f}")

print("\n== 结论判定 ==")
ok = [r for r in rows if r["same"] == "✓" and r["x"] > 0 and r["net"] > 0
      and r["legs_h"] >= 60]
if ok:
    best = max(ok, key=lambda r: r["net"] * r["legs_h"])
    print(f"  稳健且可行：|r300|≥{best['x']:.0f} ⇒ 双窗同号 ✓、净 {best['net']:+.2f}bp/腿、"
          f"腿速 {best['legs_h']}/h")
else:
    print("  无同时满足【双窗同号 + 净>0 + 腿速≥60】的规则")
out = ROOT / "research_l1" / "out" / "h451_oos_trend_lower.json"
out.write_text(json.dumps({"rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"已存 {out}")
