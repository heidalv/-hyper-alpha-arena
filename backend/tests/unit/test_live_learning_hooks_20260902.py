"""D10 实盘学习闭环钩子测试（2026-09-02 因子系统闭环修复）。

守住三件事，它们各自对应一个已经发生过的断链：

  1. **接线存在**：实盘开仓/平仓必须真的调到钩子。此前 live 路径五个学习关键词
     零匹配，实盘样本一条不进 signal_trade_feedback；
  2. **幂等**：同一子仓位加仓时不得重复写快照，否则同一因子在同一 trade_id 下
     多行、被赋同一 pnl，IC 计算里该样本被悄悄加权；
  3. **单位口径**：pnl_pct 必须是 ROI 小数，与 paper 侧一致。写成百分数会让实盘
     样本比模拟盘大 100 倍，直接毁掉 IC 估计。
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

from backend.services import live_learning_hooks as llh


# ── 测试替身 ──────────────────────────────────────────────────────

class _FakeQuery:
    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def count(self):
        return len(self._rows)


class _FakeSession:
    """只实现钩子用到的 query/first/count 与上下文管理协议。"""

    def __init__(self, rows=()):
        self.rows = list(rows)

    def query(self, *a, **k):
        return _FakeQuery(self.rows)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _RecordingTracker:
    """记录 record_entry_signals / update_trade_pnl 的调用参数。"""

    def __init__(self):
        self.entries = []
        self.pnls = []

    def record_entry_signals(self, db, account_id, trade_id, symbol, side,
                             active_signals, factor_values=None):
        self.entries.append({
            "account_id": account_id, "trade_id": trade_id, "symbol": symbol,
            "side": side, "active_signals": active_signals,
            "factor_values": factor_values,
        })

    def update_trade_pnl(self, db, trade_id, pnl, pnl_pct):
        self.pnls.append({"trade_id": trade_id, "pnl": pnl, "pnl_pct": pnl_pct})


@pytest.fixture
def wired(monkeypatch):
    """把钩子的 DB 会话与 tracker 换成测试替身，返回 (tracker, set_rows)。"""
    import backend.database.connection as _conn
    import backend.services.signal_feedback_tracker as _sft

    tracker = _RecordingTracker()
    monkeypatch.setattr(_sft, "signal_feedback_tracker", tracker)
    holder = {"rows": []}
    monkeypatch.setattr(
        _conn, "SessionLocal", lambda: _FakeSession(holder["rows"]),
    )
    monkeypatch.setenv(llh._ENV_FLAG, "true")

    def _set_rows(n):
        holder["rows"] = [object()] * n

    return tracker, _set_rows


# ── 1. 开仓快照 ───────────────────────────────────────────────────

def test_entry_snapshot_records_factor_values(wired, monkeypatch):
    """正常路径：写入快照，trade_id 用子仓位 ID，情报信号留空只记因子。"""
    tracker, _ = wired
    monkeypatch.setattr(
        llh, "compute_factor_snapshot", lambda s, n="": {"rsi_14": 0.6, "atr": -0.2},
    )

    ok = llh.record_live_entry_snapshot(
        account_id=188, sub_position_id=4321, symbol="btc",
        position_side="buy", trade_nature="scalp",
    )

    assert ok is True
    assert len(tracker.entries) == 1
    e = tracker.entries[0]
    assert e["trade_id"] == 4321, "trade_id 必须是子仓位 ID，否则平仓回填匹配不上"
    assert e["symbol"] == "BTC", "symbol 需归一化为大写"
    assert e["side"] == "long", "buy 应映射为持仓方向 long"
    assert e["active_signals"] == {}, "实盘钩子只记因子，不做情报信号网络调用"
    assert e["factor_values"] == {"rsi_14": 0.6, "atr": -0.2}


def test_entry_snapshot_is_idempotent(wired, monkeypatch):
    """幂等：该 trade_id 已有快照行时跳过，防止加仓重复记账把样本加权。"""
    tracker, set_rows = wired
    monkeypatch.setattr(
        llh, "compute_factor_snapshot", lambda s, n="": {"rsi_14": 0.6},
    )
    set_rows(7)  # 已存在 7 行开仓快照

    ok = llh.record_live_entry_snapshot(
        account_id=188, sub_position_id=4321, symbol="BTC",
        position_side="long", trade_nature="scalp",
    )

    assert ok is False
    assert tracker.entries == [], "已有快照时不得二次写入"


def test_entry_snapshot_skips_without_position_id(wired, monkeypatch):
    """拒单/零成交（无子仓位 ID）没有可归因对象，直接跳过。"""
    tracker, _ = wired
    monkeypatch.setattr(
        llh, "compute_factor_snapshot", lambda s, n="": {"rsi_14": 0.6},
    )

    for bad in (None, 0, "", "abc"):
        assert llh.record_live_entry_snapshot(
            account_id=188, sub_position_id=bad, symbol="BTC",
            position_side="long",
        ) is False
    assert tracker.entries == []


def test_entry_snapshot_skips_when_factors_unavailable(wired, monkeypatch):
    """算不出因子时不写空快照（空行会在 IC 计算里被当成有效样本）。"""
    tracker, _ = wired
    monkeypatch.setattr(llh, "compute_factor_snapshot", lambda s, n="": None)

    assert llh.record_live_entry_snapshot(
        account_id=188, sub_position_id=99, symbol="BTC", position_side="long",
    ) is False
    assert tracker.entries == []


def test_hooks_can_be_disabled_by_env(wired, monkeypatch):
    """总开关关闭时两个钩子都不动作（出问题时的快速摘除通道）。"""
    tracker, _ = wired
    monkeypatch.setenv(llh._ENV_FLAG, "false")
    monkeypatch.setattr(
        llh, "compute_factor_snapshot", lambda s, n="": {"rsi_14": 0.6},
    )

    assert llh.record_live_entry_snapshot(
        account_id=188, sub_position_id=1, symbol="BTC", position_side="long",
    ) is False
    assert llh.backfill_live_close_pnl(sub_ids=[1], pnl=1.0, pnl_pct=0.01) == 0
    assert tracker.entries == [] and tracker.pnls == []


# ── 2. 平仓回填 ───────────────────────────────────────────────────

def test_backfill_updates_matching_rows(wired, monkeypatch):
    """回填按子仓位 ID 更新，并返回真实行数（行数是闭环是否通的唯一可观测信号）。"""
    tracker, set_rows = wired
    set_rows(3)

    n = llh.backfill_live_close_pnl(sub_ids=[11, 12], pnl=-2.5, pnl_pct=-0.037)

    assert n == 6, "两个子仓各 3 行快照 → 共回填 6 行"
    assert [p["trade_id"] for p in tracker.pnls] == [11, 12]
    assert tracker.pnls[0]["pnl"] == -2.5
    assert tracker.pnls[0]["pnl_pct"] == -0.037


def test_backfill_warns_when_no_snapshot(wired):
    """无开仓快照时不调用更新，返回 0（说明开仓钩子当时没生效）。"""
    tracker, set_rows = wired
    set_rows(0)

    assert llh.backfill_live_close_pnl(sub_ids=[11], pnl=1.0, pnl_pct=0.01) == 0
    assert tracker.pnls == []


def test_backfill_dedupes_and_ignores_bad_ids(wired):
    """重复/非法子仓位 ID 不应产生重复或异常更新。"""
    tracker, set_rows = wired
    set_rows(1)

    n = llh.backfill_live_close_pnl(
        sub_ids=[5, 5, None, 0, "x", 6], pnl=1.0, pnl_pct=0.01,
    )

    assert n == 2
    assert [p["trade_id"] for p in tracker.pnls] == [5, 6]


def test_backfill_rejects_non_finite_pnl(wired, monkeypatch):
    """NaN/Inf 盈亏拒绝回填：写进库会让该因子的 IC 整体变成 NaN。"""
    tracker, set_rows = wired
    set_rows(2)

    assert llh.backfill_live_close_pnl(
        sub_ids=[1], pnl=float("nan"), pnl_pct=0.01) == 0
    assert llh.backfill_live_close_pnl(
        sub_ids=[1], pnl=1.0, pnl_pct=float("inf")) == 0
    assert tracker.pnls == []


# ── 3. 因子快照过滤 ───────────────────────────────────────────────

def test_compute_snapshot_filters_non_finite(monkeypatch):
    """NaN/Inf 因子值必须被剔除，且支持 .value 包装与裸 float 两种返回。"""
    import backend.services.kline_data_service as _kds
    from backend.services.factor_engine import factor_engine as _fe

    class _V:
        def __init__(self, v):
            self.value = v

    monkeypatch.setattr(
        _kds.kline_service, "get_klines_from_db",
        lambda s, p, c: [{"close": 1.0}] * 100,
    )
    monkeypatch.setattr(_fe, "compute_all_factors", lambda df: {
        "good_wrapped": _V(0.5),
        "good_raw": -0.25,
        "nan_f": _V(float("nan")),
        "inf_f": float("inf"),
        "none_f": _V(None),
    })

    out = llh.compute_factor_snapshot("BTC", "scalp")

    assert out == {"good_wrapped": 0.5, "good_raw": -0.25}


def test_compute_snapshot_period_by_nature():
    """scalp 用 5m、其余用 15m —— 与 paper 侧同口径，否则实盘/模拟因子不可比。"""
    assert llh._period_for("scalp") == "5m"
    assert llh._period_for("swing") == "15m"
    assert llh._period_for("") == "15m"


def test_compute_snapshot_returns_none_without_klines(monkeypatch):
    import backend.services.kline_data_service as _kds
    monkeypatch.setattr(
        _kds.kline_service, "get_klines_from_db", lambda s, p, c: [],
    )
    assert llh.compute_factor_snapshot("BTC", "scalp") is None


# ── 4. 接线契约（防回归）──────────────────────────────────────────

def _live_code_lines(path):
    """返回去掉注释行的代码行，避免断言命中注释造成假绿。"""
    with open(path, encoding="utf-8") as fh:
        return [ln for ln in fh if not ln.strip().startswith("#")]


def test_live_executor_is_wired_to_entry_hook():
    """实盘开仓入口必须调用开仓钩子（D10 的核心接线）。"""
    src = "".join(_live_code_lines(
        os.path.join(os.path.dirname(__file__), "..", "..",
                     "services", "exchange", "live_executor.py")))
    assert "record_live_entry_snapshot" in src, (
        "live_executor 未接开仓学习钩子 —— 实盘样本不会进 signal_trade_feedback"
    )


def test_live_trade_facts_is_wired_to_backfill_hook():
    """实盘平仓落账处必须回填盈亏，且用 ROI 小数口径。"""
    path = os.path.join(os.path.dirname(__file__), "..", "..",
                        "services", "live_trade_facts.py")
    src = "".join(_live_code_lines(path))
    assert "backfill_live_close_pnl" in src, "live_trade_facts 未接平仓回填钩子"
    assert "avg_entry * closed_size" in src, (
        "pnl_pct 必须按 pnl/(avg_entry*closed_size) 算成 ROI 小数，"
        "与 paper 侧 _notify_learning_on_close 口径一致"
    )
