# -*- coding: utf-8 -*-
"""S2-9 选币因子自适应（ic_weights.py）单元测试。

覆盖：
- rank_ic：Spearman 秩相关正确性（正/负/平局）
- compute_factor_ics：样本门控 / 缺失维度跳过 / 正负 IC
- to_v3_weights：归一化 / 负 IC 弃用 / 全非正回退
- dedup_by_correlation：同质剔除 / 阈值关闭 / 正交保留
- factor_vector / _parse_snapshot：快照解析（dict / str / 嵌套 parts）
- load_ic_samples：DB 过滤 + 命中样本门控（_FakeDb 模式）
- llm_compose / _extract_symbol_list：LLM 名单解析 / 失败回退 / 越界过滤
"""
import math

import pytest

from backend.services.coin_rank.ic_weights import (
    FACTOR_KEYS,
    IcSample,
    _parse_snapshot,
    compute_factor_ics,
    dedup_by_correlation,
    factor_vector,
    load_ic_samples,
    llm_compose,
    rank_ic,
    to_v3_weights,
    _extract_symbol_list,
)


# ─────────────────────────────────────────────────────────────
# Spearman 秩相关
# ─────────────────────────────────────────────────────────────
class TestRankIc:
    def test_perfect_positive(self):
        xs = [0.1, 0.2, 0.3, 0.4, 0.5]
        ys = [1, 2, 3, 4, 5]
        assert rank_ic(xs, ys) == pytest.approx(1.0, abs=1e-9)

    def test_perfect_negative(self):
        xs = [0.1, 0.2, 0.3, 0.4, 0.5]
        ys = [5, 4, 3, 2, 1]
        assert rank_ic(xs, ys) == pytest.approx(-1.0, abs=1e-9)

    def test_insufficient_samples(self):
        assert rank_ic([1.0], [1.0]) is None
        assert rank_ic([], []) is None

    def test_ties_get_average_rank(self):
        # 平局取平均秩：[-1, 1, 1] 秩 [1, 2.5, 2.5]
        xs = [-1.0, 1.0, 1.0]
        ys = [0.0, 1.0, 1.0]
        ic = rank_ic(xs, ys)
        assert ic is not None and abs(ic - 1.0) < 1e-9

    def test_monotone_outcome_separates(self):
        # 因子分与命中完全单调 → IC 高
        xs = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
        ys = [0, 0, 0, 0, 1, 1, 1, 1, 1]
        assert rank_ic(xs, ys) > 0.5


# ─────────────────────────────────────────────────────────────
# IC 计算与门控
# ─────────────────────────────────────────────────────────────
def _samples_with(base_vals, hit_flags, key="base_score"):
    return [
        IcSample(symbol=f"S{i}", factors={key: v}, hit=h)
        for i, (v, h) in enumerate(zip(base_vals, hit_flags))
    ]


class TestComputeFactorIcs:
    def test_positive_ic_detected(self):
        xs = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0] * 4
        ys = [0, 0, 0, 0, 1, 1, 1, 1, 1, 1] * 4
        ics = compute_factor_ics(_samples_with(xs, ys), min_samples=30)
        assert ics["base_score"] > 0.5

    def test_negative_ic_detected(self):
        xs = [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.0] * 4
        ys = [0, 0, 0, 0, 1, 1, 1, 1, 1, 1] * 4
        ics = compute_factor_ics(_samples_with(xs, ys), min_samples=30)
        assert ics["base_score"] < -0.5

    def test_below_min_samples_returns_zero(self):
        xs = [0.1, 0.2, 0.3, 0.4, 0.5]
        ys = [0, 1, 0, 1, 0]
        ics = compute_factor_ics(_samples_with(xs, ys), min_samples=30)
        assert ics["base_score"] == 0.0

    def test_missing_dimension_skipped(self):
        samples = _samples_with([0.1] * 40, [1] * 40)
        ics = compute_factor_ics(samples, min_samples=30)
        # flow_score 维度缺失 → 0（不参与加权，也不会报错）
        assert ics["flow_score"] == 0.0
        assert ics["base_score"] == 0.0  # 常数因子无信息

    def test_constant_factor_zero_ic(self):
        samples = _samples_with([0.5] * 40, [0, 1] * 20)
        ics = compute_factor_ics(samples, min_samples=30)
        assert ics["base_score"] == 0.0

    def test_non_numeric_values_skipped(self):
        samples = [IcSample(symbol="S1", factors={"base_score": "bad"}, hit=True)] * 40
        ics = compute_factor_ics(samples, min_samples=30)
        assert ics["base_score"] == 0.0


# ─────────────────────────────────────────────────────────────
# IC → V3 权重
# ─────────────────────────────────────────────────────────────
class TestToV3Weights:
    def test_normalizes_positive_ics(self):
        ics = {"base_score": 0.3, "flow_score": 0.1, "whale_score": 0.0}
        weights, enabled = to_v3_weights(ics)
        assert enabled is True
        assert weights["base"] == pytest.approx(0.75)
        assert weights["flow"] == pytest.approx(0.25)
        assert "whale" not in weights

    def test_negative_ic_dropped(self):
        ics = {"base_score": -0.4, "flow_score": 0.2}
        weights, enabled = to_v3_weights(ics)
        assert enabled is True
        assert "base" not in weights
        assert weights["flow"] == pytest.approx(1.0)

    def test_all_non_positive_falls_back(self):
        fallback = {"base": 0.5, "flow": 0.5}
        weights, enabled = to_v3_weights({"base_score": -0.1, "flow_score": 0.0}, fallback=fallback)
        assert enabled is False
        assert weights == fallback

    def test_empty_ics_falls_back(self):
        weights, enabled = to_v3_weights({})
        assert enabled is False
        assert set(weights) == {"base", "flow", "whale", "news", "sector"}


# ─────────────────────────────────────────────────────────────
# 组合相关性去重
# ─────────────────────────────────────────────────────────────
class TestDedupByCorrelation:
    def test_identical_vectors_keep_first(self):
        ranked = [
            ("A", {"base_score": 0.9, "mom_score": 0.8}),
            ("B", {"base_score": 0.9, "mom_score": 0.8}),
            ("C", {"base_score": 0.2, "vola_score": 0.1}),
        ]
        kept = dedup_by_correlation(ranked, threshold=0.85)
        assert kept == ["A", "C"]

    def test_threshold_disabled_keeps_all(self):
        ranked = [
            ("A", {"base_score": 0.9}),
            ("B", {"base_score": 0.9}),
        ]
        assert dedup_by_correlation(ranked, threshold=0.0) == ["A", "B"]
        assert dedup_by_correlation(ranked, threshold=1.0) == ["A", "B"]

    def test_orthogonal_vectors_kept(self):
        ranked = [
            ("A", {"base_score": 1.0, "vola_score": 0.0}),
            ("B", {"base_score": 0.0, "vola_score": 1.0}),
        ]
        assert dedup_by_correlation(ranked, threshold=0.85) == ["A", "B"]

    def test_empty_or_missing_vectors(self):
        assert dedup_by_correlation([], threshold=0.85) == []
        ranked = [("A", {}), ("B", {"base_score": 0.5})]
        assert dedup_by_correlation(ranked, threshold=0.85) == ["A", "B"]

    def test_partial_overlap_high_similarity(self):
        # 高重叠因子 → 相似度接近 1 → 剔除
        ranked = [
            ("A", {"base_score": 0.9, "mom_score": 0.8, "trend_score": 0.7}),
            ("B", {"base_score": 0.88, "mom_score": 0.82, "trend_score": 0.72}),
        ]
        kept = dedup_by_correlation(ranked, threshold=0.85)
        assert kept == ["A"]


# ─────────────────────────────────────────────────────────────
# 快照解析
# ─────────────────────────────────────────────────────────────
class TestSnapshotParsing:
    def test_factor_vector_extracts_numeric(self):
        snap = {
            "base_score": 0.9,
            "flow_score": None,
            "whale_score": "0.5",
            "mom_score": 0.3,
            "unknown_key": 1.0,
        }
        vec = factor_vector(snap)
        assert vec["base_score"] == 0.9
        assert "flow_score" not in vec
        assert vec["whale_score"] == 0.5
        assert vec["mom_score"] == 0.3
        assert "unknown_key" not in vec

    def test_factor_vector_uses_parts_fallback(self):
        snap = {"parts": {"base_score": 0.6, "flow_score": 0.4}}
        vec = factor_vector(snap)
        assert vec["base_score"] == 0.6
        assert vec["flow_score"] == 0.4

    def test_factor_vector_ignores_non_finite(self):
        snap = {"base_score": float("nan"), "mom_score": float("inf"), "trend_score": 0.5}
        vec = factor_vector(snap)
        assert vec == {"trend_score": 0.5}

    def test_parse_snapshot_dict_and_str(self):
        assert _parse_snapshot({"base_score": 0.5}) == {"base_score": 0.5}
        assert _parse_snapshot('{"base_score": 0.5}') == {"base_score": 0.5}
        assert _parse_snapshot("not-json") is None
        assert _parse_snapshot(None) is None
        assert _parse_snapshot('["list"]') is None
        assert _parse_snapshot(123) is None


# ─────────────────────────────────────────────────────────────
# 样本加载（_FakeDb 模式）
# ─────────────────────────────────────────────────────────────
class _FakeRow:
    def __init__(self, symbol, snapshot, hit):
        self.symbol = symbol
        self.factor_snapshot_json = snapshot
        self.hit_24h = hit


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *args, **kwargs):
        return self

    def limit(self, n):
        return self

    def all(self):
        return self._rows


class _FakeDb:
    def __init__(self, rows):
        self._rows = rows

    def query(self, model):
        return _FakeQuery(self._rows)


@pytest.fixture(autouse=True)
def _patch_db(monkeypatch):
    """把 load_ic_samples 的 AutoCoinSelection 导入替换为桩模型。"""
    import types
    class FakeModel:
        pass
    FakeModel.action = "injected"
    FakeModel.factor_snapshot_json = None
    FakeModel.hit_24h = None
    FakeModel.created_at = None
    monkeypatch.setitem(
        __import__("sys").modules["backend.services.coin_rank.ic_weights"].__dict__,
        "AutoCoinSelection",
        FakeModel,
    )


def _rows(n=40, hit_ratio=0.5, key="base_score"):
    rows = []
    for i in range(n):
        v = (i + 1) / n  # 升序因子分
        hit = (i % 10) < (10 * hit_ratio)
        rows.append(_FakeRow(f"S{i}", {key: v}, hit))
    return rows


class TestLoadIcSamples:
    def test_loads_and_extracts(self):
        samples = load_ic_samples(_FakeDb(_rows()), lookback_days=45)
        assert len(samples) == 40
        assert samples[0].symbol == "S0"
        assert samples[0].factors["base_score"] == pytest.approx(1 / 40)

    def test_hit_samples_gate(self):
        # 命中样本不足 min_hit → []
        rows = [_FakeRow(f"S{i}", {"base_score": 0.5}, hit=False) for i in range(10)]
        assert load_ic_samples(_FakeDb(rows), min_hit=2) == []

    def test_empty_snapshot_rows_skipped(self):
        rows = [_FakeRow("S1", None, True), _FakeRow("S2", "bad", True)]
        assert load_ic_samples(_FakeDb(rows), min_hit=1) == []

    def test_str_snapshot_parsed(self):
        rows = [_FakeRow("S1", '{"base_score": 0.7}', True)]
        samples = load_ic_samples(_FakeDb(rows), min_hit=1)
        assert len(samples) == 1
        assert samples[0].factors["base_score"] == 0.7


# ─────────────────────────────────────────────────────────────
# LLM 组合决策
# ─────────────────────────────────────────────────────────────
class TestExtractSymbolList:
    def test_plain_json_array(self):
        assert _extract_symbol_list('["BTC", "ETH", "SOL"]') == ["BTC", "ETH", "SOL"]

    def test_noise_around_json(self):
        text = '好的，以下是名单：\n```json\n["BTC", "ETH"]\n```\n请查收'
        assert _extract_symbol_list(text) == ["BTC", "ETH"]

    def test_lowercase_normalized(self):
        assert _extract_symbol_list('["btc", "eth"]') == ["BTC", "ETH"]

    def test_invalid(self):
        assert _extract_symbol_list("无法决定") is None
        assert _extract_symbol_list("") is None
        assert _extract_symbol_list("[1, 2, 3]") is None
        assert _extract_symbol_list("没有名单") is None
    
    def test_wrapped_object_with_array_tolerated(self):
        # 宽容解析：LLM 偶尔包一层 {"symbols": [...]}，仍能提取数组
        assert _extract_symbol_list('{"symbols": ["BTC"]}') == ["BTC"]


class TestLlmCompose:
    def test_happy_path(self):
        pool = [
            {"symbol": "BTC", "score": 0.9, "confidence": 0.8, "reason": "r", "factors": {"base_score": 0.9}},
            {"symbol": "ETH", "score": 0.8, "confidence": 0.7, "reason": "r", "factors": {"base_score": 0.8}},
        ]
        picked = llm_compose(pool, lambda prompt: '["BTC"]')
        assert picked == ["BTC"]

    def test_caller_exception_returns_none(self):
        pool = [{"symbol": "BTC", "score": 0.9, "confidence": 0.8, "reason": "r", "factors": {}}]

        def boom(prompt):
            raise RuntimeError("llm down")

        assert llm_compose(pool, boom) is None

    def test_unparseable_output_returns_none(self):
        pool = [{"symbol": "BTC", "score": 0.9, "confidence": 0.8, "reason": "r", "factors": {}}]
        assert llm_compose(pool, lambda prompt: "今天天气不错") is None

    def test_out_of_pool_filtered_and_capped(self):
        pool = [
            {"symbol": "BTC", "score": 0.9, "confidence": 0.8, "reason": "r", "factors": {}},
            {"symbol": "ETH", "score": 0.8, "confidence": 0.7, "reason": "r", "factors": {}},
            {"symbol": "SOL", "score": 0.7, "confidence": 0.6, "reason": "r", "factors": {}},
        ]
        picked = llm_compose(pool, lambda prompt: '["BTC", "DOGE", "ETH", "SOL"]', max_select=2)
        assert picked == ["BTC", "ETH"]

    def test_empty_pool_returns_none(self):
        assert llm_compose([], lambda prompt: "[]") is None

    def test_prompt_contains_pool_table(self):
        captured = {}

        def spy(prompt):
            captured["prompt"] = prompt
            return '["BTC"]'

        pool = [{"symbol": "BTC", "score": 0.9, "confidence": 0.8, "reason": "reasons", "factors": {"base_score": 0.9}}]
        llm_compose(pool, spy)
        assert "BTC" in captured["prompt"]
        assert "base_score=0.90" in captured["prompt"]
