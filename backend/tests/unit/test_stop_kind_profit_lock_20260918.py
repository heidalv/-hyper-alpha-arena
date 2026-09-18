# -*- coding: utf-8 -*-
"""轮97 回归：「盈利单按止损出」必须能被分清是**锁利型止损**还是**保护型止损**。

## 用户提问（2026-09-18）

> 盈利单，你按照止损出了是怎么回事

## 实测答案

系统里只有**一条**止损线 `sl_price`，它同时承担两种角色：

| 角色 | 位置 | 成交时 |
|---|---|---|
| 保护型止损 | 成本**亏损侧** | 真亏 → `reason='sl'` |
| 锁利型止损 | 成本**盈利侧** | 回吐到该线 → `pnl>0` → 被 `normalize_close_reason` 改写成 `breakeven_tp` |

account 14 近 30 天实测：

| close_reason | 笔数 | 锁利型 | 保护型 | 锁利型 PnL |
|---|---|---|---|---|
| `breakeven_tp` | 147 | **147** | 0 | +533.74 |
| `sl` | 218 | 82 | 136 | −8.69 |

⇒ **没有任何一笔通过 TP 目标止盈**；账本里的"止盈"其实全是止损线成交。

## 本轮处置

`close_reason` 字符串**不动**（`edge_ledger` / `reentry_cooldown` / channel breaker
都按字符串匹配，改字符串=改行为），改为**附加可读字段**：

- `paper_trading_engine.stop_kind()` / `stop_vs_entry_pct()`；
- 退出事件 `metadata_json` 增 `stop_kind` + `stop_vs_entry_pct`；
- 硬线成交日志增 `[锁利型止损 止损位在成本+2.50%处]` / `[保护型止损 …]`；
- 持仓/成交 API 派生 `stop_kind` + `stop_vs_entry_pct`。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.full_auto.paper_session_helpers import (  # noqa: E402
    _stop_kind_of,
    _stop_vs_entry_pct_of,
)
from backend.services.paper_trading_engine import paper_engine  # noqa: E402

_PTE = ROOT / "backend" / "services" / "paper_trading_engine.py"
_PSH = ROOT / "backend" / "services" / "full_auto" / "paper_session_helpers.py"


class _Pos:
    def __init__(self, entry, sl, side):
        self.entry_price = entry
        self.sl_price = sl
        self.side = side


def _live(src: str) -> str:
    return "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))


# ══════════════════════════════════════════════════════════════════
# 判定本身
# ══════════════════════════════════════════════════════════════════


def test_long_stop_above_entry_is_profit_lock():
    p = _Pos(entry=100.0, sl=102.5, side="long")
    assert paper_engine.stop_kind(p) == "profit_lock"
    assert paper_engine.stop_vs_entry_pct(p) == pytest.approx(2.5)


def test_long_stop_below_entry_is_protective():
    p = _Pos(entry=100.0, sl=97.0, side="long")
    assert paper_engine.stop_kind(p) == "protective"
    assert paper_engine.stop_vs_entry_pct(p) == pytest.approx(-3.0)


def test_short_side_is_mirrored():
    assert paper_engine.stop_kind(_Pos(100.0, 98.0, "short")) == "profit_lock"
    assert paper_engine.stop_kind(_Pos(100.0, 103.0, "short")) == "protective"
    assert paper_engine.stop_vs_entry_pct(_Pos(100.0, 98.0, "short")) == pytest.approx(2.0)


def test_missing_info_returns_empty_not_a_guess():
    """信息不足时必须返回空串/None —— 绝不猜一个性质出来。"""
    assert paper_engine.stop_kind(_Pos(0.0, 100.0, "long")) == ""
    assert paper_engine.stop_kind(_Pos(100.0, 0.0, "long")) == ""
    assert paper_engine.stop_vs_entry_pct(_Pos(0.0, 100.0, "long")) is None
    assert paper_engine.stop_kind(None) == ""


def test_exactly_at_entry_is_protective():
    """止损正好等于入场价（保本线）⇒ 归保护型：它不锁任何利润。"""
    assert paper_engine.stop_kind(_Pos(100.0, 100.0, "long")) == "protective"


def test_api_helpers_agree_with_engine():
    """展示层与引擎层必须同口径（否则 UI 与事件流会互相打架）。"""
    for entry, sl, side in ((100.0, 102.5, "long"), (100.0, 97.0, "long"),
                            (100.0, 98.0, "short"), (100.0, 103.0, "short")):
        p = _Pos(entry, sl, side)
        assert _stop_kind_of(p) == paper_engine.stop_kind(p), (entry, sl, side)
        assert _stop_vs_entry_pct_of(p) == paper_engine.stop_vs_entry_pct(p), (entry, sl, side)


# ══════════════════════════════════════════════════════════════════
# 接线（不是"写了函数没人用"）
# ══════════════════════════════════════════════════════════════════


def test_exit_event_metadata_carries_stop_kind():
    src = _PTE.read_text(encoding="utf-8")
    assert "stop_kind" in src and 'setdefault("stop_kind"' in src
    # 事件落库必须用带标记的 _meta，而不是原始 metadata
    assert 'metadata_json=json.dumps(_meta' in src, (
        "退出事件仍写原始 metadata ⇒ stop_kind 不会进事件流"
    )
    assert "_meta = dict(metadata or {})" in src


def test_fast_path_log_distinguishes_lock_and_protective():
    live = _live(_PTE.read_text(encoding="utf-8"))
    assert "锁利型止损" in live and "保护型止损" in live
    # 文案必须带百分比，否则运维仍看不出止损位在哪一侧
    assert "止损位在成本" in live


def test_normalize_close_reason_is_not_changed_to_a_new_string():
    """`close_reason` 字符串必须保持原样：改它会连带改 edge_ledger / 冷却 / breaker 的行为。

    本测试锁住"改标签"这个冲动：`breakeven_tp` 仍然由 `sl`+盈利改写而来。
    """
    assert paper_engine.normalize_close_reason("sl", 3.0) == "breakeven_tp"
    assert paper_engine.normalize_close_reason("sl", -3.0) == "sl"
    live = _live(_PTE.read_text(encoding="utf-8"))
    assert 'return "breakeven_tp"' in live


def test_docstring_records_the_measured_split():
    """把实测口径写进代码，避免下一个人又把 breakeven_tp 当"止盈通道"。"""
    src = _PTE.read_text(encoding="utf-8")
    assert "147" in src and "锁利型" in src and "TP 目标" in src


def test_api_serializer_emits_the_derived_fields():
    live = _live(_PSH.read_text(encoding="utf-8"))
    assert '"stop_kind": _stop_kind_of(p)' in live
    assert '"stop_vs_entry_pct": _stop_vs_entry_pct_of(p)' in live


# ══════════════════════════════════════════════════════════════════
# 数据侧：这条口径确实能解释用户看到的现象
# ══════════════════════════════════════════════════════════════════


def test_breakeven_tp_closes_are_all_profit_lock_in_live_db():
    """活库核验：`breakeven_tp` 的止损位**全部**在成本盈利侧（≥30 笔才断言）。

    这条不变量是"breakeven_tp 其实是锁利型止损"的直接证据；
    若将来出现"止损位在亏损侧却记 breakeven_tp"的行，说明又混进了别的语义。
    """
    from sqlalchemy import text

    try:
        from backend.database.connection import SessionLocal
        db = SessionLocal()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"数据库不可用: {exc}")
    try:
        rows = db.execute(text(
            "SELECT COUNT(*) n, "
            "SUM(CASE WHEN (side='long' AND sl_price > entry_price) "
            "       OR (side='short' AND sl_price < entry_price) THEN 1 ELSE 0 END) lock_n "
            "FROM paper_positions WHERE account_id=14 AND status IN ('closed','liquidated') "
            "AND closed_at > now() - interval '30 days' AND close_reason='breakeven_tp' "
            "AND sl_price IS NOT NULL AND entry_price > 0"
        )).fetchone()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"查询失败: {exc}")
    finally:
        db.close()
    n, lock_n = int(rows[0] or 0), int(rows[1] or 0)
    if n < 30:
        pytest.skip(f"样本不足（{n} 笔）")
    assert lock_n == n, (
        f"{n} 笔 breakeven_tp 里有 {n - lock_n} 笔止损位在亏损侧 —— "
        f"说明 breakeven_tp 已混入非锁利语义，需要重新梳理"
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
