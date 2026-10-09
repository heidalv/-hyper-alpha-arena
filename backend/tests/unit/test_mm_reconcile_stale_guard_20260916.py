# -*- coding: utf-8 -*-
"""[F254 2026-09-16] 对账写校正行的「运行态陈旧闸」回归锁：

现场：09-16 00:38 对账在运行态快照陈旧（旧时代持仓）时写校正行 ⇒ 幽灵仓位
（XRP +$6,559 / SOL -$3,259 / ETH -$3,374 出现在时代口径 /positions，实盘空仓）。

契约：
  1. 运行态快照 > STALE_MAX_AGE_SEC ⇒ 拒绝写（返回 0）；
  2. 无运行态行 ⇒ 拒绝写；
  3. 快照新鲜 ⇒ 正常写校正行。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import reconcile as rc  # noqa: E402

M = [{"symbol": "XRP", "diff_qty": -100.0, "mark_px": 1.3}]


def test_rejects_stale_runtime(monkeypatch):
    monkeypatch.setattr(rc, "runtime_stale_age_sec", lambda lid: 3600.0)
    n = rc.apply_adjustments(lane_id="mm_asterdex", mismatches=M, ts=None)
    assert n == 0, "陈旧快照绝不允许写校正行"


def test_rejects_missing_runtime(monkeypatch):
    monkeypatch.setattr(rc, "runtime_stale_age_sec", lambda lid: None)
    n = rc.apply_adjustments(lane_id="mm_asterdex", mismatches=M, ts=None)
    assert n == 0, "无运行态行绝不允许写校正行"


def test_writes_when_fresh(monkeypatch):
    monkeypatch.setattr(rc, "runtime_stale_age_sec", lambda lid: 5.0)
    wrote = {}

    def fake_record_fill(**kw):
        wrote.update(kw)
        return True
    monkeypatch.setattr(rc.lane_ledger, "record_fill", fake_record_fill)
    n = rc.apply_adjustments(lane_id="mm_asterdex", mismatches=M, ts=None)
    assert n == 1 and wrote.get("symbol") == "XRP", "新鲜快照应正常写校正行"
    # 零盈亏校正：fill_px == mid_px、fee 0
    assert wrote.get("fill_px") == wrote.get("mid_px") == 1.3
    assert wrote.get("fee_rate") == 0.0
