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


RETENTION = {
    "kline_1m_3m_5m": _retention_days("1m", 30) * 86400,     # 1m/3m/5m 保留 30 天（回填目标 30 天）
    "kline_15m_30m": _retention_days("15m", 90) * 86400,     # 15m/30m 保留 90 天（回填目标 60/90 天）
    "kline_1h_4h": _retention_days("1h", 400) * 86400,       # 1h/4h 保留 400 天（回填目标 210/365 天）
    "ai_decision_logs": 90 * 86400,     # AI 决策日志保留 90 天，便于排查策略卡死/重复决策
    "llm_usage_logs": 14 * 86400,       # LLM 使用日志保留 14 天
    "whale_activities": 7 * 86400,      # 鲸鱼活动保留 7 天
    "strategy_analysis_logs": 90 * 86400,  # 策略分析日志保留 90 天
    "news_events": 30 * 86400,          # 新闻保留 30 天
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
            # 1. K线清理（最大的表）
            for periods, key in [
                (("1m", "3m", "5m"), "kline_1m_3m_5m"),
                (("15m", "30m"), "kline_15m_30m"),
                (("1h", "4h"), "kline_1h_4h"),
            ]:
                cutoff = now_ts - RETENTION[key]
                placeholders = ",".join(f"'{p}'" for p in periods)
                result = market_db.execute(
                    text(f"DELETE FROM crypto_klines WHERE period IN ({placeholders}) AND timestamp < :cutoff"),
                    {"cutoff": cutoff},
                )
                cnt = result.rowcount
                if cnt > 0:
                    total_deleted += cnt
                    logger.info(f"[Maintenance] 清理K线 {periods}: {cnt:,} 行")

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
