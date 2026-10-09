"""H68：引擎的 mid 有多新？—— tick 节奏与快照滞后

# H67 的方向性证据（这是关键）

| 方向 | 笔数 | 有符号偏差（账本mid − 真实mid） |
|---|---|---|
| buy | 1,018 | **+2.7517bp** |
| sell | 1,001 | **−3.9835bp** |

**买单时引擎的 mid 偏高、卖单时偏低** —— 这是**滞后**的典型特征：
引擎的参考中价在跟着市场跑（市场跌了它还没跌 ⇒ 买单显得"偏高"）。

且 `corr(帧延迟, |Δmid|) = −0.087`（≈0）⇒ 不是"某一笔查询延迟"，
而是**引擎整体用了一个较旧的 mid 源**。

# 嫌疑

`runner.py:1880` 的实盘 tick 读的是 **`market_orderbook_snapshots`**：

    SELECT timestamp, best_bid, best_ask FROM market_orderbook_snapshots
     WHERE exchange=:e AND symbol=:s ... ORDER BY timestamp DESC LIMIT 1

而该表的写入节奏是 **~30 秒一行**（实测：6,358 行 / 2 小时 / 9 币 ≈ 每币每 2 分钟 4 行）。
对照 `asterdex_book_ticker` 是 **36ms** 一行（快 800 倍）。

⇒ 若 tick 节奏也是 15s 级，则引擎的 mid 平均**滞后 7.5~15 秒**。
在 15 秒内 BTC 走 3~5bp 完全正常 —— **与实测的 5.5bp 偏差量级吻合**。

# 本脚本量什么

  1. 车道的 **tick 间隔**（从 status 的 ticks 与运行时长推算 + 直接看快照时间戳）
  2. `market_orderbook_snapshots` 对该车道币的**写入间隔**
  3. 引擎 mid 相对真实 mid 的**滞后量**（用"多长时间之后真实 mid 才追上引擎 mid"反推）

判据：
  · 若 tick 间隔 ≥10s 且快照间隔 ≥20s ⇒ **mid 源滞后是结构性的**
    ⇒ 对策：把实盘 tick 的 mid 换成 `asterdex_book_ticker` 的实时 mid
  · 若 tick 间隔 <2s ⇒ 滞后来自别处

用法：
    .venv\\Scripts\\python.exe scripts\\h68_mid_lag_probe.py
"""
from __future__ import annotations

import json
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

LANE_SYMS = ["ASTER", "XRP", "SOL", "DOGE", "UNI", "SEI", "PENDLE",
             "VIRTUAL", "1000SHIB", "ARB"]


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

    print("=" * 100)
    print("H68  引擎 mid 的新鲜度 —— tick 节奏与快照滞后")
    print("=" * 100)

    sf = ROOT / "logs" / "mm_lane_status.json"
    st = json.loads(sf.read_text(encoding="utf-8")) if sf.exists() else {}
    ticks = int(st.get("ticks") or 0)
    print(f"\n  status: ticks={ticks}  ts={st.get('ts')}  last_tick_ts={st.get('last_tick_ts')}")
    if st.get("ts") and st.get("last_tick_ts"):
        print(f"  最近两次 tick 间隔 = {float(st['ts']) - float(st['last_tick_ts']):.2f}s")

    cn = psycopg2.connect(_dsn("alpha_market"))
    cn.autocommit = True
    cur = cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # ── 1) market_orderbook_snapshots 的写入间隔（引擎 mid 的来源）──
    print("\n" + "=" * 100)
    print("1) market_orderbook_snapshots（**引擎 mid 的来源**）的写入间隔")
    print("=" * 100)
    print(f"\n  {'币':<12} {'近2h行数':>10} {'间隔中位ms':>12} {'间隔p90ms':>12} "
          f"{'最新距今s':>12}")
    print("  " + "-" * 62)
    ob_gaps = []
    for s in LANE_SYMS:
        cur.execute(
            "SELECT timestamp FROM market_orderbook_snapshots"
            " WHERE exchange='asterdex' AND symbol=%s"
            "   AND timestamp > (extract(epoch from now())*1000)::bigint - 7200000"
            " ORDER BY timestamp", (s,))
        d = cur.fetchall()
        if len(d) < 3:
            print(f"  {s:<12} {len(d):>10}  （不足）")
            continue
        t = np.array([int(r["timestamp"]) for r in d], dtype=np.int64)
        gaps = np.diff(t)
        age = (int(__import__("time").time() * 1000) - int(t[-1])) / 1000.0
        ob_gaps.extend(gaps.tolist())
        print(f"  {s:<12} {len(d):>10,} {np.median(gaps):>12,.0f} "
              f"{np.percentile(gaps,90):>12,.0f} {age:>12.1f}")
    if ob_gaps:
        og = np.array(ob_gaps)
        print(f"\n  ⇒ 全部车道币合并：间隔 中位 {np.median(og):,.0f}ms  "
              f"均值 {og.mean():,.0f}ms  p90 {np.percentile(og,90):,.0f}ms")

    # ── 2) asterdex_book_ticker 的间隔（实时源）──
    print("\n" + "=" * 100)
    print("2) asterdex_book_ticker（**实时源**）的间隔 —— 快多少倍")
    print("=" * 100)
    print(f"\n  {'币':<12} {'近2h行数':>12} {'间隔中位ms':>12}")
    print("  " + "-" * 40)
    bt_med = []
    for s in LANE_SYMS[:6]:
        vs = s if s.endswith("USDT") else f"{s}USDT"
        cur.execute(
            "SELECT event_ts_ms FROM asterdex_book_ticker WHERE symbol=%s"
            "   AND event_ts_ms > (extract(epoch from now())*1000)::bigint - 7200000"
            " ORDER BY event_ts_ms", (vs,))
        d = cur.fetchall()
        if len(d) < 10:
            continue
        t = np.array([int(r["event_ts_ms"]) for r in d], dtype=np.int64)
        m = float(np.median(np.diff(t)))
        bt_med.append(m)
        print(f"  {s:<12} {len(d):>12,} {m:>12,.0f}")
    if bt_med and ob_gaps:
        print(f"\n  ⇒ **快 {np.median(ob_gaps)/max(np.median(bt_med),1e-9):,.0f} 倍**")

    # ── 3) 引擎 mid 相对真实 mid 的平均滞后（用互相关反推）──
    print("\n" + "=" * 100)
    print("3) 引擎 mid 的滞后时间（用账本 mid 与真实 mid 序列的互相关反推）")
    print("=" * 100)
    ca = psycopg2.connect(_dsn("alpha_arena"))
    ca.autocommit = True
    cura = ca.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cura.execute(
        "SELECT created_at, metadata_json::jsonb->>'symbol' sym,"
        "       (metadata_json::jsonb->>'mid')::float mid"
        "  FROM arbitrage_paper_ledgers"
        " WHERE account_id=101 AND action='paper_pnl'"
        "   AND metadata_json::jsonb->>'lane_id'='mm_asterdex'"
        "   AND created_at >= '2026-09-20 00:00:00'"
        "   AND metadata_json::jsonb->>'mid' IS NOT NULL"
        " ORDER BY created_at")
    led = cura.fetchall()
    ca.close()

    from collections import defaultdict
    bys = defaultdict(list)
    for r in led:
        bys[r["sym"]].append((int(r["created_at"].timestamp() * 1000), r["mid"]))

    print(f"\n  {'币':<10} {'样本':>6} {'最佳领先ms':>12} {'该点相关':>10} {'0ms处相关':>10}")
    print("  " + "-" * 54)
    leads = []
    for s, v in sorted(bys.items(), key=lambda kv: -len(kv[1]))[:6]:
        if len(v) < 40:
            continue
        vs = s if s.endswith("USDT") else f"{s}USDT"
        cur.execute(
            "SELECT event_ts_ms, bid_px::float b, ask_px::float a"
            "  FROM asterdex_book_ticker WHERE symbol=%s"
            "   AND event_ts_ms > (extract(epoch from now())*1000)::bigint - 172800000"
            " ORDER BY event_ts_ms", (vs,))
        d = cur.fetchall()
        if len(d) < 100:
            continue
        t = np.array([int(r["event_ts_ms"]) for r in d], dtype=np.int64)
        mid = 0.5 * (np.array([r["b"] for r in d]) + np.array([r["a"] for r in d]))
        ok = mid > 0
        t, mid = t[ok], mid[ok]
        # 用收益率序列做互相关（去趋势）
        lms = np.array([x[0] for x in v], dtype=np.int64)
        lm = np.array([x[1] for x in v], dtype=np.float64)
        # 引擎 mid 的变化 vs 真实 mid 的变化，在不同滞后下
        best, bestlag, c0 = -2.0, 0, 0.0
        for lag_ms in range(0, 60001, 2000):
            j = np.searchsorted(t, lms - lag_ms, side="right") - 1
            k = j >= 0
            if k.sum() < 30:
                continue
            real = mid[j[k]]
            # 引擎记录的时刻的真实 mid（lag=0）作为参照
            j0 = np.searchsorted(t, lms[k], side="right") - 1
            j0 = np.clip(j0, 0, len(t) - 1)
            base = mid[j0]
            if base.std() == 0 or lm[k].std() == 0:
                continue
            c = float(np.corrcoef(lm[k] / base, real / base)[0, 1])
            if lag_ms == 0:
                c0 = c
            if c > best:
                best, bestlag = c, lag_ms
        leads.append(bestlag)
        print(f"  {s:<10} {len(v):>6} {bestlag:>12,} {best:>10.4f} {c0:>10.4f}")
    if leads:
        print(f"\n  ⇒ 中位最佳滞后 **{np.median(leads):,.0f} ms**"
              f"（≈ {np.median(leads)/1000:.1f} 秒）")
    cn.close()

    print("\n" + "=" * 100)
    print("判读")
    print("=" * 100)
    print("  · 若快照间隔 ≫ book_ticker 间隔 ⇒ 引擎 mid 源**结构性滞后**")
    print("    ⇒ 对策：把 runner 的实盘 mid 从 market_orderbook_snapshots")
    print("      切到 asterdex_book_ticker 的实时 mid（或两者取更近者）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
