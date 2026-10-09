# -*- coding: utf-8 -*-
"""H373 统一流定律的样本外稳健性检验：前 84h vs 后 84h。

若流定律（with 正 / against 负）在两个独立窗口都成立 ⇒ 规律跨 regime 稳健；
若只在全窗成立而半窗翻转 ⇒ 过拟合风险，需降权或重新审视。
测 P1（薄流回调）与 P5（挤压突破）两个代表形态。

用法: python scripts/h373_flow_law_oos.py [--hours 168]
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
OUT = ROOT / "research_l1" / "out" / "h373_flow_law_oos.json"


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
    return {"n": n, "bp": round(m, 3), "t": round(t, 2)}


def collect(syms, ago_hours: float, hours: float):
    """ago_hours 前的 hours 窗口内，P1/P5 事件（flow 标记 + f30/f120）。"""
    import psycopg
    rows = []
    for sym in syms:
        lo_ms = (ago_hours + hours) * 3600 * 1000
        hi_ms = ago_hours * 3600 * 1000
        with psycopg.connect(read_env_dsn()) as c:
            with c.cursor() as cur:
                cur.execute("""
                    SELECT (event_ts_ms/1000)::bigint AS b,
                           (array_agg(bid_px ORDER BY event_ts_ms DESC))[1] AS bid,
                           (array_agg(ask_px ORDER BY event_ts_ms DESC))[1] AS ask
                    FROM asterdex_book_ticker
                    WHERE symbol=%s
                      AND event_ts_ms < (extract(epoch from now())*1000 - %s)::bigint
                      AND event_ts_ms >= (extract(epoch from now())*1000 - %s)::bigint
                      AND bid_px>0 AND ask_px>bid_px
                    GROUP BY b ORDER BY b
                """, (sym, hi_ms, lo_ms))
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
                    WHERE symbol=%s AND timestamp < (extract(epoch from now())*1000 - %s)::bigint
                      AND timestamp >= (extract(epoch from now())*1000 - %s)::bigint
                    ORDER BY timestamp
                """, (bare, hi_ms, lo_ms))
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

        def fwd(i, sec):
            j = bisect.bisect_right(ks, ks[i] + sec) - 1
            return (mids[j] - mids[i]) / mids[i] * 1e4 \
                if j > i and ks[j] - ks[i] >= sec * 0.9 else None

        vol60 = []
        for i in range(120, n):
            seg = mids[i - 120:i + 1]
            vol60.append(sum(abs((seg[t + 1] - seg[t]) / seg[t]) * 1e4
                             for t in range(len(seg) - 1)))
        vol_q30 = sorted(vol60)[int(len(vol60) * 0.3)] if vol60 else 1.0

        last = -1e18
        for i in range(n):
            if ks[i] - last < 20:
                continue
            r15 = past(i, 15)
            r60 = past(i, 60)
            r300 = past(i, 300)
            if r15 is None:
                continue
            cands = []
            # P3 spike_fade：|r15|≥3bp → fade
            if abs(r15) >= 3.0:
                cands.append(("P3", -1.0 if r15 > 0 else 1.0))
            if r60 is not None and r300 is not None and abs(r60) >= 2.0 \
                    and abs(r300) >= 15.0 and (r60 > 0) != (r300 > 0):
                cands.append(("P1", 1.0 if r300 > 0 else -1.0))
            segv = mids[max(0, i - 60):i + 1]
            v60 = sum(abs((segv[t + 1] - segv[t]) / segv[t]) * 1e4
                      for t in range(len(segv) - 1))
            if v60 < vol_q30 and abs(r15) >= 2.0:
                cands.append(("P5", 1.0 if r15 > 0 else -1.0))
            if not cands:
                continue
            last = ks[i]
            o = ofi.get(ks[i] // 15)
            f30v = fwd(i, 30)
            f120v = fwd(i, 120)
            if f30v is None or f120v is None:
                continue
            for pname, sign in cands:
                ofi_signed = o * sign if o is not None else None
                flow = "with" if (ofi_signed is not None and ofi_signed >= 0.3) else \
                       ("against" if (ofi_signed is not None and ofi_signed <= -0.3)
                        else "neutral")
                rows.append({"p": pname, "flow": flow,
                             "f30": f30v * sign, "f120": f120v * sign})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=168.0)
    a = ap.parse_args()
    syms = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "DOGEUSDT"]
    half = a.hours / 2.0

    old = collect(syms, ago_hours=half, hours=half)
    new = collect(syms, ago_hours=0.0, hours=half)

    out = {}
    for label, rows in (("first_half", old), ("second_half", new)):
        print(f"\n══ {label}（n={len(rows)}）══")
        print(f"{'形态×流向':<16} {'n':>7} {'f30':>14} {'f120':>14}")
        rec = {}
        for pname in ("P1", "P3", "P5"):
            for flow in ("with", "neutral", "against"):
                sub = [r for r in rows if r["p"] == pname and r["flow"] == flow]
                st30 = _stat([r["f30"] for r in sub])
                st120 = _stat([r["f120"] for r in sub])
                key = f"{pname}_{flow}"
                rec[key] = {"n": len(sub), "f30": st30, "f120": st120}
                if st30:
                    print(f"{pname}×{flow:<11} {st30['n']:>7} "
                          f"{st30['bp']:>+8.3f}({st30['t']:+.1f}) "
                          f"{st120['bp']:>+8.3f}({st120['t']:+.1f})")
        out[label] = rec

    OUT.write_text(json.dumps({"hours": a.hours, "half": half, **out},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
