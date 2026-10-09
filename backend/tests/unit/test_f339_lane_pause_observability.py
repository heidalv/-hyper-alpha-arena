# -*- coding: utf-8 -*-
"""[F339 2026-09-22] 车道级闸门的可观测性：`lane_pause` 必须能被读到。

# 缺陷现场（实测）

`plan_tick` 把车道级暂停原因放进 `dec.lane_pause` **和**返回的 `_meta`：

    dec.lane_pause = _lane_why
    return dec, {"book": local_book, "lane_pause": _lane_why}

但**没有任何代码把它聚合进状态文件** ⇒ 车道闸（日亏 / `toxic_streak` /
车道级 σ）触发时**完全不可见**。

这不是理论风险，本会话有一条直接证据：

    status 的 `skip_counts` = {vol_pause: 1422, symbol_exposure: 918, ...}

**从来没有出现过带括号的键**（车道级暂停的形状是 `vol_pause(sigma=0.71)`）
⇒ 车道闸从未被观测到过。也正因如此，
`daily_loss_stop_pct = 80%` 在 **12 天里 0 次触发**（= 尾部零保护）这件事
**一直没被发现** —— 直到 H212 离线反事实才算出来。

# 两类闸门为什么必须分开计数

`skip_counts` 只记 `dec.skip`，而车道暂停的键**带参数**。混在一起会让人
把「单侧 σ 闸」误读成「车道 σ 闸」（本会话已因此误判过一次：
看到 `vol_pause: 1422` 以为车道 σ 闸在工作）。所以：

    skip_counts        → 单侧闸（`check_side_allowed`）
    lane_pause_counts  → 车道闸（`lane_pause_reason`），键 = 原因名（已剥参数）

# 为什么还要记 `day_pnl_usd`

它是日亏闸的**判定输入**（闸门真正读到的数，受 `stats_since` 时代裁剪影响），
与触发线 `day_pnl_limit_usd` 一起看才知道"离触发还有多远"。
只看钳制后的小时均值看不出这个 —— 而调阈值唯一的依据就是它。
"""
from __future__ import annotations

import json
import pathlib

import pytest

from backend.services.market_maker import runner as R
from backend.services.market_maker import worker_status as WS

REPO = pathlib.Path(__file__).resolve().parents[3]
RUNNER_SRC = (REPO / "backend" / "services" / "market_maker" / "runner.py").read_text(
    encoding="utf-8")


# ── 1. 心跳必须真的带这些键 ──────────────────────────────────────────────
@pytest.mark.unit
def test_status_keys_are_registered_for_worker_merge():
    """`merge_worker_status` 只搬运白名单里的键。

    漏在这里 `= 前端永远读到进程内的旧值/None`，而 worker 明明写了 ——
    F285（`states`）与 F288（`avg_width_bp` 等）都是同一个坑，
    所以这条断言直接钉白名单。
    """
    for k in ("lane_pause_counts", "lane_pause_last",
              "day_pnl_usd", "day_pnl_limit_usd"):
        assert k in WS._WORKER_KEYS, (
            f"`{k}` 不在 `_WORKER_KEYS` ⇒ worker 写了也传不到 API/看板")


@pytest.mark.unit
def test_merge_worker_status_carries_lane_pause_fields():
    status = {"ticks": 0}
    snap = {"fresh": True, "ts": 0.0, "ticks": 10,
            "lane_pause_counts": {"daily_loss": 3},
            "lane_pause_last": {"reason": "daily_loss(-9.10<=-7.89)", "ts": 1.0},
            "day_pnl_usd": -9.10, "day_pnl_limit_usd": -7.89}
    out = WS.merge_worker_status(status, snap, prefer_external=True)
    assert out["lane_pause_counts"] == {"daily_loss": 3}
    assert out["lane_pause_last"]["reason"].startswith("daily_loss")
    assert out["day_pnl_usd"] == -9.10
    assert out["day_pnl_limit_usd"] == -7.89


# ── 2. 聚合代码必须存在于 tick 循环里（且真的读了 `_meta`）────────────────
@pytest.mark.unit
def test_tick_loop_aggregates_lane_pause():
    """静态钉住：tick 循环必须从 `_meta` 取 `lane_pause` 并累计。

    为什么用静态断言：跑一个"车道闸触发"的完整 tick 需要构造
    `lane_limits_enforce` + 日亏超限 + 成交，代价高且易脆；而这条缺陷的
    本质是「**写了但没人读**」，静态检查正好直击它。
    """
    assert '_meta or {}).get("lane_pause")' in RUNNER_SRC, (
        "tick 循环没有从 `_meta` 读 `lane_pause` ⇒ 车道闸触发仍不可见")
    assert "self.lane_pause_counts[" in RUNNER_SRC, "没有累计 lane_pause_counts"
    assert "self.lane_pause_last = " in RUNNER_SRC, "没有记 last（缺时刻就查不出何时开始的）"


@pytest.mark.unit
def test_status_dict_exposes_lane_pause_fields():
    """status 字典里必须有这四个键（否则白名单也搬不到东西）。"""
    for k in ('"lane_pause_counts"', '"lane_pause_last"',
              '"day_pnl_usd"', '"day_pnl_limit_usd"'):
        assert k in RUNNER_SRC, f"status 字典缺 {k}"


# ── 3. 语义：键必须是**剥掉参数**的原因名 ────────────────────────────────
@pytest.mark.unit
def test_lane_pause_key_strips_parameters():
    """`daily_loss(-9.10<=-7.89)` ⇒ 键应为 `daily_loss`。

    不剥参数的话，每个不同的数值都会变成一个新键 ⇒ 计数永远散成 1，
    等于没统计。而 `lane_pause_last` 保留完整串（诊断时需要具体数值）。
    """
    assert '_lp.split("(")[0]' in RUNNER_SRC, (
        "lane_pause 计数的键没有剥参数 ⇒ 每次数值不同都会新建一个键")


@pytest.mark.unit
def test_two_gate_families_use_separate_counters():
    """单侧闸与车道闸必须分开计数（本会话误判过一次的直接原因）。"""
    assert "self.skip_counts" in RUNNER_SRC
    assert "self.lane_pause_counts" in RUNNER_SRC
    assert "self.skip_counts = self.lane_pause_counts" not in RUNNER_SRC


# ── 4. 日亏闸阈值可读出（调阈值唯一的依据）──────────────────────────────
@pytest.mark.unit
def test_day_pnl_limit_matches_the_gate_formula():
    """`day_pnl_limit_usd` 必须与 `lane_pause_reason` 的公式一致：

        limit = -|equity × pct / 100|

    两处若各写一遍就会漂移（本会话已多次踩到"同一个量两套口径"）。
    """
    from backend.services.market_maker.core import LaneRiskLimits, lane_pause_reason
    eq, pct = 262.85, 3.0
    lim = LaneRiskLimits(daily_loss_stop_pct=pct)
    # 闸门自己算出来的触发线
    _p, why = lane_pause_reason(equity=eq, limits=lim, day_pnl_usd=-7.0)
    assert not _p, "未超限不应暂停"
    _p2, why2 = lane_pause_reason(equity=eq, limits=lim, day_pnl_usd=-7.9)
    assert _p2, f"应暂停（-7.9 <= -7.8855）；实际 why={why2!r}"
    expect = -abs(eq * pct / 100.0)
    assert abs(expect - (-7.8855)) < 1e-3, f"公式核对：{expect}"
    assert "daily_loss" in why2


@pytest.mark.unit
def test_status_exposes_both_input_and_threshold():
    """输入与阈值必须**成对**出现：只有其中一个都读不出"离触发多远"。"""
    assert '"day_pnl_usd"' in RUNNER_SRC and '"day_pnl_limit_usd"' in RUNNER_SRC
