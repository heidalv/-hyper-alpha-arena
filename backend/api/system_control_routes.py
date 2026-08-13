"""
System Control API Routes
提供系统控制接口（关闭、重启等）
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, Any
import logging
import os
import signal
import sys
import subprocess

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/system", tags=["System Control"])


# ── RL 协调器 Feature Flags（/api/system/feature-flags，v3 整改测试网）──
_FEATURE_FLAG_MAP = {
    "drl_integration": "ENABLE_DRL_INTEGRATION",
    "kelly_position": "ENABLE_KELLY_POSITION",
    "portfolio_risk": "ENABLE_PORTFOLIO_RISK",
    "coordinator": "ENABLE_COORDINATOR",
    "drl_shadow_mode": "DRL_SHADOW_MODE",
}


class FeatureFlagToggle(BaseModel):
    flag: str
    enabled: bool


def _read_feature_flags() -> Dict[str, bool]:
    from backend.config import settings as S

    out: Dict[str, bool] = {}
    for api_key, env_key in _FEATURE_FLAG_MAP.items():
        out[api_key] = bool(getattr(S, env_key, False))
    return out


@router.get("/feature-flags")
async def get_feature_flags() -> Dict[str, bool]:
    """返回 RL/协调器运行时特性开关（与 system_coordinator.status 一致）。"""
    return _read_feature_flags()


@router.post("/feature-flags")
async def set_feature_flag(body: FeatureFlagToggle) -> Dict[str, Any]:
    """运行时切换特性开关（内存生效，重启恢复 .env）。"""
    if body.flag not in _FEATURE_FLAG_MAP:
        raise HTTPException(status_code=400, detail=f"unknown flag: {body.flag}")
    from backend.config import settings as S

    setattr(S, _FEATURE_FLAG_MAP[body.flag], bool(body.enabled))
    return {"ok": True, "flag": body.flag, "enabled": bool(body.enabled)}


@router.get("/block-report-top")
async def get_block_report_top(n: int = 3, hours: int = 24) -> Dict[str, Any]:
    """
    返回最近 N 小时内阻断事件 Top N 原因 +样例。

    参数：
    - n: 返回前 N 条原因（1..10）
    - hours: 时间窗口（1..48）
    """
    try:
        from backend.services.block_report_aggregator import block_report_aggregator
        n = max(1, min(10, int(n)))
        hours = max(1, min(48, int(hours)))
        return block_report_aggregator.top(n=n, window_sec=hours * 3600)
    except Exception as exc:  # pragma: no cover
        logger.warning("block-report-top failed: %s", exc)
        return {"window_sec": hours * 3600, "total": 0, "top": []}


@router.get("/maturity-state")
async def get_maturity_state_route() -> Dict[str, Any]:
    """返回数据成熟度中枢（MaturityController）当前快照，用于「为什么松/为什么紧」面板。

    返回结构：
    - global / by_symbol_side / by_nature_tier 各维度的 {stage, count, win_rate, conf_relief, n1, n2}
    - stage ∈ {warmup, growth, mature}；conf_relief 为置信度门槛的松紧分（正=放宽）
    - config: 当前 N1/N2 阈值与 warmup 最大放宽分
    成熟度由 maturity_controller 秒级快循环写入，OpenCode 慢循环只调 N1/N2 等高层旋钮。
    """
    try:
        from backend.services.maturity_controller import get_maturity_state
        state = get_maturity_state()
        return {"ok": True, "state": state}
    except Exception as exc:  # pragma: no cover
        logger.warning("maturity-state failed: %s", exc)
        return {"ok": False, "state": {}, "error": str(exc)}



@router.post("/shutdown")
async def shutdown_services() -> Dict[str, Any]:
    """
    关闭所有服务（前端、后端、数据库）
    需要前端二次确认后才会调用此接口
    """
    try:
        logger.info("Received shutdown request from frontend")
        stopped_services = []

        project_root = os.getcwd()

        # 1. 停止前端服务 (port 5173)
        try:
            if sys.platform == 'win32':
                subprocess.run(
                    ['taskkill', '/FI', 'WINDOWTITLE eq Frontend*', '/F'],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5
                )
            else:
                subprocess.run(
                    ['pkill', '-f', 'vite'],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5
                )
            stopped_services.append("Frontend (port 5173)")
            logger.info("Frontend service stopped")
        except Exception as e:
            logger.warning(f"Failed to stop frontend: {e}")

        # 2. 停止数据库服务 (port 5432)
        try:
            pg_ctl_path = os.path.join(project_root, 'postgresql', 'bin', 'pg_ctl.exe' if sys.platform == 'win32' else 'pg_ctl')
            data_dir = os.path.join(project_root, 'postgresql', 'data')

            if os.path.exists(pg_ctl_path):
                subprocess.run(
                    [pg_ctl_path, 'stop', '-D', data_dir, '-m', 'fast'],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10
                )
                stopped_services.append("PostgreSQL (port 5432)")
                logger.info("Database service stopped")
        except Exception as e:
            logger.warning(f"Failed to stop database: {e}")

        # 3. 延迟停止后端自身
        def delayed_shutdown():
            import time
            time.sleep(1)
            logger.info("Backend service stopping...")
            os.kill(os.getpid(), signal.SIGTERM)

        import threading
        threading.Thread(target=delayed_shutdown, daemon=True).start()
        stopped_services.append("Backend (port 8000)")

        return {
            "message": "所有服务正在停止...",
            "stopped_services": stopped_services
        }

    except Exception as e:
        logger.error(f"Shutdown failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to shutdown services: {str(e)}")


@router.get("/status")
async def get_system_status() -> Dict[str, Any]:
    """
    获取系统服务状态
    
    返回:
    - services: 各服务的运行状态
    """
    import socket
    
    import os as _os

    def check_port(port: int) -> bool:
        """检查端口是否被监听"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('localhost', port))
            sock.close()
            return result == 0
        except Exception:
            return False

    def check_sqlite_db() -> bool:
        """检查 SQLite 数据库文件是否存在且可写"""
        try:
            from backend.database.connection import DATABASE_URL
            if DATABASE_URL.startswith("sqlite"):
                db_path = DATABASE_URL.replace("sqlite:///", "")
                return _os.path.isfile(db_path) and _os.access(db_path, _os.W_OK)
            # 非 SQLite（如 PostgreSQL）则检查端口
            return check_port(5432)
        except Exception:
            return False

    # 主前端为 frontend-next:5273；旧 Vite frontend:5173 仅作回退探测
    _fe_port = 5273 if check_port(5273) else (5173 if check_port(5173) else 5273)
    return {
        "services": {
            "frontend": {
                "port": _fe_port,
                "running": check_port(_fe_port),
            },
            "backend": {
                "port": 8000,
                "running": check_port(8000)
            },
            "database": {
                "running": check_sqlite_db()
            }
        }
    }
