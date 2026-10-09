# -*- coding: utf-8 -*-
"""[F330 2026-09-21] `aster_ws_ingest` 单实例锁回归测试。

# 事故（实测）

审计（`scripts/h199_data_audit.py`）发现**两个** `aster_ws_ingest` 同时运行：

    pid 27452  .venv\\Scripts\\python.exe         created 10:31:41
    pid 28648  .runtime\\Python312\\python.exe    created 10:31:41

各自建立**独立的 WS 连接、订阅同样的流**。实测近 3 分钟：

    `asterdex_book_ticker`  总行 107,146 / 唯一 (symbol,event_ts_ms) 87,389
    ⇒ **重复 1.226 倍**，单车同一毫秒最多 **23 行**（价格相同或差最后一档）

# 为什么这有害（不只是浪费）

选币器 `h125_selector_v3.pool()` 的候选池评分是
`count(*)` + 点差分位数 ⇒ 行数放大 1.23 倍、分位数被扭曲
⇒ **排序依据失真**，而排序决定车道跑哪些币。
（引擎自己的两张表实测干净，见 `h199` 的 exchange 维度修正。）

# 本测试固定什么

1. 已有**本脚本**实例存活 ⇒ 抢锁失败（不双采）
2. 陈旧锁（PID 不存在）⇒ 抢锁成功
3. `--lock-check` 能如实报告持有者（供巡检用）
4. **加锁在开 socket 之前**（源码顺序断言）—— 否则锁形同虚设

⚠️ 沿用 F294 的教训：加载被测模块时必须隔离 `sys.stdout`/`sys.stderr` 的重绑，
否则 pytest 捕获流会被关掉（`ValueError: I/O operation on closed file`）。
"""
from __future__ import annotations

import importlib.util
import io
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]      # D:\001Alpha\Hyper-Alpha-Arena
# ⚠️ `research_l1` 与 `Hyper-Alpha-Arena` 是**平级**目录，不是在后者内部。
# 首版写成 ROOT/'research_l1'/... 直接 FileNotFoundError。
ALPHA = ROOT.parent                              # D:\001Alpha
INGEST = ALPHA / "research_l1" / "services" / "aster_ws_ingest.py"


def _load(tmp_lock_dir: Path):
    """加载模块并把锁重定向到 tmp（**绝不碰生产锁文件**）。"""
    os.environ["L1_INGEST_LOCK_DIR"] = str(tmp_lock_dir)
    spec = importlib.util.spec_from_file_location("aster_ws_ingest_under_test", INGEST)
    m = importlib.util.module_from_spec(spec)
    saved = sys.stdout, sys.stderr
    sink = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    try:
        sys.stdout = sys.stderr = sink
        spec.loader.exec_module(m)
    finally:
        sys.stdout, sys.stderr = saved
    return m


@pytest.fixture()
def mod(tmp_path):
    m = _load(tmp_path)
    m._LOCK = tmp_path / "aster_ws_ingest.lock"
    return m


@pytest.mark.unit
def test_ingest_script_exists():
    assert INGEST.exists(), f"找不到 {INGEST}"


@pytest.mark.unit
def test_acquire_when_no_lock(mod):
    """无锁 ⇒ 抢到，且锁里写着本进程 PID。"""
    assert mod._acquire_lock() is True
    assert mod._LOCK.read_text(encoding="ascii").strip() == str(os.getpid())
    assert mod._holds_lock() is True


@pytest.mark.unit
def test_refuse_when_our_ingest_alive(mod, monkeypatch):
    """**核心**：已有本脚本实例存活 ⇒ 必须拒绝（否则双采双写）。"""
    other = _spawn_sleeping_ingest_like()
    try:
        mod._LOCK.write_text(str(other.pid), encoding="ascii")
        assert mod._acquire_lock() is False, "另一个 ingest 活着时不得抢锁"
        # 锁不能被自己覆盖
        assert mod._LOCK.read_text(encoding="ascii").strip() == str(other.pid)
    finally:
        other.kill()
        other.wait(timeout=10)


@pytest.mark.unit
def test_stale_lock_can_be_taken(mod):
    """陈旧锁（PID 不存在）⇒ 应当能抢到。"""
    mod._LOCK.write_text("999999", encoding="ascii")
    assert mod._acquire_lock() is True
    assert mod._LOCK.read_text(encoding="ascii").strip() == str(os.getpid())


@pytest.mark.unit
def test_holds_lock_false_when_other_holder(mod):
    mod._LOCK.write_text("999999", encoding="ascii")
    assert mod._holds_lock() is False


@pytest.mark.unit
def test_lock_check_mode_reports(tmp_path):
    """`--lock-check` 必须能跑并如实报告（不开采集）。"""
    r = subprocess.run(
        [sys.executable, str(INGEST), "--lock-check"],
        capture_output=True, text=True, timeout=90,
        env={**os.environ, "L1_INGEST_LOCK_DIR": str(tmp_path)},
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, f"exit={r.returncode} out={out[:300]}"
    assert "lock-check:" in out, f"输出里没有 lock-check：{out[:300]}"
    assert "i_hold_lock=" in out


@pytest.mark.unit
def test_lock_is_taken_before_any_socket():
    """**源码顺序断言**：抢锁必须在建立 WS 连接之前。

    否则两个实例会先各自连上再互相发现 ⇒ 已经双写了。
    """
    src = INGEST.read_text(encoding="utf-8")
    i_lock = src.find("if not _acquire_lock():")
    i_run = src.find("asyncio.run(amain(args))")
    assert i_lock > 0, "找不到抢锁调用点"
    assert i_run > 0, "找不到 asyncio.run(amain(args))"
    assert i_lock < i_run, (
        "抢锁必须在 asyncio.run(amain(...)) **之前** —— "
        "先连上再发现重复已经造成双写了")


def _spawn_sleeping_ingest_like():
    """起一个**命令行含 aster_ws_ingest** 的睡眠进程，冒充"另一个实例"。

    用 `-c` 配合 `sys.argv` 伪造命令行是做不到的（cmdline 来自真实 argv），
    所以直接跑本脚本的 `--lock-check` 会立即退出 —— 改用
    `python -c "import time; time.sleep(60)" aster_ws_ingest.py`：
    `-c` 之后的参数会成为 `sys.argv[1:]`，进程命令行里就带着那个字符串，
    而进程本身只是在睡觉 ⇒ 稳定可判、不连任何 socket。
    """
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)",
         str(INGEST)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
