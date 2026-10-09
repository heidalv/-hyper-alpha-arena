# -*- coding: utf-8 -*-
"""H307 逆失衡确认入场（文献第 2 推荐，本会话尚未测过）：OFI 衰竭才逆势。

# 依据：Albers et al.（arXiv:2502.18625）——maker 成交概率与成交后收益负相关，
# 胜利路径 = 在"盘口失衡将错误预测下一跳"的 Reversal 状态、逆失衡方向挂单。
# 实盘问题：maker 腿 price_bp 持续为负（入场被逆向选择）。
# 本脚本：同一衰减出场（P3）下，对比
#   BASE   逆 60s 趋势（现行）
#   OFI_CFM  逆 60s 趋势 且 最近 15s 桶 OFI 已衰竭：
#            OFI 与趋势同向（顺势流还在推）→ 不进场；
#            只进 OFI 反向（流已回头）或 |OFI|<0.15（流安静）的场。
# 数据：book 1s mid + market_trades_aggregated 15s 桶（taker buy/sell notional）。

# 用法

    python scripts/h307_ofi_confirmation.py --hours 168
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h307_ofi_confirmation.json"
CUR = ["SOL", "DOGE", "ETH", "BNB"]


def market_dsn() -> str:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    base = env["DATABASE_URL"]
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        base = base.replace(j, "")
    return base.rsplit("/", 1)[0] + "/alpha_market"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=168.0)
    ap.add_argument("--ago", type=float, default=0.0, help="窗口前移小时数（交叉验证）")
    ap.add_argument("--lookback", type=float, default=60.0)
    ap.add_argument("--thr", type=float, default=2.0)
    ap.add_argument("--decay", type=float, default=4.0)
    a = ap.parse_args()

    import psycopg

    mids_series = {}
    ofi_map = {}   # sym -> {15s_bucket_key: ofi}
    for sym in CUR:
        with psycopg.connect(market_dsn()) as c:
            with c.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT ON (bucket) bucket, bid_px, ask_px
                    FROM (SELECT (event_ts_ms/1000) AS bucket, bid_px, ask_px
                          FROM asterdex_book_ticker
                          WHERE event_ts_ms >= (extract(epoch from now())*1000 - (%s+%s)*3600*1000)::bigint
                            AND event_ts_ms <  (extract(epoch from now())*1000 - %s*3600*1000)::bigint
                            AND symbol = %s AND bid_px>0 AND ask_px>bid_px) t
                    ORDER BY bucket, bid_px
                """, (a.hours, a.ago, a.ago, sym + "USDT"))
                recs = cur.fetchall()
        d = {int(b): ((float(x) + float(y)) / 2.0) for b, x, y in recs}
        ks = sorted(d)
        mids_series[sym] = (ks, [d[k] for k in ks])
        # OFI 桶（15s，毫秒时间戳；symbol 为裸符号）
        with psycopg.connect(market_dsn()) as c:
            with c.cursor() as cur:
                cur.execute("""
                    SELECT timestamp, taker_buy_notional, taker_sell_notional
                    FROM market_trades_aggregated
                    WHERE symbol=%s
                      AND timestamp >= (extract(epoch from now())*1000 - (%s+%s)*3600*1000)::bigint
                      AND timestamp <  (extract(epoch from now())*1000 - %s*3600*1000)::bigint
                    ORDER BY timestamp
                """, (sym, a.hours, a.ago, a.ago))
                recs2 = cur.fetchall()
        ofi_map[sym] = {}
        for ts_ms, buy_n, sell_n in recs2:
            b = int(ts_ms) // 15000
            tot = float(buy_n or 0) + float(sell_n or 0)
            ofi_map[sym][b] = (float(buy_n or 0) - float(sell_n or 0)) / tot if tot > 0 else 0.0
        print(f"  {sym:6} mid {len(ks)} 点  ofi 桶 {len(ofi_map[sym])}", flush=True)

    def sim(use_ofi: bool):
        stats = {"n": 0, "net_bp": [], "mae_bp": [], "blocked": 0, "exits": {}}
        for sym, (ks, px) in mids_series.items():
            n = len(ks)
            last = -1e18
            for i in range(n):
                if ks[i] - last < 60.0:
                    continue
                last = ks[i]
                j = i
                while j >= 0 and ks[i] - ks[j] < a.lookback:
                    j -= 1
                if j < 0 or ks[i] - ks[j] < a.lookback * 0.9 or px[j] <= 0:
                    continue
                rlb = (px[i] - px[j]) / px[j] * 1e4
                if abs(rlb) < a.thr:
                    continue
                if use_ofi:
                    b = ks[i] // 15
                    ofi = ofi_map[sym].get(b, 0.0)
                    # 流衰竭：OFI 与趋势同向（还在推）→ 不进；反向或安静才进
                    if rlb > 0 and ofi > 0.15:
                        stats["blocked"] += 1
                        continue
                    if rlb < 0 and ofi < -0.15:
                        stats["blocked"] += 1
                        continue
                sign = -1.0 if rlb > 0 else 1.0
                entry_px = px[i]
                t = i
                exit_bp = None
                exit_type = "timeout"
                while t + 1 < n and ks[t + 1] - ks[i] <= 120.0:
                    t += 1
                    mv = (px[t] - entry_px) / entry_px * 1e4 * sign
                    if mv >= 5.0:
                        exit_type = "tp"
                        exit_bp = mv
                        break
                    jj = t
                    while jj >= 0 and ks[t] - ks[jj] < 30.0:
                        jj -= 1
                    if jj >= 0 and px[jj] > 0:
                        r30 = (px[t] - px[jj]) / px[jj] * 1e4
                        if r30 * sign <= -a.decay:
                            exit_type = "decay"
                            exit_bp = mv
                            break
                    if ks[t] - ks[i] >= 120.0:
                        exit_type = "timeout"
                        exit_bp = mv
                        break
                if exit_bp is None:
                    exit_bp = (px[t] - entry_px) / entry_px * 1e4 * sign
                fee = 4.0 if exit_type == "tp" else 0.0
                stats["n"] += 1
                stats["net_bp"].append(exit_bp - fee)
                mae = 0.0
                for tt in range(i, t + 1):
                    mv = (px[tt] - entry_px) / entry_px * 1e4 * sign
                    if mv < mae:
                        mae = mv
                stats["mae_bp"].append(mae)
                stats["exits"][exit_type] = stats["exits"].get(exit_type, 0) + 1
        net = stats["net_bp"]
        return {"n": stats["n"], "blocked": stats["blocked"],
                "net_mean_bp": round(sum(net) / len(net), 3) if net else None,
                "mae_mean_bp": round(sum(stats["mae_bp"]) / len(stats["mae_bp"]), 3) if stats["mae_bp"] else None,
                "exits": stats["exits"]}

    base = sim(use_ofi=False)
    cfm = sim(use_ofi=True)
    print(f"\n  BASE（逆 60s 趋势）: {base}")
    print(f"  OFI_CFM（加流衰竭确认）: {cfm}")
    OUT.write_text(json.dumps({"base": base, "ofi_cfm": cfm}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\n  已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
