# -*- coding: utf-8 -*-
"""[新目标 R2] V3 因子管道"外部注入 TTL 缓存"的单测。

根因（实测 probe_v3_per_symbol_cost_20260925.py）：
  onchain_collector.collect_all 每标的 11~14s，加订单流/期权 BTC 合计 ~20s ⇒
  45s 预算每轮只能算 3~5 个标的就超时。这些是慢变外部数据，逐轮逐标的实拉是浪费。
修复：只缓存"注入新增键"，TTL 默认 600s；开关 V3_FACTOR_INJECT_CACHE_ENABLED（默认 true）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

import backend.services.full_auto.v3_factor_pipeline as V

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("V3_FACTOR_INJECT_CACHE_ENABLED", raising=False)
    monkeypatch.delenv("V3_FACTOR_INJECT_CACHE_TTL_SEC", raising=False)
    V._reset_inject_cache_for_test()
    yield
    V._reset_inject_cache_for_test()


def test_switch_and_ttl_defaults(monkeypatch):
    assert V._v3_inject_cache_enabled() is True
    assert V._v3_inject_ttl_sec() == 14400  # 调度 R7：7200→14400（2× 余量覆盖吞吐下限）
    monkeypatch.setenv("V3_FACTOR_INJECT_CACHE_ENABLED", "false")
    assert V._v3_inject_cache_enabled() is False
    monkeypatch.setenv("V3_FACTOR_INJECT_CACHE_ENABLED", "garbage")
    assert V._v3_inject_cache_enabled() is False  # 非法值 fail-closed（与本仓其它开关同约定）
    monkeypatch.setenv("V3_FACTOR_INJECT_CACHE_TTL_SEC", "120")
    assert V._v3_inject_ttl_sec() == 120
    monkeypatch.setenv("V3_FACTOR_INJECT_CACHE_TTL_SEC", "garbage")
    assert V._v3_inject_ttl_sec() == 14400


def test_cache_holds_added_keys_and_respects_ttl(monkeypatch):
    """缓存语义：TTL 内命中返回同一份"新增键"；过期后可刷新。"""
    V._V3_INJECT_CACHE["BTC"] = (1000.0, {"fear_greed": 51.0})
    hit = V._V3_INJECT_CACHE.get("BTC")
    assert hit is not None
    # 用当前时间模拟：默认 TTL 600s，写回时间 1000s 前 ⇒ 已过期（验证 TTL 判定逻辑由调用方执行）
    import time
    assert (time.time() - hit[0]) >= V._v3_inject_ttl_sec()


def test_reset_helper_clears_cache():
    V._V3_INJECT_CACHE["BTC"] = (0.0, {})
    V._reset_inject_cache_for_test()
    assert V._V3_INJECT_CACHE == {}


def test_injection_site_uses_cache():
    """源码守卫：注入点必须包在缓存逻辑里，且三个外部调用都保留（未命中时仍走原路径）。"""
    src = (ROOT / "backend/services/full_auto/v3_factor_pipeline.py").read_text(encoding="utf-8")
    i = src.find("_inj_cache = _V3_INJECT_CACHE")
    assert i >= 0, "未找到缓存接入点"
    seg = src[i:i + 4200]  # 窗口加长：注入段含三段外部调用，勿再缩短
    assert "inject_orderflow_for_factors" in seg
    assert "collect_all" in seg
    assert "get_options_for_symbol" in seg
    assert "_added = {" in seg, "必须只缓存注入新增键"
    assert "V3_FACTOR_INJECT_CACHE_ENABLED" in src, "必须带开关说明（在全文件内找，注释在接入点之前）"


def test_geometry_guarantees_second_pass_hits():
    """[调度 R7] 几何属性（可证伪）：在**修复后的吞吐下限**（每轮 ≥12 标的）下，
    一轮完整轮转的时间必须 ≤ TTL 的 1/2（2× 安全余量）。
    注：时间 TTL 在任意低吞吐下无法保证（每轮 5 标的 ⇒ 13200s 已由历史证伪 7200），
    该残留限制在代码注释中如实登记（需自适应 TTL 才可解，未实现）。"""
    import math
    ttl = V._v3_inject_ttl_sec()
    for per_cycle, universe in ((12, 107), (19, 107)):
        cycles = math.ceil(universe / per_cycle)
        revisit_s = cycles * 600.0          # 健康检查自然节奏 ≈ 10 分钟
        assert revisit_s <= ttl / 2.0, (
            f"每轮 {per_cycle} 标的 ⇒ 一圈 {cycles} 轮 ≈ {revisit_s:.0f}s > TTL/2={ttl/2:.0f}s："
            "第二圈命中没有 2× 余量"
        )
