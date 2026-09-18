# -*- coding: utf-8 -*-
"""轮83 P2-5 回归：用**编造的权益**当风控分母 → 冻结阈值随账户规模随机失真。

## 事故

`symbol_risk.check_per_symbol_risk` 旧实现：

    total_equity = 0.0
    try:
        bal = paper_engine.get_balance(db, trading_acct) or {}
        total_equity = float(bal.get("total_equity", 0))
    except Exception:
        pass                      # ← 无日志
    if total_equity <= 0:
        total_equity = 10000.0    # ← fallback：编一个分母

而两条 per-symbol 冻结判据都是 `loss_pct = abs(pnl) / total_equity`：

- 真实权益 **< 10000** → 分母被放大 → `loss_pct` 偏小 → **该冻结的币冻不住**；
- 真实权益 **> 10000** → 分母被缩小 → 过度冻结。

即风控阈值随账户规模随机失真。而 `paper_engine.get_balance` 在**没有 PaperBalance 行**时
返回 `None`（不是抛异常），这正是 fallback 最常被触发的情形。

## 修复

1. 权益读取失败 → `logger.warning`（失败必须可见）；
2. 权益不可用时**跳过**按权益百分比的两条判据（不编数字）；
3. **但 Layer 2a 的回撤判据与权益无关**（`session.current_drawdown` 本身已是比例），
   必须照常生效 —— 原先该判据在 `total_equity>0` 的假成立下"看似生效"，
   若直接把 fallback 改成 0 而不加保护，它会被 `if total_equity > 0` 静默关掉。
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_SRC = os.path.join(_ROOT, 'backend/services/full_auto/symbol_risk.py')


def _read():
    return io.open(_SRC, encoding='utf-8').read()


def _code():
    return '\n'.join(l for l in _read().splitlines() if not l.lstrip().startswith('#'))


# ══════════════════════════════════════════════════════════════════════
# 1. 不得再编造分母
# ══════════════════════════════════════════════════════════════════════

def _fn_body(name: str) -> str:
    """取模块级函数体，并剔除注释（修复说明会引用旧写法）。"""
    src = _read()
    m = re.search(rf'^def {re.escape(name)}\(.*?(?=^def |\Z)', src, re.S | re.M)
    assert m, f'找不到 {name}'
    return '\n'.join(l for l in m.group(0).splitlines() if not l.lstrip().startswith('#'))


def test_no_fabricated_equity_fallback():
    """`check_per_symbol_risk` 内不得再编造权益分母。

    注：文件他处有 `initial_cap = 10000.0`（`_initial_capital` 之类，
    与风控阈值无关），故断言必须限定在本函数体内 —— 首版对整个文件断言，误报。
    """
    body = _fn_body('check_per_symbol_risk')
    assert '10000.0' not in body, '不得再用 `total_equity = 10000.0` 编造风控分母'
    assert 'total_equity = 10000' not in body


def test_equity_failure_is_logged_not_silent():
    src = _read()
    i = src.find('def check_per_symbol_risk')
    seg = src[i:i + 3000]
    assert '权益读取异常' in seg, '权益读取异常必须记 warning（原为 except: pass）'
    assert '权益不可用' in seg, '权益不可用时必须显式说明将跳过哪些判据'


def test_equity_known_flag_gates_percent_checks():
    code = _code()
    assert '_equity_known' in code, '需要显式的「权益是否可用」标记'
    assert code.count('_equity_known') >= 4, \
        '该标记应同时用于：赋值、warning、per-symbol 判据、全局判据'


def test_per_symbol_percent_checks_are_gated():
    """两条按权益百分比的判据都必须受 _equity_known 保护。"""
    src = _read()
    i = src.find('def check_per_symbol_risk')
    seg = src[i:i + 4000]
    # tier 分支：无分母时 break
    assert re.search(r'if not _equity_known:\s*\n\s*break', seg), \
        'tier 分支应在无分母时停止判定'
    # 旧回退分支：整体受 _equity_known 保护
    assert 'elif _equity_known:' in seg, '旧 symbol 级回退分支应受 _equity_known 保护'


# ══════════════════════════════════════════════════════════════════════
# 2. 回撤安全网不得被这次修改牵连关掉
# ══════════════════════════════════════════════════════════════════════

def test_drawdown_safety_net_survives():
    """回撤判据与权益无关，必须仍然可达。"""
    src = _read()
    i = src.find('def check_per_symbol_risk')
    seg = src[i:i + 4500]
    assert 'current_dd > global_dd' in seg, '回撤安全网必须仍在'
    # 该判据不得被 `total_equity > 0` 之类的权益条件包住
    m = re.search(r'(\n\s*)if current_dd > global_dd:', seg)
    assert m, '找不到回撤判据'
    indent = m.group(1)
    # 往上找最近的 if/elif 同级条件，确认没有被权益条件包裹
    before = seg[:m.start()]
    last_cond = None
    for line in before.splitlines():
        s = line.strip()
        if re.match(r'(if|elif|else)\b', s) and (len(line) - len(line.lstrip())) <= len(indent):
            last_cond = s
    assert last_cond is None or 'total_equity' not in last_cond, \
        f'回撤判据不应受权益条件约束（当前外层条件: {last_cond}）'


def test_global_daily_check_still_guards_against_zero_equity():
    """全局日亏损判据仍须防除零。"""
    code = _code()
    assert 'if total_equity > 0 and abs(total_daily_loss) / total_equity' in code, \
        '全局日亏损判据应保留 `total_equity > 0` 防除零'


def test_no_division_by_total_equity_without_guard():
    """任何 `abs(pnl)/total_equity` 都必须处在「权益已知」的分支内。"""
    lines = _code().splitlines()
    for i, l in enumerate(lines):
        if re.search(r'/\s*total_equity', l):
            # 向上找最近的守卫
            guard = None
            for j in range(i - 1, max(0, i - 25), -1):
                s = lines[j].strip()
                if '_equity_known' in s or 'total_equity > 0' in s:
                    guard = s
                    break
                if re.match(r'def ', s):
                    break
            assert guard is not None, f'第 {i+1} 行的除法缺少权益守卫: {l.strip()}'
