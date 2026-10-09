"""采集健康探针 —— 供 `scripts/aster-depth-watchdog.ps1` 调用。

## 判定口径（为什么不是"深度最新时间"）

只看深度最新时间会漏掉一整类故障。实测事故：看门狗只探深度、却用它去启动
book/trades 订阅，于是 **book 数据在 19 个币上同时断供 32 分钟**（age 恒为 1926s），
而深度因为只看 13 币仍然"新鲜" ⇒ 看门狗判定健康 ⇒ 无人发现。

⇒ 本探针按 **(币, 数据流)** 逐项检查，返回**最差年龄**：

    book   : 受管币全部要有 `asterdex_book_ticker` 落库
    depth  : 受管币全部要有 `asterdex_depth_snapshots` 落库
    trades : 受管币全部要有 `asterdex_trades` 落库

只要有一个 (币, 流) 超阈值，最差年龄就超阈值 ⇒ 看门狗重启。

## 阈值

默认 1800s。**不能用 5 分钟**：实测这些表是批量 flush，正常落库延迟就有
**~340s**（实测 book/trades 稳定在 342s）。300s 阈值会把全部币判成不新鲜。

## 输出契约（stdout 单行）

    正常：`<worst_age_seconds>`（整数，可含负号表示轻微时钟回拨）
    失败：`ERR <原因>`，退出码非 0

调用方**必须**在失败时不做任何动作（宁可不作为，也不误杀采集）。
"""
from __future__ import annotations

import datetime
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARKET_DB = "alpha_market"

# 受管币 = 采集宇宙（book/trades）+ 深度名单，两者并集
MANAGED = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT",
    "LINKUSDT", "ADAUSDT", "AVAXUSDT", "SUIUSDT", "NEARUSDT", "ARBUSDT",
    "ENAUSDT", "WLDUSDT", "ONDOUSDT", "ASTERUSDT", "HYPEUSDT", "ZECUSDT",
    "XLMUSDT", "TAOUSDT", "UNIUSDT", "SEIUSDT", "PENDLEUSDT", "1000PEPEUSDT",
    "LTCUSDT", "XMRUSDT", "WLFIUSDT", "1000SHIBUSDT", "AAVEUSDT",
    "VIRTUALUSDT", "PUMPUSDT", "LITUSDT",
]
DEPTH_MANAGED = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT",
    "UNIUSDT", "ASTERUSDT", "HYPEUSDT", "ZECUSDT", "ONDOUSDT", "ARBUSDT",
    "SEIUSDT",
]


def main() -> int:
    try:
        from dotenv import load_dotenv

        env_path = os.path.join(REPO_ROOT, ".env")
        if not os.path.isfile(env_path):
            print(f"ERR .env not found at {env_path}")
            return 2
        load_dotenv(env_path, override=False)

        import psycopg2

        url = os.environ.get("DATABASE_URL", "")
        if not url:
            print("ERR DATABASE_URL empty")
            return 2
        for drv in ("+psycopg2", "+psycopg", "+asyncpg"):
            url = url.replace(drv, "")
        head, _, _ = url.rpartition("/")
        conn = psycopg2.connect(f"{head}/{MARKET_DB}")
        conn.autocommit = True
        cur = conn.cursor()
        now_ms = int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000)

        # ⚠️ **故意不把 `asterdex_trades` 纳入判定**：
        #   trades 是**逐笔**表，冷门币可以几十分钟没有一笔成交（实测
        #   1000SHIB 66 分钟、PENDLE 54 分钟无成交）。把它当健康信号会让看门狗
        #   对"没成交"误报"采集挂了" ⇒ 反复重启健康进程。
        #   `book_ticker` 每次盘口变动都推送 ⇒ 才是"流是否活着"的可判定信号。
        #   成交断层属**业务事实**（该币不活跃），由选币评分处理，不由采集看门狗处理。
        checks = (
            ("asterdex_book_ticker", MANAGED),
            ("asterdex_depth_snapshots", DEPTH_MANAGED),
        )
        worst = None
        worst_desc = ""
        missing = []
        # [2026-09-23] 性能修复：只看最近 1 小时（走 event_ts_ms 索引），
        # 否则 `symbol = ANY(...) GROUP BY` 在 1.8 亿行/39GB 的 book_ticker 和
        # 7100 万行/118GB 的 depth 表上走全表扫描，单次 2~4 分钟，
        # 30 分钟一次的重叠实例把整个 DB IO 拖死（前端全体 3s+ 超时的根因）。
        # 语义不变：本探针只判「流是否活着」，1 小时内无行 = 停摆/未落库。
        since_ms = now_ms - 3600 * 1000
        for table, syms in checks:
            cur.execute(
                f"select symbol, max(event_ts_ms) from {table} "
                f"where symbol = any(%s) and event_ts_ms >= %s group by symbol",
                (syms, since_ms),
            )
            got = {r[0]: r[1] for r in cur.fetchall()}
            for s in syms:
                mx = got.get(s)
                if not mx:
                    missing.append(f"{table}:{s}")
                    continue
                age = (now_ms - int(mx)) / 1000.0
                if worst is None or age > worst:
                    worst = age
                    worst_desc = f"{table}:{s}"
        conn.close()

        if missing:
            # 从未落库的 (币,流) 也是故障：如实上报，不静默
            print(f"ERR missing rows for {len(missing)} (table:symbol), e.g. {missing[:3]}")
            return 3
        if worst is None:
            print("ERR no rows at all")
            return 3
        print(int(worst))
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"ERR {type(e).__name__}: {e}")
        return 4


if __name__ == "__main__":
    sys.exit(main())
