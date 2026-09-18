"""P2-7 回归：启动期「孤儿决策快照」检测必须真的跑、真的报。

事故背景（轮66 审计 P2-7）
-------------------------
`backend/services/full_auto_trading_service.py` 的启动健康摘要里有：

    _orphan = db.execute(
        "SELECT COUNT(*) FROM decision_snapshots ds WHERE ds.session_id IS NOT NULL "
        "AND ds.session_id NOT IN (SELECT id FROM full_auto_sessions)"
    ).scalar() if False else None

三处都错：
1. `if False else None` → 恒为 None，而且 `_orphan` **从未出现在任何日志里**
   （算了却不打印），孤儿快照长期不可见；
2. `decision_snapshots` 在 AnalyticsBase（`alpha_analytics`），
   `full_auto_sessions` 在 Core（`alpha_arena`）—— **两个物理库**，
   跨库 `NOT IN (SELECT ...)` 在单条 SQL 里不可能成立；
3. `db.execute("裸 SQL 字符串")` 在 SQLAlchemy 2.x 需要 `text()` 包装。

修法：Analytics 侧按 `session_id` 计数、Core 侧取合法 id，Python 里求差集
（`orphan_snapshot_stats()`），并真的把结果打进启动摘要。
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.full_auto_trading_service import (  # noqa: E402
    orphan_snapshot_stats,
)

_SRC = ROOT / "backend" / "services" / "full_auto_trading_service.py"


def _source() -> str:
    return _SRC.read_text(encoding="utf-8")


def _strip_comments(src: str) -> str:
    """去掉注释，避免匹配到「引用旧写法」的注释文字（本项目既有约定）。"""
    out = []
    for line in src.splitlines():
        if line.lstrip().startswith("#"):
            continue
        out.append(re.sub(r"\s+#\s.*$", "", line))
    return "\n".join(out)


# ── 纯函数行为 ────────────────────────────────────────────────


def test_zero_orphans_when_all_sessions_exist():
    total, ids = orphan_snapshot_stats({10: 5524, 12: 447}, {10, 12})
    assert (total, ids) == (0, [])


def test_counts_snapshots_not_sessions():
    """报的必须是「条数」，不是「几个 session_id」——运维要知道脏数据量级。"""
    total, ids = orphan_snapshot_stats({10: 5000, 99: 3, 98: 7}, {10})
    assert ids == [98, 99], "孤儿 id 必须升序且完整"
    assert total == 10, "10 条（3+7），不是 2 个 session"


def test_no_live_sessions_means_everything_is_orphan():
    total, ids = orphan_snapshot_stats({10: 5, 11: 6}, set())
    assert (total, ids) == (11, [10, 11])


def test_empty_and_none_inputs_are_safe():
    """查询失败时上游会传 {} / set()，不能抛异常拖垮启动。"""
    assert orphan_snapshot_stats({}, set()) == (0, [])
    assert orphan_snapshot_stats(None, None) == (0, [])
    assert orphan_snapshot_stats({}, {1, 2}) == (0, [])


def test_string_keys_are_coerced():
    """不同驱动的 row 可能给回字符串，比较前必须归一到 int。"""
    assert orphan_snapshot_stats({"10": 5, "99": 2}, {"10"}) == (2, [99])


# ── 源码级守卫 ────────────────────────────────────────────────


def test_no_disabled_if_false_expression_in_live_code():
    """`if False else None` 这类「就地禁用」不得留在可执行代码里。

    用 AST 找真正的 `IfExp(test=Constant(False))` 节点，而不是文本匹配：
    本文件的 `orphan_snapshot_stats()` docstring 和调用处注释都原文引用了旧写法，
    文本匹配会命中说明文字（本项目已多次踩过这个坑）。
    """
    tree = ast.parse(_source())
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.IfExp)
        and isinstance(node.test, ast.Constant)
        and node.test.value is False
    ]
    assert not offenders, f"仍有被 if False 就地禁用的表达式，行号: {offenders}"


def test_orphan_result_is_actually_logged():
    """算出来必须打印，否则等于没检测（这正是原缺陷的形态）。"""
    live = _strip_comments(_source())
    assert "孤儿决策快照" in live, "登录摘要里必须出现孤儿快照告警"
    assert re.search(r"if _orphan\s*:", live), "告警必须以 _orphan 非零为条件"
    assert "_orphan_ids" in live, "告警里要给出具体 session_id 便于清理"


def test_cross_db_query_is_split_into_two_steps():
    """跨库子查询必须拆成两步：Analytics 取 session_id，Core 取 id。"""
    live = _strip_comments(_source())
    assert "NOT IN (SELECT id FROM full_auto_sessions)" not in live, (
        "decision_snapshots 与 full_auto_sessions 不在同一个库，这种子查询不可能成立"
    )
    assert "SELECT session_id, COUNT(*) FROM decision_snapshots" in live, (
        "Analytics 侧要按 session_id 分组计数"
    )
    assert "SELECT id FROM full_auto_sessions" in live, "Core 侧要取合法 id"
    # 裸字符串 execute 在 SQLAlchemy 2.x 会抛，必须 text() 包装
    assert not re.search(r"\.execute\(\s*[\"']SELECT", live), (
        "execute() 的 SQL 必须用 sa_text() 包装"
    )


def test_analytics_part_runs_on_analytics_session():
    """snapshot 侧的查询必须挂在 AnalyticsSessionLocal 上，不能在 Core db 上跑。"""
    src = _source()
    idx_ana = src.index("SELECT session_id, COUNT(*) FROM decision_snapshots")
    idx_close = src.index("_ana_db.close()", idx_ana - 2000)
    assert idx_ana < idx_close, "计数查询必须在 _ana_db 关闭之前执行"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
