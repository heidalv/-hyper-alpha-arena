# -*- coding: utf-8 -*-
"""币安用户数据流 worker 嵌入保活（实盘权益快照的关键供给方）。

背景（2026-08-28 实盘零成交排查）：
独立运行的 backend/workers/binance_user_stream.py 一旦退出（实测无任何日志、
无报错地静默死亡），data/live_user_stream_snapshot.json 不再刷新 →
get_live_equity 快照路径失效；REST 兜底偶发失败时实盘权益=0 →
scalp/midlong 实盘会话整轮跳过开仓（实盘零成交根因之一）。

本模块把 worker 的生命周期交给后端托管：后端由 backend-watchdog 保活，
worker 由本模块守护线程保活，链条闭合。

规则：
  - USER_STREAM_EMBEDDED=false 时整体关闭（回到独立 schtasks worker 模式）。
  - 启动时若快照 <30s 新鲜 → 判定外部 worker 存活，不嵌入（避免 listenKey 双开）。
  - 嵌入进程退出 → 8s 后自动重启；重启前再次检查快照（外部 worker 接管时退出守护）。
  - 后端退出（atexit）时终止嵌入进程。
"""
from __future__ import annotations

import atexit
import logging
import os
import subprocess
import sys
import threading
import time

logger = logging.getLogger(__name__)

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_WORKER = os.path.join(_REPO, "backend", "workers", "binance_user_stream.py")
_SNAPSHOT = os.path.join(_REPO, "data", "live_user_stream_snapshot.json")

_state = {
    "lock": threading.Lock(),
    "proc": None,
    "stop": threading.Event(),
    "thread": None,
    "started": False,
}


def _snapshot_fresh(within_sec: float = 30.0) -> bool:
    try:
        if os.path.exists(_SNAPSHOT):
            return (time.time() - os.path.getmtime(_SNAPSHOT)) <= within_sec
    except Exception:
        pass
    return False


def _kill_existing_workers() -> int:
    """[2026-09-01 僵尸风暴根治] 拉起新 worker 前清掉所有残留 worker 进程。

    根因：后端被硬杀（stop-dev KILL）时 atexit 不执行 → 嵌入 worker 变孤儿；
    每次重启再拉一个 → 累积 57 个僵尸 worker 每 12s 各打一轮 REST → 币安对
    白名单 IP 触发 -1003 限流且封禁时间被不断后推（实盘账户数据读不到）。
    用 PowerShell CIM 按命令行过滤后逐个强杀（wmic 已从新版 Windows 移除）。
    """
    killed = 0
    try:
        import subprocess as _sp
        _ps = (
            "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
            "Where-Object { $_.CommandLine -match 'binance_user_stream' } | "
            "ForEach-Object { Write-Output $_.ProcessId }"
        )
        _out = _sp.run(
            ["powershell", "-NoProfile", "-Command", _ps],
            capture_output=True, text=True, timeout=30,
        ).stdout or ""
        for _tok in _out.split():
            _tok = _tok.strip()
            if not _tok.isdigit():
                continue
            _pid = int(_tok)
            if _pid == os.getpid():
                continue
            try:
                _sp.run(["taskkill", "/F", "/PID", str(_pid)],
                        capture_output=True, timeout=15)
                killed += 1
            except Exception:
                pass
    except Exception as _e:
        logger.warning("[UserStreamGuard] 残留 worker 清理失败(忽略): %s", _e)
    if killed:
        logger.warning("[UserStreamGuard] 清理残留 user-stream worker %d 个", killed)
    return killed


def _spawn():
    _kill_existing_workers()
    _log_out = os.path.join(_REPO, "logs", "user_stream_out.log")
    _log_err = os.path.join(_REPO, "logs", "user_stream_err.log")
    try:
        _out = open(_log_out, "ab", buffering=0)
    except Exception:
        _out = subprocess.DEVNULL
    try:
        _err = open(_log_err, "ab", buffering=0)
    except Exception:
        _err = subprocess.DEVNULL
    proc = subprocess.Popen(
        [sys.executable, _WORKER],
        cwd=_REPO,
        stdout=_out,
        stderr=_err,
        stdin=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    logger.info("[UserStreamGuard] 已拉起用户流 worker pid=%s", proc.pid)
    return proc


def _monitor() -> None:
    while not _state["stop"].is_set():
        try:
            proc = _state["proc"]
            if proc is None:
                if not _snapshot_fresh(30.0):
                    _state["proc"] = _spawn()
            elif proc.poll() is not None:
                _rc = proc.returncode
                logger.warning(
                    "[UserStreamGuard] 用户流 worker 退出(rc=%s)，8s 后重启", _rc,
                )
                _state["proc"] = None
                time.sleep(8)
                if not _state["stop"].is_set() and not _snapshot_fresh(30.0):
                    _state["proc"] = _spawn()
        except Exception as _e:
            logger.warning("[UserStreamGuard] 守护循环异常: %s", _e)
        _state["stop"].wait(15.0)


def ensure_user_stream_worker() -> None:
    """幂等入口：主进程启动时调用一次。"""
    with _state["lock"]:
        if _state["started"]:
            return
        if _snapshot_fresh(30.0):
            logger.info("[UserStreamGuard] 外部用户流 worker 存活（快照新鲜），不嵌入")
            return
        _state["started"] = True
        _state["proc"] = _spawn()
        _state["thread"] = threading.Thread(
            target=_monitor, name="user-stream-guard", daemon=True,
        )
        _state["thread"].start()
        atexit.register(_shutdown)


def _shutdown() -> None:
    _state["stop"].set()
    proc = _state.get("proc")
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
        except Exception:
            pass
