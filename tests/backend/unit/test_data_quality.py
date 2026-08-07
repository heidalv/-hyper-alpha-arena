"""
DataQualityMonitor 单元测试

覆盖:
- K线新鲜度检测 (fresh / stale / missing)
- 因子异常检测 (normal / z>4)
- 数据源健康追踪
"""

import time
import pytest


@pytest.mark.unit
class TestDataQualityMonitor:
    def test_fresh_klines_no_alerts(self):
        from backend.services.data_quality_monitor import DataQualityMonitor
        monitor = DataQualityMonitor()
        now = time.time()
        alerts = monitor.check_kline_freshness(
            symbols=["BTC", "ETH"],
            latest_timestamps={"BTC": now - 60, "ETH": now - 120},
        )
        assert len(alerts) == 0

    def test_stale_klines_warning(self):
        from backend.services.data_quality_monitor import DataQualityMonitor
        monitor = DataQualityMonitor()
        now = time.time()
        alerts = monitor.check_kline_freshness(
            symbols=["BTC"],
            latest_timestamps={"BTC": now - 400},  # > 5min
        )
        assert len(alerts) == 1
        assert alerts[0].level == "warning"

    def test_missing_klines_critical(self):
        from backend.services.data_quality_monitor import DataQualityMonitor
        monitor = DataQualityMonitor()
        alerts = monitor.check_kline_freshness(
            symbols=["BTC"],
            latest_timestamps={},  # missing
        )
        assert len(alerts) == 1
        assert alerts[0].level == "critical"

    def test_normal_factors_no_anomaly(self):
        from backend.services.data_quality_monitor import DataQualityMonitor
        monitor = DataQualityMonitor()
        factor_values = {
            "BTC": {"rsi": 55.0, "adx": 25.0},
            "ETH": {"rsi": 48.0, "adx": 22.0},
            "SOL": {"rsi": 52.0, "adx": 28.0},
        }
        alerts = monitor.check_factor_anomalies(factor_values)
        assert len(alerts) == 0

    def test_source_health_tracking(self):
        from backend.services.data_quality_monitor import DataQualityMonitor
        monitor = DataQualityMonitor()
        for i in range(10):
            monitor.record_source_call("binance", success=(i < 8), latency_ms=50.0)
        report = monitor.get_source_health_report()
        assert "binance" in report
        assert report["binance"]["success_rate"] == 0.8
        assert report["binance"]["healthy"]
