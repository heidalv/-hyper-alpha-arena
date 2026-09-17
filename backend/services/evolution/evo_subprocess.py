"""出进程因子进化入口（FIX-4，2026-08-19）。

把重挖掘（GP/MCTS GPU 批量求值 + 门禁）从 Web 后端进程隔离到独立子进程：
  - 避免 GPU 求值的 Python 级编排与 uvicorn/APScheduler 争 GIL（独立进程 6s/300树 vs 进程内 200~500s）；
  - 子进程崩/被 kill 不影响主服务；后端重启也不腰斩挖掘；
  - 独立 DB 会话、独立 CUDA 上下文。

用法：
  python -m backend.services.evolution.evo_subprocess 4h [--quick]

调度接线：make_subprocess_task(period, fallback_fn) 包一层 cron task_func；
FACTOR_EVO_SUBPROCESS=1 时出进程，否则回退原函数（默认关，可回滚）。
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys

logger = logging.getLogger(__name__)


def _repo_root() -> str:
    # backend/services/evolution/evo_subprocess.py -> 上溯 3 层 = 仓库根
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _setup_logging() -> None:
    root = logging.getLogger()
    # 幂等：已有 FileHandler 说明主进程已配置（in-process 路径），不重复
    if any(isinstance(h, logging.FileHandler) for h in root.handlers):
        return
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] [tr=-] %(name)s:%(lineno)d - %(message)s"
    )
    try:
        log_dir = os.path.join(_repo_root(), "logs")
        os.makedirs(log_dir, exist_ok=True)
        fh = logging.FileHandler(os.path.join(log_dir, "evo_subprocess.log"), encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except Exception:  # noqa: BLE001
        pass
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    root.setLevel(logging.INFO)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="出进程因子进化（单周期）")
    parser.add_argument("period", help="4h / 5m / 15m / 1h ...")
    parser.add_argument("--quick", action="store_true", help="quick 模式")
    args = parser.parse_args(argv)

    _setup_logging()
    logger.info("[EvoSubprocess] 启动 period=%s quick=%s", args.period, args.quick)
    from backend.services.evolution.factor_evolution_loop import run_factor_evolution_loop

    # [轮50 2026-09-17] 可见性接线：进化任务此前**从不写 job_registry**，导致
    # 无论成功/失败/超时，在所有健康检查与 ops 面板上都是隐形的 —— 失败只落进
    # logs/evo_subprocess.log 的一行 INFO，无人告警（用户 2026-09-17 指出：
    # "要是明早自动跑有问题，还是发现不了"）。
    # 这里登记元数据（expected_interval_sec=24h，用于陈旧判定）并用 job_run 包裹：
    #   成功 → 记 ok + duration_ms；异常/error → 记 error + _alert_failure + 抛出。
    # 回滚：FACTOR_EVO_JOB_REGISTRY=false（跳过接线，回到纯日志行为）。
    _job_name = f"factor_evolution_{args.period}"
    _cm = None
    try:
        if str(os.getenv("FACTOR_EVO_JOB_REGISTRY", "true")).strip().lower() in ("1", "true", "yes", "on"):
            from backend.services.ops.job_registry import job_run, register_job
            register_job(
                _job_name,
                cadence="daily",
                description=f"因子进化（{args.period} 周期，出进程完整 WFO/门禁）",
                owner="factor_evolution",
                expected_interval_sec=24 * 3600,
            )
            _cm = job_run(_job_name)
    except Exception as _reg_err:  # noqa: BLE001
        logger.warning("[EvoSubprocess] job_registry 接线失败（不影响进化）: %s", _reg_err)
    if _cm is None:
        import contextlib
        _cm = contextlib.nullcontext()

    try:
        with _cm:
            report = run_factor_evolution_loop(period=args.period, quick=args.quick, source="subprocess")
            _err = report.get("error")
            if _err:
                # 让 job_registry 记 error + 告警（此前只靠退出码 1，外部看不见）
                raise RuntimeError(f"evolution reported error: {_err}")
    except Exception as exc:  # noqa: BLE001
        logger.error("[EvoSubprocess] period=%s 失败: %s", args.period, exc)
        return 1
    logger.info("[EvoSubprocess] 完成 period=%s %s", args.period, str(report)[:500])
    return 0


def make_subprocess_task(period: str, fallback_fn):
    """返回一个 cron task_func：FACTOR_EVO_SUBPROCESS=1 时出进程，否则回退 fallback_fn。"""

    def _task():
        if os.getenv("FACTOR_EVO_SUBPROCESS", "0") == "1":
            cmd = [sys.executable, "-m", "backend.services.evolution.evo_subprocess", period]
            logger.info("[EvoSubprocess] 出进程运行 period=%s cmd=%s", period, " ".join(cmd))
            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=_repo_root(),
                    # [2026-09-05] DEVNULL：子进程日志走自己的 FileHandler 落盘，不需要
                    # 主进程 stdout。此前 stdout=None 继承 backend.log 句柄——主进程
                    # 死后孤儿 evo 子进程仍握着句柄，导致新后端 `>> backend.log`
                    # 重定向失败、秒退无日志（2026-09-05 03:16 事故根因）。
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return {"subprocess_pid": proc.pid, "period": period}
            except Exception as e:  # noqa: BLE001
                logger.warning("[EvoSubprocess] 出进程启动失败，回退进程内: %s", e)
        return fallback_fn()

    return _task


if __name__ == "__main__":
    raise SystemExit(main())
