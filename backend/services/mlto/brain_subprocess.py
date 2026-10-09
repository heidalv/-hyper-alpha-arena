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
import logging.handlers
import os
import sys
import time

logger = logging.getLogger(__name__)

#: [2026-09-20] `logs/brain_subprocess.log` 此前**无任何轮转**，实测 13.1 MB/天、
#: 11.5 天涨到 150.3 MB（94.6 万行）；且 `log_retention_service` 只清 `*.log.*`、
#: 不碰活文件 ⇒ 无上限增长。20 MB × (1 活 + 5 备) = 120 MB 上限 ≈ 7.6 天留存。
_LOG_MAX_MB_DEFAULT = 20
_LOG_BACKUPS_DEFAULT = 5
#: 轮转被并发写入阻塞时，`# [rotate-deferred]` 标记的最小间隔（秒），避免刷屏。
_ROTATE_DEFER_MARK_SEC = 300.0


def _repo_root() -> str:
    # backend/services/mlto/brain_subprocess.py -> 上溯 3 层 = 仓库根
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _env_int(name: str, default: int) -> int:
    raw = str(os.getenv(name, "") or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except Exception:  # noqa: BLE001
        return default


class _MultiProcSafeRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """按大小轮转 + 多子进程并发安全（[2026-09-20]）。

    为什么不直接用标准库 `RotatingFileHandler` —— 在**本项目运行期解释器**（`.venv` = Python 3.12.10）
    上实测（探针：base 日志被 peer 进程握着 ⇒ `base→备份` 改名必失败，发 6 条日志）：

        stdlib RotatingFileHandler : 活文件里只剩 1 条，handleError 被调 5 次 ⇒ 5 条日志被丢弃
        本类                        : 活文件里 6 条齐全，handleError 0 次

    被丢掉的 record 之所以完全不可观测：子进程 stderr 是 `DEVNULL`
    （`brain.py:_spawn_brain_subprocess`），`handleError` 的回溯直接进黑洞；且此后文件仍超限，
    之后**每一条**日志都会重试轮转、每条都可能被吞 ⇒ 并发窗口内近似"日志全灭"。

    并发是实测事实而非假设：`_BRAIN_SUBPROCS` 只按 tier 去重，mid 与 long 两个子进程会同时跑
    （2026-09-20 01:18–01:47 的 30 分钟里重叠 7 次，其中 mid 批次最长持续 216s）。

    本实现的差别：把**唯一会失败的步骤（base 改名）放在最前**，失败就整体放弃并继续追加
    （备份链一个字节都不动，见 `test_deferred_rollover_keeps_record_and_backup_chain`），
    同时按 5 分钟一次写入 `# [rotate-deferred]` 可观测标记；成功后再搬备份链，
    且用 `os.replace`(Windows 覆盖式改名) 而不是「先 remove 再 rename」。
    """

    _defer_mark_ts = 0.0

    def _reopen(self) -> None:
        if not self.delay:
            self.stream = self._open()

    def _mark_deferred(self, err: OSError) -> None:
        now = time.time()
        if now - self._defer_mark_ts < _ROTATE_DEFER_MARK_SEC:
            return
        self._defer_mark_ts = now
        try:
            if self.stream is None:
                self.stream = self._open()
            self.stream.write(
                "# [rotate-deferred] %s 轮转被并发写入阻塞（%s: %s）；本条之后继续追加，"
                "待 peer 子进程退出后自动补轮转\n"
                % (time.strftime("%Y-%m-%d %H:%M:%S"), err.__class__.__name__, err)
            )
            self.stream.flush()
        except Exception:  # noqa: BLE001
            pass

    def doRollover(self) -> None:  # noqa: N802 —— 覆写标准库命名
        if self.stream:
            self.stream.close()
            self.stream = None
        base = self.baseFilename
        staging = base + ".rotating"
        try:
            # 唯一会因「peer 子进程握着 base」而失败的一步：先做，失败即整体放弃
            os.replace(base, staging)
        except OSError as e:
            # 不 raise：raise 会被 BaseRotatingHandler.emit 吞成 handleError ⇒ 丢日志
            self._reopen()
            self._mark_deferred(e)
            return
        # 立刻重建 base（先于搬备份链）：把"base 短暂不存在"的窗口压到一次 open()。
        # 读方 `agent_wall._read_incremental` 已处理 inode 变化/文件缺失，这里只是再收窄。
        self._reopen()
        try:
            for i in range(self.backupCount - 1, 0, -1):
                sfn = "%s.%d" % (base, i)
                if os.path.exists(sfn):
                    os.replace(sfn, "%s.%d" % (base, i + 1))
            os.replace(staging, base + ".1")
        except OSError:
            # base 已搬走；最坏情况是 .1 缺一代，staging 会被下一轮覆盖，不阻塞追加
            pass


def _setup_logging() -> None:
    root = logging.getLogger()
    # 幂等：已有 FileHandler/RotatingFileHandler（子类）不重复挂
    if any(isinstance(h, logging.FileHandler) for h in root.handlers):
        return
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] [tr=-] %(name)s:%(lineno)d - %(message)s"
    )
    try:
        log_dir = os.path.join(_repo_root(), "logs")
        os.makedirs(log_dir, exist_ok=True)
        fh = _MultiProcSafeRotatingFileHandler(
            os.path.join(log_dir, "brain_subprocess.log"),
            maxBytes=max(1, _env_int("BRAIN_SUBPROCESS_LOG_MAX_MB", _LOG_MAX_MB_DEFAULT)) * 1024 * 1024,
            backupCount=max(0, _env_int("BRAIN_SUBPROCESS_LOG_BACKUPS", _LOG_BACKUPS_DEFAULT)),
            encoding="utf-8",
        )
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
