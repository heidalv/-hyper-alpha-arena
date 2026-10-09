"""H65：入场腿的巨亏是不是「陈旧 mid / 数据跳空」造成的？

# 要验证的假设

实盘 `fill`（入场）腿：均值 −2.3714bp，**中位 +1.2719bp**，胜率 0.7466，
但 91 笔（5%）吃掉 87% 的亏损，单笔到 **−124.197bp**（ASTER sell）。

**一个能同时解释"中位正、均值负、集中在薄币"的机制**：
引擎用**陈旧的 mid** 报价 ⇒ 成交价相对"真实当时 mid"是错的
⇒ 记账时用「成交价 vs 记账 mid」算 paper_pnl ⇒ 出现巨大亏损。

证据链上的可疑点：
  · `mid_splice_on_gap` = **None**（未设置）⇒ 跳空无保护
  · `max_quote_age_sec` = **None** ⇒ 挂单无最大存续期保护
  · `vol_pause_mult` = **0.0** ⇒ 疑似波动闸未启用
  · 巨亏集中在 ASTER / UNI / ARB / DOGE（薄币 = 数据稀疏 = 陈旧风险最大）

# 本脚本怎么验

对**实盘账本里每一笔巨亏的 fill 腿**，回数据库查：
  1. 该时刻前后 **book_ticker 的数据间隔**（间隔大 ⇒ 陈旧）
  2. 成交价与该时刻**真实 mid** 的偏离（bp）
  3. 该时刻的**价差**是否异常放大

对照：同一币、同一时段**正常笔**的同样指标。
若巨亏笔的"前置数据间隔"或"价差"显著大于正常笔 ⇒ 假设成立。

判据（事先定死）：
  · 巨亏笔的前置间隔中位 > 正常笔的 3 倍 ⇒ **陈旧 mid 是主因** ⇒ 对策是数据新鲜度闸
  · 巨亏笔的价差中位 > 正常笔的 3 倍 ⇒ **是行情事件** ⇒ 对策是价差闸/波动闸
  · 两者都不显著 ⇒ 是别的机制，继续查

用法：
    .venv\\Scripts\\python.exe scripts\\h65_stale_mid_hypothesis.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

NOTIONAL = 28.3195


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

    ca = psycopg2.connect(_dsn("alpha_arena"))
    ca.autocommit = True
    cura = ca.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # 取 09-20 起全部 fill 腿，带 symbol/px/mid/时间
    cura.execute(
        "SELECT amount_usd::float a, created_at,"
        "       metadata_json::jsonb->>'symbol' sym,"
        "       metadata_json::jsonb->>'side' sd,"
        "       (metadata_json::jsonb->>'px')::float px,"
        "       (metadata_json::jsonb->>'mid')::float mid,"
        "       (metadata_json::jsonb->>'qty')::float qty"
        "  FROM arbitrage_paper_ledgers"
        " WHERE account_id=101 AND action='paper_pnl'"
        "   AND metadata_json::jsonb->>'lane_id'='mm_asterdex'"
        "   AND COALESCE(metadata_json::jsonb->>'phase','')='fill'"
        "   AND created_at >= '2026-09-20 00:00:00'"
        "   AND metadata_json::jsonb->>'px' IS NOT NULL"
        " ORDER BY created_at")
    rows = cura.fetchall()
    ca.close()
    if not rows:
        print("无 fill 腿数据")
        return 1
    print("=" * 100)
    print("H65  入场腿巨亏的机制 —— 陈旧 mid？行情事件？还是别的")
    print("=" * 100)
    print(f"  fill 腿样本 {len(rows):,} 笔（09-20 起）")

    bp = np.array([r["a"] for r in rows]) / NOTIONAL * 1e4
    o = np.argsort(bp)
    k5 = max(1, int(len(bp) * 0.05))
    worst_idx = set(o[:k5].tolist())
    print(f"  均值 {bp.mean():+.4f}bp  中位 {np.median(bp):+.4f}bp  "
          f"最亏 5% = {k5} 笔，合计 {bp[o[:k5]].sum():+.1f}bp")

    # 逐笔回查盘口
    cb = psycopg2.connect(_dsn("alpha_market"))
    cb.autocommit = True
    curb = cb.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    def probe(sym, ts):
        """返回该时刻的 (前置数据间隔ms, 价差bp, 真实mid)"""
        vs = sym if sym.endswith("USDT") else f"{sym}USDT"
        ms = int(ts.timestamp() * 1000)
        curb.execute(
            "SELECT event_ts_ms, bid_px::float b, ask_px::float a"
            "  FROM asterdex_book_ticker WHERE symbol=%s"
            "   AND event_ts_ms <= %s AND event_ts_ms > %s - 300000"
            " ORDER BY event_ts_ms DESC LIMIT 2", (vs, ms, ms))
        d = curb.fetchall()
        if not d:
            return None
        cur_ = d[0]
        mid = 0.5 * (cur_["b"] + cur_["a"])
        sp = (cur_["a"] - cur_["b"]) / mid * 1e4 if mid > 0 else float("nan")
        gap = (ms - int(cur_["event_ts_ms"]))
        return {"gap_ms": gap, "spread_bp": sp, "mid": mid}

    worst_res, norm_res = [], []
    from collections import Counter
    worst_syms = Counter()
    for i, r in enumerate(rows):
        p = probe(r["sym"], r["created_at"])
        if not p:
            continue
        rec = {"bp": bp[i], "gap_ms": p["gap_ms"], "spread_bp": p["spread_bp"],
               "dev_bp": (r["px"] - p["mid"]) / p["mid"] * 1e4, "sym": r["sym"]}
        if i in worst_idx:
            worst_res.append(rec)
            worst_syms[r["sym"]] += 1
        else:
            norm_res.append(rec)
    cb.close()

    def summ(rs, label):
        if not rs:
            print(f"\n  {label}: 无数据")
            return
        gap = np.array([x["gap_ms"] for x in rs])
        sp = np.array([x["spread_bp"] for x in rs])
        dev = np.array([x["dev_bp"] for x in rs])
        print(f"\n  ── {label}  n={len(rs):,}")
        print(f"     前置数据间隔 ms:  中位 {np.median(gap):>10,.0f}   "
              f"p75 {np.percentile(gap,75):>10,.0f}   p95 {np.percentile(gap,95):>10,.0f}")
        print(f"     价差 bp:          中位 {np.nanmedian(sp):>10.4f}   "
              f"p75 {np.nanpercentile(sp,75):>10.4f}   p95 {np.nanpercentile(sp,95):>10.4f}")
        print(f"     成交价偏离真实mid: 中位 {np.median(dev):>10.4f}bp  "
              f"p5 {np.percentile(dev,5):>10.4f}  p95 {np.percentile(dev,95):>10.4f}")
        return gap, sp, dev

    print("\n" + "=" * 100)
    print("巨亏 5% vs 正常笔 —— 三项指标对照")
    print("=" * 100)
    w = summ(worst_res, "最亏 5%")
    n = summ(norm_res, "其余 95%")

    if w and n:
        print("\n" + "=" * 100)
        print("判读")
        print("=" * 100)
        rg = np.median(w[0]) / max(np.median(n[0]), 1)
        rs = np.nanmedian(w[1]) / max(np.nanmedian(n[1]), 1e-9)
        print(f"  前置数据间隔 比值（巨亏/正常）= **{rg:.1f}x**")
        print(f"  价差          比值（巨亏/正常）= **{rs:.1f}x**")
        if rg > 3:
            print("  ⇒ **陈旧 mid 是主因** ⇒ 对策：数据新鲜度闸（max_quote_age / 跳空保护）")
        elif rs > 3:
            print("  ⇒ **是行情事件**（价差放大）⇒ 对策：价差/波动闸")
        else:
            print("  ⇒ 两者都不显著 ⇒ 是别的机制，需继续查（可能是成交价本身偏离）")
        print(f"\n  巨亏笔的币种分布：{dict(worst_syms.most_common(8))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
