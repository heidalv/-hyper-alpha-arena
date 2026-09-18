# -*- coding: utf-8 -*-
"""轮74 P1-8 回归：实盘执行结果被丢弃或伪造 → 失败被记成成功。

## 三处缺陷

### ① `_close_position_live_aware` 从**请求**而非结果合成返回值

    return {"status": ..., "pnl": 0.0, "closed_fully": quantity is None}
                                                  ^^^^^^^^^^^^^^^^^^^^^ 请求说全平就报全平

dict 恒为真，于是调用方：

- `scalp_position_review.py:235  acted["executed"] = bool(res)` → `error` 也记 executed
- `defensive_cycle.py:331  closed_fully = result.get("closed_fully", False)` → 失败也报「全平」

后果：`live_trade` 事件与 `persist_tcp_snapshot(executed=True)` 声称有实盘成交；
冷却/去重闸门与 UI 把仍在交易所的仓位当已平；PnL 归因被污染。

### ② `full_auto_trading_service._execute_live_trade` 漏 `return`

`execute_live_trade` 返回 `bool`，本方法吞掉它 → `:5069` 的
`return bool(self._execute_live_trade(...))` **恒为 False**：
live 会话即使下单成功也被判失败（与 ① 的「无条件报成功」正好相反）。

### ③ `live_trading.execute_live_trade` 的 legacy 分支无条件报成功

`place_ai_driven_order()` 声明 `-> None`，对失败不返回任何东西（该函数与两个下游
共有 ~20 处无值 `return`），本路径**无法确认成交**，旧实现却写
`live_trade: "实盘下单已提交"` 并 `return True`。
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from backend.services.exit.exit_types import CLOSE_OK_STATUSES, close_result_succeeded

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


# ══════════════════════════════════════════════════════════════════════
# 1. 统一判定：失败状态绝不算成功
# ══════════════════════════════════════════════════════════════════════

def test_failure_statuses_are_not_success():
    for bad in ("error", "blocked", "rejected", "failed", "unknown", "cancelled", "timeout"):
        assert close_result_succeeded({"status": bad}) is False, bad


def test_success_statuses_are_success():
    for good in ("filled", "closed", "ok", "success", "partial", "partially_filled"):
        assert close_result_succeeded({"status": good}) is True, good


def test_explicit_success_flag_wins():
    assert close_result_succeeded({"success": True, "status": "error"}) is True
    assert close_result_succeeded({"success": False, "status": "filled"}) is False


def test_legacy_paper_result_without_status_is_success():
    """老 paper 路径不带 status/success，但带回执字段 → 视为成功。"""
    assert close_result_succeeded({"pnl": 1.23, "closed_fully": True}) is True


def test_none_and_empty_are_not_success():
    assert close_result_succeeded(None) is False
    assert close_result_succeeded({}) is False


def test_ok_statuses_do_not_include_error_words():
    for bad in ("error", "blocked", "rejected", "failed", "unknown"):
        assert bad not in CLOSE_OK_STATUSES


# ══════════════════════════════════════════════════════════════════════
# 2. `_close_position_live_aware` 的返回值必须诚实
# ══════════════════════════════════════════════════════════════════════

def _svc_and_session():
    from backend.services.full_auto_trading_service import FullAutoTradingService

    svc = FullAutoTradingService.__new__(FullAutoTradingService)
    svc._is_live_trading_session = lambda s: True          # type: ignore[assignment]

    class _S:
        pass

    return svc, _S()


class _FakeRes:
    def __init__(self, status, pnl=None):
        self.status = status
        self.pnl = pnl


def _patch_live_executor(monkeypatch, status, pnl=None):
    import backend.services.exchange.live_executor as le

    class _LE:
        def close_position(self, *a, **k):
            return _FakeRes(status, pnl)

    monkeypatch.setattr(le, 'LiveExecutor', _LE)


def test_close_error_does_not_report_closed_fully(monkeypatch):
    svc, sess = _svc_and_session()
    _patch_live_executor(monkeypatch, "error")
    out = svc._close_position_live_aware(object(), sess, 14, "BNB", "long", reason="t")
    assert out["success"] is False
    assert out["closed_fully"] is False, '请求全平 ≠ 已全平（旧实现此处为 True）'
    assert out["status"] == "error"
    assert close_result_succeeded(out) is False


def test_close_filled_reports_success(monkeypatch):
    svc, sess = _svc_and_session()
    _patch_live_executor(monkeypatch, "filled", pnl=3.5)
    out = svc._close_position_live_aware(object(), sess, 14, "BNB", "long", reason="t")
    assert out["success"] is True
    assert out["closed_fully"] is True
    assert out["pnl"] == 3.5, '不应硬编码 0.0（旧实现如此）'


def test_partial_reduce_never_reports_closed_fully(monkeypatch):
    svc, sess = _svc_and_session()
    _patch_live_executor(monkeypatch, "filled")
    out = svc._close_position_live_aware(object(), sess, 14, "BNB", "long",
                                         reason="t", quantity=0.5)
    assert out["success"] is True
    assert out["closed_fully"] is False


def test_blocked_status_is_not_success(monkeypatch):
    svc, sess = _svc_and_session()
    _patch_live_executor(monkeypatch, "blocked")
    out = svc._close_position_live_aware(object(), sess, 14, "BNB", "long", reason="t")
    assert close_result_succeeded(out) is False


# ══════════════════════════════════════════════════════════════════════
# 3. `_execute_live_trade` 必须把 bool 传出去
# ══════════════════════════════════════════════════════════════════════

def test_execute_live_trade_returns_result():
    """源码级：必须 `return execute_live_trade(...)`，不能吞掉返回值。"""
    import re
    src = io.open(os.path.join(_ROOT, 'backend/services/full_auto_trading_service.py'), encoding='utf-8').read()
    m = re.search(r'def _execute_live_trade\(.*?(?=\n    def )', src, re.S)
    assert m, '找不到 _execute_live_trade'
    body = '\n'.join(l for l in m.group(0).splitlines() if not l.lstrip().startswith('#'))
    assert re.search(r'return\s+execute_live_trade\(', body), \
        '_execute_live_trade 必须 return execute_live_trade(...)（否则调用方 bool() 恒 False）'


def test_caller_uses_the_bool():
    """调用点确实依赖该 bool（说明漏 return 不是无害的）。"""
    src = io.open(os.path.join(_ROOT, 'backend/services/full_auto_trading_service.py'), encoding='utf-8').read()
    assert 'bool(self._execute_live_trade(' in src


# ══════════════════════════════════════════════════════════════════════
# 4. legacy 路由不得谎报成功
# ══════════════════════════════════════════════════════════════════════

def test_legacy_route_does_not_claim_success():
    src = io.open(os.path.join(_ROOT, 'backend/services/full_auto/live_trading.py'), encoding='utf-8').read()
    assert 'place_ai_driven_order(' in src
    # 旧文案（声称成功）不得再出现
    assert '"live_trade",\n            f"实盘下单已提交' not in src
    assert '实盘下单已提交: {symbol} {operation}")\n        return True' not in src
    # 新文案明确「未确认成交」
    i = src.find('live_trade_routed')
    assert i > 0, 'legacy 路由应改用 live_trade_routed（不声称成交）'


def test_legacy_route_returns_none_not_true():
    """legacy 路径无法确认成交 → 返回 None（未知），不得返回 True。"""
    import re
    src = io.open(os.path.join(_ROOT, 'backend/services/full_auto/live_trading.py'), encoding='utf-8').read()
    i = src.find('live_trade_routed')
    assert i > 0
    tail = src[i:i + 700]
    assert 'return None' in tail, 'legacy 分支应 return None'
    assert 'return True' not in tail, 'legacy 分支不得 return True'


# ══════════════════════════════════════════════════════════════════════
# 5. 调用方必须按结果判定（不得用真值判断 dict）
# ══════════════════════════════════════════════════════════════════════

def test_scalp_review_uses_helper():
    import re as _re
    src = io.open(os.path.join(_ROOT, 'backend/services/full_auto/scalp_position_review.py'), encoding='utf-8').read()
    code = _re.sub(r'#.*', '', src)          # 去掉注释（修复说明会引用旧写法）
    assert 'close_result_succeeded' in code
    assert 'bool(res)' not in code, '不得再用 bool(res)（dict 恒为真）'


def test_defensive_cycle_uses_helper():
    src = io.open(os.path.join(_ROOT, 'backend/services/full_auto/defensive_cycle.py'), encoding='utf-8').read()
    assert 'close_result_succeeded' in src
