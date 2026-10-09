# -*- coding: utf-8 -*-
"""定论：后端进程里 `kline_service.collect_historical_klines`（= 按需回填队列的执行体）
在 **DC_ONLY** 下到底能不能取到交易所数据？

这是 D-5/D-6 的关键前提：若能，则"按需回填"是可用通道（可用它补齐任何标的的长周期）；
若不能，则该队列在本部署是死的，只能靠数据中心静态宇宙。

做法：对仍陈旧的 COTI 取 **2 天的 1d**（极小范围，顺带补数据），打印结果与异常。
"""
from __future__ import annotations

import asyncio
import io
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SYM = "COTI"
PERIOD = "1d"


async def main() -> int:
    from backend.services.kline_data_service import kline_service
    from backend.services.market_data import _dc_only_enabled

    print(f"DC_ONLY = {_dc_only_enabled()}")
    end = datetime.now(timezone.utc).replace(tzinfo=None)
    start = end - timedelta(days=2)
    print(f"调用 collect_historical_klines({SYM}, {start} → {end}, {PERIOD}) …")
    try:
        await kline_service.initialize()
    except Exception as exc:  # noqa: BLE001
        print(f"  initialize 失败: {type(exc).__name__}: {str(exc)[:120]}")
    try:
        n = await kline_service.collect_historical_klines(SYM, start, end, PERIOD)
        print(f"  返回条数 = {n}")
        if n:
            print("  ⇒ ✅ DC_ONLY 下**可用**：按需回填通道成立")
        else:
            print("  ⇒ ⚠️ 返回 0（可能是无新数据/被拒/异常，见日志）")
    except Exception as exc:  # noqa: BLE001
        print(f"  ✗ 抛异常: {type(exc).__name__}: {str(exc)[:200]}")
        print("  ⇒ ❌ 该通道在 DC_ONLY 下不可用")

    # 复核：写进去了吗
    try:
        from sqlalchemy import text
        from backend.database.connection import MarketSessionLocal as S
        db = S()
        try:
            db.execute(text("SET app.is_admin='on'"))
            r = db.execute(text(
                "SELECT max(timestamp), count(*) FROM crypto_klines "
                "WHERE symbol=:s AND period=:p"), {"s": SYM, "p": PERIOD}).fetchone()
            import time as _t
            if r and r[0]:
                print(f"  复核 DB：{SYM}/{PERIOD} 最新 bar 年龄 = "
                      f"{(_t.time()-float(r[0]))/3600:.1f}h，共 {r[1]} 行")
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        print(f"  （DB 复核失败: {type(exc).__name__}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
