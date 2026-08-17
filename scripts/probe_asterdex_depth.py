"""探针：asterdex 各周期历史深度上限（交易所侧是否封顶）。

直接问交易所 API 要「60 天前」的 K 线，看能拿回多老的数据。
只读探测，走采集器同一路径（尊重限流桶）。
"""
import asyncio
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, r"D:\001Alpha\Hyper-Alpha-Arena")
# 先加载 settings：把 .env（HTTP(S)_PROXY=http://127.0.0.1:1080）注入环境，
# 否则 ccxt 直连 fapi.asterdex.com 必然失败（与 DC worker 环境不一致）。
import backend.config.settings  # noqa: F401


async def main():
    from backend.services.kline_collectors import ExchangeDataSourceFactory

    collector = ExchangeDataSourceFactory.get_collector("asterdex")
    now = datetime.now(timezone.utc)
    probes = [
        # (period, 窗口起点, 窗口终点, 说明)
        ("5m", now - timedelta(days=60), now - timedelta(days=55), "60~55 天前"),
        ("5m", now - timedelta(days=35), now - timedelta(days=30), "35~30 天前"),
        ("5m", now - timedelta(days=31), now - timedelta(days=30), "31~30 天前"),
        ("1m", now - timedelta(days=60), now - timedelta(days=55), "60~55 天前"),
        ("1m", now - timedelta(days=31), now - timedelta(days=30), "31~30 天前"),
        ("15m", now - timedelta(days=120), now - timedelta(days=110), "120~110 天前"),
    ]
    for period, ws, we, label in probes:
        try:
            bars = await collector.fetch_historical_klines("BTC", ws, we, period)
            if not bars:
                print(f"BTC {period} [{label}]: EMPTY -> 交易所不给这段历史")
                continue
            first = datetime.fromtimestamp(getattr(bars[0], "timestamp", 0), tz=timezone.utc)
            last = datetime.fromtimestamp(getattr(bars[-1], "timestamp", 0), tz=timezone.utc)
            print(f"BTC {period} [{label}]: got={len(bars)} 实际覆盖 {first.isoformat()} .. {last.isoformat()}")
        except Exception as e:
            print(f"BTC {period} [{label}]: EXC {type(e).__name__}: {str(e)[:150]}")


if __name__ == "__main__":
    asyncio.run(main())
