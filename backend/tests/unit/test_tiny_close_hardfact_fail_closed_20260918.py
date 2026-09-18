"""P2-10 回归：微仓「等效全平」硬事实闸门不得静默 fail-OPEN。

事故背景（轮66 审计 P2-10）
-------------------------
`backend/services/full_auto/paper_risk_helpers.py` 的
`tiny_close_allowed_by_hardfact()` 末行原为：

    except Exception as _e:
        return True, f"tiny-close guard error: {_e}"      # ← 异常即放行

两个问题：
1. **fail-OPEN**：该闸门的存在意义是「别把微仓小亏秒平」
   （`master_running_close_tiny` 小亏秒平是历史事故），复核最短持有、
   `MASTER_CLOSE_TINY_DISABLED_TIERS`、`master_close` 硬阈值。异常时放行
   = 保护网在需要它的时候自动消失。
2. **静默**：调用方只在**拒绝**分支打印 detail
   （`master_execution.py:2729-2730` / `:2789-2790`），放行时那句
   `tiny-close guard error: …` 根本没人打印。

修法（轮90）：改为 `return False, ...`（不放行 ⇒ 持有 ⇒ 交 SL/TP），
与正常拒绝分支走同一条路径；附带好处是错误自动出现在 `close_tiny_hold` 事件里。

方向性论证（为什么这里 fail-closed 是安全的）：平掉一个微仓**不可能**降低清算风险，
所以「该平不平」的反向风险由 SL（独立安全网）承担；而「不该平却平了」是纯粹的手续费
磨损 + 违反既定的分层平仓纪律。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_HELPERS = ROOT / "backend" / "services" / "full_auto" / "paper_risk_helpers.py"
_MASTER = ROOT / "backend" / "services" / "full_auto" / "master_execution.py"


def _strip_comments(src: str) -> str:
    out = []
    for line in src.splitlines():
        if line.lstrip().startswith("#"):
            continue
        out.append(re.sub(r"\s+#\s.*$", "", line))
    return "\n".join(out)


def _except_body(src: str) -> str:
    """取 `tiny_close_allowed_by_hardfact` 里 `except Exception as _e:` 之后的部分。"""
    live = _strip_comments(src)
    anchor = live.index("def tiny_close_allowed_by_hardfact")
    tail = live[anchor:]
    return tail.split("except Exception as _e:", 1)[1]


# ── 行为：异常必须拒绝 ────────────────────────────────────────


def test_guard_error_denies_close(monkeypatch):
    """`master_close_guard` 导入失败时，闸门必须拒绝（不放行），且原因可读。"""
    import builtins

    real_import = builtins.__import__

    def _boom(name, *a, **k):
        if name == "backend.services.master_close_guard":
            raise RuntimeError("模拟硬事实复核不可用")
        return real_import(name, *a, **k)

    from backend.services.full_auto import paper_risk_helpers as prh

    monkeypatch.setattr(builtins, "__import__", _boom)
    ok, detail = prh.tiny_close_allowed_by_hardfact(
        14, {"timeframe_tier": "short", "margin": 5.0, "unrealized_pnl": -0.4},
        "test",
    )
    assert ok is False, "复核异常必须拦截（fail-CLOSED）"
    assert "guard error" in detail
    assert "拦截" in detail, "原因文案要让人一眼看出这是「没算出来所以不放行」"


def test_guard_error_is_logged(monkeypatch, caplog):
    """异常必须留 warning 级日志 —— 调用方只在拒绝分支打印 detail，不能只靠它。"""
    import builtins
    import logging

    real_import = builtins.__import__

    def _boom(name, *a, **k):
        if name == "backend.services.master_close_guard":
            raise RuntimeError("模拟硬事实复核不可用")
        return real_import(name, *a, **k)

    from backend.services.full_auto import paper_risk_helpers as prh

    monkeypatch.setattr(builtins, "__import__", _boom)
    with caplog.at_level(logging.WARNING, logger="backend.services.full_auto.paper_risk_helpers"):
        prh.tiny_close_allowed_by_hardfact(14, {"timeframe_tier": "short"}, "test")
    assert any("微仓硬事实复核异常" in r.message for r in caplog.records), (
        "没有留下 warning 日志"
    )


# ── 源码级守卫 ────────────────────────────────────────────────


def test_no_fail_open_return_true_in_except():
    body = _except_body(_HELPERS.read_text(encoding="utf-8"))
    assert not re.search(r"return True", body), "异常分支不得再返回 True（放行）"
    assert re.search(r"return False", body), "异常分支必须返回 False（拦截）"


def test_exception_branch_keeps_error_text():
    """拦截文案里要带上原始错误，否则排障时只知道"被拦了"。"""
    body = _except_body(_HELPERS.read_text(encoding="utf-8"))
    assert "_e" in body.split("return", 1)[1], "detail 里必须包含原始异常"


def test_caller_prints_detail_only_on_reject():
    """前提复核：调用方只在拒绝分支打印 detail —— 这正是「必须返回 False」的原因。

    如果哪天有人把 detail 改成无条件打印，本测试会失败并提醒重新评估该结论。
    """
    live = _strip_comments(_MASTER.read_text(encoding="utf-8"))
    assert re.search(r"if not _tc_ok:", live)
    assert re.search(r"if not _tc_ok2:", live)
    # 两处 detail 都必须出现在对应调用点**之后**（即只在该调用返回后才被使用）
    first_call = live.index("tiny_close_allowed_by_hardfact")
    for detail, call_anchor in (("_tc_detail", "if not _tc_ok:"),
                                ("_tc_detail2", "if not _tc_ok2:")):
        idx_call = live.index(call_anchor, first_call)
        idx_detail = live.index(detail, idx_call)
        assert idx_detail > idx_call, f"{detail} 必须在拒绝分支内使用"
    # 且 detail 的使用必须发生在 append_event 的调用参数里（真的被打印）
    assert re.search(r"append_event\([^)]*\{_tc_detail", live, re.S) or (
        "_tc_detail" in live.split("close_tiny_hold", 2)[1]
    ), "detail 必须进入 close_tiny_hold 事件文案"


def test_production_wiring_passes_the_real_service():
    """Host 的 lambda 默认值（放行）在生产不可达：:156 必须接线真实服务。"""
    live = _strip_comments(_MASTER.read_text(encoding="utf-8"))
    assert "tiny_close_allowed_by_hardfact=svc._tiny_close_allowed_by_hardfact" in live


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
