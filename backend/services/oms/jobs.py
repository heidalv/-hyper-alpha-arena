# -*- coding: utf-8 -*-
"""OMS 定时任务（v3 方向 2，p2-oms-exec）。

  oms_stuck_scan     每 5 分钟：捞悬挂单（intent/submitted/unknown 超时）告警
  oms_daily_reconcile 每日 BJ 08:40：对每个 live 账户做订单级对账

两个任务都**独立于 EXEC_ALGO_ENABLED**：只要有 live_orders 记录（哪怕只是影子单），
就需要有人看着状态机。对账默认只报告不自动修。
"""
from __future__ import annotations

import logging
import os
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

STUCK_INTERVAL_SEC = 300
RECONCILE_HOUR_LOCAL = 8
RECONCILE_MINUTE_LOCAL = 40


def _env_true(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def oms_jobs_enabled() -> bool:
    return _env_true("OMS_JOBS_ENABLED", True)


def scheduled_stuck_scan() -> Dict[str, Any]:
    if not oms_jobs_enabled():
        return {"skipped": True, "reason": "OMS_JOBS_ENABLED=false"}
    from backend.services.oms.execution_algo import recover_stuck
    from backend.services.oms.order_store import ensure_schema

    ensure_schema()
    return recover_stuck(older_than_sec=float(os.getenv("OMS_STUCK_SEC", "300") or 300))


def _live_accounts() -> List[Dict[str, Any]]:
    """返回 [{account_id, exchange}]。取不到就空列表（不造账户）。"""
    try:
        from sqlalchemy import text
        from backend.database.connection import SessionLocal

        db = SessionLocal()
        try:
            rows = db.execute(text(
                "SELECT id, selected_exchange FROM accounts "
                "WHERE trading_mode = 'live' AND COALESCE(selected_exchange, '') <> ''"
            )).fetchall()
            return [{"account_id": int(r[0]), "exchange": str(r[1]).lower()} for r in rows]
        finally:
            db.close()
    except Exception as exc:
        logger.warning("[oms.jobs] 枚举 live 账户失败: %s", exc)
        return []


def _build_client(exchange: str, account_id: int):
    from backend.services.exchange.exchange_manager import ExchangeManager

    mgr = ExchangeManager()
    return mgr.create_fresh_client(exchange, account_id=account_id) or \
        mgr.get_or_create_global_client(exchange, account_id=account_id)


def scheduled_daily_reconcile() -> Dict[str, Any]:
    if not oms_jobs_enabled():
        return {"skipped": True, "reason": "OMS_JOBS_ENABLED=false"}
    from backend.core.tenant import set_system_identity
    from backend.services.oms.order_store import ensure_schema
    from backend.services.oms.reconcile import reconcile_account_sync

    set_system_identity()
    ensure_schema()
    accounts = _live_accounts()
    out: Dict[str, Any] = {"n_accounts": len(accounts), "results": [], "errors": []}
    if not accounts:
        out["note"] = "无 live 账户，跳过"
        return out

    for acct in accounts:
        aid, ex = acct["account_id"], acct["exchange"]
        try:
            client = _build_client(ex, aid)
            if client is None:
                out["errors"].append(f"acct={aid} ex={ex}: 无法创建客户端")
                continue
            # 确保有事件循环上下文（ccxt async）
            import asyncio

            try:
                if hasattr(client, "_ensure_loop"):
                    # 同步包装里会自己建 loop；这里先暖一下
                    pass
            except Exception:
                pass
            r = reconcile_account_sync(client, aid, ex)
            out["results"].append({
                "account_id": aid, "exchange": ex, "ok": r.get("ok"),
                "diff": r.get("diff"), "n_actions": (r.get("fix") or {}).get("n_actions"),
                "error": r.get("error"),
            })
        except Exception as exc:
            logger.exception("[oms.jobs] 对账失败 acct=%s", aid)
            out["errors"].append(f"acct={aid}: {str(exc)[:150]}")
    return out


def register_oms_jobs(task_scheduler, wrap: Callable[..., Any],
                      job: Callable[..., Any]) -> List[str]:
    registered: List[str] = []
    try:
        job("oms_stuck_scan", f"interval {STUCK_INTERVAL_SEC}s",
            "OMS 悬挂单扫描：intent/submitted/unknown 超时告警（不擅自撤补）",
            owner="oms", runner=scheduled_stuck_scan, expected_interval_sec=STUCK_INTERVAL_SEC)
        task_scheduler.add_interval_task(
            task_func=wrap("oms_stuck_scan", scheduled_stuck_scan),
            interval_seconds=STUCK_INTERVAL_SEC, task_id="v3_oms_stuck_scan", max_instances=1,
        )
        registered.append("oms_stuck_scan")
    except Exception as exc:
        logger.warning("[oms.jobs] oms_stuck_scan 注册失败: %s", exc)

    try:
        from backend.services.analysis.scheduling import off_peak_cron

        cron = off_peak_cron(RECONCILE_HOUR_LOCAL, RECONCILE_MINUTE_LOCAL)
        job("oms_daily_reconcile",
            f"cron {cron['hour']:02d}:{cron['minute']:02d} (BJ {RECONCILE_HOUR_LOCAL}:{RECONCILE_MINUTE_LOCAL:02d})",
            "OMS 订单级日对账：本地 live_orders vs 交易所 fetch_orders；默认只报告",
            owner="oms", runner=scheduled_daily_reconcile, expected_interval_sec=86400)
        task_scheduler.add_cron_task(
            task_func=wrap("oms_daily_reconcile", scheduled_daily_reconcile),
            task_id="v3_oms_daily_reconcile", hour=cron["hour"], minute=cron["minute"],
        )
        registered.append("oms_daily_reconcile")
    except Exception as exc:
        logger.warning("[oms.jobs] oms_daily_reconcile 注册失败: %s", exc)

    logger.info("[oms.jobs] 已注册: %s", registered)
    return registered
