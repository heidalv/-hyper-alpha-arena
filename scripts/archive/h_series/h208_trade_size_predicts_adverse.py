# -*- coding: utf-8 -*-
"""H208 成交规模能否预测逆向选择（用我们自己的逐笔成交）。

# 为什么测这个

H207 已排除 OFI/失衡类信号（三个信号全部平坦，且跨窗口不一致）。

文献里的标准假设（Kyle 1985 等）：**知情交易者用大单** ⇒
大额主动成交携带更多信息 ⇒ 它打穿我们挂单后，价格更可能继续走。

而 `asterdex_trades` 有**逐笔**（价格/数量/方向/时间戳），完全可测。

# 判据

对每笔 maker 成交，取**成交前**最近窗口内的逐笔成交，聚合出规模特征：

    prev_trade_max_notl   成交前最大单笔名义
    prev_trade_mean_notl  成交前单笔均名义
    prev_trade_notl_sum   成交前总名义
    prev_n_trades         成交前笔数
    same_side_ratio       成交前同向（与成交侧相反=打我们）占比

⚠️ 全部取"成交**前**"的窗口 ⇒ 无前视偏差。

然后把每个特征按分位分 5 桶，看 `price_bp` 是否单调 ⇒ 可用/不可用。

# 与前一轮的纪律一致

**任何"某信号更好"的结论，必须先在多个时间窗口里符号一致。**
本脚本直接输出跨窗口检验，避免再出现"阈值扫描看起来很美、
实际只在某个 regime 里成立"（H207 的教训）。

# 用法

    python scripts/h208_trade_size_predicts_adverse.py --hours 24
    python scripts/h208_trade_size_predicts_adverse.py --hours 30 --window-sec 15
"""
from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"


def dsn(which: str = "alpha_arena") -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env["DATABASE_URL"]
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    base, _, _ = url.rpartition("/")
    return f"{base}/{which}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--window-sec", type=int, default=15,
                    help="成交前多长的窗口内聚合逐笔特征")
    a = ap.parse_args()

    import psycopg

    W = int(a.window_sec) * 1000

    print("=" * 100)
    print("H208  成交规模能否预测 price_bp")
    print("=" * 100)
    print(f"  窗口 {a.hours:.0f}h   特征窗口 = 成交前 {a.window_sec}s")

    # ── 一次查询里把两边的数据都取回来，在 Python 里做窗口聚合 ──
    # （用 SQL 的横向 JOIN 会退化成 O(n·m)；逐币内存聚合是 O(n log m)）
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("SET statement_timeout = 300000")
            cur.execute("""
                SELECT ts, symbol, lower(meta_json->>'side') AS side, price_bp, notional
                FROM lane_ledger
                WHERE lane_id=%s AND ts > now() - make_interval(hours => %s::int)
                  AND meta_json->>'flatten' NOT IN ('true','True')
                ORDER BY symbol, ts
            """, (LANE, int(a.hours)))
            fills = [{"ts": r[0], "symbol": r[1], "side": r[2],
                      "price_bp": float(r[3] or 0), "notional": float(r[4] or 0)}
                     for r in cur.fetchall()]

    if len(fills) < 200:
        print(f"  成交不足（{len(fills)}）")
        return 0
    syms = sorted({f["symbol"] for f in fills})
    t0 = min(f["ts"] for f in fills)
    print(f"  成交 {len(fills)} 笔，币 {syms}")

    # ══ [F337] 符号体系映射 —— 本项目第三次踩同一个坑 ══
    #
    # 三张表的 `symbol` 写法**不一致**：
    #
    #   `lane_ledger`                  → `ASTER`      （**裸符号**）
    #   `asterdex_trades`              → `ASTERUSDT`  （带 USDT）
    #   `market_orderbook_snapshots`   → `ASTER`      （裸符号）
    #   `asterdex_book_ticker`         → `ASTERUSDT`  （带 USDT）
    #
    # 前两次踩坑记录：
    #   · h204：拿 `XRPUSDT` 查 `market_orderbook_snapshots`（要裸符号）⇒ 行情侧全空
    #   · h208 首版：拿 `ASTER` 查 `asterdex_trades`（要带 USDT）⇒ 逐笔取到 0 条
    #
    # ⇒ 统一在这里做一次显式映射，**并且断言映射后确实取到数据**
    #   （静默取空是最危险的失败模式：后续所有统计都变成"样本不足"而不是报错）。
    def to_usdt(s: str) -> str:
        return s if s.endswith("USDT") else s + "USDT"

    trade_syms = [to_usdt(s) for s in syms]
    print(f"  映射到逐笔表的符号：{trade_syms}")

    with psycopg.connect(dsn("alpha_market")) as c:
        with c.cursor() as cur:
            cur.execute("SET statement_timeout = 300000")
            cur.execute("""
                SELECT symbol, event_ts_ms, price, qty, is_buyer_maker
                FROM asterdex_trades
                WHERE symbol = ANY(%s)
                  AND event_ts_ms >= (extract(epoch FROM %s::timestamptz)*1000 - 60000)
                ORDER BY symbol, event_ts_ms
            """, (trade_syms, t0))
            trades: dict = {}
            for sym, ms, px, qty, bm in cur.fetchall():
                # is_buyer_maker=True ⇒ 主动方是**卖方**
                side = "sell" if bm else "buy"
                notl = float(px or 0) * float(qty or 0)
                trades.setdefault(sym, []).append((int(ms), side, notl))
    print(f"  逐笔成交 {sum(len(v) for v in trades.values())} 条\n")
    # 前置覆盖检查：逐笔取不到 ⇒ 符号体系或时间窗对不上，直接报出来而不是静默 0
    if not any(trades.values()):
        print("  ✗ 逐笔成交为 0 条 ⇒ 符号体系或时间窗不匹配，无法继续")
        print("    （检查 `asterdex_trades.symbol` 写法是否与 `lane_ledger.symbol` 一致）")
        return 1

    import bisect

    def features(sym: str, ts, side: str) -> dict | None:
        arr = trades.get(sym)
        if not arr:
            return None
        ms = int(ts.timestamp() * 1000)
        lo = bisect.bisect_left(arr, (ms - W,))
        hi = bisect.bisect_left(arr, (ms,))          # **严格早于**成交时刻
        seg = arr[lo:hi]
        if not seg:
            return None
        notls = [t[2] for t in seg if t[2] > 0]
        if not notls:
            return None
        # 同向占比（相对我们的成交侧）
        same = sum(1 for t in seg if t[1] == side)
        return {
            "n": len(seg),
            "max_notl": max(notls),
            "mean_notl": sum(notls) / len(notls),
            "sum_notl": sum(notls),
            "same_ratio": same / len(seg),
        }

    rows = []
    miss = 0
    for f in fills:
        # ⚠️ `trades` 的键是**逐笔表的写法**（`ASTERUSDT`），
        # 而 `f["symbol"]` 是账本写法（`ASTER`）⇒ 必须映射，否则永远取空。
        # 首版就是这里漏了：逐笔明明取到 15 万条，覆盖率却是 0。
        ft = features(to_usdt(f["symbol"]), f["ts"], f["side"] or "")
        if ft:
            rows.append({**f, **ft})
        else:
            miss += 1
    print(f"  特征覆盖率 {len(rows)}/{len(fills)} "
          f"（{100.0*len(rows)/max(len(fills),1):.1f}%）   未命中 {miss}")
    if not rows:
        print("  ✗ 覆盖率 0 ⇒ 特征窗口内没有逐笔数据，无法继续")
        return 1
    base = sum(r["price_bp"] for r in rows) / len(rows)
    print(f"  基线 avg price_bp = {base:+.4f}bp\n")

    def quintiles(key: str) -> list:
        s = sorted(rows, key=lambda r: r[key])
        n = len(s)
        out = []
        for i in range(5):
            seg = s[i * n // 5:(i + 1) * n // 5]
            if seg:
                out.append((seg[0][key], seg[-1][key],
                            sum(x["price_bp"] for x in seg) / len(seg), len(seg)))
        return out

    print("  ── 各特征的分位表现 ──")
    results = {}
    for key, label in (("max_notl", "成交前最大单笔名义$"),
                       ("mean_notl", "成交前单笔均名义$"),
                       ("sum_notl", "成交前总名义$"),
                       ("n", "成交前笔数"),
                       ("same_ratio", "同向占比")):
        q = quintiles(key)
        if len(q) < 5:
            continue
        span = q[-1][2] - q[0][2]
        flips = sum(1 for i in range(len(q) - 1)
                    if (q[i + 1][2] - q[i][2]) * (q[1][2] - q[0][2]) < 0)
        results[key] = {"q": q, "span": span, "flips": flips}
        print(f"  {label}（Q5−Q1={span:+.3f}bp，翻转{flips}次）")
        for i, (lo, hi, avg, n) in enumerate(q, 1):
            print(f"    Q{i}  [{lo:>12,.3f},{hi:>12,.3f}]  n={n:>5}  "
                  f"price_bp={avg:+.3f}")
        print()

    # ── 跨窗口一致性检验（H207 的教训）──
    print("  ── 跨窗口一致性（把样本按时间切 4 段，看最强特征）──")
    best = max(results.items(), key=lambda kv: abs(kv[1]["span"])) if results else None
    if best:
        key, info = best
        print(f"  最强特征：{key}（全样本跨度 {info['span']:+.3f}bp）")
        s = sorted(rows, key=lambda r: r["ts"])
        n = len(s)
        print(f"    {'段':<20}{'腿数':>6}{'Q1 price':>11}{'Q5 price':>11}{'Q5−Q1':>10}")
        signs = []
        for i in range(4):
            seg = s[i * n // 4:(i + 1) * n // 4]
            if len(seg) < 80:
                continue
            seg = sorted(seg, key=lambda r: r[key])
            m = len(seg)
            q1 = seg[:m // 5]
            q5 = seg[-m // 5:]
            a1 = sum(x["price_bp"] for x in q1) / len(q1)
            a5 = sum(x["price_bp"] for x in q5) / len(q5)
            d = a5 - a1
            signs.append(d)
            t0s = seg[0]["ts"].strftime("%m-%d %H:%M")
            t1s = seg[-1]["ts"].strftime("%H:%M")
            print(f"    {t0s}→{t1s:<9}{m:>6}{a1:>+11.3f}{a5:>+11.3f}{d:>+10.3f}")
        if signs:
            same = all(x > 0 for x in signs) or all(x < 0 for x in signs)
            print(f"\n    ⇒ 4 段符号{'**一致** ⇒ 该特征跨窗口稳定，值得接线' if same else '**不一致** ⇒ 只是某个窗口的特性，不可用'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
