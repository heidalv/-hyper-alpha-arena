# -*- coding: utf-8 -*-
"""口径与杠杆审计（Y1）：「先盈利后大亏」诊断的第一性核查。

疑点：`peak_pnl_pct` 是**保证金口径**（含杠杆）还是价格口径？
若含杠杆，则「峰值 → 最终」的对比此前混了口径（peak 用保证金、final 用价格），
「回吐」被系统性高估，且用户界面上看到的浮盈/亏损被杠杆放大。

本脚本对近 30 天 mid/long 成交逐笔：
  1. 打印 leverage / peak_pnl_pct / peak_unrealized_pnl / size / original_size；
  2. 用 1h K 线算**价格口径**的真实峰值（max high vs entry）；
  3. 对比三个量：peak_pnl_pct*100、peak_unrealized_pnl/(orig_size*entry)*100、
     价格口径峰值 → 判断 peak_pnl_pct 的口径；
  4. 统计 leverage 分布与「保证金口径 vs 价格口径」的比值。
"""
from __future__ import annotations

import json
import os
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text  # noqa: E402

ARENA_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
MARKET_URL = os.getenv("MARKET_DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
OUT = ROOT / "data" / "peak_unit_audit.json"


def load_klines(symbols):
    h1 = defaultdict(list)
    eng = create_engine(MARKET_URL)
    with eng.connect() as c:
        c.execute(text("set statement_timeout='900000'"))
        for exch in ("asterdex", "binance"):
            for s, ts, o, h, l, cl in c.execute(text("""
                select symbol, timestamp, open_price, high_price, low_price, close_price
                from crypto_klines where period='1h' and exchange=:ex and symbol = any(:syms)
                order by symbol, timestamp
            """), {"ex": exch, "syms": list(symbols)}).fetchall():
                h1[(exch, s)].append((int(ts), float(o), float(h), float(l), float(cl)))
    return h1


def pick(series, sym):
    for ex in ("asterdex", "binance"):
        v = series.get((ex, sym))
        if v and len(v) > 200:
            return v
    return None


def main() -> int:
    eng = create_engine(ARENA_URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        rows = [dict(r._mapping) for r in c.execute(text("""
            select id, symbol, side, timeframe_tier, entry_price, close_price, leverage,
                   size, original_size, margin, original_margin,
                   peak_pnl_pct, peak_unrealized_pnl, trough_pnl_pct, trough_unrealized_pnl,
                   unrealized_pnl, partial_realized_pnl, close_reason, opened_at, closed_at
            from paper_positions
            where timeframe_tier in ('mid','long') and status='closed'
              and closed_at >= now() - interval '30 days'
            order by opened_at
        """)).fetchall()]
        open_rows = [dict(r._mapping) for r in c.execute(text("""
            select id, symbol, side, timeframe_tier, entry_price, mark_price, leverage,
                   size, original_size, margin, peak_pnl_pct, peak_unrealized_pnl,
                   unrealized_pnl, opened_at
            from paper_positions
            where timeframe_tier in ('mid','long') and status='open'
            order by opened_at
        """)).fetchall()]

    h1 = load_klines({r["symbol"] for r in rows} | {r["symbol"] for r in open_rows})
    print(f"近 30 天已平仓 {len(rows)} 笔 | 当前未平仓 {len(open_rows)} 笔")

    levs = [float(r["leverage"] or 0) for r in rows if r["leverage"]]
    print(f"\n=== 杠杆分布（已平仓）===")
    if levs:
        from collections import Counter
        cnt = Counter(round(x, 2) for x in levs)
        for k in sorted(cnt):
            print(f"  {k:>5}x  n={cnt[k]:>4}")
        print(f"  均值={st.mean(levs):.2f}x 中位={st.median(levs):.2f}x")

    print(f"\n=== 逐笔口径对比（前 20 笔 + 全部统计）===")
    print(f"{'时间':<12}{'币':<8}{'方向':<6}{'杠杆':>5}{'peak_pct':>10}{'peak_usd':>10}"
          f"{'价格峰值':>10}{'比值':>7}{'最终价格':>10}")
    ratios = []
    for r in rows[:20]:
        entry = float(r["entry_price"] or 0)
        close = float(r["close_price"] or 0)
        if entry <= 0 or close <= 0:
            continue
        side = str(r["side"] or "long")
        sign = 1.0 if side == "long" else -1.0
        s = pick(h1, r["symbol"])
        price_peak = None
        if s:
            ts = int(r["opened_at"].timestamp())
            i = next((k for k, row in enumerate(s) if row[0] >= ts), None)
            # 必须确认序列真正覆盖入场时刻（否则 i=0 会拿"未来"的 K 线算峰值）
            if i is not None and i > 0 and abs(s[i][0] - ts) < 7200:
                # 峰值必须取「持仓窗口内」（入场 → 平仓），而非入场后 400 根
                hold_h = ((r["closed_at"] - r["opened_at"]).total_seconds() / 3600
                          if r.get("closed_at") else 0)
                end = min(i + int(hold_h) + 1, len(s) - 1)
                seg = s[i:end + 1]
                if side == "long":
                    price_peak = (max(x[2] for x in seg) - entry) / entry * 100
                else:
                    price_peak = (entry - min(x[3] for x in seg)) / entry * 100
        peak_pct = float(r["peak_pnl_pct"] or 0) * 100
        orig = float(r["original_size"] or r["size"] or 0)
        peak_usd = float(r["peak_unrealized_pnl"] or 0)
        peak_usd_pct = peak_usd / (orig * entry) * 100 if orig and entry else 0
        ratio = (peak_pct / price_peak) if (price_peak and abs(price_peak) > 1e-9) else None
        final_px = sign * (close - entry) / entry * 100
        if ratio is not None:
            ratios.append(ratio)
        print(f"{str(r['opened_at'])[5:16]:<12}{r['symbol']:<8}{side:<6}"
              f"{float(r['leverage'] or 0):>5.1f}{peak_pct:>10.3f}{peak_usd_pct:>10.3f}"
              f"{(price_peak if price_peak is not None else float('nan')):>10.3f}"
              f"{(ratio if ratio is not None else float('nan')):>7.2f}{final_px:>10.3f}")

    if ratios:
        print(f"\n  peak_pnl_pct / 价格峰值 比值：n={len(ratios)} 中位={st.median(ratios):.2f} "
              f"均值={st.mean(ratios):.2f} p10={sorted(ratios)[len(ratios)//10]:.2f} "
              f"p90={sorted(ratios)[min(len(ratios)-1, 9*len(ratios)//10)]:.2f}")
        print("  解读：比值≈1 → peak_pnl_pct 是价格口径；≈杠杆 → 保证金口径")

    print(f"\n=== 当前未平仓（口径对照）===")
    for r in open_rows:
        entry = float(r["entry_price"] or 0)
        mark = float(r["mark_price"] or 0)
        if entry <= 0 or mark <= 0:
            continue
        side = str(r["side"] or "long")
        sign = 1.0 if side == "long" else -1.0
        px = sign * (mark - entry) / entry * 100
        peak_pct = float(r["peak_pnl_pct"] or 0) * 100
        orig = float(r["original_size"] or r["size"] or 0)
        peak_usd = float(r["peak_unrealized_pnl"] or 0)
        peak_usd_pct = peak_usd / (orig * entry) * 100 if orig and entry else 0
        lev = float(r["leverage"] or 0)
        print(f"  {str(r['opened_at'])[5:16]} {r['symbol']:<8} {side:<6} 杠杆={lev:>4.1f}x "
              f"现价浮盈={px:>+6.2f}% 保证金浮盈={px*lev:>+7.2f}% | "
              f"peak_pct={peak_pct:>+6.2f}% peak_usd口径={peak_usd_pct:>+6.2f}%")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "n_closed": len(rows), "n_open": len(open_rows),
        "leverage_median": (st.median(levs) if levs else None),
        "ratio_median": (st.median(ratios) if ratios else None),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
