"""出进程主脑批次（2026-09-08）— 把 context_pack 重计算从 API 进程隔离到独立子进程。

背景（GIL 治本）：主脑批次的 CPU 大头是 _build_context_pack 给全池币种算指标（13~21s）。
此前它在 API 进程的后台线程里跑——CPython 的 GIL 让同一进程任意时刻只有一个线程执行
Python 字节码，于是这批重计算与 uvicorn 事件循环抢同一个核，表现为网页 API 卡十几秒
（36 核机器只用 1 核）。拆到独立子进程后：子进程有自己的 GIL 与 DB 会话，重计算吃别的核，
API 进程彻底解放。模式复用 evolution/evo_subprocess.py（因子进化已验证）。

用法：
  python -m backend.services.mlto.brain_subprocess --tier mid --symbols BTC,ETH --trigger scheduler

调度接线：midlong_brain_scheduler._run_batch_in_background 在 MIDLONG_BRAIN_SUBPROCESS=1
时 spawn 本模块（fire-and-forget，子进程直接写库），失败回退进程内线程。
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

logger = logging.getLogger(__name__)


def _repo_root() -> str:
    # backend/services/mlto/brain_subprocess.py -> 上溯 3 层 = 仓库根
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _setup_logging() -> None:
    root = logging.getLogger()
    # 幂等：已有 FileHandler 不重复挂
    if any(isinstance(h, logging.FileHandler) for h in root.handlers):
        return
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] [tr=-] %(name)s:%(lineno)d - %(message)s"
    )
    try:
        log_dir = os.path.join(_repo_root(), "logs")
        os.makedirs(log_dir, exist_ok=True)
        fh = logging.FileHandler(os.path.join(log_dir, "brain_subprocess.log"), encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except Exception:  # noqa: BLE001
        pass
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    root.setLevel(logging.INFO)


def main(argv=None) -> int:
    # [2026-09-08] 在任何 numpy/torch/pandas 导入前锁 BLAS/OpenMP 为单线程（照 run_uvicorn_dev.py）：
    # 子进程是一次性短进程，不需要 BLAS 自建线程池；也避免 torch/numba 触发额外原生线程。
    for _v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
               "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "TOKENIZERS_PARALLELISM"):
        os.environ.setdefault(_v, "1" if _v != "TOKENIZERS_PARALLELISM" else "false")
    try:
        import torch  # noqa: E402
        torch.set_num_threads(1)
        torch.set_num_interop_threads(1)
    except Exception:  # noqa: BLE001
        pass

    parser = argparse.ArgumentParser(description="出进程主脑批次（单批）")
    parser.add_argument("--tier", default="mid", help="mid / long / short(intraday)")
    parser.add_argument("--symbols", default="", help="逗号分隔币种，空=让 brain 自己选")
    parser.add_argument("--trigger", default="scheduler")
    args = parser.parse_args(argv)

    _setup_logging()
    # sys.path：仓库根 + backend/ 都挂上——brain.py 及其依赖混用限定(backend.*)与
    # 非限定(from database.models import ...)导入，缺 backend/ 会在子进程里 ImportError。
    _root = _repo_root()
    _backend = os.path.join(_root, "backend")
    for _p in (_root, _backend):
        if _p not in sys.path:
            sys.path.append(_p)
    symbols = [s.strip().upper() for s in (args.symbols or "").split(",") if s.strip()]
    logger.info(
        "[BrainSubprocess] 启动 tier=%s symbols=%d trigger=%s pid=%d",
        args.tier, len(symbols), args.trigger, os.getpid(),
    )
    try:
        # RLS 穿透：子进程独立 DB 会话，需系统身份否则查不到 session（09-05 RLS 教训）
        try:
            from backend.core.tenant import set_system_identity
            set_system_identity()
        except Exception:  # noqa: BLE001
            pass

        from backend.database.connection import SessionLocal
        from backend.database.models import FullAutoSession
        from backend.services.mlto.brain import run_midlong_brain_batch

        db = SessionLocal()
        try:
            sess = (
                db.query(FullAutoSession)
                .filter(FullAutoSession.status == "running")
                .order_by(FullAutoSession.id.desc())
                .first()
            )
            if sess is not None:
                db.expunge(sess)  # 脱离 session，纯数据载体
        finally:
            db.close()
        if sess is None:
            logger.warning("[BrainSubprocess] 无 running 会话，退出")
            return 0

        summary = run_midlong_brain_batch(
            host=None, session=sess, symbols=symbols, tier=args.tier,
            market_summary=None,  # 子进程自己建 context_pack（这正是要挪出 API 的重活）
            analysis_only=True,   # 只分析+写论题，不开仓（开仓留 API 进程用真实 host）
        )
        logger.info("[BrainSubprocess] 完成 tier=%s n=%d", args.tier, len(summary or []))
        return 0
    except Exception as e:  # noqa: BLE001
        logger.exception("[BrainSubprocess] 批次异常 tier=%s: %s", args.tier, e)
        return 1


if __name__ == "__main__":
    # [2026-09-08] Windows multiprocessing spawn 会以 `-m brain_subprocess` 重新执行本模块，
    # 且 `-m` 把 __name__ 设成 __main__——若不拦截，被 spawn 出的子进程会再跑一遍 main()
    # （双重 LLM 调用）。freeze_support() 识别"被 spawn 的子进程"并只跑其 target、不碰 main。
    import multiprocessing as _mp
    _mp.freeze_support()
    raise SystemExit(main())
