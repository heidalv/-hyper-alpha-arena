# -*- coding: utf-8 -*-
"""[2026-09-02] 因子权重体系完整性单测（B4 截断 key 清理 + B5 fail-open 消除）。

B4 背景：旧代码把 signal_type（格式 f"factor:{name}"）按 VARCHAR(28) 截断，
减去 7 字符前缀后因子名恰好被切到 21 字符。写入侧已于 08-29 随迁移 0008 修复，
但 IC 评估的 lookback 窗口内仍有历史行，每轮都把截断名重新写进
data/factor_runtime_weights.json（实测 29 个孤儿 key）。这些 key 永远查不到——
所有消费者都用全名走 runtime_weights.get(name)。

B5 背景：因子权重链上原有一整串 fail-open，任一环出问题都会静默退化成"等权
满权出信号"：
  1. combo_weights 全零 → 回退 1/n 均权（把刚归零的因子还回等权）；
  2. active_factor_set 的 except 分支 → 等权 1.0；
  3. wmap.get(fid, 1.0) → 未定权因子满权；
  4. `rec.get("runtime_weight") or 1.0` → Python falsy 陷阱，显式 0.0 被还原成 1.0；
  5. _runtime_weights 兜底路径遍历 JSON 顶层键 → 必抛 ValueError → 永远返回 {}；
  6. 权重文件相对路径依赖进程 cwd → 非仓库根启动时静默读不到。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))


# ─────────────────────── B4：历史截断名识别与剔除 ───────────────────────

def test_truncated_name_predicate():
    """判据必须同时满足"长度恰为 21"与"无对应因子源文件"。"""
    from backend.services.factor_ic_evaluator import (
        _LEGACY_TRUNCATED_NAME_LEN,
        _is_legacy_truncated_name,
    )

    assert _LEGACY_TRUNCATED_NAME_LEN == 21, "28 字符列宽 - len('factor:') = 21"

    known = {"ai_gen_short_timeout_avoid", "ai_gen_exactly_21_chars_x"}
    truncated = "ai_gen_short_timeout_"          # 21 字符、无同名源文件
    assert len(truncated) == 21
    assert _is_legacy_truncated_name(truncated, known) is True

    # 长度不是 21 → 不是 28 字符截断产物（可能是因子已删除的正常孤儿，须保留）
    assert _is_legacy_truncated_name("ai_gen_bsq", known) is False
    assert _is_legacy_truncated_name("ai_gen_short_timeout_avoid", known) is False

    # 真实存在的 21 字符因子名不能被误判
    real21 = "ai_gen_exactly_21_cha"
    assert _is_legacy_truncated_name(real21, known | {real21}) is False


def test_predicate_disabled_when_known_set_empty():
    """因子名集合获取失败（空集）时必须退化为不过滤，避免误杀有效权重。"""
    from backend.services.factor_ic_evaluator import _is_legacy_truncated_name

    assert _is_legacy_truncated_name("ai_gen_short_timeout_", set()) is False


def test_recover_truncated_name_unique_prefix_only():
    """截断名还原：唯一前缀 → 全名；多义 / 无匹配 / 非截断名 → None。

    [2026-09-02] 实测 14 天窗口 8242 行截断样本中 6668 行（35 个名）可唯一还原，
    还原后 16 个因子跨过 MIN_SAMPLES、n<30 档权重占比 27.8%→14.6%。
    多义时绝不能猜——把 A 因子的盈亏记到 B 头上比丢样本更糟。
    """
    from backend.services.factor_ic_evaluator import _recover_truncated_name

    known = {
        "ai_gen_extreme_reversal",        # 唯一前缀
        "ai_gen_short_timeout_vol",       # ↓ 多义：共享 21 字符前缀
        "ai_gen_short_timeout_avoid",
        "ai_gen_exactly_21_chars_x",
    }
    # 唯一前缀：还原
    t1 = "ai_gen_extreme_revers"
    assert len(t1) == 21
    assert _recover_truncated_name(t1, known) == "ai_gen_extreme_reversal"

    # 多义：不还原
    t2 = "ai_gen_short_timeout_"
    assert len(t2) == 21
    assert _recover_truncated_name(t2, known) is None

    # 无任何前缀匹配（因子已删除）：不还原
    t3 = "ai_a101_close_open_4h"
    assert len(t3) == 21
    assert _recover_truncated_name(t3, known) is None

    # 非截断名（长度不是 21 / 本身就在 known 里）：不动
    assert _recover_truncated_name("ai_gen_bsq", known) is None
    assert _recover_truncated_name("ai_gen_extreme_reversal", known) is None

    # known 为空 → 判定器退化为不过滤 → 不还原
    assert _recover_truncated_name(t1, set()) is None


def test_resolvable_factor_names_non_empty():
    """因子源文件扫描应能拿到集合，否则 B4 过滤等于没开。"""
    from backend.services.factor_ic_evaluator import _resolvable_factor_names

    names = _resolvable_factor_names()
    assert len(names) > 50, f"仅扫到 {len(names)} 个因子名，路径推导可能不对"
    assert not any(n.startswith("__") for n in names)


def test_truncated_keys_in_live_file_are_recognized(capsys):
    """存量权重文件里的截断残留必须能被过滤器逐个识别。

    这里刻意**不**断言"文件已经干净"。该文件是活的运行时产物：只要还有跑着旧
    代码的后端进程，它每轮 IC 评估都会把截断名重新写回来（实测清理后 2 小时内
    就从 97 条涨回 126 条、截断 key 从 0 变 36）。那反映的是「服务尚未重启」这个
    运维状态，不是代码属性 —— 拿它当断言会让用例随后台进程的作息随机红绿。

    真正该守的代码属性是：过滤器认得出真实数据里的每一个截断残留，因而服务一旦
    重启，这些 key 就会被自然剔除、不再写回。
    """
    from backend.services.factor_ic_evaluator import (
        RUNTIME_WEIGHTS_FILE,
        _LEGACY_TRUNCATED_NAME_LEN,
        _is_legacy_truncated_name,
        _resolvable_factor_names,
    )

    if not os.path.exists(RUNTIME_WEIGHTS_FILE):
        pytest.skip("权重文件不存在（IC 评估尚未产出）")
    with open(RUNTIME_WEIGHTS_FILE, "r", encoding="utf-8") as f:
        raw = json.load(f) or {}
    known = _resolvable_factor_names()
    weights = raw.get("weights") or {}

    suspects = [
        k for k in weights
        if len(k) == _LEGACY_TRUNCATED_NAME_LEN and k not in known
    ]
    unrecognized = [k for k in suspects if not _is_legacy_truncated_name(k, known)]
    assert not unrecognized, (
        f"过滤器漏判了截断残留（重启后仍会写回）: {unrecognized[:5]}"
    )

    if suspects:
        with capsys.disabled():
            print(
                f"\n  [提示] 权重文件仍有 {len(suspects)} 个截断残留 key —— "
                "说明后端服务还在跑 B4 修复前的旧代码；重启后本轮 IC 评估即会剔除。"
            )


# ─────────────────────── B5：fail-open 消除 ───────────────────────

def test_weights_path_is_absolute():
    """权重文件路径必须绝对化，不能依赖进程 cwd。"""
    from backend.services.factor_ic_evaluator import RUNTIME_WEIGHTS_FILE
    from backend.services.factor_engine.factor_slimming_audit import (
        STATE_PATH, WEIGHTS_PATH,
    )

    for p in (RUNTIME_WEIGHTS_FILE, WEIGHTS_PATH, STATE_PATH):
        assert os.path.isabs(p), f"{p} 仍是相对路径"
    # 审计模块与评估模块必须指向同一份权重文件，否则降权写不到被读的那份
    assert os.path.normcase(os.path.normpath(WEIGHTS_PATH)) == \
        os.path.normcase(os.path.normpath(RUNTIME_WEIGHTS_FILE))


def test_explicit_zero_weight_survives_paper_cap():
    """显式 0.0 权重不能被 `or 1.0` 还原成满权（falsy 陷阱）。"""
    from backend.services.factor_engine import scalp_active_factor_set as mod

    class _Store:
        @staticmethod
        def list_active(tenant_id=None):
            return [{"factor_id": "zeroed", "extra": {"horizon": "scalp",
                                                      "role": "paper"}}]

    monkey = pytest.MonkeyPatch()
    try:
        # custom_factor_store 在函数体内 import，必须 patch 源模块属性
        monkey.setattr(
            "backend.services.factor_engine.custom_factor_store.custom_factor_store",
            _Store, raising=False)
        monkey.setattr(mod, "_resolve_tenant_id", lambda: 1, raising=False)
        monkey.setattr(mod, "_is_scalp", lambda r: True, raising=False)
        monkey.setattr(mod.ScalpActiveFactorSet, "_tradable_ast_bridge",
                       staticmethod(lambda: []))
        monkey.setattr(mod.ScalpActiveFactorSet, "_runtime_weights",
                       staticmethod(lambda: {}))
        monkey.setattr(
            "backend.services.factor_engine.combo_weights.resolve_combo_weights",
            lambda records, manual: {"zeroed": 0.0},
        )
        out = mod.ScalpActiveFactorSet().get_active_factors()
    finally:
        monkey.undo()

    assert len(out) == 1
    assert out[0]["runtime_weight"] == 0.0, (
        "归零权重被还原成满权，被判定不可信的因子会继续满权投票"
    )


def test_resolve_failure_is_fail_closed():
    """组合权重解析抛异常时应全零（fail-closed），不再退化等权。"""
    from backend.services.factor_engine import scalp_active_factor_set as mod

    class _Store:
        @staticmethod
        def list_active(tenant_id=None):
            return [{"factor_id": "f1", "extra": {"horizon": "scalp"}},
                    {"factor_id": "f2", "extra": {"horizon": "scalp"}}]

    def _boom(records, manual):
        raise RuntimeError("simulated resolve failure")

    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(
            "backend.services.factor_engine.custom_factor_store.custom_factor_store",
            _Store, raising=False)
        monkey.setattr(mod, "_resolve_tenant_id", lambda: 1, raising=False)
        monkey.setattr(mod, "_is_scalp", lambda r: True, raising=False)
        monkey.setattr(mod.ScalpActiveFactorSet, "_tradable_ast_bridge",
                       staticmethod(lambda: []))
        monkey.setattr(mod.ScalpActiveFactorSet, "_runtime_weights",
                       staticmethod(lambda: {"f1": 1.2, "f2": 0.8}))
        monkey.setattr(
            "backend.services.factor_engine.combo_weights.resolve_combo_weights",
            _boom,
        )
        out = mod.ScalpActiveFactorSet().get_active_factors()
    finally:
        monkey.undo()

    assert [r["runtime_weight"] for r in out] == [0.0, 0.0], (
        "解析失败后仍给出非零权重 = 按不可信权重继续开仓"
    )


def test_runtime_weights_fallback_reads_weights_section():
    """兜底直读路径必须解析 weights 段，而不是 JSON 顶层键。

    原实现遍历顶层（updated_at/lookback_days/weights/stats），
    float("2026-09-02T...") 必抛 ValueError → 这条兜底从未真正生效。
    """
    from backend.services.factor_engine import scalp_active_factor_set as mod
    from backend.services.factor_ic_evaluator import RUNTIME_WEIGHTS_FILE

    if not os.path.exists(RUNTIME_WEIGHTS_FILE):
        pytest.skip("权重文件不存在")

    def _boom():
        raise RuntimeError("force fallback")

    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(
            "backend.services.factor_ic_evaluator.load_runtime_factor_weights",
            _boom,
        )
        got = mod.ScalpActiveFactorSet._runtime_weights()
    finally:
        monkey.undo()

    with open(RUNTIME_WEIGHTS_FILE, "r", encoding="utf-8") as f:
        expected = (json.load(f) or {}).get("weights") or {}
    if expected:
        assert got, "兜底路径返回空 → 全体因子静默退化等权"
        assert set(got) == set(expected)
        # 顶层元数据键绝不能出现在结果里
        for meta_key in ("updated_at", "lookback_days", "weights", "stats"):
            assert meta_key not in got
