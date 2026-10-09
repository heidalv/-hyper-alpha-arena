"""H67：5.5bp 的 mid 偏差是"引擎用平滑中价"还是"引擎用陈旧 mid"？

H66 结果：|账本mid − 真实盘口mid| 中位 **5.5440bp**，p95 20.84，最大 75.66。
且巨亏组与正常组**几乎相同**（5.5358 vs 5.5440）⇒ **系统性，不是尾部特有**。

# 为什么不能直接下结论

账本 `paper_pnl` 与账本自己的 `mid` 是**自洽的**（H66 重算差异只来自公式细节）。
所以"错了"指的是：**记账用的公允价值（mid）与真实盘口 mid 差 5.5bp**。
但有两种完全不同的解释，**对策完全相反**：

  **解释 A：引擎的 mid 是"平滑/带迟滞的中价"**（做市常用做法，抗抖动）
     ⇒ 不是 bug，但会让 P&L 归因偏离真实 ⇒ **只影响观测口径，不影响实际成交**
     ⇒ 特征：偏差随**价差**成比例放大（平滑必然落在价差内），且**与成交方向无关**

  **解释 B：引擎的 mid 是陈旧的**（用了几十秒前的价）
     ⇒ 是真 bug，会让报价挂在错误位置、被系统性打穿
     ⇒ 特征：偏差与**数据间隔/成交方向**相关，且在**波动大的时刻**显著放大

# 本脚本怎么区分

对每笔 fill 腿算 |账本mid − 真实mid|，然后看它与以下量的关系：
  1. 该币的**价差**（解释 A 预测：比值恒定 ≈ 半个价差的某个倍数）
  2. 该笔的**成交方向**（解释 B 预测：买单与卖单偏差符号相反且不对称）
  3. 该笔的**帧延迟**（解释 B 预测：延迟越大偏差越大）
  4. **逐币偏差 vs 逐币价差**的相关性（解释 A 预测：强正相关）

判据（事先定死）：
  · 偏差/半价差 的比值**跨币稳定**（变异系数 < 0.5）⇒ 解释 A（平滑），不是 bug
  · 比值跨币**极不稳定**或与延迟强相关 ⇒ 解释 B（陈旧），是 bug

用法：
    .venv\\Scripts\\python.exe scripts\\h67_mid_bias_decomposition.py
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
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
        "   AND created_at >= '2026-09-20 00:00:00'"
        "   AND metadata_json::jsonb->>'px' IS NOT NULL"
        " ORDER BY created_at")
    rows = cura.fetchall()
    ca.close()

    cb = psycopg2.connect(_dsn("alpha_market"))
    cb.autocommit = True
    curb = cb.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cache = {}

    def load(sym):
        vs = sym if sym.endswith("USDT") else f"{sym}USDT"
        if vs in cache:
            return cache[vs]
        curb.execute(
            "SELECT event_ts_ms, bid_px::float b, ask_px::float a"
            "  FROM asterdex_book_ticker WHERE symbol=%s"
            "   AND event_ts_ms > (extract(epoch from now())*1000)::bigint - 172800000"
            " ORDER BY event_ts_ms", (vs,))
        d = curb.fetchall()
        if not d:
            cache[vs] = None
            return None
        t = np.array([r["event_ts_ms"] for r in d], dtype=np.int64)
        b = np.array([r["b"] for r in d]); a = np.array([r["a"] for r in d])
        ok = (b > 0) & (a > b)
        cache[vs] = (t[ok], 0.5 * (b[ok] + a[ok]), b[ok], a[ok])
        return cache[vs]

    recs = []
    for r in rows:
        c = load(r["sym"])
        if c is None:
            continue
        t, mid, b, a = c
        ms = int(r["created_at"].timestamp() * 1000)
        j = np.searchsorted(t, ms, side="right") - 1
        if j < 0:
            continue
        rm = float(mid[j])
        if rm <= 0:
            continue
        half_sp = 0.5 * (float(a[j]) - float(b[j])) / rm * 1e4
        recs.append({
            "sym": r["sym"], "sd": str(r["sd"]).lower(),
            "bp": r["a"] / NOTIONAL * 1e4,
            "dmid_abs": abs(r["mid"] - rm) / rm * 1e4,
            "dmid_signed": (r["mid"] - rm) / rm * 1e4,
            "half_sp": half_sp, "mid_real": rm,
            "lag_ms": int(ms - int(t[j])),
            "qty": r["qty"],
        })
    cb.close()
    if not recs:
        print("无数据")
        return 1

    print("=" * 104)
    print("H67  mid 偏差分解：平滑中价（正常）还是陈旧 mid（bug）")
    print("=" * 104)
    print(f"  样本 {len(recs):,} 笔")

    # ── 逐币 ──
    by = defaultdict(list)
    for x in recs:
        by[x["sym"]].append(x)
    print(f"\n  {'币':<12} {'n':>6} {'半价差bp':>10} {'|Δmid|bp':>10} "
          f"{'比值 |Δmid|/半价差':>18} {'lag中位ms':>10}")
    print("  " + "-" * 74)
    ratios = []
    for s, v in sorted(by.items(), key=lambda kv: -len(kv[1])):
        if len(v) < 10:
            continue
        hs = float(np.median([x["half_sp"] for x in v]))
        dm = float(np.median([x["dmid_abs"] for x in v]))
        lg = float(np.median([x["lag_ms"] for x in v]))
        ratio = dm / hs if hs > 0 else float("nan")
        ratios.append(ratio)
        print(f"  {s:<12} {len(v):>6} {hs:>10.4f} {dm:>10.4f} {ratio:>18.2f} {lg:>10,.0f}")

    ratios = np.array([r for r in ratios if np.isfinite(r)])
    print(f"\n  比值 |Δmid|/半价差：中位 {np.median(ratios):.2f}   "
          f"范围 [{ratios.min():.2f}, {ratios.max():.2f}]   "
          f"变异系数 {ratios.std()/max(ratios.mean(),1e-9):.2f}")

    # ── 按方向 ──
    print("\n" + "=" * 104)
    print("按成交方向（解释 B 预测：买单/卖单偏差符号相反）")
    print("=" * 104)
    for sd in ("buy", "sell"):
        v = [x for x in recs if x["sd"] == sd]
        if not v:
            continue
        ds = np.array([x["dmid_signed"] for x in v])
        da = np.array([x["dmid_abs"] for x in v])
        print(f"  {sd:<6} n={len(v):>5}  有符号偏差 中位 {np.median(ds):>+9.4f}bp   "
              f"|偏差| 中位 {np.median(da):>9.4f}bp")

    # ── 按延迟分档 ──
    print("\n" + "=" * 104)
    print("按帧延迟分档（解释 B 预测：延迟越大偏差越大）")
    print("=" * 104)
    lag = np.array([x["lag_ms"] for x in recs])
    dmabs = np.array([x["dmid_abs"] for x in recs])
    qs = np.percentile(lag, [25, 50, 75])
    print(f"\n  延迟分位: {[int(q) for q in qs]}")
    print(f"\n  {'延迟区间ms':>20} {'n':>7} {'|Δmid|中位':>12} {'半价差中位':>12}")
    print("  " + "-" * 56)
    for lo, hi in ((-1, qs[0]), (qs[0], qs[1]), (qs[1], qs[2]), (qs[2], 1e18)):
        m = (lag > lo) & (lag <= hi)
        if m.sum() < 10:
            continue
        print(f"  {f'({int(lo)}, {int(hi)}]':>20} {int(m.sum()):>7} "
              f"{np.median(dmabs[m]):>12.4f} "
              f"{np.median([x['half_sp'] for x in recs if True][0]) if False else np.median(np.array([x['half_sp'] for x in recs])[m]):>12.4f}")
    # 相关性
    if len(lag) > 50 and lag.std() > 0:
        c1 = float(np.corrcoef(lag, dmabs)[0, 1])
        c2 = float(np.corrcoef(np.array([x["half_sp"] for x in recs]), dmabs)[0, 1])
        print(f"\n  corr(延迟, |Δmid|)   = {c1:+.4f}")
        print(f"  corr(半价差, |Δmid|) = {c2:+.4f}")

    print("\n" + "=" * 104)
    print("判读（事先定死）")
    print("=" * 104)
    cv = ratios.std() / max(ratios.mean(), 1e-9)
    if cv < 0.5:
        print(f"  比值跨币**稳定**（变异系数 {cv:.2f} < 0.5）")
        print("  ⇒ **解释 A：引擎用的是「平滑中价」** ⇒ 不是 bug。")
        print("     影响：仅记账归因偏离真实公允价值；**实际成交价不受影响**。")
        print("     但 P&L 的绝对值必须以**真实 mid** 重算后才能用于策略判断。")
    else:
        print(f"  比值跨币**不稳定**（变异系数 {cv:.2f} ≥ 0.5）")
        print("  ⇒ 偏向 **解释 B：陈旧 mid** ⇒ 需查 tick 的 mid 来源")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
