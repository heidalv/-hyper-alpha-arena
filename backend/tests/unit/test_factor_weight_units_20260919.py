# -*- coding: utf-8 -*-
"""轮105 因子票权口径回归测试（2026-09-19）。

## 背景（实测）

体检发现中线因子路由的票权被 **5 个进化仓 AST 因子**主导，而它们是同一个信号的
近似重复 + 量纲错位：

    evo_2e6e556cd8f21fae = -1 × decay_linear(returns, 20)   icir=1.307
    evo_3d51976a92a07bea = -1 × mean(returns, 20)           icir=1.072
    evo_5a0c7a226b5446a4 = -1 × mean(returns, 10)           icir=1.039
    evo_d6f82d364676127e = -1 × mean(returns, 50)           icir=0.934
    evo_180ee6eedd1fe9cc = -1 × mean(returns, 5)            icir=0.841

① **量纲**：`_tradable_ast_bridge` 把 ICIR 塞进 `scores["ic_mean"]`（当"权重幅度代理"），
   而路由 `w = abs(ic) × runtime_weight` 又乘一次 ⇒ evo 票权 0.0728 vs macd 0.0097（7.5×）。
② **重复**：5 条里 4 条只差窗口 ⇒ 一个信号计 5 票。
③ **反不了号**：`expected_sign` 恒 +1，而轮58/59 的"趋势 regime 反号"只作用于 `orient < 0`，
   于是趋势行情里这批均值反转因子以最大票权持续逆势投票（实测 8 币 score ≈ −0.54~−0.81 全 sell）。

本测试锁死四项修复，防止回退。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from backend.services.factor_engine import midlong_factor_route as R

# ── 事故现场常量（实测的 5 条 AST 表达式）──
AST_MEAN5 = {"op": "mul", "args": [{"c": -1}, {"op": "mean", "args": [{"f": "returns"}, {"c": 5}]}]}
AST_MEAN50 = {"op": "mul", "args": [{"c": -1}, {"op": "mean", "args": [{"f": "returns"}, {"c": 50}]}]}
AST_DECAY20 = {"op": "mul", "args": [{"c": -1},
                                     {"op": "decay_linear", "args": [{"f": "returns"}, {"c": 20.0}]}]}
AST_MOMENTUM = {"op": "mean", "args": [{"f": "returns"}, {"c": 20}]}          # 非反转
AST_INVERTED_NO_RET = {"op": "mul", "args": [{"c": -1}, {"f": "volume"}]}     # 反转但不含 returns


def _ast_rec(fid, expr, icir=1.0):
    return {
        "factor_id": fid,
        "formula": None,
        "extra": {"horizon": "midlong", "timeframe": "4h", "kind": "ast", "expr_ast": expr},
        "scores": {"ic_mean": icir, "icir": icir, "expected_sign": 1},
    }


def _formula_rec(fid, ic=0.11):
    return {"factor_id": fid, "formula": "close-mean(close,10)",
            "extra": {"horizon": "midlong", "timeframe": "4h", "kind": "registry"},
            "scores": {"ic_mean": ic, "icir": -0.5, "expected_sign": -1}}


# ══════════════════════════════════════════════════════════════════════
# ② 同族去重：结构签名把"只差窗口"归为一族
# ══════════════════════════════════════════════════════════════════════

def test_structure_signature_ignores_numeric_constants():
    assert R._structure_signature(AST_MEAN5) == R._structure_signature(AST_MEAN50)


def test_structure_signature_distinguishes_ops():
    assert R._structure_signature(AST_MEAN5) != R._structure_signature(AST_DECAY20)


def test_bridge_dedups_to_one_per_family(monkeypatch):
    """实测的 5 条 → 2 条（mean 族与 decay_linear 族各留 |ICIR| 最大者）。"""
    rows = [
        {"factor_id": "2e6e556cd8f21fae", "expr_ast": AST_DECAY20, "icir": 1.307, "source": "gp"},
        {"factor_id": "3d51976a92a07bea", "expr_ast": AST_MEAN50, "icir": 1.072, "source": "gp"},
        {"factor_id": "5a0c7a226b5446a4", "expr_ast": AST_MEAN5, "icir": 1.039, "source": "gp"},
        {"factor_id": "d6f82d364676127e", "expr_ast": AST_MEAN5, "icir": 0.934, "source": "gp"},
        {"factor_id": "180ee6eedd1fe9cc", "expr_ast": AST_MEAN50, "icir": 0.841, "source": "gp"},
    ]
    import backend.services.factor_engine.active_set_policy as pol
    monkeypatch.setattr(pol, "load_factor_active_rows", lambda *a, **k: rows, raising=False)

    from backend.services.factor_engine.midlong_active_factor_set import MidLongActiveFactorSet as M
    out = M._tradable_ast_bridge()
    assert len(out) == 2, [r["factor_id"] for r in out]
    kept = {r["factor_id"]: r["scores"]["icir"] for r in out}
    # 同族留下的是 |ICIR| 最大的那条
    assert kept.get("evo_2e6e556cd8f21fae") == pytest.approx(1.307)
    assert kept.get("evo_3d51976a92a07bea") == pytest.approx(1.072)


def test_bridge_dedup_can_be_disabled(monkeypatch):
    rows = [
        {"factor_id": "a1", "expr_ast": AST_MEAN5, "icir": 1.0, "source": "gp"},
        {"factor_id": "a2", "expr_ast": AST_MEAN50, "icir": 0.9, "source": "gp"},
    ]
    import backend.services.factor_engine.active_set_policy as pol
    monkeypatch.setattr(pol, "load_factor_active_rows", lambda *a, **k: rows, raising=False)
    monkeypatch.setattr("backend.config.settings.MIDLONG_AST_BRIDGE_DEDUP", False, raising=False)

    from backend.services.factor_engine.midlong_active_factor_set import MidLongActiveFactorSet as M
    out = M._tradable_ast_bridge()
    assert len(out) == 2, "关掉去重应恢复旧行为（两条都桥接）"


# ══════════════════════════════════════════════════════════════════════
# ① 量纲：AST 的 ic_mean(实为 ICIR) 参与票权前必须封顶
# ══════════════════════════════════════════════════════════════════════

def test_ast_ic_cap_default_and_env(monkeypatch):
    monkeypatch.delenv("FACTOR_ROUTE_AST_IC_CAP", raising=False)
    assert R._ast_ic_cap() == pytest.approx(0.15)
    monkeypatch.setenv("FACTOR_ROUTE_AST_IC_CAP", "0.05")
    assert R._ast_ic_cap() == pytest.approx(0.05)
    monkeypatch.setenv("FACTOR_ROUTE_AST_IC_CAP", "0")
    assert R._ast_ic_cap() == 0.0, "0 = 关闭封顶（回滚语义）"


def test_ast_weight_is_comparable_to_formula_weight_after_cap():
    """事故算术：ICIR 1.307 × w文件 0.0557 = 0.0728（macd 的 7.5 倍）；
    封顶后 0.15 × 0.0557 = 0.00836 < macd 的 0.01107。"""
    cap = 0.15
    rw_ast, rw_macd = 0.0557, 0.0852
    assert 1.307 * rw_ast > 0.01107 * 6          # 修复前：至少 6 倍
    assert cap * rw_ast < 0.1143 * rw_macd       # 修复后：低于 macd 的票权


def test_contrarian_ast_detection():
    assert R._is_contrarian_ast(_ast_rec("x", AST_MEAN5)) is True
    assert R._is_contrarian_ast(_ast_rec("x", AST_DECAY20)) is True
    assert R._is_contrarian_ast(_ast_rec("x", AST_MOMENTUM)) is False, "非反转结构"
    assert R._is_contrarian_ast(_ast_rec("x", AST_INVERTED_NO_RET)) is False, "不含 returns"
    assert R._is_contrarian_ast(_formula_rec("macd@4h")) is False, "公式因子不适用"


def test_ast_invert_toggle(monkeypatch):
    monkeypatch.delenv("MIDLONG_ROUTE_TREND_INVERT_AST", raising=False)
    assert R._ast_invert_enabled() is True
    monkeypatch.setenv("MIDLONG_ROUTE_TREND_INVERT_AST", "false")
    assert R._ast_invert_enabled() is False


# ══════════════════════════════════════════════════════════════════════
# ④ 不变量：票权集中度不得超过公式因子中位数的 3 倍
# ══════════════════════════════════════════════════════════════════════

def test_concentration_flags_the_incident_shape():
    """实测现场：修复前 AST 票权 0.0728，而 12 个公式因子票权中位 ≈ 0.0107 → 6.8×。"""
    votes = {
        "macd@4h": {"w": 0.01107}, "momentum@4h": {"w": 0.00720},
        "hv@4h": {"w": 0.00047}, "obv@4h": {"w": 0.01039}, "obv@1d": {"w": 0.01074},
        "vwap@4h": {"w": 0.04580}, "sma_cross@4h": {"w": 0.01059},
        "supertrend@4h": {"w": 0.01121}, "evo_x": {"w": 0.0728},
    }
    rep = R.concentration_report(votes)
    assert rep["offenders"] == ["evo_x"], rep
    assert rep["ratio"] > 6.0
    assert rep["ratio"] > R._CONC_MAX_RATIO


def test_concentration_clean_after_cap():
    votes = {"macd@4h": {"w": 0.01107}, "vwap@4h": {"w": 0.04580},
             "obv@4h": {"w": 0.01039},
             "evo_x": {"w": 0.00949}, "evo_y": {"w": 0.00779}}
    rep = R.concentration_report(votes)
    assert rep["offenders"] == [], rep
    assert rep["ratio"] < 1.0


def test_concentration_ignores_skipped_votes():
    """z=None/skip 的票没有 w → 不参与中位数，也不得误报。"""
    votes = {"macd@4h": {"w": 0.01}, "nohist@4h": {"z": None, "vote": None, "skip": "no_history"}}
    rep = R.concentration_report(votes)
    assert rep["offenders"] == [] and rep["formula_median_w"] is None


def test_ast_weight_is_clamped_by_construction(monkeypatch):
    """[轮106] 不变量必须**按构造成立**，而不是只告警。

    实测：轮105 只加告警，第二天权重文件刷新后 AST 票权就变成公式中位的 3.9×。
    所以路由在投票前预扫票权，把 AST 钳到「公式中位 × _CONC_MAX_RATIO」以内。
    """
    import numpy as np

    import backend.config.settings as _s
    import backend.services.factor_engine.midlong_active_factor_set as mafs

    class _FakeSet:
        def get_active_factors(self):
            return [
                {"factor_id": "f1@4h", "scores": {"ic_mean": 0.10, "expected_sign": -1},
                 "runtime_weight": 0.01, "extra": {"timeframe": "4h", "kind": "registry"}},
                {"factor_id": "f2@4h", "scores": {"ic_mean": 0.10, "expected_sign": 1},
                 "runtime_weight": 0.01, "extra": {"timeframe": "4h", "kind": "registry"}},
                # AST 的 ic_mean 实为 ICIR(1.0)：封顶后票权 0.15×0.2 = 0.03，
                # 仍是公式因子（0.10×0.01 = 0.001）的 30 倍 → 必须被钳到 0.003
                {"factor_id": "evo_x", "scores": {"ic_mean": 1.0, "expected_sign": 1},
                 "runtime_weight": 0.2,
                 "extra": {"kind": "ast", "timeframe": "4h", "expr_ast": AST_MEAN5}},
            ]

    monkeypatch.setattr(mafs, "midlong_active_factor_set", _FakeSet())
    monkeypatch.setattr(_s, "FACTOR_ROUTE_MIN_ACTIVE_FACTORS", 1, raising=False)
    monkeypatch.setattr(R, "_resolve_regime", lambda s, ms=None: "ranging")
    monkeypatch.setattr(R, "_factor_history", lambda rec, sym: np.linspace(0.0, 3.0, 200))
    monkeypatch.setattr(R, "_dynamic_sl_tp", lambda s: (0.05, 0.10, "t"))

    out = R.factor_route_decide("BTC", {"BTC": {"current_price": 100.0}})
    assert out.get("ast_weight_clamped"), out
    _ast_w = out["votes"]["evo_x"]["w"]
    _f_w = [v["w"] for k, v in out["votes"].items() if not str(k).startswith("evo_")]
    assert _ast_w <= R._CONC_MAX_RATIO * float(np.median(_f_w)) + 1e-9, (_ast_w, _f_w)
    assert out["votes"]["evo_x"].get("w_clamped_from") == pytest.approx(0.03), out["votes"]["evo_x"]
    assert R.concentration_report(out["votes"])["offenders"] == []


# ══════════════════════════════════════════════════════════════════════
# 源码级棘轮：三处修复都还在
# ══════════════════════════════════════════════════════════════════════

def _route_src():
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))),
        "backend/services/factor_engine/midlong_factor_route.py")
    return open(p, encoding="utf-8").read()


def test_route_applies_cap_and_reports_weight():
    src = _route_src()
    assert "_amp_capped, _amp = _cap, _cap" in src, "幅度封顶被摘掉"
    assert '"w": round(w, 6)' in src, "投票记录必须携带实际票权（否则失衡不可见）"
    assert '"ic_capped"' in src


def test_route_widens_invert_rule_to_ast():
    src = _route_src()
    assert "or _inv_ast" in src, "反转类 AST 因子未被纳入趋势反号"
    assert "_is_contrarian_ast(rec)" in src


def test_route_emits_concentration_in_output():
    src = _route_src()
    assert 'out["weight_concentration"] = _conc' in src
    assert "_warn_concentration_once(sym, _conc)" in src
