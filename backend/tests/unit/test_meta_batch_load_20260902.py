"""F15 meta 训练分批读取测试（2026-09-02 因子系统闭环修复）。

原病症：``_load_settled_rows`` 用单条 ``fetchall()`` 拉 35.2 万行含
``features_json`` 大字段，并**在同一事务内**逐行解 JSON。事务长时间 idle，撞上
``DB_IDLE_IN_TXN_TIMEOUT_MS``（120 秒）被服务端断开，报 "consuming input failed:
server closed the connection unexpectedly"，整轮 meta 训练失败 —— meta 不可用会
直接影响 pwin 仲裁。

本测试锁定三件事：确实分了批；每批用独立会话且取完即关；JSON 解码发生在会话
**关闭之后**（这是超时的直接原因，也是最容易在重构中被改回去的一点）。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

from backend.services import scalp_meta_trainer as smt


_NOW = datetime(2026, 9, 2, 12, 0, 0)


class _Row:
    """scalp_signal_log 的最小行替身。"""

    def __init__(self, created_at, features_json='{"a": 1}', probe=None):
        self.id = 1
        self.signal_ts = int(created_at.timestamp())
        self.settle_ts = self.signal_ts + 1800
        self.created_at = created_at
        self.symbol = "BTC"
        self.direction = "long"
        self.factor_score = 30.0
        self.win = True
        self.fwd_ret = 0.001
        self.net_ret = 0.0005
        self.horizon_sec = 1800
        self._features_json = features_json
        self._probe = probe

    @property
    def features_json(self):
        # 解码时会读取本属性 —— 借此记录"读取发生时会话是否已关闭"
        if self._probe is not None:
            self._probe.append(self._probe.session_open)
        return self._features_json


class _Probe(list):
    """记录每次 features_json 读取时是否还有会话处于打开状态。"""

    session_open = False


class _FakeResult:
    def __init__(self, rows, scalar=None):
        self._rows = rows
        self._scalar = scalar

    def fetchall(self):
        return self._rows

    def scalar(self):
        return self._scalar


class _FakeSession:
    def __init__(self, registry, probe):
        self.registry = registry
        self.probe = probe

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if "SELECT NOW()" in sql:
            return _FakeResult([], scalar=_NOW)
        self.registry["windows"].append((params["w_start"], params["w_end"]))
        rows = [
            r for r in self.registry["all_rows"]
            if params["w_start"] <= r.created_at < params["w_end"]
        ]
        self.registry["fetched"] += len(rows)
        return _FakeResult(rows)

    def __enter__(self):
        self.registry["opened"] += 1
        self.probe.session_open = True
        return self

    def __exit__(self, *a):
        self.registry["closed"] += 1
        self.probe.session_open = False
        return False


@pytest.fixture
def wired(monkeypatch):
    """替换 SessionLocal，返回 (registry, probe, set_rows)。"""
    import backend.database.connection as _conn

    probe = _Probe()
    registry = {
        "opened": 0, "closed": 0, "fetched": 0,
        "windows": [], "all_rows": [],
    }
    monkeypatch.setattr(
        _conn, "SessionLocal", lambda: _FakeSession(registry, probe),
    )

    def _set_rows(rows):
        registry["all_rows"] = rows

    return registry, probe, _set_rows


# ── 1. 分批行为 ───────────────────────────────────────────────────

def test_load_is_batched_by_window(wired, monkeypatch):
    """60 天按 5 天/批 → 12 批查询，而不是一次 fetchall。"""
    registry, _, _ = wired
    monkeypatch.setenv("SCALP_META_LOAD_WINDOW_DAYS", "5")

    smt._load_settled_rows()

    assert len(registry["windows"]) == 12, (
        f"应切成 12 批，实际 {len(registry['windows'])} 批"
    )
    # 基准时间查询 + 12 批数据查询 = 13 次会话，且全部关闭
    assert registry["opened"] == 13
    assert registry["closed"] == 13, "每批会话都必须关闭（连接及时归还）"


def test_window_size_configurable(wired, monkeypatch):
    registry, _, _ = wired
    monkeypatch.setenv("SCALP_META_LOAD_WINDOW_DAYS", "10")

    smt._load_settled_rows()

    assert len(registry["windows"]) == 6


def test_window_days_is_clamped(monkeypatch):
    """窗口天数越界要收敛，避免 0/负数造成死循环。"""
    for raw, expect in (("0", 1), ("-3", 1), ("999", 60), ("abc", 5), ("", 5)):
        monkeypatch.setenv("SCALP_META_LOAD_WINDOW_DAYS", raw)
        assert smt._load_window_days() == expect


# ── 2. 窗口无重叠、无缝隙 ─────────────────────────────────────────

def test_windows_are_contiguous_and_non_overlapping(wired, monkeypatch):
    """半开区间首尾相接：批边界既不重复取行，也不漏行。"""
    registry, _, _ = wired
    monkeypatch.setenv("SCALP_META_LOAD_WINDOW_DAYS", "5")

    smt._load_settled_rows()

    wins = registry["windows"]
    for (s1, e1), (s2, e2) in zip(wins, wins[1:]):
        assert e1 == s2, "相邻窗口必须首尾相接（漂移会造成重复或漏读）"
        assert s1 < e1
    assert wins[0][0] == _NOW - timedelta(days=60)
    assert wins[-1][1] >= _NOW, "末批必须覆盖到基准时刻"


def test_boundary_row_read_exactly_once(wired, monkeypatch):
    """恰好落在批边界与基准时刻的行各自只被读到一次。"""
    registry, _, set_rows = wired
    monkeypatch.setenv("SCALP_META_LOAD_WINDOW_DAYS", "5")
    set_rows([
        _Row(_NOW - timedelta(days=60)),   # 区间左端
        _Row(_NOW - timedelta(days=55)),   # 批边界
        _Row(_NOW - timedelta(days=30)),
        _Row(_NOW),                        # 基准时刻（末批余量覆盖）
    ])

    out = smt._load_settled_rows()

    assert registry["fetched"] == 4, "边界行不得重复读取"
    assert len(out) == 4, "边界行不得漏读"


# ── 3. 解码必须在事务之外（超时的直接原因）─────────────────────────

def test_json_decoded_outside_session(wired, monkeypatch):
    """features_json 解码时不得有会话处于打开状态。

    这是 F15 的核心：35 万行的 JSON 解码是 CPU 密集操作，放在事务内会让事务
    idle 超过 120 秒被服务端掐断。
    """
    registry, probe, set_rows = wired
    monkeypatch.setenv("SCALP_META_LOAD_WINDOW_DAYS", "60")
    set_rows([
        _Row(_NOW - timedelta(days=d), probe=probe) for d in (10, 20, 30)
    ])

    smt._load_settled_rows()

    assert probe, "未观测到 features_json 读取，测试替身失效"
    assert not any(probe), (
        "JSON 解码发生在会话打开期间 —— 这正是事务 idle 超时的成因"
    )


# ── 4. 解析与降级 ─────────────────────────────────────────────────

def test_row_parsing_shape():
    r = _Row(_NOW, features_json='{"roll_wr20": 0.5}')
    out = smt._parse_settled_row(r)

    assert out["symbol"] == "BTC"
    assert out["direction"] == "long"
    assert out["win"] == 1
    assert out["feats"] == {"roll_wr20": 0.5}
    assert out["settle"] == out["ts"] + 1800


@pytest.mark.parametrize("bad", [None, "", "{", "[]", '"str"', "null"])
def test_row_parsing_tolerates_bad_json(bad):
    """坏 features_json 降级为空 dict，不得中断整轮训练。"""
    out = smt._parse_settled_row(_Row(_NOW, features_json=bad))
    assert out["feats"] == {}


def test_missing_signal_ts_falls_back_to_created_at():
    r = _Row(_NOW)
    r.signal_ts = 0
    r.settle_ts = 0
    out = smt._parse_settled_row(r)
    assert out["ts"] == int(_NOW.timestamp())
    assert out["settle"] == out["ts"] + 1800


def test_returns_empty_when_db_time_unavailable(monkeypatch):
    """拿不到基准时间时安全返回空，不用本地时钟去猜（时区语义不同）。"""
    import backend.database.connection as _conn

    class _NoTimeSession:
        def execute(self, *a, **k):
            return _FakeResult([], scalar=None)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(_conn, "SessionLocal", lambda: _NoTimeSession())
    assert smt._load_settled_rows() == []


# ── 5. 接线契约（防回归）──────────────────────────────────────────

def test_no_unbounded_fetchall_in_loader():
    """加载器不得回到「一次拉全量 + 事务内解码」的老写法。"""
    path = smt.__file__
    with open(path, encoding="utf-8") as fh:
        src = "".join(ln for ln in fh if not ln.strip().startswith("#"))

    assert "INTERVAL '60 days'" not in src, (
        "仍在用单条 60 天全量查询 —— 会再次撞事务超时"
    )
    assert ":w_start" in src and ":w_end" in src, "缺少分批时间窗参数"
    assert "SELECT NOW()" in src, "缺少固定基准时间（每批各自 NOW() 会漂移）"
