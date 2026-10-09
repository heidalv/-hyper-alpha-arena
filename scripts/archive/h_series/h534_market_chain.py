"""h534：**行情链路断点定位**——采集器还活着吗？WS 还连着吗？

h533 已确认：`market_trades_aggregated` 与 `market_orderbook_snapshots`
双双停在 **04:32:30 / 04:32:42**（滞后 4.2h），与车道零腿时刻完全一致。
但这两张是**聚合产物**，链路是：

    aster_ws_ingest.py（Aster WS）
      → asterdex_trades / asterdex_book_ticker / asterdex_depth_snapshots
      → asterdex_stream_health（连接健康）
      → 聚合 → market_trades_aggregated / market_orderbook_snapshots
      → 做市引擎判定成交

本脚本自动探测各表的**时间列**（各表命名不统一，硬编码列名会误判成"表坏了"），
逐环打印新鲜度，并把 `asterdex_stream_health` 的最新几行原样打出来——
那一行直接说明 WS 是否连着、收到多少条。

用法：python scripts/h534_market_chain.py
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
TABLES = ["asterdex_trades", "asterdex_book_ticker", "asterdex_depth_snapshots",
          "asterdex_stream_health", "market_trades_aggregated",
          "market_orderbook_snapshots"]
PREF = ("ts", "timestamp", "created_at", "time", "event_time", "recv_ts", "updated_at")


def read_env() -> dict:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def main() -> int:
    env = read_env()
    dsn = env.get("MARKET_DATABASE_URL") or env["DATABASE_URL"]
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        dsn = dsn.replace(j, "")
    with psycopg.connect(dsn, autocommit=True) as c:
        with c.cursor() as cur:
            cur.execute("SELECT now()")
            now = cur.fetchone()[0]
            print(f"market 库 now() = {now:%Y-%m-%d %H:%M:%S}")

            def lagof(v):
                """把各种时间表示统一成"距 now 的秒数"；无法解释时返回 None。

                ⚠️ 不能假设只有一种量纲：本库同时存在 epoch_ms、epoch_us、
                epoch_ns 乃至纯自增 id 列（首版直接把 id 当 ms 传给
                `fromtimestamp` ⇒ OSError: Invalid argument）。逐档试，失败即 None。
                """
                if v is None:
                    return None
                if isinstance(v, (int, float)):
                    f = float(v)
                    for div in (1e3, 1e6, 1e9):        # ms → µs → ns
                        try:
                            v = dt.datetime.fromtimestamp(f / div)
                            break
                        except (OSError, OverflowError, ValueError):
                            continue
                    else:
                        return None
                if isinstance(v, str) or not hasattr(v, "tzinfo"):
                    return None
                if getattr(v, "tzinfo", None) is None:
                    v = v.replace(tzinfo=dt.timezone.utc)
                n = now if getattr(now, "tzinfo", None) else now.replace(
                    tzinfo=dt.timezone.utc)
                d = (n - v).total_seconds()
                # 明显不合理的量纲（>10 年或 <-10 年）视为不可解释
                return d if -3.2e8 < d < 3.2e8 else None

            print("\n逐表时间列与新鲜度（自动探测）")
            print("=" * 96)
            for t in TABLES:
                cur.execute("""
                    SELECT column_name, data_type FROM information_schema.columns
                    WHERE table_name=%s ORDER BY ordinal_position""", (t,))
                cols = cur.fetchall()
                if not cols:
                    print(f"{t:<30s} **表不存在**")
                    continue
                names = [n for n, _ in cols]
                tcols = [n for n, d in cols
                         if d in ("timestamp with time zone", "timestamp without time zone",
                                  "bigint", "double precision", "numeric")
                         and any(p in n.lower() for p in PREF)]
                best = None
                for col in tcols or [n for n, d in cols
                                     if d.startswith("timestamp")]:
                    try:
                        cur.execute(f'SELECT max("{col}") FROM "{t}"')  # noqa: S608
                        mx = cur.fetchone()[0]
                    except Exception:
                        continue
                    lg = lagof(mx)
                    if lg is not None and (best is None or lg < best[2]):
                        best = (col, mx, lg)
                if best:
                    flag = "  ← **中断**" if best[2] > 600 else "  ✓"
                    print(f"{t:<30s} {best[0]:<14s} 最新 {str(best[1])[:24]:<24s} "
                          f"滞后 {best[2]/60:7.1f}m{flag}")
                else:
                    print(f"{t:<30s} 时间列={tcols or '无'}（无法判定新鲜度）")

            print("\nasterdex_stream_health 最新 6 行（WS 连接健康）")
            print("=" * 96)
            try:
                cur.execute("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name='asterdex_stream_health'
                    ORDER BY ordinal_position""")
                hc = [r[0] for r in cur.fetchall()]
                print("  列：" + "、".join(hc))
                order = ("updated_at" if "updated_at" in hc else
                         "created_at" if "created_at" in hc else
                         "ts" if "ts" in hc else hc[0])
                cur.execute(
                    f'SELECT * FROM asterdex_stream_health ORDER BY "{order}" DESC '
                    f'LIMIT 6')  # noqa: S608
                for row in cur.fetchall():
                    print("   " + " | ".join(f"{str(v)[:26]}" for v in row))
            except Exception as e:
                print(f"  失败：{str(e)[:120]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
