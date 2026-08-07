"""factor_jobs — 因子重活的后台异步任务管理器（进程内 · 单 worker 线程池）。

背景
====
`/api/factors/validate` 与 `/api/factors/alpha101/validate` 会对多个候选因子逐个做
样本外回测（拉多标的、多周期 K 线，纯 CPU + 网络），单次可耗时数分钟，HTTP 同步
调用极易触发客户端超时。本模块提供轻量的进程内任务队列：

- 提交后**立即返回 job_id**，不阻塞请求线程；
- **单 worker 串行**执行，避免并发重活拖垮实盘/模拟盘决策主链；
- 同类任务**单飞（single-flight）**：已有 pending/running 的同类任务时直接复用，
  防止前端重复点击触发多份重活；
- 支持 `GET` 轮询状态 / 进度 / 结果。

注意：任务记录为进程内内存态，后端重启后清空（重活本就应按需重跑）。
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# 保留最近完成/失败的任务数（避免内存无限增长）
_MAX_KEEP = 50


class _Job:
    """单个后台任务的运行时状态。"""

    __slots__ = (
        "id", "kind", "status", "created_at", "started_at", "finished_at",
        "result", "error", "progress", "total", "message",
    )

    def __init__(self, job_id: str, kind: str):
        self.id = job_id
        self.kind = kind
        self.status = "pending"  # pending -> running -> done|error
        self.created_at = time.time()
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None
        self.result: Any = None
        self.error: Optional[str] = None
        self.progress = 0
        self.total = 0
        self.message = ""

    def to_dict(self) -> Dict[str, Any]:
        now = time.time()
        base = self.started_at or self.created_at
        end = self.finished_at or now
        return {
            "job_id": self.id,
            "kind": self.kind,
            "status": self.status,
            "created_at": round(self.created_at, 3),
            "started_at": round(self.started_at, 3) if self.started_at else None,
            "finished_at": round(self.finished_at, 3) if self.finished_at else None,
            "elapsed_sec": round(end - base, 1),
            "progress": self.progress,
            "total": self.total,
            "percent": round(100.0 * self.progress / self.total, 1) if self.total else None,
            "message": self.message,
            "result": self.result,
            "error": self.error,
        }


class FactorJobManager:
    """进程内、单 worker 的因子任务管理器。"""

    def __init__(self):
        self._jobs: Dict[str, _Job] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="factor-job")

    def _find_active_by_kind(self, kind: str) -> Optional[_Job]:
        for j in self._jobs.values():
            if j.kind == kind and j.status in ("pending", "running"):
                return j
        return None

    def submit(
        self,
        kind: str,
        fn: Callable[["_Job"], Any],
        *,
        single_flight: bool = True,
    ) -> _Job:
        """提交任务；single_flight=True 时若同类任务在跑则复用之。"""
        with self._lock:
            if single_flight:
                existing = self._find_active_by_kind(kind)
                if existing is not None:
                    logger.info("[FactorJob] reuse active job id=%s kind=%s", existing.id, kind)
                    return existing
            job = _Job(uuid.uuid4().hex[:12], kind)
            self._jobs[job.id] = job
            self._prune_locked()
        self._executor.submit(self._run, job, fn)
        return job

    def _run(self, job: _Job, fn: Callable[["_Job"], Any]) -> None:
        job.status = "running"
        job.started_at = time.time()
        logger.info("[FactorJob] start id=%s kind=%s", job.id, job.kind)
        try:
            job.result = fn(job)
            job.status = "done"
        except Exception as e:  # noqa: BLE001
            job.error = str(e)[:500]
            job.status = "error"
            logger.exception("[FactorJob] failed id=%s kind=%s: %s", job.id, job.kind, e)
        finally:
            job.finished_at = time.time()
            logger.info(
                "[FactorJob] end id=%s kind=%s status=%s elapsed=%.1fs",
                job.id, job.kind, job.status,
                job.finished_at - (job.started_at or job.finished_at),
            )

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            j = self._jobs.get(job_id)
            return j.to_dict() if j else None

    def list(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            js = sorted(self._jobs.values(), key=lambda x: x.created_at, reverse=True)[:limit]
            return [j.to_dict() for j in js]

    def _prune_locked(self) -> None:
        done = [j for j in self._jobs.values() if j.status in ("done", "error")]
        if len(done) <= _MAX_KEEP:
            return
        done.sort(key=lambda x: x.finished_at or 0)
        for j in done[: len(done) - _MAX_KEEP]:
            self._jobs.pop(j.id, None)


factor_job_manager = FactorJobManager()


# ══════════════════════════════════════════════════════════════
#  预置任务：与旧同步接口等价，但带逐因子进度上报
# ══════════════════════════════════════════════════════════════

def run_validate_alpha101(limit: int = 50) -> _Job:
    """后台执行 Alpha101 中长线候选样本外打分晋升（带进度）。"""

    def _fn(job: _Job) -> Dict[str, Any]:
        from backend.services.factor_engine.custom_factor_store import custom_factor_store
        from backend.services.factor_engine.factor_backtest_scorer import factor_backtest_scorer

        cands = [
            r for r in custom_factor_store.list_candidates()
            if r.get("category") == "alpha101"
            and str((r.get("extra") or {}).get("horizon") or "").lower() == "midlong"
        ][:limit]
        job.total = len(cands)
        results: List[Dict[str, Any]] = []
        for i, rec in enumerate(cands):
            fid = rec.get("factor_id")
            job.message = f"打分 {fid} ({i + 1}/{job.total})"
            try:
                r = factor_backtest_scorer.validate_and_promote(rec["factor_id"])
                results.append({
                    "factor_id": r.factor_id, "grade": r.grade,
                    "admitted": r.admitted, "ic": r.ic_mean,
                    "oos_sharpe": r.oos_sharpe, "reason": r.reason,
                })
            except Exception as e:  # noqa: BLE001
                logger.warning("[Alpha101] %s 打分异常: %s", fid, e)
            job.progress = i + 1
        promoted = [r for r in results if r["admitted"]]
        logger.info("[Alpha101] 后台验证完成: 打分%d 晋升%d", len(results), len(promoted))
        return {"scored": len(results), "promoted": len(promoted), "results": results}

    return factor_job_manager.submit("alpha101_validate", _fn)


def run_validate_candidates(limit: int = 20) -> _Job:
    """后台执行全部候选因子（含短线/中长线）样本外打分晋升（带进度）。"""

    def _fn(job: _Job) -> Dict[str, Any]:
        from backend.services.factor_engine.custom_factor_store import custom_factor_store
        from backend.services.factor_engine.factor_backtest_scorer import factor_backtest_scorer

        cands = custom_factor_store.list_candidates()[:limit]
        job.total = len(cands)
        results: List[Dict[str, Any]] = []
        for i, rec in enumerate(cands):
            fid = rec.get("factor_id")
            job.message = f"打分 {fid} ({i + 1}/{job.total})"
            try:
                r = factor_backtest_scorer.validate_and_promote(rec["factor_id"])
                results.append({
                    "factor_id": r.factor_id, "grade": r.grade,
                    "admitted": r.admitted, "reason": r.reason,
                })
            except Exception as e:  # noqa: BLE001
                logger.warning("[FactorScorer] %s 打分异常: %s", fid, e)
            job.progress = i + 1
        promoted = [r for r in results if r["admitted"]]
        return {"scored": len(results), "promoted": len(promoted), "results": results}

    return factor_job_manager.submit("candidates_validate", _fn)


def run_train_scalp_meta() -> _Job:
    """后台执行短线元标签模型训练+验证（样本不足会优雅跳过）。"""

    def _fn(job: _Job) -> Dict[str, Any]:
        job.total = 1
        job.message = "读取真实信号 + 训练/验证元标签模型"
        from backend.services.scalp_meta_trainer import train_and_validate
        rep = train_and_validate()
        job.progress = 1
        job.message = f"完成: {rep.get('status')} usable={rep.get('usable')}"
        return rep

    return factor_job_manager.submit("scalp_meta_train", _fn)
