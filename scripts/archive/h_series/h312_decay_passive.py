# -*- coding: utf-8 -*-
"""H312 衰减触发后的被动出场研究：等反弹（maker 0 费） vs 立即 taker。

# 用户指令：手续费是最大开销 → 提高被动交易。
# 衰减腿现状：触发后 8s maker 宽限 → taker 4bp，实测平均 −13~−18bp。
# F296 经验：超时腿 96.1% 能在 120s 内被动出库（价格会回来）。
# 问题：衰减触发（30s 趋势反向延伸 ≥3bp）后，价格回头的概率/深度如何？
# 若回弹率高，衰减出场应改成"等回弹 maker 出（如超时同款），taker 只留给 40bp 兜底"。

# 方法：7 天窗口、现行入场（θ2 + OFI 无），每次衰减触发记录触发时 P&L(mv_t)，
# 以及其后 300s 内：最差漂移、60/120/300s 时 P&L、是否回到 ≥−1bp。
# 对比三种出场策略的期望：
#   TAKER   触发即走（mv_t − 4bp 费）
#   BOUNCE60 60s 内回到 ≥−1bp → 0 费出；否则 taker（min(mv_t, mv_t+60s) − 4）
#   BOUNCE300 300s 内回到 ≥−1bp → 0 费出；否则按 300s 时 P&L（0 费）出
# （被动成交条件用"价格回到触发价"作保守代理；真实减仓侧 maker 单在盘口被动成交
#   比这个更容易，因为对手方打过来就能成交。）

# 用法

    python scripts/h312_decay_passive.py --hours 168
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "research_l1" / "out" / "h312_decay_passive.json"
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
    a = ap.parse_args()

    import psycopg

    mids_series = {}
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
                """, (a.hours, sym + "USDT"))
                recs = cur.fetchall()
        d = {int(b): ((float(x) + float(y)) / 2.0) for b, x, y in recs}
        ks = sorted(d)
        mids_series[sym] = (ks, [d[k] for k in ks])
        print(f"  {sym:6} {len(ks)} 点", flush=True)

    events = []  # 每个衰减触发
    for sym, (ks, px) in mids_series.items():
        n = len(ks)
        last = -1e18
        for i in range(n):
            if ks[i] - last < 60.0:
                continue
            last = ks[i]
            j = i
            while j >= 0 and ks[i] - ks[j] < 60.0:
                j -= 1
            if j < 0 or ks[i] - ks[j] < 54 or px[j] <= 0:
                continue
            r60 = (px[i] - px[j]) / px[j] * 1e4
            if abs(r60) < 2.0:
                continue
            sign = -1.0 if r60 > 0 else 1.0
            entry_px = px[i]
            t = i
            triggered = False
            while t + 1 < n and ks[t + 1] - ks[i] <= 300.0:
                t += 1
                mv = (px[t] - entry_px) / entry_px * 1e4 * sign
                if mv >= 12.0:
                    break  # 到止盈了，不算衰减样本
                jj = t
                while jj >= 0 and ks[t] - ks[jj] < 30.0:
                    jj -= 1
                if jj >= 0 and px[jj] > 0:
                    r30 = (px[t] - px[jj]) / px[jj] * 1e4
                    if r30 * sign <= -3.0:
                        triggered = True
                        break
            if not triggered:
                continue
            # 触发时刻 t：mv_t；其后路径
            mv_t = (px[t] - entry_px) / entry_px * 1e4 * sign
            worst = mv_t
            rec = False
            rec60 = False
            rec300 = False
            mv60 = mv300 = mv_t
            t60 = t300 = t
            for tt in range(t + 1, min(n, t + 301)):
                dt = ks[tt] - ks[t]
                if dt > 300:
                    break
                mv = (px[tt] - entry_px) / entry_px * 1e4 * sign
                if mv < worst:
                    worst = mv
                if dt <= 60:
                    mv60 = mv
                    t60 = tt
                mv300 = mv
                t300 = tt
                if not rec and mv >= -1.0:
                    rec = True
                    if ks[tt] - ks[t] <= 60:
                        rec60 = True
                    rec300 = True
            events.append({"mv_t": mv_t, "worst": worst, "mv60": mv60, "mv300": mv300,
                           "rec60": rec60, "rec300": rec300, "rec": rec})

    n = len(events)
    if not n:
        print("  无衰减样本")
        return 1
    print(f"\n  衰减触发样本: {n}")
    import statistics as st

    def mean(xs):
        return sum(xs) / len(xs)

    mv_t = [e["mv_t"] for e in events]
    worst = [e["worst"] for e in events]
    mv60 = [e["mv60"] for e in events]
    mv300 = [e["mv300"] for e in events]
    rec_rate = sum(1 for e in events if e["rec"]) / n
    rec60_rate = sum(1 for e in events if e["rec60"]) / n

    taker = mean(mv_t) - 4.0
    # BOUNCE60：60s 内回 ≥−1 → 按 −0.5（≈0 费在触发价附近出）；否则 taker at min(mv_t, mv60)
    bounce60 = mean([-0.5 if e["rec60"] else (min(e["mv_t"], e["mv60"]) - 4.0) for e in events])
    # BOUNCE300：300s 内回 ≥−1 → −0.5；否则 300s 时 P&L（0 费 maker 假设）
    bounce300 = mean([-0.5 if e["rec300"] else e["mv300"] for e in events])

    print(f"\n  触发时 P&L 均值: {mean(mv_t):+.2f}bp   最差漂移均值: {mean(worst):+.2f}bp")
    print(f"  60s 内回到 ≥−1bp 的概率: {rec60_rate:.0%}   300s 内: {rec_rate:.0%}")
    print(f"  t+60s P&L 均值: {mean(mv60):+.2f}bp   t+300s P&L 均值: {mean(mv300):+.2f}bp")
    print(f"\n  出场策略期望:")
    print(f"    TAKER(触发即走,4bp费)      : {taker:+.2f}bp/腿")
    print(f"    BOUNCE60(等60s,否则taker)  : {bounce60:+.2f}bp/腿")
    print(f"    BOUNCE300(等300s,0费出)    : {bounce300:+.2f}bp/腿")

    OUT.write_text(json.dumps({
        "n": n, "mv_t_mean": round(mean(mv_t), 3), "worst_mean": round(mean(worst), 3),
        "rec60_rate": round(rec60_rate, 4), "rec300_rate": round(rec_rate, 4),
        "mv60_mean": round(mean(mv60), 3), "mv300_mean": round(mean(mv300), 3),
        "policy_taker": round(taker, 3), "policy_bounce60": round(bounce60, 3),
        "policy_bounce300": round(bounce300, 3),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  已存 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
