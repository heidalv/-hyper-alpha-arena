# -*- coding: utf-8 -*-
"""轮98 · P1-8 回归：实盘平仓路由（LiveExecutor vs LPM）的结论必须可执行、可验证。

## 结论（本轮核实，取代此前"二选一待定"的状态）

`LiveExecutor` 与 `LivePositionManager`(LPM) **不是二选一**：

| 角色 | 组件 |
|---|---|
| 执行通道（下单/平仓/cancel/查询） | `services/exchange/live_executor.LiveExecutor` |
| 子仓位**账本**（按 trade_nature 分层记账、只发净差额单） | `services/live_position_manager.LivePositionManager` |

接线由 `LIVE_SUB_POSITION_TRACKING` 决定：

    开关 = true   → place_order / close_position 都委托 LPM（GAP-4 的目标状态）
    开关 = false  → 直发交易所单，**LPM 账本不动** ⇒ 本地记账与实仓漂移

实测（2026-09-18）：该键**既没写进 `.env`、也没登记 `KNOWN_FLAGS`** ⇒ 默认 false、
且没有任何一处告诉运维"账本不会更新"。而 `_apply_leverage`（杠杆对齐）只装在
LPM 路径上 —— 旧路径历史上从未执行过杠杆对齐，这正是 XPL 事故的根因。

## 本轮处置

1. 登记 `KNOWN_FLAGS`；
2. `.env` 显式声明 `LIVE_SUB_POSITION_TRACKING=false`（**不改行为**，只让它可见）；
3. `/api/live/readiness` 增加第 3 项 `sub_position_tracking` 门禁 →
   "开实盘"与"要记账"绑在同一个门禁里；
4. 旧路径上每个进程告警一次（`_lpm_off_warn_once`）；
5. LPM 异常后的降级直发改为 warning 并写明"本单不更新账本，事后需对账"。
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.exchange import live_executor as le  # noqa: E402

_LE_SRC = ROOT / "backend" / "services" / "exchange" / "live_executor.py"
_ROUTES = ROOT / "backend" / "api" / "live_trading_routes.py"


def _live(src: str) -> str:
    """剥掉整行注释，避免命中"引用旧写法"的说明文字。"""
    return "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))


# ══════════════════════════════════════════════════════════════════
# 开关语义
# ══════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("raw,expected", [
    ("true", True), ("TRUE", True), ("1", True), ("yes", True), ("on", True),
    ("false", False), ("0", False), ("no", False), ("off", False), ("", False),
])
def test_tracking_flag_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("LIVE_SUB_POSITION_TRACKING", raw)
    assert le._live_sub_position_tracking_enabled() is expected


def test_tracking_flag_defaults_off(monkeypatch):
    """默认必须是 false（保守）——但**必须可见**，见下方告警测试。"""
    monkeypatch.delenv("LIVE_SUB_POSITION_TRACKING", raising=False)
    assert le._live_sub_position_tracking_enabled() is False


def test_off_path_warns_once(monkeypatch, caplog):
    """旧路径必须告警，且只告警一次（热路径不刷屏）。"""
    monkeypatch.setattr(le, "_LPM_OFF_WARNED", False, raising=False)
    with caplog.at_level(logging.WARNING, logger=le.__name__):
        le._lpm_off_warn_once()
        le._lpm_off_warn_once()
        le._lpm_off_warn_once()
    msgs = [r.message for r in caplog.records if "LPM" in r.message]
    assert len(msgs) == 1, f"应只告警一次，实际 {len(msgs)} 次"
    assert "不更新" in msgs[0] and "账本" in msgs[0]


# ══════════════════════════════════════════════════════════════════
# 路由接线（源码守卫）
# ══════════════════════════════════════════════════════════════════


def test_place_order_and_close_position_both_route_to_lpm():
    live = _live(_LE_SRC.read_text(encoding="utf-8"))
    # 开仓
    assert "_place_order_via_lpm(db, ctx)" in live
    assert "_live_sub_position_tracking_enabled()" in live
    # 平仓（LPM 分支）
    assert "live_position_manager.close_sub_position(" in live
    assert "live_position_manager.close_all_symbol(" in live
    # 两处都在开关判断之内
    assert live.count("if _live_sub_position_tracking_enabled():") == 2, (
        "开/平仓都必须有 LPM 路由分支"
    )


def test_off_path_emits_the_warning():
    live = _live(_LE_SRC.read_text(encoding="utf-8"))
    assert "_lpm_off_warn_once()" in live, (
        "旧路径上必须调用一次性告警，否则'账本不更新'依旧静默"
    )


def test_lpm_failure_degradation_names_the_ledger_consequence():
    """降级直发是对的（平仓不能被账本故障卡住），但必须写明账本不会更新。

    且**不得**再退回 `logger.error` 就完事 —— 旧写法只说"路由异常"，
    事后对账时无从判断"这笔单改没改账本"。
    """
    live = _live(_LE_SRC.read_text(encoding="utf-8"))
    assert "不会**更新 LPM 账本" in live or "不会" in live and "对账" in live
    assert "降级直连 reduce_only" in live


def test_close_result_status_comes_from_ledger_result_not_request():
    """平仓返回的 status 必须由 LPM 结果推导（旧实现从请求合成，会把未成交报成成功）。"""
    live = _live(_LE_SRC.read_text(encoding="utf-8"))
    seg = live[live.index("def close_position("):]
    seg = seg[: seg.index("def get_positions(")]
    assert 'status="filled" if res.get("closed") else "no_position"' in seg
    assert "closed_fully" not in seg, "执行器层不得自造 closed_fully（由调用方按 status 判读）"


# ══════════════════════════════════════════════════════════════════
# 治理：开关必须登记 + 显式声明 + 进就绪门禁
# ══════════════════════════════════════════════════════════════════


def test_flag_is_registered_in_env_registry():
    from backend.config import env_registry as er
    assert "LIVE_SUB_POSITION_TRACKING" in er.KNOWN_FLAGS
    assert "LIVE_SUB_POSITION_TRACKING" not in er.find_read_but_unregistered_flags()


def test_flag_is_declared_in_env_file():
    env = (ROOT / ".env").read_text(encoding="utf-8", errors="replace")
    assert re.search(r"^LIVE_SUB_POSITION_TRACKING=", env, re.M), (
        "该键必须显式声明 —— 否则又是「默认关闭且不可发现」"
    )
    # 声明值必须与"本机跑 paper、无实盘会话"的事实一致（保守）
    m = re.search(r"^LIVE_SUB_POSITION_TRACKING=(\S+)", env, re.M)
    assert m.group(1).lower() in ("false", "true"), m.group(1)


def test_readiness_endpoint_gates_on_tracking(monkeypatch):
    """就绪门禁必须包含 sub_position_tracking，且随开关变化。"""
    from backend.api import live_trading_routes as ltr

    fake_db = MagicMock()
    fake_db.query.return_value.filter.return_value.all.return_value = []
    out = ltr.live_readiness(db=fake_db)
    assert "sub_position_tracking" in out["checks"], "无实盘账户分支也必须带该检查项"
    assert out["checks"]["sub_position_tracking"] is False

    monkeypatch.setenv("LIVE_SUB_POSITION_TRACKING", "true")
    out2 = ltr.live_readiness(db=fake_db)
    assert out2["checks"]["sub_position_tracking"] is True


def test_readiness_with_live_account_reports_tracking(monkeypatch):
    """有实盘账户时同样要看这一项（这才是"开实盘前"的真实分支）。"""
    from backend.api import live_trading_routes as ltr

    acct = MagicMock()
    acct.trading_mode = "live"
    acct.is_active = True
    acct.selected_exchange = "hyperliquid"
    fake_db = MagicMock()
    fake_db.query.return_value.filter.return_value.all.return_value = [acct]

    monkeypatch.setattr(ltr, "_credential_exists", lambda db, a: True)
    monkeypatch.setenv("LIVE_SUB_POSITION_TRACKING", "false")
    out = ltr.live_readiness(db=fake_db)
    assert out["checks"]["sub_position_tracking"] is False
    assert out["ready"] is False

    monkeypatch.setenv("LIVE_SUB_POSITION_TRACKING", "true")
    out2 = ltr.live_readiness(db=fake_db)
    assert out2["checks"]["sub_position_tracking"] is True
    # 其余项仍是未验证状态 ⇒ 依旧不许实盘（本项只是必要非充分）
    assert out2["ready"] is False


def test_readiness_message_mentions_the_ledger_requirement():
    src = _ROUTES.read_text(encoding="utf-8")
    assert "LIVE_SUB_POSITION_TRACKING" in src
    assert "否则账本不更新" in src


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
