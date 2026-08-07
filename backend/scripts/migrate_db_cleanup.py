"""迁移前数据库清理：删过期缓存/日志膨胀，保留交易历史与运行态。

安全原则：
- 保留：open 持仓、running 会话、已平仓记录、已成交订单、近期决策
- 删除：过期订单簿/原始行情事件、过期信号日志、空壳膨胀表
- 不做 VACUUM FULL（避免长时间锁表）；DELETE 后 VACUUM ANALYZE
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

from sqlalchemy import create_engine, text

sys.stdout.reconfigure(encoding="utf-8")


def _load_env() -> None:
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    env_path = os.path.join(root, ".env")
    if not os.path.isfile(env_path):
        return
    with open(env_path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def _exec(eng, sql: str, params=None) -> int:
    with eng.connect() as c:
        r = c.execute(text(sql), params or {})
        try:
            c.commit()
        except Exception:
            pass
        return int(r.rowcount or 0)


def _scalar(eng, sql: str):
    with eng.connect() as c:
        return c.execute(text(sql)).scalar()


def clean_market(url: str) -> None:
    print("\n=== CLEAN alpha_market ===")
    eng = create_engine(url, isolation_level="AUTOCOMMIT")
    before = _scalar(eng, "SELECT pg_size_pretty(pg_database_size(current_database()))")
    print("size before:", before)

    # 订单簿快照：只留近 3 天（raw_levels 极大）
    n = _exec(
        eng,
        "DELETE FROM market_orderbook_snapshots WHERE created_at < NOW() - INTERVAL '3 days'",
    )
    print(f"deleted market_orderbook_snapshots: {n}")

    # 原始行情事件：只留近 7 天
    n = _exec(
        eng,
        "DELETE FROM raw_market_events WHERE created_at < NOW() - INTERVAL '7 days'",
    )
    print(f"deleted raw_market_events: {n}")

    # 聚合交易/资产指标：只留近 14 天（若有 created_at）
    for tbl, col in (
        ("market_trades_aggregated", "created_at"),
        ("market_asset_metrics", "created_at"),
    ):
        try:
            n = _exec(
                eng,
                f"DELETE FROM {tbl} WHERE {col} < NOW() - INTERVAL '14 days'",
            )
            print(f"deleted {tbl}: {n}")
        except Exception as e:
            print(f"skip {tbl}: {e}")

    # 非当前主所的 1m K 线可砍（保留 asterdex/binance；删 okx/hyperliquid 的 1m）
    try:
        n = _exec(
            eng,
            """
            DELETE FROM crypto_klines
            WHERE period = '1m'
              AND exchange IN ('okx', 'hyperliquid')
            """,
        )
        print(f"deleted crypto_klines 1m okx/hl: {n}")
    except Exception as e:
        print(f"skip kline trim: {e}")

    with eng.connect() as c:
        c.execute(text("VACUUM ANALYZE market_orderbook_snapshots"))
        c.execute(text("VACUUM ANALYZE raw_market_events"))
        c.execute(text("VACUUM ANALYZE crypto_klines"))
    after = _scalar(eng, "SELECT pg_size_pretty(pg_database_size(current_database()))")
    print("size after:", after)
    eng.dispose()


def clean_analytics(url: str) -> None:
    print("\n=== CLEAN alpha_analytics ===")
    eng = create_engine(url, isolation_level="AUTOCOMMIT")
    before = _scalar(eng, "SELECT pg_size_pretty(pg_database_size(current_database()))")
    print("size before:", before)

    # decision_snapshots 时间列因版本而异
    snap_deleted = False
    for col in ("created_at", "timestamp", "ts", "recorded_at", "decision_time", "updated_at"):
        try:
            n = _exec(
                eng,
                f"DELETE FROM decision_snapshots WHERE {col} < NOW() - INTERVAL '14 days'",
            )
            print(f"deleted decision_snapshots by {col}: {n}")
            snap_deleted = True
            break
        except Exception as e:
            print(f"try {col}: {type(e).__name__}")
    if not snap_deleted:
        # 若只有 epoch 毫秒
        for col in ("timestamp", "ts", "created_ts"):
            try:
                n = _exec(
                    eng,
                    f"""
                    DELETE FROM decision_snapshots
                    WHERE to_timestamp(({col})/1000.0) < NOW() - INTERVAL '14 days'
                    """,
                )
                print(f"deleted decision_snapshots by epoch {col}: {n}")
                snap_deleted = True
                break
            except Exception as e:
                print(f"try epoch {col}: {type(e).__name__}")

    n = _exec(
        eng,
        "DELETE FROM ai_decision_logs WHERE created_at < NOW() - INTERVAL '30 days'",
    )
    print(f"deleted ai_decision_logs: {n}")

    for tbl in ("mlto_memory_events", "factor_performance_logs", "mlto_thesis_events"):
        try:
            n = 0
            for col in ("created_at", "event_ts", "timestamp", "ts"):
                try:
                    n = _exec(
                        eng,
                        f"DELETE FROM {tbl} WHERE {col} < NOW() - INTERVAL '30 days'",
                    )
                    print(f"deleted {tbl} by {col}: {n}")
                    break
                except Exception:
                    continue
        except Exception as e:
            print(f"skip {tbl}: {e}")

    with eng.connect() as c:
        c.execute(text("VACUUM ANALYZE decision_snapshots"))
        c.execute(text("VACUUM ANALYZE ai_decision_logs"))
    after = _scalar(eng, "SELECT pg_size_pretty(pg_database_size(current_database()))")
    print("size after:", after)
    eng.dispose()


def clean_arena(url: str) -> None:
    print("\n=== CLEAN alpha_arena ===")
    eng = create_engine(url, isolation_level="AUTOCOMMIT")
    before = _scalar(eng, "SELECT pg_size_pretty(pg_database_size(current_database()))")
    print("size before:", before)

    # 取消/拒绝订单：只删 14 天前；保留 filled + 近期
    n = _exec(
        eng,
        """
        DELETE FROM paper_orders
        WHERE status IN ('cancelled', 'rejected', 'expired')
          AND created_at < NOW() - INTERVAL '14 days'
        """,
    )
    print(f"deleted old cancelled/rejected paper_orders: {n}")

    # 信号日志：保留 14 天
    for tbl in ("scalp_signal_log", "signal_trade_feedback"):
        for col in ("created_at", "timestamp", "ts", "logged_at"):
            try:
                n = _exec(
                    eng,
                    f"DELETE FROM {tbl} WHERE {col} < NOW() - INTERVAL '14 days'",
                )
                print(f"deleted {tbl} by {col}: {n}")
                break
            except Exception:
                continue

    # 空壳膨胀表：行数为 0 则 TRUNCATE 回收空间
    empty_candidates = [
        "alpha_assistant_messages",
        "multi_symbol_kelly",
        "strategy_hypotheses",
        "signal_weight_history",
        "opencode_insights",
        "opencode_evolution_proposals",
        "rebate_positions",
        "rebate_trade_outcomes",
        "arbitrage_paper_ledgers",
        "signal_definitions",
    ]
    for tbl in empty_candidates:
        try:
            cnt = _scalar(eng, f"SELECT count(*) FROM {tbl}")
            if cnt == 0:
                _exec(eng, f"TRUNCATE TABLE {tbl}")
                print(f"truncated empty bloated table: {tbl}")
            else:
                print(f"keep {tbl}: rows={cnt}")
        except Exception as e:
            print(f"skip truncate {tbl}: {e}")

    # 明确保留：open positions / closed positions / filled orders / running session
    open_n = _scalar(eng, "SELECT count(*) FROM paper_positions WHERE status='open'")
    closed_n = _scalar(eng, "SELECT count(*) FROM paper_positions WHERE status='closed'")
    sess = _scalar(eng, "SELECT count(*) FROM full_auto_sessions WHERE status='running'")
    print(f"KEEP open_positions={open_n} closed_positions={closed_n} running_sessions={sess}")

    with eng.connect() as c:
        c.execute(text("VACUUM ANALYZE paper_orders"))
        c.execute(text("VACUUM ANALYZE scalp_signal_log"))
        c.execute(text("VACUUM ANALYZE signal_trade_feedback"))
    after = _scalar(eng, "SELECT pg_size_pretty(pg_database_size(current_database()))")
    print("size after:", after)
    eng.dispose()


def main() -> None:
    _load_env()
    print("cleanup start", datetime.now(timezone.utc).isoformat())
    only = (sys.argv[1] if len(sys.argv) > 1 else "all").lower()
    if only in ("all", "arena"):
        clean_arena(os.environ["DATABASE_URL"])
    if only in ("all", "analytics"):
        clean_analytics(os.environ["ANALYTICS_DATABASE_URL"])
    if only in ("all", "market"):
        clean_market(os.environ["MARKET_DATABASE_URL"])
    print("\ncleanup done", datetime.now(timezone.utc).isoformat())


if __name__ == "__main__":
    main()
