"""实测：实盘 mid_hist 的追加节奏是否真的"每快照一条"。

[F213 2026-09-15] 为什么要测
--------------------------------------------------
F212 对拍发现：同窗口下实盘冻结档占比 **78%**，模型 **0%**，而按快照数据离线复算只有 ~20% ✗。
追到 `runner.py:1498-1504`（F102）的守卫：
    _snap_ms_now = int(m.get("ts_ms") or 0)
    if _snap_ms_now != int(st.last_mid_src_ms or 0):
        st.mid_hist.append(mid); st.last_mid_src_ms = _snap_ms_now
若 `m["ts_ms"]` **不是盘口快照的时间戳**（比如是取数时刻、或跨币取最大），
这个守卫就会每 tick 放行 ⇒ `mid_hist` 变密 ⇒ "最近 120 条"覆盖的时间骤减
⇒ 更容易判为冻结 ⇒ 挂 3bp 窄单 ⇒ 周转暴涨（正是要查的方向）✓。

做法：隔 90 秒采样两次 `/shadow`，看每个币
  ① `last_mid_src_ms` 前进了多少；
  ② 同期数据库里该币**最新快照**的时间戳；
  ③ `mid_hist` 的末 5 个值是否在变化（能否反映真实中价）。
判读：
  · last_mid_src_ms 前进量 ≈ 快照网格间隔 × tick 数 ⇒ 每 tick 都在追加（守卫失效）✗
  · 前进量 ≈ 采样间隔（90s）且与最新快照对齐 ⇒ 每快照一条 ✓
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from backend.database.connection import MarketSessionLocal  # noqa: E402

LANE = os.getenv("MM_LANE", "mm_asterdex")
SYMS = ["BTC", "ETH", "BNB", "XRP", "SOL"]


def snap() -> dict:
    with urllib.request.urlopen(
            f"http://127.0.0.1:8000/api/trading/lanes/{LANE}/shadow", timeout=25) as r:
        return json.loads(r.read())


def newest_snap_ts() -> dict:
    out = {}
    with MarketSessionLocal() as s:
        s.execute(text("SET statement_timeout = 20000"))
        for r in s.execute(text(
                """SELECT symbol, max(timestamp) AS mx FROM market_orderbook_snapshots
                   WHERE exchange='asterdex' AND symbol = ANY(:syms) GROUP BY symbol"""),
                {"syms": SYMS}).mappings().all():
            out[r["symbol"]] = int(r["mx"] or 0)
    return out


def main() -> int:
    gap = float(sys.argv[1]) if len(sys.argv) > 1 else 90.0
    a = snap()
    ta = newest_snap_ts()
    t0 = time.time()
    print(f"采样 A @ {datetime.now().strftime('%H:%M:%S')}  tick={a.get('ticks')}")
    time.sleep(gap)
    b = snap()
    tb = newest_snap_ts()
    dt = time.time() - t0
    print(f"采样 B @ {datetime.now().strftime('%H:%M:%S')}  tick={b.get('ticks')}"
          f"  间隔 {dt:.0f}s  期间 tick +{int(b.get('ticks') or 0) - int(a.get('ticks') or 0)}")
    print(f"\n  {'币':<6}{'hist长度':>9}{'src前进(ms)':>13}{'src前进(s)':>11}"
          f"{'快照前进(s)':>12}{'末值变化':>10}")
    for s in SYMS:
        sa = (a.get("states") or {}).get(s) or {}
        sb = (b.get("states") or {}).get(s) or {}
        ha = sa.get("mid_hist") or []
        hb = sb.get("mid_hist") or []
        adv_ms = int(sb.get("last_mid_src_ms") or 0) - int(sa.get("last_mid_src_ms") or 0)
        snap_adv = (tb.get(s, 0) - ta.get(s, 0)) / 1000.0
        tail_a = [round(float(x), 4) for x in ha[-3:]]
        tail_b = [round(float(x), 4) for x in hb[-3:]]
        print(f"  {s:<6}{len(hb):>9}{adv_ms:>13}{adv_ms/1000.0:>11.1f}"
              f"{snap_adv:>12.1f}{'变了' if tail_a != tail_b else '没变':>10}")
    print("\n判读：src 前进(s) ≈ 快照前进(s) ⇒ 每快照一条 ✓；"
          "若 src 前进远大于快照前进 ⇒ 守卫失效、历史被灌密 ✗（会导致过度冻结）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
