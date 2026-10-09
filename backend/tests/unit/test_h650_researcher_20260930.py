# -*- coding: utf-8 -*-
"""[h650] mm 离线研究员:提示词硬规则 + 提案 schema 校验 + 队列落盘。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import researcher as R  # noqa: E402


def test_system_prompt_contains_laws_and_boundaries():
    p = R.SYSTEM_PROMPT
    assert "统一流定律" in p
    assert "趋势对齐" in p
    assert "形态×币匹配" in p
    assert "quote-time" in p
    assert "禁止输出「应该开/关某闸」的执行结论" in p
    assert "temperature" not in p  # 温度在调用参数,不在提示词


def test_user_prompt_has_numbered_blocks():
    u = R.build_user_prompt({
        "gate_stats": {"skip_counts": {"book_tight": 1}},
        "leg_stats": {"rows": []},
        "symbol_stats": {"BTC": {"spread_bp": 0.01}},
        "excluded": ["X"],
    })
    for k in ("{S1}", "{S2}", "{S3}", "{S4}", '"proposals"'):
        assert k in u


def test_validate_proposals_accepts_good_entries():
    data = {"proposals": [
        {"gate": "pullback_flow_block", "symbol": "ALL", "side": "buy",
         "threshold": 0.3, "rationale": "{S2} 卖边被逆向选择",
         "expected_bp_per_leg": 0.5, "min_sample_n": 15,
         "verdict_criteria": "12h Welch"},
    ]}
    out = R.validate_proposals(data)
    assert len(out) == 1 and out[0]["gate"] == "pullback_flow_block"
    assert out[0]["threshold"] == 0.3


def test_validate_proposals_drops_bad_entries():
    data = {"proposals": [
        {"gate": "", "symbol": "ALL", "side": "buy", "threshold": 1,
         "rationale": "x", "expected_bp_per_leg": 1, "min_sample_n": 15,
         "verdict_criteria": "x"},                       # 空闸名
        {"gate": "g", "symbol": "ALL", "side": "both", "threshold": 1,
         "rationale": "x", "expected_bp_per_leg": 1, "min_sample_n": 15,
         "verdict_criteria": "x"},                       # 非法 side
        {"gate": "g2", "symbol": "ALL", "side": "buy", "threshold": 1,
         "rationale": "x", "expected_bp_per_leg": 1, "min_sample_n": 5,
         "verdict_criteria": "x"},                       # 最小样本不足
        {"gate": "g3", "symbol": "ALL", "side": "buy", "threshold": 1,
         "rationale": "", "expected_bp_per_leg": 1, "min_sample_n": 15,
         "verdict_criteria": "x"},                       # 无 rationale
    ]}
    assert R.validate_proposals(data) == []


def test_validate_proposals_rejects_non_dict_and_empty():
    assert R.validate_proposals(None) == []
    assert R.validate_proposals({"proposals": []}) == []
    assert R.validate_proposals({"proposals": "oops"}) == []


def test_validate_proposals_caps_at_max():
    data = {"proposals": [
        {"gate": f"g{i}", "symbol": "ALL", "side": "buy", "threshold": 1,
         "rationale": "x", "expected_bp_per_leg": 1, "min_sample_n": 15,
         "verdict_criteria": "x"} for i in range(5)
    ]}
    assert len(R.validate_proposals(data, max_n=3)) == 3


def test_append_queue_writes_jsonl(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "QUEUE_FILE", tmp_path / "q.jsonl")
    assert R.append_queue({"ts": "t", "ok": True, "proposals": []})
    lines = (tmp_path / "q.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["ok"] is True


def test_gather_stats_shape(monkeypatch, tmp_path):
    from backend.services.market_maker import gather as G
    monkeypatch.setattr(G, "STATUS", tmp_path / "status.json")
    (tmp_path / "status.json").write_text(json.dumps({
        "skip_counts": {"book_tight": 3}, "gate_probe_counts": {},
        "direction_card": {"stopped": False}, "day_pnl_usd": -1.0,
        "fills_per_hour": 60.0, "symbols": ["BTC"], "states": {},
    }), encoding="utf-8")
    monkeypatch.setattr(G, "_market_spreads", lambda syms: {"BTC": {"spread_bp": 0.01}})
    monkeypatch.setattr(G, "fetch_open_legs", lambda **kw: [])
    monkeypatch.setattr(G, "direction_rows_from_legs", lambda legs, mids: [])
    st = G.gather_researcher_stats(lane_id="mm_asterdex", hours=4)
    assert st["gate_stats"]["skip_counts"]["book_tight"] == 3
    assert st["symbol_stats"]["BTC"]["spread_bp"] == 0.01
    assert st["leg_stats"]["n_open_legs"] == 0
