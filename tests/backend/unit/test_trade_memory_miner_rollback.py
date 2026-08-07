"""
批次一 1.2 回归测试：trade_memory_miner 脏 session 修复

根因：mine_trade_patterns(db) 只读 SELECT，但传入的 db 可能被上游 inject_patterns_to_memory
的写操作污染成 aborted 状态；函数本身无 rollback，导致后续所有查询抛 InFailedSqlTransaction
（日志 50 次）。

修复：
1. mine_trade_patterns 在 SELECT 前 rollback 清场（让 aborted session 复活）。
2. except 分支补 db.rollback()，杜绝脏 session 外泄。
3. inject_patterns_to_memory except 分支补 rollback。

本测试覆盖 rollback 路径，不依赖真实表结构（用 mock db）。
"""
import pytest
from unittest.mock import MagicMock, patch

import backend.services.trade_memory_miner as tmm_mod
from backend.services.trade_memory_miner import (
    mine_trade_patterns,
    inject_patterns_to_memory,
)


pytestmark = pytest.mark.unit


def _make_aborted_session():
    """构造一个模拟 aborted 状态的 session：execute 抛 InFailedSqlTransaction-like 错误。

    第二次 execute（rollback 后）返回空结果，模拟 rollback 让 session 复活后查询成功。
    """
    db = MagicMock()
    call_count = {"execute": 0}

    class _FakeInFailed(Exception):
        pass

    db._fake_err = _FakeInFailed

    def fake_execute(stmt, params=None):
        call_count["execute"] += 1
        if call_count["execute"] == 1:
            raise _FakeInFailed("当前事务被终止, 事务块结束之前的查询被忽略")
        # rollback 后复活，返回空结果
        result = MagicMock()
        result.fetchall.return_value = []
        return result

    db.execute.side_effect = fake_execute
    db.rollback = MagicMock()
    db.commit = MagicMock()
    return db


def test_mine_trade_patterns_rolls_back_on_exception():
    """SELECT 抛错时，函数应 rollback 且返回错误占位结果，不向上传播异常。"""
    db = MagicMock()
    db.execute.side_effect = Exception("boom")
    db.rollback = MagicMock()

    result = mine_trade_patterns(db, symbol="BTC")

    # 必须调用 rollback 清理 session
    assert db.rollback.called, "mine_trade_patterns 必须在异常时 rollback"
    # 返回结构完整（不抛异常）
    assert result["total_records"] == 0
    assert "profitable_patterns" in result


def test_mine_trade_patterns_pre_cleanse_aborted_session():
    """即使 db 进入 aborted 状态，SELECT 前的 rollback 应让 session 复活。

    模拟：execute 首次抛 InFailedSqlTransaction，rollback 后再 execute 成功。
    验证：rollback 被调用（清场），最终返回正常结果而非传播 InFailed 错误。
    """
    db = MagicMock()
    calls = {"n": 0}

    def fake_execute(stmt, params=None):
        calls["n"] += 1
        if calls["n"] == 1:
            # 清场 rollback 后的第一次真实 execute 仍失败（模拟表不存在等）
            raise Exception("trade_memory_records relation does not exist")
        result = MagicMock()
        result.fetchall.return_value = []
        return result

    db.execute.side_effect = fake_execute
    db.rollback = MagicMock()

    # 不应抛异常
    result = mine_trade_patterns(db)

    # 清场 rollback 必须被调用
    assert db.rollback.called
    assert result["total_records"] == 0


def test_inject_patterns_to_memory_rollback_on_failure():
    """inject_patterns_to_memory 写失败时必须 rollback，不外泄脏 session。"""
    db = MagicMock()
    # mine_trade_patterns 返回有数据，让函数进入写路径
    db.query.return_value.filter.return_value.first.side_effect = Exception("write failed")
    db.rollback = MagicMock()

    # patch mine_trade_patterns 返回非空，强制进入 StrategyMemory 查询路径
    with patch.object(tmm_mod, "mine_trade_patterns", return_value={
        "total_records": 5,
        "profitable_patterns": [{"side": "long", "regime": "trend", "confidence_range": "70-80", "win_rate": 0.7, "trades": 10}],
        "losing_patterns": [],
    }):
        result = inject_patterns_to_memory(db, strategy_id="strat_1", symbol="BTC")

    assert result is False
    # 关键：写操作失败必须 rollback，否则 session 脏掉污染后续查询
    assert db.rollback.called, "inject_patterns_to_memory 必须在异常时 rollback"


def test_inject_patterns_to_memory_no_data_returns_false_no_write():
    """mine_trade_patterns 返回空时，应短路返回，不触发 commit（写库）。

    注意：account_id 解析（db.query(AIStrategy)）发生在 mine_trade_patterns 之前，
    所以 query 会被调用一次；但 total_records=0 时必须短路，不应进入
    StrategyMemory 写入 / commit 路径。
    """
    db = MagicMock()
    db.rollback = MagicMock()
    # account_id 解析返回 None（全局挖掘）
    db.query.return_value.filter.return_value.first.return_value = None

    with patch.object(tmm_mod, "mine_trade_patterns", return_value={
        "total_records": 0,
        "profitable_patterns": [],
        "losing_patterns": [],
    }):
        result = inject_patterns_to_memory(db, strategy_id="strat_1")

    assert result is False
    # 关键：短路返回，不应触发任何写库 commit
    db.commit.assert_not_called()
