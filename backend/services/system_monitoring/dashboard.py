"""
ATAS V2 监控仪表板

修复记录：
- 修复 Windows 平台 psutil.disk_usage('/') 路径不兼容的问题
- 修复 system_health 硬编码为 95.0 的问题，改为基于真实指标动态计算
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Any
import sys
import os
import psutil


@dataclass
class DashboardMetrics:
    timestamp: datetime
    cpu_usage: float
    memory_usage: float
    disk_usage: float
    active_strategies: int
    total_positions: int
    daily_pnl: float
    system_health: str  # 改为字符串描述，如 "正常", "警告", "危险"


class MonitoringDashboard:
    def __init__(self):
        pass
    
    def _get_disk_usage(self) -> float:
        """跨平台获取磁盘使用率"""
        try:
            if sys.platform == 'win32':
                # Windows: 使用当前工作目录所在的驱动器
                drive = os.path.splitdrive(os.getcwd())[0]
                if not drive:
                    drive = 'C:\\'
                else:
                    drive = drive + '\\'
                return psutil.disk_usage(drive).percent
            else:
                # Linux/macOS
                return psutil.disk_usage('/').percent
        except Exception:
            return 0.0
    
    def _calculate_system_health(self, cpu: float, memory: float, disk: float) -> str:
        """
        基于真实系统指标动态计算健康状态
        
        判断规则：
        - 任一指标 > 90% -> "危险"
        - 任一指标 > 75% -> "警告"  
        - 所有指标 < 75% -> "正常"
        """
        if cpu > 90 or memory > 90 or disk > 90:
            return "危险"
        elif cpu > 75 or memory > 75 or disk > 75:
            return "警告"
        return "正常"
    
    def get_metrics(self, portfolio: Dict[str, Any]) -> DashboardMetrics:
        """获取监控仪表板数据"""
        cpu = psutil.cpu_percent()
        memory = psutil.virtual_memory().percent
        disk = self._get_disk_usage()
        health = self._calculate_system_health(cpu, memory, disk)
        
        return DashboardMetrics(
            timestamp=datetime.now(),
            cpu_usage=cpu,
            memory_usage=memory,
            disk_usage=disk,
            active_strategies=portfolio.get('active_strategies', 0),
            total_positions=len(portfolio.get('positions', {})),
            daily_pnl=portfolio.get('daily_pnl', 0),
            system_health=health
        )


def get_dashboard_data(portfolio: Dict[str, Any]) -> DashboardMetrics:
    dashboard = MonitoringDashboard()
    return dashboard.get_metrics(portfolio)
