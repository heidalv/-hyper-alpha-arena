"""验证 F281 在**实盘**里生效：账本 mid 是否已从 15s 快照切到新鲜盘口。

# 关键口径修正（第 24 条教训候选）

最初的版本直接比 |账本mid − 真实mid|，**这个指标被该币自身价差污染**：
车道里有 SEI（价差 15.8bp）、1000SHIB（14.6bp）、PENDLE（9.5bp）、VIRTUAL（8.9bp）
—— 这些币的**价差本身就贡献 ±价差/2 的 mid 差**，与"引擎 mid 是否陈旧"无关。
所以跨币取中位会把"币种构成"误读成"新鲜度变化"。

⇒ 正确做法：**按币分组**，并报 **|Δmid| / 该币半价差** 这个无量纲比值。
   若引擎 mid 与盘口 mid 同步，该比值应 ≈ 0；若滞后一个快照周期，比值会显著 >1。

同时报修前基线（H67，2,019 笔）作对照。

用法：
    .venv\\Scripts\\python.exe scripts\\verify_f281_live.py --since "2026-09-21 01:08:30"
"""
from __future__ import annotations

import argparse
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

load_dotenv(ROOT / ".env", override=False)


def _dsn(db: str) -> str:
    url = os.getenv("DATABASE_URL") or ""
    for p in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(p, "")
    head, _, _ = url.rpartition("/")
    return head + "/" + db


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-09-21 01:08:30")
    ap.add_argument("--baseline-since", default="2026-09-20 00:00:00",
                    help="对照窗口起点（默认全天，含 F280 前）")
    a = ap.parse_args()

    import numpy as np
    import psycopg2
    import psycopg2.extras

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
        b = np.array([r["b"] for r in d]); aa = np.array([r["a"] for r in d])
        ok = (b > 0) & (aa > b)
        cache[vs] = (t[ok], 0.5 * (b[ok] + aa[ok]),
                     0.5 * (aa[ok] - b[ok]) / (0.5 * (b[ok] + aa[ok])) * 1e4)
        return cache[vs]

    def window(lo, label):
        ca = psycopg2.connect(_dsn("alpha_arena"))
        ca.autocommit = True
        cura = ca.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cura.execute(
            "SELECT created_at, metadata_json::jsonb->>'symbol' sym,"
            "       metadata_json::jsonb->>'side' sd,"
            "       (metadata_json::jsonb->>'mid')::float mid"
            "  FROM arbitrage_paper_ledgers"
            " WHERE account_id=101 AND action='paper_pnl'"
            "   AND metadata_json::jsonb->>'lane_id'='mm_asterdex'"
            "   AND metadata_json::jsonb->>'mid' IS NOT NULL"
            "   AND created_at >= %s ORDER BY created_at", (lo,))
        rows = cura.fetchall()
        ca.close()

        per = defaultdict(list)
        bysd = defaultdict(list)
        for r in rows:
            c = load(r["sym"])
            if c is None:
                continue
            t, mid, half = c
            ms = int(r["created_at"].timestamp() * 1000)
            j = np.searchsorted(t, ms, side="right") - 1
            if j < 0:
                continue
            rm = float(mid[j])
            if rm <= 0:
                continue
            d = (r["mid"] - rm) / rm * 1e4
            per[r["sym"]].append((abs(d), half[j]))
            bysd[str(r["sd"]).lower()].append(d)
        return {"n": len(rows), "per": per, "bysd": bysd}

    print("=" * 96)
    print("F281 实盘验证（按币归一，剔除币种价差构成的污染）")
    print("=" * 96)

    res = {}
    for lo, lab in ((a.baseline_since, "修前/全天基线"),
                    (a.since, "F281 生效后")):
        w = window(lo, lab)
        res[lab] = w
        devs, ratios = [], []
        for s, v in w["per"].items():
            if not v:
                continue
            for dv, hf in v:
                devs.append(dv)
                if hf > 0:
                    ratios.append(dv / hf)
        if not devs:
            print(f"\n【{lab}】无可用样本")
            continue
        devs = np.array(devs); ratios = np.array(ratios)
        print(f"\n【{lab}】n={w['n']:,} 笔")
        print(f"  |Δmid| 中位 {np.median(devs):>9.4f}bp   "
              f"p95 {np.percentile(devs,95):>9.4f}bp")
        print(f"  **|Δmid|/半价差** 中位 **{np.median(ratios):>7.3f}**   "
              f"p95 {np.percentile(ratios,95):>7.3f}")
        for k, v in sorted(w["bysd"].items()):
            if v:
                print(f"    有符号偏差 {k:<5} n={len(v):>5}  中位 "
                      f"{np.median(v):>+9.4f}bp")

    print("\n" + "=" * 96)
    print("逐币对照（|Δmid| / 半价差）")
    print("=" * 96)
    keys = sorted(set(res["修前/全天基线"]["per"]) | set(res["F281 生效后"]["per"]))
    print(f"\n  {'币':<12} {'修前 n':>7} {'修前比值':>10} {'修后 n':>7} {'修后比值':>10} {'改善':>9}")
    print("  " + "-" * 62)
    imps = []
    for s in keys:
        def rat(lab):
            v = res[lab]["per"].get(s) or []
            if not v:
                return None, 0
            d = np.array([x[0] for x in v])
            h = np.array([x[1] for x in v])
            m = h > 0
            if not m.any():
                return None, len(v)
            return float(np.median(d[m] / h[m])), len(v)
        r0, n0 = rat("修前/全天基线")
        r1, n1 = rat("F281 生效后")
        if r0 is None and r1 is None:
            continue
        f0 = f"{r0:.3f}" if r0 is not None else "-"
        f1 = f"{r1:.3f}" if r1 is not None else "-"
        imp = ""
        if r0 and r1:
            imp = f"{(1-r1/r0)*100:+.0f}%"
            imps.append(1 - r1 / r0)
        print(f"  {s:<12} {n0:>7} {f0:>10} {n1:>7} {f1:>10} {imp:>9}")

    print("\n" + "=" * 96)
    if imps:
        m = float(np.mean(imps))
        if m > 0.3:
            print(f"  ⇒ **F281 生效**：平均收窄 {m*100:.0f}% ✓")
        elif m > 0:
            print(f"  ⇒ 有改善但幅度小（平均 {m*100:.0f}%）⇒ 可考虑把刷新间隔再调小")
        else:
            print(f"  ⇒ **未改善**（平均 {m*100:.0f}%）⇒ F281 可能未真正生效 ✗")
    n1 = res["F281 生效后"]["n"]
    if n1 < 200:
        print(f"  ⚠️ F281 生效后只有 {n1} 笔 ⇒ **不足以判定**，需等累计 ≥500 笔再下结论")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
