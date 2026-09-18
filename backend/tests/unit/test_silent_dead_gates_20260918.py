# -*- coding: utf-8 -*-
"""轮67 P1 回归：三道「静默死掉」的门控。

三处都是**同一个失败模式**：一个必然抛出的错误被 `logger.debug`/`warning` 吞掉，
于是功能看起来在跑、实际从未生效，日志里也没有任何 error 级痕迹。

1. `health_check_cycle.py:695` —— 模块级函数里 `getattr(self, ...)` → NameError
   → `STRICT_DATA_GATE` 从未生效（策略会在数据未就绪的标的上被创建）
2. `paper_execution.py:161` —— 模块级函数里 `hasattr(self, ...)` → NameError
   → 「同模板 5 分钟内最多 2 个标的」限流从未生效（单一信号源可填满整个 tier 预算）
3. `exit_agent.py:113` —— `close_position` 缺必填位置参数 → TypeError
   → `EXIT_AGENT_EXECUTE=true` 承诺的时间止损执行静默无效

本测试用**源码级守卫**（1、2）与**签名绑定**（3）锁死，避免回归。
"""
import inspect
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _func_body(rel_path: str, func_name: str) -> str:
    src = io.open(os.path.join(_ROOT, rel_path), encoding='utf-8').read()
    m = re.search(rf'^def {re.escape(func_name)}\(.*?(?=^def |^class |\Z)', src, re.S | re.M)
    assert m, f'{rel_path}: 找不到模块级函数 {func_name}'
    body = m.group(0)
    # 去掉注释行：修复说明里会引用旧代码（如 `hasattr(self, ...)`），
    # 那是说明不是代码，不该触发守卫。
    return '\n'.join(
        ln for ln in body.splitlines() if not ln.lstrip().startswith('#')
    )


# ══════════════════════════════════════════════════════════════════════
# 1 + 2. 模块级函数体内不得出现裸 self
# ══════════════════════════════════════════════════════════════════════

def test_run_health_check_has_no_bare_self():
    """`run_health_check` 是模块级函数（无 self）—— 体内出现裸 self 必然 NameError。"""
    body = _func_body('backend/services/full_auto/health_check_cycle.py', 'run_health_check')
    assert not re.search(r'(?<![\w.])self\.', body), \
        'run_health_check 体内出现裸 self → 该处必然抛 NameError 并被 debug 吞掉'


def test_execute_paper_trade_inner_has_no_bare_self():
    body = _func_body('backend/services/full_auto/paper_execution.py', '_execute_paper_trade_inner')
    assert not re.search(r'(?<![\w.])self\.', body), \
        '_execute_paper_trade_inner 体内出现裸 self → 同模板齐发限流必然失效'


def test_strict_data_gate_reads_snapshot_from_host():
    """STRICT_DATA_GATE 的快照必须取自 host（本模块既有口径）。"""
    body = _func_body('backend/services/full_auto/health_check_cycle.py', 'run_health_check')
    assert 'last_unified_snapshot' in body
    assert re.search(r'getattr\(host,\s*["\']last_unified_snapshot["\']', body), \
        'STRICT_DATA_GATE 应通过 host.last_unified_snapshot 取快照'


def test_template_burst_uses_host_window():
    """同模板齐发限流必须使用 Host 契约提供的 template_recent_opens。"""
    body = _func_body('backend/services/full_auto/paper_execution.py', '_execute_paper_trade_inner')
    assert 'host.template_recent_opens' in body
    assert 'hasattr(self' not in body


# ══════════════════════════════════════════════════════════════════════
# 3. ExitAgent 的调用必须能满足 close_position 的签名
# ══════════════════════════════════════════════════════════════════════

def test_close_position_required_params():
    """记录签名契约：这些参数缺一个都会在碰 DB 之前 TypeError。"""
    from backend.services.paper_trading_engine import PaperTradingEngine
    sig = inspect.signature(PaperTradingEngine.close_position)
    params = list(sig.parameters.items())[1:]      # 去掉 self
    required = [n for n, p in params
                if p.default is inspect.Parameter.empty
                and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    assert required == ['db', 'account_id', 'symbol', 'side'], required


def test_exit_agent_passes_all_required_params():
    """源码级：ExitAgent 调用 close_position 时必须提供全部必填位置参数。"""
    body = _func_body('backend/services/full_auto/exit_agent.py', 'run_exit_pass')
    m = re.search(r'close_position\((.*?)\n\s*\)', body, re.S)
    assert m, '找不到 close_position 调用'
    call = m.group(1)
    for name in ('db=', 'account_id=', 'symbol=', 'side='):
        assert name in call, f'ExitAgent 调用缺 {name}（会 TypeError 并被吞成 warning）'


def test_time_stop_advice_carries_execution_keys():
    """advice 必须带 account_id / trade_nature，否则执行侧无法定位仓位。"""
    body = _func_body('backend/services/full_auto/exit_agent.py', 'run_exit_pass')
    assert '"account_id"' in body or "'account_id'" in body
    assert '"trade_nature"' in body or "'trade_nature'" in body


def test_exit_agent_executes_with_recorded_advice():
    """端到端：按 advice 形态构造入参，验证签名可绑定（不再 TypeError）。

    用 stub 替掉 paper_engine，只验「调用契约」这一件事。
    """
    from backend.services.full_auto import exit_agent

    captured = {}

    class _StubEngine:
        def close_position(self, **kw):
            captured.update(kw)
            return {"status": "closed"}

    real_engine = None
    try:
        import backend.services.paper_trading_engine as pte
        real_engine = pte.paper_engine
        pte.paper_engine = _StubEngine()
        os.environ['EXIT_AGENT_EXECUTE'] = 'true'
        from datetime import datetime, timedelta
        # 关键：opened_at 必须是 datetime —— 实现里走 `opened_at.timestamp()`，
        # 传 float 会被 except 吞成 None → 不产生 advice（这是实现既有语义，非本次改动）
        old_open = datetime.now() - timedelta(hours=999)
        positions = [{
            "id": 1234, "account_id": 14, "symbol": "BNB", "side": "long",
            "tier": "long", "trade_nature": "trend_follow",
            "entry_price": 700.0, "mark_price": 710.0, "margin": 100.0,
            "opened_at": old_open, "status": "open",
        }]
        out = exit_agent.run_exit_pass(db=object(), positions=positions, market_summary={})
        assert out["executed"] == 1, f'应执行 1 笔，实际 {out["executed"]}（advice={out["advice"]}）'
        assert captured.get("account_id") == 14
        assert captured.get("symbol") == "BNB"
        assert captured.get("side") == "long"
        assert captured.get("position_id") == 1234
        assert captured.get("trade_nature") == "trend_follow"
    finally:
        os.environ.pop('EXIT_AGENT_EXECUTE', None)
        if real_engine is not None:
            import backend.services.paper_trading_engine as pte
            pte.paper_engine = real_engine
