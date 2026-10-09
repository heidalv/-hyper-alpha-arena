# -*- coding: utf-8 -*-
"""H450 方向性验证：趋势闸该是"上界"还是"下界"？
   A) 只做 |r300| ≥ x 的状态（下界规则）——频率、毛边际、净、总/h
   B) 只做 σ ≥ x（下界）
   C) 联合：θ≥0.5 且 |r300|≥x
用 h449 的特征缓存，不重新取数。
"""
import json
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CACHE = ROOT / "research_l1" / "out" / "h449_features_cache.json"
CACHE2 = ROOT / "research_l1" / "out" / "h450_samples_cache.json"

if CACHE.exists():
    j = json.loads(CACHE.read_text(encoding="utf-8"))
    s = j["s"]
    print(f"用 h449 缓存：{len(s)} 样本")
else:
    raise SystemExit("需要 h449 缓存（先跑 h449_regime_quota.py）")

LIVE_LEGS = 160.0
COST = 2.3
base_p = sum(1 for r in s if r[0] >= 0.5) / len(s)


def st(xs):
    n = len(xs)
    if n < 20:
        return None
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    return {"n": n, "mean": m, "t": m / math.sqrt(var / n) if var > 0 else 0.0}


print(f"\n样本量 {len(s)}；当前基准 θ=0.5 通过率 {base_p:.3f}（腿速基准 {LIVE_LEGS}/h）")

print("\n== A) 下界规则：只做 |r300| ≥ x（流确认 θ≥0.5）==")
print(f"{'x':>5}{'通过率':>9}{'腿速/h':>9}{'毛边际':>10}{'t':>7}{'净/腿':>9}{'总/h':>9}")
rowsA = []
for x in (0.0, 5.0, 10.0, 15.0, 20.0, 30.0, 40.0):
    xs = [r[3] for r in s if r[0] >= 0.5 and r[2] >= x]
    d = st(xs)
    if not d:
        continue
    p = d["n"] / len(s)
    legs = LIVE_LEGS * (p / base_p)
    net = d["mean"] - COST
    rowsA.append({"x": x, "pass": round(p, 4), "legs_h": round(legs, 1),
                  "edge": round(d["mean"], 3), "t": round(d["t"], 2),
                  "net_bp": round(net, 3), "total": round(net * legs, 2)})
    flag = " ←破60" if legs < 60 else ""
    print(f"{x:>5.0f}{p:>9.3f}{legs:>9.1f}{d['mean']:>+10.2f}{d['t']:>+7.1f}"
          f"{net:>+9.2f}{net*legs:>+9.1f}{flag}")

print("\n== B) 下界规则：只做 σ ≥ x（流确认 θ≥0.5）==")
print(f"{'x':>5}{'通过率':>9}{'腿速/h':>9}{'毛边际':>10}{'t':>7}{'净/腿':>9}{'总/h':>9}")
rowsB = []
for x in (-0.5, 0.0, 0.5, 1.0, 2.0, 3.0):
    xs = [r[3] for r in s if r[0] >= 0.5 and r[1] >= x]
    d = st(xs)
    if not d:
        continue
    p = d["n"] / len(s)
    legs = LIVE_LEGS * (p / base_p)
    net = d["mean"] - COST
    rowsB.append({"x": x, "pass": round(p, 4), "legs_h": round(legs, 1),
                  "edge": round(d["mean"], 3), "t": round(d["t"], 2),
                  "net_bp": round(net, 3), "total": round(net * legs, 2)})
    flag = " ←破60" if legs < 60 else ""
    print(f"{x:>5.1f}{p:>9.3f}{legs:>9.1f}{d['mean']:>+10.2f}{d['t']:>+7.1f}"
          f"{net:>+9.2f}{net*legs:>+9.1f}{flag}")

print("\n== C) 对照：上界规则（现行 trend_pause 方向）==")
for x in (15.0, 20.0, 30.0):
    xs = [r[3] for r in s if r[0] >= 0.5 and r[2] < x]
    d = st(xs)
    if not d:
        continue
    p = d["n"] / len(s)
    legs = LIVE_LEGS * (p / base_p)
    net = d["mean"] - COST
    print(f"  |r300|<{x:<5.0f} 通过率={p:.3f} 腿速={legs:>5.1f}/h 毛={d['mean']:+.2f}bp "
          f"t={d['t']:+.1f} 净={net:+.2f} 总/h={net*legs:+.1f}")

feasA = [r for r in rowsA if r["legs_h"] >= 60]
bestA = max(feasA, key=lambda r: r["total"]) if feasA else None
print(f"\n下界规则最优（≥60/h）：|r300|≥{bestA['x']} ⇒ 净 {bestA['net_bp']:+.2f}bp/腿、"
      f"腿速 {bestA['legs_h']}/h、总 {bestA['total']:+.1f}" if bestA else "无可行解")

CACHE2.write_text(json.dumps({"rowsA": rowsA, "rowsB": rowsB, "bestA": bestA},
                             ensure_ascii=False, indent=2), encoding="utf-8")
print(f"已存 {CACHE2}")
