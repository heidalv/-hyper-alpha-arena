# -*- coding: utf-8 -*-
"""H151：直接从账本复算宽度扫描各档结果（不依赖扫描器的 stdout 缓冲）。

各档窗口（脚本会在每档重启+丢弃 120s 后才开始计时）：
    0.5 → 约 11:36 ~ 11:46
    1.3 → 约 11:47 ~ 11:57
    1.8 → 约 11:58 ~ 12:08
（窗口起点以 _env 变更与心跳生效时刻为准，这里给出可调的 --window 参数。）

用法：
    .venv\\Scripts\\python.exe scripts\\h151_sweep_recompute.py
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"


def dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    url = env.get("DATABASE_URL", "")
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(j, "")
    return url


def stats(t0, t1) -> dict:
    with psycopg.connect(dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT count(*), coalesce(sum(notional),0),
                       coalesce(sum(spread_bp*notional/1e4),0),
                       coalesce(sum(price_bp*notional/1e4),0),
                       coalesce(sum(fee_bp*notional/1e4),0),
                       coalesce(sum(net_bp*notional/1e4),0)
                FROM lane_ledger
                WHERE lane_id=%s AND event='fill' AND ts >= %s AND ts < %s
            """, (LANE, t0, t1))
            n, notl, sp, pr, fe, net = cur.fetchone()
            n, notl = int(n or 0), float(notl or 0)
            b = (1e4 / notl) if notl > 0 else 0.0
            mins = max((t1 - t0).total_seconds() / 60.0, 1e-9)
            return {"fills": n, "notional": notl, "fpm": n / mins,
                    "spread_bp_w": float(sp or 0) * b,
                    "price_bp_w": float(pr or 0) * b,
                    "fee_bp_w": float(fe or 0) * b,
                    "net_bp_w": float(net or 0) * b,
                    "net_usd": float(net or 0)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--t0", default="11:36", help="第一档窗口起点 HH:MM")
    ap.add_argument("--minutes", type=float, default=10.0)
    ap.add_argument("--ladder", default="0.5,1.3,1.8")
    a = ap.parse_args()

    base = datetime.strptime(f"2026-09-21 {a.t0}", "%Y-%m-%d %H:%M").astimezone()
    ladder = [float(x) for x in a.ladder.split(",")]
    step = timedelta(minutes=a.minutes + 1.0)   # 每档之间还有 ~1 分钟的重启/切换

    print("=" * 104)
    print("H151  宽度扫描复算（直接从 lane_ledger）")
    print("=" * 104)
    print(f"  起点 {base.strftime('%H:%M')}  每档 {a.minutes:.0f} 分钟  档位 {ladder}\n")
    print(f"  {'spread_mult':>12} {'窗口':<14} {'笔数':>6} {'笔/分':>7} {'名义$':>12} "
          f"{'价差bp':>9} {'行情bp':>9} {'费bp':>8} {'净bp':>9} {'净$':>9}")
    print("  " + "-" * 100)

    rows = []
    for i, v in enumerate(ladder):
        t0 = base + i * step
        t1 = t0 + timedelta(minutes=a.minutes)
        if t1 > datetime.now().astimezone():
            print(f"  {v:>12} {t0.strftime('%H:%M')}~{t1.strftime('%H:%M')}  （窗口未结束，跳过）")
            continue
        s = stats(t0, t1)
        s["mult"] = v
        rows.append(s)
        print(f"  {v:>12} {t0.strftime('%H:%M')}~{t1.strftime('%H:%M')} {s['fills']:>6} "
              f"{s['fpm']:>7.1f} {s['notional']:>12,.0f} {s['spread_bp_w']:>+9.4f} "
              f"{s['price_bp_w']:>+9.4f} {s['fee_bp_w']:>+8.4f} "
              f"{s['net_bp_w']:>+9.4f} {s['net_usd']:>+9.4f}")

    if rows:
        print("\n  读法：")
        print("    笔/分 随 spread_mult 上升而下降（挂得越远越难成交）——这是预期")
        print("    价差bp 应随宽度上升（捕获更多）；行情bp 是逆向选择（越宽应越好/越不负）")
        print("    判据看 **净bp**：它 = 价差 + 行情 + 费")
        best = max(rows, key=lambda r: r["net_bp_w"])
        print(f"\n  ⇒ 净bp 最高档：spread_mult=**{best['mult']}**（{best['net_bp_w']:+.4f}bp/笔）")
        print(f"     参照（整夜实测 0.9 档）：价差 +0.205~+0.413bp、行情 −0.548~−6.75bp")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
