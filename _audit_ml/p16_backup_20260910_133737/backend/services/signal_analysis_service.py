"""
Signal Analysis Service

Provides statistical analysis of market flow indicators to help users
set appropriate signal thresholds.
"""

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session
from sqlalchemy import func

logger = logging.getLogger(__name__)

# Minimum samples required (15 minutes of 5m data = 3 samples)
MIN_SAMPLES = 3
# Warning threshold for limited data
LIMITED_DATA_THRESHOLD = 10


class SignalAnalysisService:
    """Service for analyzing market flow indicators and suggesting thresholds."""

    def analyze_metric(
        self,
        db: Session,
        symbol: str,
        metric: str,
        period: str,
        days: int = 7
    ) -> Dict[str, Any]:
        """
        Analyze a metric and provide statistical summary with threshold suggestions.

        Args:
            db: Database session
            symbol: Trading symbol (e.g., "BTC")
            metric: Metric type (e.g., "oi_delta_percent", "cvd")
            period: Time period (e.g., "5m", "15m")
            days: Number of days to analyze (default 7)

        Returns:
            Dict with statistics and suggestions, or error info
        """
        try:
            # Map old metric names to new names (backward compatibility)
            metric_name_map = {
                "oi_delta_percent": "oi_delta",
                "funding_rate": "funding",
                "taker_buy_ratio": "taker_ratio",
            }
            metric = metric_name_map.get(metric, metric)

            # Handle taker_volume specially (returns complete result dict)
            if metric == "taker_volume":
                from services.market_flow_indicators import TIMEFRAME_MS
                if period not in TIMEFRAME_MS:
                    raise ValueError(f"Unsupported period: {period}")
                interval_ms = TIMEFRAME_MS[period]
                # 修时�?bug：用 UTC-aware 计算 Unix 毫秒
                current_time_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
                start_time_ms = current_time_ms - (days * 24 * 60 * 60 * 1000)
                return self._analyze_taker_volume(
                    db, symbol, interval_ms, start_time_ms, current_time_ms, days
                )

            # Get historical values for the metric
            values, time_range = self._get_metric_history(db, symbol, metric, period, days)

            if len(values) < MIN_SAMPLES:
                return {
                    "status": "insufficient_data",
                    "message": f"Need at least {MIN_SAMPLES} samples, found {len(values)}",
                    "sample_count": len(values),
                    "required_samples": MIN_SAMPLES
                }

            # Determine precision based on metric type
            # funding needs higher precision (values like 0.00125%)
            precision = 6 if metric == "funding" else 4

            # Calculate statistics
            stats = self._calculate_statistics(values, precision)

            # Generate threshold suggestions
            suggestions = self._generate_suggestions(stats, metric)

            result = {
                "status": "ok",
                "symbol": symbol,
                "metric": metric,
                "period": period,
                "sample_count": len(values),
                "time_range_hours": time_range,
                "statistics": stats,
                "suggestions": suggestions
            }

            # Add warning if limited data
            if len(values) < LIMITED_DATA_THRESHOLD:
                result["warning"] = f"Limited data ({len(values)} samples). Statistics may not be representative."

            return result

        except Exception as e:
            logger.error(f"Error analyzing metric {metric} for {symbol}: {e}", exc_info=True)
            return {
                "status": "error",
                "message": str(e)
            }

    def _get_metric_history(
        self,
        db: Session,
        symbol: str,
        metric: str,
        period: str,
        days: int
    ) -> tuple[List[float], float]:
        """Get historical values for a metric. Returns (values, time_range_hours)."""
        from services.market_flow_indicators import TIMEFRAME_MS, floor_timestamp
        from backend.database.models import MarketAssetMetrics, MarketTradesAggregated, MarketOrderbookSnapshots

        if period not in TIMEFRAME_MS:
            raise ValueError(f"Unsupported period: {period}")

        interval_ms = TIMEFRAME_MS[period]
        # 修时�?bug：用 UTC-aware 计算 Unix 毫秒
        current_time_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_time_ms = current_time_ms - (days * 24 * 60 * 60 * 1000)

        values = []
        min_ts = None
        max_ts = None

        # Metric names aligned with K-line indicators (MarketFlowIndicators.tsx)
        # cvd, taker_volume, oi, oi_delta, funding, depth_ratio, order_imbalance
        if metric == "oi_delta":
            values, min_ts, max_ts = self._get_oi_delta_history(
                db, symbol, interval_ms, start_time_ms, current_time_ms
            )
        elif metric == "cvd":
            values, min_ts, max_ts = self._get_cvd_history(
                db, symbol, interval_ms, start_time_ms, current_time_ms
            )
        elif metric == "depth_ratio":
            values, min_ts, max_ts = self._get_depth_ratio_history(
                db, symbol, interval_ms, start_time_ms, current_time_ms
            )
        elif metric == "order_imbalance":
            values, min_ts, max_ts = self._get_imbalance_history(
                db, symbol, interval_ms, start_time_ms, current_time_ms
            )
        elif metric == "taker_ratio":
            # Taker buy/sell ratio (buy/sell), aligned with K-line TAKER indicator
            values, min_ts, max_ts = self._get_taker_ratio_history(
                db, symbol, interval_ms, start_time_ms, current_time_ms
            )
        elif metric == "funding":
            values, min_ts, max_ts = self._get_funding_history(
                db, symbol, interval_ms, start_time_ms, current_time_ms
            )
        elif metric == "oi":
            values, min_ts, max_ts = self._get_oi_history(
                db, symbol, interval_ms, start_time_ms, current_time_ms
            )
        else:
            raise ValueError(f"Unsupported metric: {metric}")

        # Calculate time range in hours
        time_range_hours = 0.0
        if min_ts and max_ts:
            time_range_hours = (max_ts - min_ts) / (1000 * 60 * 60)

        return values, time_range_hours

    def _get_oi_delta_history(self, db, symbol, interval_ms, start_time_ms, current_time_ms):
        """Get OI delta percentage history."""
        from services.market_flow_indicators import floor_timestamp
        from backend.database.models import MarketAssetMetrics

        records = db.query(
            MarketAssetMetrics.timestamp,
            MarketAssetMetrics.open_interest
        ).filter(
            MarketAssetMetrics.symbol == symbol.upper(),
            MarketAssetMetrics.timestamp >= start_time_ms,
            MarketAssetMetrics.timestamp <= current_time_ms
        ).order_by(MarketAssetMetrics.timestamp).all()

        if not records:
            return [], None, None

        # Bucket by period
        buckets = {}
        for ts, oi in records:
            bucket_ts = floor_timestamp(ts, interval_ms)
            buckets[bucket_ts] = float(oi) if oi else None

        # Calculate deltas
        sorted_times = sorted(buckets.keys())
        values = []
        for i in range(1, len(sorted_times)):
            prev_oi = buckets[sorted_times[i-1]]
            curr_oi = buckets[sorted_times[i]]
            if prev_oi and curr_oi and prev_oi != 0:
                delta_pct = ((curr_oi - prev_oi) / prev_oi) * 100
                values.append(delta_pct)

        min_ts = sorted_times[0] if sorted_times else None
        max_ts = sorted_times[-1] if sorted_times else None
        return values, min_ts, max_ts

    def _get_cvd_history(self, db, symbol, interval_ms, start_time_ms, current_time_ms):
        """Get CVD history."""
        from services.market_flow_indicators import floor_timestamp
        from backend.database.models import MarketTradesAggregated

        records = db.query(
            MarketTradesAggregated.timestamp,
            MarketTradesAggregated.taker_buy_notional,
            MarketTradesAggregated.taker_sell_notional
        ).filter(
            MarketTradesAggregated.symbol == symbol.upper(),
            MarketTradesAggregated.timestamp >= start_time_ms,
            MarketTradesAggregated.timestamp <= current_time_ms
        ).order_by(MarketTradesAggregated.timestamp).all()

        if not records:
            return [], None, None

        buckets = {}
        for ts, buy, sell in records:
            bucket_ts = floor_timestamp(ts, interval_ms)
            if bucket_ts not in buckets:
                buckets[bucket_ts] = {"buy": 0, "sell": 0}
            buckets[bucket_ts]["buy"] += float(buy or 0)
            buckets[bucket_ts]["sell"] += float(sell or 0)

        sorted_times = sorted(buckets.keys())
        values = [buckets[ts]["buy"] - buckets[ts]["sell"] for ts in sorted_times]

        min_ts = sorted_times[0] if sorted_times else None
        max_ts = sorted_times[-1] if sorted_times else None
        return values, min_ts, max_ts

    def _get_depth_ratio_history(self, db, symbol, interval_ms, start_time_ms, current_time_ms):
        """Get depth ratio (bid/ask) history."""
        from services.market_flow_indicators import floor_timestamp
        from backend.database.models import MarketOrderbookSnapshots

        records = db.query(
            MarketOrderbookSnapshots.timestamp,
            MarketOrderbookSnapshots.bid_depth_5,
            MarketOrderbookSnapshots.ask_depth_5
        ).filter(
            MarketOrderbookSnapshots.symbol == symbol.upper(),
            MarketOrderbookSnapshots.timestamp >= start_time_ms,
            MarketOrderbookSnapshots.timestamp <= current_time_ms
        ).order_by(MarketOrderbookSnapshots.timestamp).all()

        if not records:
            return [], None, None

        buckets = {}
        for ts, bid, ask in records:
            bucket_ts = floor_timestamp(ts, interval_ms)
            buckets[bucket_ts] = {"bid": float(bid or 0), "ask": float(ask or 0)}

        sorted_times = sorted(buckets.keys())
        values = []
        for ts in sorted_times:
            ask = buckets[ts]["ask"]
            if ask > 0:
                values.append(buckets[ts]["bid"] / ask)

        min_ts = sorted_times[0] if sorted_times else None
        max_ts = sorted_times[-1] if sorted_times else None
        return values, min_ts, max_ts

    def _get_imbalance_history(self, db, symbol, interval_ms, start_time_ms, current_time_ms):
        """Get order imbalance history."""
        from services.market_flow_indicators import floor_timestamp
        from backend.database.models import MarketOrderbookSnapshots

        records = db.query(
            MarketOrderbookSnapshots.timestamp,
            MarketOrderbookSnapshots.bid_depth_5,
            MarketOrderbookSnapshots.ask_depth_5
        ).filter(
            MarketOrderbookSnapshots.symbol == symbol.upper(),
            MarketOrderbookSnapshots.timestamp >= start_time_ms,
            MarketOrderbookSnapshots.timestamp <= current_time_ms
        ).order_by(MarketOrderbookSnapshots.timestamp).all()

        if not records:
            return [], None, None

        buckets = {}
        for ts, bid, ask in records:
            bucket_ts = floor_timestamp(ts, interval_ms)
            buckets[bucket_ts] = {"bid": float(bid or 0), "ask": float(ask or 0)}

        sorted_times = sorted(buckets.keys())
        values = []
        for ts in sorted_times:
            bid, ask = buckets[ts]["bid"], buckets[ts]["ask"]
            total = bid + ask
            if total > 0:
                values.append((bid - ask) / total)

        min_ts = sorted_times[0] if sorted_times else None
        max_ts = sorted_times[-1] if sorted_times else None
        return values, min_ts, max_ts

    def _get_taker_ratio_history(self, db, symbol, interval_ms, start_time_ms, current_time_ms):
        """Get taker buy/sell log ratio history. Uses ln(buy/sell) for symmetry around 0.

        Log transformation makes the ratio symmetric:
        - ln(2.0) = +0.69 (buyers 2x sellers)
        - ln(1.0) = 0 (balanced)
        - ln(0.5) = -0.69 (sellers 2x buyers)
        """
        import math
        from services.market_flow_indicators import floor_timestamp
        from backend.database.models import MarketTradesAggregated

        records = db.query(
            MarketTradesAggregated.timestamp,
            MarketTradesAggregated.taker_buy_notional,
            MarketTradesAggregated.taker_sell_notional
        ).filter(
            MarketTradesAggregated.symbol == symbol.upper(),
            MarketTradesAggregated.timestamp >= start_time_ms,
            MarketTradesAggregated.timestamp <= current_time_ms
        ).order_by(MarketTradesAggregated.timestamp).all()

        if not records:
            return [], None, None

        buckets = {}
        for ts, buy, sell in records:
            bucket_ts = floor_timestamp(ts, interval_ms)
            if bucket_ts not in buckets:
                buckets[bucket_ts] = {"buy": 0, "sell": 0}
            buckets[bucket_ts]["buy"] += float(buy or 0)
            buckets[bucket_ts]["sell"] += float(sell or 0)

        sorted_times = sorted(buckets.keys())
        values = []
        for ts in sorted_times:
            buy = buckets[ts]["buy"]
            sell = buckets[ts]["sell"]
            # Log ratio = ln(buy/sell), symmetric around 0
            if buy > 0 and sell > 0:
                values.append(math.log(buy / sell))

        min_ts = sorted_times[0] if sorted_times else None
        max_ts = sorted_times[-1] if sorted_times else None
        return values, min_ts, max_ts

    def _get_funding_history(self, db, symbol, interval_ms, start_time_ms, current_time_ms):
        """Get funding rate history. Aligned with K-line FUNDING indicator."""
        from services.market_flow_indicators import floor_timestamp
        from backend.database.models import MarketAssetMetrics

        records = db.query(
            MarketAssetMetrics.timestamp,
            MarketAssetMetrics.funding_rate
        ).filter(
            MarketAssetMetrics.symbol == symbol.upper(),
            MarketAssetMetrics.timestamp >= start_time_ms,
            MarketAssetMetrics.timestamp <= current_time_ms,
            MarketAssetMetrics.funding_rate.isnot(None)
        ).order_by(MarketAssetMetrics.timestamp).all()

        if not records:
            return [], None, None

        buckets = {}
        for ts, funding in records:
            bucket_ts = floor_timestamp(ts, interval_ms)
            buckets[bucket_ts] = float(funding) * 100  # Convert to percentage

        sorted_times = sorted(buckets.keys())
        values = [buckets[ts] for ts in sorted_times]

        min_ts = sorted_times[0] if sorted_times else None
        max_ts = sorted_times[-1] if sorted_times else None
        return values, min_ts, max_ts

    def _get_oi_history(self, db, symbol, interval_ms, start_time_ms, current_time_ms):
        """Get absolute OI history. Aligned with K-line OI indicator (raw value, no conversion)."""
        from services.market_flow_indicators import floor_timestamp
        from backend.database.models import MarketAssetMetrics

        records = db.query(
            MarketAssetMetrics.timestamp,
            MarketAssetMetrics.open_interest
        ).filter(
            MarketAssetMetrics.symbol == symbol.upper(),
            MarketAssetMetrics.timestamp >= start_time_ms,
            MarketAssetMetrics.timestamp <= current_time_ms,
            MarketAssetMetrics.open_interest.isnot(None)
        ).order_by(MarketAssetMetrics.timestamp).all()

        if not records:
            return [], None, None

        buckets = {}
        for ts, oi in records:
            bucket_ts = floor_timestamp(ts, interval_ms)
            buckets[bucket_ts] = float(oi)  # Raw value, no conversion

        sorted_times = sorted(buckets.keys())
        values = [buckets[ts] for ts in sorted_times]

        min_ts = sorted_times[0] if sorted_times else None
        max_ts = sorted_times[-1] if sorted_times else None
        return values, min_ts, max_ts

    def _calculate_statistics(self, values: List[float], precision: int = 4) -> Dict[str, Any]:
        """Calculate statistical summary of values."""
        import numpy as np

        arr = np.array(values)
        abs_arr = np.abs(arr)
        return {
            "mean": round(float(np.mean(arr)), precision),
            "std": round(float(np.std(arr)), precision),
            "min": round(float(np.min(arr)), precision),
            "max": round(float(np.max(arr)), precision),
            "abs_percentiles": {
                "p75": round(float(np.percentile(abs_arr, 75)), precision),
                "p90": round(float(np.percentile(abs_arr, 90)), precision),
                "p95": round(float(np.percentile(abs_arr, 95)), precision),
                "p99": round(float(np.percentile(abs_arr, 99)), precision)
            }
        }

    def _generate_suggestions(self, stats: Dict[str, Any], metric: str) -> Dict[str, Any]:
        """Generate threshold suggestions based on statistics."""
        import math
        p = stats["abs_percentiles"]

        suggestions = {
            "aggressive": {
                "threshold": p["p75"],
                "description": "~25% trigger rate"
            },
            "moderate": {
                "threshold": p["p90"],
                "description": "~10% trigger rate",
                "recommended": True
            },
            "conservative": {
                "threshold": p["p95"],
                "description": "~5% trigger rate"
            }
        }

        # For taker_ratio (log values), add multiplier info for user understanding
        if metric == "taker_ratio":
            for key in suggestions:
                log_val = suggestions[key]["threshold"]
                multiplier = round(math.exp(abs(log_val)), 2)
                suggestions[key]["multiplier"] = multiplier
                suggestions[key]["description"] += f" ({multiplier}x)"

        return suggestions

    def _analyze_taker_volume(self, db, symbol, interval_ms, start_time_ms, current_time_ms, days):
        """
        Analyze taker_volume composite signal.
        Returns statistics for both ratio and volume dimensions.
        """
        from services.market_flow_indicators import floor_timestamp
        from backend.database.models import MarketTradesAggregated
        import numpy as np

        records = db.query(
            MarketTradesAggregated.timestamp,
            MarketTradesAggregated.taker_buy_notional,
            MarketTradesAggregated.taker_sell_notional
        ).filter(
            MarketTradesAggregated.symbol == symbol.upper(),
            MarketTradesAggregated.timestamp >= start_time_ms,
            MarketTradesAggregated.timestamp <= current_time_ms
        ).order_by(MarketTradesAggregated.timestamp).all()

        if not records:
            return {"status": "insufficient_data", "message": "No data available"}

        buckets = {}
        for ts, buy, sell in records:
            bucket_ts = floor_timestamp(ts, interval_ms)
            if bucket_ts not in buckets:
                buckets[bucket_ts] = {"buy": 0, "sell": 0}
            buckets[bucket_ts]["buy"] += float(buy or 0)
            buckets[bucket_ts]["sell"] += float(sell or 0)

        sorted_times = sorted(buckets.keys())
        if len(sorted_times) < MIN_SAMPLES:
            return {
                "status": "insufficient_data",
                "message": f"Need at least {MIN_SAMPLES} samples, found {len(sorted_times)}"
            }

        # Calculate log ratio and volume for each period
        # Log ratio = ln(buy/sell), symmetric around 0
        # >0 means buyers dominate, <0 means sellers dominate
        import math
        ratios = []
        volumes = []
        for ts in sorted_times:
            buy = buckets[ts]["buy"]
            sell = buckets[ts]["sell"]
            total = buy + sell
            if total > 0 and buy > 0 and sell > 0:
                ratio = math.log(buy / sell)  # Log transformation for symmetry
                ratios.append(ratio)
                volumes.append(total)

        if len(ratios) < MIN_SAMPLES:
            return {
                "status": "insufficient_data",
                "message": f"Need at least {MIN_SAMPLES} valid samples"
            }

        # Calculate statistics for ratio
        ratio_arr = np.array(ratios)
        ratio_stats = {
            "mean": round(float(np.mean(ratio_arr)), 2),
            "min": round(float(np.min(ratio_arr)), 2),
            "max": round(float(np.max(ratio_arr)), 2),
            "p75": round(float(np.percentile(ratio_arr, 75)), 2),
            "p90": round(float(np.percentile(ratio_arr, 90)), 2),
            "p95": round(float(np.percentile(ratio_arr, 95)), 2),
        }

        # Calculate statistics for volume
        vol_arr = np.array(volumes)
        volume_stats = {
            "mean": round(float(np.mean(vol_arr)), 0),
            "min": round(float(np.min(vol_arr)), 0),
            "max": round(float(np.max(vol_arr)), 0),
            "p25": round(float(np.percentile(vol_arr, 25)), 0),
            "p50": round(float(np.percentile(vol_arr, 50)), 0),
            "p75": round(float(np.percentile(vol_arr, 75)), 0),
        }

        time_range_hours = 0.0
        if sorted_times:
            time_range_hours = (sorted_times[-1] - sorted_times[0]) / (1000 * 60 * 60)

        # Convert log ratio suggestions back to multiplier for user-friendly display
        # User sets multiplier (e.g., 1.5), backend converts to log for comparison
        return {
            "status": "ok",
            "symbol": symbol,
            "metric": "taker_volume",
            "period": f"{interval_ms // 60000}m",
            "sample_count": len(ratios),
            "time_range_hours": round(time_range_hours, 1),
            "ratio_statistics": ratio_stats,  # Log values for display
            "volume_statistics": volume_stats,
            "suggestions": {
                "ratio": {
                    # Convert log values back to multiplier: exp(log_ratio)
                    "aggressive": round(math.exp(abs(ratio_stats["p75"])), 2),
                    "moderate": round(math.exp(abs(ratio_stats["p90"])), 2),
                    "conservative": round(math.exp(abs(ratio_stats["p95"])), 2)
                },
                "volume": {
                    "low": volume_stats["p25"],
                    "medium": volume_stats["p50"],
                    "high": volume_stats["p75"]
                }
            }
        }


# Singleton instance
signal_analysis_service = SignalAnalysisService()
