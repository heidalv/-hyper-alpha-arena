"""H86：强平到底是"风控事件"还是"超时兜底"？—— 决定该修哪一头。

# 触发本脚本的两个实测

**(1) H84 的时长分布**
```
含 flatten 周期：时长中位 **61s**   ← 正好 = max_one_side_seconds(60)
无 flatten 周期：时长中位 15s
```

**(2) H85 的 AUC = 0.398**（低于随机）
用**入场时刻**的特征预测"会不会强平"，AUC 连 0.5 都不到。

**两者合起来指向一个不同的解释**：
若强平由**入场时刻的市场状态**驱动（逆选择/毒性流），
那么它应该①可预测 ②时长分布不应是个尖峰。
实测却是 ①不可预测 ②时长精确堆在 60s ⇒
**强平更像是"被动出库腿没成交 ⇒ 撞超时 ⇒ 兜底市价平"的机械结果**，
而不是"遇到了坏行情"。

# 本脚本检验这个解释（三条独立判据）

  A. **时长分布**：含 flatten 周期的时长是否**高度集中在 60s 附近**
     · 若 60±10s 占比 > 50% ⇒ 是超时兜底
     · 若分散 ⇒ 是行情事件
  B. **强平率是否随"被动出库腿的挂价"变化**
     · 这是 F287 的核心假设：挂得越靠 mid，成交越快，强平越少
     · 用 H84 的 edge_bp 分档看强平率
  C. **强平率是否与"入场时的价差"相关**
     · 价差越宽 ⇒ 被动腿越难成交 ⇒ 强平越多（若是机械机制）
     · 若无关 ⇒ 倾向行情事件解释

判据（事先定死）：
  · A 成立（60±10s 占比 >50%）⇒ **强平是超时兜底** ⇒ 修出库腿（F287 方向）
  · A 不成立且 C 成立 ⇒ 是行情事件 ⇒ 改做事件闸（但 H59 已证闸门无效）

用法：
    .venv\\Scripts\\python.exe scripts\\h86_flatten_mechanism.py
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def main():
    import numpy as np

    from h84_derive_episodes import derive, load

    print("=" * 100)
    print("H86  强平是「风控事件」还是「超时兜底」？")
    print("=" * 100)

    rows = load()
    eps = derive(rows)
    f = [e for e in eps if e["flat"]]
    nf = [e for e in eps if not e["flat"]]
    print(f"  周期 {len(eps)}   含强平 {len(f)}（{len(f)/len(eps)*100:.1f}%）")

    # ── A. 时长分布 ──
    print("\n" + "=" * 100)
    print("A. 含强平周期的时长分布（max_one_side_seconds = 60s）")
    print("=" * 100)
    d = np.array([e["dur_s"] for e in f])
    print(f"\n  时长 s：中位 {np.median(d):.0f}  均值 {d.mean():.0f}  "
          f"p10 {np.percentile(d,10):.0f}  p90 {np.percentile(d,90):.0f}  最大 {d.max():.0f}")
    buckets = [(0, 20), (20, 45), (45, 75), (75, 120), (120, 300), (300, 1e9)]
    print(f"\n  {'区间 s':>14} {'周期数':>8} {'占比':>8}")
    print("  " + "-" * 34)
    for lo, hi in buckets:
        m = (d >= lo) & (d < hi)
        lab = f"[{lo:.0f}, {hi:.0f})" if hi < 1e8 else f"[{lo:.0f}, ∞)"
        print(f"  {lab:>14} {int(m.sum()):>8} {m.mean()*100:>7.1f}%")
    near60 = ((d >= 50) & (d <= 70)).mean()
    print(f"\n  ⇒ 60±10s 占比 **{near60*100:.1f}%**")

    # 对照：无强平周期
    dn = np.array([e["dur_s"] for e in nf])
    print(f"\n  对照 无强平周期时长：中位 {np.median(dn):.0f}  "
          f"60±10s 占比 {((dn>=50)&(dn<=70)).mean()*100:.1f}%")

    # ── B. 强平率 vs 入场 edge_bp ──
    print("\n" + "=" * 100)
    print("B. 强平率 vs 入场报价的 edge_bp（F287 的核心假设）")
    print("=" * 100)
    # edge_bp 在 fill_basis 里；用周期映射回入场记录
    by_ts = defaultdict(list)
    for r in rows:
        by_ts[r.get("symbol")].append(r)
    for s in by_ts:
        by_ts[s].sort(key=lambda r: r.get("ts") or 0)
    pairs = []
    for e in eps:
        cand = [r for r in by_ts.get(e["sym"], []) if r.get("ts") == e.get("ts0")]
        if cand:
            pairs.append((e, cand[0]))
    edges = np.array([float(r.get("edge_bp") or 0.0) for _, r in pairs])
    flats = np.array([1 if e["flat"] else 0 for e, _ in pairs])
    qs = np.percentile(edges, [20, 40, 60, 80])
    print(f"\n  edge_bp 分位: {[round(float(x),4) for x in qs]}")
    print(f"\n  {'edge_bp 区间':>20} {'周期数':>8} {'强平率':>9}")
    print("  " + "-" * 40)
    bins = [(-1e9, qs[0]), (qs[0], qs[1]), (qs[1], qs[2]), (qs[2], qs[3]), (qs[3], 1e9)]
    for lo, hi in bins:
        m = (edges >= lo) & (edges < hi)
        if m.sum() < 5:
            continue
        lab = f"[{lo:.3f}, {hi:.3f})" if lo > -1e8 else f"(-∞, {hi:.3f})"
        print(f"  {lab:>20} {int(m.sum()):>8} {flats[m].mean():>9.4f}")

    # ── C. 强平率 vs 入场价差（seg 宽代理）──
    print("\n" + "=" * 100)
    print("C. 强平率 vs 入场时的段宽（seg_high−seg_low）/mid")
    print("=" * 100)
    segw = []
    for e, r in pairs:
        m0 = float(r.get("engine_mid") or 0)
        lo = float(r.get("seg_low") or 0)
        hi = float(r.get("seg_high") or 0)
        segw.append((hi - lo) / m0 * 1e4 if (m0 > 0 and hi > lo) else 0.0)
    segw = np.array(segw)
    qs2 = np.percentile(segw, [25, 50, 75])
    print(f"\n  段宽分位: {[round(float(x),4) for x in qs2]}")
    print(f"\n  {'段宽区间 bp':>22} {'周期数':>8} {'强平率':>9}")
    print("  " + "-" * 42)
    for lo, hi in [(-1e9, qs2[0]), (qs2[0], qs2[1]), (qs2[1], qs2[2]), (qs2[2], 1e9)]:
        m = (segw >= lo) & (segw < hi)
        if m.sum() < 5:
            continue
        lab = f"[{lo:.4f}, {hi:.4f})" if lo > -1e8 else f"(-∞, {hi:.4f})"
        print(f"  {lab:>22} {int(m.sum()):>8} {flats[m].mean():>9.4f}")

    print("\n" + "=" * 100)
    print("判据")
    print("=" * 100)
    if near60 > 0.50:
        print(f"  A 成立：含强平周期 {near60*100:.1f}% 落在 60±10s")
        print("  ⇒ **强平是「超时兜底」的机械结果**，不是行情事件")
        print("  ⇒ 正确的杠杆是**让被动出库腿更容易成交**（F287 方向），")
        print("     而不是预测强平（H85 已证不可预测，AUC 0.398）")
    else:
        print(f"  A 不成立（60±10s 仅 {near60*100:.1f}%）⇒ 时长分散，倾向行情事件")
    print(f"\n  B/C 供进一步确认：若强平率随 edge_bp 单调（越小越易强平）")
    print(f"     或随段宽单调（越宽越易强平）⇒ 同样支持「机械机制」解释")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
