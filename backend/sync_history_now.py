"""
一键历史数据同步脚本 - 直接运行即可拉取Binance历史K线

用法：python sync_history_now.py
"""
import os
import sys
import time
from datetime import datetime, timedelta, timezone

# 加载 .env
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

# 配置
SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "ARB", "OP", "AVAX"]
PERIODS = ["1d", "4h", "1h", "15m", "5m"]
SYNC_DAYS = 365
EXCHANGE = "binance"

# 周期对应的秒数（用于估算记录数）
PERIOD_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900,
    "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400,
}


def get_ccxt_exchange():
    """初始化 ccxt Binance 实例"""
    import ccxt

    config = {
        'enableRateLimit': True,
        'timeout': 30000,
        'options': {'defaultType': 'future'}
    }

    # 代理配置 - ccxt 4.x 使用 proxies 或直接环境变量
    http_proxy = os.environ.get('BINANCE_HTTP_PROXY') or os.environ.get('HTTP_PROXY')
    https_proxy = os.environ.get('BINANCE_HTTPS_PROXY') or os.environ.get('HTTPS_PROXY')
    if http_proxy:
        print(f"[init] 使用代理: {http_proxy}")
        # ccxt 4.x 代理配置方式
        config['proxies'] = {
            'http': http_proxy,
            'https': https_proxy or http_proxy,
        }
        # 也设置环境变量（兼容 requests/urllib3）
        os.environ['HTTP_PROXY'] = http_proxy
        os.environ['HTTPS_PROXY'] = https_proxy or http_proxy
        os.environ['http_proxy'] = http_proxy
        os.environ['https_proxy'] = https_proxy or http_proxy

    exchange = ccxt.binanceusdm(config)
    print(f"[init] 正在加载币安交易对列表...")
    exchange.load_markets()
    print(f"[init] 币安初始化完成，{len(exchange.markets)} 个交易对")
    return exchange


def count_existing(db, symbol, period):
    """统计数据库已有记录"""
    from sqlalchemy import text
    cur = db.execute(
        text("SELECT COUNT(*) FROM crypto_klines WHERE exchange=:exchange AND symbol=:symbol AND period=:period"),
        {"exchange": EXCHANGE, "symbol": symbol, "period": period}
    )
    return cur.scalar()


def fetch_and_insert(exchange_client, db, symbol, period, start_dt, end_dt):
    """从 Binance 拉取 K线 并写入数据库"""
    from sqlalchemy import text
    from backend.database.dialect import dialect

    ccxt_symbol = f"{symbol}/USDT:USDT"
    since_ms = int(start_dt.timestamp() * 1000)
    until_ms = int(end_dt.timestamp() * 1000)

    total_inserted = 0
    current_since = since_ms
    batch_num = 0

    insert_sql = text(dialect.insert_on_conflict_do_nothing(
        "crypto_klines",
        "exchange, symbol, market, timestamp, period, datetime_str, "
        "open_price, high_price, low_price, close_price, volume, environment",
        ":exchange, :symbol, 'CRYPTO', :timestamp, :period, :datetime_str, "
        ":open_price, :high_price, :low_price, :close_price, :volume, 'mainnet'",
        conflict_cols="exchange, symbol, market, period, timestamp, environment",
    ))

    while current_since < until_ms:
        batch_num += 1
        try:
            ohlcv = exchange_client.fetch_ohlcv(ccxt_symbol, period, since=current_since, limit=1500)
        except Exception as e:
            print(f"    [!] API 错误 (batch {batch_num}): {e}")
            time.sleep(3)
            try:
                ohlcv = exchange_client.fetch_ohlcv(ccxt_symbol, period, since=current_since, limit=1500)
            except Exception as e2:
                print(f"    [!!] 重试仍失败: {e2}，跳过此批次")
                # 跳过一个批次的时间
                period_sec = PERIOD_SECONDS.get(period, 3600)
                current_since += period_sec * 1500 * 1000
                continue

        if not ohlcv:
            break

        rows = []
        for candle in ohlcv:
            ts_ms, o, h, l, c, v = candle[0], candle[1], candle[2], candle[3], candle[4], candle[5]
            if ts_ms > until_ms:
                break
            ts_sec = int(ts_ms / 1000)
            dt_str = datetime.utcfromtimestamp(ts_sec).strftime('%Y-%m-%d %H:%M:%S')
            rows.append({
                "exchange": EXCHANGE, "symbol": symbol, "timestamp": ts_sec,
                "period": period, "datetime_str": dt_str,
                "open_price": float(o), "high_price": float(h),
                "low_price": float(l), "close_price": float(c),
                "volume": float(v),
            })

        if rows:
            db.execute(insert_sql, rows)
            db.commit()
            total_inserted += len(rows)

        current_since = ohlcv[-1][0] + 1

        # 每10批打印进度
        if batch_num % 10 == 0:
            pct = min((current_since - since_ms) / (until_ms - since_ms) * 100, 100)
            print(f"    batch {batch_num}: {total_inserted} 条, {pct:.0f}%")

        time.sleep(0.3)

    return total_inserted


def ensure_unique_index(db):
    """确保唯一索引存在"""
    from sqlalchemy import text
    from backend.database.dialect import dialect

    if dialect.is_sqlite:
        try:
            db.execute(text("""
                CREATE UNIQUE INDEX IF NOT EXISTS
                uix_klines_exchange_symbol_market_period_ts_env
                ON crypto_klines(exchange, symbol, market, period, timestamp, environment)
            """))
            db.commit()
            print("[init] SQLite 唯一索引已确保")
        except Exception as e:
            print(f"[init] 索引检查: {e}")
    else:
        # PostgreSQL: 使用 CREATE UNIQUE INDEX IF NOT EXISTS（PG 9.5+）
        try:
            db.execute(text("""
                CREATE UNIQUE INDEX IF NOT EXISTS
                uix_klines_exchange_symbol_market_period_ts_env
                ON crypto_klines(exchange, symbol, market, period, timestamp, environment)
            """))
            db.commit()
            print("[init] PostgreSQL 唯一索引已确保")
        except Exception as e:
            print(f"[init] 索引检查: {e}")


def main():
    print("=" * 60)
    print("  Binance 历史K线数据同步")
    print(f"  交易对: {', '.join(SYMBOLS)}")
    print(f"  周期: {', '.join(PERIODS)}")
    print(f"  天数: {SYNC_DAYS} 天")
    print("=" * 60)

    # 使用 SQLAlchemy Session（自动适配 SQLite/PostgreSQL）
    from backend.database.connection import MarketSessionLocal

    # 初始化交易所
    try:
        exchange_client = get_ccxt_exchange()
    except Exception as e:
        print(f"[错误] 无法连接币安: {e}")
        sys.exit(1)

    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=SYNC_DAYS)

    total_all = 0
    task_count = len(SYMBOLS) * len(PERIODS)
    done_count = 0
    start_time = time.time()

    with MarketSessionLocal() as db:
        ensure_unique_index(db)

        for period in PERIODS:
            for symbol in SYMBOLS:
                done_count += 1
                existing = count_existing(db, symbol, period)
                period_sec = PERIOD_SECONDS.get(period, 3600)
                expected = int(SYNC_DAYS * 86400 / period_sec)

                if existing >= expected * 0.9:
                    print(f"[{done_count}/{task_count}] {symbol}/{period}: 已有 {existing}/{expected} 条, 跳过")
                    continue

                print(f"[{done_count}/{task_count}] {symbol}/{period}: 已有 {existing}, 需要 ~{expected} 条, 开始拉取...")

                try:
                    inserted = fetch_and_insert(exchange_client, db, symbol, period, start_dt, end_dt)
                    total_all += inserted

                    elapsed = time.time() - start_time
                    remaining = (elapsed / done_count) * (task_count - done_count)
                    print(f"  ✅ 完成: +{inserted} 条, 累计 {total_all}, "
                          f"预计剩余 {remaining/60:.1f} 分钟")
                except Exception as e:
                    print(f"  ❌ 失败: {e}")

    elapsed_min = (time.time() - start_time) / 60

    print("=" * 60)
    print(f"  同步完成！")
    print(f"  总计写入: {total_all} 条")
    print(f"  耗时: {elapsed_min:.1f} 分钟")
    print("=" * 60)


if __name__ == "__main__":
    main()
