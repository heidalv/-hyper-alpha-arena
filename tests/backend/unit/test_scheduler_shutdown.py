"""
批次一 1.3 回归测试：scheduler shutdown 竞态修复

根因：scheduler.shutdown(wait=False) 时 executor 立即关闭，但调度线程仍在
_process_jobs 里 submit_job，抛 ``cannot schedule new futures after shutdown``
（日志 140 次）；add_account_snapshot_task 缺少 shutdown 兜底。

修复：
1. shutdown(wait=True) 等待 in-flight job 落地。
2. add_account_snapshot_task 补 RuntimeError shutdown 兜底（与 add_interval_task 对齐）。

本测试不启动真实 APScheduler（避免线程时序不稳定），用 mock 验证：
1. shutdown 把 wait=True 传给底层 scheduler.shutdown。
2. add_account_snapshot_task 在 shutdown 状态下不抛、静默返回。
3. add_account_snapshot_task 的 add_job 抛 RuntimeError(shutdown) 时被吞掉。
"""
import pytest
from unittest.mock import MagicMock, patch

from backend.services.scheduler import TaskScheduler


pytestmark = pytest.mark.unit


def test_shutdown_passes_wait_true():
    """shutdown 应以 wait=True 调用底层，让 in-flight job 落地。"""
    sched = TaskScheduler()
    fake_aps = MagicMock()
    fake_aps.running = True
    fake_aps.get_jobs.return_value = []
    sched.scheduler = fake_aps
    sched._started = True

    sched.shutdown()

    fake_aps.pause.assert_called_once()
    fake_aps.shutdown.assert_called_once_with(wait=True)


def test_shutdown_falls_back_to_wait_false_on_block():
    """wait=True 抛异常时，回退到 wait=False，不掩盖上层关闭流程。"""
    sched = TaskScheduler()
    fake_aps = MagicMock()
    fake_aps.running = True
    fake_aps.get_jobs.return_value = []
    # 第一次 wait=True 抛错（模拟阻塞超时），第二次 wait=False 成功
    fake_aps.shutdown.side_effect = [Exception("blocked"), None]
    sched.scheduler = fake_aps
    sched._started = True

    sched.shutdown()  # 不应抛

    assert fake_aps.shutdown.call_count == 2
    assert fake_aps.shutdown.call_args_list[0].kwargs == {"wait": True}
    assert fake_aps.shutdown.call_args_list[1].kwargs == {"wait": False}


def test_add_account_snapshot_task_swallows_shutdown_runtime_error():
    """add_job 抛 RuntimeError(shutdown) 时静默返回，不向上传播（与 add_interval_task 对齐）。"""
    sched = TaskScheduler()
    fake_aps = MagicMock()
    fake_aps.running = True
    fake_aps.get_job.return_value = None  # 不存在，进入 add_job
    fake_aps.add_job.side_effect = RuntimeError("cannot schedule new futures after shutdown")
    sched.scheduler = fake_aps
    sched._started = True

    # 不应抛 RuntimeError
    sched.add_account_snapshot_task(account_id=1, interval_seconds=10)

    fake_aps.add_job.assert_called_once()


def test_add_account_snapshot_task_reraises_non_shutdown_error():
    """非 shutdown 类的 RuntimeError 仍应向上传播（避免吞掉真实配置错误）。"""
    sched = TaskScheduler()
    fake_aps = MagicMock()
    fake_aps.running = True
    fake_aps.get_job.return_value = None
    fake_aps.add_job.side_effect = ValueError("bad trigger config")
    sched.scheduler = fake_aps
    sched._started = True

    with pytest.raises(ValueError):
        sched.add_account_snapshot_task(account_id=1, interval_seconds=10)


def test_add_interval_task_swallows_shutdown_runtime_error():
    """add_interval_task 已有的兜底行为保持不变（回归保护）。"""
    sched = TaskScheduler()
    fake_aps = MagicMock()
    fake_aps.running = True
    fake_aps.add_job.side_effect = RuntimeError("Scheduler is already shut down")
    sched.scheduler = fake_aps
    sched._started = True

    def _task():
        pass

    # 不应抛
    sched.add_interval_task(_task, interval_seconds=30, task_id="t1")
    fake_aps.add_job.assert_called_once()
