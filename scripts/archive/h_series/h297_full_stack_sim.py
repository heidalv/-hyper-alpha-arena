# -*- coding: utf-8 -*-
"""H297 全栈模拟盘测验：模型入场（60s+θ）+ regime 门 + 衰减出场，7 天完整回放。

# 与 h284 的区别：h284 只有入场过滤 + 出场政策；本脚本把已部署的三道
# regime 门也纳入（它们是线上真实在跑的开关）：
    G1 trend_pause：|r300| ≥ 20bp 跳过（动量 regime 不逆势）
    G2 sudden_move：入场前 60s 内任一 1s 移动 ≥ 20bp 跳过（急动不接刀）
    G3 vol 门：σ90 > 6bp 跳过（高波动反转弱；proxy 用绝对 bp，避免基准标定）
# 出场：反转衰减 4bp（P3）+ 超时 120s + TP5（taker 4bp）。入场 maker 0 费。
# 对比：同数据不带门的 P3 基线。

# 用法

    python scripts/h297_full_stack_sim.py --hours 168
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h297_full_stack.json"
CUR = ["SOLUSDT", "DOGEUSDT", "ETHUSDT", "BNBUSDT"]


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
    ap.add_argument("--lookback", type=float, default=60.0)
    ap.add_argument("--thr", type=float, default=3.0)
    ap.add_argument("--decay", type=float, default=4.0)
    a = ap.parse_args()

    import psycopg

    series = {}
    for sym in CUR:
        with psycopg.connect(market_dsn()) as c:
            with c.cursor() as cur:
                cur.execute("""
                    SELECT DISTINCT ON (bucket) bucket, bid_px, ask_px
                    FROM (SELECT (event_ts_ms/1000) AS bucket, bid_px, ask_px
                          FROM asterdex_book_ticker
                          WHERE event_ts_ms >= (extract(epoch from now())*1000 - %s*3600*1000)::bigint
                            AND symbol = %s AND bid_px>0 AND ask_px>bid_px) t
                    ORDER BY bucket, bid_px
                """, (a.hours, sym))
                recs = cur.fetchall()
        d = {int(b): ((float(x) + float(y)) / 2.0) for b, x, y in recs}
        ks = sorted(d)
        series[sym] = (ks, [d[k] for k in ks])
        print(f"  {sym:10} {len(ks)} 点", flush=True)

    def sim(with_gates: bool):
        stats = {"n": 0, "net_bp": [], "mae_bp": [], "gated": 0, "exits": {}}
        for sym, (ks, px) in series.items():
            n = len(ks)
            last = -1e18
            for i in range(n):
                if ks[i] - last < 60.0:
                    continue
                last = ks[i]
                # 回看收益
                j = i
                while j >= 0 and ks[i] - ks[j] < a.lookback:
                    j -= 1
                if j < 0 or ks[i] - ks[j] < a.lookback * 0.9 or px[j] <= 0:
                    continue
                rlb = (px[i] - px[j]) / px[j] * 1e4
                if abs(rlb) < a.thr:
                    continue
                # regime 门（与线上实际生效的开关一致）
                if with_gates:
                    # G1 trend_pause：|r300| ≥ 20bp 时封**逆势侧**加仓
                    # （h324：涨禁卖、跌禁买；F204~h324 之间曾是"禁顺势侧"）
                    # 注：线上 vol_pause_mult=0（单币波动门关闭）、sudden_move 用 1 期 15s 口径，
                    #     回放里不再复刻这两个（避免 99.5% 误拦——上一版教训）
                    j300 = i
                    while j300 >= 0 and ks[i] - ks[j300] < 300.0:
                        j300 -= 1
                    if j300 >= 0 and px[j300] > 0:
                        _trend300 = (px[i] - px[j300]) / px[j300] * 1e4
                        _side = "sell" if rlb > 0 else "buy"
                        _counter = ((_side == "sell" and _trend300 > 0)
                                    or (_side == "buy" and _trend300 < 0))
                        if abs(_trend300) >= 20.0 and _counter:
                            stats["gated"] += 1
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
                    # 衰减：30s 趋势反向延伸 ≥ decay
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
        return {
            "n": stats["n"], "gated": stats["gated"],
            "net_mean_bp": round(sum(net) / len(net), 3) if net else None,
            "mae_mean_bp": round(sum(stats["mae_bp"]) / len(stats["mae_bp"]), 3) if stats["mae_bp"] else None,
            "exits": stats["exits"],
        }

    base = sim(with_gates=False)
    gated = sim(with_gates=True)
    print(f"\n  7 天全栈测验（lookback {a.lookback:.0f}s / θ{a.thr:.0f}bp / decay{a.decay:.0f}bp）")
    print(f"  不带门 : {base}")
    print(f"  带三道门: {gated}")
    OUT.write_text(json.dumps({"base": base, "gated": gated}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\n  已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
