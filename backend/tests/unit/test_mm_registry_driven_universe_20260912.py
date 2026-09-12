# -*- coding: utf-8 -*-
"""[2026-09-12 F77] L1 做市车道配置由 lane_registry 驱动的契约。

现场：get_runner 构造 ShadowRunner 时不传 symbols ⇒ 永远跑 DEFAULT_SYMBOLS(6 币)，
币种精选（组合回放证实 alt 币全负、BTC-only 为正）无法上线；
fill_notional 也只能靠环境变量，无法按车道配置。
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import runner as mmrunner  # noqa: E402


def test_get_runner_passes_symbols_from_registry_meta():
    """币种宇宙必须来自注册表 meta.symbols（空则回退 DEFAULT_SYMBOLS）。"""
    src = inspect.getsource(mmrunner.get_runner)
    assert 'meta.get("symbols")' in src, "必须读 meta.symbols"
    assert "symbols=_symbols" in src, "必须把 _symbols 传给 ShadowRunner"
    assert "DEFAULT_SYMBOLS" in src, "空列表须回退 DEFAULT_SYMBOLS"


def test_get_runner_passes_fill_notional_from_params():
    """每腿名义必须来自 meta.params.fill_notional（空则回退 FILL_NOTIONAL）。"""
    src = inspect.getsource(mmrunner.get_runner)
    assert 'stored.get("fill_notional")' in src, "必须读 meta.params.fill_notional"
    assert "fill_notional=_fn" in src, "必须把 _fn 传给 ShadowRunner"


def test_registry_params_map_to_dataclass_fields_only():
    """注册表参数只映射到 QuoteParams/LaneRiskLimits 声明的字段（防错字漂移）。"""
    src = inspect.getsource(mmrunner.get_runner)
    assert "QuoteParams.__dataclass_fields__" in src
    assert "LaneRiskLimits.__dataclass_fields__" in src
