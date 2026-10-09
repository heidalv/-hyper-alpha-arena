# -*- coding: utf-8 -*-
"""[F253 2026-09-16] auto_rollback 两道安全闸回归锁：

现场：09-16 09:56:31 回滚检查用「时代累计 −5.73bp」触发，把一份全 null 的旧
prev_params 写回注册表 ⇒ w_base_bp=None、frozen_width=3 + frozen_max_move=None
⇒ 下一次 runner 重建在 compute_quote 直接 TypeError ✗。

契约：
  1. `MM_AUTO_EVOLVE` 关闭时回滚检查**只读不写**（此前回滚路径不看总开关 ✗）；
  2. prev_params 含 None ⇒ **中止回滚**（宁可保持现状也不写 null ✗）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import evolution as ev  # noqa: E402


def _patch_lane(monkeypatch, prev):
    """把真实 lane_registry.get_lane 换掉（属性补丁，顺序无关；sys.modules 替换在
    不同导入顺序下会被父包属性缓存绕过）。"""
    import backend.services.lane_registry as reg_mod
    monkeypatch.setattr(reg_mod, "get_lane", lambda lid: _lane(prev))


def _lane(prev):
    return {"meta": {"params": {"w_base_bp": 12.0}, "evolution": {"prev_params": prev,
                                                                  "last_change_ts": None}}}


def _isolate_journal(monkeypatch):
    """[F307] 把回滚日志指到不存在的**绝对**路径 ⇒ 冷却闸视为"从未回滚过"。

    为什么需要：`check_and_rollback` 现在读 `data/mm_evolution_journal.jsonl` 判断
    冷却，而**真实日志里有 16 次 2026-09-16 的回滚记录**（01:56Z~08:13Z，约每 25
    分钟一次）⇒ 不隔离的话这些用例会撞上 12h 冷却、以 "cooldown" 失败，把真正要测
    的东西掩盖掉。

    必须用**绝对**路径：`JOURNAL_PATH` 是相对路径，而进程 CWD 可能是仓库根
    （后端进程）或其它目录（pytest），相对路径会落到不同文件上——现场就有两份
    journal（`data/` 与 `backend/data/`），排查时极易读错那份。
    """
    import os
    import sys
    import tempfile
    mod = sys.modules["backend.services.market_maker.evolution"]
    missing = os.path.join(tempfile.gettempdir(),
                           "no_such_journal_f307_%d.jsonl" % os.getpid())
    if os.path.exists(missing):
        os.remove(missing)
    monkeypatch.setattr(mod, "JOURNAL_PATH", missing)


def test_rollback_gated_when_auto_off(monkeypatch):
    """总开关关闭 ⇒ 回滚检查只读，绝不写注册表。"""
    monkeypatch.delenv("MM_AUTO_EVOLVE", raising=False)
    assert ev.evolve_enabled() is False
    _patch_lane(monkeypatch, {"w_base_bp": 8.0})
    _isolate_journal(monkeypatch)
    # 账本归因给出"已跌破阈值"的读数 ⇒ 判定确实要求回滚，但开关关着只能只读
    import backend.services.lane_ledger as ledger_mod
    monkeypatch.setattr(ledger_mod, "attribution",
                        lambda **k: {"total": {"n": 40, "net_bp": -2.0}})

    def boom(*a, **k):  # _apply_params 被调用即失败
        raise AssertionError("自动开关关闭时绝不允许写注册表")
    monkeypatch.setattr(ev, "_apply_params", boom)

    res = ev.check_and_rollback("mm_asterdex")
    assert res.get("action") == "none", res
    assert "只读" in str(res.get("reason") or ""), res
    assert res.get("read_only") is True, res


def test_rollback_aborts_on_null_prev(monkeypatch):
    """prev_params 含 null ⇒ 中止回滚，不写注册表（F253 现场根因）。"""
    monkeypatch.setenv("MM_AUTO_EVOLVE", "1")
    assert ev.evolve_enabled() is True
    _patch_lane(monkeypatch,
                {"w_base_bp": None, "k_inv": 0.5, "frozen_width_bp": 3.0})
    _isolate_journal(monkeypatch)
    import backend.services.lane_ledger as ledger_mod
    monkeypatch.setattr(ledger_mod, "attribution",
                        lambda **k: {"total": {"n": 40, "net_bp": -2.0}})

    def boom(*a, **k):
        raise AssertionError("prev 含 null 时绝不允许写注册表")
    monkeypatch.setattr(ev, "_apply_params", boom)

    res = ev.check_and_rollback("mm_asterdex")
    assert res.get("action") == "aborted", res
    assert res.get("ok") is False, res
    assert "w_base_bp" in (res.get("null_keys") or []), res


def test_rollback_ok_when_prev_clean(monkeypatch):
    """prev 无 null 且开关打开 ⇒ 正常回滚（写注册表一次）。"""
    monkeypatch.setenv("MM_AUTO_EVOLVE", "1")
    _isolate_journal(monkeypatch)
    clean_prev = {"w_base_bp": 8.0, "k_inv": 0.5, "k_vol": 0.3,
                  "min_width_reduce_bp": 6.0, "frozen_width_bp": 0.0,
                  "frozen_max_move_bp": 8.0, "frozen_lookback": 120,
                  "ofi_block_threshold": 0.5, "max_one_side_seconds": 3600.0}
    _patch_lane(monkeypatch, dict(clean_prev))

    wrote = {}

    def fake_apply(lane_id, meta, new_params, *, prev, reason):
        wrote["new"] = new_params
        return True
    monkeypatch.setattr(ev, "_apply_params", fake_apply)

    # 账本归因返回负净 bp（n≥30）⇒ 触发回滚
    import backend.services.lane_ledger as ledger_mod
    monkeypatch.setattr(ledger_mod, "attribution",
                        lambda **k: {"total": {"n": 40, "net_bp": -2.0}})

    res = ev.check_and_rollback("mm_asterdex")
    assert res.get("action") == "rollback", res
    assert wrote.get("new") == clean_prev, wrote
