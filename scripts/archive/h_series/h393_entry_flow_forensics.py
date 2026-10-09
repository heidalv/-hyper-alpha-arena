# -*- coding: utf-8 -*-
"""H393 #16 前置法证：止损腿的入场侧流向状态与 MFE 分类（波动段顺流过滤依据）。

问题（h389 §7 / h375 #16）：止损腿里有"无解腿"（MFE 从未到 +5bp，入场侧即错，
尾随锁利救不了）。本脚本在 #2 时代窗口的止损腿上量化：
  1. MFE 分类：MFE≥+20（尾随可解）/ +5≤MFE<+20（临界）/ MFE<+5（无解）；
  2. 入场时刻 15s OFI 流向（market_trades_aggregated，taker_buy−taker_sell 归一）：
     flow_with = OFI 方向与腿方向一致（顺流入场）vs 逆流；
  3. 列联表：MFE 类 × 顺/逆流 ⇒ 无解腿是否集中在逆流入场；
  4. 可解决损失池：若"逆流 + 波动段"入场被闸掉，可避免的 bp 与腿数
     （保守口径：只算 MFE<+5 的逆流止损腿；被闸掉的腿量按宇宙其它币替补
     不计收益，只报净 bp 与腿速代价）。

口径沿用 h390 已验证配对（出场↔最近同币反向入场/加仓，入场 mark mid 参考；
仅窗口内新鲜腿）。1s 路径 MFE 口径与 h388/h390 一致（乐观：wicks 未平滑）。

用法: python scripts/h393_entry_flow_forensics.py
"""
from __future__ import annotations

import bisect
import datetime as dt
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h393_entry_flow_forensics.json"

WINDOW_START = "2026-09-27 13:00:00+08"
WINDOW_START_DT = dt.datetime.fromisoformat(WINDOW_START.replace("+08", "+08:00"))
MFE_HIGH, MFE_LOW = 20.0, 5.0


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


def load_stop_legs():
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
        if exit_path != "stop_loss_taker" or side not in ("buy", "sell"):
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
        legs.append({"symbol": sym, "side": (entry[3] or {}).get("side"),
                     "entry_px": entry_mid, "entry_ts": entry[2],
                     "exit_ts": ts, "net_bp": float(net_bp or 0.0)})
    return legs


def load_paths(legs):
    import psycopg
    syms = sorted({l["symbol"] for l in legs})
    paths = {}
    with psycopg.connect(read_env_dsn().replace("/alpha_arena", "/alpha_market")) as c:
        with c.cursor() as cur:
            for sym in syms:
                t0 = min(l["entry_ts"].timestamp() for l in legs if l["symbol"] == sym) - 60
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
                paths[sym] = ([int(r[0]) for r in recs],
                              [(float(r[1]) + float(r[2])) / 2.0 for r in recs])
    return paths


def load_ofi(legs):
    """每腿入场前后 30s 内的 15s 桶 OFI（归一化）。"""
    import psycopg
    ofi_by = {}
    with psycopg.connect(read_env_dsn().replace("/alpha_arena", "/alpha_market")) as c:
        with c.cursor() as cur:
            for i, l in enumerate(legs):
                key = (l["symbol"], l["entry_ts"])
                # timestamp 为毫秒（实测 1790503455000），窗口 = 入场前后各 15s
                t0 = int(l["entry_ts"].timestamp()) * 1000 - 15_000
                t1 = int(l["entry_ts"].timestamp()) * 1000 + 15_000
                cur.execute("""
                    SELECT COALESCE(SUM(taker_buy_notional),0), COALESCE(SUM(taker_sell_notional),0)
                    FROM market_trades_aggregated
                    WHERE exchange='asterdex' AND symbol=%s
                      AND timestamp > %s::bigint AND timestamp <= %s::bigint
                """, (l["symbol"], t0, t1))
                r = cur.fetchone()
                b, s = float(r[0] or 0.0), float(r[1] or 0.0)
                tot = b + s
                ofi_by[key] = ((b - s) / tot) if tot > 0 else 0.0
    return ofi_by


def main() -> int:
    legs = load_stop_legs()
    print(f"止损腿（新鲜配对成功）: {len(legs)}", flush=True)
    if not legs:
        print("无腿，退出")
        return 1
    paths = load_paths(legs)
    ofi_by = load_ofi(legs)

    rows = []
    cells = {}   # (mfe_class, flow) -> [n, sumA]
    for l in legs:
        ts_1s, mid_1s = paths.get(l["symbol"], ([], []))
        s = 1.0 if l["side"] == "buy" else -1.0
        i0 = bisect.bisect_left(ts_1s, int(l["entry_ts"].timestamp()))
        i_end = bisect.bisect_left(ts_1s, int(l["exit_ts"].timestamp()))
        mfe = 0.0
        for i in range(i0, min(len(ts_1s), i_end)):
            ret = (mid_1s[i] - l["entry_px"]) / l["entry_px"] * 1e4 * s
            if ret > mfe:
                mfe = ret
        if mfe >= MFE_HIGH:
            mcls = "high"
        elif mfe >= MFE_LOW:
            mcls = "mid"
        else:
            mcls = "low"
        ofi = ofi_by.get((l["symbol"], l["entry_ts"]), 0.0)
        flow = "with" if ofi * s >= 0 else "against"
        cells.setdefault((mcls, flow), [0, 0.0])
        cells[(mcls, flow)][0] += 1
        cells[(mcls, flow)][1] += l["net_bp"]
        rows.append({"sym": l["symbol"], "side": l["side"],
                     "entry": str(l["entry_ts"])[11:19],
                     "mfe_bp": round(mfe, 1), "ofi_norm": round(ofi, 3),
                     "flow": flow, "A_bp": round(l["net_bp"], 1)})

    print(f"\n{'MFE类':<8} {'流向':<8} {'腿数':>5} {'A合计bp':>9} {'均值bp':>8}")
    print("-" * 42)
    for mcls in ("high", "mid", "low"):
        for flow in ("with", "against"):
            n, a = cells.get((mcls, flow), [0, 0.0])
            if n:
                print(f"{mcls:<8} {flow:<8} {n:>5} {a:>+9.1f} {a/n:>+8.1f}")

    n_low_ag = cells.get(("low", "against"), [0, 0.0])
    n_low_w = cells.get(("low", "with"), [0, 0.0])
    tot_a = sum(l["net_bp"] for l in legs)
    print(f"\n止损腿合计 {len(legs)} 腿 {tot_a:+.1f}bp")
    print(f"无解腿（MFE<+5）: 逆流 {n_low_ag[0]} 腿 {n_low_ag[1]:+.1f}bp ／ "
          f"顺流 {n_low_w[0]} 腿 {n_low_w[1]:+.1f}bp")
    print(f"⇒ 若「逆流+波动段」入场被闸：#16 可避免损失池 ≈ {abs(n_low_ag[1]):.1f}bp"
          f"（{n_low_ag[0]} 腿；被闸腿量代价与替补收益未计，试跑仲裁）")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "n_stop_legs": len(legs), "tot_A_bp": round(tot_a, 1),
        "cells": {f"{k[0]}_{k[1]}": {"n": v[0], "sum_bp": round(v[1], 1)}
                  for k, v in cells.items()},
        "addressable_loss_bp": round(abs(n_low_ag[1]), 1),
        "rows": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已存 {OUT}")

    # ── 第二段：全腿流向列联（#16 全影子：闸掉逆流腿会不会误伤盈利腿）──
    import psycopg
    with psycopg.connect(read_env_dsn()) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT symbol, ts, net_bp, meta_json
                FROM lane_ledger
                WHERE lane_id='mm_asterdex' AND ts > %s::timestamptz
                ORDER BY ts
            """, (WINDOW_START,))
            all_rows = cur.fetchall()
    with psycopg.connect(read_env_dsn().replace("/alpha_arena", "/alpha_market")) as c:
        with c.cursor() as cur:
            stats = {}
            for sym, ts, net_bp, meta in all_rows:
                side = (meta or {}).get("side")
                if side not in ("buy", "sell"):
                    continue
                s = 1.0 if side == "buy" else -1.0
                t0 = int(ts.timestamp()) * 1000 - 15_000
                t1 = int(ts.timestamp()) * 1000 + 15_000
                cur.execute("""
                    SELECT COALESCE(SUM(taker_buy_notional),0), COALESCE(SUM(taker_sell_notional),0)
                    FROM market_trades_aggregated
                    WHERE exchange='asterdex' AND symbol=%s
                      AND timestamp > %s::bigint AND timestamp <= %s::bigint
                """, (sym, t0, t1))
                r = cur.fetchone()
                b, sl = float(r[0] or 0.0), float(r[1] or 0.0)
                tot = b + sl
                if tot <= 0:
                    flow = "n/a"
                else:
                    ofi = (b - sl) / tot
                    if abs(ofi) < 0.15:
                        flow = "weak"
                    else:
                        flow = "with" if ofi * s >= 0 else "against"
                a = float(net_bp or 0.0)
                st = stats.setdefault(flow, [0, 0.0])
                st[0] += 1
                st[1] += a

    print(f"\n全腿流向列联（窗口内 {len(all_rows)} 行，含 maker 减仓腿）：")
    print(f"{'流向':<10} {'腿数':>6} {'净bp':>10} {'均值bp':>8}")
    print("-" * 38)
    for flow in ("with", "weak", "against", "n/a"):
        if flow in stats:
            n, a = stats[flow]
            print(f"{flow:<10} {n:>6} {a:>+10.1f} {a/n:>+8.2f}")
    n_ag = stats.get("against", [0, 0.0])
    print(f"\n⇒ 逆流腿（|ofi|≥0.15 且方向相反）共 {n_ag[0]} 腿、净 {n_ag[1]:+.1f}bp："
          f"闸掉它们 {'划算' if n_ag[1] < 0 else '会误伤盈利'}（净额口径）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
