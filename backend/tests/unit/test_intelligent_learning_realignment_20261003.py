# -*- coding: utf-8 -*-
"""[2026-10-03] 智能学习模块「以运行后端为准」重校 + 重排 + 断链暴露 护栏。

用户原话：「智能学习里前端功能应该后端对不上了，需要重新校对，并做功能重排，重新设计这个模块的前端，
和现在运行的后端对齐。并补齐功能显示，需要各种图标显示清晰，如果有断链，或是设计逻辑缺陷需要补齐，
也是看看现在的学习进化链路是不是真的起作用，慢慢补齐」。

## 本次契约体检结论（实测，2026-10-03）
· 前端在用 13 个接口：**全部 200**（无 404 断链）；
· 但后端有 **18 个带真实数据的接口从未上屏** —— Hermes 家族 9 个（智慧 232 条 / 模式 14 /
  L3 提案 967 / L4 候选 468 / 成熟度 75）、learning 家族 7 个、intelligent-learning 3 个；
· 链路真实性：学习循环 enabled/未暂停，11 项健康全 ok（复盘 4102 / 策略记忆 613 / 进化事件 470），
  **但三处闭环没走通**：
    ① 血缘账本仅 4 条事件、来源全是 selftest/backtest ⇒ 真实决策未入账；
    ② L3 架构 286 待审、accepted+rejected=0 ⇒ 无人裁决；
    ③ L4 起源 345 孵化、0 validated、0 promoted、失败 123 ⇒ 晋升闭环断（成熟度 L4=0）。
本文件钉住：新面板存在、九环判据在代码里、页签注册、以及"断链必须显式标红"的表达不被删除。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

SRC_PAGE = (ROOT / "frontend-next/src/app/intelligent-learning/page.tsx").read_text(encoding="utf-8")
SRC_CHAIN = (ROOT / "frontend-next/src/components/learning/ChainOverviewPanel.tsx").read_text(encoding="utf-8")
SRC_HERMES = (ROOT / "frontend-next/src/components/learning/HermesLevelsPanel.tsx").read_text(encoding="utf-8")
SRC_REPORTS = (ROOT / "frontend-next/src/app/reports/page.tsx").read_text(encoding="utf-8")


def test_page_registers_ten_tabs_with_new_two_first():
    for key in ('"chain"', '"hermes"', '"lifecycle"', '"channels"', '"decision"', '"coin"',
                '"compute"', '"lineage"', '"schedule"', '"reports"'):
        assert key in SRC_PAGE, f"页签缺: {key}"
    assert '{ key: "chain", label: "链路总览"' in SRC_PAGE, "链路总览必须是第一个页签"
    assert '{ key: "hermes", label: "Hermes 四级"' in SRC_PAGE
    assert "ChainOverviewPanel" in SRC_PAGE and "HermesLevelsPanel" in SRC_PAGE
    # 深链兼容：/reports 桩指向 ?tab=reports，页面必须仍接受它
    assert 'tab === "reports" && <LongReportsPanel />' in SRC_PAGE
    assert "?tab=reports" in SRC_REPORTS


def test_chain_panel_covers_nine_stages_with_real_endpoints():
    for ep in ("/api/learning/health", "/api/learning/loop/status", "/api/hermes/dashboard",
               "/api/learning/replay/stats", "/api/learning/rl/status", "/api/learning/events"):
        assert ep in SRC_CHAIN, f"链路总览缺真实数据源: {ep}"
    for i in ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨"]:
        assert f'"{i} ' in SRC_CHAIN or f"name: \"{i}" in SRC_CHAIN, f"缺环节 {i}"


def test_chain_panel_encodes_the_real_break_criteria():
    """断链判据必须写死在代码里（否则以后别人把红色改成绿色也没人发现）。

    [2026-10-03 修正] L3 判据由「accepted+rejected===0」改为「pending 占比 >25%」：
    实测 `accept_proposal` 直接落 `implemented`，accepted/rejected 计数恒为 0 是状态词汇设计，
    不能当作"无人裁决"的证据（自我纠正）。
    """
    assert "l3PendingPct > 25" in SRC_CHAIN, "L3 积压判据丢失"
    assert "num(l3.accepted) + num(l3.rejected) + num(l3.implemented)" in SRC_CHAIN, "L3 已裁决口径丢失"
    assert 'l4Validated === 0 && l4Promoted === 0' in SRC_CHAIN, "L4 晋升闭环判据丢失"
    assert "abTests === 0" in SRC_CHAIN, "L2 无 A/B 判据丢失"
    assert "synthPct > 90" in SRC_CHAIN, "回放合成占比判据丢失"
    assert "events.length < 20" in SRC_CHAIN, "血缘账本空转判据丢失"
    assert 'label: "断链"' in SRC_CHAIN and 'label: "停滞"' in SRC_CHAIN and 'label: "在跑"' in SRC_CHAIN


def test_hermes_panel_surfaces_previously_orphan_endpoints():
    """这 9 个接口此前从未被前端调用（契约体检确认），必须全部上屏。
    [2026-10-03] `architecture` 现按 `?status=pending` 拉取（L3 裁决需要 pending 列表）。"""
    for ep in ["dashboard", "maturity", "architecture", "genesis", "patterns",
               "block-patterns", "health", "schedule", "wisdom", "task-log"]:
        assert f'"{ep}' in SRC_HERMES or f'/{ep}' in SRC_HERMES, f"Hermes 面板缺接口: {ep}"
    assert "验收闭环断" in SRC_HERMES or "L3 提案裁决" in SRC_HERMES
    assert "晋升闭环断" in SRC_HERMES
    assert "影子" in SRC_CHAIN, "RL 仅影子运行（shadow_only）必须如实说明，不能当成故障或隐瞒"
