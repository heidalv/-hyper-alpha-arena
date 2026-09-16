# -*- coding: utf-8 -*-
"""[F307 2026-09-16] auto_rollback 冷却闸 + 日志范围契约测试。

背景（本轮现场）：`data/mm_evolution_journal.jsonl` 里 `auto_rollback` 连续触发
5 次（05:03→06:10Z），每次把 `w_base_bp` 改成 8.0。机制是**自维持循环**：

  回滚 → `_apply_params` 推进 `last_change_ts` → 下一轮 `attribution(since=新窗口)`
  里**仍然是那几行亏损腿** ⇒ `net_bp` 恒为 −2.0、`n` 仍 ≥30 ⇒ 条件持续成立 ⇒ 再回滚。

"改了参数但没能改变触发条件"的循环是安全机制最坏的形态：它让人觉得有保护，
实际在反复改配置（本轮的 `w_base_bp` 就在 8/12/30 之间来回跳）。

本测试锁定两件事：
  1. **冷却**：同一车道的成功回滚在 `ROLLBACK_COOLDOWN_HOURS` 内只允许一次；
  2. **日志范围**：`restored` 只能报**实际写入**的键，不得比写入范围更大。
     （此前直接打印 `prev` ⇒ 一个只含 `w_base_bp` 的局部快照会被读成"恢复了整个网格"。）

另有一条**结构性**断言：`GRID["w_base_bp"]` 的上限必须覆盖实盘在位值。
现场：GRID 上限 16.0 而实盘 `w_base_bp=30.0` ⇒ 自动进化一旦开启就会按它的纪律把
30 "修回" ≤16（这正是回滚反复写 8/12 的原因）。这不是 bug 而是**网格外配置**，
但必须被显式发现，而不是靠人翻日志。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import evolution as ev  # noqa: E402


# ── 夹具 ───────────────────────────────────────────────────────────────
def _lane(params=None, prev=None, last_change=None):
    return {"meta": {
        "params": params if params is not None else {"w_base_bp": 30.0},
        "evolution": {"prev_params": prev, "last_change_ts": last_change},
    }}


def _patch_lane(monkeypatch, lane):
    import backend.services.lane_registry as reg_mod
    monkeypatch.setattr(reg_mod, "get_lane", lambda lid: lane)


def _patch_ledger(monkeypatch, n=100, net_bp=-5.0):
    import backend.services.lane_ledger as led
    monkeypatch.setattr(led, "attribution",
                        lambda **kw: {"total": {"n": n, "net_bp": net_bp}})


def _patch_journal(monkeypatch, tmp_path, entries):
    p = tmp_path / "journal.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")
    monkeypatch.setattr(ev, "JOURNAL_PATH", str(p))
    return p


def _iso(hours_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


# ── 1. 冷却闸 ─────────────────────────────────────────────────────────
def test_recent_rollback_triggers_cooldown(monkeypatch, tmp_path):
    """1 小时前刚回滚过 ⇒ 本次必须只回冷却、**绝不写配置**。"""
    monkeypatch.setenv("MM_AUTO_EVOLVE", "1")
    assert ev.evolve_enabled() is True
    _patch_lane(monkeypatch, _lane(prev={"w_base_bp": 12.0}, last_change=_iso(2)))
    _patch_ledger(monkeypatch, n=100, net_bp=-5.0)      # 条件明明已满足
    _patch_journal(monkeypatch, tmp_path, [
        {"event": "auto_rollback", "applied": True, "lane_id": "mm_asterdex",
         "ts": _iso(1.0), "net_bp": -2.0},
    ])

    def boom(*a, **k):
        raise AssertionError("冷却期内绝不允许写注册表")
    monkeypatch.setattr(ev, "_apply_params", boom)

    res = ev.check_and_rollback("mm_asterdex")
    assert res.get("action") == "cooldown", res
    assert res.get("ok") is True, res
    assert "冷却" in str(res.get("reason") or ""), res


def test_stale_rollback_does_not_block(monkeypatch, tmp_path):
    """冷却期已过（>ROLLBACK_COOLDOWN_HOURS）⇒ 允许回滚。"""
    monkeypatch.setenv("MM_AUTO_EVOLVE", "1")
    _patch_lane(monkeypatch, _lane(prev={"w_base_bp": 12.0}, last_change=_iso(48)))
    _patch_ledger(monkeypatch, n=100, net_bp=-5.0)
    _patch_journal(monkeypatch, tmp_path, [
        {"event": "auto_rollback", "applied": True, "lane_id": "mm_asterdex",
         "ts": _iso(ev.ROLLBACK_COOLDOWN_HOURS + 1.0), "net_bp": -2.0},
    ])
    called = {}
    monkeypatch.setattr(ev, "_apply_params",
                        lambda *a, **k: called.setdefault("hit", True) or True)

    res = ev.check_and_rollback("mm_asterdex")
    assert res.get("action") == "rollback", res
    assert called.get("hit") is True, "冷却期已过，应真的执行回滚"


def test_failed_rollback_does_not_start_cooldown(monkeypatch, tmp_path):
    """只有 `applied=True` 的回滚才算数：失败的回滚不得冻结后续尝试。"""
    monkeypatch.setenv("MM_AUTO_EVOLVE", "1")
    _patch_lane(monkeypatch, _lane(prev={"w_base_bp": 12.0}, last_change=_iso(48)))
    _patch_ledger(monkeypatch, n=100, net_bp=-5.0)
    _patch_journal(monkeypatch, tmp_path, [
        {"event": "auto_rollback", "applied": False, "lane_id": "mm_asterdex",
         "ts": _iso(0.1), "net_bp": -2.0},
    ])
    monkeypatch.setattr(ev, "_apply_params", lambda *a, **k: True)
    res = ev.check_and_rollback("mm_asterdex")
    assert res.get("action") == "rollback", res


def test_other_lane_rollback_does_not_block(monkeypatch, tmp_path):
    """别的车道刚回滚过，不应冷却本车道。"""
    monkeypatch.setenv("MM_AUTO_EVOLVE", "1")
    _patch_lane(monkeypatch, _lane(prev={"w_base_bp": 12.0}, last_change=_iso(48)))
    _patch_ledger(monkeypatch, n=100, net_bp=-5.0)
    _patch_journal(monkeypatch, tmp_path, [
        {"event": "auto_rollback", "applied": True, "lane_id": "other_lane",
         "ts": _iso(0.1), "net_bp": -2.0},
    ])
    monkeypatch.setattr(ev, "_apply_params", lambda *a, **k: True)
    assert ev.check_and_rollback("mm_asterdex").get("action") == "rollback"


def test_cooldown_reads_journal_not_registry(monkeypatch, tmp_path):
    """`_last_rollback_ts` 取自 append-only 日志（历史事实），不是注册表最新状态。"""
    _patch_journal(monkeypatch, tmp_path, [
        {"event": "auto_rollback", "applied": True, "lane_id": "L", "ts": _iso(3.0)},
        {"event": "auto_rollback", "applied": True, "lane_id": "L", "ts": _iso(1.0)},
        {"event": "evolve", "applied": True, "lane_id": "L", "ts": _iso(0.1)},
    ])
    ts = ev._last_rollback_ts("L")
    assert ts is not None
    age_h = (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0
    assert 0.9 < age_h < 1.2, "应取最近一次 **auto_rollback**（1h 前），而非 evolve"


def test_missing_journal_means_no_cooldown(monkeypatch, tmp_path):
    monkeypatch.setattr(ev, "JOURNAL_PATH", str(tmp_path / "nope.jsonl"))
    assert ev._last_rollback_ts("mm_asterdex") is None


def test_corrupt_journal_lines_are_skipped(monkeypatch, tmp_path):
    p = tmp_path / "j.jsonl"
    p.write_text("not json\n"
                 + json.dumps({"event": "auto_rollback", "applied": True,
                               "lane_id": "L", "ts": _iso(2.0)}) + "\n{broken\n",
                 encoding="utf-8")
    monkeypatch.setattr(ev, "JOURNAL_PATH", str(p))
    assert ev._last_rollback_ts("L") is not None


# ── 2. 日志范围 ───────────────────────────────────────────────────────
def test_restored_scope_only_includes_grid_keys():
    """注册表里的键若不在 GRID 内，不得被报成"已恢复"。"""
    meta = {"evolution": {"prev_params": {
        "w_base_bp": 12.0, "not_a_grid_key": 999, "k_inv": 0.5}}}
    scope = ev._rollback_applied_scope(meta)
    assert set(scope) <= set(ev.GRID), "恢复范围必须落在 GRID 内"
    assert "not_a_grid_key" not in scope
    assert scope.get("w_base_bp") == 12.0
    assert scope.get("k_inv") == 0.5


def test_restored_scope_drops_none_values():
    """null 值不得进入恢复范围（F253 现场：写 null 会让 runner 直接 TypeError）。"""
    meta = {"evolution": {"prev_params": {"w_base_bp": None, "k_inv": 0.5}}}
    scope = ev._rollback_applied_scope(meta)
    assert "w_base_bp" not in scope, "None 必须被剔除（宁可不恢复也不写 null）"
    assert scope.get("k_inv") == 0.5


def test_restored_scope_empty_when_no_prev():
    assert ev._rollback_applied_scope({"evolution": {}}) == {}
    assert ev._rollback_applied_scope({}) == {}


def test_journal_restored_matches_actual_write_scope(monkeypatch, tmp_path):
    """端到端：日志里的 `restored` 必须等于 `_apply_params` 真正收到的 new_params。"""
    monkeypatch.setenv("MM_AUTO_EVOLVE", "1")
    prev = {"w_base_bp": 12.0, "bogus": 1}
    _patch_lane(monkeypatch, _lane(prev=prev, last_change=_iso(48)))
    _patch_ledger(monkeypatch, n=100, net_bp=-5.0)
    _patch_journal(monkeypatch, tmp_path, [])
    captured = {}

    def _fake_apply(lane_id, base_meta, new_params, *, prev, reason):
        captured["new_params"] = dict(new_params)
        return True
    monkeypatch.setattr(ev, "_apply_params", _fake_apply)

    res = ev.check_and_rollback("mm_asterdex")
    assert res.get("action") == "rollback", res
    assert res["restored"] == ev._rollback_applied_scope(
        {"evolution": {"prev_params": prev}}), "返回值与日志必须同口径"
    assert "bogus" not in res["restored"]
    # 日志里的 restored 也要同口径
    logged = json.loads(Path(ev.JOURNAL_PATH).read_text(encoding="utf-8").strip().splitlines()[-1])
    assert logged["restored"] == res["restored"]
    assert "restored_keys" in logged and "prev_snapshot_keys" in logged
    assert logged["prev_snapshot_keys"] == sorted(prev.keys()), (
        "必须同时记录 prev 快照的键，才能看出'快照比写入范围大'这件事")


def test_journal_records_sample_count(monkeypatch, tmp_path):
    """日志必须带 `samples`：'净 bp 低于阈值' 在 n=30 与 n=300 下可信度不同。"""
    monkeypatch.setenv("MM_AUTO_EVOLVE", "1")
    _patch_lane(monkeypatch, _lane(prev={"w_base_bp": 12.0}, last_change=_iso(48)))
    _patch_ledger(monkeypatch, n=137, net_bp=-5.0)
    _patch_journal(monkeypatch, tmp_path, [])
    monkeypatch.setattr(ev, "_apply_params", lambda *a, **k: True)
    ev.check_and_rollback("mm_asterdex")
    logged = json.loads(Path(ev.JOURNAL_PATH).read_text(encoding="utf-8").strip().splitlines()[-1])
    assert logged["samples"] == 137


# ── 3. 阈值常量 ───────────────────────────────────────────────────────
def test_min_samples_constant_replaces_magic_number():
    assert ev.ROLLBACK_MIN_SAMPLES == 30, "原内联值 30 必须保持（行为不变）"
    import inspect
    src = inspect.getsource(ev.check_and_rollback)
    assert "n < 30" not in src, "不得再出现内联字面量 30"
    assert "ROLLBACK_MIN_SAMPLES" in src


def test_cooldown_constant_positive():
    assert ev.ROLLBACK_COOLDOWN_HOURS > 0


# ── 4. 结构性：网格必须覆盖实盘在位值 ─────────────────────────────────
def test_grid_covers_live_w_base_bp():
    """**按契约条件生效**：自动进化开启时，网格必须覆盖实盘在位值。

    现场（2026-09-16）：GRID["w_base_bp"] 上限 16.0，而实盘 `w_base_bp=30.0`
    （F296 人工设定）。后果：`MM_AUTO_EVOLVE=1` 时自动进化会按自己的纪律把 30
    "修回" ≤16 —— 这正是 auto_rollback 反复写 8/12 的原因。

    为什么用 skip 而不是 fail 来表达：
      · `MM_AUTO_EVOLVE=0`（当前 .env 状态）⇒ 不会自动写配置，**风险是潜伏的**，
        不该让测试套件长期红着（长期红 = 失去"全绿"这个信号本身）；
      · `MM_AUTO_EVOLVE=1` ⇒ 风险是**活性的**，必须 fail 拦住。
    无论哪种情形，skip 的原因里都会把这件事写出来，不会被静默遗忘。
    """
    import os

    import backend.services.lane_registry as reg_mod

    lane = reg_mod.get_lane("mm_asterdex") or {}
    live = ((lane.get("meta") or {}).get("params") or {}).get("w_base_bp")
    if live is None:
        pytest.skip("读取不到实盘 w_base_bp（注册表不可用）")

    auto_on = str(os.getenv("MM_AUTO_EVOLVE", "0")).strip().lower() in (
        "1", "true", "yes", "on")
    covered = live in ev.GRID["w_base_bp"]
    msg = (f"实盘 w_base_bp={live} 不在 GRID {ev.GRID['w_base_bp']} 内 ⇒ "
           "自动进化会把该值改回网格内。要么把该值加入 GRID，要么保持 "
           "MM_AUTO_EVOLVE=0。")
    if auto_on:
        assert covered, "自动进化已开启，网格必须覆盖实盘值！" + msg
    elif not covered:
        pytest.skip("自动进化未开启（MM_AUTO_EVOLVE≠1）⇒ 风险潜伏而非活性。"
                    "若将来要开启自动进化，必须先处理：" + msg)
    else:
        assert covered
