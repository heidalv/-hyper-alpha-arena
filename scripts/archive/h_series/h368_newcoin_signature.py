# -*- coding: utf-8 -*-
"""H368 新币签名：NEAR/SUI/ARB/ADA/XRP/ENA 的形态×币种匹配预分配。

延续 h367（BNB=最强动量币 ⇒ 反转族失效、动量族有效）。本脚本对 #2 宇宙新增的
6 币算同一组签名（rel_spread / |r15| / ofi_ac1 / vr60），并与外生的事件研究
edge（h355b P1、h361b P4/P5 flow=with）合并，输出每币的形态启用建议：
  vr60 ≥ 1.35 ⇒ 反转族（P1/P2/P3）禁用、动量族（P4/P5）启用；
  vr60 < 1.35 ⇒ 全形态。
（该建议不立即部署——#2 只测宇宙；#6/#7 试跑时用作参考口径，实盘以判定为准。）

用法: python scripts/h368_newcoin_signature.py [--hours 168]
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h368_newcoin_signature.json"
# 外生事件研究 edge（flow=with 口径 f30）
P1_EDGE = {"NEAR": 1.099, "SUI": 1.143, "ARB": 0.821, "ADA": 0.799,
           "XRP": 0.762, "ENA": 0.721, "DOGE": 1.591, "ETH": 0.825,
           "SOL": 0.207, "BTC": 0.360, "BNB": -0.316}
P4_EDGE = {"SOL": 1.000, "DOGE": 1.638, "XRP": 1.314, "BNB": 0.764,
           "BTC": 0.443, "ETH": 0.407}
P5_EDGE = {"XRP": 0.810, "DOGE": 1.158, "SOL": 0.665, "SUI": 1.278,
           "ARB": 2.722, "ENA": 3.013, "BNB": 0.542, "BTC": 0.434, "ETH": 0.386}


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


def _med(xs):
    if not xs:
        return None
    s = sorted(xs)
    return s[len(s) // 2]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=168.0)
    ap.add_argument("--symbols", default="NEARUSDT,SUIUSDT,ARBUSDT,ADAUSDT,XRPUSDT,ENAUSDT")
    a = ap.parse_args()

    import psycopg

    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    rows = {}

    for sym in syms:
        bare = sym[:-4]
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
        if len(recs) < 5000:
            print(f"{bare}: book 样本不足 {len(recs)}，跳过")
            continue
        mids = [(float(r[1]) + float(r[2])) / 2.0 for r in recs]
        relsp = [(float(r[2]) - float(r[1])) / ((float(r[1]) + float(r[2])) / 2.0) * 1e4
                 for r in recs if float(r[1]) > 0 and float(r[2]) > float(r[1])]
        rets = [(mids[t + 1] - mids[t]) / mids[t] * 1e4
                for t in range(len(mids) - 1) if mids[t] > 0 and mids[t + 1] > 0]
        k = 60
        vr = None
        if len(rets) > k * 50:
            mean = sum(rets) / len(rets)
            v1 = sum((x - mean) ** 2 for x in rets) / len(rets)
            blocks = [sum(rets[i:i + k]) for i in range(0, len(rets) - k, k)]
            mb = sum(blocks) / len(blocks)
            vk = sum((x - mb) ** 2 for x in blocks) / len(blocks)
            vr = (vk / (k * v1)) if v1 > 0 else None

        with psycopg.connect(read_env_dsn()) as c:
            with c.cursor() as cur:
                cur.execute("""
                    SELECT timestamp, COALESCE(taker_buy_notional,0), COALESCE(taker_sell_notional,0)
                    FROM market_trades_aggregated
                    WHERE symbol=%s AND timestamp >= (extract(epoch from now())*1000 - %s*3600*1000)::bigint
                    ORDER BY timestamp
                """, (bare, a.hours))
                orows = cur.fetchall()
        ofi_buckets = {}
        for ts_ms, bn, sn in orows:
            tot = float(bn) + float(sn)
            if tot > 0:
                ofi_buckets[int(ts_ms) // 15000] = (float(bn) - float(sn)) / tot
        ob = [ofi_buckets[t] for t in sorted(ofi_buckets)]
        ac1 = None
        if len(ob) > 100:
            m = sum(ob) / len(ob)
            num = sum((ob[t] - m) * (ob[t - 1] - m) for t in range(1, len(ob)))
            den = sum((x - m) ** 2 for x in ob)
            ac1 = num / den if den > 0 else None

        vrx = vr if vr is not None else 0.0
        if vrx >= 1.35:
            assign = "动量族优先（P4/P5）；反转族 P1/P2/P3 禁用"
        elif vrx >= 1.2:
            assign = "全形态，但反转族谨慎（BNB 教训：vr 高 ⇒ 回调是趋势腿）"
        else:
            assign = "全形态"
        rows[bare] = {
            "p1_f30": P1_EDGE.get(bare), "p4_f30": P4_EDGE.get(bare),
            "p5_f30": P5_EDGE.get(bare),
            "median_rel_spread_bp": round(_med(relsp), 3),
            "median_abs_15s_ret_bp": round(_med([abs(x) for x in rets]), 3),
            "ofi_ac1": round(ac1, 4) if ac1 is not None else None,
            "vr60": round(vr, 3) if vr is not None else None,
            "assign": assign,
        }
        print(f"{bare}: {rows[bare]}")

    print("\n── #2 宇宙新币形态分配建议 ──")
    print(f"{'币':<6} {'P1f30':>7} {'P4f30':>7} {'P5f30':>7} {'vr60':>6} {'ofi_ac1':>8}  建议")
    for bare in sorted(rows):
        r = rows[bare]
        print(f"{bare:<6} {r['p1_f30'] or 0:>+7.2f} {r['p4_f30'] or 0:>+7.2f} "
              f"{r['p5_f30'] or 0:>+7.2f} {r['vr60'] or 0:>6.2f} {r['ofi_ac1'] or 0:>8.3f}  "
              f"{r['assign']}")

    OUT.write_text(json.dumps({"hours": a.hours, "symbols": rows},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
