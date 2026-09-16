# -*- coding: utf-8 -*-
"""[F302 2026-09-16] 行情采集覆盖率契约测试。

现场事故：`mm_asterdex` 的车道宇宙从 LINK/ADA 换成 ZEC/ASTER/SOL/DOGE/TAO 后，
`market_orderbook_snapshots` 里**依然只有旧标的**（ZEC/ASTER/TAO 一行都没有）。
后果链：
  · `replay._load_series` 读不到它们 ⇒ 回放/验收全瞎；
  · `mm_anchor_vol_baseline.py` 对它们恒返回 0.0 ⇒ σ 闸基准无效；
  · `runner.backfill_mid_hist` 补不到冷启动窗口。

根因：`market_data_center` 里**硬编码**了 10 个币，不读车道注册表 ⇒
「车道宇宙」与「行情采集」结构性解耦——换宇宙不会带动行情。

本测试锁定：任何被车道交易的标的，都必须进入行情采集的标的解析结果。
"""
from __future__ import annotations

import inspect

import pytest


def test_lane_symbols_helper_exists():
    from backend.services import market_data_symbol_config as M

    assert hasattr(M, "lane_symbols")


def test_lane_symbols_returns_registry_universe():
    """lane_symbols() 必须真的读 lane_registry.meta.symbols。"""
    from backend.services import market_data_symbol_config as M

    src = inspect.getsource(M.lane_symbols)
    assert "lane_registry" in src
    assert "meta_json" in src
    # 只取在营车道
    assert "active" in src


def test_resolve_includes_lane_symbols():
    """resolve_configured_symbols 必须并入车道标的（而非只看会话/用户自选）。"""
    from backend.services import market_data_symbol_config as M

    src = inspect.getsource(M.resolve_configured_symbols)
    assert "lane_symbols" in src, (
        "采集标的解析未并入车道宇宙 ⇒ 换宇宙后行情跟不上")


def test_market_data_center_no_longer_hardcodes_only_the_old_universe():
    """数据中心必须走解析；硬编码列表只能作为**兜底**存在。"""
    from backend.workers import market_data_center as DC

    src = inspect.getsource(DC)
    assert "resolve_configured_symbols" in src, (
        "market_data_center 仍在硬编码标的 ⇒ 车道宇宙与采集解耦")
    # 兜底列表允许保留，但必须是在解析失败时才用
    assert "_fallback_syms" in src or "fallback" in src


def test_resolution_meta_reports_lane_source(monkeypatch):
    """诊断性：解析结果要能看出标的来自哪个车道。"""
    from backend.services import market_data_symbol_config as M

    monkeypatch.setattr(M, "lane_symbols", lambda statuses=("active",): (
        ["ZEC", "ASTER"], ["lane_registry.mm_test"]))
    syms, meta = M.resolve_configured_symbols("__NO_SUCH_ENV__")
    assert "ZEC" in syms and "ASTER" in syms
    assert any("lane_registry" in s for s in (meta.get("source") or []))
    assert meta.get("lane_symbols") == ["ZEC", "ASTER"]


def test_lane_symbols_is_fail_soft(monkeypatch):
    """注册表读不到 ⇒ 返回空，绝不抛（采集不能因注册表故障而挂）。"""
    from backend.services import market_data_symbol_config as M

    class _Boom:
        def __enter__(self):
            raise RuntimeError("db down")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(M, "SessionLocal", lambda: _Boom())
    syms, src = M.lane_symbols()
    assert syms == [] and src == []
