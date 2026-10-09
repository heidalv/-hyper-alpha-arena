"""h533：**市场数据新鲜度**——成交判定拿不到成交，是采集断了还是引擎读错表？

背景（2026-09-29 08:4xL）：车道自 **04:32:38** 起 4.2 小时零腿，但 worker
`ticks` 正常推进、`quoted_decisions=426`（在报价）、`avg_sigma_all=0.16`（波动平静）
⇒ 排除"worker 停摆"与"波动闸"，剩下最可能的是**判定成交所依赖的市场数据断了**。

关键事实：`market_trades_aggregated` **只存在于 market 库**（多库分离，
见 `alembic/versions/0016_*.py` 的注释），core/analytics 库里没有这张表
⇒ 必须用 `MARKET_DATABASE_URL` 查，否则会误判成"表不存在"。

[R190 修复] 本脚本**自己烂掉了**：4 张表还在按 `ts` 查，而采集器早已改写成
`event_ts_ms`（epoch ms）/`last_recv_ns`（epoch ns）⇒ 4 行"查询失败"；同时
`created_at` 是**本地朴素时间**，和 UTC 的 `now()` 相减得到 **−479.8 分钟**这种
**不可能为负**的"滞后"，而旧的判读逻辑只对 `>600` 和空表打标 ⇒ **负数一路静默通过** ✗。
两处都属"证据基础设施在说谎"（与 R187 同类）。修法：
  · 逐表列**候选清单**（按优先级尝试，全部失败才算"查询失败"）⇒ 以后再换列名不会静默烂；
  · 滞后**一律在 SQL 里算**（`now() - to_timestamp(max(col)/…)`）⇒ 不再手工混时区；
  · **负滞后单独打标**（口径不一致）⇒ 无论如何不会再被当成"新鲜"。

用法：python scripts/h533_market_freshness.py [--minutes 120]
"""
from __future__ import annotations

import argparse
import pathlib
import sys as _sys

try:  # 管道输出（GBK 控制台）遇到非 ASCII 不再崩溃
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"

# 链路逐环：（表名，候选时间列 [(列, 单位)], 作用）
#   单位：ms = epoch 毫秒；ns = epoch 纳秒；ts = 原生 timestamp/timestamptz
CHAIN: list[tuple[str, list[tuple[str, str]], str]] = [
    ("asterdex_trades", [("event_ts_ms", "ms"), ("ts", "ts")], "原始逐笔（采集器）"),
    ("asterdex_book_ticker", [("event_ts_ms", "ms"), ("ts", "ts")], "盘口 tick"),
    ("asterdex_depth_snapshots", [("event_ts_ms", "ms"), ("ts", "ts")], "深度快照"),
    ("asterdex_stream_health", [("last_recv_ns", "ns"), ("updated_at", "ts"),
                                ("ts", "ts")], "三路流心跳"),
    ("market_trades_aggregated", [("timestamp", "ms"), ("created_at", "ts")],
     "聚合桶（引擎判成交读它）"),
    ("market_orderbook_snapshots", [("timestamp", "ms"), ("created_at", "ts")],
     "盘口快照桶"),
]

_LAG_SQL = {
    "ms": 'EXTRACT(EPOCH FROM (now() - to_timestamp(max("{c}")/1000.0)))',
    "ns": 'EXTRACT(EPOCH FROM (now() - to_timestamp(max("{c}")/1e9)))',
    "ts": 'EXTRACT(EPOCH FROM (now() - max("{c}")))',
}


def read_env() -> dict:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def dsn_of(env: dict, *keys: str) -> str | None:
    for k in keys:
        if env.get(k):
            url = env[k]
            for j in ("+psycopg2", "+psycopg", "+asyncpg"):
                url = url.replace(j, "")
            return url
    return None


def _probe(cur, table: str, cands: list[tuple[str, str]]) -> tuple[int, object, float | None, str]:
    """按候选列依次尝试；返回 (行数, 最新值, 滞后秒, 用的列名)。

    滞后在 SQL 里算 ⇒ 不再手工混时区。全部候选都失败时抛最后一个异常。
    """
    last: Exception | None = None
    for col, unit in cands:
        sql = ('SELECT count(*), max("{c}"), ' + _LAG_SQL[unit] + ' FROM "{t}"').format(
            c=col, t=table)
        try:
            cur.execute(sql)  # noqa: S608
            cnt, mx, lag = cur.fetchone()
            return int(cnt or 0), mx, (float(lag) if lag is not None else None), col
        except Exception as e:  # noqa: BLE001
            last = e
            continue
    raise last if last else RuntimeError("no candidate column")


def _flag(lag: float | None, empty: bool) -> str:
    if empty:
        return "  ← **空表**"
    if lag is None:
        return "  ← 无法计算滞后"
    if lag < -60:
        return "  ← **口径不一致（负滞后不可能）**"
    if lag > 600:
        return "  ← **中断**"
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=120)
    a = ap.parse_args()
    env = read_env()
    keys = [k for k in env if "DATABASE_URL" in k or "DB_URL" in k]
    print(f".env 里的库连接键：{keys}")
    core = dsn_of(env, "DATABASE_URL")
    market = dsn_of(env, "MARKET_DATABASE_URL", "DATABASE_URL_MARKET", "DB_MARKET_URL")
    if market is None:
        print("⚠️ 未找到 market 库连接键，回退 core 库（该库没有 market_trades_aggregated）")
        market = core

    # ── 聚合桶：引擎判定成交读的就是它 ────────────────────────────────────
    for nm, dsn in (("market", market),):
        if not dsn:
            continue
        print(f"\n{'='*78}\n[{nm}] {dsn.split('@')[-1]}")
        try:
            with psycopg.connect(dsn, autocommit=True) as c, c.cursor() as cur:
                cnt, mx, lag, col = _probe(
                    cur, "market_trades_aggregated",
                    [("timestamp", "ms"), ("created_at", "ts")])
                print(f"  market_trades_aggregated: {cnt} 行（时间列 {col}）")
                print(f"    最新 = {mx} ⇒ 滞后 "
                      f"{(lag/60 if lag is not None else float('nan')):.1f} 分钟"
                      f"{_flag(lag, cnt == 0)}")
                sql = ('SELECT symbol, count(*) AS n, max("' + col + '") AS mx'
                       ' FROM market_trades_aggregated'
                       ' WHERE "' + col + '" > (EXTRACT(EPOCH FROM now())'
                       ' - %s) * ' + ("1000" if col in ("timestamp", "event_ts_ms") else "1") +
                       ' GROUP BY symbol ORDER BY n DESC')
                cur.execute(sql, (a.minutes * 60,))  # noqa: S608
                rows = cur.fetchall()
                print(f"  近 {a.minutes} 分钟有成交桶的币：{len(rows)}")
                for s, n, mx2 in rows[:12]:
                    print(f"    {s:>6s} {n:6d} 桶  最新 {mx2}")
        except Exception as e:
            print(f"  查询失败（本脚本列名过期 ≠ 采集中断 ✗）：{str(e)[:160]}")

    # ── 链路逐环新鲜度：写入方(ingester) → 原始逐笔 → 聚合桶 ───────────────
    print(f"\n{'='*78}\n链路逐环新鲜度（market 库；写入方 = research_l1/services/"
          f"aster_ws_ingest.py）")
    print("=" * 78)
    print(f"{'表':<30s} {'行数':>12s} {'最新':<24s} {'滞后':>9s}  列")
    try:
        with psycopg.connect(market, autocommit=True) as c, c.cursor() as cur:
            for t, cands, _role in CHAIN:
                try:
                    cnt, mx, lag, col = _probe(cur, t, cands)
                except Exception as e:  # noqa: BLE001
                    print(f"{t:<30s} 查询失败（列名过期 ≠ 采集中断 ✗）: {str(e)[:52]}")
                    continue
                print(f"{t:<30s} {cnt:12d} {str(mx)[:24]:<24s} "
                      f"{(lag/60 if lag is not None else float('nan')):8.1f}m "
                      f" {col}{_flag(lag, cnt == 0)}")
            # 三路流心跳明细（msgs 应持续增长、reconnects 不应上升）
            try:
                cur.execute("SELECT stream, msgs_total, reconnects, last_event_ms,"
                            " last_recv_ns, updated_at FROM asterdex_stream_health"
                            " ORDER BY stream")
                print("\n  三路流心跳：")
                for s, m, rc, lem, ns, upd in cur.fetchall():
                    print(f"    {s:<8s} msgs_total={int(m or 0):>10d} reconnects={rc} "
                          f"last_event_ms={lem} updated_at={upd}")
            except Exception as e:  # noqa: BLE001
                print(f"  心跳明细查询失败（列名过期 ≠ 采集中断 ✗）：{str(e)[:80]}")
            # 逐币最后一条原始成交（判断是否只剩部分币）
            try:
                cur.execute("""
                    SELECT symbol, count(*) AS n, max(event_ts_ms) AS mx
                    FROM asterdex_trades
                    WHERE event_ts_ms > (EXTRACT(EPOCH FROM now()) - %s) * 1000
                    GROUP BY symbol ORDER BY n DESC""", (a.minutes * 60,))
                print(f"\n  近 {a.minutes} 分钟 asterdex_trades 逐币：")
                rs = cur.fetchall()
                if not rs:
                    print("    （无）")
                for s, n, mx in rs[:14]:
                    print(f"    {s:>10s} {n:8d} 条  最新 {mx}")
            except Exception as e:  # noqa: BLE001
                print(f"  asterdex_trades 逐币查询失败：{str(e)[:80]}")
    except Exception as e:
        print(f"  market 库连接失败：{str(e)[:120]}")
    print("\n判读：① 出现『查询失败』⇒ 先怀疑**本脚本列名过期**（不是采集中断 ✗）；"
          "② **负滞后**或『口径不一致』⇒ 时间列单位/时区不对（不是数据来自未来 ✗）；"
          "③ 滞后 > 数分钟 ⇒ **采集中断**（修采集器，不是改做市）；"
          "④ 表新鲜而引擎仍无成交 ⇒ 回到引擎判定路径查。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
