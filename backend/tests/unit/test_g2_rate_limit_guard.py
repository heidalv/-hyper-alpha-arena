# -*- coding: utf-8 -*-
"""G2 限额与频率守卫的回归测试。

用**注入的时间与临时日志**构造状态，不碰真实 `logs/llm_adjustments.jsonl`。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "g2_rate_limit.py"


def _load():
    spec = importlib.util.spec_from_file_location("g2_under_test", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    saved = sys.stdout
    try:
        spec.loader.exec_module(m)
    finally:
        sys.stdout = saved
    return m


G = _load()


@pytest.fixture()
def g(tmp_path, monkeypatch):
    """把状态文件重定向到 tmp_path（**绝不碰真实日志**）。"""
    log = tmp_path / "llm_adjustments.jsonl"
    monkeypatch.setattr(G, "LOG", log)
    return G


def _write(g, rows):
    g.LOG.parent.mkdir(parents=True, exist_ok=True)
    with g.LOG.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _iso(dt):
    return dt.isoformat()


@pytest.mark.unit
def test_single_item_allowed(g):
    now = datetime.now().astimezone()
    a, r = g.check_call([{"symbol": "XRP", "key": "spread_mult", "value": 0.45}], now=now)
    assert len(a) == 1 and not r


@pytest.mark.unit
def test_l1_only_one_coin_per_call(g):
    """L1：一次调用只能对 1 个币提调整 —— 否则无法归因。"""
    now = datetime.now().astimezone()
    items = [{"symbol": "XRP", "key": "spread_mult", "value": 0.45},
             {"symbol": "SOL", "key": "spread_mult", "value": 0.45}]
    a, r = g.check_call(items, now=now)
    assert len(a) == 1, "最多放行 1 条"
    assert len(r) == 1 and r[0]["rule"] == "L1"


@pytest.mark.unit
def test_l1_same_symbol_twice_also_rejected(g):
    """同一币在一次调用里提两条也算越限。"""
    now = datetime.now().astimezone()
    items = [{"symbol": "XRP", "key": "spread_mult", "value": 0.45},
             {"symbol": "XRP", "key": "take_profit_bp", "value": 13.0}]
    a, r = g.check_call(items, now=now)
    assert len(a) == 1 and any(x["rule"] == "L1" for x in r)


@pytest.mark.unit
def test_l3_min_gap_blocks(g):
    """L3：距上次调整不足 10 分钟 ⇒ 全部拒绝。"""
    now = datetime.now().astimezone()
    _write(g, [{"ts": _iso(now - timedelta(minutes=3)), "symbol": "XRP",
                "key": "spread_mult", "applied": True}])
    a, r = g.check_call([{"symbol": "SOL", "key": "spread_mult", "value": 0.45}], now=now)
    assert not a, "应被 L3 拒绝"
    assert any(x["rule"] == "L3" for x in r)


@pytest.mark.unit
def test_l3_allows_after_gap(g):
    """间隔足够（≥10 分钟）⇒ 放行。"""
    now = datetime.now().astimezone()
    _write(g, [{"ts": _iso(now - timedelta(minutes=30)), "symbol": "XRP",
                "key": "spread_mult", "applied": True}])
    a, r = g.check_call([{"symbol": "SOL", "key": "spread_mult", "value": 0.45}], now=now)
    assert len(a) == 1, f"应放行，实际拒绝：{r}"


@pytest.mark.unit
def test_l2_per_coin_hourly_cap(g):
    """L2：同一币 1 小时内已 2 次 ⇒ 第 3 次拒绝。"""
    now = datetime.now().astimezone()
    _write(g, [
        {"ts": _iso(now - timedelta(minutes=40)), "symbol": "XRP",
         "key": "spread_mult", "applied": True},
        {"ts": _iso(now - timedelta(minutes=20)), "symbol": "XRP",
         "key": "spread_mult", "applied": True},
    ])
    a, r = g.check_call([{"symbol": "XRP", "key": "spread_mult", "value": 0.45}], now=now)
    assert not a
    assert any(x["rule"] == "L2" for x in r)


@pytest.mark.unit
def test_l2_does_not_block_other_symbol(g):
    """L2 是**按币**计的 —— XRP 撞上限不影响 SOL。"""
    now = datetime.now().astimezone()
    _write(g, [
        {"ts": _iso(now - timedelta(minutes=40)), "symbol": "XRP",
         "key": "spread_mult", "applied": True},
        {"ts": _iso(now - timedelta(minutes=20)), "symbol": "XRP",
         "key": "spread_mult", "applied": True},
    ])
    a, r = g.check_call([{"symbol": "SOL", "key": "spread_mult", "value": 0.45}], now=now)
    assert len(a) == 1, f"SOL 应放行（L2 按币计），实际：{r}"


@pytest.mark.unit
def test_rejected_entries_do_not_consume_quota(g):
    """**被拒绝的条目不占配额** —— 否则守卫的拒绝会自我强化成永久封禁。"""
    now = datetime.now().astimezone()
    # 写 3 条 applied=False（被拒）在同一小时
    _write(g, [{"ts": _iso(now - timedelta(minutes=5 * i)), "symbol": "XRP",
                "key": "spread_mult", "applied": False} for i in (1, 2, 3)])
    a, r = g.check_call([{"symbol": "XRP", "key": "spread_mult", "value": 0.45}], now=now)
    assert len(a) == 1, f"被拒条目不应占配额，实际：{r}"


@pytest.mark.unit
def test_old_entries_expire(g):
    """超过 24 小时的记录不参与任何限额计算。"""
    now = datetime.now().astimezone()
    _write(g, [{"ts": _iso(now - timedelta(hours=30)), "symbol": "XRP",
                "key": "spread_mult", "applied": True}])
    a, r = g.check_call([{"symbol": "XRP", "key": "spread_mult", "value": 0.45}], now=now)
    assert len(a) == 1


@pytest.mark.unit
def test_append_adjustment_records_and_counts(g):
    """`append_adjustment(applied=True)` 应真的写盘并影响后续判据。"""
    now = datetime.now().astimezone()
    g.append_adjustment(symbol="XRP", key="spread_mult", value=0.45,
                        applied=True, now=now)
    assert g.LOG.exists()
    recs = [json.loads(x) for x in g.LOG.read_text(encoding="utf-8").splitlines() if x]
    assert len(recs) == 1 and recs[0]["applied"] is True
    # 立刻再调 ⇒ 被 L3 挡
    a, r = g.check_call([{"symbol": "XRP", "key": "spread_mult", "value": 0.45}], now=now)
    assert not a and any(x["rule"] == "L3" for x in r)


@pytest.mark.unit
def test_empty_input_is_safe(g):
    now = datetime.now().astimezone()
    a, r = g.check_call([], now=now)
    assert a == [] and r == []
