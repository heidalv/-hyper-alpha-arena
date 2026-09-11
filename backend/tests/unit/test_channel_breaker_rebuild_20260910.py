# -*- coding: utf-8 -*-
"""[§77 / 缺陷 #61] 通道熔断（`_breaker_shadow`）的**加载期重建**契约测试。

背景（§77.3）：`_ensure_loaded()` 因 2026-08-31「累计制→滚动窗制」迁移**刻意清空**
`_breaker_shadow`，而标志只在 `record_close()` 按"被平仓的那个通道"更新 ⇒
**每次重启后所有通道的熔断标志都为空**，要等该通道下一次平仓才重建（盲窗）。

本测试锁定：
  1. 重建口径与 `record_close()` 一致（n≥min(阈值,滚动窗) 且 wr<40% ⇒ shadow）；
  2. 样本不足的通道**不得**被标记（避免"没数据就熔断"）；
  3. 默认（开关未开）**行为不变**：加载后 `_breaker_shadow` 仍为空；
  4. 开关打开时会重建，且**只恢复**既有判定（不新增样本）。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services import source_attribution as sa  # noqa: E402


def _mk(recent, *, n=None, wins=None):
    return {"n": n if n is not None else len(recent),
            "wins": wins if wins is not None else sum(recent),
            "recent": list(recent)}


def test_rebuild_matches_record_close_rule():
    a = sa.SourceAttribution()
    a._breaker = {
        "mid|trend_broken": _mk([0] * 25 + [1] * 5),      # wr=5/30=16.7% ⇒ shadow
        "long|thesis_invalidation": _mk([0, 0, 0]),        # 样本不足 ⇒ 不标记
        "mid|breakeven_tp": _mk([1] * 30),                 # wr=100% ⇒ False
        "short|manual": _mk([1, 0] * 15),                  # wr=50% ⇒ False
    }
    out = a.rebuild_breaker_shadow()
    assert out["mid|trend_broken"] is True
    assert out["mid|breakeven_tp"] is False
    assert out["short|manual"] is False
    assert "long|thesis_invalidation" not in out, "样本不足的通道不得被标记"


def test_boundary_is_strictly_less_than_40pct(monkeypatch):
    # 先固定阈值，隔离"规则本身"的验证（环境里 MIN_N 被 .env 设为 15，见下一用例）
    monkeypatch.setenv("EXIT_CHANNEL_SHADOW_MIN_N", "30")
    monkeypatch.setenv("EXIT_CHANNEL_SHADOW_MAX_WR", "0.40")
    a = sa.SourceAttribution()
    # 恰好 40%（12/30）⇒ 不 shadow（`<` 是严格小于）
    a._breaker = {"mid|edge": _mk([1] * 12 + [0] * 18)}
    assert a.rebuild_breaker_shadow()["mid|edge"] is False
    # 36.7%（11/30）⇒ shadow
    a._breaker = {"mid|edge2": _mk([1] * 11 + [0] * 19)}
    assert a.rebuild_breaker_shadow()["mid|edge2"] is True


def test_environment_thresholds_are_what_production_uses():
    """记录**实际生效**的熔断阈值（`.env=15/0.40`）。

    [§77 教训] 我最初按"默认 30"写了边界断言，立刻被环境值（15）打红 ——
    这正是本项目反复强调的"配置实际值 vs 预期"：断言必须锚定真实口径。
    """
    import os

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
    min_n = int(float(os.environ.get("EXIT_CHANNEL_SHADOW_MIN_N", "30") or 30))
    max_wr = float(os.environ.get("EXIT_CHANNEL_SHADOW_MAX_WR", "0.40") or 0.40)
    assert min_n == 15, f"生效的最小样本数变了（{min_n}）—— 请同步本断言与 §77 口径"
    assert max_wr == 0.40
    # 窗口口径：min(配置值, ROLLING_WINDOW=30)
    a = sa.SourceAttribution()
    monkeypatch_env = {"EXIT_CHANNEL_SHADOW_MIN_N": str(min_n), "EXIT_CHANNEL_SHADOW_MAX_WR": str(max_wr)}
    a._breaker = {"mid|x": _mk([1] * 5 + [0] * 10)}  # 15 笔中 5 胜 = 33% ⇒ 该 shadow
    assert a.rebuild_breaker_shadow()["mid|x"] is True


def test_default_off_keeps_previous_behaviour(monkeypatch, tmp_path):
    """开关未开 ⇒ 加载后 `_breaker_shadow` 仍为空（与线上既有行为一致）。"""
    state = tmp_path / "state.json"
    state.write_text(json.dumps({
        "ts": 0, "tags": {}, "stats": {},
        "breaker": {"mid|trend_broken": _mk([0] * 30)},
        "shadow": {}, "breaker_shadow": {},
    }), encoding="utf-8")
    monkeypatch.setattr(sa, "_STATE_PATH", str(state))
    monkeypatch.delenv("EXIT_CHANNEL_REBUILD_ON_LOAD", raising=False)
    a = sa.SourceAttribution()
    a._ensure_loaded()
    assert a._breaker_shadow == {}, "默认不得重建（否则是未经决策的行为变更）"
    assert a.exit_channel_shadow("trend_broken", "mid") is False


def test_switch_on_rebuilds_at_load(monkeypatch, tmp_path):
    """开关打开 ⇒ 加载即重建，且按持久化的 `_breaker` 恢复既有判定。"""
    state = tmp_path / "state.json"
    state.write_text(json.dumps({
        "ts": 0, "tags": {}, "stats": {},
        "breaker": {
            "mid|trend_broken": _mk([0] * 30),        # 0% ⇒ shadow
            "mid|breakeven_tp": _mk([1] * 30),        # 100% ⇒ False
        },
        "shadow": {}, "breaker_shadow": {},
    }), encoding="utf-8")
    monkeypatch.setattr(sa, "_STATE_PATH", str(state))
    monkeypatch.setenv("EXIT_CHANNEL_REBUILD_ON_LOAD", "true")
    a = sa.SourceAttribution()
    a._ensure_loaded()
    assert a.exit_channel_shadow("trend_broken", "mid") is True
    assert a.exit_channel_shadow("breakeven_tp", "mid") is False


def test_rebuild_does_not_mutate_breaker_counters():
    """重建是只读的：不得改变 `_breaker` 计数（否则会把统计弄脏）。"""
    a = sa.SourceAttribution()
    before = {"mid|x": _mk([0] * 30)}
    a._breaker = {k: dict(v) for k, v in before.items()}
    a.rebuild_breaker_shadow()
    assert a._breaker == before
