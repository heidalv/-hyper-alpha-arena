# -*- coding: utf-8 -*-
"""H360 信号×出口组合模拟：P1（薄流回调）信号下，哪种出口策略最大化 bp/腿。

问题（目标③组合数学）：h355 显示 P1 信号 edge 集中在 120s（f120 ≈ +0.6~+1.4bp），
而现行出口栈 tp=30bp/stop=40bp/timeout=120s 的 tp 在 120s 内几乎打不到 ⇒
实际出口 ≈ 止损或超时，可能与信号形状错配。
本脚本在 1s 中价路径上模拟各出口策略（无成交模型，纯信号侧；被动出口费≈0）：

  fixed τ        固定持有 τ 秒
  tp/to          ±tp 触价 / 超时 to（平仓于触价或超时中价）
  tp/to/stop     +止损

用法: python scripts/h360_signal_exit_sim.py [--hours 168]
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h360_exit_sim.json"
POLICIES = [
    ("fixed30", "fixed", dict(hold=30)),
    ("fixed60", "fixed", dict(hold=60)),
    ("fixed120", "fixed", dict(hold=120)),
    ("fixed180", "fixed", dict(hold=180)),
    ("fixed300", "fixed", dict(hold=300)),
    ("tp5_to120", "tp", dict(tp=5.0, to=120)),
    ("tp10_to120", "tp", dict(tp=10.0, to=120)),
    ("tp20_to120", "tp", dict(tp=20.0, to=120)),
    ("tp30_to120", "tp", dict(tp=30.0, to=120)),
    ("tp10_to300", "tp", dict(tp=10.0, to=300)),
    ("tp30_to300", "tp", dict(tp=30.0, to=300)),
    ("tp30_stop40_to120", "tpstop", dict(tp=30.0, stop=40.0, to=120)),
    ("tp10_stop40_to120", "tpstop", dict(tp=10.0, stop=40.0, to=120)),
    ("tp20_stop40_to120", "tpstop", dict(tp=20.0, stop=40.0, to=120)),
    ("tp5_stop40_to120", "tpstop", dict(tp=5.0, stop=40.0, to=120)),
]


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
    return url.replace("/alpha_arena", "/alpha_market")


def _stat(xs):
    n = len(xs)
    if n < 2:
        return None
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    t = m / math.sqrt(var / n) if var > 0 else 0.0
    xs_s = sorted(xs)
    return {"n": n, "bp": round(m, 3), "t": round(t, 2),
            "median": round(xs_s[n // 2], 3),
            "win_rate": round(sum(1 for x in xs if x > 0) / n, 3)}


def simulate(events, policy) -> list[float]:
    """events: [{mids, i0, sign}]（mids=1s 中价序列，i0=事件下标，sign=交易方向）。"""
    ptype, p = policy
    hold = p.get("hold")
    tp = p.get("tp")
    stop = p.get("stop")
    to = p.get("to")
    out = []
    for ev in events:
        mids, i0, sign = ev["mids"], ev["i0"], ev["sign"]
        base = mids[i0]
        n = len(mids)
        if ptype == "fixed":
            j = min(i0 + hold, n - 1)
            out.append((mids[j] - base) / base * 1e4 * sign)
            continue
        # tp / tpstop：从 i0+1 逐秒走，触 tp/stop 或超时
        j = i0
        while j < n - 1:
            j += 1
            ret = (mids[j] - base) / base * 1e4 * sign
            if tp and ret >= tp:
                out.append(tp)
                break
            if stop and ret <= -stop:
                out.append(-stop)
                break
            if to and j - i0 >= to:
                out.append(ret)
                break
        else:
            out.append((mids[n - 1] - base) / base * 1e4 * sign)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=168.0)
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,DOGEUSDT")
    a = ap.parse_args()

    import psycopg

    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    events_all = []
    events_with = []

    for sym in syms:
        with psycopg.connect(read_env_dsn()) as c:
            with c.cursor() as cur:
                cur.execute("""
                    SELECT (event_ts_ms/1000)::bigint AS b,
                           (array_agg(bid_px ORDER BY event_ts_ms DESC))[1] AS bid,
                           (array_agg(ask_px ORDER BY event_ts_ms DESC))[1] AS ask
                    FROM asterdex_book_ticker
                    WHERE symbol=%s AND event_ts_ms >= (extract(epoch from now())*1000 - %s*3600*1000)::bigint
                      AND bid_px>0 AND ask_px>bid_px
                    GROUP BY b ORDER BY b
                """, (sym, a.hours))
                recs = cur.fetchall()
        ks = [int(r[0]) for r in recs]
        mids = [float(r[1] + r[2]) / 2.0 for r in recs]
        n = len(ks)
        if n < 5000:
            continue
        bare = sym[:-4] if sym.endswith("USDT") else sym
        with psycopg.connect(read_env_dsn()) as c:
            with c.cursor() as cur:
                cur.execute("""
                    SELECT timestamp, COALESCE(taker_buy_notional,0), COALESCE(taker_sell_notional,0)
                    FROM market_trades_aggregated
                    WHERE symbol=%s AND timestamp >= (extract(epoch from now())*1000 - %s*3600*1000)::bigint
                    ORDER BY timestamp
                """, (bare, a.hours))
                orows = cur.fetchall()
        ofi = {}
        for ts_ms, bn, sn in orows:
            tot = float(bn) + float(sn)
            if tot > 0:
                ofi[int(ts_ms) // 15000] = (float(bn) - float(sn)) / tot

        def past(i, sec):
            j = bisect.bisect_left(ks, ks[i] - sec)
            return (mids[i] - mids[j]) / mids[j] * 1e4 \
                if j < i and ks[i] - ks[j] >= sec * 0.9 and mids[j] > 0 else None

        last = -1e18
        for i in range(n):
            if ks[i] - last < 20:
                continue
            r60 = past(i, 60)
            r300 = past(i, 300)
            if r60 is None or r300 is None:
                continue
            if abs(r60) < 2.0 or abs(r300) < 15.0 or (r60 > 0) == (r300 > 0):
                continue
            last = ks[i]
            sign = 1.0 if r300 > 0 else -1.0
            o = ofi.get(ks[i] // 15)
            ofi_signed = o * sign if o is not None else None
            flow = "with" if (ofi_signed is not None and ofi_signed >= 0.3) else \
                   ("against" if (ofi_signed is not None and ofi_signed <= -0.3) else "neutral")
            ev = {"mids": mids, "i0": i, "sign": sign}
            if flow != "against":
                events_all.append(ev)
            if flow == "with":
                events_with.append(ev)

    print(f"P1 薄流事件（flow≠against）: {len(events_all)}；仅 flow=with: {len(events_with)}")
    if not events_all:
        return 1

    results = {}
    print(f"\n{'策略':<20} {'n':>7} {'均值bp':>9} {'中位bp':>9} {'t':>7} {'胜率':>7}")
    for name, ptype, p in POLICIES:
        xs = simulate(events_all, (ptype, p))
        st = _stat(xs)
        if st:
            results[name] = st
            print(f"{name:<20} {st['n']:>7} {st['bp']:>+9.3f} {st['median']:>+9.3f} "
                  f"{st['t']:>+7.1f} {st['win_rate']:>7.3f}")
    results["flow_with_only_fixed120"] = _stat(
        simulate(events_with, ("fixed", dict(hold=120))))
    print(f"\nflow=with only × fixed120: "
          f"{results['flow_with_only_fixed120']['bp']:+}bp "
          f"(t={results['flow_with_only_fixed120']['t']:+})")

    OUT.write_text(json.dumps({"hours": a.hours, "symbols": syms,
                               "n_flow_ne_against": len(events_all),
                               "n_flow_with": len(events_with),
                               "policies": results}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\n已存 {OUT}")
    print("注：信号侧口径（无成交模型）；被动出口费≈0。tp 触价按 tp 满额计（乐观），"
          "stop/超时按路径中价计。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
