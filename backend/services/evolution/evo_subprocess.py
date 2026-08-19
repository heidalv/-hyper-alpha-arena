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

    report = run_factor_evolution_loop(period=args.period, quick=args.quick, source="subprocess")
    logger.info("[EvoSubprocess] 完成 period=%s %s", args.period, str(report)[:500])
    return 0 if not report.get("error") else 1


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
                    stdout=None,  # 继承主进程 stdout（日志由子进程 FileHandler 落盘）
                    stderr=None,
                )
                return {"subprocess_pid": proc.pid, "period": period}
            except Exception as e:  # noqa: BLE001
                logger.warning("[EvoSubprocess] 出进程启动失败，回退进程内: %s", e)
        return fallback_fn()

    return _task


if __name__ == "__main__":
    raise SystemExit(main())
