"""H113：宇宙收缩的**直接验证** —— 收缩后实测强平率 vs 预测 7.9%。

# 为什么要单独测

H111 用「按周期加权的币种混合」预测收缩后强平率 **15.9% → 7.9%**，
但那个预测里混了两个假设：
  1. 每个币的强平率稳定（用小样本估出来的）
  2. 收缩后各币的实际周期占比与历史相同

**只要有一个不成立，7.9% 就是假的。** 本脚本做不带假设的直接对照：

  对照 A（无假设）：**同样这 3 个币**，收缩前 vs 收缩后 的强平率
      —— 币种集合完全相同 ⇒ 差异只能来自时间/行情，不能来自混合比

  对照 B（无假设）：**全宇宙**（收缩前）vs **3 币**（收缩后）
      —— 这才是「收缩」这个动作的总效果

# 判据（事先定死）

  · 对照 A 若收缩后强平率 **下降超过 1 个标准误** ⇒ 收缩在币内也有效（不只是混合效应）
  · 对照 A 若**没有显著下降** ⇒ 说明 7.9% 的预测主要靠「混合比」这一条腿，
    而混合比是不受控的（哪个币先成交由行情决定）⇒ **预测不可靠，需重算**
  · 对照 B 直接给出总效果，与 H111 的 −$4.06/h 预测比较

用法：
    .venv\\Scripts\\python.exe scripts\\h113_shrink_measure.py
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from h84_derive_episodes import derive, load  # noqa: E402

# 收缩执行时刻（本机时区）。取 mm_set_symbols 落地时间。
CUTOFF_ISO = "2026-09-21T09:11:00"
NEW_SET = ["ASTER", "SOL", "XRP"]


def rate(sub):
    if not sub:
        return None
    n = len(sub)
    f = sum(1 for e in sub if e["flat"])
    p = f / n
    se = (p * (1 - p) / n) ** 0.5
    return {"n": n, "flat": f, "p": p, "se": se}


def fmt(r, label):
    if r is None:
        return f"  {label:<34} (无样本)"
    return (f"  {label:<34} n={r['n']:>5}  强平 {r['flat']:>4}  "
            f"**{r['p']*100:>5.1f}%**  ±{r['se']*100:.1f}pp")


def net_per_cycle(sub):
    """用实测：每周期净额（不能用未观测的入场腿，故用 total 需外部注入）。

    这里只返回周期数与强平次数，net 由调用方结合 FLATTEN_COST 计算。
    """
    return len(sub), sum(1 for e in sub if e["flat"])


# 每周期强平成本（H105 实测，三段几乎恒定）
FLATTEN_COST = 0.134


def main():
    rows = load()
    eps = derive(rows)
    print("=" * 100)
    print("H113  宇宙收缩的直接验证")
    print("=" * 100)
    print(f"  fill_basis 行数 {len(rows):,}   推导周期数 {len(eps)}")
    print(f"  收缩时刻（本机） {CUTOFF_ISO}   新宇宙 {NEW_SET}")

    pre = [e for e in eps if (e.get("t0") or "") < CUTOFF_ISO]
    post = [e for e in eps if (e.get("t0") or "") >= CUTOFF_ISO]

    # ── 样本量把关 ──────────────────────────────────────────────
    print("\n" + "=" * 100)
    print("样本量")
    print("=" * 100)
    print(f"  收缩前周期 {len(pre)}   收缩后周期 **{len(post)}**")
    if post:
        t0 = min(e["t0"] for e in post)
        t1 = max(e["t0"] for e in post)
        print(f"  收缩后窗口 {t0} ~ {t1}")
        syms = sorted({e["sym"] for e in post})
        print(f"  收缩后实际出现的币 {syms}   （未出现的：{[s for s in NEW_SET if s not in syms]}）")
    if len(post) < 30:
        print(f"\n  ⚠️ 收缩后只有 {len(post)} 个周期 —— **样本不足，本脚本不下结论**。")
        print("     仅打印当前数值作为进度，等 ≥30 周期后重跑。")

    # ── 对照 A：同样 3 个币，收缩前 vs 后 ────────────────────────
    print("\n" + "=" * 100)
    print("对照 A  同币种集合（排除「混合比」这个假设）")
    print("=" * 100)
    pre3 = [e for e in pre if e["sym"] in NEW_SET]
    post3 = [e for e in post if e["sym"] in NEW_SET]
    r_pre3 = rate(pre3)
    r_post3 = rate(post3)
    print(fmt(r_pre3, "收缩前 · 仅 ASTER/SOL/XRP"))
    print(fmt(r_post3, "收缩后 · 仅 ASTER/SOL/XRP"))
    if r_pre3 and r_post3:
        d = r_post3["p"] - r_pre3["p"]
        se = (r_pre3["se"] ** 2 + r_post3["se"] ** 2) ** 0.5
        print(f"\n  差 {d*100:+.1f}pp   合并标准误 {se*100:.1f}pp   "
              f"z={d/se if se else 0:+.2f}")
        if abs(d) > se and d < 0:
            print("  ⇒ 收缩后**显著下降** ⇒ 币内也有效，不只是混合效应 ✓")
        elif abs(d) <= se:
            print("  ⇒ **在噪声内，无法判定**。7.9% 的预测尚未被证实。")
        else:
            print("  ⇒ 收缩后**反而上升** ⇒ 预测方向错误，需重算 ⚠️")

    # ── 对照 B：总效果 ─────────────────────────────────────────
    print("\n" + "=" * 100)
    print("对照 B  收缩这个动作的总效果（全宇宙 vs 3 币）")
    print("=" * 100)
    r_pre_all = rate(pre)
    r_post_all = rate(post)
    print(fmt(r_pre_all, "收缩前 · 全宇宙"))
    print(fmt(r_post_all, "收缩后 · 实际报价集合"))
    for lab, r in (("收缩前", r_pre_all), ("收缩后", r_post_all)):
        if r:
            exp = -FLATTEN_COST * r["p"]
            print(f"      {lab}：每周期期望（入场腿按 0 计） = "
                  f"-{FLATTEN_COST:.3f} x {r['p']*100:.1f}% = **{exp:+.5f} USD**")

    # ── 分币明细 ───────────────────────────────────────────────
    print("\n" + "=" * 100)
    print("分币明细（收缩前 / 收缩后）")
    print("=" * 100)
    print(f"  {'币':<10} {'前n':>6} {'前强平率':>10} {'后n':>6} {'后强平率':>10}  {'预测?':<10}")
    print("  " + "-" * 62)
    allsym = sorted({e["sym"] for e in eps})
    for s in allsym:
        a = rate([e for e in pre if e["sym"] == s])
        b = rate([e for e in post if e["sym"] == s])
        pa = f"{a['p']*100:.1f}%" if a else "—"
        pb = f"{b['p']*100:.1f}%" if b else "—"
        na = a["n"] if a else 0
        nb = b["n"] if b else 0
        tag = "在宇宙内" if s in NEW_SET else "已剔除"
        print(f"  {s:<10} {na:>6} {pa:>10} {nb:>6} {pb:>10}  {tag:<10}")

    # ── 时间推进下的强平率（消除「之后行情变好」的混淆）──────────
    print("\n" + "=" * 100)
    print("时间分档强平率（检查「收缩后变好」是否只是行情变好）")
    print("=" * 100)
    buckets = defaultdict(list)
    for e in eps:
        t = (e.get("t0") or "")
        if not t:
            continue
        hh = t[11:13]
        buckets[hh].append(e)
    print(f"  {'小时':<6} {'周期':>6} {'强平率':>9}  {'在宇宙内占比':>14}")
    print("  " + "-" * 44)
    for hh in sorted(buckets):
        rs = buckets[hh]
        n3 = sum(1 for e in rs if e["sym"] in NEW_SET)
        r = rate(rs)
        print(f"  {hh}:00  {r['n']:>6} {r['p']*100:>8.1f}%  {n3/max(r['n'],1)*100:>13.0f}%")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
