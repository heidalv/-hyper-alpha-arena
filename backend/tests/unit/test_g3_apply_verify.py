# -*- coding: utf-8 -*-
"""G3 应用与生效核对的回归测试。

⚠️ **本文件不写任何真实参数、不重启进程。** 只测三类纯逻辑：
  1. `needs_restart` 与 env 白名单一致（决定"改了要不要重启"）
  2. `read_effective` 两个字典都查（params + limits）
  3. `apply_and_verify` 对**白名单键**必须标注"需重启"且**不声称成功**
     —— 这是防"静默偏差"的核心：不能把"写了注册表"当成"生效了"
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "g3_apply_verify.py"


def _load():
    spec = importlib.util.spec_from_file_location("g3_under_test", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    saved = sys.stdout
    try:
        spec.loader.exec_module(m)
    finally:
        sys.stdout = saved
    return m


G = _load()


@pytest.mark.unit
def test_whitelist_keys_need_restart():
    """env 白名单里的 7 个键必须被判定为"需重启"。

    实证（本会话）：`.env` 的 MM_SPREAD_MULT 改成 0.5 后，
    worker 心跳**连续 100 秒仍是 0.9** —— 因为 `apply_env_param_overrides`
    读 `os.getenv`（进程启动时冻结），改文件对已运行进程无效。
    """
    wl = G.env_whitelist()
    assert wl, "env 白名单不应为空"
    for k in ("spread_mult", "spread_mult_reduce", "min_width_bp",
              "k_inv", "spread_cross_margin", "w_base_bp", "min_edge_frac"):
        assert k in wl, f"{k} 应在 env 白名单里"
        assert G.needs_restart(k) is True, f"{k} 应被判为需重启"


@pytest.mark.unit
def test_hot_reloadable_keys_do_not_need_restart():
    """非白名单键（如 compound_ratio / take_profit_bp / stop_loss_bp）可热更新。

    这决定了 P2 的调参能否**不重启**就生效 —— 若 LLM 想调的都是白名单键，
    每次都要重启 worker，闭环成本极高。
    """
    for k in ("compound_ratio", "take_profit_bp", "stop_loss_bp",
              "max_net_directional_ratio", "reduce_quote_disabled",
              "timeout_exit_maker_only", "daily_loss_stop_pct"):
        assert G.needs_restart(k) is False, f"{k} 不该被判为需重启"


@pytest.mark.unit
def test_read_effective_checks_both_dicts():
    """`read_effective` 必须**两个字典都查** —— params 与 limits 分属两个 dataclass。

    第一版我写的核对脚本只查了 params ⇒ `take_profit_bp` / `stop_loss_bp`
    全显示 None（假缺失），差点误判成"没生效"。
    """
    # spread_mult 在 params、stop_loss_bp 在 limits
    assert G.read_effective("spread_mult") is not None, "params 里的键应能读到"
    assert G.read_effective("stop_loss_bp") is not None, "limits 里的键应能读到"
    assert G.read_effective("不存在的键") is None


@pytest.mark.unit
def test_whitelisted_key_apply_is_flagged_not_claimed_success():
    """**核心回归**：对白名单键，`apply_and_verify` 必须标注需重启、
    且**不得声称 verified**（即使它写了注册表）。

    这就是"静默偏差"的防线：若返回 verified=True，调用方会以为改好了，
    而实盘跑的还是旧值 ⇒ 后续所有归因都建立在错误前提上。
    """
    r = G.apply_and_verify("spread_mult", read_current_plus_delta(), dry_run=True)
    assert r["requires_restart"] is True
    assert r["verified"] is False, "白名单键在未重启时绝不能声称已验证"
    assert "重启" in r["note"]


def read_current_plus_delta():
    """取当前生效值（只读），避免测试里写死数字。"""
    v = G.read_effective("spread_mult")
    return float(v) if v is not None else 0.5


@pytest.mark.unit
def test_non_whitelisted_key_is_dry_runnable():
    """非白名单键在 dry_run 下不应产生副作用，且不标 requires_restart。"""
    cur = G.read_effective("take_profit_bp")
    val = float(cur) if cur is not None else 12.0
    r = G.apply_and_verify("take_profit_bp", val, dry_run=True)
    assert r["requires_restart"] is False
    assert r["applied"] is False, "dry_run 不得写盘"
    assert r["verified"] is False


@pytest.mark.unit
def test_registry_read_matches_effective_for_hot_keys():
    """一致性自检：对**非白名单**键，注册表值与心跳生效值应当一致。

    若不一致 ⇒ 说明存在"注册表与实盘分叉"（F287/F292 那一类）。
    本测试把它变成可观测的断言，而不是靠人记得去查。
    """
    for k in ("compound_ratio", "take_profit_bp", "stop_loss_bp"):
        reg = G.read_registry_param(k)
        eff = G.read_effective(k)
        if reg is None or eff is None:
            pytest.skip(f"{k} 缺值，无法比对")
        assert abs(float(reg) - float(eff)) < 1e-6, (
            f"{k} 注册表={reg} 但实盘生效={eff} ⇒ 存在分叉")
