# -*- coding: utf-8 -*-
"""[2026-10-03] 用户两条新要求的护栏：

1. 「持仓里有手动平仓，但是没有一键平仓」⇒ 新增 `POST /api/paper/close-all/{account_id}`
   + `paper_engine.close_all_positions()`（逐笔复用唯一平仓入口 `close_position`，
   冷却/事件/盈亏口径与手动平仓同源）。
2. 「账户金额配置应该和账户重置绑定啊 —— 金额改了，但是仓位什么的不应该一起重置么？要不就乱了」
   ⇒ `set_initial_balance()` **默认 `reset_positions=True`**：改金额 = 以新金额做一次完整重置
   （清持仓/订单 + 水位重置 + 同步 accounts 行）。旧口径保留在 `reset_positions=False`。

## 顺带修掉的真 bug（本轮实测复现）
`position_exit_events.position_id → paper_positions.id` 是**无级联外键**，而 `reset_account`
（完整重置）与新的"改金额连带重置"原先都直接 `delete(paper_positions)` ⇒ 只要账户有过平仓历史
（必然写过退出事件），删除即 ForeignKeyViolation → 路由 500（用户口径的"重置不正常"）。
现在统一走 `_purge_account_trade_rows()`：**先删退出事件子表**，再删持仓/订单。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

SRC_ENGINE = (ROOT / "backend/services/paper_trading_engine.py").read_text(encoding="utf-8")
SRC_ROUTES = (ROOT / "backend/api/paper_trading_routes.py").read_text(encoding="utf-8")
SRC_PAGE = (ROOT / "frontend-next/src/app/paper-trading/page.tsx").read_text(encoding="utf-8")
SRC_API = (ROOT / "frontend-next/src/lib/api.ts").read_text(encoding="utf-8")


# ─────────────── 一、一键平仓 ───────────────

def test_close_all_route_exists_and_delegates_to_engine():
    assert '@router.post("/close-all/{account_id}")' in SRC_ROUTES, "缺一键平仓路由"
    seg = SRC_ROUTES.split('@router.post("/close-all/{account_id}")')[1][:600]
    assert "paper_engine.close_all_positions(" in seg
    assert "set_system_identity()" in seg, "白名单路径必须注入 system identity（RLS）"


def test_close_all_reuses_single_close_funnel():
    """必须逐笔复用 close_position —— 否则会出现第二条出场路径（冷却/事件/口径不同源）。"""
    body = SRC_ENGINE.split("def close_all_positions")[1].split("def reset_account")[0]
    assert "self.close_position(" in body, "未复用唯一平仓入口"
    assert "reason=reason" in body
    assert "closed_count" in body and "failed_count" in body


def test_close_all_counts_post_close_hook_failure_as_closed():
    """平仓后置钩子（冷却/事件/归因）可能在仓位已落库为 closed 之后才抛 ⇒ 必须复核真实状态，
    否则前端会把"其实已平掉"显示成"部分失败"。"""
    body = SRC_ENGINE.split("def close_all_positions")[1].split("def reset_account")[0]
    assert 'PaperPosition.id == pid, PaperPosition.status == "open"' in body, "缺平仓后的状态复核"
    assert "后置钩子异常但仓位已平" in body


def test_frontend_has_close_all_button_and_api():
    assert "一键平仓" in SRC_PAGE, "前端缺一键平仓按钮"
    assert "handleCloseAll" in SRC_PAGE
    assert "paperApi.closeAll(" in SRC_PAGE, "未接 API"
    assert "closeAll:" in SRC_API and "/paper/close-all/" in SRC_API, "api.ts 缺 closeAll"


# ─────────────── 二、改金额 = 连带重置（用户口径） ───────────────

def test_set_balance_defaults_to_resetting_positions():
    import inspect

    from backend.services.paper_trading_engine import paper_engine
    sig = inspect.signature(paper_engine.set_initial_balance)
    assert sig.parameters["reset_positions"].default is True, \
        "用户口径：改金额必须默认连带重置（reset_positions 默认 True）"
    assert "reset_positions: bool = True" in SRC_ROUTES, "路由层默认值也必须为 True"


def test_set_balance_reset_branch_purges_rows_and_syncs_account():
    body = SRC_ENGINE.split("def set_initial_balance")[1].split("def close_all_positions")[0]
    assert "_purge_account_trade_rows(db, account_id)" in body, "重置分支未清交易行"
    assert "acc.initial_capital = val" in body and "acc.current_cash = val" in body
    assert "bal.last_reset_at = datetime.now(timezone.utc)" in body, "缺水位重置"


# ─────────────── 三、外键顺序（本轮实测 500 的根因） ───────────────

def test_purge_deletes_exit_events_before_positions():
    body = SRC_ENGINE.split("def _purge_account_trade_rows")[1].split("def set_initial_balance")[0]
    ev = body.index("PositionExitEvent")
    pos = body.index("PaperPosition.account_id == account_id).delete(")
    assert ev < pos, "必须先删 position_exit_events 子表，否则无级联外键会让删除 500"
    assert "position_exit_events" in (ROOT / "backend/database/models.py").read_text(encoding="utf-8")


def test_reset_account_uses_the_same_purge_helper():
    body = SRC_ENGINE.split("def reset_account")[1].split("# ── 下单")[0]
    assert "_purge_account_trade_rows(db, account_id)" in body, "完整重置未走统一清理（FK 500 会回归）"
    assert "delete(synchronize_session=False)" not in body, "不得再直接 delete(paper_positions)"
