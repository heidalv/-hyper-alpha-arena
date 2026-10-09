"""诊断：为什么高频车道「完全没有交易」。

## 直接结论（先看数）
24 小时内账本只有 **2 笔**成交（都在 ARB）：
    16:24:57  spread +31.57 / price   0.00  → net +31.57bp  ✅ 完美赚到点差
    17:25:09  spread  −4.90 / price −74.63  → net −83.53bp  ❌ 逆选择
而报价决策 **3964 次** ⇒ 成交率 **0.05%**。

阻断统计（累计 609 tick）：
    ct_trend_up 1234 / trend_up 1038 / trend_down 811 / ct_trend_down 808  = 3891 次趋势闸
    net_exposure 847 / gross_exposure 324                                  = 1171 次敞口闸
    vol_pause 392 / ofi_toxic 136
    side_counts: both 500 / one 3464 / none 2126   ← **87% 的决策只挂单边或完全不挂**

## 本脚本量什么
逐币算「近 15 分钟净移动(bp)」，与趋势闸阈值 `trend_pause_bp`(=15) 对照，
从而判断：**是闸门把整侧封死，还是行情本身没有机会**。

## 口径
`trend_move_bp(mid_hist, lookback)` 用的是 `mid_hist`（每 15s 快照一条，
`runner.py:2037-2053`），lookback=60 ⇒ 窗口 ≈ 15 分钟。此处用
`asterdex_book_ticker` 按同一窗口近似（tick 更稠密，方向一致）。
"""
from __future__ import annotations

import datetime
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]      # scripts/ 的上一级 = 仓库根
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env", override=False)

import psycopg2  # noqa: E402


def conn(db):
    url = os.environ["DATABASE_URL"]
    for d in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(d, "")
    head, _, _ = url.rpartition("/")
    cn = psycopg2.connect(head + f"/{db}")
    cn.autocommit = True
    return cn


def main() -> int:
    reg = conn("alpha_arena")
    rc = reg.cursor()
    rc.execute("select meta_json from lane_registry where lane_id='mm_asterdex'")
    raw = rc.fetchone()[0]
    meta = raw if isinstance(raw, dict) else json.loads(raw)
    syms = meta.get("symbols") or []
    params = meta.get("params") or {}
    thr = float(params.get("trend_pause_bp") or 0)
    lookback = int(params.get("trend_skew_lookback") or 60)
    side_mode = params.get("side_mode")

    print(f"宇宙 {len(syms)} 币   side_mode={side_mode}")
    print(f"趋势闸阈值 trend_pause_bp={thr}bp   回看 {lookback} 期 ≈ {lookback*15/60:.0f} 分钟")
    print()

    mkt = conn("alpha_market")
    now_ms = int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000)
    win_ms = int(lookback * 15_000)

    print(f"{'symbol':<10}{'15min移动bp':>13}{'1h移动bp':>11}{'点差bp':>9}   趋势闸判定")
    print("-" * 68)
    blocked_buy = blocked_sell = free = 0
    for s in syms:
        cur = mkt.cursor()
        cur.execute(
            "select event_ts_ms, (bid_px+ask_px)/2, (ask_px-bid_px) from asterdex_book_ticker "
            "where symbol=%s and event_ts_ms > %s order by event_ts_ms",
            (f"{s}USDT", now_ms - 3600 * 1000),
        )
        rows = cur.fetchall()
        if not rows:
            print(f"{s:<10}{'无数据':>13}")
            continue
        mids = [float(r[1]) for r in rows if r[1]]
        ts = [int(r[0]) for r in rows if r[1]]
        sprs = [(float(r[2]) / float(r[1]) * 1e4) for r in rows if r[1] and r[2]]
        if not mids:
            continue
        t_win = now_ms - win_ms
        i_win = next((i for i, t in enumerate(ts) if t >= t_win), 0)
        mv = (mids[-1] / mids[i_win] - 1) * 1e4
        mv60 = (mids[-1] / mids[0] - 1) * 1e4
        spr = sorted(sprs)[len(sprs) // 2] if sprs else float("nan")

        if thr <= 0:
            verdict = "闸关闭"
            free += 1
        elif mv >= thr:
            verdict = f"禁买（涨 {mv:.0f}bp ≥ {thr:.0f}）"
            blocked_buy += 1
        elif mv <= -thr:
            verdict = f"禁卖（跌 {mv:.0f}bp ≤ -{thr:.0f}）"
            blocked_sell += 1
        else:
            verdict = "双侧可挂"
            free += 1
        print(f"{s:<10}{mv:>13.1f}{mv60:>11.1f}{spr:>9.2f}   {verdict}")

    print()
    print(f"汇总：禁买 {blocked_buy} 币 / 禁卖 {blocked_sell} 币 / 双侧可挂 {free} 币")
    if blocked_buy + blocked_sell > len(syms) * 0.5:
        print("⇒ **趋势闸封掉了半数以上标的的一侧**，叠加 side_mode=counter_trend "
              "⇒ 实际可挂单侧极少 ⇒ 成交率被结构性压到接近 0。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
