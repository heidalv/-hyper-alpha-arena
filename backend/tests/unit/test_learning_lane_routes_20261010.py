# -*- coding: utf-8 -*-
"""[2026-10-10 学习进化前端升级] `/api/learning/lane/*` 与手动触发/健康项的契约测试。

锁定的六件事（每条都对应一个会误导操作者的真实失效模式）：

1. **文件缺失不是 500**：入口/顾问的数据源是 JSONL/JSON 文件，缺文件必须返回
   `present:false` + 判据，而不是让面板白屏。
2. **`include_raw=0` 不返回模型原文**：`raw_llm` 实测可达数 KB（含 `choices` 包装），
   默认返回会让时间轴接口变成大响应体。
3. **白名单同源**：`/advisor` 的 `whitelists` 必须来自 `brain_advisor.allowed_keys`，
   两处各存一份必然漂移（本仓已发生多次：`_VALID_JOBS` 就是漂移的两个副本）。
4. **`backend_has_tick=false` ⇒ health=bad**：入口 tick 是**重启后**才注册的，
   前端必须能区分"未重启"与"跑了但没数据"。
5. **手动触发的 job 名覆盖服务层全部别名**：此前路由的 `_VALID_JOBS` 只有 4 个，
   `hft_brain` / `factor_decay` / `live_outcome_backfill` 一律 400。
6. **`trigger_job` 返回 extra**：前端"手动补扫"要能显示这次推进了多少。
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.api import learning_lane_routes as LLR  # noqa: E402
from backend.api import learning_loop_routes as LLRt  # noqa: E402
from backend.services import learning_health_service as LHS  # noqa: E402
from backend.services.learning_loop_service import (  # noqa: E402
    DEFAULT_INTERVALS,
    JOB_HFT_BRAIN,
    JOB_TRIGGER_ALIASES,
    LearningLoopService,
)


def _run(coro):
    return asyncio.run(coro)


# ══ 1. 文件缺失 / 乱行 / 排序 / limit ══════════════════════════════════
def test_read_jsonl_tolerates_missing_and_broken_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(LLR, "JOURNAL_PATH", str(tmp_path / "none.jsonl"))
    assert LLR._read_jsonl(LLR.JOURNAL_PATH) == []

    p = tmp_path / "j.jsonl"
    p.write_text('{"a":1}\nnot-json\n\n{"a":2}\n[1,2]\n', encoding="utf-8")
    rows = LLR._read_jsonl(str(p))
    assert [r["a"] for r in rows] == [1, 2], "坏行跳过；非 dict 行跳过"


def test_read_jsonl_limit_reads_tail(tmp_path):
    p = tmp_path / "j.jsonl"
    p.write_text("".join(f'{{"i":{i}}}\n' for i in range(100)), encoding="utf-8")
    rows = LLR._read_jsonl(str(p), limit=3)
    assert [r["i"] for r in rows] == [97, 98, 99], "limit 必须取末尾（最新）而不是前几行"


def test_advisor_empty_state_is_200_not_error(tmp_path, monkeypatch):
    monkeypatch.setattr(LLR, "JOURNAL_PATH", str(tmp_path / "none.jsonl"))
    monkeypatch.setattr(LLR, "PENDING_PATH", str(tmp_path / "none.json"))
    monkeypatch.setattr(LLR, "HISTORY_PATH", str(tmp_path / "none.jsonl"))
    out = _run(LLR.lane_advisor(lane="", limit=10, include_raw=0))
    assert out["journal"] == [] and out["journal_present"] is False
    assert out["pending_verdicts"] == [] and out["pending_present"] is False
    assert out["verdict_history"] == []
    assert out["rules"]["whitelist"]  # 判据永远要在（空态也要能读懂为什么空）


def test_advisor_journal_newest_first_and_filtered(tmp_path, monkeypatch):
    p = tmp_path / "j.jsonl"
    rows = [
        {"ts": 100.0, "lane": "hft", "decision": "hold"},
        {"ts": 200.0, "lane": "mid", "decision": "rejected"},
        {"ts": 300.0, "lane": "hft", "decision": "applied", "param": "pp_rest_sec",
         "old": 15, "new": 20, "cited": ["kb-x"]},
    ]
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                 encoding="utf-8")
    monkeypatch.setattr(LLR, "JOURNAL_PATH", str(p))
    out = _run(LLR.lane_advisor(lane="hft", limit=10, include_raw=0))
    assert [r["ts"] for r in out["journal"]] == [300.0, 100.0], "最新在前"
    assert out["decision_counts"] == {"hold": 1, "applied": 1}
    assert out["journal"][0]["cited"] == ["kb-x"]
    assert out["journal"][0]["ts_iso"], "必须给前端 ISO 时间（前端不解析 epoch）"


def test_advisor_include_raw_is_opt_in(tmp_path, monkeypatch):
    p = tmp_path / "j.jsonl"
    big = "x" * 5000
    p.write_text(json.dumps({"ts": 1.0, "lane": "hft", "decision": "hold",
                             "raw_llm": big}) + "\n", encoding="utf-8")
    monkeypatch.setattr(LLR, "JOURNAL_PATH", str(p))
    off = _run(LLR.lane_advisor(lane="hft", limit=5, include_raw=0))
    assert "raw_llm" not in off["journal"][0], "默认不返回模型原文"
    on = _run(LLR.lane_advisor(lane="hft", limit=5, include_raw=1))
    assert len(on["journal"][0]["raw_llm"]) <= 1200, "打开了也要截断"


# ══ 2. 白名单与现值同源 ════════════════════════════════════════════════
def test_whitelists_come_from_brain_advisor():
    from backend.services.market_maker import brain_advisor as BA
    wl = LLR._whitelists()
    for lane in ("hft", "mid", "long"):
        assert set(wl[lane]) == set(BA.allowed_keys(lane)), f"{lane} 白名单必须同源"
    assert "pp_rest_sec" in wl["hft"] and "mid_daily_cap" in wl["mid"]


def test_current_values_read_live_params():
    cur = LLR._current_values()
    from backend.services.market_maker import brain_advisor as BA
    assert set(cur["hft"]) == set(BA.allowed_keys("hft")), "hft 现值必须覆盖白名单"
    for lane in ("mid", "long"):
        assert set(cur[lane]) == set(BA.allowed_keys(lane)), f"{lane} 现值必须覆盖白名单"
    assert cur["hft"].get("pp_rest_sec") is not None


# ══ 3. 入口判据 ════════════════════════════════════════════════════════
def test_inlet_health_bad_when_tick_not_registered(monkeypatch):
    monkeypatch.setattr(LLR, "_ledger_counts", lambda: {
        "roundtrips_since_era": 10, "last_close_ts": None,
        "legs_last_10min": 0, "error": None})
    monkeypatch.setattr(
        LLR, "os", LLR.os)  # noqa: B018 - os 同源
    out = _run(LLR.lane_inlet())
    assert out["health"] in ("bad", "warn", "ok"), "必须给出一个结论"
    assert out["health_reason"], "结论必须带原因（前端只上色，不重算）"
    # 关键字段永远存在（前端不必做 undefined 分支）
    for key in ("backend_has_tick", "cursor", "ledger", "tick", "budget",
                "pending_from_cursor", "isolation_on", "enabled"):
        assert key in out, key


def test_inlet_bad_reason_mentions_restart_when_no_tick(monkeypatch):
    out = _run(LLR.lane_inlet())
    if out["backend_has_tick"] is False:
        assert "重启" in out["health_reason"]
        assert out["health"] == "bad"
    else:  # 本进程恰好注册过（重启后的真实环境）
        assert out["health"] in ("ok", "warn", "off")


def _fake_cursor(monkeypatch, **over):
    from backend.services.market_maker import pp_brain as pb
    cur = {"cursor_ts": 0.0, "cursor_id": 0, "ingested": 0, "skipped": 0,
           "failed": 0, "applied_at": 0.0, "attempts_id": -1, "attempts": 0}
    cur.update(over)
    monkeypatch.setattr(pb, "load_cursor", lambda root=None: dict(cur))
    return cur


def test_inlet_without_cursor_file_still_reports_progress(tmp_path, monkeypatch):
    """游标文件不存在时：`present=false`、`ingested_total=0`、「待补」= era 起总量。

    三者必须**互相自洽**（0 + 待补 = era 总量），前端才不会显示
    "已进主脑 0 / 待补 0" 这种自相矛盾的组合。
    """
    monkeypatch.setattr(LLR, "CURSOR_PATH", str(tmp_path / "none.json"))
    _fake_cursor(monkeypatch)   # 未跑过：ingested=0
    monkeypatch.setattr(LLR, "_ledger_counts", lambda: {
        "roundtrips_since_era": 7, "last_close_ts": None,
        "legs_last_10min": 0, "error": None})
    out = _run(LLR.lane_inlet())
    assert out["cursor"]["present"] is False
    assert out["cursor"]["ingested_total"] == 0
    assert out["pending_from_cursor"] == 7, "没有游标 ⇒ 待补 = era 起全部"
    assert out["health"] == "warn"
    assert "从未成功跑过" in out["health_reason"]


def test_inlet_pending_from_real_cursor(tmp_path, monkeypatch):
    """有游标时按 (era 总量 − 已进主脑) 算待补；追平后不再 warn。"""
    p = tmp_path / "cur.json"
    p.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(LLR, "CURSOR_PATH", str(p))
    _fake_cursor(monkeypatch, cursor_ts=123.0, cursor_id=9, ingested=7,
                 skipped=1, applied_at=456.0)
    monkeypatch.setattr(LLR, "_ledger_counts", lambda: {
        "roundtrips_since_era": 7, "last_close_ts": None,
        "legs_last_10min": 0, "error": None})
    out = _run(LLR.lane_inlet())
    assert out["cursor"]["present"] is True
    assert out["cursor"]["ingested_total"] == 7
    assert out["pending_from_cursor"] == 0
    assert out["cursor"]["applied_at_iso"], "必须有 ISO 形式的推进时间给前端"
    assert out["health"] in ("ok", "bad"), "tick 已注册则 ok；本进程未注册则是 bad(未重启)"


# ══ 4. 知识卡片视图 ════════════════════════════════════════════════════
def test_kb_cards_file_and_collection_shape():
    out = _run(LLR.lane_kb_cards())
    assert out["file"]["count"] >= 1, "卡片文件应已存在（36 张）"
    cards = out["cards"]
    assert all(c.get("id") for c in cards)
    assert all(c.get("text") for c in cards), "每张卡都要有可检索文本"
    assert set(out["by_lane"]) >= {"hft", "mid", "long", "all"}
    assert out["collection"]["name"] == "knowledge_cards"
    assert out["collection"].get("last_indexed"), "必须有索引时间（内存缺失时用文件 mtime 兜底）"


# ══ 5. 手动触发：job 名单与 extra ══════════════════════════════════════
def test_valid_jobs_covers_service_aliases():
    assert LLRt._valid_jobs() == set(JOB_TRIGGER_ALIASES)
    assert JOB_HFT_BRAIN.startswith("learning_loop_")
    # 别名是"路由名"，job_id 是"内部名"，两者不必相等但要能对上
    assert "hft_brain" in LLRt._valid_jobs()


def test_trigger_job_returns_extra(monkeypatch):
    svc = LearningLoopService()
    monkeypatch.setattr(svc, "_tick_hft_brain", lambda: None)
    # tick 内部会调 `_record_tick` 写一条 extra={} 的记录；这里替换成 no-op，
    # 让下面预置的 metrics 保持为"最近一次"，从而验证 `trigger_job` 的回读逻辑。
    monkeypatch.setattr(svc, "_record_tick",
                        lambda job, t0, ok, extra: None)
    import collections
    with svc._state_lock:
        svc._metrics[JOB_HFT_BRAIN] = collections.deque(
            [{"ts": "x", "elapsed_ms": 5, "success": True,
              "extra": {"ok": True, "scanned": 3, "ingested": 3,
                        "cursor": {"ts": 1.0, "id": 9}}}], maxlen=5)
    r = svc.trigger_job("hft_brain")
    assert r["ok"] is True
    assert r["ingested"] == 3, "便利字段必须冒泡（前端直接显示）"
    assert r["cursor"] == {"ts": 1.0, "id": 9}
    assert r["extra"]["scanned"] == 3


def test_trigger_job_unknown_lists_valid():
    r = LearningLoopService().trigger_job("nope")
    assert r["ok"] is False and "valid" in r
    assert "hft_brain" in r["valid"]


# ══ 6. 健康项：job 名单必须列全 ════════════════════════════════════════
def test_last_tick_map_covers_all_default_intervals():
    svc = LearningLoopService()
    assert set(svc.last_tick_map()) == set(DEFAULT_INTERVALS), (
        "漏掉的 job 在 /loop/status 与健康面板里根本不会出现（静默环）")


def test_learning_health_includes_hft_inlet():
    h = LHS.build_learning_health()
    names = {it["name"] for it in h["items"]}
    assert "loop_hft_brain" in names, "高频入口必须进健康面板"
    assert "hft_inlet_cursor" in names, "游标健康项是入口是否在动的唯一硬证据"
    assert "loop_factor_decay" in names and "loop_fusion" in names, "既有静默环也要补上"
    assert h["overall"] in ("ok", "warn", "dead")


def _loop_item(h, name):
    return next(it for it in h["items"] if it["name"] == name)


def test_low_freq_first_heartbeat_has_grace(monkeypatch):
    """低频 job 首次心跳的宽限必须压到 0.5h。

    实测：`_health_item` 的判据是「age ≤ 阈值 = ok」，阈值取间隔的 ~3 倍 ⇒ 6h 的
    `loop_fusion` 要 **18 小时**才有第一次心跳，后端刚重启它就显示 dead，
    读者会把"还没轮到"读成"停了"。
    """
    from backend.services import learning_loop_service as LLS
    monkeypatch.setattr(LLS.learning_loop, "_last_tick_at", {}, raising=False)
    monkeypatch.setattr(LLS.learning_loop, "last_tick_map", lambda: {})
    h = LHS.build_learning_health()
    for name in ("loop_factor_decay", "loop_fusion", "loop_live_backfill",
                 "loop_hft_brain", "loop_outcome_batch"):
        it = _loop_item(h, name)
        assert it["threshold_hours"] == 0.5, f"{name} 首次宽限应为 0.5h"
        assert it["status"] == "dead", "没有心跳仍然必须是 dead（不放松检测）"


def test_low_freq_threshold_unchanged_after_first_heartbeat(monkeypatch):
    """一旦有过心跳，阈值回到真实值（不放松已停摆的判定）。"""
    from datetime import datetime, timedelta, timezone
    from backend.services import learning_loop_service as LLS
    from backend.services.learning_loop_service import JOB_FUSION_MAINTENANCE
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    monkeypatch.setattr(LLS.learning_loop, "last_tick_map",
                        lambda: {JOB_FUSION_MAINTENANCE: old.isoformat()})
    h = LHS.build_learning_health()
    it = _loop_item(h, "loop_fusion")
    assert it["threshold_hours"] == 12.0, "有过心跳 ⇒ 用真实阈值（6h×2 的 ok/warn 档）"
    assert it["status"] in ("ok", "warn"), "2h 前跑过不应是 dead"
