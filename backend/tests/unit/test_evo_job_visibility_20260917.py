"""[轮50 2026-09-17] 因子进化任务的 job_registry 可见性接线契约测试。

问题（用户 2026-09-17 指出）：
> 要是明早自动跑有问题，还是发现不了，那他妈的这个项目怎么办

实测根因：进化任务跑在**分离子进程**（`evo_subprocess`），而该文件全库
`job_run` / `record` **零命中** ⇒ 无论成功/失败/超时，都不写 `job_registry` ⇒
在 `ops_jobs`（`stale_warn`/`stale_critical`）、`_alert_failure`、`watchdog_job`
里**全部隐形**。失败只落进 `logs/evo_subprocess.log` 的一行 INFO。

而基础设施其实是完备的 —— `backend/services/ops/job_registry.py` 已有
`register_job()` / `job_run()`（异常记 error + `_alert_failure` + 抛出）/
`_is_stale()` / `watchdog_job()`。缺的只是**接线**。

本测试锁定接线不被回退。
"""
from __future__ import annotations

import inspect
import os

import pytest


def _src(mod) -> str:
    return inspect.getsource(mod)


# ── 1. evo_subprocess 必须接线 ─────────────────────────────────────────────

def test_evo_subprocess_wraps_run_in_job_run():
    from backend.services.evolution import evo_subprocess as M
    src = _src(M)
    assert "from backend.services.ops.job_registry import job_run, register_job" in src, \
        "evo_subprocess 未接线 job_registry"
    assert "job_run(_job_name)" in src, "未用 job_run 包裹进化运行"
    assert "register_job(" in src, "未登记任务元数据（陈旧判定需要 expected_interval_sec）"


def test_evo_subprocess_records_error_not_just_exit_code():
    """report.get('error') 必须转成异常，否则 job_registry 会把它记成 ok。"""
    from backend.services.evolution import evo_subprocess as M
    src = _src(M)
    assert 'report.get("error")' in src
    assert "raise RuntimeError" in src, "error 未转异常 → job_registry 会误记 ok"


def test_expected_interval_is_24h():
    from backend.services.evolution import evo_subprocess as M
    assert "expected_interval_sec=24 * 3600" in _src(M)


# ── 2. 任务名与调度周期一致 ────────────────────────────────────────────────

def test_job_name_matches_period():
    from backend.services.evolution import evo_subprocess as M
    assert 'f"factor_evolution_{args.period}"' in _src(M)


def test_main_py_registers_all_scheduled_periods():
    """启动时就登记，否则"从未运行"在面板上不可见。

    main.py 用循环登记（`f"factor_evolution_{_p}"`），故按"周期元组 + 模板"断言，
    不按字面任务名断言。
    """
    import backend.main as M
    src = _src(M)
    assert 'f"factor_evolution_{_p}"' in src, "main.py 未按周期循环登记 job_registry"
    for p in ("4h", "15m", "1h"):
        assert f'("{p}"' in src, f"main.py 的登记循环未包含 {p}"
    assert 'owner="factor_evolution"' in src
    assert "expected_interval_sec=24 * 3600" in src


# ── 3. 开关默认与回滚 ─────────────────────────────────────────────────────

def test_switch_default_true_and_rollback_path_exists():
    from backend.config import settings as S
    assert hasattr(S, "FACTOR_EVO_JOB_REGISTRY")
    assert isinstance(S.FACTOR_EVO_JOB_REGISTRY, bool)
    src = inspect.getsource(S)
    assert 'FACTOR_EVO_JOB_REGISTRY", "true"' in src, "代码默认值应为 true"


def test_rollback_disables_wiring(monkeypatch):
    """置 false 时不应登记/包裹（回退纯日志行为），且不得抛异常。"""
    from backend.services.evolution import evo_subprocess as M
    for off in ("false", "0", "off", "no"):
        monkeypatch.setenv("FACTOR_EVO_JOB_REGISTRY", off)
        assert str(os.getenv("FACTOR_EVO_JOB_REGISTRY")) == off
    # 源码层保证：开关判断存在，且失败时 fail-open（不影响进化本身）
    src = _src(M)
    assert 'os.getenv("FACTOR_EVO_JOB_REGISTRY", "true")' in src
    assert "job_registry 接线失败（不影响进化）" in src, "缺少 fail-open 保护"


# ── 4. job_registry 本身具备所需能力（不是我在假设）────────────────────────

def test_job_registry_provides_staleness_and_alerting():
    from backend.services.ops import job_registry as JR
    for fn in ("register_job", "job_run", "list_jobs", "job_runs", "_is_stale",
               "_alert_failure", "watchdog_job"):
        assert hasattr(JR, fn), f"job_registry 缺少 {fn}"
