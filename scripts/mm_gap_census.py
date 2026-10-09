# -*- coding: utf-8 -*-
"""[F279 2026-09-16] 数据断点普查：统计行情快照的时间间隔分布。

目的：量化"后端重启 ⇒ 快照断点 ⇒ mid_hist 里出现一条**跨越断点的伪收益** ⇒
已实现波动被抬高 ⇒ `vol_pause` 闸把两个币都站开 ~10 分钟（窗口滚动 20 拍 × 30s）"
这一链路在**今天**到底吃掉了多少报价时间。

输出（按币）：
  · 快照总数 / 覆盖时长 / 实际快照间隔中位数
  · 断点（间隔 > GAP_MIN）个数、累计断点时长、最大断点
  · 伪波动推算：每条断点的 |收益|(bp)、以及按 vol_window=20 估计的"站开分钟数"
"""
from __future__ import annotations

import os
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from backend.core.tenant import system_identity  # noqa: E402
from backend.database.connection import MarketSessionLocal  # noqa: E402

GAP_MIN_SEC = 90          # 超过这个间隔即视为断点（正常 30s 网格）
VOL_WINDOW = 20           # 与参数表 vol_window 一致
VOL_PAUSE_MULT = 0.7      # vol_pause_sigma
CST = timezone(timedelta(hours=8))


def census(symbol: str, since_ms: int, until_ms: int) -> dict:
    with system_identity():
        with MarketSessionLocal() as db:
            rows = db.execute(text(
                "SELECT timestamp, best_bid, best_ask FROM market_orderbook_snapshots"
                " WHERE exchange = :ex AND symbol = :s"
                "   AND timestamp >= :a AND timestamp < :b"
                "   AND best_bid > 0 AND best_ask > best_bid"
                " ORDER BY timestamp"
            ), {"ex": "asterdex", "s": symbol, "a": since_ms, "b": until_ms}).all()
    ts = [int(r[0]) for r in rows]
    mid = [(float(r[1]) + float(r[2])) / 2.0 for r in rows]
    if len(ts) < 3:
        return {"symbol": symbol, "n": len(ts)}
    gaps = [(ts[i] - ts[i - 1], i) for i in range(1, len(ts))]
    step = statistics.median([g for g, _ in gaps])
    breaks = [(g, i) for g, i in gaps if g > GAP_MIN_SEC * 1000]
    # 断点处的伪收益（bp）
    brk_bp = []
    dark_min = 0.0
    for g, i in breaks:
        prev, cur = mid[i - 1], mid[i]
        bp = abs(cur - prev) / prev * 1e4 if prev > 0 else 0.0
        brk_bp.append(round(bp, 2))
        # 该伪收益滚动出窗口需要 VOL_WINDOW 拍（按真实快照节奏换算成分钟）
        dark_min += VOL_WINDOW * (step / 1000.0) / 60.0
    span_min = (ts[-1] - ts[0]) / 60000.0
    return {
        "symbol": symbol,
        "n": len(ts),
        "span_min": round(span_min, 1),
        "step_sec": round(step / 1000.0, 1),
        "breaks": len(breaks),
        "break_min": round(sum(g for g, _ in breaks) / 60000.0, 1),
        "max_break_min": round(max((g for g, _ in breaks), default=0) / 60000.0, 1),
        "break_bp": brk_bp,
        "phantom_dark_min": round(dark_min, 1),
        "dark_pct": round(dark_min / span_min * 100, 1) if span_min > 0 else 0.0,
    }


def main() -> None:
    day = sys.argv[1] if len(sys.argv) > 1 else datetime.now(CST).strftime("%Y-%m-%d")
    d0 = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=CST)
    since = int(d0.timestamp() * 1000)
    until = int((d0 + timedelta(days=1)).timestamp() * 1000)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    until = min(until, now_ms)

    print(f"[gap census] day={day} window={d0:%Y-%m-%d %H:%M}~"
          f"{datetime.fromtimestamp(until/1000, CST):%H:%M} (CST)")
    print(f"{'sym':<5}{'n':>6}{'span_m':>8}{'step_s':>7}{'brk':>5}"
          f"{'brk_min':>8}{'max_m':>7}{'dark_m':>8}{'dark%':>7}")
    for sym in ("BTC", "ETH"):
        r = census(sym, since, until)
        if r.get("n", 0) < 3:
            print(f"{sym:<5} 数据不足 ({r.get('n')})")
            continue
        print(f"{r['symbol']:<5}{r['n']:>6}{r['span_min']:>8}{r['step_sec']:>7}"
              f"{r['breaks']:>5}{r['break_min']:>8}{r['max_break_min']:>7}"
              f"{r['phantom_dark_min']:>8}{r['dark_pct']:>7}")
        if r["break_bp"]:
            print(f"      断点伪收益(bp): {r['break_bp']}")


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    main()
