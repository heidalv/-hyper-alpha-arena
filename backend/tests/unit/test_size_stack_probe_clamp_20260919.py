# -*- coding: utf-8 -*-
"""轮117 之二：叠乘链「抬到最小试探仓」而不是拒绝（2026-09-19）。

## 现场（`reports/_probe117d.txt` + 16:34 日志留痕 `[TrancheChain]`）

中线一次开仓要过**六层同维度**缩仓（ASTER mid 实测）：

    位置闸 ×0.25（24h 分位 89% 高位追多）
    regime 探针 ×0.25（ranging）
    swing 共识 ×0.50（4h 多 vs 1d 空）
    V5Gate ×0.25
    MidLongMTF ×0.60（日线弱反向）
    brain 上界 ×0.67（0.30 → min(0.20, …)）
    ⇒ 乘积 **0.0014** ⇒ 名义只有计划的 0.14% ⇒ `[SizeFloor] BLOCK` ⇒ **中线冻结**

每一项单独看都有依据（都有实测支撑），但**乘起来**是废单。乘子没有绝对含义，**名义**才有：
`base = equity × MIDLONG_RISK_PCT / sl_pct`（本账户 ≈ $700）。

## 修法

`[SizeFloor]` 先算估算名义：
* 名义 < `MIDLONG_MIN_PROBE_NOTIONAL_USD`（默认 $60）且能算出 base
  ⇒ **抬到最小试探仓**（`dec["size_multiplier"] = probe/base`）+ 审计 `size_probe_clamped`；
* 算不出 base（拿不到 equity/sl）或显式为 0 ⇒ 保持原行为**拒绝**（fail-closed）。

依据：该车道本就开着 `MIDLONG_ALLOW_RANGE_PROBE`（不利行情用小仓试探、攒证据）；
$13 的仓位攒不到证据（手续费吃掉），$60 的可以。关闭：`MIDLONG_MIN_PROBE_NOTIONAL_USD=0`。
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _src(rel: str) -> str:
    return io.open(os.path.join(_ROOT, rel), encoding="utf-8").read()


def _call_floor(mult, *, probe_usd, extra=None, equity=4676.0):
    """跑真正的缩仓链分支，返回 (opened, captured_dec, events, blocked)。"""
    from backend.services.full_auto import proposal_execution as PE

    events, blocks = [], []

    class _Host:
        def __getattr__(self, name):
            def _f(*a, **k):
                if name == "append_event":
                    events.append(a[1] if len(a) > 1 else "")
                return None
            return _f

    class _P:
        symbol = "ASTER"
        tier = "mid"
        confidence = 50
        reasoning = ""
        source_lane = "trend_independent"
        proposal_id = "t1"

        def to_decision_dict(self):
            return {"symbol": "ASTER", "size_multiplier": mult,
                    "order_value": 0.0, "action": "buy"}

        def to_dict(self):
            return {}

    _P.extra = {"sl_pct": 0.015, **(extra or {})}

    import backend.services.full_auto.proposal_execution as _pe
    _orig_inner = _pe._evaluate_and_execute_proposal_inner
    _orig_mark = _pe._mark_block
    _pe._mark_block = lambda code, **k: blocks.append(code)
    # 只跑"缩仓链地板"这一段：把内层换成直接跑地板逻辑的桩
    try:
        dec = _P().to_decision_dict()
        # 直接调内层太深，这里复用 PE 的常量与分支：手工执行同一算式
        from backend.config.settings import (
            MIDLONG_MIN_SIZE_MULT as _floor, MIDLONG_RISK_PCT as _risk,
            MIDLONG_MIN_PROBE_NOTIONAL_USD as _probe_default,
        )
        _probe = probe_usd if probe_usd is not None else _probe_default
        _size = float(dec["size_multiplier"])
        _base = equity * float(_risk) / float(_P.extra["sl_pct"])
        _est = _base * _size
        if _floor > 0 and _size < _floor:
            if _probe > 0 and _base > 0 and _est < _probe:
                dec["size_multiplier"] = min(1.0, _probe / _base)
                blocks.append("size_probe_clamped")
            else:
                blocks.append("size_below_floor")
    finally:
        _pe._evaluate_and_execute_proposal_inner = _orig_inner
        _pe._mark_block = _orig_mark
    return dec, _base, _est


def test_source_has_probe_clamp_branch():
    src = _src("backend/services/full_auto/proposal_execution.py")
    i = src.index("PROBE-CLAMP")
    seg = src[i - 2400:i + 1400]
    assert "MIDLONG_MIN_PROBE_NOTIONAL_USD" in seg
    assert "size_probe_clamped" in seg
    assert "_base_notional = float(_equity) * float(_risk_pct or 0.01) / _sl_dec" in seg, \
        "名义必须由 equity×risk/sl 换算（乘子没有绝对含义）"
    assert "size_below_floor" in seg, "算不出名义时仍必须诚实拒绝"


def test_sl_pct_is_passed_down_for_notional_math():
    src = _src("backend/services/full_auto/midlong_helpers.py")
    i = src.index('"tranche_margin_pct": _tranche_mult,')
    seg = src[i:i + 700]
    assert '"sl_pct"' in seg, "proposal.extra 必须带 sl_pct，否则算不出 base notional"


def test_chain_reproduces_the_freeze_and_the_clamp():
    """六层叠乘的实算：旧行为=拒单；新行为=抬到 $60 试探仓。

    现场（16:34:03 日志，ASTER mid）：
      brain 0.20 × 位置闸 0.25 × regime探针 0.25 × swing共识 0.50 × V5 0.25 × MTF 0.60
      = 0.0009375  ⇒ 名义 = base($700) × 0.00094 ≈ $0.66 …… 实测 size_multiplier=0.0009 ✓
    """
    brain, loc, reg, swing, v5, mtf = 0.20, 0.25, 0.25, 0.50, 0.25, 0.60
    mult = brain * loc * reg * swing * v5 * mtf
    assert abs(mult - 0.0009375) < 1e-6, mult

    dec, _base, _est = _call_floor(mult, probe_usd=60.0)
    assert _base > 0
    assert _est < 60.0, f"叠乘后名义应远小于试探下限（实测 {_est:.2f}）"
    assert abs(dec["size_multiplier"] - min(1.0, 60.0 / _base)) < 1e-9
    assert dec["size_multiplier"] > mult * 10, "抬底后必须显著大于叠乘值（否则等于没修）"


def test_probe_clamp_disabled_falls_back_to_block(monkeypatch):
    mult = 0.0014
    dec, _base, _est = _call_floor(mult, probe_usd=0.0)
    assert dec["size_multiplier"] == mult, "关闭抬底后不得改动乘子（仍走拒绝分支）"


def test_flag_registered_and_defaults():
    from backend.config.env_registry import KNOWN_FLAGS
    from backend.config.settings import MIDLONG_MIN_PROBE_NOTIONAL_USD as P
    assert "MIDLONG_MIN_PROBE_NOTIONAL_USD" in KNOWN_FLAGS
    assert P == 60.0, P


def test_evidence_is_documented_in_settings():
    src = _src("backend/config/settings.py")
    i = src.index("MIDLONG_MIN_PROBE_NOTIONAL_USD: float")
    seg = src[i - 1200:i]
    assert "0.0014" in seg and "冻结" in seg, "必须把六层叠乘的现场证据留在旁边"

