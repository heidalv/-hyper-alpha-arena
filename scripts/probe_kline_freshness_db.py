# -*- coding: utf-8 -*-
"""K 线新鲜度权威核查（只读）：直接查行情库，而不是靠日志告警。

判据：每个 (symbol, timeframe) 的最后一根 K 线的起点距现在的秒数 vs 该周期的合理阈值。
"""
from __future__ import annotations

import io
import sys
from datetime import datetime, timezone
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402
from backend.database.connection import MarketSessionLocal as SessionLocal  # noqa: E402

# 关注：midlong 批里的标的 + 日志里报过陈旧的标的
SYMS = ["SUI", "PLAY", "COTI", "ONE", "ICP", "AVAX", "ADA", "VIRTUAL", "DOGE",
        "ASTER", "XRP", "UNI", "ZEC", "SOL", "BNB", "BTC", "MU", "WIF", "SYN", "ICX"]
TFS = ["15m", "1h", "4h", "1d"]
# 阈值：周期长度 × 3（容忍 2 根未收）
TF_SEC = {"15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}

db = SessionLocal()
try:
    db.execute(text("SET app.is_admin='on'"))
    print("=" * 96)
    print("当前业务库:", db.execute(text("SELECT current_database()")).fetchone()[0])
    cand = db.execute(text("""
        SELECT table_schema, table_name FROM information_schema.tables
        WHERE table_name LIKE '%kline%' OR table_name LIKE '%ohlcv%' OR table_name LIKE '%candle%' ORDER BY 1,2
    """)).fetchall()
    print("候选 K 线表:", [(s, t) for s, t in cand])
finally:
    db.close()

# 逐个候选表尝试
for schema, tbl in cand:
    try:
        db = SessionLocal()
        db.execute(text("SET app.is_admin='on'"))
        cols = [c for (c,) in db.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = :t
        """), {"t": tbl}).fetchall()]
        need = {"symbol", "timeframe", "open_time"}
        if not need.issubset(set(cols)):
            db.close()
            continue
        print("\n" + "=" * 96)
        print(f"表 {schema}.{tbl}：各标的最后一根 K 线的新鲜度")
        print(f"{'symbol':10s} " + " ".join(f"{tf:>10s}" for tf in TFS))
        now = datetime.now(timezone.utc)
        for s in SYMS:
            row = []
            for tf in TFS:
                r = db.execute(text(f"""
                    SELECT max(open_time) FROM {schema}.{tbl}
                    WHERE symbol = :s AND timeframe = :tf
                """), {"s": s, "tf": tf}).fetchone()
                ts = r[0] if r else None
                if ts is None:
                    row.append("无数据")
                    continue
                if getattr(ts, "tzinfo", None) is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                age = (now - ts).total_seconds()
                bad = age > TF_SEC[tf] * 3
                row.append(("!" if bad else " ") + f"{age/3600:.1f}h")
            print(f"{s:10s} " + " ".join(f"{x:>10s}" for x in row))
        print("  （'!' = 过期超过 3 个周期）")
        db.close()
        break
    except Exception as exc:  # noqa: BLE001
        print(f"  表 {schema}.{tbl} 查询失败: {type(exc).__name__}: {str(exc)[:90]}")
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass

