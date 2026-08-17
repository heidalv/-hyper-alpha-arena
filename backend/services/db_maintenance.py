"""数据库定期维护：自动清理过期数据，保持数据库精简"""

import os
import time
import logging
from datetime import datetime, timezone
from sqlalchemy import text

logger = logging.getLogger(__name__)

# 数据保留策略（秒）
# [2026-08-04 修复] 保留天数必须 >= 回填目标天数（KLINE_P1_DEPTH_DAYS_*），
# 否则 db_maintenance 每 6 小时会把 DepthBackfill/P2 刚补的历史 K 线删掉，
# 形成「回填→被删→再回填」死循环，正是用户反馈「K线数据不全、周期缺失」的根因。
# 实测 14:45 maintenance 一次删除 163 万行（1m/3m/5m 152 万、15m/30m 9.2 万、1h/4h 1.5 万）。
# 各档保留天数可由 .env 覆盖（KLINE_RETENTION_DAYS_<PERIOD>）。
def _retention_days(period: str, default_days: int) -> int:
    try:
        v = int(os.getenv(f"KLINE_RETENTION_DAYS_{period.upper()}", ""))
        if v > 0:
            return v
    except (TypeError, ValueError):
        pass
    return default_days


def _retention_days_monthly() -> int:
    """月线(1M)保留天数：走专用键，避免与 1m 分钟周期共用
    KLINE_RETENTION_DAYS_1M（该键已被 1m 占用，.env=30 天）。"""
    try:
        v = int(os.getenv("KLINE_RETENTION_DAYS_1M_MONTH", "1825"))
        if v > 0:
            return v
    except (TypeError, ValueError):
        pass
    return 1825


def _rollup_flow_archive(db, now_ts: int) -> int:
    """[2026-08-15 D2] 订单流 5m 归档 + 清理 >30 天原始行。

    1. 把 market_trades_aggregated 中 30 天前的原始 15s 窗口按 5m 桶聚合，
       幂等写入 flow_archive_5m（含吃单买卖额/笔数、最大单笔）；
    2. 关联 market_asset_metrics（OI/资金费）与 market_orderbook_snapshots（点差）
       的同桶均值写入归档；
    3. 删除已归档的原始行（仅 trades_agg 30 天前部分）。
    返回删除行数。
    """
    import math

    raw_retention_ms = 30 * 86400 * 1000
    cutoff_ms = now_ts * 1000 - raw_retention_ms
    deleted = 0
    # 归档桶：按 5m 对齐
    try:
        db.execute(
            text(
                """
                INSERT INTO flow_archive_5m
                    (exchange, symbol, ts_ms, taker_buy_usd, taker_sell_usd,
                     taker_buy_count, taker_sell_count, largest_trade_usd)
                SELECT exchange, symbol,
                       (timestamp / 300000) * 300000 AS ts_ms,
                       SUM(taker_buy_notional)::numeric(24,2),
                       SUM(taker_sell_notional)::numeric(24,2),
                       SUM(taker_buy_count),
                       SUM(taker_sell_count),
                       MAX(largest_trade_usd)::numeric(24,2)
                FROM market_trades_aggregated
                WHERE timestamp < :cutoff
                GROUP BY exchange, symbol, (timestamp / 300000) * 300000
                ON CONFLICT (exchange, symbol, ts_ms) DO UPDATE SET
                    taker_buy_usd = EXCLUDED.taker_buy_usd,
                    taker_sell_usd = EXCLUDED.taker_sell_usd,
                    taker_buy_count = EXCLUDED.taker_buy_count,
                    taker_sell_count = EXCLUDED.taker_sell_count,
                    largest_trade_usd = EXCLUDED.largest_trade_usd
                """
            ),
            {"cutoff": cutoff_ms},
        )
        # 关联归档 OI/资金费（同桶均值）
        db.execute(
            text(
                """
                UPDATE flow_archive_5m a SET
                    oi = m.oi,
                    funding_rate = m.fr
                FROM (
                    SELECT exchange, symbol, (timestamp / 300000) * 300000 AS ts_ms,
                           AVG(open_interest)::numeric(24,2) AS oi,
                           AVG(funding_rate)::numeric(18,8) AS fr
                    FROM market_asset_metrics
                    WHERE timestamp < :cutoff
                    GROUP BY exchange, symbol, (timestamp / 300000) * 300000
                ) m
                WHERE a.exchange = m.exchange AND a.symbol = m.symbol
                  AND a.ts_ms = m.ts_ms
                """
            ),
            {"cutoff": cutoff_ms},
        )
        # 点差（同桶均值，bps）
        db.execute(
            text(
                """
                UPDATE flow_archive_5m a SET spread_bps = o.spread_bps
                FROM (
                    SELECT exchange, symbol, (timestamp / 300000) * 300000 AS ts_ms,
                           AVG(spread)::numeric(18,4) AS spread_bps
                    FROM market_orderbook_snapshots
                    WHERE timestamp < :cutoff AND spread IS NOT NULL
                    GROUP BY exchange, symbol, (timestamp / 300000) * 300000
                ) o
                WHERE a.exchange = o.exchange AND a.symbol = o.symbol
                  AND a.ts_ms = o.ts_ms
                """
            ),
            {"cutoff": cutoff_ms},
        )
        # 清理已归档的原始 15s 窗口行（仅 30 天前部分，最新数据不动）
        result = db.execute(
            text("DELETE FROM market_trades_aggregated WHERE timestamp < :cutoff"),
            {"cutoff": cutoff_ms},
        )
        deleted = int(result.rowcount or 0)
        if deleted:
            logger.info(f"[Maintenance] 订单流归档完成，清理原始行 {deleted:,}")
    except Exception as exc:
        db.rollback()
        raise exc
    return deleted


# [2026-08-15 R1/R2 修复] 留存从「分组桶」改为「按周期独立桶」：
# - 原分组桶只读组内一个 env 键（4h 桶读 1h、30m 桶读 15m、3m/5m 桶读 1m），
#   .env 里 KLINE_RETENTION_DAYS_4H/30M/3M/5M 全部被忽略；
# - 且 5m 留存 30 天 < 回填目标 50 天（08-08 上调后留存未同步），会重新触发
#   「回填→被删→再回填」死循环；
# - 原 1d/1w 无留存桶（表无界增长）；现补 10 年长桶：1d 历史含 binance
#   2017 年起 8.7 年数据，长线研究价值高，10 年桶既保数据又有明确边界。
_KLINE_RETENTION_DAYS = {
    "1m": _retention_days("1m", 55),     # [2026-08-15] 回填目标 55（5m/1m 切分需 50+ 天）
    "3m": _retention_days("3m", 30),     # 回填目标 30
    "5m": _retention_days("5m", 55),     # [2026-08-15] 回填目标 55（原 50 差 50 根无法切分）
    "15m": _retention_days("15m", 90),   # 回填目标 60
    "30m": _retention_days("30m", 100),  # [2026-08-15] 回填目标 100（切分需 96 天）
    "1h": _retention_days("1h", 400),    # 回填目标 210
    "4h": _retention_days("4h", 400),    # 回填目标 365
    "1d": _retention_days("1d", 3650),   # 回填目标 730；10 年长桶
    "1w": _retention_days("1w", 3650),   # 回填目标 520；10 年长桶
    "1M": _retention_days_monthly(),     # 回填目标 60 根（5 年）
}

RETENTION = {
    **{f"kline_{p}": days * 86400 for p, days in _KLINE_RETENTION_DAYS.items()},
    "ai_decision_logs": 90 * 86400,     # AI 决策日志保留 90 天，便于排查策略卡死/重复决策
    "llm_usage_logs": 14 * 86400,       # LLM 使用日志保留 14 天
    "whale_activities": 7 * 86400,      # 鲸鱼活动保留 7 天
    "strategy_analysis_logs": 90 * 86400,  # 策略分析日志保留 90 天
    "news_events": 30 * 86400,          # 新闻保留 30 天
    "ticker_snapshots": 14 * 86400,     # [2026-08-15 D5] 秒级 ticker 快照保留 14 天
    "liquidation_events": 90 * 86400,   # [2026-08-15 D3] 清算小时聚合保留 90 天
}


def run_db_maintenance():
    """执行数据库清理（由调度器周期性调用）

    表归属：
      Market DB: crypto_klines, whale_activities, news_events
      Analytics DB: ai_decision_logs, llm_usage_logs, strategy_analysis_logs
    """
    from backend.database.connection import (
        MarketSessionLocal, AnalyticsSessionLocal
    )
    start = time.time()
    total_deleted = 0
    now_ts = int(time.time())

    # ── Market DB 清理 ──
    try:
        market_db = MarketSessionLocal()
        try:
            # 1. K线清理（最大的表）——按周期独立桶（R1/R2：每周期 env 键生效）
            for period in ("1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d", "1w", "1M"):
                cutoff = now_ts - RETENTION[f"kline_{period}"]
                result = market_db.execute(
                    text("DELETE FROM crypto_klines WHERE period = :p AND timestamp < :cutoff"),
                    {"p": period, "cutoff": cutoff},
                )
                cnt = result.rowcount
                if cnt > 0:
                    total_deleted += cnt
                    logger.info(f"[Maintenance] 清理K线 {period}: {cnt:,} 行")

            # 2. 鲸鱼活动
            cutoff_dt = datetime.fromtimestamp(now_ts - RETENTION["whale_activities"])
            result = market_db.execute(
                text("DELETE FROM whale_activities WHERE created_at < :cutoff"),
                {"cutoff": cutoff_dt.strftime("%Y-%m-%d %H:%M:%S")},
            )
            if result.rowcount > 0:
                total_deleted += result.rowcount
                logger.info(f"[Maintenance] 清理鲸鱼活动: {result.rowcount:,} 行")

            # 3. 新闻
            cutoff_dt = datetime.fromtimestamp(now_ts - RETENTION["news_events"])
            result = market_db.execute(
                text("DELETE FROM news_events WHERE created_at < :cutoff"),
                {"cutoff": cutoff_dt.strftime("%Y-%m-%d %H:%M:%S")},
            )
            if result.rowcount > 0:
                total_deleted += result.rowcount
                logger.info(f"[Maintenance] 清理新闻: {result.rowcount:,} 行")

            # 4. [2026-08-15 D5] 秒级 ticker 快照（ts_ms 毫秒）
            cutoff_ms = (now_ts - RETENTION["ticker_snapshots"]) * 1000
            try:
                result = market_db.execute(
                    text("DELETE FROM ticker_snapshots WHERE ts_ms < :cutoff"),
                    {"cutoff": cutoff_ms},
                )
                if result.rowcount and result.rowcount > 0:
                    total_deleted += result.rowcount
                    logger.info(f"[Maintenance] 清理 ticker 快照: {result.rowcount:,} 行")
            except Exception as e:
                # 表未创建（DC 未重启）时静默跳过
                logger.debug("[Maintenance] ticker_snapshots 清理跳过: %s", e)

            # 5. [2026-08-15 D3] 清算小时聚合（ts_ms 毫秒）
            cutoff_ms_liq = (now_ts - RETENTION["liquidation_events"]) * 1000
            try:
                result = market_db.execute(
                    text("DELETE FROM liquidation_events WHERE ts_ms < :cutoff"),
                    {"cutoff": cutoff_ms_liq},
                )
                if result.rowcount and result.rowcount > 0:
                    total_deleted += result.rowcount
                    logger.info(f"[Maintenance] 清理清算聚合: {result.rowcount:,} 行")
            except Exception as e:
                logger.debug("[Maintenance] liquidation_events 清理跳过: %s", e)

            # 6. [2026-08-15 D2] 订单流归档：>30 天原始 15s 窗口 → 5m 归档后清理
            try:
                total_deleted += _rollup_flow_archive(market_db, now_ts)
            except Exception as e:
                logger.warning("[Maintenance] 订单流归档失败: %s", e)

            market_db.commit()
        except Exception as e:
            market_db.rollback()
            logger.error(f"[Maintenance] Market DB 清理失败: {e}", exc_info=True)
        finally:
            market_db.close()
    except Exception as e:
        logger.error(f"[Maintenance] Market DB 连接失败: {e}")

    # ── Analytics DB 清理 ──
    try:
        analytics_db = AnalyticsSessionLocal()
        try:
            # 4. AI 决策日志
            cutoff_dt = datetime.fromtimestamp(now_ts - RETENTION["ai_decision_logs"])
            result = analytics_db.execute(
                text("DELETE FROM ai_decision_logs WHERE created_at < :cutoff"),
                {"cutoff": cutoff_dt.strftime("%Y-%m-%d %H:%M:%S")},
            )
            if result.rowcount > 0:
                total_deleted += result.rowcount
                logger.info(f"[Maintenance] 清理AI决策日志: {result.rowcount:,} 行")

            # 5. LLM 使用日志
            cutoff_dt = datetime.fromtimestamp(now_ts - RETENTION["llm_usage_logs"])
            result = analytics_db.execute(
                text("DELETE FROM llm_usage_logs WHERE created_at < :cutoff"),
                {"cutoff": cutoff_dt.strftime("%Y-%m-%d %H:%M:%S")},
            )
            if result.rowcount > 0:
                total_deleted += result.rowcount
                logger.info(f"[Maintenance] 清理LLM日志: {result.rowcount:,} 行")

            # 6. 策略分析日志
            cutoff_dt = datetime.fromtimestamp(now_ts - RETENTION["strategy_analysis_logs"])
            result = analytics_db.execute(
                text("DELETE FROM strategy_analysis_logs WHERE created_at < :cutoff"),
                {"cutoff": cutoff_dt.strftime("%Y-%m-%d %H:%M:%S")},
            )
            if result.rowcount > 0:
                total_deleted += result.rowcount
                logger.info(f"[Maintenance] 清理策略日志: {result.rowcount:,} 行")

            analytics_db.commit()
        except Exception as e:
            analytics_db.rollback()
            logger.error(f"[Maintenance] Analytics DB 清理失败: {e}", exc_info=True)
        finally:
            analytics_db.close()
    except Exception as e:
        logger.error(f"[Maintenance] Analytics DB 连接失败: {e}")

    elapsed = time.time() - start
    if total_deleted > 0:
        logger.info(f"[Maintenance] 清理完成: 共删除 {total_deleted:,} 行, 耗时 {elapsed:.1f}s")
    else:
        logger.debug(f"[Maintenance] 无需清理, 耗时 {elapsed:.1f}s")
