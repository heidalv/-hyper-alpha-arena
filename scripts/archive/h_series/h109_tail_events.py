"""H109：整夜亏损的真正来源 —— **尾部事件**，不是币种。

# H108 的结构性检验（决定性）

```
币        中位USD      尾部占比   强平率      周期
ASTER    +0.00967     233.8%    6.6%      577
SOL      +0.00679     148.3%    6.6%      423
ARB      +0.00559      75.4%   53.6%      140
UNI      +0.00499     112.7%   48.5%       66
DOGE     +0.00220     119.0%   22.2%      261
XRP      +0.00188     140.6%   10.2%      571
ONDO     -0.02693      40.4%   61.2%       49   ← 唯一结构性
PENDLE   -0.02718      40.7%   91.7%       12   ← 唯一结构性
```

**⇒ 8 个币里 6 个的中位为正、平均为负；尾部占比 >100%**
**⇒ 剔除最亏 5% 的周期后，这些币**都是赚的**。**
**⇒ 亏损是**尾部事件**，不是币种劣势。**
**⇒ 砍币（H107 的建议）基本无效 —— 我的判据脚本先警告过，检验证实了。**

**强平率 vs 每周期净额：corr = −0.584（n=8，噪声大）**
⇒ 强平率是**重要但非唯一**的解释变量。

# 本脚本要回答的核心问题

**尾部事件（那些最亏的周期）到底是什么？**

候选机制：
  A. **强平腿的极端滑点**（薄币上市价砸出去，价格跳走）
  B. **持仓期间的价格大幅逆行**（持有 120s，遇到单边行情）
  C. **单笔规模过大**（$135 腿在某些币上占盘口比例过高）
  D. **连续同向成交**（库存累积到很大才平）

对每个"最亏 5%"周期，取其特征并与正常周期对比：
  · 峰值名义（规模）
  · 时长（暴露时间）
  · 笔数（累积程度）
  · 币种
  · 是 fill 还是 flatten 主导

判据（事先定死）：
  · 若尾部周期的**峰值名义显著更大** ⇒ 规模问题 ⇒ 限制单币名义
  · 若尾部周期的**时长显著更长** ⇒ 暴露时间问题 ⇒ 缩短持有或加时间止损
  · 若尾部周期**集中在少数币** ⇒ 币种流动性问题
  · 若都不是 ⇒ 是行情跳变 ⇒ 只能靠止损/尾部保险

用法：
    .venv\\Scripts\\python.exe scripts\\h109_tail_events.py
"""
from __future__ import annotations

import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env", override=False)

NOTIONAL = 135.0
DAY = "2026-09-21 00:00:00"


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def main():
    import numpy as np
    import psycopg2
    import psycopg2.extras

    from h84_derive_episodes import derive, load

    print("=" * 104)
    print("H109  尾部事件解剖（最亏 5% 的周期到底发生了什么）")
    print("=" * 104)

    eps = derive(load())
    cn = psycopg2.connect(_dsn("alpha_arena"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        "SELECT created_at, amount_usd::float a, action,"
        "       COALESCE(metadata_json::jsonb->>'phase','?') ph,"
        "       metadata_json::jsonb->>'symbol' sym"
        "  FROM arbitrage_paper_ledgers"
        " WHERE account_id=101 AND action<>'create_account'"
        "   AND metadata_json::jsonb->>'lane_id'='mm_asterdex'"
        "   AND created_at >= %s", (DAY,))
    by = defaultdict(float)
    byflat = defaultdict(float)
    for r in cur.fetchall():
        k = (r["sym"], int(r["created_at"].timestamp()))
        by[k] += r["a"]
        if r["ph"] == "flatten":
            byflat[k] += r["a"]
    cn.close()

    def ep_pnl(e):
        t = 0.0
        for x in {y for y in e["fillts"] if y}:
            for ds in (0, 1, -1, 2):
                k = (e["sym"], int(x) + ds)
                if k in by:
                    t += by[k]
                    break
        return t

    def ep_flat(e):
        t = 0.0
        for x in {y for y in e["fillts"] if y}:
            for ds in (0, 1, -1, 2):
                k = (e["sym"], int(x) + ds)
                if k in byflat:
                    t += byflat[k]
                    break
        return t

    recs = []
    for e in eps:
        v = ep_pnl(e)
        if not np.isfinite(v):
            continue
        recs.append({"e": e, "pnl": v, "flat_pnl": ep_flat(e)})
    if len(recs) < 100:
        print(f"  样本不足（{len(recs)}）")
        return 0
    v = np.array([r["pnl"] for r in recs])
    o = np.argsort(v)
    k5 = max(1, int(len(v) * 0.05))
    tail = o[:k5]
    print(f"\n  周期 {len(recs)}  合计 {v.sum():+.4f} USD  均值 {v.mean():+.5f}")
    print(f"  最亏 5% = {k5} 个周期，合计 **{v[tail].sum():+.4f}** "
          f"（占全部的 {v[tail].sum()/v.sum()*100:.0f}%）")

    def blk(idx, lab):
        pe = [recs[i]["e"] for i in idx]
        pn = np.array([recs[i]["pnl"] for i in idx])
        dur = np.array([x["dur_s"] for x in pe])
        nf = np.array([x["n"] for x in pe])
        notl = np.array([x["notional"] for x in pe])
        fl = np.array([1 if x["flat"] else 0 for x in pe])
        fpnl = np.array([recs[i]["flat_pnl"] for i in idx])
        print(f"\n  ── {lab}（n={len(idx)}）──")
        print(f"     净额       均值 {pn.mean():>+9.5f}  中位 {np.median(pn):>+9.5f}")
        print(f"     峰值名义   中位 {np.median(notl):>9.2f}  p90 {np.percentile(notl,90):>9.2f}")
        print(f"     时长 s     中位 {np.median(dur):>9.0f}  p90 {np.percentile(dur,90):>9.0f}")
        print(f"     笔数       中位 {np.median(nf):>9.0f}")
        print(f"     强平比例   {fl.mean()*100:>9.1f}%")
        print(f"     强平腿金额 合计 {fpnl.sum():>+9.4f}")
        return {"notl": np.median(notl), "dur": np.median(dur),
                "n": np.median(nf), "flat": fl.mean(), "fpnl": fpnl.sum()}

    rest = o[k5:]
    a = blk(tail, "最亏 5% 周期")
    b = blk(rest, "其余 95% 周期")

    print("\n" + "=" * 104)
    print("尾部 vs 正常的特征对比")
    print("=" * 104)
    print(f"\n  {'特征':<16} {'最亏5%':>12} {'其余95%':>12} {'比值':>10}")
    print("  " + "-" * 54)
    for k, lab in (("notl", "峰值名义"), ("dur", "时长 s"), ("n", "笔数")):
        r = a[k] / b[k] if b[k] else float("nan")
        print(f"  {lab:<16} {a[k]:>12.2f} {b[k]:>12.2f} {r:>10.2f}x")
    print(f"  {'强平比例':<16} {a['flat']*100:>11.1f}% {b['flat']*100:>11.1f}%"
          f" {(a['flat']/b['flat'] if b['flat'] else float('nan')):>10.2f}x")

    print("\n" + "=" * 104)
    print("尾部周期的币种分布")
    print("=" * 104)
    c = Counter(recs[i]["e"]["sym"] for i in tail)
    tot = sum(c.values())
    allc = Counter(r["e"]["sym"] for r in recs)
    print(f"\n  {'币':<10} {'尾部周期':>9} {'占比':>8} {'该币总周期':>10} {'尾部集中度':>10}")
    print("  " + "-" * 52)
    for s, n in c.most_common():
        print(f"  {s:<10} {n:>9} {n/tot*100:>7.1f}% {allc[s]:>10} "
              f"{n/max(allc[s],1)*100:>9.1f}%")

    print("\n" + "=" * 104)
    print("判据")
    print("=" * 104)
    rn = a["notl"] / b["notl"] if b["notl"] else float("nan")
    rd = a["dur"] / b["dur"] if b["dur"] else float("nan")
    print(f"\n  峰值名义比值 {rn:.2f}x   时长比值 {rd:.2f}x   强平比例 "
          f"{a['flat']*100:.1f}% vs {b['flat']*100:.1f}%")
    if rn > 1.5:
        print("  ⇒ **峰值名义显著更大** ⇒ 规模问题 ⇒ 限制单币名义/腿量")
    if rd > 1.5:
        print("  ⇒ **时长显著更长** ⇒ 暴露时间问题 ⇒ 缩短持有/加时间止损")
    if a["flat"] > b["flat"] * 1.5:
        print("  ⇒ **强平比例显著更高** ⇒ 尾部由强平造成 ⇒ 继续压制强平")
    if rn <= 1.5 and rd <= 1.5 and a["flat"] <= b["flat"] * 1.5:
        print("  ⇒ 三项都不显著 ⇒ **尾部是行情跳变** ⇒ 只能靠止损/尾部保险")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
