# -*- coding: utf-8 -*-
"""[2026-09-18 数据中心优化·D-2] 心跳语义契约：`last_success_at` 必须只代表**成功**。

## 修的是什么
原写入方**每轮无条件**把 `last_success_at` 刷成 `CURRENT_TIMESTAMP`（含 `symbols_ok=0` 的失败轮）
⇒ 名字说"成功"、实际是"最后一次尝试"。代价（实测）：
P0 共 1383 轮、**656 轮（47.4%）整轮全零**，而两处消费方始终判"正常"：
  · `data_center_gate._eval_p0_stale`："P0 心跳最新**成功**时间距今 >300s 即告警"
  · `market_intelligence_routes`："asterdex p0 心跳 5 分钟内**成功** = 数据中心在线"

现在：`last_attempt_at`（每轮）/ `last_success_at`（仅成功轮前移）/ `consecutive_fail_rounds`。

本文件用**临时交易所名**写真实心跳行（保证不碰生产行的语义），跑完清理。
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services import kline_sync_meta as M  # noqa: E402


@pytest.fixture
def hb_ex():
    """临时交易所名，避免污染生产心跳行。"""
    ex = f"__t{uuid.uuid4().hex[:8]}"
    yield ex
    try:
        from sqlalchemy import text
        from backend.database.connection import MarketSessionLocal
        with MarketSessionLocal() as db:
            db.execute(text("DELETE FROM kline_sync_heartbeat WHERE exchange = :e"), {"e": ex})
            db.commit()
    except Exception:  # noqa: BLE001
        pass


def _row(ex: str) -> dict:
    for h in M.get_heartbeats(ex):
        if h.get("pool") == "p0":
            return h
    return {}


def test_success_advances_last_success(hb_ex):
    M.record_heartbeat(hb_ex, pool="p0", period="*", symbols_ok=300, symbols_fail=6)
    r = _row(hb_ex)
    assert r, "心跳行必须写入"
    assert r["last_success_at"], "成功轮必须更新 last_success_at"
    assert r["last_attempt_at"], "每轮都必须更新 last_attempt_at"
    assert r["consecutive_fail_rounds"] == 0


def test_total_failure_does_not_advance_last_success(hb_ex):
    """**本文件的核心**：整轮全失败不得把 last_success_at 往前推。"""
    M.record_heartbeat(hb_ex, pool="p0", period="*", symbols_ok=300, symbols_fail=6)
    ok_ts = _row(hb_ex)["last_success_at"]
    M.record_heartbeat(hb_ex, pool="p0", period="*", symbols_ok=0, symbols_fail=306)
    r1 = _row(hb_ex)
    assert r1["last_success_at"] == ok_ts, "失败轮不得前移 last_success_at（旧实现正是这里的 bug）"
    assert r1["symbols_ok"] == 0 and r1["symbols_fail"] == 306
    assert r1["last_attempt_at"] >= r1["last_success_at"], "尝试时间必须仍在前移"
    assert r1["consecutive_fail_rounds"] == 1
    # 连续第二轮失败 ⇒ 计数累加，成功时间仍不动
    M.record_heartbeat(hb_ex, pool="p0", period="*", symbols_ok=0, symbols_fail=306)
    r2 = _row(hb_ex)
    assert r2["consecutive_fail_rounds"] == 2
    assert r2["last_success_at"] == ok_ts
    # 恢复成功 ⇒ 计数清零、成功时间前移
    M.record_heartbeat(hb_ex, pool="p0", period="*", symbols_ok=291, symbols_fail=15)
    r3 = _row(hb_ex)
    assert r3["consecutive_fail_rounds"] == 0
    assert r3["last_success_at"] >= ok_ts


def test_first_ever_beat_with_zero_ok_has_null_success(hb_ex):
    """首跳即失败 ⇒ last_success_at 必须为 NULL（而不是被伪装成"刚刚成功"）。"""
    M.record_heartbeat(hb_ex, pool="p0", period="*", symbols_ok=0, symbols_fail=306)
    r = _row(hb_ex)
    assert r["last_success_at"] is None
    assert r["last_attempt_at"] is not None
    assert r["consecutive_fail_rounds"] == 1


def test_consumers_now_see_failure_streaks():
    """把"消费方因此能看见失败连胜"钉住（两处注释本意就是"成功时间"）。"""
    gate = (ROOT / "backend" / "services" / "data_center_gate.py").read_text(encoding="utf-8")
    assert "last_success_at" in gate, "门控仍读 last_success_at（语义已修正 ⇒ 现在才有意义）"
    assert "只报告，不阻断" in gate, "该判据只报告不阻断（改语义不会拦交易）"
    mir = (ROOT / "backend" / "api" / "market_intelligence_routes.py").read_text(encoding="utf-8")
    assert "last_success_at" in mir


# ───────────────── D-3 死池清理工具的契约 ─────────────────

def test_cleanup_tool_defaults_to_dry_run():
    """清理工具**默认只报告**（`--apply` 才删）——遥测行虽轻，也不该被静默删掉。"""
    src = (ROOT / "scripts" / "cleanup_dead_heartbeats.py").read_text(encoding="utf-8")
    assert 'APPLY = "--apply" in sys.argv' in src
    assert "if APPLY and dead:" in src, "只在显式 --apply 时才走删除分支"
    assert "--days" in src, "阈值必须可调"


def test_cleanup_only_deletes_by_age_not_by_pool():
    """删除条件必须是**按年龄**，不能按池名硬编码 —— 否则复活后的池会被误删。"""
    src = (ROOT / "scripts" / "cleanup_dead_heartbeats.py").read_text(encoding="utf-8")
    i = src.index("DELETE FROM kline_sync_heartbeat")
    seg = src[i:i + 200]
    assert "updated_at < now() - make_interval" in seg, "必须按 updated_at 年龄删除"
    for bad in ("exchange =", "pool =", "pool IN"):
        assert bad not in seg, f"删除条件不得按 {bad.strip()} 硬编码"

