# -*- coding: utf-8 -*-
"""轮120：用户拍板的两件事（2026-09-19）。

> 用户：「马上恢复，允许小仓位」

## 一、恢复"单币连亏冻结"（只冻那个币）

`record_midlong_outcome()` 里原有一句
「[2026-09-11 用户指令] 模拟账户直接返回：不做任何亏损触发的冷却/熔断记账」，
后果实测：`data/midlong_circuit_state.json` **mtime 停在 09-10 20:48（9 天没写）**，
今天 UNI 连吃 3 个 SL（−13.43）却没有任何"单币 12h 冷却"。
⇒ 恢复记账（`MIDLONG_CIRCUIT_PAPER_LOCK` 默认 true，false = 回滚）。
**粒度不变**：按 (account, symbol) 记账与禁开，不引入任何全局冻结。

## 二、允许小仓位试探（`MIDLONG_NEUTRAL_PROBE_ENABLED` 默认 true）

现场（`reports/_probe119.txt`）：9 个固定币全部 `accepted=0`（neutral）或
`recommend_open=0`（"等回踩"）⇒ 扫描候选=0 ⇒ 中线整天一单不开。用户口径：
中线应当能开、除非极端反转。两条小仓路径：

* 方向明确但 `recommend_open=false`（模型在等回踩）⇒ 用原方向、NIBBLE 档（0.15）试探；
* `direction=neutral` 且 `regime=up/down` ⇒ 用 regime 定向后小仓试探；**震荡不猜方向**。

尺寸由分档系数保证是"小仓"（轮117：`recommend_open=False` ⇒ tranche_gate 走 NIBBLE 档），
且照旧过 V5/预算/组合/宪法全部闸门；每步写审计 `probe_entry:<原因>`。
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _src(rel):
    return io.open(os.path.join(_ROOT, rel), encoding="utf-8").read()


# ══════════════════════════════════════════════════════════════════════
# 一、单币连亏冻结恢复
# ══════════════════════════════════════════════════════════════════════

def test_paper_loss_recording_restored():
    src = _src("backend/services/full_auto/midlong_circuit_gate.py")
    i = src.index("轮120 2026-09-19 **用户拍板恢复**")
    seg = src[i:i + 1500]
    assert "_env_b(\"MIDLONG_CIRCUIT_PAPER_LOCK\", True)" in seg, "恢复 + 可回滚"
    assert "不引入任何全局冻结" in seg, "粒度声明必须在场"
    # 顶层不得再无条件的 paper 早退
    assert "if loss_locks_disabled(account_id):\n            return\n        _load()" not in src


def test_per_symbol_freeze_actually_fires(tmp_path, monkeypatch):
    """行为：连亏 3 笔 ⇒ 该 (account,symbol) 被禁 12h，且**其他币不受影响**。"""
    import importlib
    mod = importlib.import_module("backend.services.full_auto.midlong_circuit_gate")
    monkeypatch.setattr(mod, "_STATE_FILE", str(tmp_path / "state.json"), raising=False)
    monkeypatch.setattr(mod, "_state", {}, raising=False)
    monkeypatch.setattr(mod, "CONSEC_LOSSES_LIMIT", 3)
    monkeypatch.setattr(mod, "_env_b", lambda *a, **k: True)
    monkeypatch.setattr(mod, "_save", lambda: None)
    monkeypatch.setattr(mod, "_load", lambda: None)

    for _ in range(3):
        mod.record_midlong_outcome(14, "UNI", -4.0)
    st = mod._state.get("14:UNI") or {}
    assert int(st.get("consec_losses") or 0) >= 3, st
    assert float(st.get("banned_until") or 0) > 0, "第 3 笔连亏必须装配单币冷却"
    assert "14:BNB" not in mod._state, "不得波及其他币（用户口径：只冻亏钱的那个）"


def test_flag_registered():
    from backend.config.env_registry import KNOWN_FLAGS
    assert "MIDLONG_CIRCUIT_PAPER_LOCK" in KNOWN_FLAGS
    assert "MIDLONG_NEUTRAL_PROBE_ENABLED" in KNOWN_FLAGS


# ══════════════════════════════════════════════════════════════════════
# 二、允许小仓位试探
# ══════════════════════════════════════════════════════════════════════

def test_probe_path_exists_with_evidence():
    src = _src("backend/services/mlto/brain.py")
    i = src.index("轮120 2026-09-19 用户拍板「允许小仓位」")
    seg = src[i:i + 3000]
    assert "probe_entry:" in seg and "waiting_pullback" in seg
    assert "neutral_regime_" in seg, "neutral 必须用 regime 定向"
    assert "dto.recommend_open = False" in seg, "试探必须走 NIBBLE（小仓）档"


def test_probe_behaviour(monkeypatch):
    """等回踩（方向明确）⇒ 进候选；neutral+regime=up ⇒ 定向为 long；neutral+震荡 ⇒ 不进。"""
    from backend.services.mlto import brain as B

    calls = []

    class _Dto:
        def __init__(self, d):
            self.accepted = 0
            self.recommend_open = False
            self.direction = d
            self.tranche_stage = 0
            self.llm_conviction = 45

    _theses = {"ASTER": _Dto("long"), "BNB": _Dto("neutral"), "UNI": _Dto("neutral")}

    class _Store:
        @staticmethod
        def get(sid, sym, tier):
            return _theses.get(sym)

    monkeypatch.setattr(B, "thesis_is_fresh", lambda dto: True)
    monkeypatch.setattr(B, "maybe_open",
                        lambda **k: calls.append((k["symbol"], k["thesis"].direction,
                                                  k["thesis"].recommend_open)) or True)
    monkeypatch.setitem(sys.modules, "backend.services.mlto.thesis_store", _Store)
    monkeypatch.setattr(
        "backend.services.full_auto.midlong_position_manager.has_open_position_of_nature",
        lambda db, acct, sym, tier: False)

    class _Host:
        @staticmethod
        def resolve_independent_strategy(db, session, sym, tier):
            return object()

    class _Session:
        session_id = "fa_test"
        paper_account_id = 14

    ms = {"BNB": {"regime": "up"}, "UNI": {"regime": "ranging"}}
    B.run_midlong_open_sweep(host=_Host(), session=_Session(),
                             symbols=["ASTER", "BNB", "UNI"], tier="mid",
                             market_summary=ms)
    syms = [c[0] for c in calls]
    assert "ASTER" in syms, "等回踩（方向明确）必须进试探"
    assert "BNB" in syms, "neutral + regime=up ⇒ 定向 long 试探"
    assert "UNI" not in syms, "neutral + 震荡 ⇒ 不猜方向"
    assert all(c[2] is False for c in calls), "试探一律 recommend_open=False（NIBBLE 小仓）"
    assert dict((c[0], c[1]) for c in calls)["BNB"] == "long"
