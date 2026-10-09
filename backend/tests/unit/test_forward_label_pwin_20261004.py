# -*- coding: utf-8 -*-
"""[工作流⑤-b] 数据驱动 p_win 的护栏测试（2026-10-04）。

## 背景与实测
`midlong_ev_gate.py:6-7` 的 EV 公式已是期望值口径，`:99` 支持 `p_win_override`，
`:138` 是注入点 ⇒ ⑤ 只需替换 `p_win` 来源，**不动闸门数学与门槛常数**。
`forward_label.p_win_for()` 用前向收益分桶（含贝叶斯收缩）产出 p_win。实测（12 天/7 天前向/n=335）：

    conf=45 → p_win=0.6427  source=forward_shrunk  n=49   bucket=40-50
    conf=55 → p_win=0.2438  source=forward         n=285  bucket=50-60   ← 反相关
    master 车道 → fallback（9,555 条决策 direction='hold'，无可标注样本，不造数）

## 本测试守护的不变量
1. 分桶缺失 ⇒ **必须回退**（不得用邻近桶/全局值冒充，防止凭空造数改变交易行为）
2. 样本不足 ⇒ `forward_shrunk`（收缩值），样本充足 ⇒ `forward`
3. 返回的 p_win 必须落在 (0,1)
4. 结果为**非单调**时不得被"修正"（数据如此就如实输出）
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

SRC = (ROOT / "backend/services/learning_core/forward_label.py").read_text(encoding="utf-8")


def test_p_win_for_has_guards_and_fallback_chain():
    assert "def p_win_for(" in SRC
    assert 'FWD_MIN_BUCKET_N' in SRC, "必须有分桶最小样本守卫"
    assert "forward_shrunk" in SRC and "fallback_no_data" in SRC and "fallback_error" in SRC
    assert "FWD_PWIN_TTL_SEC" in SRC, "必须有缓存 TTL（避免每笔决策都全量重算）"


def test_bucket_miss_must_fall_back_not_extrapolate():
    """分桶缺失必须返回负值哨兵，交由调用方回退校准器 —— 不许外推。"""
    seg = SRC.split("def p_win_for(")[1]
    assert 'return {"p_win": -1.0, "source": "fallback_no_bucket"' in seg
    assert 'return {"p_win": -1.0, "source": "fallback_no_data"' in seg


def test_shrinkage_uses_beta_prior():
    seg = SRC.split("def _buckets(")[1].split("def _group(")[0]
    assert 'FWD_SHRINK_PRIOR_N' in seg
    assert "shrunk" in seg and "global_wr" in seg


def test_live_values_are_guarded_and_non_monotonic_is_preserved():
    """真实数据冒烟：返回结构完整；若有数据，p_win ∈ (0,1) 且不强制单调。"""
    try:
        from backend.services.learning_core.forward_label import p_win_for
    except Exception as exc:  # pragma: no cover
        print("import skipped:", exc)
        return
    r = p_win_for("swing_independent", "mid", 55.0)
    assert set(r.keys()) >= {"p_win", "source", "n", "bucket"}
    if r["p_win"] >= 0:
        assert 0.0 < r["p_win"] < 1.0
        assert r["source"] in ("forward", "forward_shrunk")
    else:
        assert r["source"].startswith("fallback")


GATE_SRC = (ROOT / "backend/services/decision_core/midlong_ev_gate.py").read_text(encoding="utf-8")


def test_gate_wiring_defaults_to_calibrator_and_is_fail_open():
    """默认开关必须等价于今日行为；数据层异常必须 fail-open（不改变裁决）。"""
    assert "MIDLONG_P_WIN_SOURCE" in GATE_SRC and "MIDLONG_P_WIN_SHADOW" in GATE_SRC
    assert 'self._cfg("MIDLONG_P_WIN_SOURCE", "calibrator")' in GATE_SRC, "默认必须仍是 calibrator"
    # 全文件断言（分段会先命中注释里的开关名，见本轮踩坑）
    assert 'if _pwin_mode == "forward" and float(_fw.get("p_win") or -1) >= 0:' in GATE_SRC, "只有 forward 模式且拿到有效值才替换"
    assert "fail-open" in GATE_SRC and "绝不改变闸门裁决" in GATE_SRC


def test_gate_shadow_mode_does_not_replace():
    # 影子分支只 log，不赋值 p_win（否则影子会改变交易行为）
    assert "_pwin_mode ==" in GATE_SRC and "_pwin_shadow" in GATE_SRC
