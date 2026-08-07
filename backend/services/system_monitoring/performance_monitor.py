"""ATAS V2 性能监控器"""
from dataclasses import dataclass
from typing import Dict
import time
import psutil

@dataclass
class SystemMetrics:
    cpu: float
    memory: float
    disk_io: Dict
    network_io: Dict
    process_count: int

class PerformanceMonitor:
    def __init__(self):
        self.start_time = time.time()
    
    def get_metrics(self) -> SystemMetrics:
        return SystemMetrics(
            cpu=psutil.cpu_percent(interval=1),
            memory=psutil.virtual_memory().percent,
            disk_io=psutil.disk_io_counters()._asdict() if psutil.disk_io_counters() else {},
            network_io=psutil.net_io_counters()._asdict() if psutil.net_io_counters() else {},
            process_count=len(psutil.pids())
        )

def monitor_performance() -> SystemMetrics:
    monitor = PerformanceMonitor()
    return monitor.get_metrics()
