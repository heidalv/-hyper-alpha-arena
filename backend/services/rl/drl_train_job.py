"""
DRL 训练任务（P0-3）

职责：把原先散落在 `api/rl_routes._run_training_task` 里的训练实现抽成可内部调用的
`run_shadow_training(...)`，供两类调用方复用：
  1. HTTP 入口 `/api/rl/train`（保留，走 run_shadow_training）
  2. LearningLoop `_tick_coordinator` 收到 `trigger_drl_retrain=True` 时直接调

特点：
- 共享 `rl_singleton.get_rl_optimizer()`，训练后 save 到 ppo_latest.zip
- 训练完成写 `SystemCoordinatorState.last_drl_training_at + drl_model_version`
- 带 2 小时冷却：同一进程里连续触发无效
- 异步运行（threading），不阻塞调用方
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# 冷却控制：上次训练完成时间戳（单进程内存，持久化通过 DB 恢复）
_LAST_TRAIN_AT: float = 0.0
_COOLDOWN_SECONDS: float = 2 * 3600  # P1-4：DRL 重训 2h 冷却
_RUN_LOCK = threading.Lock()
_ACTIVE_RUN_ID: Optional[str] = None


def _restore_cooldown_from_db() -> None:
    """启动时从 SystemCoordinatorState 恢复 _LAST_TRAIN_AT，避免重启抹平冷却。"""
    global _LAST_TRAIN_AT
    try:
        from backend.database.connection import MarketSessionLocal
        from backend.database.models import SystemCoordinatorState
        db = MarketSessionLocal()
        try:
            state = db.query(SystemCoordinatorState).first()
            if state and state.last_drl_training_at:
                ts = state.last_drl_training_at.timestamp()
                _LAST_TRAIN_AT = max(_LAST_TRAIN_AT, ts)
        finally:
            db.close()
    except Exception as e:
        logger.debug(f"[drl_train_job] restore cooldown skipped: {e}")


def is_in_cooldown() -> tuple[bool, float]:
    """返回 (是否冷却中, 距离冷却结束秒数)。"""
    global _LAST_TRAIN_AT
    now = time.time()
    since = now - _LAST_TRAIN_AT
    if since < _COOLDOWN_SECONDS:
        return True, _COOLDOWN_SECONDS - since
    return False, 0.0


def _record_tasks_store(task_id: str, update: Dict[str, Any]) -> None:
    """把任务状态同步到 rl_routes 的 _training_tasks 仓库（可选）。

    HTTP 端需要用 task_id 查进度；LearningLoop 调时不关心。
    使用 lazy import + 静默失败，不作强依赖。
    """
    try:
        from backend.api.rl_routes import _training_tasks, _training_lock, _latest_task_id  # type: ignore
        with _training_lock:
            if task_id not in _training_tasks:
                _training_tasks[task_id] = {
                    "task_id": task_id,
                    "is_training": True,
                    "status": "running",
                    "progress_pct": 0,
                    "current_timestep": 0,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                }
            _training_tasks[task_id].update(update)
            # 更新 _latest_task_id 指针（若当前任务更新）
            # 该变量是 module-level，无法在此直接反向写；HTTP 入口创建时已赋值
    except Exception:
        pass


def _do_train(task_id: str, total_timesteps: int, learning_rate: float) -> bool:
    """实际训练过程：读 K 线 → 构建 env → 训练 → save → 写 DB。"""
    try:
        from backend.services.rl.rl_singleton import get_rl_optimizer

        optimizer = get_rl_optimizer()
        if optimizer is None or not optimizer.is_available:
            _record_tasks_store(task_id, {
                "is_training": False,
                "status": "failed",
                "error": "DRL not available (stable-baselines3 not installed)",
                "finished_at": datetime.now(timezone.utc).isoformat(),
            })
            return False

        from backend.services.rl import TradingEnv
        import pandas as pd
        from backend.database.connection import MarketSessionLocal
        from backend.database.models import CryptoKline

        db = MarketSessionLocal()
        try:
            klines = db.query(CryptoKline).filter(
                CryptoKline.symbol == "BTC",
                CryptoKline.period == "1h",
            ).order_by(CryptoKline.timestamp.desc()).limit(1000).all()

            if len(klines) < 100:
                raise ValueError(
                    f"Insufficient kline data: {len(klines)} rows, need at least 100"
                )

            klines_df = pd.DataFrame([{
                "timestamp": k.timestamp,
                "open": float(k.open_price),
                "high": float(k.high_price),
                "low": float(k.low_price),
                "close": float(k.close_price),
                "volume": float(k.volume),
            } for k in reversed(klines)])
        finally:
            db.close()

        env = TradingEnv(klines=klines_df)

        # ── 进度回调：每个 rollout 后更新 _training_tasks ──
        def _on_progress(pct: float, current_ts: int, total_ts: int):
            _record_tasks_store(task_id, {
                "progress_pct": pct,
                "current_timestep": current_ts,
                "total_timesteps": total_ts,
                "is_training": True,
                "status": "running",
            })

        success = optimizer.train(
            env,
            total_timesteps=total_timesteps,
            learning_rate=learning_rate,
            save_after=True,
            progress_callback=_on_progress,
        )

        # 写回 SystemCoordinatorState.last_drl_training_at / drl_model_version
        try:
            db2 = MarketSessionLocal()
            try:
                from backend.database.models import SystemCoordinatorState
                state = db2.query(SystemCoordinatorState).first()
                now_utc = datetime.now(timezone.utc)
                if state is None:
                    state = SystemCoordinatorState(
                        last_drl_training_at=now_utc,
                        drl_model_version=optimizer.model_version,
                    )
                    db2.add(state)
                else:
                    state.last_drl_training_at = now_utc
                    if hasattr(state, "drl_model_version"):
                        state.drl_model_version = optimizer.model_version
                db2.commit()
            finally:
                db2.close()
        except Exception as _e:
            logger.warning(f"[drl_train_job] 更新 SystemCoordinatorState 失败: {_e}")

        _record_tasks_store(task_id, {
            "is_training": False,
            "progress_pct": 100,
            "status": "completed" if success else "completed_with_warnings",
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "model_version": optimizer.model_version,
        })
        return bool(success)
    except Exception as e:
        logger.error(f"[drl_train_job] training task {task_id} error: {e}", exc_info=True)
        _record_tasks_store(task_id, {
            "is_training": False,
            "status": "failed",
            "error": str(e),
            "finished_at": datetime.now(timezone.utc).isoformat(),
        })
        return False


def run_shadow_training(
    total_timesteps: int = 100000,
    learning_rate: float = 3e-4,
    task_id: Optional[str] = None,
    respect_cooldown: bool = True,
) -> Dict[str, Any]:
    """异步启动一次 shadow 训练。

    Returns:
        {"started": bool, "reason": str, "task_id": str}
    """
    global _LAST_TRAIN_AT, _ACTIVE_RUN_ID

    if respect_cooldown:
        cooling, remain = is_in_cooldown()
        if cooling:
            return {
                "started": False,
                "reason": f"cooldown remaining {remain:.0f}s",
                "task_id": task_id,
            }

    with _RUN_LOCK:
        if _ACTIVE_RUN_ID is not None:
            return {
                "started": False,
                "reason": f"another run in progress ({_ACTIVE_RUN_ID})",
                "task_id": task_id,
            }
        effective_task_id = task_id or f"drl_{uuid.uuid4().hex[:12]}"
        _ACTIVE_RUN_ID = effective_task_id

    def _runner():
        global _LAST_TRAIN_AT, _ACTIVE_RUN_ID
        try:
            _do_train(effective_task_id, total_timesteps, learning_rate)
        finally:
            _LAST_TRAIN_AT = time.time()
            with _RUN_LOCK:
                _ACTIVE_RUN_ID = None

    t = threading.Thread(
        target=_runner,
        daemon=True,
        name=f"drl-train-{effective_task_id}",
    )
    t.start()
    return {"started": True, "reason": "ok", "task_id": effective_task_id}
