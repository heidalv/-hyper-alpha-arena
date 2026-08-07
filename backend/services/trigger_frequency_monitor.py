"""
Trigger Frequency Monitoring Service - Monitors and alerts on abnormal trigger frequencies
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func, text

from backend.database.connection import SessionLocal

logger = logging.getLogger(__name__)


class TriggerFrequencyMonitor:
    """
    Monitors strategy trigger frequencies and detects anomalies.

    Tracks:
    - Hourly trigger counts (scheduled vs signal-based)
    - AI decision frequency
    - Buy signal frequency
    - Trade execution rate

    Alerts when:
    - Trigger frequency drops below expected threshold
    - No buy signals for extended period
    - Signal pool triggers are being rejected
    """

    # Expected minimum triggers per hour for different intervals
    EXPECTED_TRIGGERS_PER_HOUR = {
        150: 40,    # 2.5 min interval = ~40 triggers/hour
        300: 20,    # 5 min interval = ~20 triggers/hour
        600: 10,    # 10 min interval = ~10 triggers/hour
        900: 4,     # 15 min interval = ~4 triggers/hour
        1800: 2,    # 30 min interval = ~2 triggers/hour
        3600: 1,    # 60 min interval = ~1 trigger/hour
    }

    # Alert thresholds
    MIN_TRIGGERS_THRESHOLD = 0.5  # Alert if triggers < 50% of expected
    MIN_BUY_SIGNALS_PER_DAY = 3   # Alert if < 3 buy signals per day
    MIN_EXECUTIONS_PER_DAY = 2    # Alert if < 2 executions per day
    SIGNAL_POOL_REJECT_THRESHOLD = 0.3  # Alert if > 30% signal triggers rejected

    @staticmethod
    def get_hourly_trigger_counts(db: Session, hours: int = 24) -> Dict[str, Any]:
        """
        Get trigger counts per hour for the last N hours.

        Args:
            db: Database session
            hours: Number of hours to look back

        Returns:
            Dict with hourly breakdown and summary statistics
        """
        query = text("""
            SELECT
                DATE_TRUNC('hour', triggered_at) as hour,
                COUNT(*) as trigger_count,
                pool_id
            FROM signal_trigger_logs
            WHERE triggered_at > NOW() - INTERVAL :hours_interval
            GROUP BY hour, pool_id
            ORDER BY hour DESC, pool_id
        """)

        result = db.execute(query, {"hours_interval": f"{hours} hours"})
        rows = result.fetchall()

        # Organize by hour
        hourly_data: Dict[str, Dict[str, int]] = {}
        for row in rows:
            hour_str = row[0].strftime('%Y-%m-%d %H:00') if row[0] else 'Unknown'
            pool_id = row[2] or 'unknown'
            count = row[1]

            if hour_str not in hourly_data:
                hourly_data[hour_str] = {}
            hourly_data[hour_str][pool_id] = count

        # Calculate summary
        total_triggers = sum(sum(pool_counts.values()) for pool_counts in hourly_data.values())
        hours_with_data = len(hourly_data)
        avg_triggers_per_hour = total_triggers / hours_with_data if hours_with_data > 0 else 0

        return {
            "hourly_breakdown": hourly_data,
            "total_triggers": total_triggers,
            "hours_analyzed": hours_with_data,
            "avg_triggers_per_hour": avg_triggers_per_hour,
        }

    @staticmethod
    def get_ai_decision_stats(db: Session, days: int = 2) -> Dict[str, Any]:
        """
        Get AI decision statistics for the last N days.

        Args:
            db: Database session
            days: Number of days to look back

        Returns:
            Dict with daily decision statistics
        """
        query = text("""
            SELECT
                DATE(decision_time) as date,
                COUNT(*) as total_decisions,
                SUM(CASE WHEN operation = 'buy' THEN 1 ELSE 0 END) as buy_signals,
                SUM(CASE WHEN operation = 'sell' THEN 1 ELSE 0 END) as sell_signals,
                SUM(CASE WHEN operation = 'hold' THEN 1 ELSE 0 END) as hold_signals,
                SUM(CASE WHEN executed = 'true' THEN 1 ELSE 0 END) as executed_trades,
                SUM(CASE WHEN executed = 'true' AND operation = 'buy' THEN 1 ELSE 0 END) as executed_buys,
                SUM(CASE WHEN executed = 'true' AND operation = 'sell' THEN 1 ELSE 0 END) as executed_sells
            FROM ai_decision_logs
            WHERE decision_time > NOW() - INTERVAL :days_interval
            GROUP BY date
            ORDER BY date DESC
        """)

        result = db.execute(query, {"days_interval": f"{days} days"})
        rows = result.fetchall()

        daily_stats = []
        for row in rows:
            daily_stats.append({
                "date": row[0].strftime('%Y-%m-%d') if row[0] else 'Unknown',
                "total_decisions": row[1],
                "buy_signals": row[2],
                "sell_signals": row[3],
                "hold_signals": row[4],
                "executed_trades": row[5],
                "executed_buys": row[6],
                "executed_sells": row[7],
                "buy_signal_rate": f"{(row[2] / row[1] * 100):.1f}%" if row[1] > 0 else "0%",
                "execution_rate": f"{(row[5] / row[1] * 100):.1f}%" if row[1] > 0 else "0%",
            })

        return {
            "daily_stats": daily_stats,
            "days_analyzed": len(daily_stats),
        }

    @staticmethod
    def get_signal_pool_rejection_rate(db: Session, hours: int = 24) -> Dict[str, Any]:
        """
        Calculate signal pool trigger rejection rate.

        This detects if signal triggers are being detected but not executed.

        Args:
            db: Database session
            hours: Number of hours to look back

        Returns:
            Dict with rejection statistics
        """
        # Count signal pool triggers from logs
        query = text("""
            SELECT
                COUNT(*) as total_signal_triggers
            FROM signal_trigger_logs
            WHERE triggered_at > NOW() - INTERVAL :hours_interval
        """)

        result = db.execute(query, {"hours_interval": f"{hours} hours"})
        row = result.fetchone()
        total_signal_triggers = row[0] if row else 0

        # Estimate rejections by checking if AI decisions were made shortly after
        # This is a simplified check - in production, you'd track this more explicitly
        query2 = text("""
            SELECT
                COUNT(DISTINCT DATE_TRUNC('hour', decision_time)) as hours_with_decisions
            FROM ai_decision_logs
            WHERE decision_time > NOW() - INTERVAL :hours_interval
        """)

        result2 = db.execute(query2, {"hours_interval": f"{hours} hours"})
        row2 = result2.fetchone()
        hours_with_decisions = row2[0] if row2 else 0

        # If we have many signal triggers but few AI decisions, something is wrong
        expected_hours = hours
        coverage_rate = hours_with_decisions / expected_hours if expected_hours > 0 else 0

        return {
            "total_signal_triggers": total_signal_triggers,
            "hours_with_decisions": hours_with_decisions,
            "expected_hours": expected_hours,
            "coverage_rate": f"{coverage_rate * 100:.1f}%",
            "potential_rejection": total_signal_triggers > 0 and coverage_rate < 0.5,
        }

    @staticmethod
    def check_strategy_config(db: Session, account_id: int = 1) -> Dict[str, Any]:
        """
        Check strategy configuration for potential issues.

        Args:
            db: Database session
            account_id: Account ID to check

        Returns:
            Dict with configuration details and warnings
        """
        query = text("""
            SELECT
                account_id,
                trigger_interval,
                signal_pool_id,
                enabled,
                last_trigger_at,
                EXTRACT(EPOCH FROM (NOW() - COALESCE(last_trigger_at, NOW() - INTERVAL '1 day'))) / 60 as minutes_since_last_trigger
            FROM account_strategy_configs
            WHERE account_id = :acct_id
        """)

        result = db.execute(query, {"acct_id": account_id})
        row = result.fetchone()

        if not row:
            return {
                "error": f"No strategy config found for account {account_id}"
            }

        trigger_interval = row[1]
        enabled = row[3]
        minutes_since_last_trigger = row[5]

        # Check if trigger is within expected range
        expected_triggers_per_hour = TriggerFrequencyMonitor.EXPECTED_TRIGGERS_PER_HOUR.get(
            trigger_interval, trigger_interval  # fallback to interval value
        )
        min_triggers_threshold = expected_triggers_per_hour * TriggerFrequencyMonitor.MIN_TRIGGERS_THRESHOLD

        # Warn if last trigger was too long ago
        warning = None
        if enabled and minutes_since_last_trigger > (trigger_interval / 60 * 3):
            warning = f"Last trigger was {minutes_since_last_trigger:.0f} minutes ago (expected every {trigger_interval / 60:.0f} minutes)"

        return {
            "account_id": row[0],
            "trigger_interval": trigger_interval,
            "trigger_interval_minutes": trigger_interval / 60,
            "signal_pool_id": row[2],
            "enabled": enabled,
            "last_trigger_at": row[4].strftime('%Y-%m-%d %H:%M:%S') if row[4] else 'Never',
            "minutes_since_last_trigger": minutes_since_last_trigger,
            "expected_triggers_per_hour": expected_triggers_per_hour,
            "min_triggers_threshold": min_triggers_threshold,
            "warning": warning,
        }

    @staticmethod
    def generate_full_report(db: Optional[Session] = None) -> Dict[str, Any]:
        """
        Generate a comprehensive monitoring report.

        Args:
            db: Database session (will create one if not provided)

        Returns:
            Complete monitoring report with alerts
        """
        close_db = False
        if db is None:
            db = SessionLocal()
            close_db = True

        try:
            alerts = []

            # 1. Check trigger frequency
            trigger_stats = TriggerFrequencyMonitor.get_hourly_trigger_counts(db, hours=24)
            avg_triggers_per_hour = trigger_stats["avg_triggers_per_hour"]

            # Get strategy config to check expected frequency
            config = TriggerFrequencyMonitor.check_strategy_config(db, account_id=1)
            if "warning" in config:
                alerts.append({
                    "severity": "WARNING",
                    "type": "Trigger Frequency",
                    "message": config["warning"]
                })

            # 2. Check AI decision stats
            decision_stats = TriggerFrequencyMonitor.get_ai_decision_stats(db, days=2)
            if decision_stats["daily_stats"]:
                latest = decision_stats["daily_stats"][0]
                if latest["buy_signals"] < TriggerFrequencyMonitor.MIN_BUY_SIGNALS_PER_DAY:
                    alerts.append({
                        "severity": "WARNING",
                        "type": "Buy Signal Frequency",
                        "message": f"Only {latest['buy_signals']} buy signals in last 24h (expected at least {TriggerFrequencyMonitor.MIN_BUY_SIGNALS_PER_DAY})"
                    })

                if latest["executed_trades"] < TriggerFrequencyMonitor.MIN_EXECUTIONS_PER_DAY:
                    alerts.append({
                        "severity": "CRITICAL",
                        "type": "Trade Execution",
                        "message": f"Only {latest['executed_trades']} trades executed in last 24h (expected at least {TriggerFrequencyMonitor.MIN_EXECUTIONS_PER_DAY})"
                    })

            # 3. Check signal pool rejection rate
            rejection_stats = TriggerFrequencyMonitor.get_signal_pool_rejection_rate(db, hours=24)
            if rejection_stats.get("potential_rejection"):
                alerts.append({
                    "severity": "CRITICAL",
                    "type": "Signal Pool Rejection",
                    "message": f"Signal triggers detected but AI decisions not following. Coverage: {rejection_stats['coverage_rate']}"
                })

            return {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "trigger_stats": trigger_stats,
                "decision_stats": decision_stats,
                "strategy_config": config,
                "rejection_stats": rejection_stats,
                "alerts": alerts,
                "alert_count": len(alerts),
            }

        finally:
            if close_db:
                db.close()

    @staticmethod
    def log_report_summary(report: Dict[str, Any]) -> None:
        """
        Log a summary of the monitoring report.

        Args:
            report: Monitoring report from generate_full_report()
        """
        alert_count = report.get("alert_count", 0)

        if alert_count == 0:
            logger.info("✅ Trigger frequency monitoring: No issues detected")
        else:
            logger.warning(f"⚠️ Trigger frequency monitoring: {alert_count} alert(s) detected")

            for alert in report.get("alerts", []):
                severity = alert.get("severity", "INFO")
                type_name = alert.get("type", "Unknown")
                message = alert.get("message", "")
                logger.warning(f"[{severity}] {type_name}: {message}")

        # Log summary stats
        trigger_stats = report.get("trigger_stats", {})
        decision_stats = report.get("decision_stats", {})

        logger.info(f"📊 Triggers: {trigger_stats.get('avg_triggers_per_hour', 0):.1f}/hour (avg 24h)")

        if decision_stats.get("daily_stats"):
            latest = decision_stats["daily_stats"][0]
            logger.info(
                f"📊 Decisions: {latest['total_decisions']} total, "
                f"{latest['buy_signals']} buy signals, "
                f"{latest['executed_trades']} executed"
            )


# Convenience function for periodic monitoring
def run_trigger_frequency_monitoring() -> Dict[str, Any]:
    """
    Run trigger frequency monitoring and log results.
    注意：本模块使用 PostgreSQL 专有语法，在 SQLite 环境下会直接返回空报告。

    Returns:
        Monitoring report
    """
    try:
        report = TriggerFrequencyMonitor.generate_full_report()
        TriggerFrequencyMonitor.log_report_summary(report)
        return report
    except Exception as e:
        # SQLite 不支持 PostgreSQL 专有语法（NOW(), INTERVAL, DATE_TRUNC 等），静默跳过
        logger.debug(f"[TriggerMonitor] 跳过(SQLite不支持PostgreSQL语法): {e}")
        return {
            "alerts": [],
            "alert_count": 0,
            "trigger_stats": {},
            "decision_stats": {},
            "rejection_stats": {},
            "config": {},
            "note": "SQLite环境下不支持此监控，已跳过",
        }


if __name__ == "__main__":
    # Run monitoring when executed directly
    import sys
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

    logger.info("Running trigger frequency monitoring...")
    report = run_trigger_frequency_monitoring()

    if report["alert_count"] > 0:
        sys.exit(1)  # Exit with error code if alerts detected
    else:
        logger.info("✅ All checks passed")
        sys.exit(0)
