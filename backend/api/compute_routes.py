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

from fastapi import APIRouter, Body, HTTPException, Query

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
    from backend.services.compute.compute_config import get_group, get_value
    from backend.services.evolution import evo_runtime

    last_at = None
    try:
        rows = _analytics_query("SELECT max(created_at) FROM factor_evolution_log")
        if rows and rows[0][0] is not None:
            last_at = rows[0][0].isoformat() if hasattr(rows[0][0], "isoformat") else str(rows[0][0])
    except Exception:  # noqa: BLE001
        pass

    rt = evo_runtime.snapshot()
    auto = bool(get_value("FACTOR_MINING_BOOST_AUTO"))
    return {
        "running": bool(rt.get("running") or _evo_running),
        "runtime": rt,
        "mining_boost_auto": auto,
        "last_error": rt.get("last_error") or _evo_last_error,
        "last_activity_at": last_at,
        "recent_activity": _evolution_activity(10),
        "active_factors": _active_factor_stats(),
        "config": get_group("gp") + get_group("mcts") + get_group("evo"),
        "schedule": {
            "daily_4h": "每日 03:00（factor_evolution_daily）",
            "daily_5m": "每日 04:00（factor_evolution_scalp_5m_daily）",
            "hourly_weights": "每小时（factor_online_weight_hourly）",
            "note": "开启「挖矿加强自动」后，定时/手动进化前会自动套用加强档",
        },
    }


@router.post("/evolution/mining-boost-auto")
async def evolution_mining_boost_auto(body: Dict[str, Any] = Body(default_factory=dict)):
    """开启/关闭挖矿加强自动；开启时立即套用 mining_boost 预设。"""
    from backend.services.compute.compute_config import apply_preset, update

    enabled = bool((body or {}).get("enabled"))
    upd = update({"FACTOR_MINING_BOOST_AUTO": enabled})
    if not upd.get("ok"):
        raise HTTPException(status_code=400, detail=upd.get("errors"))
    preset = None
    if enabled:
        preset = apply_preset("mining_boost")
        if not preset.get("ok"):
            raise HTTPException(status_code=400, detail=preset.get("errors"))
    return {
        "ok": True,
        "mining_boost_auto": enabled,
        "preset": preset,
        "message": "已开启：以后定时/手动挖矿都会用加强档" if enabled else "已关闭自动加强（当前参数保留，不自动回退）",
    }


@router.post("/evolution/abort")
async def evolution_abort(body: Dict[str, Any] = Body(default_factory=dict)):
    """强制结束卡住的进化运行态（释放单飞锁标记）。"""
    global _evo_running, _evo_last_error
    from backend.services.evolution import evo_runtime

    reason = str((body or {}).get("reason") or "manual")
    result = evo_runtime.force_abort(reason=reason)
    _evo_running = False
    # 不把 abort 当作持续故障挂在 last_error；面板只显示当前运行态错误
    _evo_last_error = None
    try:
        if _evo_lock.locked():
            _evo_lock.release()
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, **result, "message": "已强制结束运行态；若 loky 仍占 CPU 需重启后端"}


@router.post("/evolution/repromote-quarantine")
async def evolution_repromote_quarantine(body: Dict[str, Any] = Body(default_factory=dict)):
    """隔离因子复评：达标者回 PAPER。"""
    from backend.services.evolution.repromote_quarantine import repromote_quarantine_factors

    body = body or {}
    period = str(body.get("period") or "4h").strip().lower()
    limit = int(body.get("limit") or 40)
    return repromote_quarantine_factors(period=period, limit=limit)


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
async def evolution_trigger(body: Dict[str, Any] = Body(default_factory=dict)):
    """手动触发因子进化闭环（后台线程，单飞：已在跑则拒绝）。

    body 可选：
      period: "5m" | "4h"（默认 4h）
      preset: "mining_boost" | null
      apply_boost: true 时强制套用加强档（与自动开关无关）
    """
    global _evo_running, _evo_last_error
    from backend.services.evolution import evo_runtime

    body = body or {}
    period = str(body.get("period") or "4h").strip().lower()
    if period not in ("5m", "4h", "1h", "15m", "1d"):
        period = "4h"
    preset = body.get("preset")
    apply_boost = bool(body.get("apply_boost"))
    preset_applied = None
    if preset or apply_boost:
        from backend.services.compute.compute_config import apply_preset
        preset_applied = apply_preset(str(preset or "mining_boost"))
        if not preset_applied.get("ok"):
            return {
                "success": False,
                "message": f"预设失败: {preset_applied.get('errors')}",
                "preset": preset_applied,
            }

    if evo_runtime.is_running() or not _evo_lock.acquire(blocking=False):
        return {
            "success": False,
            "message": "因子进化已在运行中（每日定时或手动触发）",
            "runtime": evo_runtime.snapshot(),
        }

    def _run() -> None:
        global _evo_running, _evo_last_error
        _evo_running = True
        _evo_last_error = None
        t0 = time.time()
        try:
            from backend.services.evolution.factor_evolution_loop import (
                run_factor_evolution_loop,
                run_scalp_factor_evolution_loop,
            )
            # 覆盖 source 标记为 manual：先让 loop mark_start，再改 snapshot 不便；
            # loop 默认 cron，此处用环境提示不必要——改 loop 支持 source 参数更好。
            if period == "5m":
                report = run_scalp_factor_evolution_loop(source="manual")
            else:
                report = run_factor_evolution_loop(period=period, source="manual")
            elapsed = round(time.time() - t0, 1)
            logger.info("[Compute] 手动因子进化完成 period=%s %.1fs: %s", period, elapsed, report)
            if isinstance(report, dict) and report.get("error"):
                _evo_last_error = str(report.get("message") or report.get("error"))[:300]
            try:
                from backend.services.compute.compute_metrics import record_task_event
                record_task_event("task", "factor_evolution_elapsed", elapsed,
                                  {"status": "error" if isinstance(report, dict) and report.get("error") else "done",
                                   "period": period})
            except Exception:  # noqa: BLE001
                pass
        except Exception as e:  # noqa: BLE001
            _evo_last_error = str(e)[:300]
            logger.error("[Compute] 手动因子进化异常: %s", e)
        finally:
            _evo_running = False
            _evo_lock.release()

    threading.Thread(target=_run, daemon=True, name="compute-evolution-manual").start()
    return {
        "success": True,
        "message": f"因子进化已触发 period={period}",
        "running": True,
        "period": period,
        "preset": preset_applied,
        "mining_boost_auto": evo_runtime.mining_boost_auto_enabled(),
    }


@router.get("/evolution/preflight")
async def evolution_preflight(period: str = Query("4h")):
    """挖矿前深度预检：用币 + 是否够 bars。"""
    try:
        from backend.services.evolution.factor_evolution_loop import (
            _lookback_for_period,
            _load_data,
            resolve_evolution_symbols,
        )
        symbols = resolve_evolution_symbols()
        need = _lookback_for_period(period)
        dfs = _load_data(symbols, period=period, lookback=need)
        ok = sorted(dfs.keys()) if dfs else []
        short = [s for s in symbols if s not in ok]
        return {
            "period": period,
            "symbols": symbols,
            "need_bars": need,
            "ok_symbols": ok,
            "short_symbols": short,
            "ready": len(short) == 0 and len(ok) > 0,
        }
    except Exception as e:  # noqa: BLE001
        return {"period": period, "ready": False, "error": str(e)[:300]}


def _bucket_reject_reason(action: str | None, reason: str | None) -> str:
    """把进化拒因归到计划验收桶（验证 L1 fail-closed 是否生效）。"""
    a = (action or "").lower()
    r = (reason or "").lower()
    if "dsr" in a or "pbo" in a or "dsr" in r or "pbo" in r:
        return "purge_dsr_pbo_reject"
    if a.startswith("wfo") or "wfo" in a or "wfo" in r:
        if "error" in a or "error" in r:
            return "wfo_error"
        if "ic" in a:
            return "wfo_ic_reject"
        return "wfo_reject"
    if "capacity_missing" in a or "capacity_missing" in r:
        return "capacity_missing"
    if "capacity" in a or "capacity" in r:
        return "capacity_reject"
    if "test_set" in a or "test_set" in r or "test_ic" in a or "test_ic" in r:
        return "test_fail_closed"
    if "fail_closed" in a or "fail_closed" in r:
        return "gate_fail_closed"
    if a in ("promote_reject", "reject", "quarantine", "deactivate"):
        return a
    return a or "other"


@router.get("/evolution/mining-diagnostics")
async def evolution_mining_diagnostics(days: int = Query(7, ge=1, le=90)):
    """挖矿诊断：拒因分桶 + Top 原因（Ops 加强档面板用）。"""
    buckets: Dict[str, int] = {}
    top_reasons: list = []
    try:
        rows = _analytics_query(
            "SELECT action, reason, count(*) AS n FROM factor_evolution_log "
            "WHERE created_at >= now() - (:d || ' days')::interval "
            "AND ("
            "  action ILIKE '%reject%' OR action ILIKE '%wfo%' "
            "  OR action ILIKE '%fail_closed%' OR action ILIKE '%quarantine%' "
            "  OR action ILIKE '%deactivate%' OR reason ILIKE '%capacity%' "
            "  OR reason ILIKE '%DSR%' OR reason ILIKE '%PBO%' OR reason ILIKE '%fail_closed%'"
            ") "
            "GROUP BY action, reason ORDER BY n DESC LIMIT 80",
            {"d": str(int(days))},
        )
        for action, reason, n in rows:
            b = _bucket_reject_reason(action, reason)
            buckets[b] = buckets.get(b, 0) + int(n)
            if len(top_reasons) < 15:
                top_reasons.append({
                    "bucket": b,
                    "action": action,
                    "reason": (reason or "")[:160],
                    "n": int(n),
                })
        return {
            "days": days,
            "buckets": dict(sorted(buckets.items(), key=lambda kv: -kv[1])),
            "top_reasons": top_reasons,
            "l1_signals": {
                "purge_dsr_pbo_reject": buckets.get("purge_dsr_pbo_reject", 0),
                "wfo_reject": buckets.get("wfo_reject", 0) + buckets.get("wfo_ic_reject", 0),
                "wfo_error": buckets.get("wfo_error", 0),
                "capacity_missing": buckets.get("capacity_missing", 0),
                "test_fail_closed": buckets.get("test_fail_closed", 0),
            },
        }
    except Exception as e:  # noqa: BLE001
        logger.error("[Compute] mining-diagnostics: %s", e)
        return {"days": days, "buckets": {}, "top_reasons": [], "error": str(e)[:300]}


@router.get("/config/presets")
async def config_presets():
    from backend.services.compute.compute_config import list_presets
    return {"presets": list_presets()}


@router.post("/config/preset")
async def config_apply_preset(body: Dict[str, Any]):
    from backend.services.compute.compute_config import apply_preset
    name = str((body or {}).get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    result = apply_preset(name)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("errors"))
    return result


@router.get("/factors/active")
async def factors_active(top: int = Query(10, ge=1, le=50)):
    """factor_active_set 统计：state 分布 + Top 因子（按 |icir| 排序）。"""
    try:
        rows = _analytics_query(
            "SELECT factor_id, source, state, icir, last_net_ic, current_weight, activated_at "
            "FROM factor_active_set WHERE state IN ('ACTIVE','PAPER','SMALL_LIVE') "
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
