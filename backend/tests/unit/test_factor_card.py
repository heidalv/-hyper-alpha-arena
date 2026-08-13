"""
test_factor_card — v6 阶段 2（S2-4）因子报告卡落库单元测试

覆盖:
1. build_factor_card 结构完整性（JSON 可序列化、全部字段）
2. IC/分位/admission 语义（构造有信号数据验证正值方向）
3. 数据质量入卡（NaN 注入 → 完整率下降/缺失比例上升）
4. parsimony 节点数
5. purge stage4_data_quality 数据质量门槛
6. run_purge_pipeline 集成（rejected_quality 计数）
7. factor_evolution_loop 评估后 card 落库（phase="card"）
"""
import json

import pytest
import numpy as np
import pandas as pd
from unittest.mock import patch

# ════════════════════════════════════════════════════════
#  Helpers
# ════════════════════════════════════════════════════════

def _make_klines(rows: int = 300, seed: int = 42, trend: float = 0.02) -> pd.DataFrame:
    """构造带趋势的 K线（趋势 → 动量类因子 IC 为正）。"""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=rows, freq="4h")
    # 正弦调制的正收益：保证动量因子与未来收益稳定正相关（无噪声漂移）
    phase = np.arange(rows) / 12.0
    rets = np.maximum(trend * 0.5 + 0.01 * np.sin(phase), 0.002)
    closes = 100.0 * np.cumprod(1.0 + rets)
    o = closes * (1 + rng.normal(0, 0.002, rows))
    return pd.DataFrame({
        "open": o,
        "high": np.maximum(o, closes) * (1 + np.abs(rng.normal(0, 0.001, rows))),
        "low": np.minimum(o, closes) * (1 - np.abs(rng.normal(0, 0.001, rows))),
        "close": closes,
        "volume": rng.uniform(1e5, 1e6, rows),
    }, index=idx)


def _make_momentum_expr():
    """mom5 表达式（标准化动量）。"""
    from backend.services.factor_engine.expr.parser import parse
    ast = {"op": "mean", "args": [{"f": "returns"}, {"c": 5}]}
    return parse(ast)


def _make_candidate(factor_id: str, expr_ast: dict, series_fn):
    from backend.services.factor_engine.purge_pipeline import CandidateFactor
    return CandidateFactor(
        factor_id=factor_id, source_name="test", expr_ast=expr_ast,
    ), series_fn


# ════════════════════════════════════════════════════════
#  1. build_factor_card 结构完整性
# ════════════════════════════════════════════════════════

class TestBuildFactorCard:
    def test_full_structure_json_safe(self):
        """报告卡全字段存在且 JSON 可序列化（无 numpy 类型泄漏）。"""
        from backend.services.factor_engine.factor_card import build_factor_card
        expr = _make_momentum_expr()
        dfs = {"BTC": _make_klines(seed=1), "ETH": _make_klines(seed=2)}
        card = build_factor_card(factor_id=expr.expr_id, expr=expr, dfs=dfs,
                                 period="4h", source="test")
        # 顶层结构
        for k in ("card_version", "basic", "ic", "quantile", "decay",
                  "turnover", "parsimony", "data_quality", "admission"):
            assert k in card, f"报告卡缺字段 {k}"
        assert card["basic"]["factor_id"] == expr.expr_id
        assert card["basic"]["period"] == "4h"
        # JSON 序列化必须成功
        json.dumps(card)
        # 聚合指标有值（趋势数据 → 动量 IC>0）
        assert card["ic"]["mean"] is not None
        assert card["ic"]["icir"] is not None
        assert card["data_quality"]["mean_completeness"] is not None

    def test_ic_semantics_positive_for_momentum(self):
        """趋势数据下动量因子 IC 均值应为正。"""
        from backend.services.factor_engine.factor_card import build_factor_card
        expr = _make_momentum_expr()
        dfs = {"BTC": _make_klines(seed=7, trend=0.02)}
        card = build_factor_card(factor_id=expr.expr_id, expr=expr, dfs=dfs)
        assert card["ic"]["mean"] > 0

    def test_admission_gate_present(self):
        """admission_gate 判定输出 passed/reasons/details。"""
        from backend.services.factor_engine.factor_card import build_factor_card
        expr = _make_momentum_expr()
        dfs = {"BTC": _make_klines(seed=7, trend=0.02)}
        card = build_factor_card(factor_id=expr.expr_id, expr=expr, dfs=dfs)
        assert isinstance(card["admission"]["passed"], bool)
        assert isinstance(card["admission"]["reasons"], list)
        assert isinstance(card["admission"]["details"], dict)

    def test_parsimony_node_count(self):
        """AST 节点数统计（mean(returns,5) = 3 节点）。"""
        from backend.services.factor_engine.factor_card import build_factor_card, _node_count
        expr = _make_momentum_expr()
        dfs = {"BTC": _make_klines(seed=3)}
        card = build_factor_card(factor_id=expr.expr_id, expr=expr, dfs=dfs)
        assert card["parsimony"]["node_count"] == _node_count(expr.ast) == 3
        assert card["parsimony"]["penalty"] == pytest.approx(3e-3, abs=1e-9)

    def test_quantile_backtest_present(self):
        """分层回测输出（多头/多空/单调性）。"""
        from backend.services.factor_engine.factor_card import build_factor_card
        expr = _make_momentum_expr()
        dfs = {"BTC": _make_klines(seed=7, trend=0.02)}
        card = build_factor_card(factor_id=expr.expr_id, expr=expr, dfs=dfs)
        q = card["quantile"]
        assert q.get("n_quantiles") == 5
        assert len(q.get("sharpe", [])) == 5
        assert "long_short_sharpe" in q
        assert "monotonic_r" in q


# ════════════════════════════════════════════════════════
#  2. 数据质量入卡
# ════════════════════════════════════════════════════════

class TestDataQualityInCard:
    def test_missing_values_detected(self):
        """NaN 注入后缺失比例上升、完整率下降。"""
        from backend.services.factor_engine.factor_card import build_factor_card
        expr = _make_momentum_expr()
        clean_df = _make_klines(seed=5)
        dirty_df = _make_klines(seed=5)
        # 后 50% 的 close 置 NaN → 因子值缺失
        dirty_df.loc[dirty_df.index[len(dirty_df) // 2:], "close"] = np.nan
        card_clean = build_factor_card(factor_id=expr.expr_id, expr=expr,
                                       dfs={"X": clean_df})
        card_dirty = build_factor_card(factor_id=expr.expr_id, expr=expr,
                                       dfs={"X": dirty_df})
        assert card_dirty["data_quality"]["mean_missing_pct"] > \
            card_clean["data_quality"]["mean_missing_pct"]
        assert card_dirty["data_quality"]["mean_completeness"] < \
            card_clean["data_quality"]["mean_completeness"]

    def test_insufficient_samples_skipped(self):
        """样本不足的品种在卡内标记 skipped 且不炸。"""
        from backend.services.factor_engine.factor_card import build_factor_card
        expr = _make_momentum_expr()
        tiny = _make_klines(rows=20, seed=9)
        card = build_factor_card(factor_id=expr.expr_id, expr=expr, dfs={"X": tiny})
        assert card["ic"]["per_symbol"]["X"]["skipped"] == "样本不足"


# ════════════════════════════════════════════════════════
#  3. purge 数据质量门槛
# ════════════════════════════════════════════════════════

class TestPurgeDataQuality:
    def _make_purge_candidates(self):
        from backend.services.factor_engine.purge_pipeline import CandidateFactor
        good_ast = {"op": "mean", "args": [{"f": "returns"}, {"c": 5}]}
        bad_ast = {"op": "mean", "args": [{"f": "returns"}, {"c": 7}]}  # 与 good 不同，避免去重误伤
        c_good = CandidateFactor(factor_id="good", source_name="t", expr_ast=good_ast)
        c_bad = CandidateFactor(factor_id="bad", source_name="t", expr_ast=bad_ast)

        def series_fn(c):
            n = 100
            s = pd.Series(np.random.default_rng(1).normal(size=n))
            if c.factor_id == "bad":
                s.iloc[:70] = np.nan  # 完整率 30% < 80%
            return s
        return [c_good, c_bad], series_fn

    def test_stage4_rejects_low_quality(self):
        """stage4_data_quality 淘汰完整率不足的候选。"""
        from backend.services.factor_engine.purge_pipeline import (
            PurgeConfig, stage4_data_quality,
        )
        cands, series_fn = self._make_purge_candidates()
        surv, rej = stage4_data_quality(cands, PurgeConfig(min_data_quality=0.8),
                                        factor_series_fn=series_fn)
        assert [c.factor_id for c in surv] == ["good"]
        assert rej[0].factor_id == "bad"
        assert "数据质量不足" in rej[0].reject_reason
        assert rej[0].status == "REJECTED"

    def test_stage4_sets_data_quality_field(self):
        """通过检查时候选的 data_quality 字段被回填。"""
        from backend.services.factor_engine.purge_pipeline import (
            PurgeConfig, stage4_data_quality,
        )
        cands, series_fn = self._make_purge_candidates()
        surv, _ = stage4_data_quality(cands, PurgeConfig(min_data_quality=0.8),
                                      factor_series_fn=series_fn)
        assert surv[0].data_quality == pytest.approx(1.0, abs=1e-6)

    def test_run_purge_pipeline_counts_quality_rejects(self):
        """run_purge_pipeline 集成：报告 rejected_quality 计数 + summary 含质量拒。"""
        from backend.services.factor_engine.purge_pipeline import (
            PurgeConfig, run_purge_pipeline,
        )
        cands, series_fn = self._make_purge_candidates()
        # 需要能通过 stage1（ast 可审计）+ stage3（用 return_series 评估）
        df = pd.DataFrame({"f": np.random.default_rng(2).normal(size=100),
                           "r": np.random.default_rng(3).normal(size=100)})
        return_series = df["r"]
        # 用阈值放宽的 lifecycle 让 stage3 不拦截，聚焦数据质量维度
        from dataclasses import replace as _dc_replace
        from backend.services.factor_engine.lifecycle import LifecycleThresholds
        loose = _dc_replace(
            LifecycleThresholds(),
            min_icir=-999.0, max_monotonicity_p=1.0,
            max_turnover=10.0, min_halflife_bars=0,
        )

        def fsf(c):
            return series_fn(c)  # bad 含 NaN → 数据质量维度生效

        # 本测只验证质量拒计数；放行 Stage7，避免内置 DSR 因噪声 IC 误杀 good
        def pass_dsr(surv):
            return surv, []

        final, report = run_purge_pipeline(
            cands, factor_series_fn=fsf, return_series=return_series,
            config=PurgeConfig(min_data_quality=0.8), thresholds=loose,
            dsr_pbo_gate=pass_dsr,
        )
        assert report.rejected_quality == 1
        assert "质量拒 1" in report.summary()
        assert [c.factor_id for c in final] == ["good"]


# ════════════════════════════════════════════════════════
#  4. factor_evolution_loop 落库
# ════════════════════════════════════════════════════════

class TestEvolutionLoopCardPersistence:
    def test_evaluate_candidates_logs_card(self):
        """评估后为每个有效候选落 phase='card' 日志（metrics 含 card）。"""
        from backend.services.factor_engine.expr.parser import parse
        from backend.services.evolution import factor_evolution_loop as fel
        ast = {"op": "mean", "args": [{"f": "returns"}, {"c": 5}]}
        expr = parse(ast)
        dfs = {"BTC": _make_klines(seed=11)}

        logged = []

        def fake_log(factor_id, phase, **kwargs):
            logged.append((factor_id, phase, kwargs))

        with patch.object(fel, "_log_evolution", side_effect=fake_log):
            results = fel._evaluate_candidates([(expr, "mom5")], dfs, "4h")

        assert expr.expr_id in results
        card_entries = [l for l in logged if l[1] == "card"]
        assert len(card_entries) == 1
        fid, phase, kwargs = card_entries[0]
        assert phase == "card"
        assert kwargs["action"] == "card_generated"
        metrics = kwargs["metrics"]
        assert metrics["card"]["basic"]["factor_id"] == fid
        assert metrics["card"]["ic"]["mean"] is not None
        assert "net_ic" in metrics

    def test_card_generation_failure_does_not_block(self):
        """报告卡生成失败时容错，评估主流程不受影响。"""
        from backend.services.factor_engine.expr.parser import parse
        from backend.services.evolution import factor_evolution_loop as fel
        ast = {"op": "mean", "args": [{"f": "returns"}, {"c": 5}]}
        expr = parse(ast)
        dfs = {"BTC": _make_klines(seed=12)}

        with patch.object(fel, "_log_evolution"):
            with patch("backend.services.factor_engine.factor_card.build_factor_card",
                       side_effect=RuntimeError("boom")):
                results = fel._evaluate_candidates([(expr, "mom5")], dfs, "4h")
        assert expr.expr_id in results
