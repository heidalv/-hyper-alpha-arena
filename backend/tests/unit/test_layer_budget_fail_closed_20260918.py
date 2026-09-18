"""P2-9 回归：层预算闸门不得静默 fail-OPEN。

事故背景（轮66 审计 P2-9）
-------------------------
`backend/services/full_auto/proposal_execution.py` 里：

    try:
        _portfolio = host.build_portfolio_for_agents(db, session)
        _equity = float((_portfolio.get("balance") or {}).get("total_equity", 0) or 0)
        _bf = budget_service.scale_factor_for_layer(tier, _equity, _trade_mode, ...)
        if _bf <= 0:
            _mark_block("budget_exhausted", ...)
            return False
        ...
    except Exception:
        pass          # ← 层预算闸门静默 fail-OPEN

`_bf <= 0`（层用量 ≥ `layer_allocations` 的 100%）是本层**唯一**的层额度拦截点 ——
与 `master_execution.py` 不同，这里没有配套的 `can_open()` 硬闸门。所以一旦
`build_portfolio_for_agents()` 或预算服务抛错，层已满也会按原始满仓下单。

修法（轮89）：改为 fail-CLOSED —— 告警 + `_mark_block("budget_error")` + `return False`，
与 `paper_execution.py:483-490` 的统一风控异常同姿态。

同时修掉 `master_execution.py` 里 `_equity > 0 and _req_margin > 0` 不成立时
整段闸门被无声跳过的问题（该处保留不拦截语义，但补 debug 痕迹）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_PROPOSAL = ROOT / "backend" / "services" / "full_auto" / "proposal_execution.py"
_MASTER = ROOT / "backend" / "services" / "full_auto" / "master_execution.py"
_REFERENCE = ROOT / "backend" / "services" / "full_auto" / "paper_execution.py"


def _strip_comments(src: str) -> str:
    out = []
    for line in src.splitlines():
        if line.lstrip().startswith("#"):
            continue
        out.append(re.sub(r"\s+#\s.*$", "", line))
    return "\n".join(out)


def _budget_block(src: str) -> str:
    """截出 proposal_execution 里包含层预算判定的那个 try 块。

    尾部锚点用 `_mtf_mult`（紧随其后的 MTF 缩仓块第一条活代码），
    不用注释行 —— `_strip_comments()` 会把注释删掉。
    """
    live = _strip_comments(src)
    start = live.index("_bf = budget_service.scale_factor_for_layer")
    head = live.rindex("try:", 0, start)
    tail = live.index("_mtf_mult", start)
    return live[head:tail]


# ── proposal_execution：必须 fail-CLOSED ─────────────────────


def test_budget_block_no_longer_swallows_exception():
    body = _budget_block(_PROPOSAL.read_text(encoding="utf-8"))
    assert "except Exception:" not in body, "层预算闸门不得再裸吞异常"
    assert not re.search(r"except Exception[^\n]*:\s*\n\s*pass", body), (
        "不得用 except: pass 静默 fail-OPEN"
    )


def test_budget_block_captures_and_logs_the_error():
    body = _budget_block(_PROPOSAL.read_text(encoding="utf-8"))
    assert re.search(r"except Exception as _bud_err:", body), "异常必须绑定变量以便记录"
    assert "logger.warning" in body, "必须留下 warning 级日志"
    assert "_bud_err" in body.split("logger.warning", 1)[1][:300], "日志里要带出原始错误"


def test_budget_block_returns_false_on_error():
    """fail-CLOSED：异常路径必须 return False，且必须登记拒绝原因。"""
    body = _budget_block(_PROPOSAL.read_text(encoding="utf-8"))
    except_part = body.split("except Exception as _bud_err:", 1)[1]
    assert "return False" in except_part, "异常路径必须拦截（fail-CLOSED）"
    assert '_mark_block("budget_error"' in except_part, "拦截原因要进漏斗审计"
    assert "return False" in except_part.split("_mark_block", 1)[1], (
        "先登记原因再 return False，顺序不能反（否则原因丢失）"
    )


def test_happy_path_block_logic_intact():
    """修复不得改动正常路径：`_bf <= 0` 仍然拦截、`_bf < 1.0` 仍然缩仓。"""
    body = _budget_block(_PROPOSAL.read_text(encoding="utf-8"))
    assert "if _bf <= 0:" in body
    assert '"budget_exhausted"' in body
    assert "if _bf < 1.0:" in body
    assert 'dec["size_multiplier"]' in body


# ── master_execution：不拦截，但必须可见 ─────────────────────


def test_master_budget_skip_is_now_visible():
    live = _strip_comments(_MASTER.read_text(encoding="utf-8"))
    start = live.index("_bf = budget_service.scale_factor_for_layer")
    window = live[max(0, start - 1200): start + 2200]
    assert "logger.debug(" in window, "equity/req_margin 未知导致的跳过必须留痕"
    assert re.search(r"层预算闸门跳过", window), "痕迹要能被人读懂（含原因）"
    assert "if _budget_block:" in window, "拦截路径（预算已满）必须保留"


def test_master_still_blocks_when_budget_full():
    live = _strip_comments(_MASTER.read_text(encoding="utf-8"))
    assert "layer_budget_block" in live
    assert "层预算已满" in live and "层预算不足" in live


# ── 与参照实现的一致性 ───────────────────────────────────────


def test_reference_fail_closed_pattern_still_exists():
    """`paper_execution.py` 的统一风控异常拦截是本次对齐的参照，不能被改掉。"""
    live = _strip_comments(_REFERENCE.read_text(encoding="utf-8"))
    assert "统一风控异常(拦截)" in live
    assert re.search(r"统一风控异常\(拦截\).*\n\s*return False", live)


def test_both_sites_now_use_same_block_reason_helper():
    """master 用 `_emit_block_event`，proposal 用 `_mark_block` —— 两条链路都要登记。"""
    assert "_emit_block_event" in _strip_comments(_MASTER.read_text(encoding="utf-8"))
    assert "_mark_block" in _strip_comments(_PROPOSAL.read_text(encoding="utf-8"))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
