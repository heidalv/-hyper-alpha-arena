# -*- coding: utf-8 -*-
"""[P7 / §71 执行 2026-09-10] EV 闸接线：mid 赛道必须咨询**自己**的（swing）校准器。

根因（§53.1/§53.2 实证）：`pipeline.evaluate_midlong_open` 先把 `trade_nature='swing'`
归一成 `trend_follow`（V5 执行语义）**再**调 EV 闸 ⇒
  * 闸问的是**未校准**的 trend 校准器（`p_src != 'calibrated'`）；
  * `MIDLONG_EV_ENFORCE_REQUIRES_CALIBRATION=true` ⇒ **永久影子放行**；
  * 实测 563 次评估 **100% 影子放行、0 拦截**，而**已校准的 swing 校准器**从未被咨询。

修法（P7）：`evaluate()` 新增 `calib_nature`，与执行语义 `nature` 分开；
pipeline 传入**归一前**的赛道 nature（mid→'swing'）。本测试锁定：
  1. `calib_nature` 决定咨询哪个校准器（而不是执行语义）；
  2. `calib_nature` 决定 EV 门槛前缀（SWING_EV / TREND_EV）；
  3. 校准生效 + EV 不足时闸**真的会拦**（证明"接线后可强制"，而非永久影子）；
  4. 不传 `calib_nature` 时行为与旧版一致（可回滚/可复现）；
  5. pipeline 把归一前的 nature 传下去了（源码级护栏）。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.decision_core import midlong_ev_gate as evg  # noqa: E402
from backend.services.calibration import confidence_calibrator as cc  # noqa: E402

PIPELINE = ROOT / "backend/services/decision_core/pipeline.py"


class _Cal:
    def __init__(self, p_win: float, source: str):
        self.p_win = p_win
        self.source = source


def _install_recorder(monkeypatch, *, p_win: float, source: str) -> list:
    asked: list = []

    def _fake(nature: str):
        asked.append(nature)

        class _C:
            def estimate_p_win(self, symbol, score, direction):
                return _Cal(p_win, source)

        return _C()

    monkeypatch.setattr(cc, "get_calibrator_for_nature", _fake)
    return asked


def test_calib_nature_selects_the_swing_calibrator(monkeypatch):
    asked = _install_recorder(monkeypatch, p_win=0.60, source="calibrated")
    evg.midlong_ev_gate._stats.clear()
    d = evg.midlong_ev_gate.evaluate(
        nature="trend_follow",          # 执行语义（V5 归一后）
        calib_nature="swing",           # 赛道语义（P7 新增）
        symbol="ETH", score=60.0, direction="long", tp_pct=0.06, sl_pct=0.03,
    )
    assert asked == ["swing"], f"咨询的校准器应为 swing，实际 {asked}"
    assert d.breakdown["calib_nature"] == "swing"
    assert d.breakdown["ev_prefix"] == "SWING_EV"
    assert "calib=swing" in d.reason and "exec=trend_follow" in d.reason


def test_calib_nature_drives_threshold_prefix(monkeypatch):
    _install_recorder(monkeypatch, p_win=0.60, source="calibrated")
    sw = evg.midlong_ev_gate.evaluate(
        nature="trend_follow", calib_nature="swing",
        symbol="ETH", score=60.0, direction="long", tp_pct=0.06, sl_pct=0.03,
    )
    tr = evg.midlong_ev_gate.evaluate(
        nature="trend_follow", calib_nature="trend_follow",
        symbol="ETH", score=60.0, direction="long", tp_pct=0.06, sl_pct=0.03,
    )
    assert sw.breakdown["ev_prefix"] == "SWING_EV"
    assert tr.breakdown["ev_prefix"] == "TREND_EV"
    assert sw.breakdown["eff_win"] != pytest.approx(0) and tr.breakdown["eff_win"] != pytest.approx(0)


def test_backward_compatible_without_calib_nature(monkeypatch):
    asked = _install_recorder(monkeypatch, p_win=0.60, source="calibrated")
    evg.midlong_ev_gate.evaluate(
        nature="trend_follow",
        symbol="ETH", score=60.0, direction="long", tp_pct=0.06, sl_pct=0.03,
    )
    assert asked == ["trend_follow"], "缺省时必须退回执行语义（旧行为可复现）"


def test_gate_can_actually_enforce_once_wired(monkeypatch, caplog):
    """校准生效 + EV 明显不足 + **显式开启** mid 赛道强制 ⇒ 必须真拦。

    P7 的核心验收：接线后闸**能够**强制（不再结构性影子）；但强制与否由
    `MIDLONG_EV_ENFORCE_MID` 显式控制，默认 false 以免接线副作用直接关闭 mid 车道。
    """
    _install_recorder(monkeypatch, p_win=0.20, source="calibrated")
    monkeypatch.setattr(evg.midlong_ev_gate, "_cfg", staticmethod(
        lambda name, default: {"MIDLONG_EV_GATE_ENABLED": True,
                               "MIDLONG_EV_ENFORCE_REQUIRES_CALIBRATION": True,
                               "MIDLONG_EV_ENFORCE_MID": True}.get(name, default)
    ))
    with caplog.at_level(logging.INFO):
        d = evg.midlong_ev_gate.evaluate(
            nature="trend_follow", calib_nature="swing",
            symbol="ETH", score=60.0, direction="long", tp_pct=0.02, sl_pct=0.05,
        )
    assert d.p_win_source == "calibrated"
    assert d.ev_pct < d.ev_min
    assert d.allowed is False, f"校准生效+开关开启+EV 不足却未拦: {d.reason}"
    assert not d.breakdown.get("shadow_cold_start"), "不应再走冷启动影子分支"
    assert not d.breakdown.get("shadow_lane_switch"), "赛道开关已开，不应影子"


def test_mid_lane_stays_shadow_until_switch_on(monkeypatch, caplog):
    """安全护栏：接线后 **默认**（未开 `MIDLONG_EV_ENFORCE_MID`）mid 仍只影子记录。

    理由（§53.2）：该口径下 mid 提案 EV ≈ −2.60% ⇒ 立刻硬拦等于**关闭 mid 车道**，
    必须是用户显式决定，而不是接线修好的副作用。
    """
    _install_recorder(monkeypatch, p_win=0.20, source="calibrated")
    monkeypatch.setattr(evg.midlong_ev_gate, "_cfg", staticmethod(
        lambda name, default: {"MIDLONG_EV_GATE_ENABLED": True,
                               "MIDLONG_EV_ENFORCE_REQUIRES_CALIBRATION": True,
                               "MIDLONG_EV_ENFORCE_MID": False}.get(name, default)
    ))
    with caplog.at_level(logging.WARNING):
        d = evg.midlong_ev_gate.evaluate(
            nature="trend_follow", calib_nature="swing",
            symbol="ETH", score=60.0, direction="long", tp_pct=0.02, sl_pct=0.05,
        )
    assert d.allowed is True, "默认应影子放行（车道强制是显式开关）"
    assert d.breakdown.get("shadow_lane_switch") is True
    assert not d.breakdown.get("shadow_cold_start"), "此时校准是生效的，影子原因是车道开关"
    assert "lane_switch_off=True" in d.reason


def test_trend_lane_unaffected_by_mid_switch(monkeypatch):
    """[M1 2026-09-14 语义更新] trend 赛道默认与 mid 同款「车道开关未开=影子放行」
    （MIDLONG_EV_ENFORCE_LONG 默认 false）；硬拦须显式开 MIDLONG_EV_ENFORCE_LONG。
    旧语义（trend 恒按校准硬拦）是「EV 负→硬拦→零样本→校准恒旧」死亡螺旋的成因。
    """
    _install_recorder(monkeypatch, p_win=0.20, source="calibrated")
    monkeypatch.setattr(evg.midlong_ev_gate, "_cfg", staticmethod(
        lambda name, default: {"MIDLONG_EV_GATE_ENABLED": True,
                               "MIDLONG_EV_ENFORCE_REQUIRES_CALIBRATION": True,
                               "MIDLONG_EV_ENFORCE_MID": False,
                               "MIDLONG_EV_ENFORCE_LONG": False,
                               "MIDLONG_EV_ENFORCE_LONG_PAPER_ALLOW": True}.get(name, default)
    ))
    d = evg.midlong_ev_gate.evaluate(
        nature="trend_follow", calib_nature="trend_follow",
        symbol="ETH", score=60.0, direction="long", tp_pct=0.02, sl_pct=0.05,
    )
    # 默认（enforce_long=false）：影子放行（lane switch off，与 mid 同语义）
    assert d.allowed is True
    assert d.breakdown.get("shadow_lane_switch") is True


def test_trend_lane_hard_blocks_when_enforce_long_on(monkeypatch):
    """[M1 2026-09-14] 显式开 MIDLONG_EV_ENFORCE_LONG 且 live → 校准硬拦（保护不变）。"""
    _install_recorder(monkeypatch, p_win=0.20, source="calibrated")
    monkeypatch.setattr(evg.midlong_ev_gate, "_cfg", staticmethod(
        lambda name, default: {"MIDLONG_EV_GATE_ENABLED": True,
                               "MIDLONG_EV_ENFORCE_REQUIRES_CALIBRATION": True,
                               "MIDLONG_EV_ENFORCE_MID": False,
                               "MIDLONG_EV_ENFORCE_LONG": True,
                               "MIDLONG_EV_ENFORCE_LONG_PAPER_ALLOW": True}.get(name, default)
    ))
    d = evg.midlong_ev_gate.evaluate(
        nature="trend_follow", calib_nature="trend_follow",
        symbol="ETH", score=60.0, direction="long", tp_pct=0.02, sl_pct=0.05,
        paper_mode=False,  # live
    )
    assert d.allowed is False
    assert not d.breakdown.get("shadow_lane_switch")


def test_pipeline_passes_pre_normalization_nature():
    src = PIPELINE.read_text(encoding="utf-8")
    assert "_lane_nature = nature" in src, "未保留归一前的赛道 nature"
    i_lane = src.index("_lane_nature = nature")
    i_norm = src.index("norm_nature = normalize_v5_nature(nature)")
    i_ev = src.index("calib_nature=_lane_nature")
    assert i_lane < i_norm < i_ev, "顺序应为：保留赛道 nature → 归一 → 传给 EV 闸"
    assert "calib_nature" in src[: i_ev + 200]


def test_mid_enforce_key_declared_and_registered():
    """.env 能控制的前提：settings 必须声明；同时必须登记（否则注册表校验会告警）。

    [P7 执行 2026-09-10] 线上已按用户决策把 `.env` 设为 `true`，故此处断言的是
    **代码里的安全默认值**（源文件字面量必须仍是 "false"）——避免"改 .env"顺手把
    代码默认也改成 true，让新建环境默认关闭 mid 车道。
    """
    from pathlib import Path as _P

    from backend.config import settings
    from backend.config.env_registry import KNOWN_FLAGS

    assert hasattr(settings, "MIDLONG_EV_ENFORCE_MID"), \
        "settings 未声明 MIDLONG_EV_ENFORCE_MID ⇒ .env 打开/关闭都不会生效（静默失效）"
    src = (_P(settings.__file__)).read_text(encoding="utf-8-sig", errors="replace")
    import re

    m = re.search(r'MIDLONG_EV_ENFORCE_MID:\s*bool\s*=\s*os\.getenv\(\s*"MIDLONG_EV_ENFORCE_MID",\s*"(\w+)"',
                  src)
    assert m, "找不到 MIDLONG_EV_ENFORCE_MID 的声明"
    assert m.group(1).lower() == "false", f"代码默认必须是 false，实际 {m.group(1)!r}"
    assert "MIDLONG_EV_ENFORCE_MID" in KNOWN_FLAGS


def test_env_turns_mid_enforcement_on():
    """线上应按用户决策生效（若这里失败，说明 .env 被改回/未加载）。"""
    from backend.config import settings

    assert settings.MIDLONG_EV_ENFORCE_MID is True, \
        "线上应为 true（P7 决策）；若你已改回 false，请同步本测试"
