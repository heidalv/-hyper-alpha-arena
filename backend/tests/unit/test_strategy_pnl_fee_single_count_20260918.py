"""P2-8 回归：单策略 PnL 里平仓手续费只能扣一次。

事故背景（轮66 审计 P2-8）
-------------------------
`backend/services/full_auto_trading_service.py` 的 `_calc_strategy_stats()`：

1. 逐仓位累加时 `pos_pnl = ... + partial_pnl - partial_fee`（`partial_fee_paid`）
2. 循环之后又有 M1-2 的「漏扣全平费」补丁：`total_pnl -= _close_fees`，
   而 `_close_fees` 是**该策略全部** `close_reason` 非空订单的 fee 之和。

`paper_trading_engine.py:2515-2537` 在累加 `partial_fee_paid` 的同一次调用里
就写了 `PaperOrder(fee=partial_fee, close_reason=...)` —— 两者是**同一笔钱**的
两种记账，所以账本总额是仓位行分批费的**超集**，第 2 步把第 1 步扣过的钱又扣了一遍。

实测（account 14）：12 个有分批费的策略里旧口径共重复扣除 5.8274；
账户分批费合计 9.8754，而 close 订单 fee 合计 98.3977（超集，无例外）。

修法（轮92）：只补扣差额 `账本总额 − 仓位行已扣额`（差额即尚未计入的全平费），
差额为负时补 0（账本不完整时按更保守的仓位行口径，绝不倒加钱）。
win/loss 判定仍用含分批费的 `pos_pnl`，口径不变。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_SVC = ROOT / "backend" / "services" / "full_auto_trading_service.py"


def _strip_comments(src: str) -> str:
    out = []
    for line in src.splitlines():
        if line.lstrip().startswith("#"):
            continue
        out.append(re.sub(r"\s+#\s.*$", "", line))
    return "\n".join(out)


def _stats_fn(src: str) -> str:
    """截出 `_calc_strategy_stats` 的函数体（到下一个 `def _build_strategy_info`）。"""
    live = _strip_comments(src)
    start = live.index("def _calc_strategy_stats(")
    end = live.index("def _build_strategy_info(", start)
    return live[start:end]


# ── 源码级守卫 ────────────────────────────────────────────────


def test_fee_ledger_is_not_subtracted_wholesale():
    """`total_pnl -= _close_fees` 这种整额再扣必须消失。"""
    body = _stats_fn(_SVC.read_text(encoding="utf-8"))
    assert "total_pnl -= _close_fees" not in body, (
        "账本总额不得再整额扣一次 —— 分批费已被 pos_pnl 扣过"
    )
    assert re.search(r"total_pnl -= _extra_fee", body), "应改为只补扣差额"


def test_extra_fee_is_ledger_minus_already_deducted():
    body = _stats_fn(_SVC.read_text(encoding="utf-8"))
    assert re.search(
        r"_extra_fee = _close_fees - _partial_fee_in_positions", body
    ), "差额必须是「账本总额 − 仓位行已扣额」"


def test_partial_fee_is_accumulated_over_positions():
    body = _stats_fn(_SVC.read_text(encoding="utf-8"))
    assert re.search(r"_partial_fee_in_positions = 0\.0", body)
    assert re.search(r"_partial_fee_in_positions \+= partial_fee", body), (
        "必须逐仓位累加已扣的分批费，否则差额算不出来"
    )


def test_negative_difference_is_clamped():
    """账本不完整（差额为负）时补 0，绝不倒加钱。"""
    body = _stats_fn(_SVC.read_text(encoding="utf-8"))
    assert re.search(r"if _extra_fee < 0:", body)
    clamp = body.split("if _extra_fee < 0:", 1)[1]
    assert re.search(r"_extra_fee = 0\.0", clamp), "负差额必须钳到 0"
    assert "_extra_fee = 0.0" in clamp.split("total_pnl -=", 1)[0], (
        "钳位必须发生在扣减之前"
    )


def test_win_rate_basis_unchanged():
    """win/loss 判定仍用含分批费的 pos_pnl（修复只改 total_pnl，不改胜率口径）。"""
    body = _stats_fn(_SVC.read_text(encoding="utf-8"))
    assert re.search(r"pos_pnl = remain_pnl \+ partial_pnl - partial_fee", body)
    assert re.search(r"pos_pnl = cur_upnl \+ partial_pnl - partial_fee", body)
    assert re.search(r"if pos_pnl > 0:\s*\n\s*wins \+= 1", body)


def test_full_close_fee_still_deducted():
    """M1-2 的原始意图（补扣全平费）不得丢失：账本查询仍在，且差额为正时会被扣。"""
    body = _stats_fn(_SVC.read_text(encoding="utf-8"))
    assert "PaperOrder as _PO" in body
    assert "_PO.close_reason.isnot(None)" in body
    assert re.search(r"_PO\.close_reason\.notin_\(", body)
    assert re.search(r"total_pnl -= _extra_fee", body), "差额（含全平费）仍要扣"


# ── 前提校验（数据侧，跳过需连库的场景）────────────────────────


def test_ledger_is_a_superset_of_position_partial_fees():
    """数值前提：close 订单账本 fee 之和 ≥ 仓位行 partial_fee_paid 之和。

    若某天前提被打破（例如分批平仓不再写订单行），差额法就会失效 —— 此断言会先炸。
    """
    from sqlalchemy import text

    try:
        from backend.database.connection import SessionLocal
        db = SessionLocal()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"数据库不可用: {exc}")
    try:
        rows = db.execute(text(
            "SELECT p.strategy_id, "
            "COALESCE(SUM(p.partial_fee_paid),0) AS pos_fee, "
            "(SELECT COALESCE(SUM(o.fee),0) FROM paper_orders o "
            " WHERE o.strategy_id = p.strategy_id AND o.fee IS NOT NULL "
            " AND o.close_reason IS NOT NULL "
            " AND o.close_reason NOT IN ('','rejected','cancelled','pending')) AS ledger "
            "FROM paper_positions p WHERE p.account_id = 14 "
            "GROUP BY p.strategy_id HAVING COALESCE(SUM(p.partial_fee_paid),0) <> 0"
        )).fetchall()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"查询失败（表结构/权限）: {exc}")
    finally:
        db.close()

    if not rows:
        pytest.skip("没有带分批费的仓位，前提暂不可验")
    bad = [(r[0], float(r[1]), float(r[2])) for r in rows if float(r[2]) < float(r[1])]
    assert not bad, f"账本不再是分批费超集，差额法失效: {bad}"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
