# -*- coding: utf-8 -*-
"""H367 坏币签名研究：BNB 为何在 5 项独立研究里全负？

对比维度（每币，168h）：
  P1 f120 edge（h355b 外生结果）
  median rel_spread（1s book_ticker）
  15s |return| 中位（波动）
  OFI 15s 一阶自相关（流持久性——流不持续 ⇒ 回调不回归）
  60s 方差比 VR60（动量结构）
  深度失衡 median |bq5−aq5|/(bq5+aq5)（5s 深度快照前 5 档）
目标：找出能作为"选币预过滤"的坏币签名（BNB vs DOGE/ETH 的差异维度）。

用法: python scripts/h367_coin_signature.py [--hours 168]
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
OUT = ROOT / "research_l1" / "out" / "h367_coin_signature.json"
# h355b 外生结果（P1 flow≠against 分币种 f120）
P1_EDGE = {"BNB": -0.316, "BTC": 0.360, "ETH": 0.825, "SOL": 0.207, "DOGE": 1.591}


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
    a = ap.parse_args()

    import psycopg

    syms = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "DOGEUSDT"]
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
        ks = [int(r[0]) for r in recs]
        mids = [(float(r[1]) + float(r[2])) / 2.0 for r in recs]
        relsp = [(float(r[2]) - float(r[1])) / ((float(r[1]) + float(r[2])) / 2.0) * 1e4
                 for r in recs if float(r[1]) > 0 and float(r[2]) > float(r[1])]
        rets = [(mids[t + 1] - mids[t]) / mids[t] * 1e4
                for t in range(len(mids) - 1) if mids[t] > 0 and mids[t + 1] > 0]
        # VR60（1s 网格，每 60 个 1s 样本一组；重叠窗口）
        k = 60
        vr = None
        if len(rets) > k * 50:
            _mean = sum(rets) / len(rets)          # 预计算（原写法在生成器内重复 sum=O(n²)）
            v1 = sum((x - _mean) ** 2 for x in rets) / len(rets)
            # 60s 非重叠块收益方差
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
        # 注：深度维度（depth_imb）因 asterdex_depth_snapshots 查询耗时过长（30min+）
        # 从本脚本移除；顶档量缺失且不是主判据。保留 4 个快速维度。
        # OFI 15s 一阶自相关（桶值序列）
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
        imb = []  # 深度维度已移除（查询过慢），占位保留输出结构
        rows[bare] = {
            "p1_f120_edge": P1_EDGE.get(bare),
            "median_rel_spread_bp": round(_med(relsp), 3),
            "median_abs_15s_ret_bp": round(_med([abs(x) for x in rets]), 3),
            "ofi_ac1": round(ac1, 4) if ac1 is not None else None,
            "vr60": round(vr, 3) if vr is not None else None,
            "median_depth_imb": round(_med(imb), 3) if imb else None,
        }
        print(f"{bare}: {rows[bare]}")

    print("\n── 坏币签名对比表 ──")
    print(f"{'币':<6} {'P1edge':>8} {'rel_sp':>8} {'|r15|':>8} {'ofi_ac1':>8} "
          f"{'vr60':>7} {'depth_imb':>10}")
    for sym in ("DOGE", "ETH", "SOL", "BTC", "BNB"):
        r = rows[sym]
        _imb = f"{r['median_depth_imb']:>10.3f}" if r['median_depth_imb'] is not None \
            else f"{'—':>10}"
        print(f"{sym:<6} {r['p1_f120_edge']:>+8.2f} {r['median_rel_spread_bp']:>8.2f} "
              f"{r['median_abs_15s_ret_bp']:>8.2f} {r['ofi_ac1']:>8.3f} "
              f"{r['vr60']:>7.2f} {_imb}")

    OUT.write_text(json.dumps({"hours": a.hours, "symbols": rows},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
