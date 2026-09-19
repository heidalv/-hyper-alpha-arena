# -*- coding: utf-8 -*-
"""轮114 冷却归属纠偏 + 审计无信息事件修复（2026-09-19）。

## 一、撤回我在轮111 加的东西（重复机制）

轮111 我在 `execute_midlong_open` 里加了一道"同币 2h 冷却"
（`MIDLONG_MID_REENTRY_COOLDOWN_SEC`）。轮114 排查审计时发现**它重复了既有机制**：

  `midlong_helpers.try_execute_independent_agent_open` 早已调用
  `reentry_cooldown.reopen_blocked(account, symbol, action, tier)` ——
  24h 拦截榜首（439 次），且比我写的完备：按 tier/account 隔离、**连亏倍率**、
  close_reason 感知（TP 地板 / 亏损 4h / SL 2h）、DB 耐久冷却。

我那道闸还**更靠前** ⇒ 会把既有模块更具体的审计原因挡在外面。
按"复用现有模块"的原则撤回，并把窗口改为调既有配置：
`.env` `TIER_MID_COOLDOWN_SEC=7200`（既有 mid 基准 1800 → 7200）。

## 二、"无信息审计事件"的真根因：09-18 的 WLFI 回环修复

现场（`alpha_analytics.mlto_thesis_events`）：`open_execute_false` 的通用串
`evaluate_and_execute_returned_false` **0 条/天（09-11…09-17）→ 30 条（09-18）→
48 条（09-19）**，24h 窗口 75 条且 `reason_detail` 全空。

根因在 `proposal_execution.evaluate_and_execute_proposal` 的**早退**：
`block_cooldown_active()` 命中时 `return False`，**不发任何 block code**，
而函数尾部那段"登记连续同因拦截"的代码被这个 `return` 直接跳过 ⇒
下游 `record_exec_false_audit()` take 不到原因 → 落通用串。

逐条对回 `data/proposal_block_streaks.json` 的 `cooldowns`，一一对应：
`ASTER:mid size_below_floor count=6` 于 14:03 触发 30 分钟冷却 →
14:10:00 那条"无因"事件正是这次跳过；`:long` 侧的
BNB/SOL/ASTER/UNI/ETH 全部对应 `long_template_source_block` 冷却。

⇒ 这个审计空洞是**我 09-18 修 WLFI 回环时自己引入的**。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _src(rel: str) -> str:
    return open(os.path.join(_ROOT, rel), encoding="utf-8").read()


def _env() -> str:
    return open(os.path.join(_ROOT, ".env"), encoding="utf-8", errors="replace").read()


# ══════════════════════════════════════════════════════════════════════
# ① 撤回重复闸：冷却只能有一个 owner
# ══════════════════════════════════════════════════════════════════════

def test_no_duplicate_cooldown_gate_in_executor():
    src = _src("backend/services/full_auto/midlong_executor.py")
    assert "MIDLONG_MID_REENTRY_COOLDOWN_SEC" not in src, "重复闸仍在（应只保留既有 reentry_cooldown）"
    assert "reentry_cooldown_verdict" not in src
    assert "mid_reentry_wait_ok" not in src
    assert "撤回轮111 的重复闸" in src, "必须留下撤回说明，否则后人会再加一遍"


def test_existing_module_is_the_single_owner():
    src = _src("backend/services/full_auto/midlong_helpers.py")
    assert "reentry_cooldown import reopen_blocked" in src
    assert "midlong_cooldown_block" in src
    rc = _src("backend/services/reentry_cooldown.py")
    # 既有模块的三个能力，正是我那道闸缺的
    assert "_get_loss_multiplier" in rc and "_FLIP_COOLDOWN_SEC" in rc
    assert "REENTRY_SL_COOLDOWN_SEC" in rc and "REENTRY_LOSS_COOLDOWN_SEC" in rc


def test_mid_cooldown_base_is_two_hours():
    """窗口改成既有配置项，而不是新增代码路径。"""
    from backend.services.reentry_cooldown import _get_cooldown_sec
    assert _get_cooldown_sec("mid") == 7200, _get_cooldown_sec("mid")
    assert "TIER_MID_COOLDOWN_SEC=7200" in _env()


def test_longer_paths_keep_their_own_windows():
    """基准调 2h 不得覆盖"亏损 4h / SL 2h"这两个更长档。"""
    rc = _src("backend/services/reentry_cooldown.py")
    assert "REENTRY_SL_COOLDOWN_SEC_MID" in rc
    assert '"14400"' in rc, "亏损档的 4h 默认值必须还在"


def test_settings_documents_the_measurement():
    src = _src("backend/config/settings.py")
    i = src.index("同向冷却基准 30min → **2h**")
    block = src[i: i + 900]
    assert "−0.389%" in block or "-0.389%" in block
    assert "0–2h" in block or "0-2h" in block


# ══════════════════════════════════════════════════════════════════════
# ② 冷却早退必须登记原因（审计空洞的真正根因）
# ══════════════════════════════════════════════════════════════════════

def _prop():
    class _P:
        symbol = "ASTER"
        tier = "mid"
    return _P()


def test_cooldown_early_return_registers_reason():
    from backend.services.full_auto import proposal_execution as PE
    from backend.services.mlto import open_block_reason as OBR

    calls = []

    class _Host:
        block_cooldown_active = staticmethod(
            lambda s, t: {"code": "size_below_floor", "until": time.time() + 1200})
        record_proposal_block = staticmethod(lambda *a: calls.append(a))

    OBR.clear_open_block()
    ok = PE.evaluate_and_execute_proposal(
        db=None, session=None, proposal=_prop(), market_summary={}, host=_Host())

    assert ok is False
    blk = OBR.peek_open_block() or {}
    assert str(blk.get("code") or "").startswith("cooldown:"), blk
    assert "size_below_floor" in str(blk.get("detail") or ""), blk
    assert "冷却" in str(blk.get("detail") or ""), blk
    assert calls == [], "冷却早退绝不能回写连续计数（否则冷却自我续期）"


def test_cooldown_mark_does_not_arm_itself():
    """结构证明：早退 return 在 `record_proposal_block` 登记块**之前**。"""
    src = _src("backend/services/full_auto/proposal_execution.py")
    i = src.index('_mark_block(\n                f"cooldown:')
    j = src.index("_ok_inner = _evaluate_and_execute_proposal_inner(")
    assert i < j, "冷却登记必须在尾部署名前完成，且不得落入 record_proposal_block 分支"
    seg = src[i:j]
    assert seg.count("record_proposal_block") == 0, "冷却分支里不得出现 record_proposal_block"


def test_normal_block_path_still_arms_the_cooldown():
    """行为不变：非冷却路径仍按原样回写连续同因计数。"""
    from backend.services.full_auto import proposal_execution as PE
    from backend.services.mlto import open_block_reason as OBR

    calls = []

    class _Host:
        block_cooldown_active = staticmethod(lambda s, t: None)
        record_proposal_block = staticmethod(lambda s, t, c: calls.append((s, t, c)))

    def _inner(**_kw):
        OBR.mark_open_block("v5gate", detail="极端行情", layer="proposal_execution")
        return False

    _orig = PE._evaluate_and_execute_proposal_inner
    PE._evaluate_and_execute_proposal_inner = _inner
    try:
        OBR.clear_open_block()
        ok = PE.evaluate_and_execute_proposal(
            db=None, session=None, proposal=_prop(), market_summary={}, host=_Host())
    finally:
        PE._evaluate_and_execute_proposal_inner = _orig

    assert ok is False
    assert calls == [("ASTER", "mid", "v5gate")], calls


def test_cooldown_path_would_have_been_generic_without_the_fix():
    """反证：不登记 code 时，下游就是那句无信息通用串（回归护栏）。"""
    from backend.services.mlto import open_block_reason as OBR
    OBR.clear_open_block()
    assert OBR.take_open_block() is None
    src = _src("backend/services/full_auto/midlong_helpers.py")
    assert 'reason = f"eval_false:{_code}" if _code else "evaluate_and_execute_returned_false"' in src


# ══════════════════════════════════════════════════════════════════════
# ③ 副本不再写通用串，也不残留陈旧原因
# ══════════════════════════════════════════════════════════════════════

def test_unregistered_copy_is_explicit_not_stale():
    src = _src("backend/services/full_auto/midlong_helpers.py")
    i = src.index("轮114 2026-09-19 修无信息事件")
    block = src[i: i + 1800]
    assert "_rm_code = \"<未登记>\"" in block
    assert "exec_false_unregistered" in block
    assert "陈旧原因比" in block, "必须写明为何仍要覆盖（否则后人又会改成'干脆不写'）"


def test_remember_semantics_by_code(monkeypatch):
    """行为：有 code → 带 code/detail/layer；无 code → 显式未登记（覆盖旧值）。"""
    import backend.services.full_auto.midlong_helpers as MH
    from backend.services.mlto import open_block_reason as OBR

    calls = []
    monkeypatch.setattr(OBR, "remember_open_block", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(OBR, "take_open_block", lambda: {})

    try:
        MH.record_exec_false_audit(symbol="ASTER", tier="mid", action="buy")
    except Exception:
        pass
    assert calls, "无 code 时也必须覆盖旧副本（否则上层会读到陈旧原因）"
    assert calls[0][0][2] == "<未登记>", calls[0]
    assert calls[0][1]["layer"] == "exec_false_unregistered", calls[0]

    calls.clear()
    monkeypatch.setattr(OBR, "take_open_block",
                        lambda: {"code": "cooldown:size_below_floor", "detail": "剩25分钟",
                                 "layer": "proposal_execution"})
    try:
        MH.record_exec_false_audit(symbol="ASTER", tier="mid", action="buy")
    except Exception:
        pass
    assert calls and calls[0][0][2].startswith("eval_false:cooldown:"), calls
    assert calls[0][1]["detail"] == "剩25分钟"
