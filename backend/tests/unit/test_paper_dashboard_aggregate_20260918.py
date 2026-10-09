# -*- coding: utf-8 -*-
"""[2026-09-18 前端刷新慢] 模拟盘「一屏一次请求」聚合端点 —— 单一来源与口径对拍。

## 为什么加这个端点（实测）
真实访问日志 15,034 条请求里 `/paper/*` 占 **50.7%**（balance/positions/orders/summary
各 5s 一次轮询），而该进程 GIL 长期贴 1 核（`backend/services/gil_watch.py` 实测：
无请求时进程 CPU 中位 ≈99%）——**请求条数本身就是排队成本**。

## 本文件锁四件事（否则聚合会变成第二个真相来源）
1. **单一来源**：四个分节的实现只有一份（helper），四个单端点与聚合端点都调用它；
   `paper_engine.get_*(...)` 的直接调用必须**只出现在 helper 内**（出现第 5 处即失败）。
2. **口径对拍**：聚合的四节与四个单端点各自返回的载荷**逐字节 JSON 全等**（真实库）。
3. **未初始化账户语义**：聚合返回 `balance=null` + `warnings`，**不整体 4xx**
   （其余三节仍有意义，且前端本就用 `!balance` 显示初始化引导）；
   而单端点 `/balance/{id}` 仍必须是 **404**（不因聚合而改口径）。
4. **形状**：聚合一次返回四节 + `positions_status` + `generated_at`。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.api import paper_trading_routes as R  # noqa: E402

SRC = (ROOT / "backend" / "api" / "paper_trading_routes.py").read_text(encoding="utf-8")


# ─────────────────────── 1. 单一来源（源码钉桩） ───────────────────────

def _body(fn_name: str) -> str:
    i = SRC.index(f"def {fn_name}(")
    j = SRC.find("\n@router", i)
    return SRC[i: j if j != -1 else len(SRC)]


@pytest.mark.parametrize("endpoint,helper", [
    ("get_paper_balance", "_paper_balance_payload"),
    ("get_paper_positions", "_paper_positions_payload"),
    ("get_paper_orders", "_paper_orders_payload"),
    ("get_paper_summary", "_paper_summary_payload"),
    ("get_paper_dashboard", "_paper_balance_payload"),
])
def test_endpoints_delegate_to_shared_helpers(endpoint, helper):
    body = _body(endpoint)
    assert helper in body, f"{endpoint} 未调用共享 helper {helper}（口径出现第二个来源）"


def test_engine_calls_live_only_inside_helpers():
    """`paper_engine.get_*` 的直接调用只允许出现在 4 个 helper 内。"""
    import re
    hits = [(m.start(), m.group(0)) for m in
            re.finditer(r"paper_engine\.(get_balance|get_positions|get_orders|get_summary)\(", SRC)]
    assert len(hits) == 4, f"应为 4 处（每个 helper 一处），实际 {len(hits)} 处：{hits}"

    helper_spans = []
    for h in ("_paper_balance_payload", "_paper_positions_payload",
              "_paper_orders_payload", "_paper_summary_payload"):
        i = SRC.index(f"def {h}(")
        j = SRC.find("\ndef ", i + 1)
        helper_spans.append((i, j if j != -1 else len(SRC)))
    for pos, call in hits:
        assert any(a <= pos < b for a, b in helper_spans), \
            f"{call} 出现在 helper 之外（pos={pos}）——聚合口径会与单端点分叉"


def test_dashboard_is_registered_route():
    assert '@router.get("/dashboard/{account_id}")' in SRC


# ─────────────────────── 2/3/4. 真实库对拍 ───────────────────────

def _session_or_skip():
    try:
        from sqlalchemy import text
        from backend.database.connection import SessionLocal
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        return db
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"数据库不可用，跳过：{type(e).__name__}: {e}")


def _accounts_with_orders(db, limit=4):
    from sqlalchemy import text
    rows = db.execute(text(
        "SELECT DISTINCT account_id FROM paper_orders ORDER BY account_id LIMIT :n"
    ), {"n": limit}).fetchall()
    return [r[0] for r in rows]


def test_aggregate_shape_and_single_request():
    db = _session_or_skip()
    try:
        accts = _accounts_with_orders(db)
        if not accts:
            pytest.skip("库里没有订单")
        got = R.get_paper_dashboard(accts[0], "open", db)
    finally:
        db.close()
    for k in ("account_id", "balance", "positions", "orders", "summary",
              "positions_status", "generated_at"):
        assert k in got, f"聚合响应缺字段 {k}"
    assert got["positions_status"] == "open"
    assert isinstance(got["positions"], list) and isinstance(got["orders"], list)


def test_aggregate_sections_equal_standalone_endpoints():
    """逐账户对拍：聚合四节 == 四个单端点各自的载荷（JSON 全等）。"""
    db = _session_or_skip()
    try:
        accts = _accounts_with_orders(db)
        assert accts, "库里没有订单，无法对拍"
        for acct in accts:
            agg = R.get_paper_dashboard(acct, "open", db)
            one = {
                "balance": R.get_paper_balance(acct, db),
                "positions": R.get_paper_positions(acct, "open", db),
                "orders": R.get_paper_orders(acct, None, 50, db),
                "summary": R.get_paper_summary(acct, db),
            }
            for name, value in one.items():
                ja = json.dumps(agg[name], sort_keys=True, default=str)
                jo = json.dumps(value, sort_keys=True, default=str)
                assert ja == jo, f"账号 {acct} 的 {name} 聚合值与单端点不一致（口径漂移）"
    finally:
        db.close()


def test_uninitialized_account_returns_null_balance_not_4xx():
    """未初始化账户：聚合给 null + warnings（不整体 4xx），且不影响其余三节。"""
    db = _session_or_skip()
    try:
        acct = 99_000_001          # 不存在的账户
        agg = R.get_paper_dashboard(acct, "open", db)
        assert agg["balance"] is None
        assert agg.get("warnings"), "未初始化必须显式说明（禁止静默给 null）"
        assert agg["positions"] == [] and agg["orders"] == []
        assert isinstance(agg["summary"], dict)
    finally:
        db.close()


def test_standalone_balance_still_404_for_uninitialized():
    """单端点语义不因聚合而改变：仍未初始化 ⇒ 404。"""
    from fastapi import HTTPException
    db = _session_or_skip()
    try:
        with pytest.raises(HTTPException) as ei:
            R.get_paper_balance(99_000_001, db)
        assert ei.value.status_code == 404, "单端点 /balance 的 404 语义被改动"
    finally:
        db.close()
