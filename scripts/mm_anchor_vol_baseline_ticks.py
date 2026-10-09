"""补齐车道波动基准：对 15s 快照表**缺数据**的币，改用 tick 数据按同网格重采样。

## 为什么必须补

`runner.py:2078-2079`：

    sigma = max(0.0, vol_cur / st.vol_baseline_bp - 1.0) if st.vol_baseline_bp > 0 else 0.0

⇒ 基准 = 0 时 **σ 恒为 0**，`vol_pause`（σ > vol_pause_sigma 才暂停）对**该币永不触发**。
这是已知的公平性坑：换宇宙后新币没有锚定基准，等于给它们开了免暂停特权，
使对照实验不可比（2026-09-17 已因此得出过一次错误结论）。

## 为什么不能用 tick 直接算，而要按 15s 重采样

运行时的 `mid_hist` 是**每 15s 快照追加一条**（`runner.py:2037-2053`），
`realized_vol_bp` / `compute_vol_baselines` 的"窗口 20 期"= 20×15s = 5 分钟。
若基准用 tick 的 20×36ms = 0.72 秒窗口算，两者尺度差 200 倍，σ 会完全失去意义。

⇒ 本脚本把 `asterdex_book_ticker` 的 mid **按 15s 网格取末值**，再喂给**同一个**
`compute_vol_baselines`（单一实现，口径与回放/实盘一致）。

对快照表里**已有**数据的币，两套都算出来以便对照；最终写入时优先 tick 版
（tick 表无采集空洞，比 15s 快照更稠密）。

用法：
    python scripts/mm_anchor_vol_baseline_ticks.py --dry-run
    python scripts/mm_anchor_vol_baseline_ticks.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO / ".env", override=False)

from backend.services import lane_registry as reg  # noqa: E402
from backend.services.market_maker.portfolio_replay import (  # noqa: E402
    _load_all, compute_vol_baselines,
)

GRID_MS = 15_000


def _market_conn():
    import psycopg2

    url = os.environ["DATABASE_URL"]
    for d in ("+psycopg2", "+psycopg", "+asyncpg"):
        url = url.replace(d, "")
    head, _, _ = url.rpartition("/")
    cn = psycopg2.connect(head + "/alpha_market")
    cn.autocommit = True
    return cn


def mids_on_15s_grid(venue_symbol: str, since_ms: int) -> list:
    """把 book_ticker 的 mid 采样到 15s 网格（每格取末值）。"""
    cn = _market_conn()
    cur = cn.cursor()
    cur.execute(
        "select event_ts_ms, (bid_px + ask_px)/2 from asterdex_book_ticker "
        "where symbol=%s and event_ts_ms >= %s order by event_ts_ms",
        (venue_symbol, since_ms),
    )
    buckets = defaultdict(list)   # grid_id -> [last_ts, last_mid]
    for ts, mid in cur.fetchall():
        try:
            m = float(mid)
        except (TypeError, ValueError):
            continue
        if m <= 0:
            continue
        g = int(ts) // GRID_MS
        prev = buckets.get(g)
        if prev is None or ts >= prev[0]:
            buckets[g] = (int(ts), m)
    cn.close()
    return [buckets[g][1] for g in sorted(buckets)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lane", default="mm_asterdex")
    ap.add_argument("--days", type=float, default=14.0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    lane = reg.get_lane(args.lane)
    if not lane:
        print(f"车道不存在: {args.lane}")
        return 1
    meta = dict(lane.get("meta") or {})
    symbols = [str(s).upper() for s in (meta.get("symbols") or [])]
    if not symbols:
        print("车道未配置 symbols")
        return 1
    venue = str(meta.get("venue") or "asterdex")

    since_ms = int(datetime.now(timezone.utc).timestamp() * 1000) - int(args.days * 86400_000)
    print(f"车道 {args.lane}  币种 {len(symbols)}  窗口 {args.days} 天  网格 {GRID_MS//1000}s")
    print()

    # ① 快照口径（现有脚本用的）
    try:
        data = _load_all(symbols, venue)
        snap_series = {}
        for s in symbols:
            d = data.get(s) or {}
            bb, ba = d.get("bb"), d.get("ba")
            if bb is None or ba is None:
                snap_series[s] = []
                continue
            snap_series[s] = [float((bb[i] + ba[i]) / 2) for i in range(len(bb))
                              if float((bb[i] + ba[i]) / 2) > 0]
        snap_base = compute_vol_baselines(snap_series)
    except Exception as e:  # noqa: BLE001
        print(f"  快照口径失败（忽略）: {e}")
        snap_series, snap_base = {}, {}

    # ② tick 口径（15s 重采样）
    tick_series, tick_base = {}, {}
    for s in symbols:
        ser = mids_on_15s_grid(f"{s}USDT", since_ms)
        tick_series[s] = ser
        tick_base[s] = 0.0
    tick_base = compute_vol_baselines(tick_series)

    old = dict((meta.get("replay_baseline") or {}).get("vol_baseline_bp") or {})
    print(f"{'币':<10}{'现值':>9}{'快照口径':>10}{'tick口径':>10}{'采用':>10}{'样本(快照/tick)':>18}")
    # ⚠️ `final` 必须从**空**开始，不能 `dict(old)`：
    #   `old` 里可能残留**已移出宇宙**的币（实测 TAO/ZEC），复制过来会让它们继续
    #   出现在基准表里 —— 读的人会以为宇宙里还有这些币（幽灵标的）。
    final: dict = {}
    for s in symbols:
        o = float(old.get(s) or 0.0)
        sb = float(snap_base.get(s) or 0.0)
        tb = float(tick_base.get(s) or 0.0)
        use = tb if tb > 0 else sb
        src = "tick" if tb > 0 else ("snapshot" if sb > 0 else "无")
        print(f"{s:<10}{o:>9.4f}{sb:>10.4f}{tb:>10.4f}{use:>10.4f}"
              f"{f'{len(snap_series.get(s) or [])}/{len(tick_series.get(s) or [])}':>18}")
        if use > 0:
            final[s] = round(use, 4)

    missing = [s for s in symbols if float(final.get(s) or 0.0) <= 0]
    print()
    if missing:
        print(f"!! 仍无基准的币: {missing}")
    if args.dry_run:
        print("\n（dry-run）未写入")
        return 0 if not missing else 1

    # ⚠️ 重建整个 replay_baseline 块，而不是往旧块里塞：
    #   旧块会残留**已移出宇宙**的币（实测有 TAO/ZEC）与失效备注
    #   （"需要 market_orderbook_snapshots 覆盖新币"——本脚本已用 tick 口径解决）。
    #   残留会让下一次读数的人以为宇宙里还有那些币。
    meta["replay_baseline"] = {
        "vol_baseline_bp": final,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "symbols": len(final),
        "window_days": float(args.days),
        "source": "tick_15s_grid+snapshot",
        "method": ("mid 按 15s 网格取末值 → compute_vol_baselines(window=20) "
                   "→ 20×15s=5min 窗口的 realized_vol_bp 中位；"
                   "与 runner 的 mid_hist 同网格"),
        "note": ("基准=0 会让 σ 恒为 0 ⇒ vol_pause 对该币永不触发（己知公平性坑）。"
                 "快照表无数据的币由 tick 口径补齐。"),
    }
    reg.update_meta(args.lane, meta)
    print(f"\n已写入 {args.lane} 的 replay_baseline.vol_baseline_bp（{len(final)} 币）")
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
