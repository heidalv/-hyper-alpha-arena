"""
算力中心 REST API（v6 第十章 前端仪表台配套）。

prefix: /api/compute

端点：
  GET  /api/compute/hardware             — GPU/CPU/内存/磁盘实时（3s TTL 缓存）
  GET  /api/compute/gpu-env              — torch/cu124 环境探活
  GET  /api/compute/evolution/status     — 因子进化运行状态 + 最近活动 + 配置生效值
  GET  /api/compute/evolution/history    — factor_evolution_log 分页
  POST /api/compute/evolution/trigger    — 手动触发因子进化（后台线程，单飞锁）
  GET  /api/compute/factors/active       — factor_active_set 统计
  GET  /api/compute/tasks                — 后台任务队列聚合
  GET  /api/compute/config               — 全量配置项当前值
  PUT  /api/compute/config               — 校验并下发配置（写覆盖文件 + 注入 env）
  GET  /api/compute/llm/status           — 本地 LLM 双机配置状态 + 最近探测结果
  POST /api/compute/llm/check            — 后台触发 4 项连通性检查
  GET  /api/compute/metrics?window=      — 历史指标（资源/任务耗时/成功率）
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/compute", tags=["Compute"])

# ── 进程内状态 ──
_evo_lock = threading.Lock()            # 因子进化单飞锁
_evo_running = False                    # 与锁联动，供 status 返回
_evo_last_error: Optional[str] = None
_llm_check_lock = threading.Lock()
_llm_checking = False
_llm_check_cache: Optional[Dict[str, Any]] = None


# ══════════════════════════════════════════════════════════
# 硬件
# ══════════════════════════════════════════════════════════
@router.get("/hardware")
async def get_hardware():
    """GPU/CPU/内存/磁盘实时快照（gpu_guard + psutil，3s TTL）。"""
    try:
        from backend.services.compute.hardware_monitor import snapshot as hw_snapshot
        return hw_snapshot()
    except Exception as e:  # noqa: BLE001
        logger.error("[Compute] hardware error: %s", e)
        return {"ts": int(time.time() * 1000), "error": str(e)}


@router.get("/gpu-env")
async def get_gpu_env():
    """torch / CUDA 环境探活（损坏时如实返回 degraded，供前端黄徽章）。"""
    probe: Dict[str, Any] = {"checked_at": int(time.time() * 1000)}
    try:
        import torch  # noqa: WPS433

        version = getattr(torch, "__version__", None)
        file_ = getattr(torch, "__file__", None)
        if version is None or file_ is None:
            probe.update({
                "available": False,
                "broken": True,
                "error": "torch 包损坏（空 namespace package：__version__/__file__ 缺失），"
                         "需重装 cu124 版，如 pip install torch --index-url https://download.pytorch.org/whl/cu124",
                "install_hint": "pip install torch --index-url https://download.pytorch.org/whl/cu124",
            })
            return probe
        cuda_ok = False
        device_name = None
        try:
            cuda_ok = bool(torch.cuda.is_available())
            if cuda_ok:
                device_name = torch.cuda.get_device_name(0)
        except Exception as e:  # noqa: BLE001
            logger.debug("[Compute] cuda probe: %s", e)
        probe.update({
            "available": True,
            "broken": False,
            "version": str(version),
            "cuda_available": cuda_ok,
            "cuda_version": torch.version.cuda if getattr(torch, "version", None) else None,
            "device_name": device_name,
            "note": "Pascal sm_61：无 Tensor Core，训练一律 FP32；"
                    "WDDM 桌面模式实际可用显存预算约 6.4GB（v6 10.1 口径）",
        })
    except ImportError:
        probe.update({
            "available": False,
            "broken": False,
            "error": "torch 未安装（requirements-optional.txt 声明 torch==2.11.0 但未生效）",
        })
    except Exception as e:  # noqa: BLE001
        probe.update({"available": False, "broken": True, "error": str(e)[:200]})
    return probe


# ══════════════════════════════════════════════════════════
# 因子进化
# ══════════════════════════════════════════════════════════
def _analytics_query(sql: str, params: Optional[Dict[str, Any]] = None) -> List[tuple]:
    from sqlalchemy import text as _sa_text
    from backend.database.connection import analytics_engine
    with analytics_engine.connect() as conn:
        return list(conn.execute(_sa_text(sql), params or {}))


def _evolution_activity(limit: int = 10) -> List[Dict[str, Any]]:
    try:
        rows = _analytics_query(
            "SELECT phase, action, factor_id, source, reason, created_at "
            "FROM factor_evolution_log ORDER BY created_at DESC LIMIT :lim",
            {"lim": limit},
        )
        return [{
            "phase": r[0], "action": r[1], "factor_id": r[2],
            "source": r[3], "reason": (r[4] or "")[:120],
            "created_at": r[5].isoformat() if hasattr(r[5], "isoformat") else str(r[5]),
        } for r in rows]
    except Exception as e:  # noqa: BLE001
        logger.warning("[Compute] evolution activity: %s", e)
        return []


def _active_factor_stats() -> Dict[str, Any]:
    try:
        rows = _analytics_query(
            "SELECT state, count(*) FROM factor_active_set GROUP BY state"
        )
        dist = {r[0]: int(r[1]) for r in rows}
        return {"state_dist": dist, "total": sum(dist.values())}
    except Exception as e:  # noqa: BLE001
        logger.warning("[Compute] active factors: %s", e)
        return {"state_dist": {}, "total": 0, "error": str(e)}


@router.get("/evolution/status")
async def evolution_status():
    """因子进化运行状态 + 最近活动 + GP/MCTS/进化配置生效值。"""
    from backend.services.compute.compute_config import get_group
    last_at = None
    try:
        rows = _analytics_query("SELECT max(created_at) FROM factor_evolution_log")
        if rows and rows[0][0] is not None:
            last_at = rows[0][0].isoformat() if hasattr(rows[0][0], "isoformat") else str(rows[0][0])
    except Exception:  # noqa: BLE001
        pass
    return {
        "running": _evo_running,
        "last_error": _evo_last_error,
        "last_activity_at": last_at,
        "recent_activity": _evolution_activity(8),
        "active_factors": _active_factor_stats(),
        "config": get_group("gp") + get_group("mcts") + get_group("evo"),
        "schedule": {
            "daily_cron": "每日 03:00（main.py factor_evolution_daily）",
            "hourly_weights": "每小时（factor_online_weight_hourly）",
        },
    }


@router.get("/evolution/history")
async def evolution_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    action: Optional[str] = None,
):
    """factor_evolution_log 分页（PG analytics）。"""
    try:
        where = ""
        params: Dict[str, Any] = {"lim": page_size, "off": (page - 1) * page_size}
        if action:
            where = "WHERE action = :action "
            params["action"] = action
        total_rows = _analytics_query(
            f"SELECT count(*) FROM factor_evolution_log {where}", params if action else {}
        )
        total = int(total_rows[0][0]) if total_rows else 0
        rows = _analytics_query(
            f"SELECT phase, action, factor_id, source, reason, metrics, created_at "
            f"FROM factor_evolution_log {where} ORDER BY created_at DESC LIMIT :lim OFFSET :off",
            params,
        )
        records = []
        for r in rows:
            m = r[5]
            if isinstance(m, str):
                try:
                    m = json.loads(m)
                except Exception:  # noqa: BLE001
                    m = None
            records.append({
                "phase": r[0], "action": r[1], "factor_id": r[2],
                "source": r[3], "reason": (r[4] or "")[:200], "metrics": m,
                "created_at": r[6].isoformat() if hasattr(r[6], "isoformat") else str(r[6]),
            })
        return {"total": total, "page": page, "page_size": page_size,
                "records": records}
    except Exception as e:  # noqa: BLE001
        logger.error("[Compute] evolution history: %s", e)
        return {"total": 0, "page": page, "page_size": page_size, "records": [],
                "error": str(e)}


@router.post("/evolution/trigger")
async def evolution_trigger():
    """手动触发因子进化闭环（后台线程，单飞：已在跑则拒绝）。"""
    global _evo_running, _evo_last_error
    if not _evo_lock.acquire(blocking=False):
        return {"success": False, "message": "因子进化已在运行中（每日 3 点调度或手动触发）"}

    def _run() -> None:
        global _evo_running, _evo_last_error
        _evo_running = True
        _evo_last_error = None
        t0 = time.time()
        try:
            from backend.services.evolution.factor_evolution_loop import run_factor_evolution_loop
            report = run_factor_evolution_loop()
            elapsed = round(time.time() - t0, 1)
            logger.info("[Compute] 手动因子进化完成 %.1fs: %s", elapsed, report)
            try:
                from backend.services.compute.compute_metrics import record_task_event
                record_task_event("task", "factor_evolution_elapsed", elapsed,
                                  {"status": "error" if "error" in report else "done"})
            except Exception:  # noqa: BLE001
                pass
        except Exception as e:  # noqa: BLE001
            _evo_last_error = str(e)[:300]
            logger.error("[Compute] 手动因子进化异常: %s", e)
        finally:
            _evo_running = False
            _evo_lock.release()

    threading.Thread(target=_run, daemon=True, name="compute-evolution-manual").start()
    return {"success": True, "message": "因子进化已触发（后台运行）", "running": True}


@router.get("/factors/active")
async def factors_active(top: int = Query(10, ge=1, le=50)):
    """factor_active_set 统计：state 分布 + Top 因子（按 |icir| 排序）。"""
    try:
        rows = _analytics_query(
            "SELECT factor_id, source, state, icir, last_net_ic, current_weight, activated_at "
            "FROM factor_active_set WHERE state IN ('ACTIVE','ORTHO','PAPER') "
            "ORDER BY abs(coalesce(icir,0)) DESC LIMIT :lim",
            {"lim": top},
        )
        top_factors = [{
            "factor_id": r[0], "source": r[1], "state": r[2],
            "icir": float(r[3]) if r[3] is not None else None,
            "last_net_ic": float(r[4]) if r[4] is not None else None,
            "current_weight": r[5],
            "activated_at": r[6].isoformat() if hasattr(r[6], "isoformat") else str(r[6]),
        } for r in rows]
        return {"stats": _active_factor_stats(), "top_factors": top_factors}
    except Exception as e:  # noqa: BLE001
        logger.error("[Compute] factors active: %s", e)
        return {"stats": {}, "top_factors": [], "error": str(e)}


# ══════════════════════════════════════════════════════════
# 任务队列
# ══════════════════════════════════════════════════════════
@router.get("/tasks")
async def tasks(limit: int = Query(20, ge=1, le=100)):
    """后台任务队列聚合：factor_job_manager + 进化运行标志。"""
    try:
        from backend.services.factor_engine.factor_jobs import factor_job_manager
        jobs = factor_job_manager.list(limit=limit)
    except Exception as e:  # noqa: BLE001
        jobs = []
        logger.debug("[Compute] jobs list: %s", e)
    active = [j for j in jobs if j.get("status") in ("pending", "running")]
    return {
        "total": len(jobs),
        "active": len(active),
        "evolution_running": _evo_running,
        "jobs": jobs,
    }


# ══════════════════════════════════════════════════════════
# 配置
# ══════════════════════════════════════════════════════════
@router.get("/config")
async def get_config():
    """全量配置项当前值（env → 覆盖文件 → 默认值），含分组/说明/来源。"""
    from backend.services.compute.compute_config import get_all
    return {"configs": get_all()}


@router.put("/config")
async def put_config(payload: Dict[str, Any]):
    """校验并下发配置：写覆盖文件 + 注入 os.environ 即时生效。"""
    from backend.services.compute.compute_config import update as cfg_update
    result = cfg_update(payload)
    if not result.get("ok"):
        raise HTTPException(status_code=422, detail=result)
    return result


# ══════════════════════════════════════════════════════════
# 本地 LLM 双机
# ══════════════════════════════════════════════════════════
def _llm_config_status() -> Dict[str, Any]:
    from backend.services.compute.compute_config import get_value
    cfg_id = int(get_value("LOCAL_LLM_CONFIG_ID") or 0)
    out: Dict[str, Any] = {
        "config_id": cfg_id,
        "enabled": cfg_id > 0,
        "note": "LOCAL_LLM_CONFIG_ID>0 时启用本地 LLM 门控优化（Governor source=local_llm_optimizer 优先级 55）",
    }
    if cfg_id > 0:
        try:
            from backend.services.llm_config_service import get_llm_config
            cfg = get_llm_config(cfg_id)
            if cfg:
                from urllib.parse import urlparse
                host = urlparse(cfg.base_url).hostname or cfg.base_url
                out["base_url"] = cfg.base_url
                out["host"] = host
                out["model"] = cfg.model_deep or getattr(cfg, "model", None)
                out["config_found"] = True
            else:
                out["config_found"] = False
                out["error"] = f"LLMConfiguration id={cfg_id} 不存在"
        except Exception as e:  # noqa: BLE001
            out["config_found"] = False
            out["error"] = str(e)[:150]
    return out


@router.get("/llm/status")
async def llm_status():
    """本地 LLM 双机配置状态 + 最近探测结果（探测由 POST /llm/check 触发）。"""
    status = _llm_config_status()
    with _llm_check_lock:
        status["checking"] = _llm_checking
        status["last_check"] = _llm_check_cache
    return status


@router.post("/llm/check")
async def llm_check():
    """后台触发 4 项连通性检查（网络/模型列表/真实推理/JSON 格式），结果缓存供 GET 轮询。"""
    global _llm_checking
    with _llm_check_lock:
        if _llm_checking:
            return {"success": False, "message": "连通性检查已在进行中"}
        _llm_checking = True

    def _run() -> None:
        global _llm_checking, _llm_check_cache
        t0 = time.time()
        result: Dict[str, Any] = {"checked_at": int(time.time() * 1000)}
        try:
            from backend.services.compute.compute_config import get_value
            from backend.services.local_llm.connectivity_check import (
                check_inference,
                check_json_params,
                check_models,
                check_reachable,
            )
            from backend.services.llm_config_service import get_llm_config
            cfg_id = int(get_value("LOCAL_LLM_CONFIG_ID") or 0)
            cfg = get_llm_config(cfg_id) if cfg_id > 0 else None
            if not cfg:
                result.update({"skipped": True, "message": "LOCAL_LLM_CONFIG_ID 未配置，跳过检查"})
                return
            base_url = cfg.base_url.rstrip("/")
            api_key = ""
            try:
                api_key = cfg.api_key or ""
            except Exception:  # noqa: BLE001
                pass
            steps = []
            steps.append({"name": "网络可达", "ok": check_reachable(base_url, timeout=10.0)})
            model = None
            if steps[-1]["ok"]:
                model = check_models(base_url, api_key, timeout=15.0)
                steps.append({"name": "模型列表", "ok": model is not None, "model": model})
            if model:
                inf = check_inference(base_url, api_key, model, timeout=100.0)
                steps.append({"name": "真实推理", "ok": inf is not None,
                              "elapsed": round(inf["elapsed"], 1) if inf else None})
                steps.append({"name": "JSON 参数", "ok": check_json_params(inf)})
            result.update({
                "base_url": base_url,
                "model": model,
                "steps": steps,
                "passed": sum(1 for s in steps if s["ok"]),
                "total": len(steps),
                "elapsed_sec": round(time.time() - t0, 1),
            })
        except Exception as e:  # noqa: BLE001
            result.update({"error": str(e)[:300]})
        finally:
            with _llm_check_lock:
                _llm_check_cache = result
                _llm_checking = False

    threading.Thread(target=_run, daemon=True, name="compute-llm-check").start()
    return {"success": True, "checking": True, "message": "连通性检查已开始（后台）"}


# ══════════════════════════════════════════════════════════
# 历史指标
# ══════════════════════════════════════════════════════════
@router.get("/metrics")
async def metrics(window: str = Query("24h", pattern="^(1h|24h|7d|30d)$")):
    """历史指标：资源趋势 / 任务耗时 / 成功率（compute_metrics 表）。"""
    from backend.services.compute.compute_metrics import query as m_query
    data = m_query(window=window, limit=2000)
    # 任务成功率：task kind 下按 key 前缀统计
    series = data.get("series", {})
    task_keys = [k for k in series if k.startswith("factor_") or "elapsed" in k]
    return {
        "window": window,
        "resource": {k: v for k, v in series.items() if k.startswith(("cpu_", "mem_", "gpu_"))},
        "tasks": {k: v for k, v in series.items() if k in task_keys},
        "error": data.get("error"),
    }
