# -*- coding: utf-8 -*-
"""[F305 2026-09-16] LLM 假设闭环契约测试：假设 → 谓词 → walk-forward → 保留/丢弃。

本模块的价值全在"**能不能挡住**"，所以测试重点是拒绝路径，不是通过路径：
  1. 沙箱：合法谓词必须编译；已知利用必须全部被拒（含平台沙箱漏掉的那一类）；
  2. 改写：`and/or/not` 必须能用在 numpy 数组上（否则 LLM 的书写习惯会让整条链失效）；
  3. 因果性：特征不得引用未来数据；
  4. 门禁：单折为正、方向相反、事件不足、样本外为负 —— 都必须丢弃；
  5. 回滚开关：环境变量关掉即整体停用，且**不产生假候选**；
  6. LLM 不可用时如实返回，绝不造候选。
"""
from __future__ import annotations

import os

import numpy as np
import pytest

from backend.services import llm_hypothesis_lane as L


# ── 1. 沙箱 ────────────────────────────────────────────────────────────
LEGIT = [
    "ofi > 0.3",
    "ofi_ewma > 0.2 and trade_intensity > 1.5",
    "ret_1 < -5 and vol_bp > 2",
    "max(ofi, ofi_ewma) > 0.25",
    "abs(ret_5) > 10 and bars_since_shock < 3",
    "not (ofi > 0.3)",
    "where(ofi > 0.4, 1, where(ofi < -0.4, -1, 0))",
    "round(ofi, 2) > 0.3",
    "vol_bp > 1.0 * 2",
]

EXPLOITS = [
    '__import__("os").system("calc")',
    'os.system("calc")',
    'open("C:/Windows/System32/drivers/etc/hosts")',
    'np.load("x.npy")',
    'eval("1+1")',
    'exec("x=1")',
    'ofi.__class__',
    'ofi.__class__.__mro__',
    'getattr(ofi, "x")',
    "(lambda: 1)()",
    'pd.io.common.os.system("calc")',
    "ofi > 0; import os",
    "builtins",
    'subprocess.run(["calc"])',
    "ofi.__globals__",
    "__builtins__",
    "globals()",
    "sys.modules",
    "ofi > 0\nimport os",
    'compile("1", "<s>", "eval")',
    'pickle.loads(b"")',
    'ctypes.CDLL("x")',
    "_secret > 0",
    '__builtins__["__import__"]("os")',
]


@pytest.mark.parametrize("expr", LEGIT)
def test_legit_predicates_compile(expr):
    fn, why = L.compile_predicate(expr)
    assert fn is not None, f"合法谓词被误杀: {expr} -> {why}"


@pytest.mark.parametrize("expr", EXPLOITS)
def test_exploits_are_rejected(expr):
    fn, why = L.compile_predicate(expr)
    assert fn is None, f"沙箱被穿透: {expr}"


def test_lambda_self_binding_is_blocked():
    """平台 `code_safety` 为支持中间变量写法而放行 lambda/推导式/海象，
    于是 `(lambda: 1)()` 能通过平台校验；本模块的严格白名单必须堵住它。
    这一条是**本模块存在的理由之一**，不能被"顺手放宽"改掉。"""
    from backend.services.factor_engine.code_safety import ast_whitelist_check

    src = "def _pred(%s):\n    return ((lambda: 1)())\n" % ", ".join(L.FEATURES)
    platform_ok, _ = ast_whitelist_check(src)
    assert platform_ok is True, "确认平台校验确实放行 lambda（前提变了就要重审本测试）"
    mine_ok, _ = L._ast_strict_check("(lambda: 1)()")
    assert mine_ok is False, "本模块必须拒掉 lambda 自绑定"


def test_comprehension_and_walrus_blocked():
    for expr in ("[x for x in ofi]", "((y := ofi) > 0)"):
        fn, _ = L.compile_predicate(expr)
        assert fn is None, f"应拒: {expr}"


def test_output_shape_mismatch_rejected():
    fn, why = L.compile_predicate("ofi[0:2]")
    assert fn is None


# ── 2. and/or/not 改写（LLM 书写习惯）──────────────────────────────────
def test_bool_ops_work_on_arrays():
    """`x > 0 and y > 0` 在 numpy 数组上必须可用。

    未改写时必然抛 "truth value of an array is ambiguous"——实测 LLM 极爱写
    `and`，不改写会让整条链因为一个书写习惯而失效。
    """
    fn, why = L.compile_predicate("ofi > 0 and vol_bp > 1")
    assert fn is not None, why
    a = np.array([1.0, -1.0, 1.0])
    b = np.array([2.0, 2.0, 0.5])
    out = L.evaluate_predicate(fn, {**{k: np.zeros(3) for k in L.FEATURES},
                                    "ofi": a, "vol_bp": b})
    assert list(out) == [1.0, 0.0, 0.0], "逐元素与：只有第一行两个条件都成立"


def test_or_and_not_semantics():
    fn, _ = L.compile_predicate("ofi > 0 or vol_bp > 1")
    feats = {**{k: np.zeros(3) for k in L.FEATURES},
             "ofi": np.array([-1.0, 1.0, -1.0]),
             "vol_bp": np.array([0.0, 0.0, 5.0])}
    assert list(L.evaluate_predicate(fn, feats)) == [0.0, 1.0, 1.0]

    fn2, _ = L.compile_predicate("not (ofi > 0)")
    feats2 = {**{k: np.zeros(3) for k in L.FEATURES},
              "ofi": np.array([-1.0, 1.0, -1.0])}
    assert list(L.evaluate_predicate(fn2, feats2)) == [1.0, 0.0, 1.0]


def test_where_rewritten_to_np_where():
    rw, why = L._rewrite_bool_ops("where(ofi > 0, 1, -1)")
    assert why == ""
    assert "np.where" in rw, "where(...) 必须改写成 np.where(...) 以过平台白名单"


# ── 3. 因果性 ──────────────────────────────────────────────────────────
def _synthetic_tape(n_bars=400, bar_ms=15000, seed=0):
    rng = np.random.default_rng(seed)
    n = n_bars * 2
    ots = (np.arange(n) * (bar_ms // 2)).astype(np.int64) + 1_700_000_000_000
    mid = 100.0 + np.cumsum(rng.normal(0, 0.02, n))
    bb, ba = mid - 0.005, mid + 0.005
    tts = (np.arange(n_bars) * bar_ms).astype(np.int64) + 1_700_000_000_000
    lo, hi = mid[::2][:n_bars] - 0.01, mid[::2][:n_bars] + 0.01
    sv = np.abs(rng.normal(10, 3, n_bars))
    bv = np.abs(rng.normal(10, 3, n_bars))
    tmk = tts + 5000
    return ots, bb, ba, tts, lo, hi, sv, bv, tmk


def test_features_are_causal_no_lookahead():
    """把**未来**的盘口全改掉，第 i 个特征值不得变化。

    这是最直接的因果性检验：若某个特征偷看了未来，改未来必然改变它。
    """
    ots, bb, ba, tts, lo, hi, sv, bv, tmk = _synthetic_tape(200)
    f1 = L.compute_features(ots, bb, ba, tts, lo, hi, sv, bv, tmk)
    cut = len(ots) // 2
    bb2, ba2 = bb.copy(), ba.copy()
    bb2[cut:] = bb2[cut:] * 1.5          # 篡改未来
    ba2[cut:] = ba2[cut:] * 1.5
    f2 = L.compute_features(ots, bb2, ba2, tts, lo, hi, sv, bv, tmk)
    for k in L.FEATURES:
        if k in ("hour_sin", "hour_cos", "ofi", "ofi_ewma", "trade_intensity",
                 "range_bp", "bars_since_shock"):
            # 这些只依赖盘口时刻/成交，与未来盘口无关
            np.testing.assert_allclose(
                f1[k][:cut], f2[k][:cut], rtol=1e-9, atol=1e-9,
                err_msg=f"特征 {k} 在改未来后发生了变化 ⇒ 有前视")
    # 依赖中间价的特征：过去的必须不变
    for k in ("ret_1", "ret_5", "vol_bp", "spread_bp"):
        np.testing.assert_allclose(
            f1[k][:cut - 2], f2[k][:cut - 2], rtol=1e-9, atol=1e-9,
            err_msg=f"特征 {k} 的过去值被未来篡改影响 ⇒ 有前视")


def test_trade_features_respect_visibility():
    """成交桶必须按**落库时刻**做可见性过滤（F107）：把未来桶的 tmk 推后，
    早期观测的 ofi 不得改变。"""
    ots, bb, ba, tts, lo, hi, sv, bv, tmk = _synthetic_tape(150)
    f1 = L.compute_features(ots, bb, ba, tts, lo, hi, sv, bv, tmk)
    tmk2 = tmk.copy()
    tmk2[100:] = tmk2[100:] + 10_000_000        # 未来的桶"很晚才落库"
    f2 = L.compute_features(ots, bb, ba, tts, lo, hi, sv, bv, tmk2)
    np.testing.assert_allclose(f1["ofi"][:100], f2["ofi"][:100],
                               rtol=1e-9, atol=1e-9)


def test_empty_bars_are_forward_filled_not_zero():
    """回归：稀疏桶必须前向填充，否则"有价桶→空桶"会被算成 −100% 假收益。

    实测事故：SOL 在 15s 网格上有 1460~132015 个空桶，未填充时整条链的净收益
    被虚报成 **−1000bp 量级**，而且每个自洽性检查都过得去（它看起来只是"信号很亏"）。
    这里用稀疏但价格平滑的 tape 直接锁住填充行为。
    """
    bar_ms = 15000
    n_bars = 300
    # 每 3 个桶才有一个盘口观测 ⇒ 2/3 的桶为空
    idx = np.arange(0, n_bars, 3)
    ots = (idx * bar_ms).astype(np.int64) + 1_700_000_000_000
    mid = 100.0 + np.cumsum(np.random.default_rng(0).normal(0, 0.01, len(idx)))
    bb, ba = mid - 0.005, mid + 0.005
    tts = ots.copy()
    lo, hi = mid - 0.01, mid + 0.01
    sv = bv = np.ones(len(idx)) * 10.0
    tmk = tts + 1000

    f = L.compute_features(ots, bb, ba, tts, lo, hi, sv, bv, tmk,
                           bar_ms=bar_ms)
    bar = f["_bar"].astype(np.int64)
    nb = int(bar[-1]) + 1
    filled = L._bar_last((bb + ba) / 2.0, bar, nb)
    assert (filled > 0).all(), "空桶必须被前向填充，不能留 0"

    fwd = L.forward_return_bp(f, bar_ms * 4, bar_ms)
    # 平滑价格 + 填充 ⇒ 前向收益必须是 bp 量级，绝不能出现 −10000bp（= 价格归零）
    assert np.abs(fwd).max() < 500.0, (
        "前向收益出现 bp 量级以上的跳变 ⇒ 空桶没填充（实测曾虚报 −1000bp）")


def test_forward_return_sign_matches_price_move():
    """前向收益的符号必须与价格实际移动方向一致（量纲/方向自检）。"""
    bar_ms = 15000
    n = 200
    ots = (np.arange(n) * bar_ms).astype(np.int64) + 1_700_000_000_000
    mid = 100.0 + np.arange(n) * 0.01          # 单调上涨
    bb, ba = mid - 0.005, mid + 0.005
    tts = ots.copy()
    lo, hi = mid - 0.01, mid + 0.01
    sv = bv = np.ones(n) * 10.0
    f = L.compute_features(ots, bb, ba, tts, lo, hi, sv, bv, tts + 1000,
                           bar_ms=bar_ms)
    fwd = L.forward_return_bp(f, bar_ms * 3, bar_ms)
    assert fwd[:n - 5].mean() > 0, "单调上涨的 tape 前向收益必须为正"


def test_load_tape_drops_stale_scattered_points():
    """盘口表里混着很久以前的散点（实测 SOL 跨度 721 小时、最大间隔 22 天），
    `max_span_hours` 必须把那部分裁掉，否则桶编号被撑爆、统计被稀释成噪声。"""
    import inspect

    src = inspect.getsource(L.load_tape)
    assert "max_span_hours" in src
    assert "cut = int(ots[-1]" in src, "必须从**最后一个观测**往前推窗口"
    # 默认参数也要有窗口，否则默认路径就没有保护
    sig = inspect.signature(L.load_tape)
    assert sig.parameters["max_span_hours"].default > 0


# ── 4. 门禁 ────────────────────────────────────────────────────────────
def _signal_and_fwd(sig, fwd):
    return np.asarray(sig, dtype=float), np.asarray(fwd, dtype=float)


def test_gate_drops_when_no_signal():
    n = 600
    folds, why = L.walk_forward_gate(np.zeros(n), np.ones(n) * 5.0)
    assert folds == []
    assert "从未触发" in why


def test_gate_drops_when_oos_negative():
    """样本内为正、样本外为负 —— 典型的过拟合，必须丢弃。"""
    n = 600
    sig = np.ones(n)
    fwd = np.ones(n) * -3.0
    folds, why = L.walk_forward_gate(sig, fwd,
                                     L.GateParams(min_oos_events=5,
                                                  min_total_events=10))
    assert folds
    assert "非正" in why or "t 不足" in why


def test_gate_drops_when_direction_mismatched():
    """LLM 声明做多，但实际做多为负（方向反了）—— 必须丢弃，
    不能因为"反向做正好赚钱"就放行（那是符号事故，不是假设成立）。"""
    n = 600
    sig = np.ones(n)
    fwd = np.ones(n) * -5.0
    folds, why = L.walk_forward_gate(
        sig, fwd, L.GateParams(min_oos_events=5, min_total_events=10,
                               require_direction_match=True), direction=1)
    assert folds
    bad = [f for f in folds if not f.sign_ok]
    assert bad, "应识别出方向不一致"
    assert "方向不一致" in why


def test_gate_keeps_when_all_folds_positive_and_significant():
    """构造一个真实有效的信号：sig 与未来收益同向。"""
    rng = np.random.default_rng(7)
    n = 900
    fwd = rng.normal(0, 3.0, n)
    # 信号 = 未来收益的符号（强信号，OOS 必然显著）——只用于验证门禁逻辑
    sig = np.where(fwd > 0, 1.0, 0.0)
    folds, why = L.walk_forward_gate(
        sig, fwd, L.GateParams(min_oos_events=5, min_total_events=20,
                               min_fold_t=2.0))
    assert why == "", why
    assert all(f.oos_net_bp > 0 for f in folds)
    assert all(f.oos_t > 2.0 for f in folds)


def test_gate_requires_every_fold_positive_not_just_average():
    """整体平均为正但有一折为负 ⇒ 必须丢弃（不允许"平均掩盖"）。"""
    n = 900
    sig = np.ones(n)
    fwd = np.ones(n) * 5.0
    fwd[n // 2: n // 2 + 120] = -1.0      # 挖一个负段
    folds, why = L.walk_forward_gate(
        sig, fwd, L.GateParams(min_oos_events=5, min_total_events=10,
                               require_all_folds_positive=True))
    assert folds
    assert why != "", "有折为负时必须给出失败原因"


def test_gate_drops_when_events_too_few():
    n = 600
    sig = np.zeros(n)
    sig[::50] = 1.0                       # 只有 12 次触发
    fwd = np.ones(n) * 5.0
    folds, why = L.walk_forward_gate(
        sig, fwd, L.GateParams(min_oos_events=20, min_total_events=200))
    assert "事件不足" in why


# ── 5. gate() 端到端（含编译）──────────────────────────────────────────
def _feats_with(ofi=None, n=900, target_fwd=None):
    """构造特征字典。

    关键：`gate()` 的前向收益是从 `_mid` **派生**的（`forward_return_bp`），
    不是外部传进去的。所以想让 "ofi > 0" 成为有效信号，必须把目标收益写进
    `_mid`，而不是把随机数塞进 `ofi` —— 早先那么写导致夹具自相矛盾，
    测试失败在夹具而不是在代码。
    """
    rng = np.random.default_rng(1)
    f = {k: rng.normal(0, 1, n) for k in L.FEATURES}
    if ofi is not None:
        f["ofi"] = ofi
    if target_fwd is not None:
        # 让 bar k → k+1 的收益 ≈ target_fwd[k]/1e4
        base = 100.0
        steps = np.asarray(target_fwd, dtype=float) / 1e4 * base
        mid = base + np.concatenate([[0.0], np.cumsum(steps)[:-1]])
    else:
        mid = 100.0 + np.cumsum(rng.normal(0, 0.01, n))
    f["_mid"] = mid
    f["_bar"] = np.arange(n, dtype=float)
    return f


def test_gate_records_compile_failure():
    h = L.make_hypothesis("SOL", "os.system('calc')", 1)
    v = L.gate(h, _feats_with())
    assert v.kept is False
    assert v.compile_ok is False
    assert "compile_failed" in v.reason


def _winning_case(n=900, seed=3, edge=6.0):
    """造一个真实有效的信号：`ofi > 0` 时下一桶上涨，否则下跌。

    返回 (feats, hypothesis)。用于验证 keep 路径与 edge_json 结构。
    """
    rng = np.random.default_rng(seed)
    up = rng.random(n) < 0.5
    ofi = np.where(up, 1.0, -1.0)
    fwd = np.where(up, edge + rng.normal(0, 1.0, n),
                   -edge + rng.normal(0, 1.0, n))
    feats = _feats_with(ofi=ofi, n=n, target_fwd=fwd)
    return feats, L.make_hypothesis("SOL", "ofi > 0", 1)


def test_gate_keeps_structure_and_edge_json():
    feats, h = _winning_case()
    v = L.gate(h, feats, L.GateParams(min_oos_events=5, min_total_events=20,
                                      min_fold_t=2.0))
    assert v.compile_ok is True
    assert v.kept is True, v.reason
    ej = v.edge_json()
    assert ej["source"] == "paper_shadow"
    assert ej["n"] > 0
    assert len(ej["folds"]) >= 4
    for f in ej["folds"]:
        assert set(f) >= {"fold", "n", "net_bp", "t"}


def test_edge_source_is_trusted_by_registry():
    from backend.services.lane_registry import EDGE_SOURCES
    feats, h = _winning_case(seed=4)
    v = L.gate(h, feats, L.GateParams(min_oos_events=5, min_total_events=20,
                                      min_fold_t=2.0))
    assert v.edge_json()["source"] in EDGE_SOURCES


def test_verdict_to_dict_is_json_safe():
    import json
    h = L.make_hypothesis("SOL", "ofi > 0", 1)
    v = L.gate(h, _feats_with(n=200))
    json.dumps(v.to_dict())


# ── 6. 回滚开关 + 不造假候选 ─────────────────────────────────────────────
def test_env_switch_disables_everything(monkeypatch):
    monkeypatch.setenv(L.ENV_ENABLED, "0")
    assert L.enabled() is False
    h = L.make_hypothesis("SOL", "ofi > 0", 1)
    v = L.gate(h, _feats_with(n=300))
    assert v.kept is False
    assert "disabled_by_env" in v.reason, "回滚开关必须真的拦住判定"


def test_env_switch_default_on():
    assert L.enabled() is True, "默认应开启（这是研究闭环，不下单）"


def test_disabled_loop_returns_error_not_fake_hypotheses(monkeypatch):
    monkeypatch.setenv(L.ENV_ENABLED, "0")
    res = L.run_closed_loop("SOL", hyps=[])
    assert res["ok"] is False
    assert "disabled_by_env" in res["error"]


def test_llm_unavailable_is_reported_honestly(monkeypatch):
    """LLM 不可用时必须如实报错，**绝不**产生假候选。

    平台既有教训：占位实现会造出"假候选"，比没有更糟。
    """
    import backend.services.llm_config_service as svc

    def _no_config(*a, **k):
        return None

    monkeypatch.setattr(svc, "get_llm_config_for_usage", _no_config)
    res = L.propose("SOL")
    assert res["ok"] is False
    assert res["error"] == "llm_config_unavailable"
    assert res["hypotheses"] == []


# ── 7. 解析健壮性 ──────────────────────────────────────────────────────
def test_parse_plain_json():
    hyps = L.parse_hypotheses(
        '{"statement":"s","predicate":"ofi > 0","direction":1,'
        '"expected_edge_bp":3.0}', "SOL")
    assert len(hyps) == 1
    assert hyps[0].predicate == "ofi > 0"
    assert hyps[0].direction == 1


def test_parse_fenced_and_noisy():
    raw = "好的，这是结果：\n```json\n{\"statement\":\"s\",\"predicate\":\"vol_bp > 2\"," \
          "\"direction\":-1}\n```\n希望有帮助"
    hyps = L.parse_hypotheses(raw, "DOGE")
    assert len(hyps) == 1
    assert hyps[0].direction == -1


def test_parse_rejects_missing_predicate():
    assert L.parse_hypotheses('{"statement":"no pred"}', "SOL") == []


def test_parse_garbage_returns_empty():
    assert L.parse_hypotheses("not json at all", "SOL") == []
    assert L.parse_hypotheses("", "SOL") == []


def test_prompt_has_no_format_placeholder_bug():
    """prompt 里含 JSON 大括号，用 % / format 会炸（实测踩过）。"""
    p = L.build_prompt("SOL", "背景", ["旧假设"])
    assert "{" in p and "}" in p
    assert "predicate" in p
