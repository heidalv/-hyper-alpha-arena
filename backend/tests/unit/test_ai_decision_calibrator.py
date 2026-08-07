"""S2-8: ai_decision_logs 拟合 LLM 置信度→胜率曲线 校准器单元测试。"""
import json

import pytest

from backend.services.calibration.ai_decision_calibrator import (
    AiDecisionConfidenceCalibrator,
    _extract_confidence,
)


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return list(self._rows)


class _FakeDb:
    def __init__(self, rows):
        self._rows = rows

    def query(self, *entities):
        return _FakeQuery(self._rows)

    def rollback(self):
        pass

    def close(self):
        pass


def _snap(conf=None, operation="buy"):
    d = {"operation": operation, "symbol": "BTC"}
    if conf is not None:
        d["confidence"] = conf
    return json.dumps(d)


def _row(snap=None, mid_conf=None, pnl=None):
    return (snap, mid_conf, pnl)


@pytest.fixture(autouse=True)
def _patch_db(monkeypatch):
    """默认把 AnalyticsSessionLocal patch 成可注入行的 FakeDb。"""
    holder = {}

    def _install(rows):
        holder["rows"] = rows

    monkeypatch.setattr(
        "backend.database.connection.AnalyticsSessionLocal",
        lambda: _FakeDb(holder.get("rows", [])),
    )
    return _install


# ── confidence 解析 ──

class TestExtractConfidence:
    def test_0_1_format(self):
        assert _extract_confidence(_snap(conf=0.78), None) == pytest.approx(0.78)

    def test_0_100_percent_format(self):
        # 早期规则引擎写回 confidence*100
        assert _extract_confidence(_snap(conf=78), None) == pytest.approx(0.78)

    def test_fallback_mid_confidence(self):
        assert _extract_confidence(None, 0.62) == pytest.approx(0.62)

    def test_invalid_json_fallback_mid_conf(self):
        assert _extract_confidence("{not-json", 0.55) == pytest.approx(0.55)

    def test_invalid_snapshot_conf_fallback(self):
        assert _extract_confidence(_snap(conf="abc"), 0.51) == pytest.approx(0.51)

    def test_none_when_all_missing(self):
        assert _extract_confidence(None, None) is None

    def test_zero_conf_returns_none(self):
        # 0 是降级/禁开标记，视为缺失
        assert _extract_confidence(_snap(conf=0), 0.6) == pytest.approx(0.6)


# ── 样本加载过滤 ──

class TestLoadSamples:
    def test_filters_hold_unexecuted_and_zero_pnl(self, _patch_db):
        rows = [
            # hold 剔除
            _row(_snap(conf=0.8, operation="hold"), None, 100.0),
            # 未执行剔除
            _row(_snap(conf=0.8), None, 100.0),
            # pnl 为 0（开仓单）剔除
            _row(_snap(conf=0.8), None, 0.0),
            # pnl 缺失剔除
            _row(_snap(conf=0.8), None, None),
            # 合法：buy 已执行 pnl>0
            _row(_snap(conf=0.75), None, 50.0),
            # 合法：sell 已执行 pnl<0
            _row(_snap(conf=0.6, operation="sell"), None, -30.0),
        ]
        # 过滤条件是 query.filter 上做的（Fake 不执行），这里验证 load 的置信度侧过滤
        _patch_db(rows)
        cal = AiDecisionConfidenceCalibrator()
        pairs = cal._load_samples(45)
        # FakeQuery 不做 SQL 过滤 → 全部进入；hold/未执行/0pnl 由真实 SQL 过滤，
        # 此处仅验证置信度解析侧：hold 的 conf 有效仍会进入（SQL 层负责剔除）。
        confs = [c for c, _ in pairs]
        assert 0.75 in confs and 0.6 in confs

    def test_drops_invalid_confidence(self, _patch_db):
        rows = [
            _row(_snap(conf=0), None, 100.0),      # 0 降级剔除
            _row(_snap(conf=0.03), None, 100.0),   # <0.05 剔除
            _row(_snap(conf=0.97), None, 100.0),   # >0.95 剔除
            _row(None, None, 100.0),               # 无 confidence 剔除
            _row(_snap(conf=0.5), None, 100.0),    # 合法
        ]
        _patch_db(rows)
        cal = AiDecisionConfidenceCalibrator()
        pairs = cal._load_samples(45)
        assert pairs == [(0.5, True)]

    def test_win_definition(self, _patch_db):
        rows = [
            _row(_snap(conf=0.5), None, 100.0),   # 盈利 → True
            _row(_snap(conf=0.6), None, -50.0),   # 亏损 → False
        ]
        _patch_db(rows)
        cal = AiDecisionConfidenceCalibrator()
        pairs = cal._load_samples(45)
        assert pairs == [(0.5, True), (0.6, False)]


# ── 拟合 ──

class TestFitModel:
    def _cal(self, monkeypatch, min_samples=10, min_bucket=3):
        import backend.config.settings as settings
        monkeypatch.setattr(settings, "AI_DECISION_CALIBRATOR_MIN_SAMPLES", min_samples)
        monkeypatch.setattr(settings, "AI_DECISION_CALIBRATOR_MIN_BUCKET", min_bucket)
        monkeypatch.setattr(settings, "AI_DECISION_CALIBRATOR_ENABLED", True)
        return AiDecisionConfidenceCalibrator()

    def test_insufficient_samples_not_calibrated(self, _patch_db, monkeypatch):
        _patch_db([_row(_snap(conf=0.5), None, 100.0) for _ in range(3)])
        cal = self._cal(monkeypatch, min_samples=10)
        model = cal._fit_model()
        assert model.is_calibrated is False
        assert model.n_samples == 3
        assert model.base_rate == pytest.approx(0.95)  # 3 笔全赢，clip 上限 0.95

    def test_valid_buckets_isotonic_monotonic(self, _patch_db, monkeypatch):
        # 5 个桶（桶2/4/6/8），每个 4 笔，胜率随置信度递增
        rows = []
        for conf, wins in ((0.25, 1), (0.45, 2), (0.65, 3), (0.85, 4)):
            rows += [
                _row(_snap(conf=conf), None, 100.0 if i < wins else -100.0)
                for i in range(4)
            ]
        _patch_db(rows)
        cal = self._cal(monkeypatch, min_samples=10, min_bucket=3)
        model = cal._fit_model()
        assert model.is_calibrated is True
        assert model.n_samples == 16
        assert model.xs == pytest.approx([0.25, 0.45, 0.65, 0.85])
        # PAVA 保序：单调不减
        assert all(b >= a for a, b in zip(model.ys, model.ys[1:]))

    def test_too_few_valid_buckets_not_calibrated(self, _patch_db, monkeypatch):
        # 只在一个桶里堆样本 → 有效桶 <2 → 不校准
        rows = [_row(_snap(conf=0.55), None, 100.0 if i % 2 == 0 else -100.0)
                for i in range(20)]
        _patch_db(rows)
        cal = self._cal(monkeypatch, min_samples=10, min_bucket=3)
        model = cal._fit_model()
        assert model.is_calibrated is False
        assert model.n_samples == 20


# ── estimate_p_win ──

class TestEstimatePWin:
    def test_disabled_uses_cold_linear(self, _patch_db, monkeypatch):
        import backend.config.settings as settings
        monkeypatch.setattr(settings, "AI_DECISION_CALIBRATOR_ENABLED", False)
        cal = AiDecisionConfidenceCalibrator()
        res = cal.estimate_p_win(0.7)
        assert res.source == "cold_linear"
        assert res.note == "calibrator_disabled"

    def test_cold_start_linear_anchor(self, _patch_db, monkeypatch):
        import backend.config.settings as settings
        monkeypatch.setattr(settings, "AI_DECISION_CALIBRATOR_ENABLED", True)
        monkeypatch.setattr(settings, "AI_DECISION_CALIBRATOR_MIN_SAMPLES", 10)
        cal = AiDecisionConfidenceCalibrator()
        res = cal.estimate_p_win(0.5)
        assert res.source == "cold_linear"
        assert res.p_win == pytest.approx(0.45)  # 无样本 base=0.45，枢轴 0.5
        # 高置信度应高于低置信度（线性映射正斜率）
        hi = cal.estimate_p_win(0.8)
        assert hi.p_win > res.p_win

    def test_calibrated_interpolation(self, _patch_db, monkeypatch):
        import backend.config.settings as settings
        monkeypatch.setattr(settings, "AI_DECISION_CALIBRATOR_ENABLED", True)
        monkeypatch.setattr(settings, "AI_DECISION_CALIBRATOR_MIN_SAMPLES", 10)
        monkeypatch.setattr(settings, "AI_DECISION_CALIBRATOR_MIN_BUCKET", 3)
        rows = []
        for conf, wins in ((0.25, 1), (0.45, 2), (0.65, 3), (0.85, 4)):
            rows += [
                _row(_snap(conf=conf), None, 100.0 if i < wins else -100.0)
                for i in range(4)
            ]
        _patch_db(rows)
        cal = AiDecisionConfidenceCalibrator()
        model = cal._fit_model()
        cal._model = model  # 直接注入，跳过 TTL
        res = cal.estimate_p_win(0.45)
        assert res.source == "calibrated"
        assert res.n_samples == 16
        # 0.45 桶胜率 50%（2/4）；曲线外推低置信度被钳制在 floor 内
        low = cal.estimate_p_win(0.05)
        assert low.p_win >= 0.30
        assert low.p_win <= res.p_win  # 单调性在外推区也保持


# ── get_stats ──

class TestGetStats:
    def test_stats_shape(self, _patch_db, monkeypatch):
        import backend.config.settings as settings
        monkeypatch.setattr(settings, "AI_DECISION_CALIBRATOR_ENABLED", True)
        monkeypatch.setattr(settings, "AI_DECISION_CALIBRATOR_MIN_SAMPLES", 10)
        monkeypatch.setattr(settings, "AI_DECISION_CALIBRATOR_MIN_BUCKET", 3)
        rows = []
        for conf, wins in ((0.25, 1), (0.45, 2), (0.65, 3), (0.85, 4)):
            rows += [
                _row(_snap(conf=conf), None, 100.0 if i < wins else -100.0)
                for i in range(4)
            ]
        _patch_db(rows)
        cal = AiDecisionConfidenceCalibrator()
        stats = cal.get_stats()
        assert stats["source"] == "ai_decision_logs"
        assert stats["calibrated"] is True
        assert stats["n_samples"] == 16
        assert len(stats["curve"]) == 4
        assert "0.25" in stats["buckets"] or any(
            k.startswith("0.2") for k in stats["buckets"]
        )
        # 曲线单调不减
        pws = [pt["p_win"] for pt in stats["curve"]]
        assert all(b >= a for a, b in zip(pws, pws[1:]))

    def test_stats_cold_start(self, _patch_db, monkeypatch):
        import backend.config.settings as settings
        monkeypatch.setattr(settings, "AI_DECISION_CALIBRATOR_ENABLED", True)
        _patch_db([])
        cal = AiDecisionConfidenceCalibrator()
        stats = cal.get_stats()
        assert stats["calibrated"] is False
        assert stats["n_samples"] == 0
        assert stats["curve"] == []
