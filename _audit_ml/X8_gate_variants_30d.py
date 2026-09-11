# -*- coding: utf-8 -*-
"""门变体对照表（X8）：近 30 天实际 P&L 口径。

X7 发现：round-17 放宽 chop 后，放行集实际 P&L 从 +0.20%(n=44) 降到 -0.11%(n=80)。
本脚本把各候选门变体套在近 30 天 184 笔上，逐条报：
  n / 实际净均值 / 72h 前向均值 / 峰值均值 / 胜率 / 合计
既看「实际兑现」（含出场），也看「72h 前向」（纯入场质量），据此定门。

变体：
  G_none      : 不拦（基线）
  G_down      : 仅拦 down（regime_only）
  G_r16       : round-16（up chg≥3；chop pos≥60 且 chg≥2）
  G_r17       : round-17（up chg∈[3,6)；chop 无条件）
  G_r17b      : up chg∈[3,6)；chop pos≥60 且 chg≥2（=r16 加 spike 上界）
  G_r17c      : up chg∈[3,6)；chop chg≥2（去掉 pos 要求）
  G_r17d      : up chg∈[3,6)；chop pos<60（只做回调低吸）
  G_r17e      : up chg∈[3,6)；chop 无条件；额外要求 pos<60（全场低吸）
  G_up_only   : up chg∈[3,6)；chop 不拦；down 拦（=r17）
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

from deep_long_freshness import (  # noqa: E402
    load_klines, pick, build_bar_features, cost_pct,
)

from sqlalchemy import create_engine, text  # noqa: E402

ARENA_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_arena")
OUT = ROOT / "data" / "gate_variants_30d.json"


def load_recent(days=30):
    eng = create_engine(ARENA_URL)
    with eng.connect() as c:
        c.execute(text("set app.is_admin='on'"))
        return [dict(r._mapping) for r in c.execute(text(f"""
            select id, symbol, side, timeframe_tier, entry_price, close_price,
                   peak_pnl_pct, close_reason, opened_at, closed_at
            from paper_positions
            where timeframe_tier in ('mid','long') and status='closed'
              and closed_at >= now() - interval '{int(days)} days'
            order by opened_at
        """)).fetchall()]


VARIANTS = {
    "G_none 不拦": lambda reg, pos, chg: True,
    "G_down 仅拦down": lambda reg, pos, chg: reg != "down",
    "G_r16": lambda reg, pos, chg: (reg == "up" and chg >= 3) or (reg == "chop" and pos >= 60 and chg >= 2),
    "G_r17 up[3,6)+chop无条件": lambda reg, pos, chg: (reg == "up" and 3 <= chg < 6) or reg == "chop",
    "G_r17b up[3,6)+chop pos60chg2": lambda reg, pos, chg: (reg == "up" and 3 <= chg < 6) or (reg == "chop" and pos >= 60 and chg >= 2),
    "G_r17c up[3,6)+chop chg>=2": lambda reg, pos, chg: (reg == "up" and 3 <= chg < 6) or (reg == "chop" and chg >= 2),
    "G_r17d up[3,6)+chop pos<60": lambda reg, pos, chg: (reg == "up" and 3 <= chg < 6) or (reg == "chop" and pos < 60),
    "G_r17e up[3,6)+chop无条件+pos<60": lambda reg, pos, chg: reg != "down" and pos < 60 and ((reg == "up" and 3 <= chg < 6) or reg == "chop"),
}


def main() -> int:
    rows = load_recent(30)
    h1, d1 = load_klines({r["symbol"] for r in rows})
    feats = {}
    for sym in {r["symbol"] for r in rows}:
        s = pick(h1, sym)
        ds = pick(d1, sym)
        if s is None or ds is None or len(s) < 300 or len(ds) < 70:
            continue
        feats[sym] = (s, build_bar_features(s, ds))

    recs = []
    for r in rows:
        sym = r["symbol"]
        if sym not in feats:
            continue
        s, (reg_arr, pos_arr, chg_arr) = feats[sym]
        entry = float(r["entry_price"] or 0)
        close = float(r["close_price"] or 0)
        if entry <= 0 or close <= 0:
            continue
        ts = int(r["opened_at"].timestamp())
        i = next((k for k, row in enumerate(s) if row[0] >= ts), None)
        if i is None or i < 25:
            continue
        side = str(r["side"] or "long")
        hold_h = (r["closed_at"] - r["opened_at"]).total_seconds() / 3600 if r["closed_at"] else 0
        raw = (close - entry) / entry * 100 if side == "long" else (entry - close) / entry * 100
        k72 = min(i + 72, len(s) - 1)
        fwd72 = ((s[k72][4] - entry) / entry * 100 if side == "long"
                 else (entry - s[k72][4]) / entry * 100) - cost_pct(72)
        recs.append({
            "regime": reg_arr[i], "pos": pos_arr[i], "chg": chg_arr[i],
            "actual": raw - cost_pct(hold_h), "fwd72": fwd72,
            "peak": float(r["peak_pnl_pct"] or 0) * 100, "side": side,
        })

    print(f"近 30 天可评估: {len(recs)} 笔")
    print(f"\n{'变体':<38}{'n':>4}{'实际均值':>10}{'72h前向':>9}{'峰值':>7}{'胜率':>7}{'合计':>9}")
    out = {}
    for name, fn in VARIANTS.items():
        sub = [x for x in recs if fn(x["regime"], x["pos"], x["chg"])]
        if not sub:
            continue
        n = len(sub)
        a = sum(x["actual"] for x in sub) / n
        f = sum(x["fwd72"] for x in sub) / n
        pk = sum(x["peak"] for x in sub) / n
        w = sum(1 for x in sub if x["actual"] > 0) / n
        out[name] = {"n": n, "actual": round(a, 3), "fwd72": round(f, 3),
                     "peak": round(pk, 3), "win": round(w, 3),
                     "sum": round(sum(x["actual"] for x in sub), 1)}
        print(f"{name:<38}{n:>4}{a:>+10.3f}{f:>+9.3f}{pk:>+7.2f}{w:>7.3f}{out[name]['sum']:>+9.1f}")

    print("\n=== 只多头 ===")
    longs = [x for x in recs if x["side"] == "long"]
    print(f"{'变体':<38}{'n':>4}{'实际均值':>10}{'72h前向':>9}{'峰值':>7}{'胜率':>7}{'合计':>9}")
    for name, fn in VARIANTS.items():
        sub = [x for x in longs if fn(x["regime"], x["pos"], x["chg"])]
        if not sub:
            continue
        n = len(sub)
        a = sum(x["actual"] for x in sub) / n
        f = sum(x["fwd72"] for x in sub) / n
        pk = sum(x["peak"] for x in sub) / n
        w = sum(1 for x in sub if x["actual"] > 0) / n
        out[f"{name} (long)"] = {"n": n, "actual": round(a, 3), "fwd72": round(f, 3),
                                 "peak": round(pk, 3), "win": round(w, 3),
                                 "sum": round(sum(x["actual"] for x in sub), 1)}
        print(f"{name:<38}{n:>4}{a:>+10.3f}{f:>+9.3f}{pk:>+7.2f}{w:>7.3f}{out[f'{name} (long)']['sum']:>+9.1f}")

    print("\n=== 按 regime 分组（近 30 天实际）===")
    for reg in ("up", "chop", "down"):
        sub = [x for x in recs if x["regime"] == reg]
        if not sub:
            continue
        n = len(sub)
        print(f"  {reg:<5} n={n:>3} 实际={sum(x['actual'] for x in sub)/n:>+7.2f}% "
              f"72h前向={sum(x['fwd72'] for x in sub)/n:>+7.2f}% "
              f"峰值={sum(x['peak'] for x in sub)/n:>+6.2f}%")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "variants": out,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
