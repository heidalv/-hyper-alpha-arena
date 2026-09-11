# -*- coding: utf-8 -*-
"""[2026-09-02] 负期望旁路封堵单测（C7 保底探针 / C8 影子探针 / C9 探索配额）。

定量依据（12.66 万条已结算信号反事实回溯，近 14 天）：
    pwin<0.45       n=27457 wr41.2% 净-0.190%
    pwin[0.45,0.50) n=7442  wr38.1% 净-0.339%   ← 最亏一档
    pwin[0.50,0.55) n=7051  wr50.5% 净-0.176%
    pwin[0.55,0.60) n=3672  wr57.8% 净+0.126%   ← 正期望起点
    pwin[0.60,0.65) n=804   wr67.0% 净+0.336%
正期望分界线 0.55（60 天与 14 天两个口径一致）。主门槛
FUSION_PWIN_TIERS=0.60:0.75,0.55:0.50 的硬地板正好是 0.55，本身是对的；
问题在于三条旁路全部开在 0.55 以下的负期望区间：
    C7 保底探针  FUSION_PROBE_MIN_PWIN=0.45  配额 15/天（超实盘日上限 12）
    C8 影子探针  未显式设置 → 继承 0.45，且与主探针共享同一份配额
    C9 探索配额  元模型 unusable 时每天 5 笔无 pwin 把关的盲开

注：decide_scalp 每次调用都会 _maybe_reload_env(override=True) 覆盖环境变量，
因此下面用 monkeypatch 把它禁掉，以便精确控制单个门槛做逻辑验证；真实 .env
取值另由 test_env_contract_* 直接断言。

[2026-09-04 口径修正] 上述三条封堵的立论是"学习样本可由 scalp_signal_log
无成交积累，不需要用真金白银去换"——这只对实盘成立。模拟盘里没有真金白银，
而信号日志里也没有重训元模型所需的滑点/实际成交价/真实 PnL/退出行为；把
hold 施加到模拟盘的结果是死锁：模型 unusable → 不开仓 → 攒不到成交样本 →
模型永远 unusable（实测 09-04 全天短线 0 单，09-03 尚有 31 笔）。
故封堵用例一律改用 mode="live" 断言，另加 mode="paper" 的放行用例锁定新语义。
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

from backend.services import decision_fusion_arbiter as arb


@pytest.fixture
def frozen_env(monkeypatch):
    """禁掉 .env 热重载，并给出一组确定的门槛。"""
    monkeypatch.setattr(arb, "_maybe_reload_env", lambda: None)
    monkeypatch.setenv("FUSION_PWIN_TIERED", "true")
    monkeypatch.setenv("FUSION_PWIN_TIERS", "0.60:0.75,0.55:0.50")
    monkeypatch.setenv("FUSION_PWIN_ABSOLUTE_MIN", "0.45")
    monkeypatch.setenv("FUSION_SHORT_PWIN_EXTRA", "0")
    monkeypatch.setenv("FUSION_PROBE_MIN_PWIN", "0.55")
    monkeypatch.setenv("FUSION_PROBE_MIN_SCORE", "45")
    monkeypatch.setenv("FUSION_PROBE_DAILY_QUOTA", "3")
    monkeypatch.setenv("FUSION_PWIN_UNUSABLE_MODE", "hold")
    # [2026-09-04] paper/live 分治：实盘沿用全部封堵，模拟盘按采样需求放开
    monkeypatch.setenv("FUSION_PWIN_UNUSABLE_MODE_LIVE", "hold")
    monkeypatch.setenv("FUSION_PWIN_UNUSABLE_MODE_PAPER", "explore_quota")
    monkeypatch.setenv("FUSION_PWIN_EXPLORE_DAILY_QUOTA_PAPER", "120")
    monkeypatch.setenv("FUSION_PROBE_MIN_PWIN_PAPER", "0.42")
    monkeypatch.setenv("FUSION_PROBE_DAILY_QUOTA_PAPER", "60")
    monkeypatch.setenv("FUSION_PROBE_DAILY_QUOTA_LIVE", "0")
    monkeypatch.setattr(arb, "_probe_quota_used", lambda account_id=None: 0)
    monkeypatch.setattr(arb, "_explore_quota_used", lambda account_id=None: 0)
    monkeypatch.setattr(arb, "set_pwin_floor_override", lambda v: None)
    arb.set_pwin_floor_override(None)
    yield


# ───────────────────────── C7：保底探针 ─────────────────────────

@pytest.mark.parametrize("pwin", [0.46, 0.49, 0.50, 0.54])
def test_probe_no_longer_fires_in_negative_ev_band(frozen_env, pwin):
    """实盘：pwin 落在 [0.45,0.55) 负期望带时必须 hold，不能再走探针放行。"""
    d = arb.decide_scalp(
        pwin=pwin, factor_score=70.0, direction="long",
        credit=1.0, mode="live", is_mr=False,
    )
    assert d.action == "hold", f"pwin={pwin} 仍被放行（reason={d.reason}）"
    assert d.reason != "pwin_probe_quota"
    assert d.size_mult == 0.0


@pytest.mark.parametrize("pwin", [0.46, 0.49, 0.50, 0.54])
def test_paper_keeps_sampling_in_negative_ev_band(frozen_env, monkeypatch, pwin):
    """模拟盘：同一负期望带必须继续开单——亏损本身就是要采集的样本。

    模拟盘不花真钱，封堵它只会让元模型永远等不到重训所需的成交样本。
    """
    import backend.services.scalp_meta_trainer as smt
    monkeypatch.setattr(smt, "meta_model_usable", lambda: False, raising=False)
    monkeypatch.setenv("SCALP_FACTOR_EXECUTE_THRESHOLD", "35")

    d = arb.decide_scalp(
        pwin=pwin, factor_score=70.0, direction="long",
        credit=1.0, mode="paper", is_mr=False,
    )
    assert d.action == "trade", f"模拟盘 pwin={pwin} 被拦（reason={d.reason}）"
    assert d.size_mult > 0.0


def test_positive_ev_band_still_trades(frozen_env, monkeypatch):
    """0.55 之上（正期望区间）必须照常放行，别把主链路一起堵死。"""
    import backend.services.scalp_meta_trainer as smt
    monkeypatch.setattr(smt, "meta_model_usable", lambda: True, raising=False)
    d = arb.decide_scalp(
        pwin=0.58, factor_score=70.0, direction="long",
        credit=1.0, mode="paper", is_mr=False,
    )
    assert d.action == "trade"
    assert d.size_mult > 0.0


def test_probe_min_not_below_lowest_tier():
    """实盘探针门槛不得低于最低档阈值，否则旁路重新打开。

    探针分支只在 pwin<硬地板 时进入，而硬地板 = max(最低档阈值, ...)。
    probe_min >= 最低档 ⇒ 该分支数学上不可命中（探针实质关闭）。

    [2026-09-04] 模拟盘（_PAPER）刻意低于最低档——那正是它要采样的区间；
    本用例只守实盘口径。
    """
    from dotenv import dotenv_values
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    env = dotenv_values(os.path.join(repo_root, ".env"))

    probe_min = float(env.get("FUSION_PROBE_MIN_PWIN_LIVE")
                      or env.get("FUSION_PROBE_MIN_PWIN") or 0.40)
    tiers = str(env.get("FUSION_PWIN_TIERS") or "")
    lowest = min(float(p.split(":")[0]) for p in tiers.split(",") if ":" in p)
    assert probe_min >= lowest, (
        f"实盘探针门槛 {probe_min} < 最低档 {lowest} → 负期望旁路仍然打开"
    )


def test_env_contract_probe_quota_below_live_daily_cap():
    """实盘探针日配额必须低于实盘日开仓上限，否则单条旁路就能吃满全天额度。

    [2026-09-04] 只约束实盘口径：模拟盘配额（_PAPER）按采样需求给量，与
    实盘日上限无关。
    """
    from dotenv import dotenv_values
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    env = dotenv_values(os.path.join(repo_root, ".env"))

    quota = int(float(env.get("FUSION_PROBE_DAILY_QUOTA_LIVE")
                      or env.get("FUSION_PROBE_DAILY_QUOTA") or 3))
    live_cap = int(float(env.get("V5_MAX_DAILY_TRADES_LIVE") or 12))
    assert quota < live_cap, f"实盘探针配额 {quota} >= 实盘日上限 {live_cap}"
    assert quota <= 9, "配额应压到个位数"


# ───────────────────────── C8：影子探针 ─────────────────────────

def test_shadow_probe_threshold_pinned_explicitly():
    """影子探针门槛必须显式登记，且与主探针一致。

    代码是 _f("FUSION_SHADOW_PROBE_MIN_PWIN", _f("FUSION_PROBE_MIN_PWIN", 0.40))
    —— 不显式设置时静默继承主探针；一旦有人只调主探针，影子探针会留在旧值
    继续消耗同一份配额。
    """
    from dotenv import dotenv_values
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    env = dotenv_values(os.path.join(repo_root, ".env"))

    shadow = env.get("FUSION_SHADOW_PROBE_MIN_PWIN")
    assert shadow is not None, "影子探针门槛未显式登记"
    assert float(shadow) >= float(env.get("FUSION_PROBE_MIN_PWIN") or 0.40)


def test_shadow_probe_blocked_in_negative_band(frozen_env, monkeypatch):
    """实盘：credit<=0 的影子来源，在负期望带同样不得放行。"""
    monkeypatch.setenv("FUSION_SHADOW_PROBE_MIN_PWIN", "0.55")
    monkeypatch.setenv("FUSION_SHADOW_PROBE_MIN_SCORE", "45")

    d = arb.decide_scalp(
        pwin=0.50, factor_score=70.0, direction="long",
        credit=0.0, mode="live", is_mr=False,
    )
    assert d.reason != "shadow_probe_quota", "影子探针仍在负期望带放行"
    # credit<=0 的来源在探针不放行时回到熔断停摆语义（standdown），与 hold
    # 同为"不开仓"，区别只是归因：standdown=来源被信用熔断。
    assert d.action in ("hold", "standdown")
    assert d.size_mult == 0.0


# ───────────────────────── C9：元模型不可用 ─────────────────────────

def test_unusable_model_no_longer_blind_opens(frozen_env, monkeypatch):
    """实盘：元模型不可用时不得走"无 pwin 把关"的探索配额放行。"""
    import backend.services.scalp_meta_trainer as smt
    monkeypatch.setattr(smt, "meta_model_usable", lambda: False, raising=False)
    monkeypatch.setenv("SCALP_FACTOR_EXECUTE_THRESHOLD", "35")

    d = arb.decide_scalp(
        pwin=0.50, factor_score=70.0, direction="long",
        credit=1.0, mode="live", is_mr=False,
    )
    assert d.reason != "pwin_model_unusable_explore", (
        "元模型不可用期仍在盲开（pwin 此时是掷硬币噪声）"
    )
    assert d.action == "hold"


def test_unusable_model_still_samples_on_paper(frozen_env, monkeypatch):
    """模拟盘：元模型不可用时必须继续采样，否则重训样本永远等不到。"""
    import backend.services.scalp_meta_trainer as smt
    monkeypatch.setattr(smt, "meta_model_usable", lambda: False, raising=False)
    monkeypatch.setenv("SCALP_FACTOR_EXECUTE_THRESHOLD", "35")

    d = arb.decide_scalp(
        pwin=0.50, factor_score=70.0, direction="long",
        credit=1.0, mode="paper", is_mr=False,
    )
    assert d.reason == "pwin_model_unusable_explore"
    assert d.action == "trade"
    assert d.tags.get("explore_quota") is True, "未打配额标签则成交后不会扣减"


def test_env_contract_unusable_mode_split_by_mode():
    """.env 契约：实盘 hold、模拟盘 explore_quota。

    此前是单个全局键钉死 hold，模拟盘被一并封死（09-04 全天 0 单）。
    """
    from dotenv import dotenv_values
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    env = dotenv_values(os.path.join(repo_root, ".env"))
    live = (env.get("FUSION_PWIN_UNUSABLE_MODE_LIVE")
            or env.get("FUSION_PWIN_UNUSABLE_MODE") or "")
    paper = (env.get("FUSION_PWIN_UNUSABLE_MODE_PAPER") or "")
    assert live.strip().lower() == "hold", "实盘不得在 pwin 噪声期盲开"
    assert paper.strip().lower() == "explore_quota", (
        "模拟盘被封死 → 元模型永远攒不到成交样本（死锁）"
    )


def test_usable_model_path_unaffected(frozen_env, monkeypatch):
    """模型可用时行为不变（确认 C9 只影响 unusable 分支）。"""
    import backend.services.scalp_meta_trainer as smt
    monkeypatch.setattr(smt, "meta_model_usable", lambda: True, raising=False)

    d = arb.decide_scalp(
        pwin=0.62, factor_score=70.0, direction="long",
        credit=1.0, mode="paper", is_mr=False,
    )
    assert d.action == "trade"
