# -*- coding: utf-8 -*-
"""[F390 / 解冻选项E 2026-09-18] `open_execute_false` 补 `reason`：让"为什么没开成"可查。

## 背景（取证报告 `docs/中线开仓冻结根因取证_20260918.md` §6.2）

`mlto_thesis_events` 里近 48h 有 **321 条 `open_execute_false`**，而它的 payload 只有
`{symbol, tier, direction, action}` —— **没有原因字段**；其中 **160 条是 mid 做多**。
对照 `open_blocked`（有 `reason`）可知这不是设计如此，而是**接线缺口**：
执行链各层用 `open_block_reason.mark_open_block()` 登记了原因，但**没人把它写进这个事件**。

## 实现时踩到的真坑（本文件第 3 条用例专门钉它）

`midlong_helpers.record_exec_false_audit()`（就在 `try_execute_independent_agent_open` 内部、
即 brain 调用的那个函数里）已经用 **`take_open_block()`** 把 ContextVar 版**取走并清空**
（那是它 2026-09-10 为审计 JSONL 设计的正确语义）。
⇒ 若 brain 在 `execute_midlong_open()` 返回后只 `peek`，**永远拿到 None**。
故新增一条**按 (symbol, tier) 留存**的副本通道（`remember_/last_/clear_last_open_block`），
**既不改 take 语义**（既有消费者一字未动），又能被上层读到。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.mlto import open_block_reason as OBR  # noqa: E402


def setup_function(_fn):
    OBR.clear_open_block()
    OBR.clear_last_open_block()


# ─────────────────────── 1. 留存通道的基本语义 ───────────────────────

def test_remember_and_last_roundtrip():
    OBR.remember_open_block("XRP", "mid", "midlong_cooldown_block:刚平多仓未满120分钟",
                            detail="约剩 111 分钟", layer="midlong_helpers")
    got = OBR.last_open_block("xrp", "MID")          # 大小写不敏感
    assert got and got["code"].startswith("midlong_cooldown_block")
    assert got["detail"] == "约剩 111 分钟"
    assert got["layer"] == "midlong_helpers"
    assert OBR.last_open_block("BTC", "mid") is None  # 按 symbol 隔离


def test_last_is_overwritten_and_clearable():
    OBR.remember_open_block("ASTER", "mid", "location_gate_veto:…70% 硬否决")
    OBR.remember_open_block("ASTER", "mid", "regime_extreme")
    assert OBR.last_open_block("ASTER", "mid")["code"] == "regime_extreme", "后写必须覆盖先写"
    OBR.clear_last_open_block("ASTER", "mid")
    assert OBR.last_open_block("ASTER", "mid") is None
    OBR.remember_open_block("A", "mid", "x")
    OBR.remember_open_block("B", "mid", "y")
    OBR.clear_last_open_block()
    assert OBR.last_open_block("A", "mid") is None and OBR.last_open_block("B", "mid") is None


# ────────── 2. 本文件存在的理由：take 之后留存副本仍在 ──────────

def test_last_survives_take_which_is_the_whole_point():
    """模拟真实调用链：内层登记 → 内层 take（清空 ContextVar）→ 上层仍要能读到原因。

    这正是 E 第一版会失效的原因（brain 只能 peek，而 peek 已被 take 清空）。
    """
    OBR.remember_open_block("XRP", "mid", "midlong_cooldown_block:…",
                            layer="midlong_helpers")
    OBR.mark_open_block("midlong_cooldown_block:…", layer="midlong_helpers")

    taken = OBR.take_open_block()                    # ← midlong_helpers 末尾的既有消费
    assert taken and taken["code"].startswith("midlong_cooldown_block")
    assert OBR.peek_open_block() is None, "take 必须清空 ContextVar（既有语义不得破坏）"
    assert OBR.last_open_block("XRP", "mid") is not None, (
        "留存副本必须在 take 之后仍可读 —— 否则 open_execute_false 仍然无线索")


# ─────────────────────── 3. 三个接入点都在位（源码级） ───────────────────────

def test_executor_and_helpers_remember_the_reason():
    ex = (ROOT / "backend" / "services" / "full_auto"
          / "midlong_executor.py").read_text(encoding="utf-8")
    hlp = (ROOT / "backend" / "services" / "full_auto"
           / "midlong_helpers.py").read_text(encoding="utf-8")
    assert "remember_open_block(sym_u" in ex, "_record_fail 未登记原因（fuse 层）"
    assert "remember_open_block(_sym_u" in hlp, "_audit_skip 未登记原因（内部 7 道闸）"
    # 内部闸统一出口：确认登记发生在 _audit_skip 内（一处覆盖全部 return False 点）
    i = hlp.index("def _audit_skip(")
    seg = hlp[i:i + 1400]
    assert "remember_open_block" in seg and "mark_open_block" in seg


def test_brain_payload_carries_reason_without_guessing():
    src = (ROOT / "backend" / "services" / "mlto" / "brain.py").read_text(encoding="utf-8")
    # 注意：必须定位**调用点**而不是函数定义（`def _emit_open_execute_false(` 也在文件里，
    # 我第一版就踩了这个坑 —— 断言打在了函数签名上，测的不是 payload）。
    idxs = [k for k in range(len(src)) if src.startswith("_emit_open_execute_false(", k)]
    call = None
    for k in idxs:
        seg = src[k:k + 700]
        if "thesis.thesis_id" in seg and '"symbol"' in seg:
            call = k
    assert call is not None, "未找到 open_execute_false 的调用点（结构变了⇒复核选项E）"
    # 取值代码就在 emit **之前**若干行，故窗口要往前留足（我第一版只往后看，又漏了）
    seg = src[max(0, call - 1400):call + 900]
    assert '"reason"' in seg, "open_execute_false payload 仍未带 reason"
    assert "last_open_block" in seg, "应优先读按 (symbol,tier) 留存的副本"
    assert "<未登记>" in seg, "无登记时必须写显式占位，绝不猜测原因"
    # 尝试前必须清掉本 (symbol,tier) 的旧值，避免读到上一次决策的原因
    j = src.index("clear_last_open_block as _clr_last")
    assert "_clr_last(" in src[j:j + 400]


def test_existing_take_semantics_unchanged():
    """选项E 不得改变 2026-09-10 定的 take 语义（既有审计消费者依赖它）。"""
    src = (ROOT / "backend" / "services" / "mlto" / "open_block_reason.py").read_text(
        encoding="utf-8")
    i = src.index("def take_open_block")
    seg = src[i:i + 300]
    assert "_BLOCK.set(None)" in seg, "take 必须仍然取走即清空"
    hlp = (ROOT / "backend" / "services" / "full_auto"
           / "midlong_helpers.py").read_text(encoding="utf-8")
    assert "take_open_block" in hlp, "既有消费点不得被移除"
