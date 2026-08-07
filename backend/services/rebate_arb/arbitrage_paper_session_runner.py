"""Arbitrage dedicated Paper verification session runner."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

JOB_ID = "arbitrage_paper_verification_tick"
DEFAULT_INTERVAL_SECONDS = 90


def _interval_seconds() -> int:
    raw = os.getenv("ARBITRAGE_PAPER_TICK_SECONDS", str(DEFAULT_INTERVAL_SECONDS))
    try:
        return max(30, int(raw))
    except Exception:
        return DEFAULT_INTERVAL_SECONDS


def _default_auto_execute() -> bool:
    """Paper 验证会话默认自动执行；可用环境变量显式关闭。"""
    return os.getenv("ARBITRAGE_PAPER_AUTO_EXECUTE", "true").strip().lower() != "false"


class ArbitragePaperSessionRunner:
    def __init__(self) -> None:
        self._session: Optional[Dict[str, Any]] = None
        self._scheduled = False
        self._last_bound_account: Optional[int] = None
        self._last_bound_strategies: Optional[tuple] = None

    def get_status(self) -> Dict[str, Any]:
        if not self._session:
            return {"running": False}
        return {"running": True, **self._session}

    def _bind_runtime(self, account_id: int, strategies: List[str]) -> None:
        from backend.services.rebate_arb.capital_coordinator import capital_coordinator
        from backend.services.rebate_arb.engine import rebate_arb_engine
        from backend.services.rebate_arb.arbitrage_paper_account_service import STRATEGY_IDS

        capital_coordinator.set_arbitrage_paper_account(account_id)
        rebate_arb_engine.paper_mode = True
        try:
            from backend.database.connection import SessionLocal
            from backend.services.rebate_arb.arbitrage_paper_account_service import (
                arbitrage_paper_account_service,
            )

            db = SessionLocal()
            try:
                arbitrage_paper_account_service.reconcile_orphan_s8_positions(db, account_id)
            finally:
                db.close()
        except Exception as exc:
            logger.warning("[ArbPaperRunner] S8 reconcile skip: %s", exc)
        rebate_arb_engine.initialize()

        selected = tuple(sorted(s.upper() for s in strategies))
        if self._last_bound_account == account_id and self._last_bound_strategies == selected:
            try:
                from backend.services.rebate_arb.engine import rebate_arb_engine

                if not rebate_arb_engine._active_positions:
                    rebate_arb_engine.reload_active_positions()
            except Exception:
                pass
            return

        selected_set = set(selected)
        strategies_patch = {sid: {"enabled": sid in selected_set} for sid in STRATEGY_IDS}
        rebate_arb_engine.apply_config_patch({"strategies": strategies_patch})
        self._last_bound_account = account_id
        self._last_bound_strategies = selected

    def _run_tick(self) -> Dict[str, Any]:
        if not self._session:
            return {"skipped": True, "reason": "no_active_session"}

        account_id = int(self._session["account_id"])
        strategies = list(self._session.get("strategies") or [])
        trader_profile = self._session.get("trader_profile")
        self._bind_runtime(account_id, strategies)

        try:
            from backend.services.rebate_arb.rebate_position_mtm import refresh_all_paper_positions_mtm

            refresh_all_paper_positions_mtm()
        except Exception:
            pass

        from backend.services.rebate_arb.tick_context import resolve_account_equity
        from backend.services.rebate_arb.qaa_rebate_tick import run_qaa_rebate_tick
        from backend.services.rebate_arb.trader_llm_resolver import resolve_rebate_tick_params

        equity = resolve_account_equity()
        if not trader_profile:
            trader_profile = resolve_rebate_tick_params(
                arbitrage_paper_account_id=account_id,
                enabled_strategies=strategies,
            )
        selected_strategies = [s.upper() for s in strategies]
        if trader_profile and isinstance(trader_profile, dict):
            trader_profile = {**trader_profile, "enabled_strategies": selected_strategies}

        result = run_qaa_rebate_tick(
            account_equity=equity if equity > 0 else None,
            auto_execute=bool(self._session.get("auto_execute", _default_auto_execute())),
            enabled_strategies=selected_strategies,
            trader_profile_id=(trader_profile or {}).get("profile_id"),
            trader_account_id=(trader_profile or {}).get("trader_account_id"),
            arbitrage_paper_account_id=account_id,
            profile_snapshot=trader_profile if isinstance(trader_profile, dict) else None,
            source="api",
        )
        self._session["last_tick_at"] = time.time()
        self._session["last_tick"] = {
            "account_equity": result.get("account_equity", equity),
            "total_evaluated": result.get("total_evaluated", 0),
            "viable_count": result.get("viable_count", 0),
            "auto_executed": result.get("auto_executed", False),
            "top_strategy": result.get("top_strategy"),
            "auto_exec_error": (result.get("auto_exec_result") or {}).get("error"),
        }
        self._session["tick_count"] = int(self._session.get("tick_count") or 0) + 1
        logger.info(
            "[ArbPaperRunner] tick #%s account=%s viable=%s auto=%s equity=%.0f",
            self._session["tick_count"],
            account_id,
            result.get("viable_count", 0),
            result.get("auto_executed", False),
            float(result.get("account_equity") or equity or 0),
        )
        return result

    def _tick_task(self) -> Dict[str, Any]:
        try:
            return self._run_tick()
        except Exception as exc:
            logger.warning("[ArbPaperRunner] tick failed: %s", exc)
            return {"success": False, "error": str(exc)}

    def _ensure_scheduled(self) -> None:
        if self._scheduled:
            return
        try:
            from backend.services.scheduler import task_scheduler

            if not task_scheduler.is_running():
                task_scheduler.start()
            task_scheduler.add_interval_task(
                task_func=self._tick_task,
                interval_seconds=_interval_seconds(),
                task_id=JOB_ID,
                max_instances=1,
            )
            self._scheduled = True
            logger.info("[ArbPaperRunner] scheduled every %ss", _interval_seconds())
        except Exception as exc:
            logger.warning("[ArbPaperRunner] schedule failed: %s", exc)

    def _clear_schedule(self) -> None:
        try:
            from backend.services.scheduler import task_scheduler

            task_scheduler.remove_task(JOB_ID)
        except Exception:
            pass
        self._scheduled = False

    def start_session(
        self,
        account_id: int,
        strategies: List[str],
        trader_profile: Optional[Dict[str, Any]] = None,
        auto_execute: Optional[bool] = None,
    ) -> Dict[str, Any]:
        selected = [s.upper() for s in strategies if s]
        now = time.time()
        self._session = {
            "account_id": account_id,
            "strategies": selected,
            "trader_profile": trader_profile,
            "started_at": now,
            "last_tick_at": None,
            "tick_count": 0,
            "interval_seconds": _interval_seconds(),
            "auto_execute": (
                _default_auto_execute() if auto_execute is None else bool(auto_execute)
            ),
        }
        self._bind_runtime(account_id, selected)
        self._ensure_scheduled()
        first_tick = self._run_tick()
        return {
            "session": self.get_status(),
            "first_tick": first_tick,
        }

    def stop_session(self, account_id: Optional[int] = None) -> Dict[str, Any]:
        if not self._session:
            return {"success": True, "running": False, "message": "无运行中的 Paper 验证会话"}
        if account_id and int(self._session.get("account_id") or 0) != int(account_id):
            return {
                "success": False,
                "error": f"当前运行账户 #{self._session.get('account_id')} 与请求 #{account_id} 不一致",
            }
        self._session = None
        self._last_bound_account = None
        self._last_bound_strategies = None
        self._clear_schedule()
        return {"success": True, "running": False}

    def restore_from_db(self) -> bool:
        try:
            from backend.database.connection import SessionLocal
            from backend.database.models import ArbitragePaperAccountDB

            db = SessionLocal()
            try:
                rows = (
                    db.query(ArbitragePaperAccountDB)
                    .filter(ArbitragePaperAccountDB.status == "running")
                    .all()
                )
                if not rows:
                    return False

                def _started_at(row: ArbitragePaperAccountDB) -> float:
                    try:
                        meta = json.loads(row.metadata_json or "{}")
                    except Exception:
                        meta = {}
                    return float((meta.get("last_start") or {}).get("started_at") or 0)

                row = sorted(rows, key=_started_at, reverse=True)[0]
                try:
                    meta = json.loads(row.metadata_json or "{}")
                except Exception:
                    meta = {}
                strategies = list((meta.get("last_start") or {}).get("strategies") or ["S8"])
                trader_profile = (meta.get("last_start") or {}).get("trader_profile")
                logger.info(
                    "[ArbPaperRunner] restore account #%s strategies=%s",
                    row.id,
                    strategies,
                )
                self.start_session(int(row.id), strategies, trader_profile=trader_profile)
                return True
            finally:
                db.close()
        except Exception as exc:
            logger.warning("[ArbPaperRunner] restore failed: %s", exc)
            return False


arbitrage_paper_session_runner = ArbitragePaperSessionRunner()
