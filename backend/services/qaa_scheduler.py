"""
QAA 调度统一器 — 阶段2(S2-10c)

学习三通道之三：把散落在各域的 QAA tick 收拢为
「域注册表 + 统一心跳 + 统一调度」三件套：

- **域注册表**：``register_domain`` 声明每个 QAA 域的入口函数、调度间隔、
  启用开关与说明（当前域：rebate_arb / full_auto）。
- **统一心跳**：``get_heartbeats()`` 汇总各域最近运行时间与状态，
  供前端看板（S2-11 三通道看板）与健康检查使用。
- **统一调度**：``run_due_domains()`` 按各域间隔检查心跳，到期才执行，
  由 maintenance_loop 挂载驱动（低频、幂等、异常隔离）。

安全默认：总开关与各域开关默认均为 False —— 只建立统一架构，不改变
现有运行行为；运维按需开启（``QAA_SCHEDULER_ENABLED`` + 域级开关）。
"""

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── 默认配置（settings.py 未注册时兜底）──
QAA_SCHEDULER_ENABLED = False
QAA_REBATE_SCHEDULE_ENABLED = False
QAA_REBATE_INTERVAL_SEC = 900          # 15 分钟
QAA_FULLAUTO_SCHEDULE_ENABLED = False
QAA_FULLAUTO_INTERVAL_SEC = 900        # 15 分钟

# 域注册表：name -> spec
# spec = {
#   "runner": Callable[..., Any],       # 域 tick 入口（关键字参数调用）
#   "interval_sec": float,              # 调度间隔
#   "enabled": bool,                    # 是否参与统一调度
#   "description": str,
#   "last_run_at": float,               # 最近一次运行（unix 秒）
#   "last_status": str,                 # ok / error / skipped
#   "last_error": str,
#   "run_count": int,
# }
_domains: Dict[str, Dict[str, Any]] = {}
_scheduler_lock = threading.Lock()


def _cfg(name: str, default: Any) -> Any:
    """读 settings 配置（缺失不炸）。"""
    try:
        from backend.config import settings as _s
        return getattr(_s, name, default)
    except Exception:
        return default


def register_domain(
    name: str,
    runner: Callable[..., Any],
    *,
    interval_sec: float,
    enabled: bool,
    description: str = "",
) -> None:
    """注册一个 QAA 域。幂等：重复注册只更新配置，保留心跳计数。"""
    with _scheduler_lock:
        spec = _domains.get(name) or {}
        spec.update({
            "runner": runner,
            "interval_sec": float(interval_sec),
            "enabled": bool(enabled),
            "description": description,
            "last_run_at": spec.get("last_run_at", 0.0),
            "last_status": spec.get("last_status", "never"),
            "last_error": spec.get("last_error", ""),
            "run_count": spec.get("run_count", 0),
        })
        _domains[name] = spec
        logger.debug("[QAAScheduler] 域注册: %s (interval=%ss enabled=%s)",
                     name, interval_sec, enabled)


def _heartbeat(name: str, status: str, error: str = "") -> None:
    """记录域心跳（runner 执行后由 run_due_domains 调用）。"""
    with _scheduler_lock:
        spec = _domains.get(name)
        if spec is None:
            return
        spec["last_run_at"] = time.time()
        spec["last_status"] = status
        spec["last_error"] = error
        spec["run_count"] = (spec.get("run_count") or 0) + 1


def get_heartbeats() -> Dict[str, Dict[str, Any]]:
    """统一心跳视图：汇总所有注册域的最新运行状态（前端看板/健康检查用）。

    心跳来源优先级：
    1. 域自带心跳（如 qaa_rebate_tick.get_last_rebate_tick_at()）——
       能反映 orchestrator 直接驱动的 tick（不经 scheduler 的也可见）；
    2. scheduler 自己记录的 last_run_at。
    """
    view: Dict[str, Dict[str, Any]] = {}
    with _scheduler_lock:
        for name, spec in dict(_domains).items():
            last_run = spec.get("last_run_at", 0.0)
            # 域自带心跳兜底（rebate_arb 在 orchestrator 路径也会更新）
            try:
                if name == "rebate_arb":
                    from backend.services.rebate_arb.qaa_rebate_tick import (
                        get_last_rebate_tick_at,
                    )
                    ext_ts = get_last_rebate_tick_at()
                    if ext_ts > last_run:
                        last_run = ext_ts
            except Exception:
                pass
            view[name] = {
                "enabled": spec.get("enabled", False),
                "interval_sec": spec.get("interval_sec", 0),
                "description": spec.get("description", ""),
                "last_run_at": last_run,
                "last_status": spec.get("last_status", "never"),
                "last_error": spec.get("last_error", ""),
                "run_count": spec.get("run_count", 0),
            }
    return view


def run_due_domains(
    svc: Any = None,
    session_id: Optional[str] = None,
) -> List[str]:
    """统一调度：按各域间隔检查心跳，到期才执行；异常隔离。

    maintenance_loop 低频调用。返回实际执行过的域列表。
    """
    if not bool(_cfg("QAA_SCHEDULER_ENABLED", QAA_SCHEDULER_ENABLED)):
        return []

    executed: List[str] = []
    with _scheduler_lock:
        specs = {k: dict(v) for k, v in _domains.items()}
    now = time.time()

    for name, spec in specs.items():
        if not spec.get("enabled", False):
            continue
        interval = float(spec.get("interval_sec") or 0)
        last = float(spec.get("last_run_at") or 0)
        if interval <= 0 or (now - last) < interval:
            continue

        runner = spec.get("runner")
        if runner is None:
            continue
        try:
            kw: Dict[str, Any] = {}
            # full_auto 域需要 svc + session_id（注册表 runner 按签名取用）
            if name == "full_auto":
                if svc is None or not session_id:
                    _heartbeat(name, "skipped", "svc/session_id 缺失")
                    continue
                kw = {"svc": svc, "session_id": session_id}
            runner(**kw)
            _heartbeat(name, "ok")
            executed.append(name)
            logger.info("[QAAScheduler] 域 %s tick 完成", name)
        except Exception as e:
            _heartbeat(name, "error", str(e)[:300])
            logger.warning("[QAAScheduler] 域 %s tick 失败: %s", name, e)
    return executed


# ─────────────────────────────────────────────────────────────────
#  域注册（延迟 import：避免模块加载时拉入重型依赖）
# ─────────────────────────────────────────────────────────────────

def _ensure_domains_registered() -> None:
    """注册内置 QAA 域（幂等，供 maintenance / 看板首次调用时确保就绪）。"""
    if _domains:
        return

    # rebate_arb 域：qaa_rebate_tick（V3 orchestrator 或 ExecutionAuthority 兜底）
    register_domain(
        "rebate_arb",
        _run_rebate_domain_tick,
        interval_sec=float(_cfg("QAA_REBATE_INTERVAL_SEC", QAA_REBATE_INTERVAL_SEC)),
        enabled=bool(_cfg("QAA_REBATE_SCHEDULE_ENABLED", QAA_REBATE_SCHEDULE_ENABLED)),
        description="Rebate 套利域 QAA tick（run_qaa_rebate_tick）",
    )

    # full_auto 域：qaa_legacy_cycle（统一循环 QAA tick）
    register_domain(
        "full_auto",
        _run_fullauto_domain_tick,
        interval_sec=float(_cfg("QAA_FULLAUTO_INTERVAL_SEC", QAA_FULLAUTO_INTERVAL_SEC)),
        enabled=bool(_cfg("QAA_FULLAUTO_SCHEDULE_ENABLED", QAA_FULLAUTO_SCHEDULE_ENABLED)),
        description="全自动 QAA legacy tick（run_qaa_tick）",
    )


def _run_rebate_domain_tick(**kw: Any) -> Any:
    """rebate_arb 域 runner：参数缺省时走内部默认（source=fullauto）。"""
    from backend.services.rebate_arb.qaa_rebate_tick import run_qaa_rebate_tick
    return run_qaa_rebate_tick(source="fullauto")


def _run_fullauto_domain_tick(**kw: Any) -> Any:
    """full_auto 域 runner：需要 svc + session_id。"""
    svc = kw.get("svc")
    session_id = kw.get("session_id")
    if svc is None or not session_id:
        raise ValueError("full_auto 域需要 svc 与 session_id")
    svc._run_qaa_tick(session_id)


def get_scheduler_status() -> Dict[str, Any]:
    """调度器整体状态（总开关 + 心跳视图）。"""
    _ensure_domains_registered()
    return {
        "enabled": bool(_cfg("QAA_SCHEDULER_ENABLED", QAA_SCHEDULER_ENABLED)),
        "domains": get_heartbeats(),
    }


# 模块加载即注册（心跳视图无需维护者先调 run_due_domains）
_ensure_domains_registered()
