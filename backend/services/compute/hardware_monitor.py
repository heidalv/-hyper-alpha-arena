"""
硬件资源采样服务（v6 第十章 前端仪表台配套）。

- GPU：复用 backend/scripts/gpu_guard.py（nvidia-smi 解析 + 老卡阈值告警：
  温度 83°C / 功耗 90% / 可用显存 512MB）
- CPU / 内存 / 磁盘：psutil 实时采样
- 惰性采样 + 3s TTL 内存缓存（线程锁保护），避免前端轮询打到 nvidia-smi 子进程
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, Optional

import psutil

logger = logging.getLogger(__name__)

_SAMPLE_TTL_SEC = 3.0
_lock = threading.Lock()
_cache: Optional[Dict[str, Any]] = None
_cache_ts: float = 0.0


def _gpu_snapshot() -> Dict[str, Any]:
    """GPU 实时快照：nvidia-smi 一次查询 + 阈值告警。"""
    try:
        from backend.scripts.gpu_guard import check as _guard_check
        from backend.scripts.gpu_guard import query_gpu
    except Exception as e:  # noqa: BLE001
        return {"available": False, "error": f"gpu_guard 导入失败: {e}"}

    g = query_gpu()
    if g is None:
        return {
            "available": False,
            "error": "nvidia-smi 不可用或无 NVIDIA GPU（驱动未装/被占用）",
        }
    alerts = _guard_check(g)
    util = _gpu_utilization()
    return {
        "available": True,
        "name": g["name"],
        "driver": g["driver"],
        "mem_total_mb": g["mem_total_mb"],
        "mem_used_mb": g["mem_used_mb"],
        "mem_free_mb": g["mem_free_mb"],
        "mem_available_budget_mb": max(0.0, g["mem_free_mb"] - 0.0),
        # WDDM 桌面模式：实际可用预算 ≈ 6.4GB（v6 10.1 口径，由 free 显存体现）
        "temp_c": g["temp_c"],
        "power_w": g["power_w"],
        "power_limit_w": g["power_limit_w"],
        "utilization_pct": util,
        "alerts": alerts,
        "health": "alert" if alerts else "ok",
    }


def _gpu_utilization() -> Optional[float]:
    """nvidia-smi 利用率查询（附加查询，失败返回 None）。"""
    import subprocess

    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0 and out.stdout.strip():
            return float(out.stdout.splitlines()[0].strip())
    except Exception:  # noqa: BLE001
        pass
    return None


def _cpu_snapshot() -> Dict[str, Any]:
    """CPU 实时快照（16C/32T Xeon E5-2698B v3 口径）。"""
    try:
        # psutil.cpu_percent 首次调用返回 0.0，这里做一次预热采样
        psutil.cpu_percent(interval=None)
        time.sleep(0.05)
        pct = psutil.cpu_percent(interval=None)
    except Exception:  # noqa: BLE001
        pct = 0.0
    return {
        "logical_cores": psutil.cpu_count(logical=True) or 0,
        "physical_cores": psutil.cpu_count(logical=False) or 0,
        "usage_pct": round(float(pct), 1),
        "load_avg": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
    }


def _mem_snapshot() -> Dict[str, Any]:
    vm = psutil.virtual_memory()
    return {
        "total_gb": round(vm.total / (1024 ** 3), 1),
        "used_gb": round(vm.used / (1024 ** 3), 1),
        "available_gb": round(vm.available / (1024 ** 3), 1),
        "usage_pct": round(float(vm.percent), 1),
    }


def _disk_snapshot() -> Dict[str, Any]:
    """磁盘：C（系统盘，剩余 ~16GB 风险）与 D（模型/缓存所在盘）。"""
    out = []
    for letter in ("C", "D"):
        try:
            du = psutil.disk_usage(f"{letter}:\\")
            out.append({
                "mount": f"{letter}:",
                "total_gb": round(du.total / (1024 ** 3), 1),
                "free_gb": round(du.free / (1024 ** 3), 1),
                "usage_pct": round(float(du.percent), 1),
                "low_space": du.free < 20 * (1024 ** 3),
            })
        except Exception:  # noqa: BLE001
            continue
    return {"disks": out}


def snapshot() -> Dict[str, Any]:
    """聚合硬件快照（3s TTL 缓存）。"""
    global _cache, _cache_ts
    now = time.time()
    with _lock:
        if _cache is not None and (now - _cache_ts) < _SAMPLE_TTL_SEC:
            return _cache
        snap = {
            "ts": int(now * 1000),
            "gpu": _gpu_snapshot(),
            "cpu": _cpu_snapshot(),
            "memory": _mem_snapshot(),
            "disk": _disk_snapshot(),
        }
        _cache = snap
        _cache_ts = now
        return snap
