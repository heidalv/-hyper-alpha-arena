"""H114：收缩后为什么还出现 DOGE/UNI？以及日内强平率的波动幅度。

# 两个待查

1. **收缩后窗口（09:11~09:26, n=40）里出现了 DOGE(4) 与 UNI(1)**
   宇宙已改为 [ASTER,SOL,XRP]，这两个币从哪来？
   假设 a：收缩时手上还握着它们的未平仓位 ⇒ 只能继续管理到平掉（合理，非 bug）
   假设 b：F283 热更新没生效 ⇒ 引擎还在报价这两个币（bug）

2. **日内强平率波动 8.1%~25.0%（H113 分档）**
   若成立，则「−$4/小时」是伪精确，必须给出区间。

# 判据

  · 若 DOGE/UNI 的周期全是**收缩前就开着的旧仓位**（t0 < 收缩时刻）⇒ 假设 a
  · 若出现 t0 > 收缩时刻的 DOGE/UNI 新周期 ⇒ 假设 b，F283 热更新有问题

用法：
    .venv\\Scripts\\python.exe scripts\\h114_post_shrink_probe.py
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from h84_derive_episodes import derive, load  # noqa: E402

CUTOFF_ISO = "2026-09-21T09:11:00"
NEW_SET = ["ASTER", "SOL", "XRP"]


def main():
    rows = load()
    eps = derive(rows)
    print("=" * 100)
    print("H114  收缩后残留币 + 日内波动幅度")
    print("=" * 100)

    # ── 1. 收缩后出现的外来币 ──────────────────────────────────
    post = [e for e in eps if (e.get("t0") or "") >= CUTOFF_ISO]
    aliens = [e for e in post if e["sym"] not in NEW_SET]
    print(f"\n收缩后周期 {len(post)}，其中非宇宙币 **{len(aliens)}**")
    print(f"\n  {'eid':<14} {'币':<8} {'笔':>4} {'flat':>6} {'closed':>7} "
          f"{'时长s':>7} {'峰值名义':>10} {'起点(iso)':<20}")
    print("  " + "-" * 84)
    for e in sorted(aliens, key=lambda x: x["ts0"] or 0):
        print(f"  {e['eid']:<14} {e['sym']:<8} {e['n']:>4} {str(e['flat']):>6} "
              f"{str(e.get('closed')):>7} {e['dur_s']:>7.0f} {e['notional']:>10.2f} "
              f"{str(e['t0'])[:19]:<20}")

    # 这些币的**全部**周期，看最后一个 t0
    print("\n  被剔除币的最后活动时间（判断是旧仓位收尾还是仍在报价）：")
    for s in sorted({e["sym"] for e in eps}):
        if s in NEW_SET:
            continue
        ss = [e for e in eps if e["sym"] == s]
        if not ss:
            continue
        last = max(ss, key=lambda x: x["ts0"] or 0)
        n_after = sum(1 for e in ss if (e.get("t0") or "") >= CUTOFF_ISO)
        print(f"    {s:<10} 周期 {len(ss):>4}  最后一个 t0={str(last['t0'])[:19]}  "
              f"收缩后活动 {n_after}")

    # ── 2. 日内波动幅度 ───────────────────────────────────────
    print("\n" + "=" * 100)
    print("日内强平率波动（决定「每周期期望」能不能报成单点数）")
    print("=" * 100)
    buckets = defaultdict(list)
    for e in eps:
        t = (e.get("t0") or "")
        if len(t) >= 13:
            buckets[t[:13]].append(e)
    ps = []
    print(f"  {'时段':<8} {'周期':>6} {'强平率':>9}  {'95%CI':>16}")
    print("  " + "-" * 44)
    for k in sorted(buckets):
        rs = buckets[k]
        n = len(rs)
        f = sum(1 for e in rs if e["flat"])
        p = f / n
        se = (p * (1 - p) / n) ** 0.5
        lo, hi = max(0.0, p - 1.96 * se), min(1.0, p + 1.96 * se)
        ps.append(p)
        print(f"  {k[11:]:<8} {n:>6} {p*100:>8.1f}%  [{lo*100:>5.1f}%, {hi*100:>5.1f}%]")

    if ps:
        import statistics as st
        print(f"\n  15 分钟档强平率：{len(ps)} 个时段")
        print(f"    最小 {min(ps)*100:.1f}%   最大 {max(ps)*100:.1f}%   "
              f"中位 {st.median(ps)*100:.1f}%")
        print(f"    极差 **{(max(ps)-min(ps))*100:.1f}pp**")
        print(f"\n  ⇒ 每周期期望区间（入场腿按 0 计，强平成本 0.134）：")
        print(f"      最好 {(-0.134*min(ps)):+.5f} ~ 最差 {(-0.134*max(ps)):+.5f} USD")
        # 小时档
        hb = defaultdict(list)
        for e in eps:
            t = (e.get("t0") or "")
            if len(t) >= 13:
                hb[t[:13]].append(e)
    # 用整点小时算
    hb = defaultdict(list)
    for e in eps:
        t = (e.get("t0") or "")
        if len(t) >= 13:
            hb[t[11:13]].append(e)
    print(f"\n  整点小时强平率：")
    hp = []
    for hh in sorted(hb):
        n = len(hb[hh])
        f = sum(1 for e in hb[hh] if e["flat"])
        hp.append((hh, n, f / n))
    for hh, n, p in hp:
        print(f"    {hh}:00  n={n:>4}  {p*100:>5.1f}%")
    pv = [p for _, _, p in hp]
    import statistics as st
    print(f"    小时档极差 **{(max(pv)-min(pv))*100:.1f}pp**  "
          f"（{min(pv)*100:.1f}% ~ {max(pv)*100:.1f}%）")

    # ── 3. 用「同币种子集」重算预期，避免混合比假设 ─────────────
    print("\n" + "=" * 100)
    print("按小时看「3 币子集」的强平率（去掉混合比这个混淆）")
    print("=" * 100)
    hb3 = defaultdict(list)
    for e in eps:
        if e["sym"] not in NEW_SET:
            continue
        t = (e.get("t0") or "")
        if len(t) >= 13:
            hb3[t[11:13]].append(e)
    print(f"  {'小时':<6} {'3币周期':>8} {'3币强平率':>10}")
    print("  " + "-" * 30)
    for hh in sorted(hb3):
        n = len(hb3[hh])
        f = sum(1 for e in hb3[hh] if e["flat"])
        print(f"  {hh}:00  {n:>8} {f/max(n,1)*100:>9.1f}%")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
