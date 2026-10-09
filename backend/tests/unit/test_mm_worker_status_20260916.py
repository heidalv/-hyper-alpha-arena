# -*- coding: utf-8 -*-
"""[F284 2026-09-16] `/shadow` 观测口径合并契约（外部 worker ↔ 进程内 runner）。

背景：F283 把 tick 移出 API 进程后，`/shadow` 读的进程内计数器恒为 0 ⇒ 观测失真。
契约（宁可显示"进程内 + 心跳陈旧"，也不伪造计数）：
  · 心跳读不到 ⇒ `ticker="inprocess"`，状态原样；
  · 心跳新鲜 + 进程内 ticks==0 ⇒ 用 worker 计数，`ticker="external-worker"`；
  · 心跳新鲜 + `MM_LANE_TICKER=external` ⇒ 即使进程内也有计数，仍以 worker 为准；
  · 心跳**陈旧** ⇒ 不用它的计数（只附诊断），避免把几分钟前的数字当"现在"。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker.worker_status import (  # noqa: E402
    FRESH_MAX_AGE_SEC, merge_worker_status, read_worker_status,
)

NOW = 1_700_000_100.0


def _write(tmp_path: Path, payload) -> str:
    p = tmp_path / "mm_lane_status.json"
    if isinstance(payload, str):
        p.write_text(payload, encoding="utf-8")
    else:
        p.write_text(json.dumps(payload), encoding="utf-8")
    return str(p)


def _snap(age: float, ticks: int = 7) -> dict:
    return {"ts": NOW - age, "ok": True, "reason": "", "ticks": ticks, "fills": 3,
            "last_tick_ts": NOW - age - 1, "skip_counts": {"below_step": 5},
            "side_counts": {"both": 4, "one": 0, "none": 5}, "fill_notional": 29.98}


def test_read_missing_file_returns_none(tmp_path):
    assert read_worker_status(str(tmp_path / "nope.json"), now=NOW) is None


def test_read_bad_json_returns_none(tmp_path):
    assert read_worker_status(_write(tmp_path, "{not json"), now=NOW) is None


def test_fresh_snapshot_flags(tmp_path):
    snap = read_worker_status(_write(tmp_path, _snap(age=12.0)), now=NOW)
    assert snap["fresh"] is True and abs(snap["snapshot_age_sec"] - 12.0) < 0.01


def test_stale_snapshot_not_used(tmp_path):
    snap = read_worker_status(_write(tmp_path, _snap(age=FRESH_MAX_AGE_SEC + 30)), now=NOW)
    merged = merge_worker_status({"ticks": 0, "fills": 0}, snap, prefer_external=True)
    assert merged["ticker"] == "inprocess"
    assert merged["ticks"] == 0 and merged["worker_snapshot"]["fresh"] is False


def test_external_worker_takes_over_counts(tmp_path):
    snap = read_worker_status(_write(tmp_path, _snap(age=5.0, ticks=42)), now=NOW)
    merged = merge_worker_status({"ticks": 0, "fills": 0, "equity": 299.8},
                                 snap, prefer_external=True)
    assert merged["ticker"] == "external-worker"
    assert merged["ticks"] == 42 and merged["fills"] == 3
    assert merged["skip_counts"] == {"below_step": 5}
    assert merged["equity"] == 299.8, "非计数键必须保留进程内状态（库存/挂单）"
    assert merged["worker_snapshot"]["fresh"] is True


def test_inprocess_wins_when_it_is_the_ticker(tmp_path):
    snap = read_worker_status(_write(tmp_path, _snap(age=5.0, ticks=42)), now=NOW)
    merged = merge_worker_status({"ticks": 9, "fills": 1}, snap, prefer_external=False)
    assert merged["ticker"] == "inprocess" and merged["ticks"] == 9


def test_worker_states_replace_stale_inprocess_states(tmp_path):
    """[F285] 持仓/挂单价必须取"真正在 tick 的那个进程"的心跳（否则看板滞后）。"""
    payload = _snap(age=4.0, ticks=11)
    payload["states"] = {"ETH": {"qty": 0.012, "quote_bid": 2399.5, "quote_ask": 2400.4}}
    snap = read_worker_status(_write(tmp_path, payload), now=NOW)
    stale = {"ticks": 0, "states": {"ETH": {"qty": 0.0, "quote_bid": 0.0, "quote_ask": 0.0}}}
    merged = merge_worker_status(stale, snap, prefer_external=True)
    assert merged["states"]["ETH"]["quote_bid"] == 2399.5
    assert merged["states"]["ETH"]["qty"] == 0.012
    # 进程内是 ticker 时不得被覆盖
    merged2 = merge_worker_status({"ticks": 5, "states": stale["states"]}, snap,
                                  prefer_external=False)
    assert merged2["states"]["ETH"]["quote_bid"] == 0.0
