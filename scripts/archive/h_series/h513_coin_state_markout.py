"""h513：**逐币 × 状态** markout——BNB 的毒性是"币的问题"还是"状态的问题"？

为什么这一步决定处置方式（h512 的结论目前是"BNB 结构不可行 ⇒ 腿数必掉"）：
  · 若 BNB 在**所有状态**都为负 ⇒ 只能少做/不做（腿数掉，与 ≥60/h 冲突）；
  · 若 BNB 只在**部分状态**为负（例如高波动/强趋势），而在其余状态为正
    ⇒ 可以用**按状态的门控**保住腿数（这与 h454 的趋势闸同一种工具）。

口径（每笔入场腿，按 h488 的 45s 延迟校正）：
  · `r300` = 真实逐笔 5 分钟趋势（bp，带符号）；
  · `noise15` = 该币 15s 桶相邻变化的绝对值（bp，用成交前后各 15 分钟窗口估计）；
  · `markout60` = 有向 `(mid_{t+60} − fill_px)`（含捕获）；
  分档：|r300| ∈ {0-10, 10-20, 20-40, ≥40}bp；噪声按该币中位数二分（低/高）。

用法：python scripts/h513_coin_state_markout.py [--hours 48]
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
OUT = ROOT / "research_l1" / "out" / "h513_coin_state.json"
LAG_S = 45
RB = ((0, 10), (10, 20), (20, 40), (40, 1e9))


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


def mid_at(cur, sym, t_ms, tol=20000):
    cur.execute(
        "SELECT (ARRAY_AGG((bid_px+ask_px)/2 ORDER BY event_ts_ms DESC))[1]::float8 "
        "FROM asterdex_book_ticker WHERE symbol=%s AND event_ts_ms > %s "
        "AND event_ts_ms <= %s AND bid_px>0 AND ask_px>bid_px",
        (sym + "USDT", t_ms - tol, t_ms + tol))
    r = cur.fetchone()
    return float(r[0]) if r and r[0] else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=48.0)
    a = ap.parse_args()
    dsn = read_env_dsn()
    with psycopg.connect(dsn, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT meta_json->'symbols' FROM lane_registry WHERE lane_id=%s",
                        (LANE,))
            r = cur.fetchone()
            syms = [str(s) for s in (r[0] if r and isinstance(r[0], list) else [])]
            cur.execute(
                "SELECT ts, symbol, COALESCE(meta_json->>'side',''), "
                "ABS(COALESCE((meta_json->>'qty')::float8,0))::float8, "
                "COALESCE(meta_json->>'exit_path',''), "
                "COALESCE((meta_json->>'fill_px')::float8,0)::float8 "
                "FROM lane_ledger WHERE lane_id=%s AND symbol = ANY(%s) "
                "AND ts > now() - make_interval(hours => %s::int) ORDER BY symbol, ts",
                (LANE, syms, int(a.hours)))
            rows = cur.fetchall()
    by_sym = collections.OrderedDict()
    for r in rows:
        by_sym.setdefault(r[1], []).append(r)
    ents = []
    for sym, rs in by_sym.items():
        cum = 0.0
        for ts, _s, side, qty, ep, px in rs:
            signed = abs(qty) if str(side).lower() == "buy" else -abs(qty)
            before = cum
            cum += signed
            if ep == "" and (abs(before) < 1e-9 or (before > 0) == (signed > 0)) and px > 0:
                ents.append({"sym": sym, "ts": ts, "side": str(side).lower(), "px": float(px)})
    print(f"近 {a.hours:.0f}h 入场腿 {len(ents)}")
    # 噪声中位（每币）
    mk = dsn.replace("/alpha_arena", "/alpha_market")
    noise_med = {}
    with psycopg.connect(mk, autocommit=True) as cm:
        with cm.cursor() as cur:
            now_ms = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
            for s in syms:
                cur.execute(
                    "WITH m AS (SELECT floor(event_ts_ms/15000) AS b, "
                    "(ARRAY_AGG(price ORDER BY event_ts_ms DESC))[1]::float8 AS px "
                    "FROM asterdex_trades WHERE symbol=%s AND event_ts_ms > %s "
                    "GROUP BY 1), d AS (SELECT px, LAG(px) OVER (ORDER BY b) AS p0 FROM m) "
                    "SELECT percentile_cont(0.5) WITHIN GROUP "
                    "(ORDER BY ABS(px-p0)/NULLIF(p0,0)*1e4)::float8 "
                    "FROM d WHERE p0 IS NOT NULL", (s + "USDT", now_ms - 86400_000))
                noise_med[s] = float(cur.fetchone()[0] or 0.0)
            grid: dict = collections.defaultdict(lambda: {"mo": [], "n": 0})
            for e in ents:
                t_ms = int((e["ts"] - dt.timedelta(seconds=LAG_S)).timestamp() * 1000)
                r3 = None
                cur.execute(
                    "SELECT (ARRAY_AGG(price ORDER BY event_ts_ms ASC))[1]::float8, "
                    "(ARRAY_AGG(price ORDER BY event_ts_ms DESC))[1]::float8, count(*) "
                    "FROM asterdex_trades WHERE symbol=%s AND event_ts_ms > %s "
                    "AND event_ts_ms <= %s",
                    (e["sym"] + "USDT", t_ms - 300000, t_ms))
                rr = cur.fetchone()
                if rr and rr[0] and rr[1] and int(rr[2] or 0) >= 2:
                    r3 = abs((float(rr[1]) - float(rr[0])) / float(rr[0]) * 1e4)
                m60 = mid_at(cur, e["sym"], t_ms + 60000)
                if m60 is None or r3 is None:
                    continue
                sgn = 1.0 if e["side"] == "buy" else -1.0
                mo = sgn * (m60 - e["px"]) / e["px"] * 1e4
                b = next((f"{lo}-{hi}" if hi < 1e8 else f">={lo}"
                          for lo, hi in RB if lo <= r3 < hi), None)
                if b is None:
                    continue
                g = grid[(e["sym"], b)]
                g["mo"].append(mo)
                g["n"] += 1
    print("=" * 104)
    print(f"逐币 × |r300| 分档 markout@60s（噪声中位："
          + "、".join(f"{s}={noise_med.get(s, 0):.1f}bp" for s in syms) + "）")
    print(f"{'币':6s} " + " ".join(f"{b:>14s}" for b in [f"{lo}-{hi}" if hi < 1e8
                                                        else f">={lo}" for lo, hi in RB]))
    res = {}
    for s in syms:
        cells = []
        for lo, hi in RB:
            b = f"{lo}-{hi}" if hi < 1e8 else f">={lo}"
            g = grid.get((s, b))
            if not g or g["n"] < 8:
                cells.append(f"{'—':>14s}")
                continue
            m = st.mean(g["mo"])
            sd = st.stdev(g["mo"]) if len(g["mo"]) > 1 else 0.0
            t = m / (sd / math.sqrt(len(g["mo"]))) if sd > 0 else 0.0
            cells.append(f"{m:+7.2f}({g['n']:3d})")
            res[f"{s}|{b}"] = {"n": g["n"], "mo": round(m, 3), "t": round(t, 2)}
        print(f"{s:6s} " + " ".join(cells))
    print("=" * 104)
    # 逐币：最好档与最差档
    verdicts = []
    for s in syms:
        cells = {b: res.get(f"{s}|{b}") for b in
                 [f"{lo}-{hi}" if hi < 1e8 else f">={lo}" for lo, hi in RB]}
        ok = [(b, v) for b, v in cells.items() if v and v["mo"] > 0.3 and v["t"] > 1.0]
        bad = [(b, v) for b, v in cells.items() if v and v["mo"] < -0.3 and v["t"] < -1.0]
        if ok:
            verdicts.append((s, "有正 EV 状态", [b for b, _ in ok]))
        elif bad:
            verdicts.append((s, "全档为负或中性", [b for b, _ in bad]))
        else:
            verdicts.append((s, "各档都打平/不显著", []))
    for s, tag, bs in verdicts:
        print(f"  {s:6s} ⇒ {tag}  {bs}")
    good = [s for s, tag, _ in verdicts if tag == "有正 EV 状态"]
    if good:
        verdict = (f"**{good} 存在正 EV 状态** ⇒ 对这些币可以用**按状态门控**保住腿数，"
                   f"不必直接删币（与 h454 的趋势闸同一工具）；"
                   f"其余币（{[s for s, tag, _ in verdicts if tag != '有正 EV 状态']}）"
                   f"在各档都赚不回来 ⇒ 只能少做/不做")
    else:
        verdict = ("**没有任何币在任何状态档为正** ⇒ 状态门控救不了，"
                   "只能接受更少腿量或改几何（都与 ≥60/h 冲突，需你裁定）")
    print("\n⇒ 裁决:", verdict)
    OUT.write_text(json.dumps({"hours": a.hours, "entries": len(ents),
                               "noise_median": noise_med, "cells": res,
                               "by_coin": {s: tag for s, tag, _ in verdicts},
                               "verdict": verdict}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print("已写:", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
