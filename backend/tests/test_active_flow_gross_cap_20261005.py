# -*- coding: utf-8 -*-
r"""[整顿轮·T20 2026-10-05] `active_flow` 路径必须遵守**账户级总敞口上限**。

事故（实测，$10k 规模）：

    runner.py:1417  active_flow_decision(...)      <- 主路径，先执行
    runner.py:2479  check_side_allowed(...)        <- 账户级敞口闸，后执行

`active_flow_decision` 内部只 import 了 `notional_cap_usd`（单笔上限），
**没有** `check_side_allowed` ⇒ 主路径（`active_flow_mode=1`）
**完全绕过了 gross / symbol / net 三重上限**。

实测后果：$10,252 权益上出现单腿 **$68,247**（= 权益 6.7 倍），
而声明的 `max_gross_notional_ratio=3.0`（上限 $30,757）**从未生效** ——
运行时 `skip_counts` 里 `gross_exposure` / `symbol_exposure` **一次都没出现**。

机制：单笔上限只约束**单次下单**（且另有 `cap ≤ equity×5%`），
而仓位**跨 tick 累积**，累积过程中没有任何账户级约束介入。

修法：调用方传入当前总敞口与上限比例，单笔上限同时受"剩余总敞口"约束。
**这不是新增门禁** —— 该上限早已在配置里、且早已在另一条路径上强制执行，
这里只是让主路径**遵守同一条既有规矩**。
回滚：`MM_AF_GROSS_CAP=0`。
"""
from __future__ import annotations

import inspect

from backend.services.market_maker import active_flow as AF


def test_signature_has_gross_params():
    sig = inspect.signature(AF.active_flow_decision)
    assert "gross_notional_usd" in sig.parameters, "必须接受当前总敞口"
    assert "max_gross_notional_ratio" in sig.parameters, "必须接受敞口上限比例"
    # 默认必须是 0 ⇒ 不传时行为与修复前完全一致（安全回滚）
    assert sig.parameters["gross_notional_usd"].default == 0.0
    assert sig.parameters["max_gross_notional_ratio"].default == 0.0


def test_uses_existing_limit_not_a_new_gate():
    """必须复用既有配置名，不得自造一个新阈值。"""
    src = open(AF.__file__, encoding="utf-8", errors="replace").read()
    assert "max_gross_notional_ratio" in src
    assert "gross_notional_usd" in src


def test_runner_passes_gross():
    """runner 调用点必须真的把值传进去，否则等于没修。"""
    from backend.services.market_maker import runner as R
    src = open(R.__file__, encoding="utf-8", errors="replace").read()
    assert "gross_notional_usd=_af_gross_af" in src, "调用点必须传当前总敞口"
    assert "max_gross_notional_ratio=_af_gross_ratio_af" in src, "调用点必须传上限"


def test_rollback_switch_exists():
    from backend.services.market_maker import runner as R
    src = open(R.__file__, encoding="utf-8", errors="replace").read()
    assert "MM_AF_GROSS_CAP" in src, "必须有回滚开关"


def test_zero_ratio_is_noop():
    """ratio=0 ⇒ 不做任何拦截（回滚路径必须是真的 no-op）。"""
    src = open(AF.__file__, encoding="utf-8", errors="replace").read()
    # 拦截分支必须被 ratio>0 守卫
    idx = src.find("gross_exposure_active_flow")
    assert idx > 0
    guard = src[max(0, idx - 700):idx]
    assert "max_gross_notional_ratio or 0.0) > 0" in guard, (
        "拦截必须由 ratio > 0 守卫，否则回滚后会误伤")
