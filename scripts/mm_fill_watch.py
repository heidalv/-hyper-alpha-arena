# -*- coding: utf-8 -*-
"""[F305 2026-09-16] 里程碑守候：LINK 段首批成交出现即落盘（跨后端重启也不会漏）。

为什么需要：LINK 段的实盘成交是**小时~天级**事件（F304：15s 步进中位 0.46bp，
30bp 挂宽下成交靠多拍累积），而 API 后端每 ~5 分钟被外部监管者重启一次 ⇒
靠人工盯着 `/shadow`（进程内计数器会清零）容易漏掉"第一批成交"这个里程碑。

做法：每 `--interval` 秒查一次行情库账本（`lane_ledger`，排除 reconcile 行），
发现**新增**成交就带上下文（symbol/side/notional/net_bp/markout 近似）追加到
`logs/mm_fill_watch.log`，并在首次出现时打印醒目一行。只读、零副作用。

用法：python scripts/mm_fill_watch.py --since 2026-09-16T13:55:00+08:00 --interval 300
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CST = timezone(timedelta(hours=8))
LOG = ROOT / "logs" / "mm_fill_watch.log"


def log(msg: str) -> None:
    line = datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " [fill-watch] " + msg
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def snapshot(since: datetime):
    from sqlalchemy import text

    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal

    with system_identity():
        with SessionLocal() as db:
            rows = db.execute(text(
                "SELECT symbol, COUNT(*) n, COALESCE(SUM(notional),0) notional,"
                " COALESCE(SUM(points_usd),0) pts, COALESCE(AVG(net_bp),0) net_bp,"
                " MAX(ts) last_ts FROM lane_ledger"
                " WHERE lane_id='mm_asterdex' AND ts >= :t"
                "   AND COALESCE((meta_json->>'source'),'') <> 'reconcile'"
                " GROUP BY symbol"), {"t": since}).mappings().all()
    return [dict(r) for r in rows]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-09-16T13:55:00+08:00")
    ap.add_argument("--interval", type=float, default=300.0)
    ap.add_argument("--hours", type=float, default=12.0)
    a = ap.parse_args()
    since = datetime.fromisoformat(a.since)
    deadline = time.time() + a.hours * 3600
    log(f"start since={a.since} interval={a.interval:.0f}s hours={a.hours:.0f}")
    seen = {}
    milestones = set()
    while time.time() < deadline:
        try:
            for row in snapshot(since):
                sym = str(row["symbol"])
                n = int(row["n"] or 0)
                if n > seen.get(sym, 0):
                    log(f"{sym}: fills={n} (+{n - seen.get(sym, 0)}) "
                        f"notional={float(row['notional'] or 0):.2f}$ "
                        f"realized={float(row['pts'] or 0):+.4f}$ "
                        f"net_bp均值={float(row['net_bp'] or 0):+.2f} "
                        f"last={row['last_ts']}")
                    seen[sym] = n
                    if sym not in milestones:
                        milestones.add(sym)
                        log(f"MILESTONE first fills on {sym}: n={n}")
        except Exception as e:
            log(f"query failed: {e}")
        time.sleep(a.interval)
    log("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
