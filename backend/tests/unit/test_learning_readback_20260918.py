# -*- coding: utf-8 -*-
"""[F345 2026-09-18] 学习→策略 **读回路** 不变量测试。

锁的不是"函数能跑"，而是本次复查反复踩到的四类**静默失败**：

1. **写必有读**（判据 A5）：教训被"读取"必须留下 `use_count`/`last_used_at`/`v7_retrieval_log`
   痕迹——否则 `evolution_memory_v7.maintenance()` 会把**被真读过的教训当垃圾退役**，
   而所有读数仍显示"只写不读"。本文件用真实 `maintenance()` 做回归。
2. **无垄断**（分层配额）：旧实现 `kind IN (4类)` + `quality + 0.5*recency` 饱和，
   实测 top4 恒为 4 条最新 `success_recipe`；`trajectory`/`pipeline_issue` 共 459 条
   结构性不可达。本文件锁"多 kind 可达 + 单一 kind 不得占满"。
3. **默认不改行为**：`LEARNING_READBACK_ENABLED=auto` 时新车道必须返回空串（与今日
   prompt 逐字节一致），master 车道沿用 `V7_LESSONS_IN_MASTER` 旧语义。
4. **禁止静默退化**（判据 A6）：截断、关闸、库不可用都不许无声无息。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services import learning_readback as LR  # noqa: E402

_LESSONS_DDL = """
CREATE TABLE IF NOT EXISTS v7_lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    cycle TEXT NOT NULL,
    period TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    report_json TEXT NOT NULL DEFAULT '{}',
    quality REAL NOT NULL DEFAULT 0.5,
    use_count INTEGER NOT NULL DEFAULT 0,
    last_used_at TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    UNIQUE(created_at, kind, cycle, period, title)
);
CREATE TABLE IF NOT EXISTS v7_retrieval_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    query TEXT NOT NULL,
    cycle TEXT,
    period TEXT,
    top_ids_json TEXT NOT NULL DEFAULT '[]'
);
"""


def _mkdb(tmp_path: Path) -> Path:
    p = tmp_path / "v7.db"
    con = sqlite3.connect(str(p))
    try:
        con.executescript(_LESSONS_DDL)
        con.commit()
    finally:
        con.close()
    return p


def _add(db: Path, kind: str, title: str, *, quality: float = 0.7, summary: str = "s",
         age_days: float = 0.0, cycle: str = "M", period: str = "1h") -> int:
    ts = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()
    con = sqlite3.connect(str(db))
    try:
        cur = con.execute(
            "INSERT INTO v7_lessons (created_at,kind,cycle,period,title,summary,quality) "
            "VALUES (?,?,?,?,?,?,?)", (ts, kind, cycle, period, title, summary, quality))
        con.commit()
        return int(cur.lastrowid)
    finally:
        con.close()


def _rows(db: Path, sql: str, params=()):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


@pytest.fixture(autouse=True)
def _clean_module_state(monkeypatch):
    """每个用例重置进程内缓存/去重表，避免相互污染。"""
    LR._POOL_CACHE.clear()
    LR._SEEN.clear()
    LR._LOG_ONCE.clear()
    for k in ("LEARNING_READBACK_ENABLED", "V7_LESSONS_IN_MASTER",
              "LEARNING_READBACK_DEDUPE_SECONDS", "LEARNING_READBACK_CHARS"):
        monkeypatch.delenv(k, raising=False)
    yield


# ───────────────────── ① 开关语义：默认不改行为 ─────────────────────

def test_auto_mode_disables_new_lanes(monkeypatch):
    """auto（默认）下，新车道必须关 —— 合入本模块不改变实盘行为。"""
    assert LR.readback_mode() == "auto"
    assert LR.lane_enabled("mlto") is False


def test_auto_mode_preserves_master_legacy_semantics(monkeypatch):
    """master 沿用既有开关 V7_LESSONS_IN_MASTER（默认 true）= 现状不变。"""
    assert LR.lane_enabled("master") is True
    monkeypatch.setenv("V7_LESSONS_IN_MASTER", "false")
    assert LR.lane_enabled("master") is False


def test_explicit_switch_covers_all_lanes(monkeypatch):
    monkeypatch.setenv("LEARNING_READBACK_ENABLED", "1")
    assert LR.lane_enabled("mlto") is True and LR.lane_enabled("master") is True
    monkeypatch.setenv("LEARNING_READBACK_ENABLED", "0")
    assert LR.lane_enabled("mlto") is False and LR.lane_enabled("master") is False


def test_disabled_lane_returns_empty_even_with_rich_pool(tmp_path, monkeypatch):
    """关闸 ⇒ 空串（不是"少给几条"），且池里有货也不读。"""
    db = _mkdb(tmp_path)
    for k in LR.ALL_KINDS:
        _add(db, k, f"{k} 标题", summary="x" * 50)
    monkeypatch.setenv("V7_MEMORY_DB_PATH", str(db))
    assert LR.decision_block(lane="mlto") == ""
    assert _rows(db, "SELECT SUM(COALESCE(use_count,0)) FROM v7_lessons")[0][0] == 0


# ───────────────────── ② 分层配额：无垄断、旧盲区可达 ─────────────────────

def test_quota_prevents_single_kind_monopoly():
    """混合池里单一 kind 不得占满名额（旧实现实测 top4 全是 success_recipe）。"""
    now = datetime.now(timezone.utc).isoformat()
    pool = []
    for i in range(50):
        pool.append({"id": 1000 + i, "kind": "success_recipe",
                     "title": f"晋升因子 {chr(97 + i % 26)}{i} 结构", "summary": "s",
                     "quality": 0.95, "use_count": 0, "cycle": "M", "period": "1h",
                     "created_at": now})
    for j, k in enumerate([k for k in LR.ALL_KINDS if k != "success_recipe"]):
        for i in range(10):
            pool.append({"id": 5000 + j * 10 + i, "kind": k,
                         "title": f"{k} 标题 {chr(97 + i)}{i}", "summary": "s",
                         "quality": 0.7, "use_count": 0, "cycle": "M", "period": "1h",
                         "created_at": now})
    picked = LR.select_lessons(pool, limit=6)
    assert len(picked) == 6
    assert sum(1 for r in picked if r["kind"] == "success_recipe") <= 2
    assert len({r["kind"] for r in picked}) >= 5, "余量补齐必须偏向被选得最少的 kind"


def test_fill_prefers_least_selected_kind():
    """只有一种 kind 时不许崩，且必须填满（池子贫瘠不是拒绝给上下文的理由）。"""
    now = datetime.now(timezone.utc).isoformat()
    pool = [{"id": i, "kind": "gate_lesson", "title": f"门禁 {chr(97 + i)}{i}", "summary": "s",
             "quality": 0.7, "use_count": 0, "cycle": "M", "period": "1h",
             "created_at": now} for i in range(20)]
    assert len(LR.select_lessons(pool, limit=4)) == 4


def test_trajectory_and_pipeline_issue_are_reachable():
    """旧 `kind IN (...)` 把这两类结构性排除（实测 459/810 = 56.7% 不可达）。"""
    now = datetime.now(timezone.utc).isoformat()
    pool = []
    for i, k in enumerate(LR.ALL_KINDS):
        for j in range(5):
            pool.append({"id": i * 10 + j, "kind": k, "title": f"{k}-{j} 模板标题",
                         "summary": "s", "quality": 0.5, "use_count": 0,
                         "cycle": "M", "period": "1h", "created_at": now})
    kinds = {r["kind"] for r in LR.select_lessons(pool, limit=6)}
    assert "trajectory" in kinds and "pipeline_issue" in kinds


def test_signature_dedupe_avoids_near_identical_lines():
    """实测 153 条 gate_lesson 高度同质（只差数字），去重后不得占满配额。"""
    now = datetime.now(timezone.utc).isoformat()
    pool = [{"id": i, "kind": "gate_lesson", "title": f"15m 全量拒绝: {100 + i} 候选 0 晋升",
             "summary": "s", "quality": 0.7, "use_count": 0, "cycle": "M", "period": "15m",
             "created_at": now} for i in range(20)]
    picked = LR.select_lessons(pool, limit=6, quota={"gate_lesson": 3})
    assert len(picked) == 1, "同模板标题应只留一条"


def test_quality_beats_recency_within_kind():
    """打分不得因 recency 饱和而把高质量旧教训永久埋掉。"""
    now = datetime.now(timezone.utc).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    pool = [
        {"id": 1, "kind": "failure_case", "title": "高质旧案", "summary": "s",
         "quality": 0.95, "use_count": 5, "cycle": "M", "period": "1h", "created_at": old},
        {"id": 2, "kind": "failure_case", "title": "低质新案", "summary": "s",
         "quality": 0.3, "use_count": 0, "cycle": "M", "period": "1h", "created_at": now},
    ]
    picked = LR.select_lessons(pool, limit=1, quota={"failure_case": 1})
    assert picked and picked[0]["id"] == 1


# ───────────────────── ③ 写必有读：留痕 + 防误退役 ─────────────────────

def test_read_is_recorded_once_per_window(tmp_path, monkeypatch):
    db = _mkdb(tmp_path)
    lid = _add(db, "gate_lesson", "门禁教训 A")
    monkeypatch.setenv("V7_MEMORY_DB_PATH", str(db))
    assert LR.record_uses([lid], lane="mlto", cycle="M", period="1h") == 1
    assert _rows(db, "SELECT use_count,last_used_at FROM v7_lessons WHERE id=?", (lid,))[0][0] == 1
    log = _rows(db, "SELECT query FROM v7_retrieval_log")
    assert log and log[0][0].startswith("[mlto]"), "必须留下可分辨车道的读取痕迹"
    # 去重窗口内重复读不累加（决策循环 45s 一轮，否则次数被刷成噪声）
    assert LR.record_uses([lid], lane="mlto", cycle="M", period="1h") == 0
    assert _rows(db, "SELECT use_count FROM v7_lessons WHERE id=?", (lid,))[0][0] == 1


def test_dedupe_window_expires(tmp_path, monkeypatch):
    db = _mkdb(tmp_path)
    lid = _add(db, "decay_case", "衰减案例 A")
    monkeypatch.setenv("V7_MEMORY_DB_PATH", str(db))
    monkeypatch.setenv("LEARNING_READBACK_DEDUPE_SECONDS", "0")
    LR.record_uses([lid], lane="mlto")
    assert LR.record_uses([lid], lane="mlto") == 1


def test_read_prevents_maintenance_retirement(tmp_path, monkeypatch):
    """回归：真被读过的教训不得被 maintenance() 当"从未使用"退役。

    实测 active 中 751/810（92.7%）记 0 次，而旧读点是 `mode=ro` 不计次
    ⇒ 读过的教训 30 天后照样被清。
    """
    from backend.services.evolution import evolution_memory_v7 as EV7
    db = _mkdb(tmp_path)
    monkeypatch.setenv("V7_MEMORY_DB_PATH", str(db))
    monkeypatch.setattr(EV7, "_DB_PATH", db, raising=False)
    old = _add(db, "success_recipe", "老配方", age_days=45)
    new = _add(db, "success_recipe", "新配方", age_days=45)
    LR.record_uses([old], lane="mlto")
    out = EV7.maintenance(max_unused_age_days=30)
    assert out["retired"] == 1
    st = dict(_rows(db, "SELECT id,status FROM v7_lessons"))
    assert st[old] == "active", "被读过的教训必须保留"
    assert st[new] == "retired", "从未被读过的照旧退役（不改变旧语义）"


def test_lane_is_distinguishable_in_retrieval_log(tmp_path, monkeypatch):
    db = _mkdb(tmp_path)
    a = _add(db, "gate_lesson", "A")
    b = _add(db, "decay_case", "B")
    monkeypatch.setenv("V7_MEMORY_DB_PATH", str(db))
    LR.record_uses([a], lane="master")
    LR.record_uses([b], lane="mlto")
    qs = sorted(r[0] for r in _rows(db, "SELECT query FROM v7_retrieval_log"))
    assert qs[0].startswith("[master]") and qs[1].startswith("[mlto]")


# ───────────────────── ④ 输出与降级 ─────────────────────

def test_format_is_char_capped_and_marks_truncation():
    lessons = [{"kind": "gate_lesson", "cycle": "M", "period": "1h",
                "title": "标题" * 40, "summary": "摘要" * 200}]
    out = LR.format_for_prompt(lessons, None, char_budget=120)
    assert out.endswith("…（截断）") and len(out) <= 120 + len("…（截断）")


def test_snapshot_tolerates_missing_decay_file(tmp_path, monkeypatch):
    monkeypatch.setattr(LR, "_DECAY_STATUS_PATH", tmp_path / "nope.json", raising=False)
    snap = LR.factor_system_snapshot()
    assert snap["decay"]["n"] == 0
    assert isinstance(snap["runtime_weights"], dict)


def test_snapshot_parses_real_decay_shape(tmp_path, monkeypatch):
    """真实结构是 {"_meta","status","ic_history"}（曾按 "factors" 解析 ⇒ 只数出 3 个 unknown）。"""
    p = tmp_path / "decay.json"
    p.write_text(json.dumps({
        "_meta": {"saved_at": "x"},
        "status": {"rsi": {"recommendation": "retire"}, "obv": {"recommendation": "keep"},
                   "macd": {"recommendation": "reduce"}},
        "ic_history": {},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(LR, "_DECAY_STATUS_PATH", p, raising=False)
    snap = LR.factor_system_snapshot()
    assert snap["decay"]["n"] == 3
    assert snap["decay"]["by_recommendation"] == {"retire": 1, "keep": 1, "reduce": 1}
    assert "rsi" in snap["decay"]["retire_sample"]


def test_zero_weight_is_reported_in_both_calibers(tmp_path, monkeypatch):
    """F346：文件口径 0（=不参与合成）与读回口径（被夹到 ≥0.1）必须都出现在读数里。

    只报读回口径会打印"零权重 0 个"，与同一段里的 `retire=62` 自相矛盾 —— 读数骗人。
    """
    wf = tmp_path / "w.json"
    wf.write_text(json.dumps({"weights": {"rsi": 0.0, "zscore": 0.0, "obv": 0.68}},
                             ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(LR, "_WEIGHTS_PATH", wf, raising=False)
    snap = LR.factor_system_snapshot()
    rw = snap["runtime_weights"]
    assert rw.get("n_file_zero") == 2
    assert set(rw.get("file_zero_sample") or []) == {"rsi", "zscore"}
    txt = LR.format_for_prompt([], snap)
    assert "真源不一致" in txt and "不参与合成" in txt


def test_missing_db_degrades_to_empty_not_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("V7_MEMORY_DB_PATH", str(tmp_path / "absent.db"))
    assert LR.load_active_pool() == []
    assert LR.retrieve_lessons(lane="mlto") == []


def test_pool_is_not_truncated_by_id_window(tmp_path, monkeypatch):
    """旧实现 `ORDER BY id DESC LIMIT 200` 让老教训不可达；全量参与打分。"""
    db = _mkdb(tmp_path)
    ids = [_add(db, "gate_lesson", f"老教训 {i}", quality=0.99, age_days=60) for i in range(3)]
    for i in range(250):
        _add(db, "gate_lesson", f"新教训 {i}", quality=0.4)
    monkeypatch.setenv("V7_MEMORY_DB_PATH", str(db))
    pool = LR.load_active_pool()
    assert len(pool) == 253
    assert {r["id"] for r in pool} >= set(ids)


# ───────────────────── ⑤ 接线：真的挂上了决策路径 ─────────────────────

def test_master_block_delegates_to_readback_module():
    src = (ROOT / "backend" / "services" / "trading_analysts.py").read_text(encoding="utf-8")
    assert "learning_readback import decision_block" in src
    assert 'decision_block(lane="master"' in src
    # 方法体（docstring 之后）不得再有自建 sqlite 查询与 kind 白名单过滤
    region = src.split("def _build_v7_lessons_block")[1].split("def synthesize")[0]
    body = region.split('"""')[2] if region.count('"""') >= 2 else region
    assert "kind IN " not in body, "旧 kind 白名单把 trajectory/pipeline_issue 结构性排除"
    assert "sqlite3" not in body, "读回路必须走唯一入口模块，不得各写一份口径"


def test_mlto_feed_is_wired_with_key_and_gate(monkeypatch):
    src = (ROOT / "backend" / "services" / "mlto" / "brain.py").read_text(encoding="utf-8")
    assert '"factor_system_lessons"' in src, "读回路必须真的出现在 extras 里（否则算了没人看）"
    assert 'decision_block(lane="mlto"' in src
    assert "def _factor_system_lessons_feed" in src
    # 关闸 ⇒ 空串（不改 prompt 字节）
    from backend.services.mlto.brain import _factor_system_lessons_feed
    monkeypatch.delenv("LEARNING_READBACK_ENABLED", raising=False)
    assert _factor_system_lessons_feed() == ""


def test_extras_truncation_is_logged():
    src = (ROOT / "backend" / "services" / "mlto" / "brain.py").read_text(encoding="utf-8")
    assert "extras 超预算被截断" in src, "截断必须留日志（禁止静默退化）"


def test_stats_reports_read_accounting(tmp_path, monkeypatch):
    db = _mkdb(tmp_path)
    a = _add(db, "gate_lesson", "A")
    _add(db, "trajectory", "B")
    monkeypatch.setenv("V7_MEMORY_DB_PATH", str(db))
    LR.record_uses([a], lane="mlto")
    st = LR.stats()
    assert st["active"] == 2 and st["used_active"] == 1
    assert any(x["kind"] == "gate_lesson" and x["used"] == 1 for x in st["by_kind"])


# ───────────────────── ⑥ F360：回测侧归因进决策上下文（回测↔学习↔决策闭环） ─────────────────────

def _write_counterfactual(tmp_path, *, trusted=True, factors=None):
    rec = {
        "symbol": "BTC", "timeframe": "4h", "days": 180, "tier": "mid", "ts": 1.0,
        "baseline": {"sum_pnl": -6313.617878, "n_trades": 84},
        "incremental": factors or [
            {"factor": "evo_5a0c7a226b5446a4", "available": True,
             "d_sum_pnl": 27.786832, "d_trades": 1},
            {"factor": "group:5a0c7a226b5446a4", "available": True,
             "d_sum_pnl": 27.786832, "d_trades": 1},
            {"factor": "evo_180ee6eedd1fe9cc", "available": True,
             "d_sum_pnl": 0.0, "d_trades": 0},
            {"factor": "evo_dead", "available": False},
        ],
        "method": "OFAT ablation",
    }
    if trusted:
        rec["funding_fgi"] = {"funding_n": 3018, "fgi_n": 365, "valid": True}
    p = tmp_path / "cf.jsonl"
    p.write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
    return p


def test_backtest_attr_snapshot_reads_both_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(LR, "_COUNTERFACTUAL_PATH", _write_counterfactual(tmp_path), raising=False)
    monkeypatch.setattr(
        "backend.services.live_pipeline_backtest_engine.load_factor_attr",
        lambda run_id=None, limit=50: [{
            "run_id": "lp_x", "symbol": "BTC", "tier": "mid",
            "by_name": {"f1": {"coverage": 1.0, "n_long": 4, "n_short": 6,
                               "avg_bp_long": -97.98, "avg_bp_short": -15.0}},
        }], raising=False)
    snap = LR.backtest_attr_snapshot()
    cov, inc = snap["coverage"], snap["incremental"]
    assert cov["available"] and cov["n_factors"] == 1 and cov["run_id"] == "lp_x"
    assert "非增量 alpha" in cov["caveat"]
    assert inc["available"] and inc["trusted"] is True
    assert "不相加等于总收益" in inc["caveat"]


def test_untrusted_incremental_is_flagged_and_rendered(tmp_path, monkeypatch):
    """**最关键的一条**：缺 `funding_fgi.valid` 的记录是 F355 假零，必须显式标注不可信。"""
    monkeypatch.setattr(LR, "_COUNTERFACTUAL_PATH",
                        _write_counterfactual(tmp_path, trusted=False), raising=False)
    monkeypatch.setattr(
        "backend.services.live_pipeline_backtest_engine.load_factor_attr",
        lambda run_id=None, limit=50: [], raising=False)
    inc = LR.backtest_attr_snapshot()["incremental"]
    assert inc["trusted"] is False and inc["trust_note"]
    txt = LR.format_for_prompt([], {"backtest_attr": {"incremental": inc}})
    assert "不可信" in txt and "假零" in txt
    assert "结构性失效" in txt


def test_incremental_dedupe_prefers_group_row(tmp_path, monkeypatch):
    """别名 + group 行去重：同一底层因子只留一条，且优先保留 group 结论。"""
    monkeypatch.setattr(LR, "_COUNTERFACTUAL_PATH", _write_counterfactual(tmp_path), raising=False)
    inc = LR.backtest_attr_snapshot()["incremental"]
    names = [r["factor"] for r in inc["top"]]
    assert names.count("group:5a0c7a226b5446a4") == 1
    assert not any(n == "evo_5a0c7a226b5446a4" for n in names), "别名行应被 group 行吸收"
    # available=False 的记录不得进入
    assert all(r["factor"] != "evo_dead" for r in inc["top"])


def test_backtest_attr_snapshot_degrades_gracefully(tmp_path, monkeypatch):
    monkeypatch.setattr(LR, "_COUNTERFACTUAL_PATH", tmp_path / "absent.jsonl", raising=False)
    monkeypatch.setattr(
        "backend.services.live_pipeline_backtest_engine.load_factor_attr",
        lambda run_id=None, limit=50: [], raising=False)
    snap = LR.backtest_attr_snapshot()
    assert snap["coverage"]["available"] is False and snap["incremental"]["available"] is False
    # 渲染不得崩，且不产生空标题块
    txt = LR.format_for_prompt([], {"backtest_attr": snap})
    assert "🔬" not in txt


def test_factor_system_snapshot_includes_backtest_attr(tmp_path, monkeypatch):
    monkeypatch.setattr(LR, "_COUNTERFACTUAL_PATH", _write_counterfactual(tmp_path), raising=False)
    monkeypatch.setattr(LR, "_DECAY_STATUS_PATH", tmp_path / "none.json", raising=False)
    monkeypatch.setattr(LR, "_WEIGHTS_PATH", tmp_path / "none.json", raising=False)
    monkeypatch.setattr(
        "backend.services.live_pipeline_backtest_engine.load_factor_attr",
        lambda run_id=None, limit=50: [], raising=False)
    snap = LR.factor_system_snapshot()
    assert "backtest_attr" in snap
    txt = LR.format_for_prompt([], snap)
    assert "回测侧因子战绩" in txt and "并列" in txt


def test_rendered_block_respects_budget_with_backtest_section(tmp_path, monkeypatch):
    monkeypatch.setattr(LR, "_COUNTERFACTUAL_PATH", _write_counterfactual(tmp_path), raising=False)
    monkeypatch.setattr(LR, "_DECAY_STATUS_PATH", tmp_path / "none.json", raising=False)
    monkeypatch.setattr(LR, "_WEIGHTS_PATH", tmp_path / "none.json", raising=False)
    monkeypatch.setattr(
        "backend.services.live_pipeline_backtest_engine.load_factor_attr",
        lambda run_id=None, limit=50: [], raising=False)
    txt = LR.format_for_prompt([], LR.factor_system_snapshot(), char_budget=250)
    assert txt.endswith("…（截断）") and len(txt) <= 250 + len("…（截断）")
