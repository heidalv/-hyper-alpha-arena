# -*- coding: utf-8 -*-
"""[交易分析 R11] min_roi_decay「拿久点更好」到底是规则问题还是市场 beta？——阴性对照设计。

方法：对**两类退出**（min_roi_decay / sl）做同一套"继续持有 +H 小时"反事实：
  - 检查期间是否触及原 SL（触及按 SL 成交）；
  - 同时统计标的自身在这段窗口的涨跌幅（beta 指示器）。
判据（预写死）：
  - 若 **sl 组也大幅改善** ⇒ "拿久点更好"是市场 beta（采样期上涨），不构成对 min_roi_decay 的指控；
  - 若 sl 组不改善而 min_roi_decay 组显著改善 ⇒ 该退出规则确实砍早了；反之亦然。
"""
from __future__ import annotations

import datetime as _dt
import sys

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")

from sqlalchemy import text  # noqa: E402

from backend.database.connection import SessionLocal, market_engine  # noqa: E402

TBL, PCOL, TCOL, CCOL, LCOL = "crypto_klines", "period", "timestamp", "close_price", "low_price"


def mq(sql, **kw):
    with market_engine.connect() as c:
        return c.execute(text(sql), kw).fetchall()


# [R11 修复] crypto_klines 有 `exchange` 列：不过滤会把多个交易所的价格混在一起
# （bars[0] 与 bars[-1] 来自不同 venue ⇒ 凭空"上涨"）。先取活跃交易所，再全程过滤。
_EX = "binance"
try:
    _r = mq("SELECT exchange, count(*) FROM crypto_klines WHERE period='1h' "
            "GROUP BY exchange ORDER BY 2 DESC LIMIT 3")
    print("1h K 线按交易所:", [(x[0], x[1]) for x in _r])
    if _r:
        _EX = _r[0][0]
except Exception as e:  # noqa: BLE001
    print("交易所探测失败:", str(e)[:80])
print("使用交易所:", _EX)


def ep(dt):
    """[R11/R12 关键修正] `paper_positions.closed_at` 是**本地时间(UTC+8)的裸时间戳**，
    而 `crypto_klines.timestamp` 是 **UTC epoch**。裸 datetime 的 `.timestamp()` 会按
    系统本地时区解释 ⇒ **必须用它**，不能 replace(tzinfo=utc)（那会整体偏移 8 小时）。
    交叉验证：SOL 那笔 close=96.9333，本地 13:20 = UTC 05:20，正落在 05:00 那根 bar
    （low 96.85 / close 97.18）内 ✓。"""
    return int(dt.timestamp()) if dt.tzinfo is None else int(dt.timestamp())


with SessionLocal() as s:
    rows = s.execute(text("""
        SELECT symbol, lower(side), entry_price, close_price, sl_price, size, closed_at,
               ((CASE WHEN lower(side) IN ('long','buy') THEN (close_price-entry_price)
                      ELSE (entry_price-close_price) END) * size)
               - coalesce(partial_fee_paid,0) - coalesce(final_fee_paid,0) net,
               coalesce(partial_fee_paid,0) + coalesce(final_fee_paid,0) fee,
               close_reason
        FROM paper_positions
        WHERE status='closed' AND closed_at >= '2026-09-15'
        ORDER BY closed_at
    """)).fetchall()

groups = {
    "min_roi_decay": [r for r in rows if str(r[9]).startswith("exit_policy:min_roi_decay")],
    "sl": [r for r in rows if str(r[9]).startswith("sl")],
    "其它退出(对照)": [r for r in rows if not str(r[9]).startswith(("exit_policy:min_roi_decay", "sl"))],
}

print("=== 反事实：继续持有 +H（含 SL 检查）与标的自身涨跌（beta）===")
for name, rs in groups.items():
    if not rs:
        continue
    actual = sum(float(r[7]) for r in rs)
    print(f"\n【{name}】n={len(rs)} 实际净={actual:.2f}")
    for H in (6, 12, 24):
        tot = 0.0
        sl_hits = 0
        beta_sum = 0.0
        beta_n = 0
        for r in rs:
            sym, side = r[0], r[1]
            entry, slp, size, closed, fee = (float(r[2]),
                                             float(r[4]) if r[4] is not None else float(r[3]),
                                             float(r[5]), r[6], float(r[8]))
            bars = mq(
                f"SELECT {LCOL}, {CCOL} FROM {TBL} WHERE symbol=:s AND {PCOL}='1h' "
                f"AND exchange=:ex AND {TCOL} > :a AND {TCOL} <= :b ORDER BY {TCOL}",
                s=sym, ex=_EX, a=ep(closed), b=ep(closed + _dt.timedelta(hours=H)),
            )
            if not bars:
                tot += float(r[7])
                continue
            stop = any(
                (lo is not None) and ((side in ("long", "buy") and float(lo) <= slp) or
                                      (side in ("short", "sell") and float(lo) >= slp))
                for lo, _ in bars
            )
            px = slp if stop else float(bars[-1][1])
            sl_hits += 1 if stop else 0
            gross = (px - entry) * size if side in ("long", "buy") else (entry - px) * size
            tot += gross - fee
            try:
                beta_sum += (float(bars[-1][1]) / float(bars[0][1]) - 1.0)
                beta_n += 1
            except Exception:
                pass
        beta = (beta_sum / beta_n * 100) if beta_n else 0.0
        print(f"   +{H:2d}h: 反事实净={tot:8.2f}（差={tot-actual:+8.2f}） 触SL={sl_hits:2d}/{len(rs)} "
              f" 标的窗口平均涨跌={beta:+6.2f}%")
