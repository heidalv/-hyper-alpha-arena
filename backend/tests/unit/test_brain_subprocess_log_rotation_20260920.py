"""[2026-09-20] brain_subprocess 日志轮转回归（`logs/brain_subprocess.log` 150.3 MB 无轮转）。

改动的那一层 = `_MultiProcSafeRotatingFileHandler`，所以断言必须打在这一层，而不是"配置里有 maxBytes"：

- 挂上去的必须是**会轮转**的 handler，且 maxBytes/backupCount 与实测口径一致（20MB × 5 备）；
- 幂等：重复 `_setup_logging()` 不重复挂（子进程里可能被 import 两次）；
- 轮转真的发生：`.1`/`.2` 出现、代次顺序正确、代次数量不超 backupCount；
- **并发阻塞时不许丢日志**：base 改名失败（Windows 上 peer 子进程握着句柄就是这个错）时，
  触发轮转的那条 record 仍必须落盘，且留 `# [rotate-deferred]` 标记；
  备份链一个字节都不许动（标准库 `RotatingFileHandler` 恰好在这里丢日志 + 白搬备份）；
- 标记 5 分钟限频，不许刷屏。
"""
from __future__ import annotations

import logging
import os

import pytest

from backend.services.mlto import brain_subprocess as B


def _mk(tmp_path, **kw):
    """建一个只指向 tmp_path 的 handler + 独立 logger（不碰 root，不碰真实 logs 目录）。"""
    kw.setdefault("maxBytes", 120)
    kw.setdefault("backupCount", 1)
    h = B._MultiProcSafeRotatingFileHandler(
        str(tmp_path / "brain_subprocess.log"), encoding="utf-8", **kw
    )
    h.setFormatter(logging.Formatter("%(message)s"))
    lg = logging.getLogger("brain_subprocess_rotation_test_%d" % id(h))
    lg.handlers = [h]
    lg.setLevel(logging.INFO)
    lg.propagate = False
    return h, lg


def _live(tmp_path) -> str:
    return (tmp_path / "brain_subprocess.log").read_text(encoding="utf-8")


# ---------------------------------------------------------------- 接线层

def test_setup_logging_attaches_rotating_handler(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "_repo_root", lambda: str(tmp_path))
    monkeypatch.delenv("BRAIN_SUBPROCESS_LOG_MAX_MB", raising=False)
    monkeypatch.delenv("BRAIN_SUBPROCESS_LOG_BACKUPS", raising=False)
    root = logging.getLogger()
    saved = list(root.handlers)
    try:
        root.handlers = []          # 隔离：本进程 root 上已有 backend 的 handler
        B._setup_logging()
        fhs = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
        assert len(fhs) == 1
        fh = fhs[0]
        assert isinstance(fh, B._MultiProcSafeRotatingFileHandler), "必须是会轮转的 handler"
        assert fh.maxBytes == B._LOG_MAX_MB_DEFAULT * 1024 * 1024
        assert fh.backupCount == B._LOG_BACKUPS_DEFAULT
        assert fh.baseFilename == os.path.join(str(tmp_path), "logs", "brain_subprocess.log")
        assert (tmp_path / "logs").is_dir()
        # 幂等
        B._setup_logging()
        assert len([h for h in root.handlers if isinstance(h, logging.FileHandler)]) == 1
    finally:
        for h in list(root.handlers):
            if isinstance(h, logging.FileHandler):
                h.close()
        root.handlers = saved


def test_env_override_and_bad_value(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "_repo_root", lambda: str(tmp_path))
    monkeypatch.setenv("BRAIN_SUBPROCESS_LOG_MAX_MB", "3")
    monkeypatch.setenv("BRAIN_SUBPROCESS_LOG_BACKUPS", "1")
    root = logging.getLogger()
    saved = list(root.handlers)
    try:
        root.handlers = []
        B._setup_logging()
        fh = [h for h in root.handlers if isinstance(h, logging.FileHandler)][0]
        assert fh.maxBytes == 3 * 1024 * 1024
        assert fh.backupCount == 1
    finally:
        for h in list(root.handlers):
            if isinstance(h, logging.FileHandler):
                h.close()
        root.handlers = saved
    # 坏值不许把日志挂掉（回退默认）
    assert B._env_int("BRAIN_SUBPROCESS_LOG_MAX_MB", 20) == 3
    monkeypatch.setenv("BRAIN_SUBPROCESS_LOG_MAX_MB", "abc")
    assert B._env_int("BRAIN_SUBPROCESS_LOG_MAX_MB", 20) == 20
    monkeypatch.delenv("BRAIN_SUBPROCESS_LOG_MAX_MB", raising=False)
    assert B._env_int("BRAIN_SUBPROCESS_LOG_MAX_MB", 20) == 20


# ---------------------------------------------------------------- 轮转真的发生

def test_rollover_shifts_chain_in_order(tmp_path):
    h, lg = _mk(tmp_path, maxBytes=100, backupCount=2)
    try:
        for i in range(6):
            lg.info("REC-%d-%s" % (i, "x" * 200))   # 单条 > maxBytes ⇒ 每条都触发轮转
        h.flush()
    finally:
        h.close()
    base = (tmp_path / "brain_subprocess.log").read_text(encoding="utf-8")
    b1 = (tmp_path / "brain_subprocess.log.1").read_text(encoding="utf-8")
    b2 = (tmp_path / "brain_subprocess.log.2").read_text(encoding="utf-8")
    assert "REC-5" in base and "REC-4" in b1 and "REC-3" in b2, (base, b1, b2)
    assert not (tmp_path / "brain_subprocess.log.3").exists(), "代次不许超过 backupCount"
    assert (tmp_path / "brain_subprocess.log").stat().st_size < 320, "活文件不许无限增长"


def test_no_defer_marker_when_rollover_succeeds(tmp_path):
    h, lg = _mk(tmp_path, maxBytes=100, backupCount=1)
    try:
        for i in range(4):
            lg.info("OK-%d-%s" % (i, "x" * 200))
        h.flush()
    finally:
        h.close()
    assert "# [rotate-deferred]" not in _live(tmp_path)


# ---------------------------------------------------------------- 并发阻塞路径

def test_deferred_rollover_keeps_record_and_backup_chain(tmp_path, monkeypatch):
    h, lg = _mk(tmp_path, maxBytes=120, backupCount=1)
    try:
        for i in range(4):                       # 预热：制造一代真实备份
            lg.info("WARM-%d-%s" % (i, "y" * 200))
        h.flush()
        warm1 = tmp_path / "brain_subprocess.log.1"
        assert warm1.exists(), "前置条件：预热阶段应产生 `.1`"
        warm1_bytes = warm1.read_bytes()
        assert warm1_bytes, "`.1` 不许是空文件"

        h._defer_mark_ts = 0.0
        real_replace = os.replace
        base_abs = os.path.abspath(h.baseFilename)

        def _fake_replace(src, dst):
            # 精确复现 Windows 现象：base 仍被 peer 子进程握着 ⇒ 改名失败
            if os.path.abspath(src) == base_abs:
                raise PermissionError(32, "The process cannot access the file")
            return real_replace(src, dst)

        monkeypatch.setattr(os, "replace", _fake_replace)
        lg.info("BLOCKED-RECORD-" + "z" * 200)
        h.flush()
        live = _live(tmp_path)
        # 1) 标准库在这里会整条丢日志；本实现必须落盘
        assert "BLOCKED-RECORD-" in live
        # 2) 阻塞必须留痕（含异常类型，便于区分"并发阻塞"与"权限/磁盘"问题）
        assert "# [rotate-deferred]" in live
        assert "PermissionError" in live
        # 3) 备份链一个字节都不许动
        assert warm1.read_bytes() == warm1_bytes
        assert not (tmp_path / "brain_subprocess.log.2").exists()
        assert not (tmp_path / "brain_subprocess.log.rotating").exists()
        assert live.startswith("WARM-3"), "阻塞后应是追加，不许重写已有内容"
        assert live.count("WARM-3") == 1

        # 4) 限频：紧接着再阻塞一次也不多写标记
        lg.info("BLOCKED-AGAIN-" + "z" * 200)
        h.flush()
        live2 = _live(tmp_path)
        assert live2.count("# [rotate-deferred]") == 1
        assert "BLOCKED-AGAIN-" in live2
    finally:
        monkeypatch.undo()
        h.close()


def test_rollover_recovers_after_peer_exits(tmp_path, monkeypatch):
    """peer 退出后（改名不再失败）必须自动补轮转，不许永久停在"超大文件"状态。"""
    h, lg = _mk(tmp_path, maxBytes=120, backupCount=1)
    try:
        lg.info("BEFORE-" + "b" * 200)
        h.flush()
        h._defer_mark_ts = 0.0
        real_replace = os.replace
        base_abs = os.path.abspath(h.baseFilename)
        blocked = {"on": True}

        def _fake_replace(src, dst):
            if blocked["on"] and os.path.abspath(src) == base_abs:
                raise PermissionError(32, "The process cannot access the file")
            return real_replace(src, dst)

        monkeypatch.setattr(os, "replace", _fake_replace)
        lg.info("DURING-BLOCK-" + "d" * 200)
        h.flush()
        assert "DURING-BLOCK-" in _live(tmp_path)
        blocked["on"] = False                    # peer 退出
        lg.info("AFTER-PEER-EXIT-" + "a" * 200)
        h.flush()
        base = tmp_path / "brain_subprocess.log"
        b1 = tmp_path / "brain_subprocess.log.1"
        assert b1.exists() and b1.stat().st_size > 0
        assert base.stat().st_size < 120 + 220, "补轮转后活文件必须回落"
        assert "AFTER-PEER-EXIT-" in base.read_text(encoding="utf-8")
    finally:
        monkeypatch.undo()
        h.close()
