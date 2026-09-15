"""离线计算「冻结档命中占比」：实盘报价到底有多少时间走的是 frozen 分支。

[F205b 2026-09-15] 为什么要离线算
--------------------------------------------------
F189 的部署失败（−$63.8）我当时的解释是"模型以为挂 ~13bp，实盘因冻结档只挂 5.5/4.7bp" ✗ ——
但这句话其实**没有证据**：模型从未声称过 13bp，那是我从 `w_base_bp=12` 推的 ✗。
要判断"实盘与模型在冻结档上是否分叉"，必须真的把这个占比量出来 ✓。

判据与 `core.compute_quote` 完全一致（**同一套规则**，避免双口径）：
    frozen  ⟺  frozen_width_bp>0  且  0 < slow_range_bp < frozen_max_move_bp
    slow_range_bp = 近 `frozen_lookback` 期快照中价的 **max|Δmid|/mid × 1e4**（单步最大移动）
（注意不是"全幅"：F80 的注释说明冻结行情是"±1-2bp 高频往返"，全幅指标在漂移型冻结下
永远超阈值 ✗。）

输出：逐币与合并的 frozen 占比、slow_range 中位数，以及**按小时**的占比
（用来对照 F196 那 1.6 小时亏损窗口内的占比 ✓）。
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
from sqlalchemy import text  # noqa: E402

from backend.database.connection import MarketSessionLocal, SessionLocal  # noqa: E402

SYMS = ["BTC", "ETH", "BNB", "XRP", "SOL"]
TZ = timezone(timedelta(hours=8))


def _a(sql: str, **p) -> List[dict]:
    with SessionLocal() as s:
        s.execute(text("SET statement_timeout = 60000"))
        return [dict(r) for r in s.execute(text(sql), p).mappings().all()]


def main() -> int:
    lane = os.getenv("MM_LANE", "mm_asterdex")
    since = sys.argv[1] if len(sys.argv) > 1 else "2026-09-15T08:00:00+08:00"
    since_dt = datetime.fromisoformat(since)

    meta = {}
    for r in _a("SELECT meta_json FROM lane_registry WHERE lane_id=:l", l=lane):
        meta = r["meta_json"] or {}
    p = (meta.get("params") or {})
    fw = float(p.get("frozen_width_bp") or 0.0)
    fmv = float(p.get("frozen_max_move_bp") or 0.0)
    flb = int(p.get("frozen_lookback") or 120)
    print(f"冻结档离线占比 · {lane} · 起点 {since}")
    print(f"  参数：frozen_width_bp={fw} frozen_max_move_bp={fmv} frozen_lookback={flb} "
          f"w_base_bp={p.get('w_base_bp')} k_vol={p.get('k_vol')}")
    if fw <= 0:
        print("  ⇒ frozen_width_bp<=0（冻结档已关闭）⇒ 占比恒为 0 ✓")
        return 0

    lo = int(since_dt.timestamp() * 1000)
    hourly: Dict[str, List[int]] = defaultdict(list)
    tot_f = tot_n = 0
    print(f"\n  {'币':<6}{'样本':>8}{'frozen占比':>11}{'slow_range中位':>15}{'p90':>8}")
    for s in SYMS:
        rows = []
        with MarketSessionLocal() as ses:
            ses.execute(text("SET statement_timeout = 60000"))
            for r in ses.execute(text(
                    """SELECT timestamp, best_bid, best_ask FROM market_orderbook_snapshots
                       WHERE symbol=:s AND timestamp >= :a ORDER BY timestamp"""),
                    {"s": s, "a": lo}).mappings().all():
                if r["best_bid"] and r["best_ask"]:
                    rows.append((int(r["timestamp"]),
                                 (float(r["best_bid"]) + float(r["best_ask"])) / 2))
        if len(rows) < flb + 5:
            print(f"  {s:<6}{len(rows):>8}  样本不足")
            continue
        mids = np.array([m for _t, m in rows])
        d = np.abs(np.diff(mids) / np.maximum(mids[:-1], 1e-9)) * 1e4      # 单步 |Δmid| bp
        # slow_range_bp[i] = 最近 flb 个单步移动的最大值（用截至 i 的窗口）
        sr = np.full(len(mids), np.nan)
        for i in range(flb, len(d) + 1):
            sr[i] = float(np.max(d[max(0, i - flb):i]))
        valid = np.isfinite(sr)
        fr = valid & (sr > 0) & (sr < fmv)
        n = int(valid.sum())
        f = int(fr.sum())
        tot_f += f
        tot_n += n
        print(f"  {s:<6}{n:>8}{100.0*f/max(1,n):>10.1f}%{np.nanmedian(sr):>15.2f}"
              f"{np.nanpercentile(sr, 90):>8.2f}")
        # 按小时
        for (t, _m), is_v, is_f in zip(rows, valid, fr):
            if not is_v:
                continue
            h = datetime.fromtimestamp(t / 1000, tz=TZ).strftime("%m-%d %H:00")
            hourly[h].append(1 if is_f else 0)
    if tot_n:
        print(f"  {'合并':<6}{tot_n:>8}{100.0*tot_f/tot_n:>10.1f}%")

    print("\n【按小时】frozen 占比（对照 F196 亏损窗口 09-15 12:57~14:02）")
    for h in sorted(hourly):
        v = hourly[h]
        bar = "#" * int(40 * sum(v) / len(v))
        mark = "  ← F196 亏损窗口" if h.startswith("09-15 13") else ""
        print(f"  {h}  {100.0*sum(v)/len(v):>5.1f}%  {bar}{mark}")
    print("\n读法：占比高 ⇒ 冻结档主导报价（`w_base_bp` 基本不参与）⇒ "
          "在这种时段里改 `w_base_bp` **不会改变实盘行为** ✗（F189 的坑）；"
          "要改的是 `frozen_width_bp`/`frozen_max_move_bp` ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
