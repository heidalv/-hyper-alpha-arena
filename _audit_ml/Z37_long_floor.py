# -*- coding: utf-8 -*-
"""Z37：long 车道「动量/位置地板」验证（针对昨晚 4 笔 long 亏损单）。

现状：long 车道豁免 learned 门（§31.2 依据：全量封堵会拦掉 +$96.73 盈利单）。
但昨晚 4 笔 long 全是 `up 且 chg24<3%` 或 `chop 且 pos24<60`。
本脚本测「部分地板」——只拦其中最差的子集，看能否只砍尾不砍右尾：

  F1: up 且 chg24 < 0        （下跌中做多）
  F2: chop 且 pos24 < 20     （区间最下沿低吸）
  F3: F1 ∪ F2
  F4: 全量 learned 门（对照，已知有害）
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

URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")


def pick(series, sym, ts):
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
    eng = create_engine(URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        rows = [dict(r._mapping) for r in c.execute(text("""
            select id, symbol, timeframe_tier, trade_nature, entry_price, original_size, size,
                   peak_pnl_pct, unrealized_pnl, partial_realized_pnl, partial_fee_paid,
                   close_reason, opened_at, closed_at
            from paper_positions
            where timeframe_tier='long' and status='closed'
              and closed_at >= now() - interval '75 days'
            order by opened_at
        """)).fetchall()]
    h1, d1 = load_klines({r["symbol"] for r in rows})
    cache = {}
    recs = []
    for p in rows:
        sym = p["symbol"]
        ts = int(p["opened_at"].timestamp())
        if sym not in cache:
            s = pick(h1, sym, ts)
            ds = pick(d1, sym, ts)
            cache[sym] = (s, build_bar_features(s, ds)) if (
                s and ds and len(s) >= 300 and len(ds) >= 70) else None
        cc = cache.get(sym)
        if not cc:
            continue
        s, (reg, pos, chg) = cc
        i = next((k for k, row in enumerate(s) if row[0] >= ts), None)
        if i is None or i == 0:
            continue
        sz0 = float(p["original_size"] or p["size"] or 0)
        entry = float(p["entry_price"] or 0)
        if sz0 <= 0 or entry <= 0:
            continue
        recs.append({
            "id": p["id"], "symbol": sym, "notional0": sz0 * entry,
            "usd": (float(p["unrealized_pnl"] or 0) + float(p["partial_realized_pnl"] or 0)
                    - float(p["partial_fee_paid"] or 0)),
            "peak": float(p["peak_pnl_pct"] or 0) * 100,
            "reg": reg[i], "pos": pos[i], "chg": chg[i],
            "opened": str(p["opened_at"])[:19],
            "reason": str(p["close_reason"] or "")[:24],
        })

    def agg(sub):
        if not sub:
            return None
        pcts = [r["usd"] / r["notional0"] * 100 for r in sub]
        pat = sum(1 for r in sub if r["peak"] >= 0.5 and r["usd"] < 0)
        return {"n": len(sub), "usd": sum(r["usd"] for r in sub),
                "mean": sum(pcts) / len(pcts),
                "win": sum(1 for x in pcts if x > 0) / len(pcts),
                "pat": pat / len(sub), "le2": sum(1 for x in pcts if x <= -2),
                "worst": min(r["usd"] for r in sub)}

    base = agg(recs)
    print(f"long 层近 75 天 n={base['n']} USD={base['usd']:+.2f} 均值={base['mean']:+.3f}% "
          f"胜率={base['win']:.3f} 模式率={base['pat']:.3f} ≤-2%={base['le2']} "
          f"最差={base['worst']:+.2f}")

    rules = {
        "F1 up 且 chg24<0": lambda r: not (r["reg"] == "up" and r["chg"] < 0),
        "F2 chop 且 pos24<20": lambda r: not (r["reg"] == "chop" and r["pos"] < 20),
        "F3 F1∪F2": lambda r: not ((r["reg"] == "up" and r["chg"] < 0)
                                   or (r["reg"] == "chop" and r["pos"] < 20)),
        "F4 全量 learned 门（对照）": lambda r: learned_ok_prod(r["reg"], r["pos"], r["chg"]),
        "F5 up 且 chg24<+1": lambda r: not (r["reg"] == "up" and r["chg"] < 1.0),
    }
    print(f"\n{'规则':<26}{'保留n':>6}{'保留USD':>10}{'均值%':>9}{'胜率':>7}{'模式率':>8}"
          f"{'≤-2%':>7}{'被拦n':>7}{'被拦USD':>10}")
    for name, keepfn in rules.items():
        keep = [r for r in recs if keepfn(r)]
        skip = [r for r in recs if not keepfn(r)]
        a = agg(keep)
        s = agg(skip)
        print(f"{name:<26}{a['n']:>6}{a['usd']:>+10.2f}{a['mean']:>+9.3f}{a['win']:>7.3f}"
              f"{a['pat']:>8.3f}{a['le2']:>7}{s['n'] if s else 0:>7}"
              f"{s['usd'] if s else 0:>+10.2f}")

    print("\n=== F1 / F2 分别拦掉的是哪些单 ===")
    for name, fn in (("F1", rules["F1 up 且 chg24<0"]), ("F2", rules["F2 chop 且 pos24<20"])):
        skip = [r for r in recs if not fn(r)]
        print(f"  {name}:")
        for r in sorted(skip, key=lambda x: x["usd"]):
            print(f"    #{r['id']:<5}{r['symbol']:<9}{r['reg']:<6}pos={r['pos']:>5.1f} "
                  f"chg={r['chg']:>+6.2f} USD={r['usd']:>+8.2f} 峰值={r['peak']:>5.2f}% "
                  f"{r['opened'][:16]} {r['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
