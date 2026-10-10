# -*- coding: utf-8 -*-
"""[2026-10-10 学习进化前端升级] 学习循环 job 名单的"单一来源"契约。

现场缺口（两个，都是同一类错误：同一份事实存了两个副本）：

1. `api/learning_loop_routes._VALID_JOBS` 硬编码 4 个名字，而服务层支持 7 个
   ⇒ 前端/运维手册里承诺的 `factor_decay` / `live_outcome_backfill` / `hft_brain`
   手动触发一律 **400**。
2. `LearningLoopService._last_tick_at` 手工列了 8 个 job，漏了
   `JOB_FUSION_MAINTENANCE` ⇒ 它在 `/loop/status` 与健康面板里**根本不会出现**
   （不是 dead，是"看不见"）—— 静默环最坏的形态。

本测试把两个名单都钉成"派生自唯一来源"，再加一条 `extra` 冒泡的回归
（前端「手动补扫」依赖它显示这次推进了多少）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.api import learning_loop_routes as LR  # noqa: E402
from backend.services.learning_loop_service import (  # noqa: E402
    DEFAULT_INTERVALS,
    JOB_FUSION_MAINTENANCE,
    JOB_HFT_BRAIN,
    JOB_TRIGGER_ALIASES,
    LearningLoopService,
    _JOB_ID_BY_TICK,
)


def test_valid_jobs_is_derived_not_hardcoded():
    assert LR._valid_jobs() == set(JOB_TRIGGER_ALIASES)
    for job in ("hft_brain", "factor_decay", "live_outcome_backfill"):
        assert job in LR._valid_jobs(), f"{job} 必须可手动触发（此前 400）"


def test_every_alias_maps_to_a_real_tick_method():
    svc = LearningLoopService()
    for alias, attr in JOB_TRIGGER_ALIASES.items():
        assert callable(getattr(svc, attr, None)), f"{alias} → {attr} 不存在"


def test_every_tick_method_maps_to_a_tracked_job_id():
    for alias, attr in JOB_TRIGGER_ALIASES.items():
        job_id = _JOB_ID_BY_TICK.get(attr)
        assert job_id, f"{attr} 没有对应的 job_id —— extra 会读不到"
        assert job_id in DEFAULT_INTERVALS, f"{job_id} 不在 DEFAULT_INTERVALS"


def test_last_tick_map_covers_all_intervals():
    svc = LearningLoopService()
    assert set(svc.last_tick_map()) == set(DEFAULT_INTERVALS)
    assert JOB_FUSION_MAINTENANCE in svc.last_tick_map(), (
        "融合维护此前漏登记 ⇒ 停摆也看不见")


def test_status_exposes_hft_brain_interval():
    svc = LearningLoopService()
    st = svc.status()
    assert JOB_HFT_BRAIN in (st.get("intervals") or {})
    assert JOB_HFT_BRAIN in (st.get("last_tick_at") or {})
    assert JOB_HFT_BRAIN in (st.get("next_tick_at") or {})


def test_trigger_unknown_job_lists_valid():
    r = LearningLoopService().trigger_job("nope")
    assert r["ok"] is False
    assert "hft_brain" in r["valid"] and "factor_decay" in r["valid"]


def test_trigger_job_surfaces_extra_by_alias(monkeypatch):
    """`trigger_job("hft_brain")` 必须读到 `learning_loop_hft_brain` 的 extra。

    实测踩过：入参是**路由别名**而 `_metrics` 的键是**内部 job_id**，
    直接 `self._metrics.get(job)` 永远拿不到 ⇒ 返回 extra={}（前端无从显示进展）。
    """
    import collections

    svc = LearningLoopService()
    monkeypatch.setattr(svc, "_tick_hft_brain", lambda: None)
    monkeypatch.setattr(svc, "_record_tick", lambda job, t0, ok, extra: None)
    with svc._state_lock:
        svc._metrics[JOB_HFT_BRAIN] = collections.deque(
            [{"ts": "x", "elapsed_ms": 7, "success": True,
              "extra": {"ok": True, "scanned": 4, "ingested": 4, "failed": 0,
                        "skipped_rows": 0, "cursor": {"ts": 1.0, "id": 11}}}],
            maxlen=5)
    r = svc.trigger_job("hft_brain")
    assert r["ok"] is True and r["job"] == "hft_brain"
    assert r["ingested"] == 4 and r["scanned"] == 4
    assert r["cursor"] == {"ts": 1.0, "id": 11}
    assert r["extra"]["skipped_rows"] == 0
