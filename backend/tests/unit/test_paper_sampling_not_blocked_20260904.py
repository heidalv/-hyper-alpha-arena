# -*- coding: utf-8 -*-
"""[2026-09-04] 模拟盘采样链路不得被"保护真金白银"的闸门封死。

背景（实测证据，09-04 全天短线 0 单、中线 0 单，09-03 尚有 31/1 笔）：

三道闸门各自都合理，但都是按"实盘口径"设计，又被无差别地施加到模拟盘：

1. `symbol_penalty` 亏损币状态机（paper 日报驱动）
   九个固定币全部中招：BTC/ETH/SOL/VIRTUAL 进观察名单被直接 continue 禁开；
   XRP/BNB/UNI/XPL/ASTER 被 ×0.5 打折 → 最高分 69 腰斩成 34.5。
2. `FUSION_PWIN_UNUSABLE_MODE=hold`
   元模型 unusable 期直接不开仓。
3. 探索分支门槛 `SCALP_FACTOR_EXECUTE_THRESHOLD=45`
   腰斩后的 34.5 数学上永不可达。

三者叠加成死锁：亏损 → 惩罚/封禁 → 开不了单 → 攒不到新的成交样本 →
惩罚无法翻案、元模型无法重训 → 永远开不了单。

关键区分：这些闸门的立论都是"不拿真金白银换数据"。模拟盘里没有真金白银，
它的产出就是数据本身，而重训元模型所需的滑点、实际成交价、真实 PnL、退出
行为，信号日志里一条都没有，只能靠真实下单产生。故：实盘从严，模拟盘采样。
"""
from __future__ import annotations

import ast
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))


def _src(*parts: str) -> str:
    with open(os.path.join(_ROOT, *parts), encoding="utf-8") as f:
        return f.read()


# ───────────────── 闸门一：亏损币惩罚状态机 ─────────────────

def test_模拟盘默认豁免亏损币惩罚():
    from backend.config.settings import PAPER_SYMBOL_PENALTY_ENABLED
    assert PAPER_SYMBOL_PENALTY_ENABLED is False, (
        "模拟盘重新套用亏损币状态机 → 连亏的币被禁开/打折，"
        "而翻案需要新成交样本，样本又只能靠开单产生（死锁）"
    )


def test_惩罚豁免分支对模拟盘生效():
    """scalp_loop 里必须有 paper 分支给 _pen_exempt 赋值。"""
    src = _src("backend", "services", "full_auto", "loops", "scalp_loop.py")
    assert "PAPER_SYMBOL_PENALTY_ENABLED" in src, "模拟盘豁免开关未接线"
    assert "_pen_exempt = not PAPER_SYMBOL_PENALTY_ENABLED" in src, (
        "模拟盘未从惩罚状态机豁免"
    )
    # 变量名不得退回 _live_pen_exempt —— 它现在同时服务 paper，旧名会误导
    assert "_live_pen_exempt" not in src, (
        "变量名 _live_pen_exempt 已扩展到模拟盘，保留旧名会让人以为只影响实盘"
    )


def test_惩罚仍对实盘生效():
    """实盘不得被一起豁免（引导期另有 live_bootstrap_active 判定）。"""
    src = _src("backend", "services", "full_auto", "loops", "scalp_loop.py")
    assert "live_bootstrap_active(account_id)" in src, (
        "实盘引导期豁免判定丢失 —— 实盘会无条件套用 paper 日报驱动的状态机"
    )
    assert "is_watchlisted(sym)" in src, "观察名单禁开逻辑不应被删除（实盘仍需要）"


# ───────────────── 闸门二：元模型不可用期的处置 ─────────────────

def test_元模型不可用时模拟盘继续采样():
    from dotenv import dotenv_values
    env = dotenv_values(os.path.join(_ROOT, ".env"))
    paper = (env.get("FUSION_PWIN_UNUSABLE_MODE_PAPER") or "").strip().lower()
    live = (env.get("FUSION_PWIN_UNUSABLE_MODE_LIVE")
            or env.get("FUSION_PWIN_UNUSABLE_MODE") or "").strip().lower()
    assert paper == "explore_quota", "模拟盘在元模型不可用期停摆 → 永远攒不到重训样本"
    assert live == "hold", "实盘不得在 pwin 噪声期盲开"


@pytest.mark.parametrize("mode,expect_open", [("paper", True), ("live", False)])
def test_仲裁按模式分治(monkeypatch, mode, expect_open):
    """同一个噪声带信号：模拟盘放行采样，实盘拒绝。"""
    from backend.services import decision_fusion_arbiter as arb
    import backend.services.scalp_meta_trainer as smt

    monkeypatch.setattr(arb, "_maybe_reload_env", lambda: None)
    monkeypatch.setattr(smt, "meta_model_usable", lambda: False, raising=False)
    monkeypatch.setattr(arb, "_explore_quota_used", lambda account_id=None: 0)
    monkeypatch.setattr(arb, "_probe_quota_used", lambda account_id=None: 0)
    monkeypatch.setenv("FUSION_PWIN_TIERS", "0.60:0.75,0.55:0.50")
    monkeypatch.setenv("FUSION_PWIN_UNUSABLE_MODE_PAPER", "explore_quota")
    monkeypatch.setenv("FUSION_PWIN_UNUSABLE_MODE_LIVE", "hold")
    monkeypatch.setenv("FUSION_PWIN_EXPLORE_DAILY_QUOTA_PAPER", "120")
    monkeypatch.setenv("SCALP_FACTOR_EXECUTE_THRESHOLD", "45")
    monkeypatch.setenv("FUSION_PROBE_DAILY_QUOTA_LIVE", "0")

    d = arb.decide_scalp(
        pwin=0.50, factor_score=61.0, direction="long",
        tp_pct=0.008, sl_pct=0.005, credit=1.0, mode=mode,
        account_id=14, is_mr=False,
    )
    if expect_open:
        assert d.action == "trade", f"模拟盘被拦：{d.reason}"
    else:
        assert d.action == "hold", f"实盘被放行：{d.reason}"


# ───────────────── 闸门三：短线影子模式 ─────────────────

def test_模拟盘不走影子层():
    """影子的立论（省手续费）只对实盘成立，模拟盘手续费是虚拟记账。"""
    from backend.services.scalp.shadow_mode import scalp_shadow_enabled
    assert scalp_shadow_enabled("paper") is False, (
        "模拟盘仍被改写成 shadow_fill → 拿不到真实成交数据，"
        "而恢复真单的晋升门(N≥300)恰恰需要这些数据（死锁）"
    )


def test_实盘仍受影子模式约束():
    from backend.services.scalp.shadow_mode import scalp_shadow_enabled
    assert scalp_shadow_enabled("live") is True, (
        "实盘影子被一并关掉 —— 未过 edge_ledger 晋升门不得恢复真单"
    )


def test_影子开关fail_closed(monkeypatch):
    """读取异常时必须按影子处理，不能因配置错误回到亏损路径。"""
    import backend.services.scalp.shadow_mode as sm

    def _boom(*a, **k):
        raise RuntimeError("env boom")

    monkeypatch.setattr(sm.os, "getenv", _boom)
    assert sm.scalp_shadow_enabled("paper") is True, "异常时未 fail-closed"


def test_影子开关真的读取了模式参数():
    """回归：原实现收了 trade_mode 却从不使用，两种模式返回同一个值。"""
    import inspect

    from backend.services.scalp import shadow_mode as sm
    src = inspect.getsource(sm.scalp_shadow_enabled)
    assert "trade_mode" in src.split('"""')[0] + src.split('"""')[-1], (
        "trade_mode 参数仍未参与判定"
    )
    assert "SCALP_SHADOW_MODE_PAPER" in src


# ───────────────── 闸门四：中线入场阈值 ─────────────────

def test_中线阈值按模式分治():
    from backend.config.settings import (
        FACTOR_ROUTE_ENTRY_THRESHOLD_LIVE,
        FACTOR_ROUTE_ENTRY_THRESHOLD_PAPER,
    )
    assert FACTOR_ROUTE_ENTRY_THRESHOLD_PAPER < FACTOR_ROUTE_ENTRY_THRESHOLD_LIVE, (
        "模拟盘阈值未低于实盘 —— 实测全 universe |score| 中位 0.128，"
        "0.35 只有 1/10 过门（且那一笔逆资金流被拦），中线整天零开仓"
    )
    assert FACTOR_ROUTE_ENTRY_THRESHOLD_LIVE >= 0.35, "实盘阈值不得被一并放松"


def test_中线路由按交易模式取阈值():
    src = _src("backend", "services", "factor_engine", "midlong_factor_route.py")
    assert "FACTOR_ROUTE_ENTRY_THRESHOLD_PAPER" in src, "中线路由未按模式取阈值"
    assert "FACTOR_ROUTE_ENTRY_THRESHOLD_LIVE" in src


# ───────────────── 中线宇宙：只用固定币 ─────────────────

def test_中线只扫固定币():
    from backend.config.settings import MIDLONG_MID_AI_CANDIDATES_ENABLED
    assert MIDLONG_MID_AI_CANDIDATES_ENABLED is False, (
        "AI 候选重新并入中线宇宙 —— 每 tick 只扫 MIDLONG_SCAN_BATCH 个币，"
        "AI 那几个名额会挤占固定币的扫描轮次，且实测选出的 AVAX/LINK "
        "在 active 所取不到 K 线（0 根）"
    )


def test_已开AI币仍并入续管():
    """关掉 AI 候选后，已持仓的 AI 币不能没人管仓。"""
    src = _src("backend", "services", "full_auto", "loops", "midlong_loop.py")
    assert "_ai_mid_hold" in src, "续管持仓集合丢失 → 已开的 AI 币会没人管仓"
    assert "_ai_mid_scan = list(dict.fromkeys(list(_ai_mid) + list(_ai_mid_hold)))" in src
