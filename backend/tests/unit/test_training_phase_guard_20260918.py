# -*- coding: utf-8 -*-
"""轮79 P1 回归：训练期「非目标币禁开」在独立调度器配置下**完全失效**。

## 事故链

`host.training_allowed_symbols` 只有一条写入路径：

    loops/trading_cycle_loop.py:24   _apply_training_phase_tick_constraints → self._training_allowed_symbols = allowed
    loops/trading_cycle_loop.py:76   只在 run_trading_cycle() 内被调用

而 `_run_trading_cycle` 在 `.env` 的当前配置下**永不执行**：

    coordinator_loop.py:101  _ai = [t for t in due_tiers if t == "short"] if MIDLONG_AGENT_INDEPENDENT_SCHEDULER
                             #  .env: MIDLONG_AGENT_INDEPENDENT_SCHEDULER=true → 只剩 short
    coordinator_loop.py:103  if SCALP_OPEN_DISABLED: _ai = [t for t in _ai if t != "short"]
                             #  .env: SCALP_OPEN_DISABLED=true → 清空
    coordinator_loop.py:104  if _ai:  → 恒假 → _run_trading_cycle 不被调用

于是该字段**永远是空集**，而守卫写的是：

    if _train_allowed and sym.upper() not in _train_allowed:
        block

**空集 = 不限制** → 训练期（`data/training_phase.json`: active=true,
symbols=[BTC,ETH,SOL,BNB,ASTER]）master 车道可开任意标的。

## 修复

不再只依赖 host 字段：`_resolve_training_allowed_symbols()` 直接从
`training_phase_service` 取（其 `load_state` 自带 5s 缓存，每 tick 开销可忽略），
host 字段作为优先来源保留；两者都为空才视为「不限制」。
在 `execute_master_decisions` 入口解析一次并缓存到局部。
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from backend.services.full_auto.master_execution import _resolve_training_allowed_symbols

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_SRC = os.path.join(_ROOT, 'backend/services/full_auto/master_execution.py')


class _Host:
    pass


def _code(path):
    src = io.open(path, encoding='utf-8').read()
    return '\n'.join(l for l in src.splitlines() if not l.lstrip().startswith('#'))


# ══════════════════════════════════════════════════════════════════════
# 1. host 字段为空时仍能解析出真实集合（核心修复）
# ══════════════════════════════════════════════════════════════════════

def test_resolves_from_service_when_host_field_empty(monkeypatch):
    import backend.services.training_phase_service as tps
    monkeypatch.setattr(tps, 'is_active', lambda: True)
    monkeypatch.setattr(tps, 'target_symbols', lambda: ['BTC', 'ETH', 'SOL'])
    got = _resolve_training_allowed_symbols(_Host())
    assert got == {'BTC', 'ETH', 'SOL'}, got


def test_host_field_takes_priority(monkeypatch):
    import backend.services.training_phase_service as tps
    monkeypatch.setattr(tps, 'is_active', lambda: True)
    monkeypatch.setattr(tps, 'target_symbols', lambda: ['BTC'])
    h = _Host()
    h.training_allowed_symbols = {'DOGE'}
    assert _resolve_training_allowed_symbols(h) == {'DOGE'}


def test_symbols_are_upper_cased(monkeypatch):
    import backend.services.training_phase_service as tps
    monkeypatch.setattr(tps, 'is_active', lambda: True)
    monkeypatch.setattr(tps, 'target_symbols', lambda: ['btc', 'Eth'])
    assert _resolve_training_allowed_symbols(_Host()) == {'BTC', 'ETH'}


# ══════════════════════════════════════════════════════════════════════
# 2. 训练期未启用 → 空集（= 不限制）—— 修复不得变成「永远全禁」
# ══════════════════════════════════════════════════════════════════════

def test_inactive_training_phase_means_no_restriction(monkeypatch):
    import backend.services.training_phase_service as tps
    monkeypatch.setattr(tps, 'is_active', lambda: False)
    monkeypatch.setattr(tps, 'target_symbols', lambda: ['BTC'])
    assert _resolve_training_allowed_symbols(_Host()) == set()


def test_service_error_degrades_to_no_restriction(monkeypatch):
    """服务异常 → 不限制（fail-open），且不得抛出。"""
    import backend.services.training_phase_service as tps

    def _boom():
        raise RuntimeError('state file unreadable')

    monkeypatch.setattr(tps, 'is_active', _boom)
    assert _resolve_training_allowed_symbols(_Host()) == set()


# ══════════════════════════════════════════════════════════════════════
# 3. 真实环境：训练期确实处于 active → 必须解析出非空集合
# ══════════════════════════════════════════════════════════════════════

def test_real_training_phase_state_is_resolved():
    """本项目 `data/training_phase.json` 为 active=true —— 必须解析出非空限制。"""
    from backend.services.training_phase_service import is_active, target_symbols
    if not is_active():
        import pytest
        pytest.skip('当前训练期未启用')
    got = _resolve_training_allowed_symbols(_Host())
    assert got, '训练期 active 却解析出空集 → 限制仍会失效'
    assert got == {str(s).upper() for s in target_symbols()}


# ══════════════════════════════════════════════════════════════════════
# 4. 源码级守卫：调用点必须用解析结果，不能再直接 getattr(host, ...)
# ══════════════════════════════════════════════════════════════════════

def test_guard_uses_resolved_cache_not_host_field():
    code = _code(_SRC)
    assert '_training_allowed_cache = _resolve_training_allowed_symbols(host)' in code, \
        '入口必须解析一次训练期集合'
    assert '_train_allowed = _training_allowed_cache' in code, \
        '守卫必须用解析结果'
    assert 'getattr(host, "training_allowed_symbols", set())' not in code, \
        '不得再直接读 host 字段（它在独立调度器下恒为空 → 守卫失效）'


def test_resolution_happens_at_function_entry():
    code = _code(_SRC)
    i_fn = code.find('def execute_master_decisions(')
    i_res = code.find('_training_allowed_cache = _resolve_training_allowed_symbols(host)')
    assert i_fn > 0 and i_res > i_fn
    # 入口解析必须早于守卫使用
    i_use = code.find('_train_allowed = _training_allowed_cache')
    assert i_res < i_use


def test_resolver_exists_and_documents_why():
    src = io.open(_SRC, encoding='utf-8').read()
    assert 'def _resolve_training_allowed_symbols(' in src
    assert 'MIDLONG_AGENT_INDEPENDENT_SCHEDULER' in src, '应注明失效链的根因'
