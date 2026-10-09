# -*- coding: utf-8 -*-
"""[2026-09-18 前端刷新慢] `/api/paper/orders` 不再全量物化持仓 —— 契约与对拍。

## 根因（实证）
`PaperTradingEngine.get_orders` 原实现为了给**极少数**缺 `entry_price` 的订单回退开仓价，
对**该账户全部** `paper_positions` 做 `.all()`（ORM 实体）：

```python
positions = db.query(PaperPosition).filter(
    PaperPosition.account_id == account_id
).all()          # 账号 14 = 3,124 行（open 仅 8、closed 3,116）
```

实测（账号 14，`scripts/profile_readonly_endpoints.py` / `anchor_paper_row_counts.py`）：
- cProfile 一次调用触发 **3,174 次 `_populate_full`** = 3,124 持仓 + 50 订单（数字精确吻合）；
- **84.5ms/次**，而"只取 8 条 open"只需 **1.39ms**（60×）；
- 该端点被前端**每 5s 轮询**（`useTradingData.ts`），且是进程 GIL 饱和期的主要排队者之一。

本文件锁四件事：
1. 无候选需求时**一次持仓查询都不发**（回退只在平仓单缺价时才需要）；
2. 有需求时按 `(symbol, side)` 精确配对、取 **6 列投影**（不建 ORM 实体）；
3. 侧向映射与解析器一致（sell→long / buy→short）；
4. 与旧实现**逐条 JSON 全等**（真实库对拍，库不可用时跳过）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.paper_trading_engine import paper_engine  # noqa: E402


# ─────────────────────── 伪造 db：记录是否发查询、发的是什么 ───────────────────────

class _FakeQuery:
    def __init__(self, recorder, args):
        self._rec = recorder
        self._args = args

    def filter(self, *criteria):
        self._rec["filters"].append(criteria)
        return self

    def all(self):
        self._rec["calls"] += 1
        return self._rec["rows"]


class _FakeDB:
    def __init__(self, rows=()):
        self.rec = {"calls": 0, "args": None, "filters": [], "rows": list(rows)}

    def query(self, *args):
        self.rec["args"] = args
        return _FakeQuery(self.rec, args)


class _Order:
    def __init__(self, symbol, side, close_reason=None, oid=1, strategy_id=None):
        self.id = oid
        self.symbol = symbol
        self.side = side
        self.close_reason = close_reason
        self.strategy_id = strategy_id


class _Row:
    """6 列投影行（属性访问，模拟 SQLAlchemy Row）。"""

    def __init__(self, symbol, side, strategy_id=None, entry_price=1.0,
                 opened_at=None, closed_at=None):
        self.symbol = symbol
        self.side = side
        self.strategy_id = strategy_id
        self.entry_price = entry_price
        self.opened_at = opened_at
        self.closed_at = closed_at


# ─────────────────────────────── 1. 无需求 ⇒ 零查询 ───────────────────────────────

def test_no_candidate_query_when_no_close_order():
    """开仓单 / 无 close_reason 的订单不可能用持仓候选 ⇒ 不得查库。"""
    db = _FakeDB()
    orders = [
        _Order("BTC", "buy"),                       # 开仓单：解析器直接返回 filled_price
        _Order("ETH", "sell", close_reason=None),   # 无 close_reason：同上
    ]
    got = paper_engine._load_entry_price_candidates(db, 14, orders)
    assert got == []
    assert db.rec["calls"] == 0, "无候选需求时仍发出了持仓查询（白跑 3,124 行）"


def test_empty_orders_never_touches_db():
    db = _FakeDB()
    assert paper_engine._load_entry_price_candidates(db, 14, []) == []
    assert db.rec["calls"] == 0


# ──────────────────────── 2. 有需求 ⇒ 投影查询 + 精确配对 ────────────────────────

def test_projection_query_and_pair_filtering():
    rows = [
        _Row("SUI", "long"),     # 命中
        _Row("SUI", "short"),    # 未命中（同一 symbol 的另一侧）
        _Row("BTC", "long"),     # 未命中（另一 symbol 的 long）
    ]
    db = _FakeDB(rows)
    orders = [_Order("SUI", "sell", close_reason="manual")]   # 平仓单 sell ⇒ long
    got = paper_engine._load_entry_price_candidates(db, 14, orders)

    assert db.rec["calls"] == 1
    assert [(r.symbol, r.side) for r in got] == [("SUI", "long")], \
        "未按 (symbol, side) 精确配对（会发生 (A,long)×(B,short) 交叉命中）"

    # 投影：必须是列对象，而不是 PaperPosition 实体
    from backend.database.models import PaperPosition
    args = db.rec["args"]
    assert len(args) == 6, f"应为 6 列投影，实际 {len(args)}"
    assert all(a is not PaperPosition for a in args), "仍在查询整个 ORM 实体"


@pytest.mark.parametrize("side,want", [("sell", "long"), ("buy", "short"),
                                       ("SELL", "long"), ("Buy", "short")])
def test_side_mapping_matches_resolver(side, want):
    db = _FakeDB([_Row("SUI", want)])
    orders = [_Order("SUI", side, close_reason="manual")]
    got = paper_engine._load_entry_price_candidates(db, 14, orders)
    assert got and got[0].side == want, f"{side} 应映射为 {want}（与解析器一致）"


# ──────────────────── 3. 源码钉桩：不得回退到全量 ORM 物化 ────────────────────

def test_get_orders_no_longer_loads_all_positions():
    src = (ROOT / "backend" / "services" / "paper_trading_engine.py").read_text(encoding="utf-8")
    start = src.index("def get_orders(")
    end = src.index("def _load_entry_price_candidates(")
    body = src[start:end]
    assert "_load_entry_price_candidates(" in body, "get_orders 未走最小列集路径"
    assert ".all()" in body  # 仍应有订单查询
    assert "db.query(PaperPosition)" not in body, \
        "get_orders 内又出现整实体持仓查询（3,124 行物化会回来）"


# ──────────────────── 4. 真实库对拍：新旧实现逐条 JSON 全等 ────────────────────

def _legacy_get_orders(db, account_id, status=None, limit=50):
    """旧实现原样复刻（对拍基线），只在本测试内使用。"""
    from backend.database.models import PaperOrder, PaperPosition
    q = db.query(PaperOrder).filter(PaperOrder.account_id == account_id)
    if status:
        q = q.filter(PaperOrder.status == status)
    orders = q.order_by(PaperOrder.id.desc()).limit(limit).all()
    positions = db.query(PaperPosition).filter(
        PaperPosition.account_id == account_id
    ).all()
    entry_fallback = paper_engine._build_entry_price_fallback(orders)
    result = []
    for o in orders:
        d = paper_engine._order_to_dict(o)
        if not d.get("entry_price"):
            d["entry_price"] = (entry_fallback.get(o.id)
                                or paper_engine._resolve_entry_from_positions(o, positions))
        result.append(d)
    return result


def _session_or_skip():
    try:
        from sqlalchemy import text
        from backend.database.connection import SessionLocal
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        return db
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"数据库不可用，跳过真实库对拍：{type(e).__name__}: {e}")


def test_parity_with_legacy_on_real_db():
    """真实库对拍：新实现必须与旧实现逐条全等（含 entry_price 回退值）。"""
    db = _session_or_skip()
    try:
        from sqlalchemy import text
        accts = [r[0] for r in db.execute(text(
            "SELECT DISTINCT account_id FROM paper_orders ORDER BY account_id LIMIT 5"
        )).fetchall()]
        assert accts, "库里没有订单，无法对拍"
        for acct in accts:
            new = paper_engine.get_orders(db, acct, None, 50)
            old = _legacy_get_orders(db, acct, None, 50)
            jn = json.dumps(new, sort_keys=True, default=str)
            jo = json.dumps(old, sort_keys=True, default=str)
            assert jn == jo, f"账号 {acct} 新旧结果不一致（口径漂移）"
    finally:
        db.close()


def test_legacy_path_is_actually_heavier_when_positions_exist():
    """守住改动的意义：有大量历史持仓的账户，旧路径确实更慢（否则本优化无对象）。"""
    db = _session_or_skip()
    try:
        from sqlalchemy import text
        row = db.execute(text(
            "SELECT account_id, count(*) c FROM paper_positions "
            "GROUP BY 1 ORDER BY c DESC LIMIT 1"
        )).fetchone()
        if not row or row[1] < 200:
            pytest.skip(f"最大账户持仓仅 {row[1] if row else 0} 行，样本不足以体现差异")
        acct, n_pos = row[0], row[1]
        import time
        t0 = time.perf_counter(); paper_engine.get_orders(db, acct, None, 50)
        t_new = time.perf_counter() - t0
        t0 = time.perf_counter(); _legacy_get_orders(db, acct, None, 50)
        t_old = time.perf_counter() - t0
        assert n_pos >= 200
        # 不强求倍数（受缓存/计划影响），但旧路径不应更快
        assert t_old >= t_new * 0.5, f"异常：旧={t_old*1000:.1f}ms 新={t_new*1000:.1f}ms（{n_pos} 行）"
    finally:
        db.close()
