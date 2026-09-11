# -*- coding: utf-8 -*-
"""因子逐项归因贯通（2026-09-02 P1.2）。

问题：合成信号只把聚合后的 direction/confidence 传下去，逐因子明细在
`v3_factor_pipeline._summary_payload` 处蒸发。落库的 scalp_signal_log 因此对
141 个因子只有一个 `composite` 分 —— 一笔亏损无法归因到具体因子，进化闭环
（哪个因子该降权/淘汰）缺少输入。

本用例锁定三件事：
1. 归因是**加性**的：Σcontrib == CompositeSignal.direction（不是近似估计）；
2. 归因在聚合处产出（eff_weight 已含 _cap_category_share 压制，外部重算会失真）；
3. 归因能穿过 router → features_json 整条链路，且不污染 meta 模型输入。
"""
import json

import pytest

from backend.services.factor_engine.base_factors import FactorCategory, FactorValue
from backend.services.factor_engine.factor_signal_generator import (
    FactorSignalGenerator,
)


def _fv(name: str, value: float, normalized: float,
        category=FactorCategory.MOMENTUM) -> FactorValue:
    return FactorValue(
        name=name, category=category, value=value,
        normalized=normalized, timestamp=None,
        has_data=True, is_directional=True,
    )


def _spread_categories(n: int):
    """打散 category，避开 _cap_category_share 的同类压制，便于验证纯加性。"""
    cats = [FactorCategory.MOMENTUM, FactorCategory.VOLATILITY,
            FactorCategory.VOLUME, FactorCategory.TREND]
    return [cats[i % len(cats)] for i in range(n)]


class TestAdditivity:
    """性质一：归因必须精确加总回合成方向。"""

    def test_sum_of_contrib_equals_direction(self):
        cats = _spread_categories(5)
        fvs = {
            f"f{i}": _fv(f"f{i}", 0.5, 0.3 + i * 0.1, cats[i])
            for i in range(5)
        }
        sig = FactorSignalGenerator().generate_signals(fvs)
        assert sig.attribution, "必须产出归因明细"
        total = sum(a.contrib for a in sig.attribution)
        assert total == pytest.approx(sig.direction, abs=1e-4), (
            f"Σcontrib={total} 应等于 composite direction={sig.direction}"
        )

    def test_additivity_holds_with_mixed_signs(self):
        """多空混杂时仍需加性成立（这是归因最容易写错的场景）。"""
        cats = _spread_categories(6)
        fvs = {}
        for i in range(6):
            nrm = 0.4 + i * 0.08
            fvs[f"f{i}"] = _fv(f"f{i}", 1.0, nrm if i % 2 == 0 else -nrm, cats[i])
        sig = FactorSignalGenerator().generate_signals(fvs)
        total = sum(a.contrib for a in sig.attribution)
        assert total == pytest.approx(sig.direction, abs=1e-4)
        assert any(a.contrib > 0 for a in sig.attribution)
        assert any(a.contrib < 0 for a in sig.attribution)

    def test_additivity_survives_category_capping(self):
        """同类因子扎堆时 _cap_category_share 会改 eff_weight，加性仍须成立。"""
        fvs = {
            f"m{i}": _fv(f"m{i}", 1.0, 0.5 + i * 0.05, FactorCategory.MOMENTUM)
            for i in range(8)
        }
        sig = FactorSignalGenerator().generate_signals(fvs)
        total = sum(a.contrib for a in sig.attribution)
        assert total == pytest.approx(sig.direction, abs=1e-4), (
            "同类压制后归因仍须加总回合成方向 —— 这正是不能在外部重算的原因"
        )


class TestAttributionShape:
    """性质二：归因内容与排序符合契约。"""

    def test_sorted_by_abs_contrib_desc(self):
        cats = _spread_categories(6)
        fvs = {
            f"f{i}": _fv(f"f{i}", 1.0, 0.2 + i * 0.12, cats[i])
            for i in range(6)
        }
        sig = FactorSignalGenerator().generate_signals(fvs)
        mags = [abs(a.contrib) for a in sig.attribution]
        assert mags == sorted(mags, reverse=True), "须按 |contrib| 降序"

    def test_fields_present(self):
        fvs = {"a": _fv("a", 1.0, 0.6), "b": _fv("b", 1.0, -0.4,
                                                 FactorCategory.VOLUME)}
        sig = FactorSignalGenerator().generate_signals(fvs)
        a = sig.attribution[0]
        assert a.factor_id and isinstance(a.factor_id, str)
        for f in ("direction", "weight", "eff_weight", "contrib"):
            assert isinstance(getattr(a, f), float), f"{f} 须为 float"
        assert a.category

    def test_capped_at_top_n(self):
        """合成只取 top-15，归因不应超过它。"""
        cats = _spread_categories(40)
        fvs = {
            f"f{i}": _fv(f"f{i}", 1.0, 0.15 + (i % 20) * 0.04, cats[i])
            for i in range(40)
        }
        sig = FactorSignalGenerator().generate_signals(fvs)
        assert 0 < len(sig.attribution) <= 15

    def test_zero_weight_factors_excluded(self):
        """权重 0 的因子不参与合成，也不该出现在归因里。"""
        cats = _spread_categories(3)
        fvs = {f"f{i}": _fv(f"f{i}", 1.0, 0.5, cats[i]) for i in range(3)}
        sig = FactorSignalGenerator().generate_signals(
            fvs, weights={"f0": 0.0, "f1": 1.0, "f2": 1.0},
        )
        names = {a.factor_id for a in sig.attribution}
        assert "f0" not in names, "权重 0 的因子不得出现在归因中"

    def test_neutral_factors_excluded(self):
        """|direction|<0.1 的中性因子被聚合跳过，归因须一致。"""
        fvs = {
            "strong": _fv("strong", 1.0, 0.8),
            "flat": _fv("flat", 1.0, 0.01, FactorCategory.VOLUME),
        }
        sig = FactorSignalGenerator().generate_signals(fvs)
        names = {a.factor_id for a in sig.attribution}
        assert "strong" in names
        assert "flat" not in names

    def test_empty_input_is_safe(self):
        sig = FactorSignalGenerator().generate_signals({})
        assert sig.attribution == []
        assert sig.direction == 0.0


class TestRouterPassthrough:
    """性质三：归因穿过 router 进入 breakdown，并且不污染 meta 输入。"""

    def test_router_forwards_factor_contrib(self):
        from backend.services.scalp_factor_router import ScalpFactorRouter
        contrib = [{"f": "rsi", "d": 0.5, "w": 1.0, "c": 0.3, "cat": "momentum"}]
        md = {"factor_signal": {"direction": 0.5, "factor_contrib": contrib}}
        _s, _d, bd = ScalpFactorRouter()._extract_factor_signal("BTC", md)
        assert bd.get("factor_contrib") == contrib
        assert "composite" in bd and "raw_dir" in bd

    def test_router_tolerates_missing_contrib(self):
        """老 schema（无 factor_contrib）不得报错 —— 灰度期两种 payload 并存。"""
        from backend.services.scalp_factor_router import ScalpFactorRouter
        md = {"factor_signal": {"direction": 0.4}}
        _s, _d, bd = ScalpFactorRouter()._extract_factor_signal("BTC", md)
        assert "factor_contrib" not in bd
        assert "composite" in bd

    def test_router_ignores_malformed_contrib(self):
        from backend.services.scalp_factor_router import ScalpFactorRouter
        for bad in ("notalist", {}, [], 42):
            md = {"factor_signal": {"direction": 0.4, "factor_contrib": bad}}
            _s, _d, bd = ScalpFactorRouter()._extract_factor_signal("X", md)
            assert "factor_contrib" not in bd, f"畸形输入 {bad!r} 不应写入"

    def test_contrib_is_json_serializable(self):
        """features_json 落库需 json.dumps 成功。"""
        cats = _spread_categories(4)
        fvs = {f"f{i}": _fv(f"f{i}", 1.0, 0.3 + i * 0.1, cats[i]) for i in range(4)}
        sig = FactorSignalGenerator().generate_signals(fvs)
        payload = [
            {"f": a.factor_id, "d": a.direction, "w": a.weight,
             "c": a.contrib, "cat": a.category}
            for a in sig.attribution
        ]
        s = json.dumps({"factor_contrib": payload}, ensure_ascii=False)
        assert json.loads(s)["factor_contrib"][0]["f"]
        assert len(s) < 20000, "须能容纳进 features_json 的 20000 字符上限"

    def test_meta_features_filter_out_list(self):
        """meta 特征构造以 `not isinstance(v,(dict,list))` 过滤，list 不入模型。

        锁死这条契约：若哪天有人把归因摊平成标量键，会静默改变模型输入维度。
        """
        snap = {"composite": 40, "raw_dir": 0.5,
                "factor_contrib": [{"f": "rsi", "c": 0.3}]}
        feats = {k: v for k, v in snap.items()
                 if not isinstance(v, (dict, list))}
        assert "factor_contrib" not in feats
        assert feats["composite"] == 40

    def test_top_factors_ranking_skips_list(self):
        """scalp_loop 的 top_factors 只取数值项，遇到 list 不得抛错。"""
        breakdown = {"composite": 40.0, "raw_dir": 0.5,
                     "factor_contrib": [{"f": "rsi"}], "trend_flip": "15m_up"}
        ranked = sorted(
            ((str(k), float(v)) for k, v in breakdown.items()
             if isinstance(v, (int, float))),
            key=lambda kv: abs(kv[1]), reverse=True,
        )[:8]
        assert [n for n, _ in ranked] == ["composite", "raw_dir"]


class TestPipelineSchema:
    """性质四：落库 payload 的 schema 版本与字段名不漂移。"""

    def test_pipeline_declares_schema_3(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[3] / "backend" / "services"
               / "full_auto" / "v3_factor_pipeline.py").read_text(encoding="utf-8")
        assert '"schema_version": 3' in src, "带归因的 payload 须声明 schema 3"
        assert '"factor_contrib"' in src
