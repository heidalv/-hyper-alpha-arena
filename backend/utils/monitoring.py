"""
AI策略系统监控配置

包括：
1. 日志配置
2. 性能指标收集
3. 告警规则
4. 健康检查
"""
import logging
import time
from typing import Dict, Any, Optional
from functools import wraps
from datetime import datetime, timezone


# ===== 日志配置 =====

def configure_logging():
    """配置日志系统"""
    logging_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "detailed": {
                "format": "%(asctime)s [%(levelname)s] %(name)s:%(lineno)d - %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
            "simple": {
                "format": "%(levelname)s - %(message)s",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "level": "INFO",
                "formatter": "simple",
                "stream": "ext://sys.stdout",
            },
            "file": {
                "class": "logging.handlers.RotatingFileHandler",
                "level": "DEBUG",
                "formatter": "detailed",
                "filename": "logs/ai_strategy_system.log",
                "maxBytes": 10485760,  # 10MB
                "backupCount": 5,
            },
            "error_file": {
                "class": "logging.handlers.RotatingFileHandler",
                "level": "ERROR",
                "formatter": "detailed",
                "filename": "logs/ai_strategy_errors.log",
                "maxBytes": 10485760,
                "backupCount": 5,
            },
        },
        "loggers": {
            "backend.services.ai_strategy_engine": {
                "level": "DEBUG",
                "handlers": ["console", "file", "error_file"],
                "propagate": False,
            },
            "backend.services.prompt_training_system": {
                "level": "DEBUG",
                "handlers": ["console", "file", "error_file"],
                "propagate": False,
            },
            "backend.api.ai_strategy_routes": {
                "level": "INFO",
                "handlers": ["console", "file"],
                "propagate": False,
            },
        },
        "root": {
            "level": "INFO",
            "handlers": ["console", "file"],
        },
    }
    
    return logging_config


# ===== 性能监控 =====

class PerformanceMonitor:
    """性能监控器"""
    
    def __init__(self):
        self.metrics: Dict[str, list] = {
            "strategy_execution_time": [],
            "ai_decision_time": [],
            "database_query_time": [],
            "training_time": [],
        }
    
    def record_metric(self, metric_name: str, value: float):
        """记录指标"""
        if metric_name not in self.metrics:
            self.metrics[metric_name] = []
        
        self.metrics[metric_name].append({
            "value": value,
            "timestamp": datetime.now(timezone.utc),
        })
        
        # 保持最近1000条记录
        if len(self.metrics[metric_name]) > 1000:
            self.metrics[metric_name] = self.metrics[metric_name][-1000:]
    
    def get_statistics(self, metric_name: str) -> Dict[str, float]:
        """获取指标统计"""
        if metric_name not in self.metrics or not self.metrics[metric_name]:
            return {}
        
        values = [m["value"] for m in self.metrics[metric_name]]
        values.sort()
        
        n = len(values)
        return {
            "count": n,
            "min": values[0],
            "max": values[-1],
            "mean": sum(values) / n,
            "p50": values[int(n * 0.5)],
            "p95": values[int(n * 0.95)],
            "p99": values[int(n * 0.99)],
        }


# 全局监控实例
performance_monitor = PerformanceMonitor()


def monitor_performance(metric_name: str):
    """性能监控装饰器"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                return result
            finally:
                elapsed = time.time() - start_time
                performance_monitor.record_metric(metric_name, elapsed)
                
                # 如果执行时间过长，记录警告
                if elapsed > 5.0:
                    logging.warning(
                        f"{func.__name__} took {elapsed:.2f}s (metric: {metric_name})"
                    )
        return wrapper
    return decorator


# ===== 告警规则 =====

class AlertManager:
    """告警管理器"""
    
    def __init__(self):
        self.alert_rules = {
            "strategy_execution_failure_rate": {
                "threshold": 0.05,  # 5%
                "window": 3600,  # 1小时
                "severity": "high",
            },
            "api_response_time": {
                "threshold": 3.0,  # 3秒
                "window": 300,  # 5分钟
                "severity": "medium",
            },
            "database_connection_pool": {
                "threshold": 0.8,  # 80%
                "window": 60,  # 1分钟
                "severity": "high",
            },
        }
        
        self.alert_history = []
    
    def check_alert(self, rule_name: str, current_value: float) -> Optional[Dict[str, Any]]:
        """检查是否需要告警"""
        if rule_name not in self.alert_rules:
            return None
        
        rule = self.alert_rules[rule_name]
        
        if current_value > rule["threshold"]:
            alert = {
                "rule": rule_name,
                "value": current_value,
                "threshold": rule["threshold"],
                "severity": rule["severity"],
                "timestamp": datetime.now(timezone.utc),
            }
            
            self.alert_history.append(alert)
            return alert
        
        return None
    
    def send_alert(self, alert: Dict[str, Any]):
        """发送告警（示例实现）"""
        logging.error(
            f"ALERT [{alert['severity'].upper()}] {alert['rule']}: "
            f"value={alert['value']:.2f}, threshold={alert['threshold']:.2f}"
        )
        
        # 这里可以集成钉钉、邮件、短信等告警渠道
        # 示例：发送到钉钉
        # send_to_dingtalk(alert)


# 全局告警管理器
alert_manager = AlertManager()


# ===== 健康检查 =====

class HealthChecker:
    """健康检查器"""
    
    @staticmethod
    def check_database(db) -> bool:
        """检查数据库连接"""
        try:
            db.execute("SELECT 1")
            return True
        except Exception as e:
            logging.error(f"Database health check failed: {e}")
            return False
    
    @staticmethod
    def check_ai_service() -> bool:
        """检查AI服务"""
        # 这里可以添加AI服务健康检查逻辑
        return True
    
    @staticmethod
    def check_strategy_engine(db) -> bool:
        """检查策略引擎（已迁移到 StrategyCoordinator）"""
        try:
            from backend.services.strategy_coordinator import StrategyCoordinator
            return True
        except Exception as e:
            logging.error(f"Strategy engine health check failed: {e}")
            return False
    
    @classmethod
    def get_health_status(cls, db) -> Dict[str, Any]:
        """获取整体健康状态"""
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "healthy",  # healthy, degraded, unhealthy
            "checks": {
                "database": cls.check_database(db),
                "ai_service": cls.check_ai_service(),
                "strategy_engine": cls.check_strategy_engine(db),
            },
        }


# ===== 监控端点 =====

def create_monitoring_endpoints(app):
    """创建监控端点（供API调用）"""
    from fastapi import APIRouter, Depends
    from backend.database.connection import get_db
    from sqlalchemy.orm import Session
    
    router = APIRouter(prefix="/api/monitoring", tags=["Monitoring"])
    
    @router.get("/health")
    def health_check(db: Session = Depends(get_db)):
        """健康检查端点"""
        checker = HealthChecker()
        return checker.get_health_status(db)
    
    @router.get("/metrics")
    def get_metrics():
        """获取性能指标"""
        return {
            "strategy_execution": performance_monitor.get_statistics("strategy_execution_time"),
            "ai_decision": performance_monitor.get_statistics("ai_decision_time"),
            "database_query": performance_monitor.get_statistics("database_query_time"),
            "training": performance_monitor.get_statistics("training_time"),
        }
    
    @router.get("/alerts")
    def get_alerts():
        """获取告警历史"""
        return {
            "total": len(alert_manager.alert_history),
            "recent": alert_manager.alert_history[-10:],
        }
    
    app.include_router(router)
    return router


# ===== 使用示例 =====

if __name__ == "__main__":
    # 配置日志
    import logging.config
    logging.config.dictConfig(configure_logging())
    
    # 测试性能监控
    @monitor_performance("test_function")
    def test_function():
        import time
        time.sleep(0.1)
        return "done"
    
    # 执行几次
    for _ in range(5):
        test_function()
    
    # 查看统计
    print("\n性能统计:")
    print(performance_monitor.get_statistics("test_function"))
    
    # 测试告警
    alert = alert_manager.check_alert("api_response_time", 5.0)
    if alert:
        alert_manager.send_alert(alert)
