# -*- coding: utf-8 -*-
"""[2026-09-24 第17轮] 「4h 震荡不入场」闸门的 **walk-forward 检验**（中线）。

背景：chop_regime_audit 显示中线在 4h 震荡环境入场 13 笔合计 −$69.93（均 −$5.38/笔），
是其最差桶；而 trend_up 桶 +$67.73。候选闸门：入场时 4h |mom24|<2% 且 |dist50|<1.5% → 不开仓。

协议（与第 8 轮同）：116 笔按 opened_at 切 3 折；只用前 k 折判断"是否采用该闸门"，
在第 k+1 折看样本外表现；同时给出每折的桶内/桶外收益，便于看稳定性。
只读。
"""
from __future__ import annotations

import datetime as dt
import io
import sys

import psycopg

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ARENA = "postgresql://laobao:alpha_pass@localhost:5432/alpha_arena"
MARKET = "postgresql://laobao:alpha_pass@localhost:5432/alpha_market"
CST = dt.timezone(dt.timedelta(hours=8))


def ema(vals, period):
    k = 2.0 / (period + 1.0)
    e = vals[0]
    out = []
    for v in vals:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def main() -> int:
    with psycopg.connect(ARENA, autocommit=True) as ac:
        cur = ac.cursor()
        cur.execute("SET app.is_admin='on'")
        cur.execute(
            """select id, symbol, entry_price, opened_at, unrealized_pnl,
                      coalesce(partial_realized_pnl,0) pr, peak_pnl_pct
               from paper_positions
               where account_id=14 and timeframe_tier='mid' and status in ('closed','liquidated')
                 and closed_at > timestamp '2026-09-15 00:00:00'
               order by opened_at""")
        cols = [d[0] for d in cur.description]
        trades = [dict(zip(cols, r)) for r in cur.fetchall()]

    with psycopg.connect(MARKET, autocommit=True) as mc:
        cur = mc.cursor()
        for sym in sorted({t["symbol"] for t in trades}):
            cur.execute(
                """select timestamp, close_price from crypto_klines
                   where symbol=%s and exchange='binance' and period='4h' and environment='mainnet'
                   order by timestamp""", (sym,))
            rows = [(int(r[0]), float(r[1])) for r in cur.fetchall()]
            if len(rows) < 60:
                continue
            ts = [r[0] for r in rows]
            closes = [r[1] for r in rows]
            e50 = ema(closes, 50)
            for t in trades:
                if t["symbol"] != sym:
                    continue
                o = int(t["opened_at"].replace(tzinfo=CST).timestamp())
                i = max((k for k, x in enumerate(ts) if x <= o), default=None)
                if i is None or i < 6:
                    t["chop"] = None
                    continue
                mom24 = (closes[i] / closes[i - 6] - 1.0) * 100.0
                dist50 = (closes[i] / e50[i] - 1.0) * 100.0
                t["chop"] = (abs(mom24) < 2.0 and abs(dist50) < 1.5)

    usable = [t for t in trades if t.get("chop") is not None]
    for t in usable:
        t["pnl"] = float(t["unrealized_pnl"] or 0) + float(t["pr"] or 0)
    n = len(usable)
    size = n // 3
    folds = [usable[i * size:(i + 1) * size] for i in range(2)] + [usable[2 * size:]]
    print("样本 %d 笔 → 3 折（%d/%d/%d）" % (n, len(folds[0]), len(folds[1]), len(folds[2])))
    for i, f in enumerate(folds, 1):
        chop = [t for t in f if t["chop"]]
        oth = [t for t in f if not t["chop"]]
        print("  折%d: n=%d 震荡 %d 笔 %+.2f（均 %+.2f） | 非震荡 %d 笔 %+.2f（均 %+.2f）"
              % (i, len(f), len(chop), sum(t["pnl"] for t in chop),
                 (sum(t["pnl"] for t in chop) / len(chop)) if chop else 0,
                 len(oth), sum(t["pnl"] for t in oth),
                 (sum(t["pnl"] for t in oth) / len(oth)) if oth else 0))
    print("\n== walk-forward：前 k 折决定是否启用闸门 → 第 k+1 折样本外 ==")
    for k in (1, 2):
        train = [t for f in folds[:k] for t in f]
        test = folds[k]
        t_chop = [t for t in train if t["chop"]]
        train_gate_good = bool(t_chop) and (sum(t["pnl"] for t in t_chop) / len(t_chop)) < 0
        base = sum(t["pnl"] for t in test)
        kept = [t for t in test if not t["chop"]] if train_gate_good else test
        print("  train=折1..%d：震荡桶均 %+.2f（%d 笔）⇒ 闸门%s"
              % (k, (sum(t["pnl"] for t in t_chop) / len(t_chop)) if t_chop else 0, len(t_chop),
                 "启用" if train_gate_good else "不启用"))
        print("     样本外（折%d）：基线 %+.2f（%d 笔）→ 启用后 %+.2f（%d 笔），差 %+.2f"
              % (k + 1, base, len(test), sum(t["pnl"] for t in kept), len(kept),
                 sum(t["pnl"] for t in kept) - base))
    print("\n== 全样本参考 ==")
    chop = [t for t in usable if t["chop"]]
    oth = [t for t in usable if not t["chop"]]
    print("  震荡 %d 笔 %+.2f（均 %+.2f）| 非震荡 %d 笔 %+.2f（均 %+.2f）"
          % (len(chop), sum(t["pnl"] for t in chop), sum(t["pnl"] for t in chop) / max(1, len(chop)),
             len(oth), sum(t["pnl"] for t in oth), sum(t["pnl"] for t in oth) / max(1, len(oth))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
