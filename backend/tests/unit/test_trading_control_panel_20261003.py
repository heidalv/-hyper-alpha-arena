# -*- coding: utf-8 -*-
"""[2026-10-03] 「交易总控」统一控制面护栏。

用户原话：「一个统一管理的位置，要不这个分散和不可见，无法真正的控制账户」。

背景（为什么必须有这一页 / 这一接口）：
  · 「能不能自动交易」= `accounts.auto_trading_enabled`（只在交易所管理页可见）；
  · 「在不在跑」= `full_auto_sessions.status`（只在 AI 策略 → 会话管理可见）；
  · 「停手」要同时做两件事 ⇒ 实测出现过"会话一直 running 并在开仓，而用户以为早停了"。
本文件钉住：全局停止接口存在、页面存在、侧栏可达、以及"关账户开关"必须走同一页。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

SRC_ROUTES = (ROOT / "backend/api/full_auto_routes.py").read_text(encoding="utf-8")
SRC_PAGE = (ROOT / "frontend-next/src/app/control/page.tsx").read_text(encoding="utf-8")
SRC_SIDEBAR = (ROOT / "frontend-next/src/components/layout/Sidebar.tsx").read_text(encoding="utf-8")
SRC_API = (ROOT / "frontend-next/src/lib/api.ts").read_text(encoding="utf-8")


def test_stop_all_route_stops_sessions_and_can_disable_accounts():
    assert '@router.post("/stop-all")' in SRC_ROUTES, "缺统一停止接口"
    seg = SRC_ROUTES.split('@router.post("/stop-all")')[1][:1500]
    assert 'FullAutoSession.status.in_(["running", "defensive", "paused"])' in seg, "必须覆盖三类活动态"
    assert "full_auto_service.stop_session(" in seg, "必须复用既有停会话入口"
    assert 'Account.auto_trading_enabled == "true"' in seg, "必须能关闭账户自动交易开关"
    assert "also_disable_accounts" in seg, "必须可只停会话、不动账户开关"


def test_control_page_is_the_single_control_surface():
    assert "交易总控" in SRC_PAGE
    for token in ("全部停止交易", "sessionApi.stopAll(", "auto_trading_enabled", "useSessions(", "useAccounts("):
        assert token in SRC_PAGE, f"总控页缺关键要素: {token}"
    # 破坏性动作必须二次确认（要求输入确认词）
    assert 'requireText: "停止"' in SRC_PAGE, "一键停手必须要求输入确认词"


def test_control_page_reachable_from_sidebar():
    assert '{ href: "/control", label: "交易总控"' in SRC_SIDEBAR, "侧栏缺「交易总控」入口"
    assert "Power" in SRC_SIDEBAR


def test_api_stop_all_wired():
    assert "/full-auto/stop-all" in SRC_API
    assert "stopAll:" in SRC_API


# ─────────────── 状态列语义（[2026-10-03 用户口径]） ───────────────

def test_status_column_answers_is_it_running_not_is_account_enabled():
    """用户口径：「这个启用，是不是应该改为状态…现在的两个『启用』应该改为关闭，
    等真的运行了，再改为运行中」⇒ 该列只回答"这个账户现在在不在跑"：
    有 running/defensive 会话 → 运行中；否则 → 关闭。"""
    assert ">状态<" in SRC_PAGE or "状态</th>" in SRC_PAGE, "表头必须是「状态」"
    assert ">启用</th>" not in SRC_PAGE, "「启用」列名不得回归（名不副实：看着像在跑）"
    assert "运行中" in SRC_PAGE and "关闭" in SRC_PAGE, "缺 运行中/关闭 两态"
    assert 'live.length > 0 ?' in SRC_PAGE, "状态必须由活动会话（live）推导，而不是 is_active"
    # is_active 只作为附属小字，不再单独占一列
    assert "（账户已停用）" in SRC_PAGE


def test_auto_trading_toggle_is_compact_switch():
    """自动交易列的按钮文案压成 开/关（原为"已开 → 点击关闭"这类长句，易被误读成状态）。"""
    assert '"开" : "关"' in SRC_PAGE, "开关文案应为 开/关"
    assert "已开 → 点击关闭" not in SRC_PAGE, "长句文案不得回归"
    assert "自动交易开关：开（点击关闭）" in SRC_PAGE, "需保留 title 说明"


# ─────────────── 2026-10-03 第二轮：AI 策略 / 交易所管理 并入交易总控 ───────────────

SRC_SIDEBAR = (ROOT / "frontend-next/src/components/layout/Sidebar.tsx").read_text(encoding="utf-8")
SRC_PALETTE = (ROOT / "frontend-next/src/components/layout/CommandPalette.tsx").read_text(encoding="utf-8")
SRC_STRATEGY = (ROOT / "frontend-next/src/app/strategy/page.tsx").read_text(encoding="utf-8")
SRC_EXCHANGE = (ROOT / "frontend-next/src/app/exchange/page.tsx").read_text(encoding="utf-8")


def test_control_page_hosts_all_four_areas():
    """用户口径：「会话管理并入交易总控，交易所管理页并入交易总控」⇒ 四区必须在同一页。"""
    for token in ('"control" | "sessions" | "exchange" | "lanes"', "SessionManager",
                  "ExchangeManagerPanel", "LaneConfigPanel"):
        assert token in SRC_PAGE, f"总控页缺: {token}"
    assert 'label: "总控"' in SRC_PAGE and 'label: "会话管理"' in SRC_PAGE
    assert 'label: "交易所管理"' in SRC_PAGE and 'label: "车道配置"' in SRC_PAGE


def test_merged_panels_exist_and_are_wired():
    """被并入的两块内容必须真的抽成了组件（而不是复制粘贴两份逻辑）。"""
    exch = (ROOT / "frontend-next/src/components/exchange/ExchangeManagerPanel.tsx").read_text(encoding="utf-8")
    assert "export function ExchangeManagerPanel(" in exch, "交易所面板未导出"
    # 只测"有没有真的用"，不测注释里提到的字样
    assert "import { PageHeader }" not in exch and "<PageHeader" not in exch, "面板不应带页面级 PageHeader"
    for sub in ("账户管理", "API 凭证", "交易所监控", "积分账本"):
        assert sub in exch, f"交易所子页签丢失: {sub}"
    lanes = (ROOT / "frontend-next/src/components/config/LaneConfigPanel.tsx").read_text(encoding="utf-8")
    assert "export function LaneConfigPanel(" in lanes
    assert 'tier="mid"' in lanes and 'tier="long"' in lanes, "中线/长线配置卡必须都在"
    assert 'searchParams.get("cfg")' in lanes and 'searchParams.get("sub")' in lanes, "深链参数需保留"


def test_sidebar_group_and_removed_entries():
    """用户口径：「交易总控放在策略配置组，原来的 ai 策略就没有了」。"""
    grp = SRC_SIDEBAR.split('title: "策略配置"')[1].split('title: "市场 & 分析"')[0]
    assert '{ href: "/control", label: "交易总控"' in grp, "交易总控必须在策略配置组"
    assert '"/strategy"' not in SRC_SIDEBAR, "AI 策略入口必须移除"
    assert 'title: "交易所"' not in SRC_SIDEBAR, "「交易所」分组必须移除（已并入总控）"
    assert '{ href: "/exchange"' not in SRC_SIDEBAR


def test_legacy_routes_are_redirect_stubs():
    """旧路由保留为跳转桩（书签/文档/旧 e2e 兼容，静态导出下删除即 404）。"""
    assert "softNavigate" in SRC_STRATEGY and '"/control?tab=sessions"' in SRC_STRATEGY
    assert 'cfg=' in SRC_STRATEGY, "AI 策略桩需把 cfg/sub 深链转成 lanes 页签"
    assert "softNavigate" in SRC_EXCHANGE and '"/control?tab=exchange"' in SRC_EXCHANGE
    assert "import { SessionManager }" not in SRC_STRATEGY, "AI 策略页不得再渲染组件（已成为桩）"


def test_command_palette_retargeted():
    """搜索面板不得再指向已合并的旧页面（否则点了会先跳桩再跳，体验倒退）。"""
    assert 'href: "/strategy"' not in SRC_PALETTE, "仍指向 /strategy"
    assert 'href: "/exchange"' not in SRC_PALETTE, "仍指向 /exchange"
    assert 'href: "/control?tab=sessions"' in SRC_PALETTE
    assert 'href: "/control?tab=exchange"' in SRC_PALETTE
    assert 'href: "/control?tab=lanes&cfg=long&sub=prompts"' in SRC_PALETTE, "提示词直达丢"
