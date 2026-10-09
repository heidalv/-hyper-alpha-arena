"""成交真实性审计：我们的成交价在**真实逐笔**里找得到吗？

背景：`runner.py:1756-1762` 自己写明，模拟盘的成交判定只有**桶级**数据
（`low/high/taker_buy/taker_sell`），**无法判断桶内是否真有成交落在我们的价位**，
`px_exact_hit` 一律留 `None`；代码注释提到实测 **47.7% 的成交价在真实市场里
找不到对应逐笔**。这直接决定所有"盈利"结论是不是乐观上界。

本工具用真实逐笔表 `alpha_market.asterdex_trades` 做外验：
对每条我方成交腿，在其成交时刻附近一个窄窗内，
查该标的是否存在**价格穿过我们成交价**的真实逐笔。

判据：
  · 找到 ⇒ `verified`（模拟成交在真实市场里可解释）
  · 找不到 ⇒ `phantom`（幽灵成交，盈利不可信）
"""
from __future__ import annotations

import argparse
import io
import sys
from collections import Counter

import psycopg

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
CORE = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"
MKT = "postgresql://laobao:alpha_pass@localhost:5432/alpha_market"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=60)
    ap.add_argument("--window-ms", type=int, default=60000,
                    help="成交时刻前后各取多少毫秒的逐笔（默认 ±60s）")
    ap.add_argument("--tol-bp", type=float, default=2.0,
                    help="价格容差（bp）；逐笔价与成交价之差在此内算命中")
    args = ap.parse_args()

    with psycopg.connect(CORE, autocommit=True) as c:
        cur = c.cursor()
        cur.execute(f"""
            SELECT ts, symbol,
                   coalesce((meta_json->>'fill_px')::float8, 0),
                   coalesce(meta_json->>'qty','0')::float8,
                   coalesce((meta_json->>'fill_px')::float8,0)
                     * coalesce(meta_json->>'qty','0')::float8,
                   coalesce(notional,0), coalesce(net_bp,0)
            FROM lane_ledger
            WHERE lane_id='mm_asterdex' AND event='fill'
              AND ts > now() - interval '{int(args.minutes)} minutes'
              AND meta_json ? 'fill_px'
              AND coalesce((meta_json->>'fill_px')::float8,0) > 0
            ORDER BY ts
        """)
        legs = cur.fetchall()

    print("=" * 92)
    print(f"成交真实性审计 窗口={args.minutes}分钟  腿数={len(legs)}  "
          f"逐笔窗=±{args.window_ms}ms  容差={args.tol_bp}bp")
    print("=" * 92)
    if not legs:
        print("  无样本")
        return 0

    verdicts = Counter()
    detail = []
    with psycopg.connect(MKT, autocommit=True) as m:
        mc = m.cursor()
        for ts, sym, fpx, qty, notl, notional, net_bp in legs:
            ts_ms = int(ts.timestamp() * 1000)
            lo, hi = ts_ms - args.window_ms, ts_ms + args.window_ms
            # ⚠️ 逐笔表的 symbol 带 USDT 后缀（`BTCUSDT`），
            # 而 lane_ledger 存的是裸符号（`BTC`）—— 首版没做映射 ⇒ 100% 查不到。
            mkt_sym = str(sym).upper()
            if not mkt_sym.endswith("USDT"):
                mkt_sym = mkt_sym + "USDT"
            mc.execute("""
                SELECT count(*), min(price), max(price)
                FROM asterdex_trades
                WHERE symbol = %s AND trade_ts_ms BETWEEN %s AND %s
            """, (mkt_sym, lo, hi))
            n, pmin, pmax = mc.fetchone()
            if not n:
                verdicts["no_market_data"] += 1
                detail.append((ts, sym, fpx, "no_market_data", 0))
                continue
            pmin, pmax = float(pmin), float(pmax)
            tol = fpx * args.tol_bp / 1e4
            if pmin - tol <= fpx <= pmax + tol:
                verdicts["verified"] += 1
                v = "verified"
            else:
                verdicts["phantom"] += 1
                v = "phantom"
            detail.append((ts, sym, fpx, v, n))

    print()
    print(f"{'判定':<18}{'腿数':>6}{'占比':>9}")
    total = sum(verdicts.values())
    for k, v in verdicts.most_common():
        print(f"{k:<18}{v:>6}{v / max(total,1) * 100:>8.1f}%")
    print("-" * 40)
    print(f"{'合计':<18}{total:>6}")

    print()
    print("逐腿明细（前 30）：")
    print(f"{'ts':<10}{'sym':<10}{'fill_px':>14}{'判定':<18}{'窗内逐笔数':>12}")
    for ts, sym, fpx, v, n in detail[:30]:
        print(f"{str(ts)[11:19]:<10}{str(sym)[:9]:<10}{fpx:>14.6f}{v:<18}{n:>12}")

    ph = verdicts.get("phantom", 0)
    print()
    if total:
        print(f"  phantom 占比 = {ph / total * 100:.1f}%")
        print("  读法：phantom 高 ⇒ 模拟成交在真实市场无法解释 ⇒ **盈利结论不可信**")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
