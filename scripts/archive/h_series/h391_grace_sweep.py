# -*- coding: utf-8 -*-
"""H391 宽限时长扫描：stop_maker_grace_sec 对出口侧 P&L 的影响（#18 预注册依据）。

背景（h390）：尾随锁利的期望被两股力量稀释，其中最大的一项是**宽限击穿**——
30s maker 宽限内价格继续下穿触发线，taker 在到期时成交在更深价位。
本脚本在 h390 的干净腿群体（窗口内强制出场腿、反向成交配对、入场 mark mid 参考、
15s 生产网格）上扫描 grace ∈ {0,10,15,30,60,120}s × 尾随 {关, 开(trail=20)}：
  · 曲线 A（尾随关）：grace 对**现行止损**的影响（改 grace 是独立单变量，
    本身就是一个试跑候选）；
  · 曲线 B（尾随开）：grace 与尾随的交互（#18 治理滑点的参数依据）。
输出：每个 grace 下的 Δ15s 合计、受影响腿数、平均出场 bp、机会成本。

已知乐观假设同 h390（即时成交、未建模滑点/多加仓均价重锚）。
用法: python scripts/h391_grace_sweep.py
"""
from __future__ import annotations

import bisect
import datetime as dt
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h391_grace_sweep.json"

WINDOW_START = "2026-09-27 13:00:00+08"
WINDOW_START_DT = dt.datetime.fromisoformat(WINDOW_START.replace("+08", "+08:00"))
TAKER_FEE_BP = 4.0
STOP0, BE_AT = 40.0, 20.0
OPP_WINDOW_SEC = 1800.0
GRACES = [0.0, 10.0, 15.0, 30.0, 60.0, 120.0]


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


def trail_line(mfe_bp: float, trail_on: bool) -> float:
    if not trail_on:
        return -STOP0
    mfe_r = round(float(mfe_bp), 6)
    if mfe_r < BE_AT:
        return -STOP0
    return -5.0 + 10.0 * ((mfe_r - BE_AT) // 10.0)


def simulate(ts_g, mid_g, entry_px, entry_ts, exit_ts, s, grace_sec, trail_on):
    """返回 (exit_bp, affected, opp_bp)。exit_bp=None ⇒ 未触发（B=A）。"""
    i0 = bisect.bisect_left(ts_g, int(entry_ts.timestamp()))
    i_end = bisect.bisect_left(ts_g, int(exit_ts.timestamp()))
    mfe = 0.0
    i = i0
    while i < min(len(ts_g), i_end):
        ret = (mid_g[i] - entry_px) / entry_px * 1e4 * s
        if ret > mfe:
            mfe = ret
        if ret <= trail_line(mfe, trail_on):
            j = bisect.bisect_left(ts_g, ts_g[i] + int(grace_sec))
            if j >= min(len(ts_g), i_end):
                break
            for k in range(i + 1, j + 1):
                rk = (mid_g[k] - entry_px) / entry_px * 1e4 * s
                if rk > mfe:
                    mfe = rk
            ret_j = (mid_g[j] - entry_px) / entry_px * 1e4 * s
            if ret_j <= trail_line(mfe, trail_on):
                return ret_j - TAKER_FEE_BP, True, 0.0
            i = j + 1
            continue
        i += 1
    return None, False, 0.0


def load_legs():
    import psycopg
    with psycopg.connect(read_env_dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT symbol, position_id, ts, net_bp, meta_json
                FROM lane_ledger
                WHERE lane_id='mm_asterdex'
                  AND ts > %s::timestamptz - interval '3 hours'
                ORDER BY ts
            """, (WINDOW_START,))
            all_rows = cur.fetchall()
    legs = []
    for sym, pid, ts, net_bp, meta in all_rows:
        side = (meta or {}).get("side")
        exit_path = (meta or {}).get("exit_path") or ""
        if not exit_path or side not in ("buy", "sell"):
            continue
        opp = "sell" if side == "buy" else "buy"
        entry = None
        for sym2, pid2, ts2, net_bp2, meta2 in reversed(all_rows):
            if ts2 >= ts or sym2 != sym:
                continue
            if (meta2 or {}).get("side") == opp and not ((meta2 or {}).get("exit_path") or ""):
                entry = (sym2, pid2, ts2, meta2)
                break
        if not entry or entry[2] < WINDOW_START_DT:
            continue
        entry_mid = float((entry[3].get("mid_px") or entry[3].get("fill_px") or 0.0) or 0.0)
        legs.append({
            "symbol": sym, "side": (entry[3] or {}).get("side"),
            "entry_px": entry_mid, "entry_ts": entry[2],
            "exit_ts": ts, "net_bp": net_bp, "exit_path": exit_path,
        })
    return legs


def load_paths(legs):
    import psycopg
    syms = sorted({l["symbol"] for l in legs})
    paths = {}
    with psycopg.connect(read_env_dsn().replace("/alpha_arena", "/alpha_market")) as c:
        with c.cursor() as cur:
            for sym in syms:
                t0 = min(l["entry_ts"].timestamp() for l in legs if l["symbol"] == sym) - 120
                t1 = max(l["exit_ts"].timestamp() for l in legs if l["symbol"] == sym) + 60
                cur.execute("""
                    SELECT (event_ts_ms/1000)::bigint AS b,
                           (array_agg(bid_px ORDER BY event_ts_ms DESC))[1] AS bid,
                           (array_agg(ask_px ORDER BY event_ts_ms DESC))[1] AS ask
                    FROM asterdex_book_ticker
                    WHERE symbol=%s
                      AND event_ts_ms >= %s::bigint*1000 AND event_ts_ms <= %s::bigint*1000
                      AND bid_px>0 AND ask_px>bid_px
                    GROUP BY b ORDER BY b
                """, (sym + "USDT", int(t0), int(t1)))
                recs = cur.fetchall()
                ts_1s = [int(r[0]) for r in recs]
                mid_1s = [(float(r[1]) + float(r[2])) / 2.0 for r in recs]
                grid_ts, grid_mid = [], []
                _last = None
                for t, m in zip(ts_1s, mid_1s):
                    b = (t // 15) * 15
                    if b != _last:
                        grid_ts.append(b)
                        grid_mid.append(m)
                        _last = b
                    else:
                        grid_mid[-1] = m
                paths[sym] = (grid_ts, grid_mid)
    return paths


def main() -> int:
    legs = load_legs()
    paths = load_paths(legs)
    tot_a = sum(float(l["net_bp"] or 0.0) for l in legs)
    print(f"腿数 {len(legs)}，A 合计 {tot_a:+.1f}bp", flush=True)

    results = {}
    for trail_on in (False, True):
        for g in GRACES:
            tot_b = 0.0
            n_aff = 0
            for l in legs:
                a = float(l["net_bp"] or 0.0)
                s = 1.0 if l["side"] == "buy" else -1.0
                ts_g, mid_g = paths.get(l["symbol"], ([], []))
                if l["entry_px"] and ts_g:
                    b, aff, _ = simulate(ts_g, mid_g, l["entry_px"], l["entry_ts"],
                                         l["exit_ts"], s, g, trail_on)
                    if aff:
                        n_aff += 1
                        tot_b += b
                        continue
                tot_b += a
            key = ("trail_on" if trail_on else "trail_off") + f"_grace{int(g)}s"
            results[key] = {"grace_s": g, "trail_on": trail_on, "n_aff": n_aff,
                            "tot_B_bp": round(tot_b, 1), "delta_bp": round(tot_b - tot_a, 1)}

    print(f"\n{'口径':<22} {'Δbp':>9} {'B合计':>9} {'受影响':>6}")
    print("-" * 50)
    for trail_on in (False, True):
        for g in GRACES:
            r = results[("trail_on" if trail_on else "trail_off") + f"_grace{int(g)}s"]
            tag = ("尾随开" if trail_on else "尾随关") + f" grace={int(g):>3}s"
            print(f"{tag:<22} {r['delta_bp']:>+9.1f} {r['tot_B_bp']:>+9.1f} {r['n_aff']:>6}")
    print(f"\nA（已实现）基准 = {tot_a:+.1f}bp（生产当前 grace=30s、尾随关）")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "n_legs": len(legs), "tot_A_bp": round(tot_a, 1),
        "results": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
