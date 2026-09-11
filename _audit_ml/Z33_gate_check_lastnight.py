# -*- coding: utf-8 -*-
"""Z33：昨晚 5 笔亏损仓位——生产门会不会拦住？+ 敞口/相关性画像。

关键问题：long 车道豁免 learned 门（$MIDLONG_LONG_LEARNED_TIERS=mid），
那么这 4 笔 trend_follow 亏损单，若门覆盖 long 会不会被拦？
（注：down regime 是**所有 tier 都拦**的硬拦，不受豁免影响。）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

from deep_long_freshness import build_bar_features, learned_ok_prod, load_klines  # noqa: E402

from sqlalchemy import create_engine, text  # noqa: E402

ARENA = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
IDS = [4638, 4640, 4641, 4642, 4639, 4630, 4635]


def pick(series, sym, ts):
    """挑覆盖该时间戳的序列；都没有则退回「最新」序列。"""
    best = None
    for ex in ("asterdex", "binance"):
        v = series.get((ex, sym))
        if not v or len(v) <= 200:
            continue
        if v[0][0] <= ts <= v[-1][0] + 86400:
            return v
        if best is None or v[-1][0] > best[-1][0]:
            best = v
    return best


def main() -> int:
    eng = create_engine(ARENA)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        rows = [dict(r._mapping) for r in c.execute(text("""
            select id, symbol, timeframe_tier, trade_nature, entry_price, original_size, size,
                   leverage, margin, peak_pnl_pct, unrealized_pnl, partial_realized_pnl,
                   partial_fee_paid, close_reason, opened_at, closed_at, status
            from paper_positions where id = any(:ids) order by opened_at
        """), {"ids": IDS}).fetchall()]
    h1, d1 = load_klines({r["symbol"] for r in rows})
    cache = {}
    for sym in {r["symbol"] for r in rows}:
        s = pick(h1, sym, 1700000000)
        ds = pick(d1, sym, 1700000000)
        cache[sym] = (s, build_bar_features(s, ds)) if (
            s and ds and len(s) >= 300 and len(ds) >= 70) else None

    print("=== 各笔入场时的门特征与（反事实）判定 ===")
    print(f"{'id':>6}{'sym':<10}{'tier':<6}{'nature':<13}{'regime':<7}{'pos24':>7}"
          f"{'chg24':>8}{'learned门':>11}{'down硬拦':>9}  实际结果")
    for r in rows:
        cc = cache.get(r["symbol"])
        if not cc:
            print(f"{r['id']:>6} {r['symbol']} 无行情数据")
            continue
        s, (reg, pos, chg) = cc
        ts = int(r["opened_at"].timestamp())
        i = next((k for k, row in enumerate(s) if row[0] >= ts), None)
        if i is None or i == 0:
            continue
        ok = learned_ok_prod(reg[i], pos[i], chg[i])
        down = (reg[i] == "down")
        u = (float(r["unrealized_pnl"] or 0) + float(r["partial_realized_pnl"] or 0)
             - float(r["partial_fee_paid"] or 0))
        print(f"{r['id']:>6}{str(r['symbol']):<10}{str(r['timeframe_tier']):<6}"
              f"{str(r['trade_nature'] or ''):<13}{reg[i] or '-':<7}{pos[i]:>7.1f}"
              f"{chg[i]:>+8.2f}{('放行' if ok else '拦'):>11}"
              f"{('是' if down else '否'):>9}  {u:>+8.2f}  {str(r['close_reason'] or '')[:22]}")

    # 敞口画像
    print("\n=== 敞口画像（窗口内并发）===")
    tot_notional = sum(float(r["original_size"] or 0) * float(r["entry_price"] or 0)
                       for r in rows if r["status"] == "open")
    tot_margin = sum(float(r["margin"] or 0) for r in rows if r["status"] == "open")
    print(f"  当前 open：名义合计={tot_notional:.0f} 保证金合计={tot_margin:.0f}")
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        for r in c.execute(text("""
            select count(*) n, sum(original_size*entry_price) ntl, sum(margin) mgn
            from paper_positions where status='open'
        """)).fetchall():
            print(f"  全库 open：{r[0]} 笔 名义={float(r[1] or 0):.0f} 保证金={float(r[2] or 0):.0f}")
        print("\n  9/9 21:00 时刻的持仓（按开/平时间倒推）：")
        for r in c.execute(text("""
            select id, symbol, timeframe_tier, trade_nature, original_size*entry_price ntl
            from paper_positions
            where opened_at <= '2026-09-09 21:00:00+08'
              and (closed_at is null or closed_at > '2026-09-09 21:00:00+08')
            order by opened_at
        """)).fetchall():
            print(f"    #{r[0]} {str(r[1]):<9}{str(r[2]):<6}{str(r[3] or ''):<13}名义={float(r[4] or 0):.0f}")
        for r in c.execute(text("""
            select count(*), sum(original_size*entry_price) from paper_positions
            where opened_at <= '2026-09-09 21:00:00+08'
              and (closed_at is null or closed_at > '2026-09-09 21:00:00+08')
        """)).fetchall():
            print(f"    合计 {r[0]} 笔，名义 {float(r[1] or 0):.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
