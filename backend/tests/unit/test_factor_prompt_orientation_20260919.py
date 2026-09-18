# -*- coding: utf-8 -*-
"""轮106 长线/中线因子 → LLM 注入的方向语义回归测试（2026-09-19）。

## 背景（实测）

注入链路本身是通的：`mlto_cycle` → `inject_midlong_indicators`（写
`ms["midlong_factors"] = build_snapshot(sym)`）→ `qual_layer._build_market_brief`
渲染成 prompt 里的一行。实测 BTC 渲染结果是：

    中长线因子证据(仅供分析,不投票): count=14 |
      4h[macd=+312.086, hv=+78.854, obv=+29.029, momentum=+6.131,
         supertrend=+1.000, ai_gen_short_timeout_avoid=+0.480] | 1d[obv=+2.893, ...]

两个问题：

1. **方向相反**：量化层对 IC<0 的因子是**反着用**的（`expected_sign=-1`，
   `vote = orient × z`）。上面 6 个里 5 个是反向因子 —— LLM 看到「MACD 大正」
   会读成动能强多头，而因子路由对同一读数投的是**空票**（macd@4h IC=-0.114）。
2. **排序按量纲**：`sorted(key=abs(raw))` 让原始值最大的因子长期霸榜
   （BTC macd 312 / hv 78.9 vs SOL macd 1.18），与信息量无关。

修复：`build_snapshot` 附 `meta = {fid: {sign, ic[, ic_capped]}}`（零成本，不重算因子），
`qual_layer` 据此标注 `(反向)` / `(ICIR代理)`、按 |IC| 排序，并加一行方向语义说明。
`4h`/`1d` 的原始 float 结构保持不变（`midlong_helpers` 的 SignalTradeFeedback 依赖它）。
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from backend.services.factor_engine.midlong_active_factor_set import MidLongActiveFactorSet

_SNAP = {
    "count": 3,
    "4h": {"macd": 312.086, "vwap": 0.061531, "hv": 78.854},
    "1d": {"obv": 2.8929},
    "meta": {
        "macd": {"sign": -1, "ic": 0.1143},
        "vwap": {"sign": -1, "ic": 0.4728},
        "hv": {"sign": 1, "ic": 0.0961},
        "obv": {"sign": -1, "ic": 0.1109},
    },
}


class _Pkt:
    symbol = "BTC"
    tier = "long"
    quant_brief = {}
    analyst_reports = {}
    portfolio = {}
    orchestrator = {"bias": "bullish"}

    def __init__(self, ms):
        self.market_summary_sym = ms


def _ms(snap, **extra):
    """market_summary（**不是**快照本身）：注入点是 `ms["midlong_factors"]`。"""
    return {"midlong_factors": snap, "current_price": 81150.0,
            "framework_signals": "bullish", **extra}


def _brief(ms):
    brief = _bld(_Pkt(ms))
    return next((ln for ln in brief.splitlines() if "中长线因子证据" in ln), "")


def _semantic_line(ms):
    return next((ln for ln in _bld(_Pkt(ms)).splitlines() if "方向语义" in ln), "")


def _bld(pkt):
    import backend.services.mlto.qual_layer as ql
    return ql._build_market_brief(pkt)


# ══════════════════════════════════════════════════════════════════════
# ① build_snapshot 的方向语义标注
# ══════════════════════════════════════════════════════════════════════

def test_snapshot_carries_sign_and_ic_meta():
    snap = MidLongActiveFactorSet().build_snapshot("BTC")
    meta = snap.get("meta") or {}
    assert meta, "快照必须带 meta（否则 prompt 无法标注方向）"
    assert meta.get("macd@4h", {}).get("sign") == -1, meta.get("macd@4h")
    assert meta.get("hv@4h", {}).get("sign") == 1, meta.get("hv@4h")
    assert meta["macd@4h"]["ic"] > 0


def test_meta_keys_match_snapshot_display_keys():
    """渲染端按**显示键**查 meta：registry 因子算出来的键是裸 id（`macd`），
    而 store 的 factor_id 带周期后缀（`macd@4h`）—— 两处都要登记，否则标注失效。"""
    snap = MidLongActiveFactorSet().build_snapshot("BTC")
    meta = snap.get("meta") or {}
    for tf in ("4h", "1d"):
        for k in (snap.get(tf) or {}):
            assert k in meta, f"{k} 在 meta 里查不到 → prompt 标注会静默失效"


def test_ast_meta_ic_is_capped_to_formula_scale():
    """AST 桥接的 ic 实为 ICIR（~1.3），meta 里必须封顶（与路由票权同一把尺子），
    否则它会在 prompt 的 |IC| 排序里长期霸榜。"""
    from backend.services.factor_engine.midlong_factor_route import _ast_ic_cap
    snap = MidLongActiveFactorSet().build_snapshot("BTC")
    meta = snap.get("meta") or {}
    for fid, m in meta.items():
        if str(fid).startswith("evo_"):
            assert m["ic"] <= _ast_ic_cap() + 1e-9, (fid, m)
            assert m.get("ic_capped") == pytest.approx(_ast_ic_cap())


# ══════════════════════════════════════════════════════════════════════
# ② prompt 渲染：方向标注 + 按 |IC| 排序 + 语义说明
# ══════════════════════════════════════════════════════════════════════

def test_inverted_factors_are_marked_in_prompt():
    ln = _brief(_ms(_SNAP))
    assert "(反向)" in ln
    # 反向因子标了，正向因子（hv）不标
    assert re.search(r"hv=[-+0-9.]+(?!\(反向\))", ln), ln
    assert "vwap=+0.06153(反向)" in ln.replace("0.06153", "0.06153")


def test_scoring_orders_by_ic_not_by_raw_magnitude():
    """vwap |IC|=0.47 应排在 macd |IC|=0.11 之前（尽管 macd 原始值大 5000 倍）。"""
    ln = _brief(_ms(_SNAP))
    seg = ln.split("4h[", 1)[1].split("]", 1)[0]
    order = [p.strip().split("=")[0] for p in seg.split(",")]
    assert order[0] == "vwap", order
    assert order.index("vwap") < order.index("macd"), order


def test_semantic_note_explains_inverted_direction():
    ln = _semantic_line(_ms(_SNAP))
    assert "反向" in ln and "反着用" in ln


def test_legacy_snapshot_without_meta_still_renders():
    """向后兼容：没有 meta 的旧快照退回按 |原始值| 排序，且不得抛异常。"""
    old = {k: v for k, v in _SNAP.items() if k != "meta"}
    ln = _brief(_ms(old))
    assert "macd" in ln and "(反向)" not in ln
    seg = ln.split("4h[", 1)[1].split("]", 1)[0]
    assert seg.strip().startswith("macd"), seg


def test_snapshot_raw_values_stay_floats():
    """`midlong_helpers` 的 SignalTradeFeedback 记录依赖 4h/1d 的值是 float。"""
    snap = MidLongActiveFactorSet().build_snapshot("BTC")
    for tf in ("4h", "1d"):
        for k, v in (snap.get(tf) or {}).items():
            assert isinstance(v, float), (tf, k, type(v))


# ══════════════════════════════════════════════════════════════════════
# 源码级棘轮
# ══════════════════════════════════════════════════════════════════════

def test_qual_layer_reads_meta():
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))),
        "backend/services/mlto/qual_layer.py")
    src = open(p, encoding="utf-8").read()
    assert 'mf.get("meta")' in src, "渲染端不再读方向语义"
    assert "_inv_labels" in src
    assert "方向语义" in src


def test_injection_gate_is_on():
    from backend.config.settings import MIDLONG_FACTOR_RESEARCH_ENABLED as on
    assert on is True, "MIDLONG_FACTOR_RESEARCH_ENABLED=false 时整条注入链是关的"
