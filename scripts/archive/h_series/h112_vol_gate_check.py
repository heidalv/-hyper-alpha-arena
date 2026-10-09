"""H112：vol_pause 闸该不该放宽？—— 用实测判断，不靠"它挡了很多"。

# 为什么现在能测

收缩宇宙后只剩 ASTER/SOL/XRP（`fill_basis` 里都有 `seg_low/seg_high`）。
`vol_pause_sigma = 0.7` ⇒ σ_norm > 0.7 时暂停报价，实测挡掉 **32.3%** 的拦截。

**但这个闸是保护性的：高波动时做市被逆选择更重。**
**⇒ 不能因为"它挡了很多"就放宽。必须看：高 σ 时期的入场腿是不是真的更差？**

# 用可观测的代理测 σ

`fill_basis` 没有 σ，但有 `seg_low / seg_high / engine_mid` ⇒
**段宽 bp = (seg_high − seg_low) / mid × 1e4**，这是"该段行情振幅"的直接代理。

# 本脚本做什么

按**段宽分档**，看每一档的：
  · 每周期净额（完整口径）
  · 强平率
  · 入场腿 pnl

判据（事先定死）：
  · 若高段宽档的每周期净额**显著更差** ⇒ 闸是对的，**不放宽**
  · 若各档差不多、或高段宽档**更好** ⇒ 闸误杀了机会 ⇒ 可放宽（A/B）
  · 样本 <30/档 ⇒ 只报方向

用法：
    .venv\\Scripts\\python.exe scripts\\h112_vol_gate_check.py
"""
from __future__ import annotations

import os
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

    print("=" * 100)
    print("H112  vol_pause 闸该不该放宽？（按段宽分档看实测）")
    print("=" * 100)

    rows = load()
    eps = derive(rows)
    byk = {}
    for r in rows:
        byk.setdefault((r.get("symbol"), r.get("ts")), r)

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
    byfill = defaultdict(float)
    for r in cur.fetchall():
        k = (r["sym"], int(r["created_at"].timestamp()))
        by[k] += r["a"]
        if r["ph"] == "fill" and r["action"] == "paper_pnl":
            byfill[k] += r["a"]
    cn.close()

    def get(d, e):
        t = 0.0
        for x in {y for y in e["fillts"] if y}:
            for ds in (0, 1, -1, 2):
                k = (e["sym"], int(x) + ds)
                if k in d:
                    t += d[k]
                    break
        return t

    recs = []
    for e in eps:
        r0 = byk.get((e["sym"], e.get("ts0")))
        if r0 is None:
            continue
        m0 = float(r0.get("engine_mid") or 0)
        lo = float(r0.get("seg_low") or 0)
        hi = float(r0.get("seg_high") or 0)
        if m0 <= 0 or hi <= lo:
            continue
        segw = (hi - lo) / m0 * 1e4          # 段宽 bp（σ 的代理）
        v = get(by, e)
        if not np.isfinite(v):
            continue
        recs.append({"segw": segw, "pnl": v, "flat": e["flat"],
                     "fill": get(byfill, e), "sym": e["sym"], "n": e["n"]})
    if len(recs) < 50:
        print(f"  样本不足（{len(recs)}）")
        return 0

    sw = np.array([r["segw"] for r in recs])
    pn = np.array([r["pnl"] for r in recs])
    fl = np.array([1 if r["flat"] else 0 for r in recs])
    print(f"\n  周期 {len(recs)}   段宽 bp：中位 {np.median(sw):.3f}  "
          f"p25 {np.percentile(sw,25):.3f}  p75 {np.percentile(sw,75):.3f}  "
          f"p90 {np.percentile(sw,90):.3f}")

    qs = np.percentile(sw, [20, 40, 60, 80])
    bins = [(-1, qs[0]), (qs[0], qs[1]), (qs[1], qs[2]), (qs[2], qs[3]), (qs[3], 1e18)]
    print(f"\n  {'段宽档 (bp)':>22} {'周期':>6} {'强平率':>8} {'入场腿均值USD':>14} "
          f"{'每周期净额':>12} {'胜率':>7}")
    print("  " + "-" * 76)
    tab = []
    for lo, hi in bins:
        m = (sw >= lo) & (sw < hi)
        if m.sum() < 5:
            continue
        lab = f"[{lo:.3f}, {hi:.3f})" if lo > -1 else f"(-inf, {hi:.3f})"
        fillm = np.array([r["fill"] for r, k in zip(recs, m) if k]).mean()
        tab.append({"lo": lo, "hi": hi, "n": int(m.sum()),
                    "flat": float(fl[m].mean()), "pnl": float(pn[m].mean()),
                    "fill": float(fillm), "win": float((pn[m] > 0).mean())})
        print(f"  {lab:>22} {int(m.sum()):>6} {fl[m].mean()*100:>7.1f}% "
              f"{fillm:>+14.5f} {pn[m].mean():>+12.5f} {tab[-1]['win']*100:>6.1f}%")

    print("\n" + "=" * 100)
    print("判据")
    print("=" * 100)
    if len(tab) >= 3:
        lo_, hi_ = tab[0], tab[-1]
        print(f"\n  最低段宽档：强平率 {lo_['flat']*100:.1f}%  每周期净额 "
              f"{lo_['pnl']:+.5f}")
        print(f"  最高段宽档：强平率 {hi_['flat']*100:.1f}%  每周期净额 "
              f"{hi_['pnl']:+.5f}")
        d = lo_["pnl"] - hi_["pnl"]
        print(f"\n  低段宽 − 高段宽 = {d:+.5f} USD/周期")
        print(f"  最高档强平率 / 最低档 = "
              f"{hi_['flat']/max(lo_['flat'],1e-9):.2f}x")
        if hi_["pnl"] < lo_["pnl"] - 0.01:
            print("\n  ⇒ **高波动档明显更差** ⇒ vol_pause 闸是**对的**，不应放宽")
        elif hi_["pnl"] > lo_["pnl"] + 0.01:
            print("\n  ⇒ **高波动档反而更好** ⇒ 闸在**误杀机会** ⇒ 可放宽（需 A/B）")
        else:
            print("\n  ⇒ 各档差异不大 ⇒ 闸的影响中性；放宽与否影响有限")
    print(f"\n  ⚠️ 段宽是 σ 的**代理**，不等于 `sigma_norm`（后者相对长期基准）。")
    print(f"     该结论只用于方向判断；真正决定需对 `vol_pause_sigma` 做 A/B。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
