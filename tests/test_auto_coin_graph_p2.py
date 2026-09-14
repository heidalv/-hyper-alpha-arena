# -*- coding: utf-8 -*-
"""P2：graph 维度并入 AutoCoin V3 的单元测试。"""
import pytest

from backend.services.auto_coin_selector import AutoCoinSelector, CandidateCoin
from backend.services.coin_rank import ic_weights


def _selector():
    return AutoCoinSelector.__new__(AutoCoinSelector)


def _candidate(sym: str, score: float = 0.5) -> CandidateCoin:
    return CandidateCoin(symbol=sym, score=score, onchain_data={})


# ─────────────────────────────────────────────────────────────
# _compose_v3_score
# ─────────────────────────────────────────────────────────────
def test_compose_v3_with_graph_weight():
    sel = _selector()
    # 显式权重：base 0.8 + graph 0.2
    comp, meta = sel._compose_v3_score(0.5, graph_score=0.9,
                                       weights={"base": 0.8, "graph": 0.2})
    assert comp == pytest.approx((0.5 * 0.8 + 0.9 * 0.2) / 1.0)
    assert meta["parts"]["graph"] == 0.9


def test_compose_v3_graph_skipped_when_weight_zero():
    sel = _selector()
    comp_graph, _ = sel._compose_v3_score(0.5, graph_score=0.9,
                                          weights={"base": 0.8, "graph": 0.0})
    comp_none, _ = sel._compose_v3_score(0.5, graph_score=None,
                                         weights={"base": 0.8, "graph": 0.0})
    assert comp_graph == comp_none == pytest.approx(0.5)


def test_compose_v3_graph_absent_dimension_normalizes():
    sel = _selector()
    # graph=None 时维度缺省，其余维重新归一化（旧行为不变）
    comp, meta = sel._compose_v3_score(0.6, flow_score=0.4,
                                       weights={"base": 0.5, "flow": 0.5, "graph": 0.3})
    assert comp == pytest.approx((0.6 * 0.5 + 0.4 * 0.5) / 1.0)
    assert "graph" not in meta["parts"]


# ─────────────────────────────────────────────────────────────
# ic_weights 接线
# ─────────────────────────────────────────────────────────────
def test_ic_weights_factor_keys_include_graph():
    assert "graph_score" in ic_weights.FACTOR_KEYS
    assert ic_weights._V3_KEY_MAP["graph_score"] == "graph"


def test_ic_weights_to_v3_weights_graph_positive_ic():
    ics = {"base_score": 0.5, "graph_score": 0.3, "sector_rs_score": -0.1, "flow_score": 0.0}
    w, enabled = ic_weights.to_v3_weights(ics)
    assert enabled
    assert "graph" in w and w["graph"] == pytest.approx(0.3 / 0.8)
    assert "sector" not in w  # 负 IC 弃用


def test_ic_weights_graph_missing_samples_excluded():
    """历史样本无 graph_score → IC 0 → 不进归一化权重（向后兼容）。"""
    ics = {"base_score": 0.6, "flow_score": 0.2, "graph_score": 0.0}
    w, enabled = ic_weights.to_v3_weights(ics)
    assert enabled
    assert "graph" not in w


# ─────────────────────────────────────────────────────────────
# _apply_v3_rescore 集成
# ─────────────────────────────────────────────────────────────
def test_v3_rescore_injects_graph_dimension(monkeypatch):
    """V3 开启 + graph 提供方就绪 → scores_detail 含 graph_score，composite 被抬升。"""
    import backend.config.settings as settings

    sel = _selector()
    sel._score_v3_enabled = lambda db=None: True
    monkeypatch.setattr(settings, "AUTO_COIN_SECTOR_SIGNAL_ENABLED", False)
    monkeypatch.setattr(settings, "AUTO_COIN_W_GRAPH", 0.1)
    monkeypatch.setattr(settings, "AUTO_COIN_GRAPH_SCORE_ENABLED", True)
    monkeypatch.setattr("backend.services.graph_rank.service.graph_score_for_symbols",
                        lambda symbols: {"A": 0.8, "B": 0.2})

    cands = [_candidate("A"), _candidate("B")]
    out = sel._apply_v3_rescore(cands, db=None)
    by = {c.symbol: c for c in out}
    assert by["A"].scores_detail["graph_score"] == 0.8
    assert by["B"].scores_detail["graph_score"] == 0.2
    # A 的 graph 更高 → composite 高于 B（其余维相同）
    assert by["A"].score > by["B"].score


def test_v3_rescore_graph_provider_failure_zero_impact(monkeypatch):
    """graph 提供方爆炸 → 维度缺省，行为与不注入一致。"""
    import backend.config.settings as settings

    sel = _selector()
    sel._score_v3_enabled = lambda db=None: True
    monkeypatch.setattr(settings, "AUTO_COIN_SECTOR_SIGNAL_ENABLED", False)
    monkeypatch.setattr(settings, "AUTO_COIN_GRAPH_SCORE_ENABLED", True)
    monkeypatch.setattr("backend.services.graph_rank.service.graph_score_for_symbols",
                        lambda symbols: (_ for _ in ()).throw(RuntimeError("boom")))

    cands = [_candidate("A")]
    out = sel._apply_v3_rescore(cands, db=None)
    assert len(out) == 1
    assert out[0].scores_detail.get("graph_score") is None
    assert out[0].score == pytest.approx(0.5)


def test_v3_rescore_disabled_untouched():
    """V3 关闭 → 原样返回（新增代码不执行）。"""
    sel = _selector()
    sel._score_v3_enabled = lambda db=None: False
    cands = [_candidate("A")]
    assert sel._apply_v3_rescore(cands, db=None) == cands
