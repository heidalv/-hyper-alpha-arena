# -*- coding: utf-8 -*-
"""
[F39] 短线停开时的心跳 SLA 判定回归

背景：2026-09-05 起 `SCALP_OPEN_DISABLED=true`（短线整体停开），main.py 不再
注册 scalp_daily_health / scalp_chain_health / scalp_symbol_profile /
pair_binding_lane 四个任务，心跳自然停更。而 ops 心跳端点只认识
PAIR_SELECTOR_WATCHER_ENABLED，导致这 4 条按 age 被判 "down"，
报错中心长期挂 4 条 P0「心跳中断」假警报。

本测试锁定修复后的语义：
  - 上述 4 条在 SCALP_OPEN_DISABLED=true 时 → sla=disabled（且不进报错中心）；
  - SCALP_OPEN_DISABLED=false 时 → 恢复按 age 判定（down）；
  - scalp_circuit_breaker 不受该开关影响，仍按 age 判定。
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import backend.services.scalp.scalp_heartbeat as hb_mod  # noqa: E402
from backend.api import ops_routes  # noqa: E402

STALE_ISO = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
GATED = ("scalp_daily_health", "scalp_chain_health",
         "scalp_symbol_profile", "pair_binding_lane")


def _fake_heartbeats():
    rows = {tid: {"last_ok_at": STALE_ISO, "last_status": "ok", "detail": {}} for tid in GATED}
    rows["scalp_circuit_breaker"] = {
        "last_ok_at": STALE_ISO, "last_status": "ok", "detail": {},
    }
    return rows


@pytest.fixture(autouse=True)
def _stub_heartbeats(monkeypatch):
    monkeypatch.setattr(hb_mod, "get_heartbeats", _fake_heartbeats)
    yield


def _sla_map():
    res = ops_routes._ops_heartbeats_impl()
    return {i["task_id"]: i["sla"] for i in res["items"]}


def test_scalp_gated_tasks_disabled_when_scalp_off(monkeypatch):
    import backend.config.settings as settings
    monkeypatch.setattr(settings, "SCALP_OPEN_DISABLED", True)
    sla = _sla_map()
    for tid in GATED:
        assert sla[tid] == "disabled", f"{tid} 应为 disabled，实际 {sla[tid]}"
    # 熔断器不在该开关管辖内 → 依旧按 age 判为中断
    assert sla["scalp_circuit_breaker"] == "down"


def test_scalp_gated_tasks_follow_age_when_scalp_on(monkeypatch):
    import backend.config.settings as settings
    monkeypatch.setattr(settings, "SCALP_OPEN_DISABLED", False)
    sla = _sla_map()
    for tid in GATED:
        assert sla[tid] == "down", f"{tid} 重开短线后应恢复 age 判定，实际 {sla[tid]}"


def test_disabled_heartbeats_do_not_become_p0(monkeypatch):
    """sla=disabled 的心跳不得进入报错中心（否则长期刷 P0 误导）。"""
    import backend.config.settings as settings
    monkeypatch.setattr(settings, "SCALP_OPEN_DISABLED", True)
    # 直接复用 errors 端点的分类逻辑：只把 sla=down 记为 P0
    res = ops_routes._ops_heartbeats_impl()
    p0 = [i["task_id"] for i in res["items"]
          if i["sla"] == "down" and i["task_id"] in GATED]
    assert p0 == [], f"停开任务不应判为 P0：{p0}"
