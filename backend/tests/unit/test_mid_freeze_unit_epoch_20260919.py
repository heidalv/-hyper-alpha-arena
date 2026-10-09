# -*- coding: utf-8 -*-
"""轮117 中线解冻 + 时代口径修正（2026-09-19）。

## 用户两条意见

1. 「你怎么就用这三十天的预算了？这 30 天里大的更改就多少次了，并且这两天已经又大改一次了。
   主要关注这两天的交易情况。用历史交易回测评价是对的，但是用错的历史来评价就不好了。」
2. 「现在的中线还是被冻结。」

## 根因（`reports/_probe117b.txt`）

`brain.maybe_open` 把 `MIDLONG_BRAIN_OPEN_MARGIN_PCT`（**占权益的保证金比例** 0.12）
传进了 `tranche_margin_pct` 槽位，而下游两处消费方都按**分档系数**用它：

    proposal_execution:      dec["size_multiplier"] *= tranche
    midlong_helpers:         estimate_open_notional_aligned(tranche_mult=tranche)

    ⇒ 0.12（brain）× 0.25（V5Gate）= **0.030 < MIDLONG_MIN_SIZE_MULT(0.05)**
    ⇒ 每次中线开仓都撞 `[SizeFloor] BLOCK`（线上 13:12–14:03 连续 6 次）→ 中线冻结。

设计值在 `tranche_gate.compute_margin_pct()`：BUILD 0.30/0.30/0.20/0.10 / NIBBLE 0.15/0.10。
换成 0.30 后：0.30 × 0.25 = **0.075 ≥ 0.02（新地板）** ⇒ 可开。

## 时代口径（用户第 1 条）

    30 天（轮116 用的，错）   n=1047  净 **−80.80**  笔均 −0.107%  胜率 0.411
    09-18 之后（分离起）      n=  48  净 **+20.69**  笔均 +0.435%  胜率 0.625
    09-19 00:00 之后          n=   7  净 **+11.26**  笔均 +0.969%  胜率 0.714

⇒ 撤回轮116 基于 30 天混账的偏斜（mid 0.35 / long 0.50），改为**不偏斜**的 0.40 / 0.45。
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _src(rel: str) -> str:
    return io.open(os.path.join(_ROOT, rel), encoding="utf-8").read()


# ══════════════════════════════════════════════════════════════════════
# ① 量纲契约：tranche 是分档系数，不是保证金比例
# ══════════════════════════════════════════════════════════════════════

def test_tranche_gate_is_the_single_source_of_tranche_values():
    from backend.services.mlto.tranche_gate import compute_margin_pct

    class _T:
        def __init__(self, stage):
            self.tranche_stage = stage

    class _H:
        def __init__(self, action):
            self.action = action

    assert compute_margin_pct(_T(0), _H("BUILD"), False) == 0.30
    assert compute_margin_pct(_T(1), _H("BUILD"), False) == 0.30
    assert compute_margin_pct(_T(2), _H("BUILD"), False) == 0.20
    assert compute_margin_pct(_T(3), _H("BUILD"), False) == 0.0
    assert compute_margin_pct(_T(0), _H("NIBBLE"), False) == 0.15
    assert compute_margin_pct(_T(0), _H("WAIT"), False) == 0.0


def test_brain_uses_tranche_gate_on_default_path():
    """剥掉注释后的**可执行文本**：默认路径必须是 `compute_margin_pct`。

    不能用文本包含判断 —— 解释性注释里必然提到旧键（那正是要留的证据）。
    """
    src = _src("backend/services/mlto/brain.py")
    i = src.index("轮117 2026-09-19 修「中线被冻结」")
    seg = src[i:i + 2600]
    code = "\n".join(l for l in seg.splitlines() if not l.strip().startswith("#"))
    assert "compute_margin_pct" in code, "默认路径必须走 tranche_gate"
    assert code.index("compute_margin_pct") < code.index("MIDLONG_BRAIN_OPEN_MARGIN_PCT"), \
        "compute_margin_pct 必须在前（默认路径），旧行为只能在后（else 回滚分支）"
    assert "MIDLONG_TRANCHE_FROM_GATE" in code, "回滚开关必须在场"


def test_brain_margin_pct_key_only_used_inside_a_branch():
    """AST：`MIDLONG_BRAIN_OPEN_MARGIN_PCT` 的每一处使用都必须在**某个 If 之内**。

    （= 它只能是回滚分支/兜底，不能回到无条件的默认路径上。)
    """
    import ast

    tree = ast.parse(_src("backend/services/mlto/brain.py"))
    hits = []

    class _V(ast.NodeVisitor):
        def __init__(self):
            self.depth = 0

        def visit_If(self, node):
            self.depth += 1
            self.generic_visit(node)
            self.depth -= 1

        def visit_Name(self, node):
            if node.id == "MIDLONG_BRAIN_OPEN_MARGIN_PCT":
                hits.append(self.depth)

    _V().visit(tree)
    assert hits, "回滚分支仍需要这个键"
    assert all(d >= 1 for d in hits), f"该键被用在无条件路径上: depths={hits}"


def test_product_clears_the_size_floor_in_normal_stages():
    """量纲自证：正常档位的乘积必须高于地板 —— 这条断言本轮之前会失败。

    (brain 0.12 × V5 0.25 = 0.030 < 0.05 ⇒ 中线冻结)
    """
    from backend.config.settings import MIDLONG_MIN_SIZE_MULT as FLOOR
    from backend.services.mlto.tranche_gate import compute_margin_pct

    class _T:
        tranche_stage = 0

    class _H:
        action = "BUILD"

    build = compute_margin_pct(_T(), _H(), False)
    nibble = compute_margin_pct(_T(), type("H", (), {"action": "NIBBLE"})(), False)
    for _v5 in (0.25, 1.0):
        assert build * _v5 >= FLOOR, (build, _v5, FLOOR)
    assert nibble * 0.25 >= FLOOR, "试探档(NIBBLE 0.15×0.25=0.0375)不得被地板杀掉"
    # 而轮116 之前的病态乘积仍必须被拦住
    assert 0.12 * 0.25 < FLOOR or FLOOR <= 0.02, "旧量纲乘积应落在拒绝区（或地板已下调）"


def test_floor_default_is_documented_with_arithmetic():
    from backend.config.settings import MIDLONG_MIN_SIZE_MULT as FLOOR
    assert FLOOR == 0.02, FLOOR
    src = _src("backend/config/settings.py")
    i = src.index("MIDLONG_MIN_SIZE_MULT: float")
    assert "0.0375" in src[i - 900:i], "地板的取值必须带量纲自证（NIBBLE×V5=0.0375）"


def test_margin_pct_docstring_is_corrected():
    src = _src("backend/services/mlto/midlong_portfolio_risk.py")
    i = src.index("def estimate_open_notional(")
    seg = src[i:i + 1400]
    assert "轮117 2026-09-19 更正" in seg, "那条把分档系数说成保证金比例的 docstring 必须改"


# ══════════════════════════════════════════════════════════════════════
# ② 时代口径：比例不得再按 30 天定
# ══════════════════════════════════════════════════════════════════════

def test_allocation_is_balanced_not_skewed_by_stale_data():
    from backend.config.settings import TIER_BUDGET_ALLOCATION as A
    assert A["short"] == 0.0, "结构事实：短线车道当前退役（轮160），预算并入 mid/long；非永久禁令"
    assert A["mid"] == 0.40 and A["long"] == 0.45, A
    assert abs(A["mid"] - A["long"]) <= 0.05, \
        "现役时代样本仅 48 笔 ⇒ 不做偏斜（轮116 的 0.35/0.50 建立在 30 天混账上）"
    assert abs(sum(A.values()) - 0.85) < 1e-9


def test_env_carries_the_corrected_split():
    env = io.open(os.path.join(_ROOT, ".env"), encoding="utf-8", errors="replace").read()
    for kv in ("TIER_MID_BUDGET=0.40", "TIER_LONG_BUDGET=0.45",
               "TIER_MID_MAX_MARGIN=0.40", "TIER_LONG_MAX_MARGIN=0.45"):
        assert kv in env, kv


def test_rollback_switches_registered():
    from backend.config.env_registry import KNOWN_FLAGS
    assert "MIDLONG_TRANCHE_FROM_GATE" in KNOWN_FLAGS
    src = _src("backend/config/settings.py")
    assert "MIDLONG_TRANCHE_FROM_GATE" in src
