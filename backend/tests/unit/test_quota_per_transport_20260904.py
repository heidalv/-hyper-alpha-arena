# -*- coding: utf-8 -*-
"""按传输的配额上限（2026-09-04）。

背景：`ANALYSIS_5H_CALLS_PER_MODEL` 等是**全局**上限，而各家套餐差着数量级 ——
GLM Coding Plan Max 每 5h 约 2400 次、MiniMax Token Plan 实测 8 次仅耗 4%，
DeepSeek 则是按量付费（超额即账单）。共用一个数只能按最弱的那家定，结果是
买来的高档额度只用得到约 1%，模型却频繁被本地误判 degrade。

锁三件事：
  1. map 里配了的传输按自己的上限走
  2. map 里没配的传输回落全局默认（不改变既有行为）
  3. 非法配置项被忽略而不是让整个配额体系崩掉
"""
from __future__ import annotations

import pytest

from backend.services.analysis import quota_guard as qg


@pytest.fixture()
def guard(monkeypatch):
    monkeypatch.setenv("ANALYSIS_LOCAL_TRANSPORTS", "ollama")
    monkeypatch.setenv("ANALYSIS_5H_CALLS_PER_MODEL", "3")
    monkeypatch.setenv("ANALYSIS_WEEKLY_CALLS_PER_MODEL", "10")
    monkeypatch.setenv("ANALYSIS_LIGHT_DAILY_PER_MODEL", "100")
    monkeypatch.setenv("ANALYSIS_5H_CALLS_MAP", "glm_opencode:8,glm_opencode_alt:5")
    monkeypatch.setenv("ANALYSIS_WEEKLY_CALLS_MAP", "glm_opencode:40")
    monkeypatch.setenv("ANALYSIS_DAILY_CALLS_MAP", "deepseek:4")
    g = qg.QuotaGuard()
    monkeypatch.setattr(qg.QuotaGuard, "_persist", lambda *a, **k: None)
    # 隔离生产数据：_load 会从 llm_quota_usage 回读近 7 天真实用量，
    # 不屏蔽的话断言拿到的是线上计数（实测 glm 已用 32 次），与被测逻辑无关。
    monkeypatch.setattr(qg.QuotaGuard, "_load", lambda self: None)
    return g


def _record(g, transport, n=1):
    for _ in range(n):
        g.record(transport, "gateway_test", model="m", input_tokens=10,
                 output_tokens=10, latency_ms=1, ok=True)


def test_map_中的传输按自己的上限(guard):
    """GLM 配 8：用到 7 次仍放行，正是全局 3 会误拦的区间。"""
    _record(guard, "glm_opencode", n=7)
    d = guard.check("glm_opencode", "gateway_test", est_context_tokens=100)
    assert d.action == "allow", f"GLM 上限 8，第 8 次应放行，实际 {d.action}: {d.reason}"

    _record(guard, "glm_opencode", n=1)
    d2 = guard.check("glm_opencode", "gateway_test", est_context_tokens=100)
    assert d2.action == "degrade" and "8" in d2.reason, f"到 8 应降级: {d2.reason}"


def test_未配置的传输回落全局默认(guard):
    """行为不变保证：没进 map 的传输仍按全局 3 走。"""
    _record(guard, "deepseek", n=3)
    d = guard.check("deepseek", "gateway_test", est_context_tokens=100)
    assert d.action == "degrade", f"未配置应用全局 3，实际 {d.action}"
    assert "/3" in d.reason, f"理由应体现全局上限 3：{d.reason}"


def test_各传输的额度互不干扰(guard):
    """[轮155 2026-09-21] 原用 minimax 举例（用户已停用 minimax）→ 改为 GLM 两条通道：
    同一套餐的两条通道也是独立计数，用满一条不影响另一条。"""
    _record(guard, "glm_opencode_alt", n=5)
    assert guard.check("glm_opencode_alt", "gateway_test", est_context_tokens=100).action == "degrade"
    assert guard.check("glm_opencode", "gateway_test", est_context_tokens=100).action == "allow"


def test_按传输日上限与分类上限取较小者(guard):
    """DeepSeek 是按量付费：分类上限 100 很宽，但传输日上限 4 必须生效。"""
    _record(guard, "deepseek", n=4)
    d = guard.check("deepseek", "gateway_test", est_context_tokens=100)
    assert d.action == "degrade", f"传输日上限 4 应生效，实际 {d.action}: {d.reason}"
    assert "/4" in d.reason, f"理由应体现较小的那个上限：{d.reason}"


def test_周上限同样按传输(guard):
    b = guard.budget
    assert b.calls_weekly_for("glm_opencode") == 40
    assert b.calls_weekly_for("glm_opencode_alt") == 10, "未配周上限的传输回落全局"


def test_非法配置项被忽略而不炸(monkeypatch):
    """配置写错不该让整个配额体系失效 —— 那会导致要么全拦要么全放。"""
    monkeypatch.setenv("ANALYSIS_5H_CALLS_MAP", "glm_opencode:abc,deepseek:5,,坏数据")
    b = qg.Budget.from_env()
    assert b.calls_5h_for("deepseek") == 5, "合法项仍应生效"
    assert b.calls_5h_for("glm_opencode") == b.calls_5h, "非法项回落全局默认"


def test_本地传输不受按传输上限影响(guard):
    """本地豁免优先级高于任何次数上限。"""
    _record(guard, "ollama", n=50)
    assert guard.check("ollama", "gateway_test", est_context_tokens=100).action == "allow"


def test_看板给出各传输的上限(guard):
    snap = guard.snapshot()
    tr = snap["transports"]
    assert tr["glm_opencode"]["calls_5h_budget"] == 8
    assert tr["glm_opencode_alt"]["calls_5h_budget"] == 5
    assert tr["deepseek"]["calls_5h_budget"] == 3, "未配置的回落全局"


def test_本地传输在看板上标为不限量(guard):
    """报全局默认会显示成 0/3，看着像有额度限制 —— 实则本地不受次数约束。"""
    tr = guard.snapshot()["transports"]
    assert "ollama" in tr, "本地传输也应出现在看板（耗时/成功率需与云端横向对比）"
    assert tr["ollama"]["local"] is True
    assert tr["ollama"]["calls_5h_budget"] == -1
    assert tr["ollama"]["calls_week_budget"] == -1
    assert tr["deepseek"]["local"] is False
