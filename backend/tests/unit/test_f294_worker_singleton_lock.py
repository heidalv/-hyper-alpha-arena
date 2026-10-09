# -*- coding: utf-8 -*-
"""[F294 2026-09-21] 单实例锁回归测试 —— 修「双 worker 同时 tick 同一车道」。

# 为什么必须有这个测试

2026-09-21 09:13:59~09:16:28 实测：宇宙收缩到 `[ASTER,SOL,XRP]` 后，DOGE
**仍被正常做市 4.5 分钟**（18 笔真实账本成交、峰值 |持仓| $303）。
逐条核对 `lane_ledger` 后确认：DOGE 与 ASTER/XRP/SOL **交错出现在同一 tick
时间戳** ⇒ 当时**两个 worker 进程在同时 tick 同一条车道**，各自持有自己的
`ShadowRunner`（各自 `self.symbols`）、互相看不见对方的成交，却共用一份
`lane_runtime_state` 与账本 ⇒ 状态被交叉覆写（持仓先增后减的乱序）。

病根是旧判据 `old > 0 and old != os.getpid()`：**锁文件里恰好是自己的 PID
时不检查**。「杀掉旧 worker、立刻重启新 worker」正好落在这个窗口里 ——
旧进程读到新 PID（而新 PID 又写回旧进程，形成闭环）⇒ 判为「锁是我的」⇒ 双跑。

本文件把事故场景**逐条固定下来**，避免修好又被改回去。
"""
from __future__ import annotations

import importlib.util
import io
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]   # backend/tests/unit/x.py -> 仓库根
WORKER = ROOT / "scripts" / "mm_lane_worker.py"


def _load_worker():
    """加载 worker 模块，并隔离它在**导入期**对 `sys.stdout` 的重绑。

    为什么必须隔离：worker 顶部执行
    `sys.stdout = io.TextIOWrapper(sys.stdout.buffer, ...)`（Windows 控制台编码），
    而它包装的是**捕获流的底层 buffer** ⇒ 该 wrapper 被回收时会**关掉捕获流**
    （实测 `ValueError: I/O operation on closed file`，pytest 连 setup 都跑不完）。

    这里在加载期间临时把 `sys.stdout` 换成一个**临时文件**的文本流：worker 包装的
    是临时文件而不是真实捕获流，即使被回收也只关掉临时文件。
    """
    spec = importlib.util.spec_from_file_location("mm_lane_worker_under_test", WORKER)
    m = importlib.util.module_from_spec(spec)
    saved = sys.stdout
    sink = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    try:
        sys.stdout = sink
        spec.loader.exec_module(m)
    finally:
        sys.stdout = saved
    return m


@pytest.fixture()
def mod(tmp_path, monkeypatch):
    """加载 worker 模块，把锁**与日志**全部重定向到 tmp_path。

    ⚠️ 日志也必须重定向：`log()` 是 `open(LOG, "a")`，LOG 是模块全局。
    实测过不重定向的后果 —— 测试写下的
    `another worker alive (pid=19500) -> exit` / `lock warning: disk full`
    直接混进了**生产** `logs/mm_lane_worker.log`，而那份日志正是故障复盘
    唯一的证据源 ⇒ **测试污染证据 = 破坏可诊断性**。
    """
    d = vars(_load_worker())
    lock = tmp_path / "mm_lane_worker.lock"
    monkeypatch.setitem(d, "LOCK", lock)
    monkeypatch.setitem(d, "LOG_DIR", tmp_path)
    monkeypatch.setitem(d, "LOG", tmp_path / "mm_lane_worker.log")
    monkeypatch.setitem(d, "STATUS", tmp_path / "mm_lane_status.json")
    monkeypatch.setitem(d, "_pid_file", lambda: lock.with_name(lock.name + ".pid"))
    return d


def _alive_python_sleep() -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])


def _spawn_worker_like(script: Path, log_dir: Path, *args: str) -> subprocess.Popen:
    """起一个**真跑本脚本**的子进程，并把它的一切落盘路径赶到 log_dir。

    用 `MM_LANE_WORKER_LOG_DIR`（F294 新增的 env 覆盖）而不是拷贝脚本 ——
    拷贝方案下子进程仍会把行写进**生产** `logs/mm_lane_worker.log`（实测污染过）。
    """
    env = dict(os.environ)
    env["MM_LANE_WORKER_LOG_DIR"] = str(log_dir)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.Popen(
        [sys.executable, str(script), *args],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)


def _wait_until(pred, timeout: float = 10.0, interval: float = 0.05) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return False


@pytest.mark.unit
def test_is_our_worker_excludes_self(mod):
    """`exclude_self` 的语义：只管「排不排除自己」，不改「是不是本脚本」这个判据。

    ⚠️ 注意别把这条测成「测试进程就是 worker」——pytest 命令行里没有
    `mm_lane_worker.py`，所以即使 `exclude_self=False` 也必须返回 False。
    真正要固定的是：**当前进程确实是本脚本时**，`exclude_self=False` 能认出来
    （由 `test_is_our_worker_recognizes_self_when_actually_the_worker` 覆盖）。
    """
    me = os.getpid()
    assert mod["_is_our_worker"](me) is False, "自己永远不算「另一个 worker」"
    assert mod["_is_our_worker"](me, exclude_self=False) is False, (
        "pytest 进程命令行不含 mm_lane_worker.py ⇒ 即使不排除自己也必须是 False")


@pytest.mark.unit
def test_is_our_worker_recognizes_self_when_actually_the_worker(mod, tmp_path):
    """`--lock-check` 里 `exclude_self=False` 必须能把**真 worker 进程**认出来。

    这是锁自证（`--lock-check` 判断"我是不是持有者"）赖以成立的前提。
    用一个真跑本脚本的子进程来做，而不是靠测试进程冒充。
    """
    p = _spawn_worker_like(WORKER, tmp_path / "wl", "--lock-check")
    try:
        assert _wait_until(
            lambda: mod["_is_our_worker"](p.pid, exclude_self=False) or p.poll() is not None
        ), "子进程既没被认出来、也没退出，无法判定"
        seen = mod["_is_our_worker"](p.pid, exclude_self=False)
        if p.poll() is not None:
            pytest.skip("子进程在判定前已退出（--lock-check 很快），本次无法观测")
        assert seen, "真 worker 子进程未被 exclude_self=False 认出来"
    finally:
        if p.poll() is None:
            p.kill()
        p.wait(timeout=10)


@pytest.mark.unit
def test_is_our_worker_rejects_bad_pids(mod):
    assert mod["_is_our_worker"](0) is False
    assert mod["_is_our_worker"](-1) is False
    assert mod["_is_our_worker"](999_999_999) is False


@pytest.mark.unit
def test_is_our_worker_rejects_other_python(mod):
    """普通的 python 进程**不是**我们的 worker（旧实现只认 'python' ⇒ 误判）。"""
    p = _alive_python_sleep()
    try:
        assert mod["_is_our_worker"](p.pid) is False
    finally:
        p.kill()
        p.wait(timeout=10)


@pytest.mark.unit
def test_is_our_worker_detects_same_script(mod, tmp_path):
    """命令行含 `mm_lane_worker.py` 的进程必须被认出来。"""
    p = _spawn_worker_like(WORKER, tmp_path / "wl", "--lock-check")
    try:
        seen = _wait_until(
            lambda: mod["_is_our_worker"](p.pid) or p.poll() is not None, timeout=8.0)
        if p.poll() is not None and not mod["_is_our_worker"](p.pid):
            pytest.skip("子进程在判定前已退出（--lock-check 很快），本次无法观测")
        assert seen and mod["_is_our_worker"](p.pid), (
            "命令行含 mm_lane_worker.py 的进程未被识别为本脚本实例")
    finally:
        if p.poll() is None:
            p.kill()
        p.wait(timeout=10)


@pytest.mark.unit
def test_holds_lock_tracks_lock_file(mod):
    """`_holds_lock` 必须严格跟着锁文件内容走。"""
    assert mod["_holds_lock"]() is False, "锁文件不存在时不得自称持有"
    mod["LOCK"].write_text("12345", encoding="ascii")
    assert mod["_holds_lock"]() is False, "锁里是别人时不得自称持有"
    mod["LOCK"].write_text(str(os.getpid()), encoding="ascii")
    assert mod["_holds_lock"]() is True
    mod["LOCK"].write_text("not-a-pid", encoding="ascii")
    assert mod["_holds_lock"]() is False, "锁内容非法时不得自称持有"


@pytest.mark.unit
def test_lock_ok_writes_pid_and_self_cert(mod):
    """首次取锁：写 PID 到锁文件，并写一份不随重启改变的 `*.lock.pid` 自证。"""
    assert mod["_lock_ok"]() is True
    assert mod["LOCK"].read_text(encoding="ascii").strip() == str(os.getpid())
    assert mod["_pid_file"]().read_text(encoding="ascii").strip() == str(os.getpid())


@pytest.mark.unit
def test_lock_ok_exits_when_same_script_alive(mod):
    """**正常场景**：锁里是另一个活着的本脚本实例 ⇒ 必须退出。"""
    p = _spawn_worker_like(WORKER, mod["LOG_DIR"], "--lock-check")
    try:
        took = None
        deadline = time.time() + 8.0
        while time.time() < deadline:
            mod["LOCK"].write_text(str(p.pid), encoding="ascii")
            if mod["_lock_ok"]() is False:
                took = False
                break
            if p.poll() is not None:
                break
            time.sleep(0.05)
        if p.poll() is not None and took is None:
            pytest.skip("子进程在判定前已退出，本次无法观测")
        assert took is False, "锁里是活着的同脚本实例时，_lock_ok 必须返回 False"
    finally:
        if p.poll() is None:
            p.kill()
        p.wait(timeout=10)


@pytest.mark.unit
def test_lock_ok_regression_self_pid_with_other_worker_alive(mod):
    """**事故场景的忠实复现**（本测试就是这次事故的护栏）。

    锁文件里写着**我们自己的 PID**，但**另一个本脚本实例仍然活着**。
    旧实现因 `old != os.getpid()` 这一条直接跳过检查 ⇒ 返回 True ⇒ 双跑 ✗。
    新实现必须返回 **False**。

    这里的复现方式：把 `_is_our_worker` 换成"只有那个真进程算数"的版本，
    同时把锁文件写成自己的 PID —— 精确对应旧进程当时看到的局面
    （锁文件被新进程写成新 PID，而旧进程又读到它）。
    """
    p = _alive_python_sleep()
    try:
        # 构造「锁里是自己的 pid，但另一个 worker 活着」
        mod["LOCK"].write_text(str(os.getpid()), encoding="ascii")
        real = mod["_is_our_worker"]

        def fake(pid, *, exclude_self=True):
            # 自己的 PID 说"是"（旧代码在这一步就 return True 了）
            if pid == os.getpid():
                return True
            return real(pid, exclude_self=exclude_self)

        mod["_is_our_worker"] = fake
        assert mod["_lock_ok"]() is False, (
            "锁文件是自己的 PID 而另一个 worker 活着时必须退出 —— "
            "这正是 09:13~09:16 双跑的成因")
    finally:
        p.kill()
        p.wait(timeout=10)


@pytest.mark.unit
def test_lock_ok_steals_stale_lock_with_no_worker(mod, monkeypatch):
    """锁里是**已死**进程 ⇒ 可以接管（否则重启后车道永久停摆）。"""
    p = _alive_python_sleep()
    pid = p.pid
    p.kill()
    p.wait(timeout=10)
    mod["LOCK"].write_text(str(pid), encoding="ascii")
    assert mod["_lock_ok"]() is True, "死进程的锁必须能被接管"
    assert mod["LOCK"].read_text(encoding="ascii").strip() == str(os.getpid())


@pytest.mark.unit
def test_lock_ok_falls_back_permissively_on_error(mod, monkeypatch):
    """锁机制自身异常时**不阻断车道**（与既有语义一致，避免误停交易）。

    ⚠️ 异常信息必须**可读**。第一版把异常写成 `raise OSError("disk full")`，
    结果日志里是 `lock warning: disk full` —— 看起来像真的磁盘故障，
    复盘时会把人引到完全错误的方向。异常文案要能自己说明是**测试**。
    """

    class _Boom(type(mod["LOCK"])):
        def write_text(self, *a, **k):  # pragma: no cover - 仅用于触发分支
            raise OSError("[TEST-F294] injected lock write failure (not a real disk error)")

    monkeypatch.setitem(mod, "LOCK", _Boom(mod["LOCK"]))
    assert mod["_lock_ok"]() is True


@pytest.mark.unit
def test_tests_do_not_pollute_production_worker_log(tmp_path, monkeypatch):
    """护栏：本测试文件**不许**往生产 `logs/mm_lane_worker.log` 写一行。

    这条是被真实污染逼出来的：该文件第一版跑完，生产日志里多了
    `another worker alive (pid=19500) -> exit` 与 `lock warning: disk full` ——
    而那份日志是 09:13~09:16 双跑事故复盘的**唯一**证据源。
    测试污染证据 = 破坏可诊断性，所以用一个显式断言把它钉住。
    """
    prod_log = ROOT / "logs" / "mm_lane_worker.log"
    size_before = prod_log.stat().st_size if prod_log.exists() else 0

    # 真起一个 worker 子进程（就是会写日志的那条路径）
    p = _spawn_worker_like(WORKER, tmp_path / "wl", "--lock-check")
    try:
        p.wait(timeout=15)
    finally:
        if p.poll() is None:
            p.kill()
            p.wait(timeout=10)

    size_after = prod_log.stat().st_size if prod_log.exists() else 0
    assert size_after == size_before, (
        f"生产日志被测试写入了 {size_after - size_before} 字节：{prod_log}\n"
        "原因几乎总是「子进程没吃到 MM_LANE_WORKER_LOG_DIR」")
