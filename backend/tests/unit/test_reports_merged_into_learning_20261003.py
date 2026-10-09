# -*- coding: utf-8 -*-
"""[2026-10-03] 「周期报告 并入 智能学习」护栏。

用户原话：「周期报告 并入 智能学习」。
做法（沿用本仓库合并惯例）：原 `/reports` 只有 PageHeader + `LongReportsPanel`，
现整块成为「智能学习中心」的第八个 Tab（深链 `?tab=reports`），`/reports` 保留为跳转桩
（静态导出下删路由 = 404），侧栏不再单列入口，命令面板改为直达该 Tab（老搜索词保留）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

SRC_LEARN = (ROOT / "frontend-next/src/app/intelligent-learning/page.tsx").read_text(encoding="utf-8")
SRC_REPORTS = (ROOT / "frontend-next/src/app/reports/page.tsx").read_text(encoding="utf-8")
SRC_SIDEBAR = (ROOT / "frontend-next/src/components/layout/Sidebar.tsx").read_text(encoding="utf-8")
SRC_PALETTE = (ROOT / "frontend-next/src/components/layout/CommandPalette.tsx").read_text(encoding="utf-8")


def test_learning_page_hosts_reports_tab():
    assert '"reports"' in SRC_LEARN.split("type Tab")[1].split(";")[0], "Tab 联合类型缺 reports"
    assert '{ key: "reports", label: "周期报告"' in SRC_LEARN, "页签未注册"
    assert 'tab === "reports" && <LongReportsPanel />' in SRC_LEARN, "内容未挂载"
    assert 'searchParams.get("tab")' in SRC_LEARN, "深链 ?tab=reports 未接（跳转桩目标）"


def test_reports_route_is_redirect_stub():
    assert "softNavigate" in SRC_REPORTS
    assert '"/intelligent-learning?tab=reports"' in SRC_REPORTS
    assert "LongReportsPanel" not in SRC_REPORTS.replace(
        "* 原页面只有 PageHeader + `LongReportsPanel`，", ""
    ) or True  # 说明文字可提及；关键是不得再渲染
    assert "<LongReportsPanel" not in SRC_REPORTS, "跳转桩不得再渲染面板"


def test_sidebar_and_palette_retargeted():
    assert '{ href: "/reports"' not in SRC_SIDEBAR, "侧栏仍单列「周期报告」"
    assert '{ href: "/intelligent-learning", label: "智能学习"' in SRC_SIDEBAR
    assert '{ href: "/reports"' not in SRC_PALETTE
    assert 'href: "/intelligent-learning?tab=reports"' in SRC_PALETTE, "命令面板未直达报告 Tab"
    # 老搜索词保留，避免"搜得到但打不开"
    for kw in ("周期报告", "日报", "周报"):
        assert kw in SRC_PALETTE, f"命令面板丢关键词: {kw}"
