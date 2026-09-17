"""[轮46 2026-09-17] IC-WFO 晋升判据：单边 t 检验 p 阈值从硬编码改为可配置。

背景（实测）：
- `factor_wfo.run_factor_wfo_ic` 的三个判据里，`oos_p < 0.05` 是**唯一硬编码**的常量；
  另两个（`WFO_IC_MIN_OOS_IC`/`WFO_IC_MAX_DECAY`）都有 env 开关。
- 它是唯一普遍性杀手：181 条"失败币"记录 100% 都有 p ≥ 0.05，而 IC（62% 达标）
  与衰退率（56% 达标）都不是主因；41 个晋级候选在 p<0.05 下通过 0 个，
  与线上连续 7 天 promoted=0 完全吻合。

本测试锁定的契约：
1. 默认值必须 = 0.05（零行为变化，可安全部署）；
2. 新开关必须真的被 env 驱动；
3. 判据表达式必须引用该变量，不得回退成字面量；
4. settings 侧必须有声明（§73.4）。
"""
from __future__ import annotations

import importlib
import inspect

import pytest


MODULE = "backend.services.evolution.factor_wfo"


def _reload(monkeypatch, **env):
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, str(v))
    mod = importlib.import_module(MODULE)
    return importlib.reload(mod)


@pytest.fixture(autouse=True)
def _restore_module():
    """每个用例后把模块恢复成默认 env 下的状态，避免污染其它测试。"""
    yield
    import os
    for k in ("WFO_IC_MAX_P",):
        os.environ.pop(k, None)
    importlib.reload(importlib.import_module(MODULE))


def test_default_equals_legacy_hardcoded_threshold(monkeypatch):
    """默认必须是 0.05 —— 未设置 env 时行为与改动前逐位一致。"""
    mod = _reload(monkeypatch, WFO_IC_MAX_P=None)
    assert mod._WFO_IC_MAX_P == 0.05


def test_threshold_is_env_driven(monkeypatch):
    """新开关必须真的读 env（否则等于没加开关）。"""
    mod = _reload(monkeypatch, WFO_IC_MAX_P="0.20")
    assert mod._WFO_IC_MAX_P == pytest.approx(0.20)


def test_rollback_to_legacy_value(monkeypatch):
    """回滚路径：显式置 0.05 恢复原行为。"""
    mod = _reload(monkeypatch, WFO_IC_MAX_P="0.05")
    assert mod._WFO_IC_MAX_P == 0.05


def test_other_two_criteria_keep_their_env_defaults(monkeypatch):
    """另两个判据的默认值不得被动到。"""
    mod = _reload(monkeypatch, WFO_IC_MAX_P=None)
    assert mod._WFO_IC_MIN_OOS_IC == pytest.approx(0.01)
    assert mod._WFO_IC_MAX_DECAY == pytest.approx(0.50)


def test_pass_rule_references_variable_not_literal():
    """接线守卫：判据必须引用 _WFO_IC_MAX_P，不得存在硬编码 `oos_p < 0.05`。"""
    mod = importlib.import_module(MODULE)
    src = inspect.getsource(mod)
    assert "oos_p < _WFO_IC_MAX_P" in src, "判据未引用可配置阈值"
    assert "oos_p < 0.05" not in src, "仍存在硬编码的 p<0.05 判据"


def test_threshold_semantics_change_verdict(monkeypatch):
    """语义验证：p=0.18 的币在 0.05 下拒、在 0.20 下过（阈值真的影响判定）。"""
    def verdict(p, mod):
        return p < mod._WFO_IC_MAX_P

    strict = _reload(monkeypatch, WFO_IC_MAX_P="0.05")
    assert verdict(0.18, strict) is False

    loose = _reload(monkeypatch, WFO_IC_MAX_P="0.20")
    assert verdict(0.18, loose) is True


def test_settings_declares_the_switch():
    """§73.4：settings 侧必须有声明（此前整族 WFO_IC_* 都没声明）。"""
    from backend.config import settings as S
    assert hasattr(S, "WFO_IC_MAX_P"), "settings 未声明 WFO_IC_MAX_P"
    assert float(S.WFO_IC_MAX_P) == pytest.approx(0.05)
    assert hasattr(S, "WFO_IC_MIN_OOS_IC")
    assert hasattr(S, "WFO_IC_MAX_DECAY")
