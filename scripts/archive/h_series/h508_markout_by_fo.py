"""h508：**在我们自己的成交上**复核 h483 的核心结论（confirm 饱和态才有边际）。

h483 的结论（"只有订单流饱和态有边际：`fo=OFI×d` ≥0.99 段 +3.03bp，0.4~0.99 段 ≈0 或为负"）
来自 **h447 的分钟采样**（不是我们的成交），而 ③ 正要把 `ofi_confirm_threshold`
0.5→0.9 来"只做饱和态"。在部署前，用**我们自己的入场腿**独立复核一遍：

口径（每笔 OPEN/ADD 入场腿）：
  · `d = −sign(r60)`（60s 中价趋势的逆向）——与线上方向规则同式；
  · `OFI` = 挂单前那个 15s 桶的 `(taker_buy_volume − taker_sell_volume)/(两者之和)`，
    取自 `market_trades_aggregated`（`exchange='asterdex'`）；
  · `fo = OFI × d`；`r300` 同样取自真实逐笔（按 h488 的 45s 延迟校正）；
  · **markout**：填单价 → `t+60s` 中价的有向变化（bp，正=填完就朝我们要的方向走）
    —— 这是"填单质量"的经典度量，与"已实现 net_bp"互补（后者含出场成本）。

产出：按 `fo` 分档的 腿数 / markout@60s / 已实现 net_bp / |r300| 中位。
若"fo 越饱和越好"在我们自己的成交上成立 ⇒ ③ 有据；若不成立 ⇒ ③ 可能重演
"从别人的样本里学到、在自己的成交上失效"。

用法：python scripts/h508_markout_by_fo.py [--hours 48]
"""
from __future__ import annotations

import argparse
import collections
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
OUT = ROOT / "research_l1" / "out" / "h508_markout_fo.json"
LAG_S = 45
FO_BINS = ((0.0, 0.5), (0.5, 0.8), (0.8, 0.95), (0.95, 1.01))


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


def px_at(cur, sym, t_ms, tol_ms=20000):
    cur.execute(
        "SELECT (ARRAY_AGG((bid_px+ask_px)/2 ORDER BY event_ts_ms DESC))[1]::float8 "
        "FROM asterdex_book_ticker WHERE symbol=%s "
        "AND event_ts_ms > %s AND event_ts_ms <= %s AND bid_px>0 AND ask_px>bid_px",
        (sym + "USDT", t_ms - tol_ms, t_ms + tol_ms))
    r = cur.fetchone()
    return float(r[0]) if r and r[0] else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=48.0)
    a = ap.parse_args()
    with psycopg.connect(read_env_dsn(), autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT ts, symbol, COALESCE(meta_json->>'side',''), "
                "ABS(COALESCE((meta_json->>'qty')::float8,0))::float8, "
                "COALESCE(meta_json->>'exit_path',''), COALESCE(net_bp,0)::float8, "
                "COALESCE((meta_json->>'fill_px')::float8,0)::float8, "
                "COALESCE(notional,0)::float8 "
                "FROM lane_ledger WHERE lane_id=%s "
                "AND ts > now() - make_interval(hours => %s::int) ORDER BY symbol, ts",
                (LANE, int(a.hours)))
            rows = cur.fetchall()
    by_sym = collections.OrderedDict()
    for r in rows:
        by_sym.setdefault(r[1], []).append(r)
    ents = []
    for sym, rs in by_sym.items():
        cum = 0.0
        for ts, _s, side, qty, ep, net, px, noti in rs:
            signed = abs(qty) if str(side).lower() == "buy" else -abs(qty)
            before = cum
            cum += signed
            if ep == "" and (abs(before) < 1e-9 or (before > 0) == (signed > 0)) and px > 0:
                ents.append({"sym": sym, "ts": ts, "side": str(side).lower(),
                             "px": float(px), "net": float(net or 0.0),
                             "noti": float(noti or 0.0)})
    print(f"近 {a.hours:.0f}h 入场腿 {len(ents)}")
    mk = read_env_dsn().replace("/alpha_arena", "/alpha_market")
    grids = collections.defaultdict(lambda: {"n": 0, "mo": [], "net": [], "r300": [],
                                             "usd": 0.0})
    miss = collections.Counter()
    with psycopg.connect(mk, autocommit=True) as cm:
        with cm.cursor() as cur:
            for e in ents:
                t_lag = e["ts"] - dt.timedelta(seconds=LAG_S)
                t_ms = int(t_lag.timestamp() * 1000)
                # r60 / r300
                cur.execute(
                    "SELECT (ARRAY_AGG(price ORDER BY event_ts_ms ASC))[1]::float8, "
                    "(ARRAY_AGG(price ORDER BY event_ts_ms DESC))[1]::float8, count(*) "
                    "FROM asterdex_trades WHERE symbol=%s AND event_ts_ms > %s "
                    "AND event_ts_ms <= %s",
                    (e["sym"] + "USDT", t_ms - 60000, t_ms))
                r = cur.fetchone()
                if not r or not r[0] or int(r[2] or 0) < 2:
                    miss["r60"] += 1
                    continue
                r60 = (float(r[1]) - float(r[0])) / float(r[0]) * 1e4
                cur.execute(
                    "SELECT (ARRAY_AGG(price ORDER BY event_ts_ms ASC))[1]::float8, "
                    "(ARRAY_AGG(price ORDER BY event_ts_ms DESC))[1]::float8 "
                    "FROM asterdex_trades WHERE symbol=%s AND event_ts_ms > %s "
                    "AND event_ts_ms <= %s",
                    (e["sym"] + "USDT", t_ms - 300000, t_ms))
                r3 = cur.fetchone()
                r300 = ((float(r3[1]) - float(r3[0])) / float(r3[0]) * 1e4
                        if r3 and r3[0] and r3[1] else None)
                # OFI：前一个 15s 桶（聚合表）
                cur.execute(
                    "SELECT COALESCE(sum(taker_buy_volume),0)::float8, "
                    "COALESCE(sum(taker_sell_volume),0)::float8 "
                    "FROM market_trades_aggregated WHERE exchange='asterdex' "
                    "AND symbol=%s AND timestamp > %s AND timestamp <= %s",
                    (e["sym"], t_ms - 30000, t_ms))
                of = cur.fetchone()
                bv, sv = float(of[0] or 0), float(of[1] or 0)
                if bv + sv <= 0:
                    miss["ofi"] += 1
                    continue
                ofi = (bv - sv) / (bv + sv)
                d = -1.0 if r60 > 0 else (1.0 if r60 < 0 else 0.0)
                fo = ofi * d
                # markout@60s
                m1 = px_at(cur, e["sym"], t_ms + 60000)
                if m1 is None:
                    miss["mid60"] += 1
                    continue
                sgn = 1.0 if e["side"] == "buy" else -1.0
                mo = sgn * (m1 - e["px"]) / e["px"] * 1e4
                e.update({"fo": fo, "r300": r300, "markout": mo})
                b = next((f"{lo}-{hi}" for lo, hi in FO_BINS if lo <= fo < hi), None)
                if b is None:
                    continue
                g = grids[b]
                g["n"] += 1
                g["mo"].append(mo)
                g["net"].append(e["net"])
                if r300 is not None:
                    g["r300"].append(abs(r300))
                g["usd"] += e["net"] * e["noti"] / 1e4
    print(f"（缺数据：{dict(miss)}）")
    print("=" * 96)
    print(f"{'fo=OFI×d 档':>14s} {'腿数':>6s} {'markout@60s':>12s} {'t':>7s} "
          f"{'已实现净bp':>11s} {'|r300|中位':>10s} {'净额$':>9s}")
    res = {}
    for b in [f"{lo}-{hi}" for lo, hi in FO_BINS]:
        g = grids.get(b)
        if not g or g["n"] < 5:
            print(f"{b:>14s} {0 if not g else g['n']:6d}  （样本不足）")
            continue
        m = st.mean(g["mo"])
        sd = st.stdev(g["mo"]) if len(g["mo"]) > 1 else 0.0
        t = m / (sd / math.sqrt(len(g["mo"]))) if sd > 0 else 0.0
        print(f"{b:>14s} {g['n']:6d} {m:+12.2f} {t:+7.2f} {st.mean(g['net']):+11.2f} "
              f"{(st.median(g['r300']) if g['r300'] else float('nan')):10.1f} "
              f"{g['usd']:9.2f}")
        res[b] = {"n": g["n"], "markout_bp": round(m, 3), "t": round(t, 2),
                  "net_bp": round(st.mean(g["net"]), 3), "usd": round(g["usd"], 3)}
    print("=" * 96)
    sat = res.get("0.95-1.01")
    mid = res.get("0.5-0.95") or res.get("0.8-0.95")
    lo_ = res.get("0.0-0.5")
    if sat and mid:
        d_ = sat["markout_bp"] - mid["markout_bp"]
        verdict = (f"饱和档(≥0.95) markout {sat['markout_bp']:+.2f}bp（t={sat['t']:+.2f}）"
                   f" vs 中间档 {mid['markout_bp']:+.2f}bp ⇒ Δ={d_:+.2f}bp "
                   + ("**在我们自己的成交上同样成立**（饱和更好）⇒ ③ 有据"
                      if d_ > 0.3 and sat["t"] > 1.0 else
                      "**未复现**（饱和并不更好）⇒ ③ 可能从别人的样本学到、在自己成交上失效，"
                      "需谨慎并优先看机制指标"))
    else:
        verdict = "样本不足，无法裁决"
    print("⇒ 裁决:", verdict)
    if lo_:
        print(f"（参考：低档 0.0-0.5 markout {lo_['markout_bp']:+.2f}bp，n={lo_['n']}）")
    OUT.write_text(json.dumps({"hours": a.hours, "entries": len(ents),
                               "bins": res, "verdict": verdict,
                               "missing": dict(miss)}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
