# -*- coding: utf-8 -*-
"""[2026-09-10 §52] 开仓拒单原因「跨层传递」契约测试。

背景（§52.1 实证）：漏斗审计 JSONL 里 1866/4235（44.1%）的拒仓写成通用原因
`evaluate_and_execute_returned_false`，逐条就地取证后确认其真实原因是
paper 层 `code=daily_quota`（当日开仓配额用尽）等**具体**原因——
信息只存在于日志，不在唯一的长期记录里。

本测试锁住四件事：
  1. 记录器语义：mark 覆盖（深者更具体）、take 取走即清空、线程隔离；
  2. 下游各层**真的**登记了原因（源码护栏：proposal_execution / paper_execution）；
  3. 审计写入点**真的**用了登记值，且未登记时**保持旧字符串**（向后兼容）；
  4. 运行时端到端：模拟 paper 层拒单 → 审计行 reason 含具体码 + extra 带 layer/detail。
"""
from __future__ import annotations

import inspect
import json
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.mlto import open_block_reason as obr  # noqa: E402


# ───────────────────────── ① 记录器语义 ─────────────────────────

def test_mark_overwrite_take_and_clear():
    obr.clear_open_block()
    assert obr.peek_open_block() is None
    obr.mark_open_block("a", detail="d1", layer="L1")
    assert obr.peek_open_block()["code"] == "a"
    obr.mark_open_block("b", detail="d2", layer="L2")  # 深者覆盖浅者
    got = obr.take_open_block()
    assert got == {"code": "b", "detail": "d2", "layer": "L2"}
    assert obr.peek_open_block() is None, "take 必须取走即清空（防污染后续审计行）"
    assert obr.take_open_block() is None


def test_marker_is_thread_isolated():
    obr.clear_open_block()
    seen = {}

    def _worker():
        seen["before"] = obr.peek_open_block()
        obr.mark_open_block("worker_only")
        seen["after"] = obr.peek_open_block()

    t = threading.Thread(target=_worker)
    t.start()
    t.join()
    assert seen["before"] is None, "线程间不得串味"
    assert seen["after"]["code"] == "worker_only"
    assert obr.peek_open_block() is None, "主线程不应看到子线程的登记"


def test_mark_truncates_long_fields():
    obr.clear_open_block()
    obr.mark_open_block("x" * 200, detail="y" * 500, layer="z" * 100)
    got = obr.take_open_block()
    assert len(got["code"]) == 80 and len(got["detail"]) == 200 and len(got["layer"]) == 40


# ───────────────────────── ② 下游登记点（源码护栏） ─────────────────────────

def test_all_proposal_execution_block_sites_mark_reason():
    """proposal_execution 的每个 return False 分支都要登记原因。"""
    src = (ROOT / "backend/services/full_auto/proposal_execution.py").read_text(encoding="utf-8")
    for code in ("v5gate", "persistence_ticks", "no_active_strategy", "budget_exhausted",
                 "tranche_exhausted", "decision_price_stale", "live_dust",
                 "live_constitutional", "live_order_failed", "paper_trade_false"):
        assert f'"{code}"' in src, f"proposal_execution 缺登记点: {code}"
    assert "_mark_block(" in src and "mark_open_block" in src


def test_paper_execution_marks_engine_reason_code():
    """paper 层模拟下单失败必须把引擎的 reason_code 登记出去（本次 44% 的真实来源）。"""
    src = (ROOT / "backend/services/full_auto/paper_execution.py").read_text(encoding="utf-8")
    seg = src[src.index("模拟下单失败") - 800: src.index("模拟下单失败") + 1400]
    assert "mark_open_block" in seg, "paper_execution 拒单点未登记原因"
    assert 'code or "paper_execute_failed"' in seg


# ───────────────────────── ③ 审计写入点 ─────────────────────────

def _audit_src() -> str:
    return inspect.getsource(
        sys.modules["backend.services.full_auto.midlong_helpers"]
    ) if "backend.services.full_auto.midlong_helpers" in sys.modules else (
        ROOT / "backend/services/full_auto/midlong_helpers.py"
    ).read_text(encoding="utf-8")


def test_audit_uses_recorded_reason_and_keeps_legacy_fallback():
    src = _audit_src()
    assert "take_open_block" in src, "审计写入点未消费登记值"
    assert 'f"eval_false:{_code}"' in src or 'f"eval_false:{_blk_code}"' in src, "未把具体码写进 reason"
    assert '"evaluate_and_execute_returned_false"' in src, "缺失向后兼容的旧字符串回落"
    assert '"block_layer"' in src and '"block_detail"' in src, "extra 未带层与细节"


# ───────────────────────── ④ 运行时端到端（走真实函数） ─────────────────────────

class _Session:
    session_id = "fa_test"


def test_paper_block_code_reaches_audit_row(tmp_path, monkeypatch):
    """造一笔「paper 层拒单」→ 调**真实**审计函数 → 断言 reason 带具体码、extra 带细节。"""
    import backend.services.full_auto.midlong_helpers as mh

    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("MIDLONG_DIRECTION_AUDIT_PATH", str(audit_path))

    obr.clear_open_block()
    obr.mark_open_block("daily_quota", detail="risk_engine:daily_quota[total] 10/8 已用尽",
                        layer="risk_engine")
    reason = mh.record_exec_false_audit(
        symbol="VIRTUAL", tier="mid", action="buy", session=_Session(),
        mode="llm_brain", direction="long", authority="brain",
    )
    assert reason == "eval_false:daily_quota"

    row = json.loads(audit_path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert row["reason"] == "eval_false:daily_quota"
    assert row["extra"]["block_layer"] == "risk_engine"
    assert "daily_quota[total] 10/8" in row["extra"]["block_detail"]
    assert obr.peek_open_block() is None, "登记值必须被取走（不污染后续行）"

    # 漏斗汇总按前缀聚合后应能区分出具体原因
    from backend.services.mlto.midlong_direction_audit import summarize_decision_funnel
    summ = summarize_decision_funnel(lookback_hours=1.0)
    top = [x["reason"] for x in summ["top_skip_reasons"]]
    assert "eval_false" in top, f"汇总未按前缀识别具体原因: {top}"


def test_legacy_reason_unchanged_when_no_marker(tmp_path, monkeypatch):
    """未登记时审计行必须与修复前**完全一致**（不改变既有口径）。"""
    import backend.services.full_auto.midlong_helpers as mh

    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("MIDLONG_DIRECTION_AUDIT_PATH", str(audit_path))
    obr.clear_open_block()
    reason = mh.record_exec_false_audit(
        symbol="UNI", tier="long", action="buy", session=_Session(),
    )
    assert reason == "evaluate_and_execute_returned_false"
    row = json.loads(audit_path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert row["reason"] == "evaluate_and_execute_returned_false"
    assert "extra" not in row

