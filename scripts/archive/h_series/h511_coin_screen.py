"""h511：**候选币筛查**——用"贴 touch 挂单"的假设填单法，给全部候选币排 maker 可行性。

为什么需要：h510 证明 BNB 在"价差以内挂单"下**结构性不成立**
（半价差 0.55bp < 逆向漂移 0.83bp）。但那个结论是用**我们的成交**算的，
对**没在做市的候选币**无法用同一方法（没有成交）。
而选币器（h125/h356）目前按"实测每周期净额(bp)"排序 ⇒ 只能看到已在做市的币。

本脚本给出一个**不需要成交**的筛查口径（可覆盖全部候选币）：
  · 价格序列 = `market_trades_aggregated` 的 15s 桶 vwap（`exchange='asterdex'`）；
  · 假设在**贴 touch** 处挂买单：`bid_t = vwap_{t−1} − half_spread`（半价差取该币实测均值）；
  · **假设成交**：桶 t 的 `low_price ≤ bid_t`（价格确实打到了我们的价位）；
  · **markout@60s** = `(vwap_{t+4} − bid_t) / bid_t × 1e4`（正 = 填完朝我们要的方向走）；
  · 同时给出：填单代理率、活跃度（桶数/小时）、半价差、15s 噪声。

判读（maker 可行性的三档）：
  · `markout + half_spread > 0` 且显著 ⇒ **该币贴 touch 做市为正 EV** ⇒ 可纳入候选；
  · `markout + half_spread ≈ 0` ⇒ 边缘，要看队列与费率；
  · 显著为负 ⇒ **该币不适合贴 touch 做市**（无论挂多宽都要靠更深的队列）。

⚠️ 这是**代理口径**（vwap 代中价、低点触价代成交、无队列），用于**横向排序**，
不作为单币定论；入役前仍应走"预注册试跑 + 判定"。

用法：python scripts/h511_coin_screen.py [--hours 24] [--min-buckets 200]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import pathlib
import statistics as st
import sys

sys.stdout.reconfigure(encoding="utf-8")

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
OUT = ROOT / "research_l1" / "out" / "h511_coin_screen.json"


def read_env_dsn() -> str:
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
    return url


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--min-buckets", type=int, default=200)
    ap.add_argument("--top", type=int, default=25)
    a = ap.parse_args()
    dsn = read_env_dsn()
    with psycopg.connect(dsn, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json->'symbols' FROM lane_registry WHERE lane_id=%s",
                        (LANE,))
            r = cur.fetchone()
            live = [str(s) for s in (r[0] if r and isinstance(r[0], list) else [])]
    mk = dsn.replace("/alpha_arena", "/alpha_market")
    now_ms = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
    t0 = now_ms - int(a.hours * 3600_000)
    with psycopg.connect(mk, autocommit=True) as cm:
        with cm.cursor() as cur:
            cur.execute(
                "SELECT symbol, count(*) FROM market_trades_aggregated "
                "WHERE exchange='asterdex' AND timestamp > %s GROUP BY 1", (t0,))
            coins = [s for s, n in cur.fetchall() if int(n) >= a.min_buckets * 4]
            # 半价差（一次取回，按币聚合）
            cur.execute(
                "SELECT split_part(symbol,'USDT',1) AS s, "
                "COALESCE(AVG((ask_px-bid_px)/2.0/NULLIF((ask_px+bid_px)/2.0,0))*1e4,0)::float8 "
                "FROM asterdex_book_ticker WHERE event_ts_ms > %s "
                "AND bid_px>0 AND ask_px>bid_px GROUP BY 1", (t0,))
            hs = {s: float(v or 0) for s, v in cur.fetchall()}
            print(f"候选币 {len(coins)} 个（桶数 ≥{a.min_buckets*4}，窗口 {a.hours:.0f}h）")
            rows = []
            for s in coins:
                if s not in hs or hs[s] <= 0:
                    continue
                cur.execute(
                    "SELECT timestamp, low_price::float8, vwap::float8, "
                    "taker_buy_volume::float8, taker_sell_volume::float8 "
                    "FROM market_trades_aggregated WHERE exchange='asterdex' "
                    "AND symbol=%s AND timestamp > %s AND vwap > 0 "
                    "ORDER BY timestamp", (s, t0))
                b = cur.fetchall()
                if len(b) < a.min_buckets:
                    continue
                vw = [float(x[2]) for x in b]
                h = hs[s]
                mo = []
                noise = [abs(vw[i] - vw[i - 1]) / vw[i - 1] * 1e4
                         for i in range(1, len(vw)) if vw[i - 1] > 0]
                for i in range(1, len(b) - 4):
                    bid = vw[i - 1] - h * vw[i - 1] / 1e4
                    if float(b[i][1]) <= bid and bid > 0:
                        mo.append((vw[i + 4] - bid) / bid * 1e4)
                if len(mo) < 30:
                    continue
                m = st.mean(mo)
                sd = st.stdev(mo) if len(mo) > 1 else 0.0
                t = m / (sd / math.sqrt(len(mo))) if sd > 0 else 0.0
                hrs = (b[-1][0] - b[0][0]) / 1000.0 / 3600.0 if len(b) > 1 else 0.0
                rows.append({"sym": s, "buckets": len(b),
                             "buckets_per_h": round(len(b) / max(hrs, 1e-9), 1),
                             "half_spread_bp": round(h, 3),
                             "noise_15s_bp": round(st.mean(noise), 3) if noise else 0.0,
                             "fill_proxy_n": len(mo),
                             "fill_proxy_rate": round(len(mo) / max(len(b) - 5, 1), 4),
                             "markout60_bp": round(m, 3), "t": round(t, 2),
                             "edge_bp": round(m + h, 3),
                             "live": s in live})
    rows.sort(key=lambda x: -x["edge_bp"])
    print("=" * 120)
    print(f"{'币':10s} {'在役':>4s} {'桶/h':>7s} {'半价差':>7s} {'噪声15s':>8s} "
          f"{'填单率':>7s} {'markout60':>10s} {'t':>6s} {'捕获+markout':>12s}")
    for x in rows[:a.top]:
        print(f"{x['sym']:10s} {'✓' if x['live'] else '—':>4s} {x['buckets_per_h']:7.1f} "
              f"{x['half_spread_bp']:7.2f} {x['noise_15s_bp']:8.2f} "
              f"{100*x['fill_proxy_rate']:6.1f}% {x['markout60_bp']:+10.2f} "
              f"{x['t']:+6.2f} {x['edge_bp']:+12.2f}")
    pos = [x for x in rows if x["edge_bp"] > 0.3 and x["t"] > 1.0]
    live_bad = [x for x in rows if x["live"] and x["edge_bp"] < 0]
    print("=" * 120)
    print(f"正 EV（edge>+0.3bp 且 t>1）的候选：{len(pos)} 个"
          f" → {[x['sym'] for x in pos[:12]]}")
    print(f"**在役但 edge<0**：{[(x['sym'], x['edge_bp']) for x in live_bad]}")
    if live_bad:
        verdict = (f"在役币里 **{ [x['sym'] for x in live_bad] }** 的贴 touch 做市为负 EV；"
                   f"而正 EV 候选有 {[x['sym'] for x in pos[:6]]} ⇒ "
                   f"选币器应把「捕获 + markout」作为排序指标（当前只按已做市币的实测净额）")
    else:
        verdict = "在役币的贴 touch edge 均不为负 ⇒ 不需要换币；问题在出场侧"
    print("⇒ 裁决:", verdict)
    print("⚠️ 代理口径（vwap 代中价 / 低点触价代成交 / 无队列），用于**横向排序**；"
          "入役仍须预注册试跑 + 判定 + 自动回滚。")
    OUT.write_text(json.dumps({"hours": a.hours, "rows": rows,
                               "positive": [x["sym"] for x in pos],
                               "live_negative": [x["sym"] for x in live_bad],
                               "verdict": verdict}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
