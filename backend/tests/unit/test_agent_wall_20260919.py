# -*- coding: utf-8 -*-
"""[2026-09-19 Agent Wall] 画布数据层契约：注册表完整性 / 健康映射 / 增量游标 / 审计 / 无 fan-out。

## 设计意图（`docs/Agent画布模块设计_20260919.md`）
- **每个 agent 一个节点、按边界分组、节点各自滚屏、连线看关系**；
- 健康状态**复用 `job_registry` 的 stale 判定**（一套口径，禁止另算）；
- 单页轮询只允许两个请求（state + tail），**禁止每节点一个请求**
  —— 本项目后端单进程 GIL 长期贴 1 核，24 路 fan-out 正是"前端刷新慢"的根因之一；
- tail 必须**增量**读日志：`logs/brain_subprocess.log` 已 146MB / 94.6 万行，禁止每请求全扫。

本文件锁住这些性质。所有测试**不依赖运行中的后端**（直接调服务层）。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services import agent_wall as W  # noqa: E402

VALID_GROUPS = {"G0", "G1", "G2", "G3", "G4", "G5"}
VALID_SIZES = {"XL", "L", "M", "S"}
# [轮125 2026-09-19] 新增 `json_file`：读**运行产物 JSON**（E1 卡用它 ——
# 那个节点每天 08:20 才跑一次，日志里非运行时段本来就没有业务行，
# 此前用日志过滤只能捞到卡片自己的轮询访问日志）。
# [轮139 2026-09-20] 新增 `db_table`：**表驱动卡**（风控官判定 / 六域信号 / 因子暴露快照）。
# 为什么必须有它：这些模块不写日志、只有一张表 —— 用 `kind:file` 硬套日志过滤会得到 0 行，
# 于是出现"0 行却显示 ok"的假绿（用户抱怨过「很多没有数据」）。
# 现在按**表行数 + 最新 ts** 判状态：0 行 = never（诚实）。
VALID_KINDS = {"file", "json_latest", "json_thesis", "json_file", "db_table", "none"}
VALID_STATUS = {"ok", "stale", "dead", "never", "disabled", "unknown"}


@pytest.fixture(autouse=True)
def _clear_offsets():
    W._OFFSETS.clear()
    yield
    W._OFFSETS.clear()


# ───────────────────── 1. 注册表完整性 ─────────────────────

def test_node_ids_unique_and_fields_valid():
    ids = [n["id"] for n in W.NODES]
    assert len(ids) == len(set(ids)), f"节点 id 重复: {ids}"
    assert len(W.NODES) >= 20, f"节点太少（{len(W.NODES)}），画布无法覆盖排查清单"
    for n in W.NODES:
        assert n["group"] in VALID_GROUPS, f"{n['id']} 分组非法: {n['group']}"
        assert n["size"] in VALID_SIZES, f"{n['id']} 尺寸非法: {n['size']}"
        assert n.get("label") and n.get("role"), f"{n['id']} 缺 label/role"
        src = n.get("source") or {}
        assert src.get("kind") in VALID_KINDS, f"{n['id']} source.kind 非法: {src.get('kind')}"
        if src.get("kind") == "none":
            # 禁止"无解释的死节点"：必须给出停用/废弃依据
            assert n.get("status_hint") in ("disabled", "static_dead") and src.get("reason"), \
                f"{n['id']} 是空流节点但没写依据（禁止静默死链）"


def test_edges_reference_existing_nodes_and_are_directed():
    ids = {n["id"] for n in W.NODES}
    for e in W.EDGES:
        assert e["from"] in ids, f"边起点不存在: {e['from']}"
        assert e["to"] in ids, f"边终点不存在: {e['to']}"
        assert e["from"] != e["to"], f"自环边无意义: {e['from']}"
        assert e.get("kind") and e.get("label"), f"边缺 kind/label: {e}"


def test_build_state_drops_dangling_edges(monkeypatch):
    """悬空边必须被丢弃（宁可少画一条，也不画假边）。"""
    monkeypatch.setattr(W, "EDGES", W.EDGES + [
        {"from": "does_not_exist", "to": "brain_mid", "kind": "x", "label": "x"}])
    st = W.build_state()
    assert all(e["from"] in {n["id"] for n in st["nodes"]} for e in st["edges"])


def test_build_state_shape_and_group_summary():
    st = W.build_state()
    assert set(st) >= {"generated_at_ms", "groups", "nodes", "edges", "note"}
    assert sum(g["total"] for g in st["groups"]) == len(st["nodes"])
    for g in st["groups"]:
        assert g["total"] == sum(g[k] for k in
                                ("ok", "stale", "dead", "never", "disabled", "unknown"))
    for n in st["nodes"]:
        assert n["status"] in VALID_STATUS, f"{n['id']} 状态非法: {n['status']}"
        assert n["status_detail"].get("reason") is not None


# ───────────────────── 2. 健康映射（复用 job_registry 口径） ─────────────────────

@pytest.mark.parametrize("stale,want", [
    (None, "ok"), ("ok", "ok"), ("warn", "stale"),
    ("critical", "dead"), ("never_ran", "never"),
])
def test_job_stale_maps_to_canvas_status(stale, want):
    node = {"id": "x", "job": "agent_demo", "expected_interval_s": 900}
    jobs = {"agent_demo": {"stale": stale, "enabled": True, "run_count": 3,
                           "cadence": "interval 900s", "last_end": "2026-09-19T16:00:00"}}
    got = W._status_for_node(node, jobs)
    assert got["status"] == want, f"stale={stale} 应映射为 {want}，实际 {got['status']}"
    assert got["run_count"] == 3 and got["declared_interval_s"] == 900


def test_disabled_hint_overrides_never_ran():
    """结构性停用 ≠ 故障：两者含义不同，画布必须区分（但 reason 保留 job 判定）。"""
    node = {"id": "timing", "job": "agent_timing", "status_hint": "disabled",
            "cadence_label": "2026-09-05 起停用"}
    jobs = {"agent_timing": {"stale": "never_ran", "enabled": True}}
    got = W._status_for_node(node, jobs)
    assert got["status"] == "disabled"
    assert "never_ran" in got["reason"], "不得丢掉 job_registry 的原始判定"


def test_job_enabled_false_is_disabled():
    node = {"id": "x", "job": "j"}
    got = W._status_for_node(node, {"j": {"stale": None, "enabled": False}})
    assert got["status"] == "disabled"


def test_missing_job_falls_back_to_artifact_then_hint():
    assert W._status_for_node({"id": "s", "status_hint": "static_dead"}, {})["status"] == "dead"
    assert W._status_for_node({"id": "d", "status_hint": "disabled"}, {})["status"] == "disabled"
    # 无 job、无 hint、无产物 ⇒ unknown（而不是假装 ok）
    assert W._status_for_node({"id": "u", "source": {"kind": "json_latest", "agent": "nope"}}, {})["status"] == "never"


def test_only_three_endpoints_and_no_write_routes():
    src = (ROOT / "backend" / "api" / "agent_wall_routes.py").read_text(encoding="utf-8")
    import re
    routes = re.findall(r'@router\.(get|post|put|delete)\("([^"]+)"', src)
    assert sorted(r[1] for r in routes) == ["/audit", "/state", "/tail"], f"端点清单变更: {routes}"
    assert all(m == "get" for m, _ in routes), "画布接口必须全部只读"
    assert "_clip_text" not in src  # 防误改


def test_tail_requires_explicit_nodes_and_caps_at_12():
    src = (ROOT / "backend" / "api" / "agent_wall_routes.py").read_text(encoding="utf-8")
    assert "ids[:12]" in src, "缺少节点数上限（会被当成全网拉取接口）"
    # 空 nodes ⇒ 空结果（不猜、不默认全网）
    assert W.tail([])["lines"] == {}


def test_state_does_not_embed_lines():
    """state 只给骨架：禁止把日志行塞进 state（否则画布一次拉全量日志）。"""
    st = W.build_state()
    for n in st["nodes"]:
        assert "lines" not in n, f"{n['id']} 的 state 里带了日志行"


# ───────────────────── 3. 增量游标（tail 的核心性质） ─────────────────────

def _mk(tmp_path: Path, text: str = "") -> Path:
    p = tmp_path / "node.log"
    p.write_text(text, encoding="utf-8")
    return p


def test_tail_first_read_scans_backwards_for_matches(tmp_path):
    """首读必须做**有界反向扫描**：尾部无命中时也能找到更早的命中行。"""
    rows = [f"2026-09-19 10:00:{i:02d} [INFO] filler line {i}" for i in range(60)]
    rows[5] = "2026-09-19 10:00:05 [INFO] [MidLong] stage=fuse symbol=BTC"
    p = _mk(tmp_path, "\n".join(rows) + "\n")
    got = W._read_incremental("t1", p, "MidLong")
    assert len(got) == 1 and "fuse" in got[0]["raw"], f"反向扫描未命中: {got}"
    assert "中线" in got[0]["text"], f"应编译为中文叙述，实际: {got[0]['text']}"
    assert got[0]["ts"] == "2026-09-19 10:00:05"


def test_tail_is_incremental_and_only_returns_new_lines(tmp_path):
    p = _mk(tmp_path, "2026-09-19 10:00:00 [INFO] first\n")
    first = W._read_incremental("t2", p, None)
    assert len(first) == 1
    second = W._read_incremental("t2", p, None)
    assert second == [], "无新内容必须返回空（否则每轮重复推送全量）"
    with p.open("a", encoding="utf-8") as fh:
        fh.write("2026-09-19 10:00:10 [INFO] second\n")
    third = W._read_incremental("t2", p, None)
    assert [x["text"] for x in third] == ["2026-09-19 10:00:10 [INFO] second"], "必须只返回新增行"


def test_tail_resets_after_truncation(tmp_path):
    p = _mk(tmp_path, "\n".join(f"2026-09-19 10:00:{i:02d} old {i}" for i in range(10)) + "\n")
    W._read_incremental("t3", p, None)
    p.write_text("2026-09-19 11:00:00 [INFO] after-rotate\n", encoding="utf-8")  # 轮转/截断
    got = W._read_incremental("t3", p, None)
    assert any("after-rotate" in x["text"] for x in got), "轮转后必须能读到新内容"


def test_tail_line_cap_enforced(tmp_path):
    p = _mk(tmp_path, "\n".join(f"2026-09-19 10:00:00 hit {i}" for i in range(500)) + "\n")
    got = W._read_incremental("t4", p, "hit", max_lines=50)
    assert len(got) == 50, f"行数上限失效: {len(got)}"


def test_tail_missing_file_returns_empty():
    assert W._read_incremental("t5", Path("Z:/definitely/not/here.log"), None) == []


def test_tail_multi_node_shape():
    out = W.tail(["anomaly", "brain_mid"])
    assert set(out) >= {"lines", "counts", "requested"}
    assert out["requested"] == ["anomaly", "brain_mid"]
    for nid in ("anomaly", "brain_mid"):
        assert isinstance(out["lines"][nid], list)
        for ln in out["lines"][nid]:
            assert set(ln) >= {"ts", "text"}, f"行结构缺少 ts/text: {ln}"
            assert "raw" in ln, "必须保留原文 raw（中文为编译结果，禁止不可回溯）"


# ───────────────────── 4. 审计 ─────────────────────

def test_audit_shape_and_evidence_present():
    a = W.audit()
    assert set(a) >= {"generated_at_ms", "counts", "findings", "note"}
    assert isinstance(a["findings"], list) and a["findings"], "审计不得为空（本轮已确证多条缺陷）"
    for f in a["findings"]:
        assert f["severity"] in ("high", "medium", "low", "info"), f
        assert f["what"] and f["evidence"], f"缺 what/evidence: {f}"
        assert f["kind"] in ("live", "static_verified", "fixed"), f"未标注结论来源: {f}"
    assert sum(a["counts"].values()) == len(a["findings"])


def test_audit_covers_the_verified_structural_defects():
    ids = {f["id"] for f in W.audit()["findings"]}
    for must in ("experiments_empty", "timing_stale_no_gate",
                 "long_tick_dead_config", "trend_chart_overclock", "restart_storm"):
        assert must in ids, f"已实测确认的结构性缺陷未纳入审计: {must}"


def test_audit_live_section_marks_source():
    a = W.audit()
    live = [f for f in a["findings"] if f["kind"] == "live"]
    assert live, "至少应有现场判定项（job_registry 的 never_ran/critical 等）"
    for f in live:
        assert f["measured_at"], "现场判定必须带测量时间（禁止无时间戳的断言）"


def test_audit_never_raises_even_if_registry_broken(monkeypatch):
    monkeypatch.setattr(W, "_jobs_by_name", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    # _jobs_by_name 自身有兜底；这里直接验证 audit 对异常输入的容忍度
    try:
        W.audit()
    except RuntimeError:
        pytest.fail("audit 不应把上游异常透传给请求（画布会整页崩）")


def test_tail_is_fast_on_second_call():
    """第二次调用必须是增量（毫秒级），否则 146MB 日志会把端点拖垮。"""
    W.tail(["brain_mid"])
    t0 = time.perf_counter()
    W.tail(["brain_mid"])
    dt = (time.perf_counter() - t0) * 1000
    assert dt < 200, f"增量读耗时 {dt:.0f}ms，疑似退化为全量扫描"
