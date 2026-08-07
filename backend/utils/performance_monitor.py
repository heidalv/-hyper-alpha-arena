"""
Performance Monitoring Middleware
APM (Application Performance Monitoring) for Hyper Alpha Arena
"""
import time
import logging
from typing import Callable
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)


class PerformanceMonitorMiddleware(BaseHTTPMiddleware):
    """
    Middleware to monitor API request performance
    
    Tracks:
    - Request duration
    - Request count by endpoint
    - Error rates
    """
    
    def __init__(self, app: ASGIApp, slow_request_threshold: float = 1.0):
        super().__init__(app)
        self.slow_request_threshold = slow_request_threshold
        self._request_count = 0
        self._total_duration = 0.0
        self._error_count = 0
        self._endpoint_stats: dict[str, dict] = {}
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request and track performance"""
        start_time = time.time()
        endpoint = request.url.path
        method = request.method
        
        try:
            response = await call_next(request)
            duration = time.time() - start_time
            
            # Track statistics
            self._request_count += 1
            self._total_duration += duration
            
            if response.status_code >= 400:
                self._error_count += 1
            
            # Update endpoint-specific stats
            key = f"{method} {endpoint}"
            if key not in self._endpoint_stats:
                self._endpoint_stats[key] = {
                    "count": 0,
                    "total_duration": 0.0,
                    "slow_count": 0,
                    "errors": 0
                }
            
            stats = self._endpoint_stats[key]
            stats["count"] += 1
            stats["total_duration"] += duration
            
            if duration > self.slow_request_threshold:
                stats["slow_count"] += 1
                logger.warning(
                    f"Slow request: {method} {endpoint} took {duration:.3f}s"
                )
            
            # Add performance header
            response.headers["X-Process-Time"] = str(duration)
            
            return response
            
        except Exception as e:
            duration = time.time() - start_time
            self._request_count += 1
            self._error_count += 1
            logger.error(
                f"Request failed: {method} {endpoint} - {e} ({duration:.3f}s)"
            )
            raise
    
    def get_stats(self) -> dict:
        """Get performance statistics"""
        avg_duration = (
            self._total_duration / self._request_count 
            if self._request_count > 0 else 0
        )
        
        return {
            "total_requests": self._request_count,
            "total_duration": self._total_duration,
            "average_duration": avg_duration,
            "error_count": self._error_count,
            "error_rate": self._error_count / self._request_count 
                if self._request_count > 0 else 0,
            "endpoints": self._endpoint_stats
        }
    
    def reset_stats(self):
        """Reset all statistics"""
        self._request_count = 0
        self._total_duration = 0.0
        self._error_count = 0
        self._endpoint_stats = {}


class PerformanceMonitor:
    """
    Singleton performance monitor for tracking application metrics
    """
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if not self._initialized:
            self._request_times: list[float] = []
            self._max_history = 1000
            self._initialized = True
    
    def record_request(self, duration: float, endpoint: str, method: str, status_code: int):
        """Record a request duration"""
        self._request_times.append(duration)
        
        # Keep only last N requests
        if len(self._request_times) > self._max_history:
            self._request_times = self._request_times[-self._max_history:]
    
    def get_average_duration(self) -> float:
        """Get average request duration"""
        if not self._request_times:
            return 0.0
        return sum(self._request_times) / len(self._request_times)
    
    def get_p95_duration(self) -> float:
        """Get 95th percentile request duration"""
        if not self._request_times:
            return 0.0
        sorted_times = sorted(self._request_times)
        index = int(len(sorted_times) * 0.95)
        return sorted_times[index] if index < len(sorted_times) else sorted_times[-1]
    
    def get_metrics(self) -> dict:
        """Get all metrics"""
        return {
            "total_requests": len(self._request_times),
            "average_duration": self.get_average_duration(),
            "p95_duration": self.get_p95_duration(),
            "max_duration": max(self._request_times) if self._request_times else 0,
            "min_duration": min(self._request_times) if self._request_times else 0,
        }


# Global instance
performance_monitor = PerformanceMonitor()
