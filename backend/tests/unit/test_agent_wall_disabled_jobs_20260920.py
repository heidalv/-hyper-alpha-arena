"""[2026-09-20] 画布审计：**决策≠故障** —— `job_registry.enabled=false` 的任务不许再报成 high。

背景（实测）：`agent_wall.audit()` 原先把 `list_jobs()` 的 `stale` 无条件转成 high/medium，
而 `list_jobs()` 对**已停用**的行照样算 stale ⇒ 17 条 findings 里 8 条是"我们自己按指令关掉的任务"
（`/api/ops/jobs` summary 里 `disabled: 8` 已经把它们单列）。后果：真故障被淹。

本文件锁三件事：
- 停用任务（enabled=false）⇒ 汇总成一条 info（带停用来源 + 回滚），**不再**出现 `job:<name>` 故障；
- 未停用但 stale 的任务 ⇒ **必须**仍然报故障（防止过度抑制把真问题藏掉）；
- 停用但没登记来源 ⇒ 报 medium（不许"关了但没人知道为什么"），
  停用节点没有 latest 产物 ⇒ 不报假故障。
"""
from __future__ import annotations

import pytest

from backend.services import agent_wall


def _jobs(**rows):
    return {name: dict(row) for name, row in rows.items()}


@pytest.fixture()
def patch_jobs(monkeypatch):
    def _apply(jobs):
        monkeypatch.setattr(agent_wall, "_jobs_or_empty", lambda: (jobs, None))
    return _apply


def _by_id(out):
    return {f["id"]: f for f in out["findings"]}


# ---------------------------------------------------------------- 停用 ⇒ info

def test_disabled_stale_job_is_info_not_failure(patch_jobs):
    patch_jobs(_jobs(**{"capital_allocate": {
        "stale": "critical", "enabled": False, "cadence": "cron 05:20",
        "run_count": 1, "last_start": "2026-09-05T05:20:00", "expected_interval_sec": 86400,
    }}))
    out = agent_wall.audit()
    ids = _by_id(out)
    assert "job:capital_allocate" not in ids, "已停用任务不许再报成现场故障"
    assert "jobs_stopped_by_decision" in ids
    f = ids["jobs_stopped_by_decision"]
    assert f["severity"] == "info"
    assert "capital_allocate" in f["evidence"]
    assert "ALLOCATOR_APPLY" in f["evidence"], "必须带上可核验的停用来源"
    assert "/api/ops/jobs/<name>/enable" in f["evidence"], "必须带恢复动作"
    assert out["counts"].get("high", 0) == len([x for x in out["findings"] if x["severity"] == "high"])


def test_enabled_stale_job_still_reported_as_failure(patch_jobs):
    """反向断言：抑制只能针对 enabled=false，真故障必须留 high（否则就是掩盖）。"""
    patch_jobs(_jobs(
        **{"capital_allocate": {"stale": "critical", "enabled": False, "run_count": 1}},
        **{"some_live_job": {"stale": "critical", "enabled": True, "cadence": "interval 60s",
                             "run_count": 9, "last_start": "2026-09-19T00:00:00"}},
    ))
    ids = _by_id(agent_wall.audit())
    assert ids["job:some_live_job"]["severity"] == "high"
    assert "job:capital_allocate" not in ids


def test_warn_severity_enabled_job_is_medium(patch_jobs):
    patch_jobs(_jobs(**{"live_warn": {"stale": "warn", "enabled": True, "run_count": 3}}))
    ids = _by_id(agent_wall.audit())
    assert ids["job:live_warn"]["severity"] == "medium"


def test_disabled_without_registered_source_is_medium(patch_jobs):
    patch_jobs(_jobs(**{"mystery_job": {"stale": "never_ran", "enabled": False, "run_count": 0}}))
    ids = _by_id(agent_wall.audit())
    assert "job:mystery_job" not in ids
    f = ids["job_disabled_unregistered:mystery_job"]
    assert f["severity"] == "medium", "关了但没登记来源 ⇒ 必须提醒，不许静默"
    assert "_STOPPED_JOBS" in f["evidence"]


def test_every_registered_stop_has_verifiable_source():
    """`_STOPPED_JOBS` 的每一项都必须写清可核验来源（env 键 / 日期 / 代码位置）。"""
    assert agent_wall._STOPPED_JOBS, "停用登记表不许为空"
    for name, why in agent_wall._STOPPED_JOBS.items():
        assert isinstance(why, str) and len(why) >= 15, f"{name} 的停用来源太短：{why!r}"
        assert any(k in why for k in (".env", "2026-", "NODES", "experiments", "/jobs.py")), \
            f"{name} 的停用来源里没有可核验锚点：{why!r}"


# ---------------------------------------------------------------- 停用节点不报假故障

def test_disabled_node_without_artifact_is_not_a_failure(monkeypatch):
    monkeypatch.setattr(agent_wall, "NODES", [
        {"id": "off_node", "label": "已停用节点", "group": "G2", "size": "S", "status_hint": "disabled",
         "source": {"kind": "json_latest", "agent": "definitely_missing_agent"}},
        {"id": "on_node", "label": "在跑节点", "group": "G2", "size": "S",
         "source": {"kind": "json_latest", "agent": "definitely_missing_agent"}},
    ])
    monkeypatch.setattr(agent_wall, "_jobs_or_empty", lambda: ({}, None))
    ids = _by_id(agent_wall.audit())
    assert "artifact:on_node" in ids, "在跑节点缺产物必须照报"
    assert ids["artifact:on_node"]["severity"] == "high"
    assert "artifact:off_node" not in ids, "已停用节点没有产物是预期结果，不是故障"


# ---------------------------------------------------------------- 现场实况（真实 registry）

def test_live_audit_has_no_disabled_job_findings():
    """现场自检：当前 8 个停用任务一个都不许出现在 job:<name> 故障里。"""
    jobs, warn = agent_wall._jobs_or_empty()
    if warn or not jobs:
        pytest.skip("job_registry 不可用：%s" % warn)
    ids = _by_id(agent_wall.audit())
    bad = [n for n, j in jobs.items() if j.get("enabled") is False and f"job:{n}" in ids]
    assert not bad, f"停用任务仍被当成故障：{bad}"
