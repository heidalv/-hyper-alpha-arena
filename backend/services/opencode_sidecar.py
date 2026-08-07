"""OpenCode Sidecar 托管 —— 让 `opencode serve` 成为后端的受托管系统组件。

设计（仿 opencode_shadow_worker 的 subprocess 托管范式）：
- 后端启动时自动拉起 sidecar（127.0.0.1:4096，端口取自 OPENCODE_SERVER_URL）；
- 幂等：若已有外部 sidecar 健康在跑（例如手动跑过 scripts/start_opencode_sidecar.ps1），
  则「收养」而不重复 spawn，避免双实例抢端口；
- 看门狗 ensure_sidecar()：崩溃后自动重启；
- 后端退出时通过 atexit + lifespan shutdown 回收「自己 spawn 的」进程，不动收养的外部实例；
- 失败不致命：opencode CLI 未安装 / key 缺失只打清晰告警，不影响后端主流程。
"""

from __future__ import annotations

import atexit
import logging
import os
import shutil
import subprocess
import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# 本进程 spawn 的 sidecar；None 表示未托管（未启动或已收养外部实例）
_proc: Optional[subprocess.Popen] = None
# 是否「收养」了一个外部已健康的 sidecar（非本进程 spawn，退出时不回收）
_adopted: bool = False

# P0 修复（2026-07-20）：看门狗连续失败计数器 + 熔断。
# 原实现每 2 分钟无脑重启，sidecar 启动就崩 → 24h 内 53 次启动失败 + 113 次循环。
# 现在连续失败超过阈值后进入熔断，不再空转重试，避免日志噪声和资源浪费。
_consecutive_failures: int = 0
_last_failure_ts: float = 0.0
_MAX_CONSECUTIVE_FAILURES = 5  # 连续失败 5 次后熔断
_CIRCUIT_BREAK_SECONDS = 1800  # 熔断 30 分钟后才再尝试


def _repo_root() -> str:
    # backend/services/opencode_sidecar.py → 上三级 = 仓库根（opencode.json 所在）
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _autostart_enabled() -> bool:
    try:
        from backend.config.settings import OPENCODE_ENABLED, OPENCODE_SIDECAR_AUTOSTART
        return bool(OPENCODE_ENABLED) and bool(OPENCODE_SIDECAR_AUTOSTART)
    except Exception:
        return False


def _host_port() -> tuple[str, int]:
    url = "http://127.0.0.1:4096"
    try:
        from backend.config.settings import OPENCODE_SERVER_URL
        url = OPENCODE_SERVER_URL or url
    except Exception:
        pass
    parsed = urlparse(url if "://" in url else f"http://{url}")
    return (parsed.hostname or "127.0.0.1", int(parsed.port or 4096))


def _resolve_opencode_exe() -> Optional[str]:
    """解析 opencode 可执行文件。优先级与原因：
      1. OPENCODE_CLI_PATH 显式配置（非默认 "opencode" 时采信，支持绝对路径/名字）；
      2. npm 全局安装的真实 .exe —— Windows 下 subprocess.Popen 能直接执行 .exe，
         而 shutil.which 常先命中 .CMD 包装器（Popen 直接跑会 WinError 193），故优先它；
      3. PATH 兜底（可能是 .CMD/.cmd，spawn 处用 cmd /c 兜底）。
    找不到返回 None。
    """
    try:
        from backend.config.settings import OPENCODE_CLI_PATH
        cfg = (OPENCODE_CLI_PATH or "").strip()
    except Exception:
        cfg = ""
    if cfg and cfg.lower() != "opencode":
        if os.path.sep in cfg or cfg.lower().endswith((".exe", ".cmd", ".bat")):
            if os.path.isfile(cfg):
                return cfg
        found = shutil.which(cfg)
        if found:
            return found

    appdata = os.environ.get("APPDATA")
    if appdata:
        npm_exe = os.path.join(
            appdata, "npm", "node_modules", "opencode-ai", "bin", "opencode.exe"
        )
        if os.path.isfile(npm_exe):
            return npm_exe

    return shutil.which("opencode")


def _sidecar_env() -> Dict[str, str]:
    """继承当前进程环境；若缺 DEEPSEEK_API_KEY 则解析仓库 .env 注入。"""
    env = os.environ.copy()
    if env.get("DEEPSEEK_API_KEY"):
        return env
    env_file = os.path.join(_repo_root(), ".env")
    try:
        if os.path.isfile(env_file):
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    # 仅补缺失项，不覆盖已有进程环境
                    if key and key not in env:
                        env[key] = val
    except Exception as err:
        logger.debug("[OpenCodeSidecar] 解析 .env 跳过: %s", err)
    return env


def _is_healthy() -> bool:
    try:
        from backend.services.opencode_bridge import health_check
        return bool(health_check())
    except Exception:
        return False


def start_sidecar() -> Dict[str, Any]:
    """启动（或收养）sidecar。受 OPENCODE_ENABLED + OPENCODE_SIDECAR_AUTOSTART 控制。幂等。"""
    global _proc, _adopted

    if not _autostart_enabled():
        return {"ok": False, "skipped": "autostart_disabled"}

    # 已由本进程托管且存活
    if _proc is not None and _proc.poll() is None:
        return {"ok": True, "managed": True, "pid": _proc.pid, "note": "already_managed"}

    host, port = _host_port()

    # 端口已健康 → 收养外部实例，不重复 spawn
    if _is_healthy():
        _adopted = True
        _proc = None
        logger.info("[OpenCodeSidecar] 检测到 %s:%d 已健康，收养外部实例（不再 spawn）", host, port)
        return {"ok": True, "managed": False, "adopted": True}

    exe = _resolve_opencode_exe()
    if not exe:
        logger.warning(
            "[OpenCodeSidecar] 未找到 opencode CLI，sidecar 无法自启。"
            "请安装：npm install -g opencode-ai（或设置 OPENCODE_CLI_PATH）"
        )
        return {"ok": False, "error": "opencode_cli_not_found"}

    log_dir = os.path.join(_repo_root(), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "opencode_sidecar.log")

    # .cmd/.bat 包装器无法被 Popen 直接执行（WinError 193），用 cmd /c 兜底
    args = [exe, "serve", "--port", str(port), "--hostname", host]
    if exe.lower().endswith((".cmd", ".bat")):
        args = ["cmd", "/c"] + args

    try:
        logf = open(log_path, "a", encoding="utf-8")
        _proc = subprocess.Popen(
            args,
            cwd=_repo_root(),
            env=_sidecar_env(),
            stdout=logf,
            stderr=subprocess.STDOUT,
        )
        _adopted = False
    except Exception as err:
        logger.error("[OpenCodeSidecar] spawn 失败: %s", err)
        return {"ok": False, "error": str(err)}

    # 轮询健康最多 ~15s
    deadline = time.time() + 15.0
    while time.time() < deadline:
        if _proc.poll() is not None:
            logger.error("[OpenCodeSidecar] sidecar 启动后立即退出（code=%s），见 %s",
                         _proc.returncode, log_path)
            return {"ok": False, "error": "exited_early", "log": log_path}
        if _is_healthy():
            logger.info("[OpenCodeSidecar] sidecar 已就绪 pid=%s on %s:%d", _proc.pid, host, port)
            return {"ok": True, "managed": True, "pid": _proc.pid, "port": port}
        time.sleep(1.0)

    logger.warning("[OpenCodeSidecar] sidecar 15s 内未通过健康检查（pid=%s），保留进程继续观察", _proc.pid)
    return {"ok": False, "error": "health_timeout", "pid": _proc.pid}


def stop_sidecar() -> None:
    """仅回收本进程 spawn 的 sidecar；收养的外部实例不动。"""
    global _proc, _adopted
    if _proc is not None and _proc.poll() is None:
        try:
            _proc.terminate()
            _proc.wait(timeout=10)
            logger.info("[OpenCodeSidecar] 已停止托管的 sidecar")
        except Exception:
            try:
                _proc.kill()
            except Exception:
                pass
    _proc = None
    _adopted = False


def ensure_sidecar() -> Dict[str, Any]:
    """看门狗：若开启自启但当前不健康，则（重）启动。"""
    global _proc, _adopted, _consecutive_failures, _last_failure_ts
    if not _autostart_enabled():
        return {"ok": False, "skipped": "autostart_disabled"}

    # P0 修复：熔断检查 — 连续失败超阈值且未过冷却期，直接跳过
    if _consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
        elapsed_since_failure = time.time() - _last_failure_ts
        if elapsed_since_failure < _CIRCUIT_BREAK_SECONDS:
            return {
                "ok": False,
                "skipped": "circuit_breaker",
                "consecutive_failures": _consecutive_failures,
                "cooldown_remaining": int(_CIRCUIT_BREAK_SECONDS - elapsed_since_failure),
            }
        # 冷却期结束，重置计数器，允许再试一次
        logger.info(
            "[OpenCodeSidecar] 熔断冷却结束（%s 次失败），重新尝试",
            _consecutive_failures,
        )
        _consecutive_failures = 0

    # 托管进程已死 → 清理引用，触发重启
    if _proc is not None and _proc.poll() is not None:
        logger.warning("[OpenCodeSidecar] 托管 sidecar 已退出（code=%s），将重启", _proc.returncode)
        _proc = None
    if not _is_healthy():
        # 外部收养的实例已挂，取消收养并重新 spawn
        if _adopted:
            logger.warning("[OpenCodeSidecar] 收养的 sidecar 已离线，将重新启动")
            _adopted = False
    else:
        # 健康检查通过：重置失败计数器
        if _consecutive_failures > 0:
            logger.info(
                "[OpenCodeSidecar] 健康恢复，重置失败计数器（原 %s）",
                _consecutive_failures,
            )
        _consecutive_failures = 0
        return {"ok": True, "healthy": True}

    result = start_sidecar()
    # P0 修复：根据启动结果更新失败计数器
    if result.get("ok") or result.get("healthy"):
        _consecutive_failures = 0
    else:
        _consecutive_failures += 1
        _last_failure_ts = time.time()
        if _consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
            logger.error(
                "[OpenCodeSidecar] 连续失败 %s 次，进入熔断（%ss 内不再重试）: %s",
                _consecutive_failures,
                _CIRCUIT_BREAK_SECONDS,
                result.get("error") or "unknown",
            )
        elif _consecutive_failures >= 2:
            # 从第 2 次失败开始打 warning（第 1 次可能是正常冷启动）
            logger.warning(
                "[OpenCodeSidecar] 启动失败（连续第 %s 次）: %s",
                _consecutive_failures,
                result.get("error") or "unknown",
            )
    return result


def boot_sidecar_with_retries(*, max_attempts: int = 3, pause_sec: float = 5.0) -> Dict[str, Any]:
    """后端启动时调用：带重试拉起 sidecar（不阻塞主线程）。"""
    if not _autostart_enabled():
        logger.info("[OpenCodeSidecar] 自启已关闭（OPENCODE_ENABLED / OPENCODE_SIDECAR_AUTOSTART）")
        return {"ok": False, "skipped": "autostart_disabled"}

    last: Dict[str, Any] = {"ok": False, "error": "not_started"}
    for attempt in range(1, max_attempts + 1):
        last = ensure_sidecar()
        if last.get("ok") or last.get("healthy"):
            logger.info("[OpenCodeSidecar] 启动成功 attempt=%s via=%s", attempt, last)
            return last
        logger.warning(
            "[OpenCodeSidecar] 启动未就绪 attempt=%s/%s: %s",
            attempt, max_attempts, last.get("error") or last,
        )
        if attempt < max_attempts:
            time.sleep(pause_sec)
    return last


def sidecar_status() -> Dict[str, Any]:
    host, port = _host_port()
    managed = _proc is not None and _proc.poll() is None
    healthy = _is_healthy()
    adopted = _adopted and not managed
    if healthy:
        mode = "managed" if managed else ("adopted" if adopted else "external")
    else:
        mode = "none"
    return {
        "autostart": _autostart_enabled(),
        "managed": managed,
        "adopted": adopted,
        "running": healthy,
        "healthy": healthy,
        "healthy_via": mode,
        "pid": _proc.pid if managed and _proc else None,
        "exe_found": _resolve_opencode_exe() is not None,
        "host": host,
        "port": port,
    }


atexit.register(stop_sidecar)
