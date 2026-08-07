# [fix] 2026-07-06：本文件此前为空，main.py 启动时用包级导入
# `from .services.dingtalk import get_background_tasks, get_volatility_monitor`，
# 但这两个函数实际定义在子模块里，未在此重新导出会触发 ImportError，
# 导致 DingTalk 后台任务（消息推送 + 波动监控）在启动阶段被跳过、从不运行。
#
# 注意：这里必须用非限定导入 `services.dingtalk.xxx`（而不是 `backend.services.dingtalk.xxx`），
# 与 background_tasks.py 内部 `from services.dingtalk.notification_service import ...`
# 保持同一套命名空间。混用两套路径会把本包在两个不同的模块身份
# （backend.services.dingtalk 与 services.dingtalk）下各初始化一次，
# 一旦其中一层还没定义完类就被另一层回头引用，就会触发循环导入 ImportError。
from services.dingtalk.background_tasks import (
    DingTalkBackgroundTasks,
    get_background_tasks,
)
from services.dingtalk.volatility_monitor import (
    VolatilityMonitor,
    get_volatility_monitor,
)

__all__ = [
    "DingTalkBackgroundTasks",
    "get_background_tasks",
    "VolatilityMonitor",
    "get_volatility_monitor",
]
