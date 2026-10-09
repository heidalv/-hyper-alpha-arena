# -*- coding: utf-8 -*-
"""K 线采集池健康度（只读）：按 (exchange, period, pool) 取最新一跳，看 ok/fail 与新鲜度。"""
from __future__ import annotations

import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402
from backend.database.connection import MarketSessionLocal as S  # noqa: E402

db = S()
try:
    db.execute(text("SET app.is_admin='on'"))
    rows = db.execute(text("""
        SELECT DISTINCT ON (exchange, period, pool)
               exchange, period, pool, last_success_at, symbols_ok, symbols_fail, meta_json
        FROM kline_sync_heartbeat
        ORDER BY exchange, period, pool, id DESC
    """)).fetchall()
    now = datetime.now(timezone.utc)
    print("=" * 104)
    print(f"{'exchange':12s} {'period':6s} {'pool':12s} {'ok':>5s} {'fail':>5s} "
          f"{'最后成功距今':>12s}  说明")
    print("=" * 104)
    bad = []
    for ex, per, pool, last, ok, fail, meta in rows:
        if last is not None:
            _l = last if getattr(last, "tzinfo", None) else last.replace(tzinfo=timezone.utc)
            age = (now - _l).total_seconds()
            age_s = f"{age/60:.0f}分" if age < 7200 else f"{age/3600:.1f}小时"
        else:
            age_s = "—"
        note = ""
        try:
            m = json.loads(meta or "{}")
            if m.get("periods"):
                note = f"periods={m['periods']} symbols={m.get('symbols')}"
            elif m.get("source"):
                note = f"src={m['source']} symbols={m.get('symbols')} days={m.get('days')}"
        except Exception:  # noqa: BLE001
            pass
        flag = ""
        if (fail or 0) > 0 and (ok or 0) == 0:
            flag = "  ❌ 全池失败"
            bad.append((ex, per, pool, ok, fail, age_s))
        elif (fail or 0) > 0:
            flag = "  ⚠️ 部分失败"
        print(f"{ex:12s} {per:6s} {pool:12s} {ok:5d} {fail:5d} {age_s:>12s}  {note}{flag}")

    print("\n【全池失败的池子】")
    for b in bad:
        print("   ", b)

    print("\n=== crypto_klines：midlong 关注标的的实际新鲜度（按 period） ===")
    SYMS = ["SUI", "PLAY", "COTI", "ONE", "ICP", "AVAX", "ADA", "VIRTUAL", "DOGE",
            "ASTER", "XRP", "UNI", "SOL", "BNB", "BTC"]
    PERIODS = ["15m", "1h", "4h", "1d"]
    print(f"{'symbol':10s} " + " ".join(f"{p:>12s}" for p in PERIODS))
    for s in SYMS:
        cells = []
        for per in PERIODS:
            r = db.execute(text("""
                SELECT max(timestamp) FROM crypto_klines
                WHERE symbol = :s AND period = :p
            """), {"s": s, "p": per}).fetchone()
            ts = r[0] if r else None
            if not ts:
                cells.append("无数据")
                continue
            age_h = (now.timestamp() - float(ts)) / 3600.0
            cells.append(f"{age_h:.1f}h")
        print(f"{s:10s} " + " ".join(f"{c:>12s}" for c in cells))
    print("  （timestamp 为该 K 线起点，单位秒；1d 的 12h 属正常，1h 的 >3h 属异常）")
finally:
    db.close()
