# -*- coding: utf-8 -*-
"""轮76 P1-12 回归：`paused` 会话在健康检查里整轮 return → 风控最紧张时失明。

## 事故

`run_health_check()` 旧实现：

    if session.status not in ("running", "defensive", "paused"):
        return
    if session.status == "paused":
        return          # ← 白名单接纳后立刻全部返回

于是会话一旦暂停，整轮巡检**全部跳过**：日盈亏核算、回撤复查、子仓对账、
ExitAgent 时间止损巡检都不跑。

而 `:1060` 的注释明写「**不 return**：后续持仓治理 / 平仓巡检照常，只是不再新开仓」——
DD 硬闸（`:1071 session.status = "paused"`）自己就依赖这句话让治理继续。
同一份代码里「暂停」因此有两种互相矛盾的语义：

- DD 硬闸触发的 paused → 治理照常（注释描述的行为）
- 其它来源（人工 `/api/full-auto pause`、亏损锁）→ 治理完全停摆

后果：风险最高（已触发硬闸或人工急停）时，持仓反而**失去**出场巡检与对账。

## 修复

统一为「**暂停 = 只停新开仓，治理照常**」：

1. 删除整轮 return，改为 `_session_paused` 标记 + 日志；
2. `should_run` 在 `_session_paused` 时强制 False（不开新仓）；
3. `4.6 风控巡检` 的免打扰条件从「仅 drawdown_limit」放宽到「任何 paused」——
   否则删掉 return 后，其它原因的 paused 会落进 `paper_auto_unlock_session`
   被**静默恢复交易**（该函数名即「自动解锁」），是用户明确不想要的。
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_SRC = os.path.join(_ROOT, 'backend/services/full_auto/health_check_cycle.py')


def _read():
    return io.open(_SRC, encoding='utf-8').read()


def _body(src, fn='run_health_check'):
    m = re.search(rf'^def {re.escape(fn)}\(.*?(?=^def |^class |\Z)', src, re.S | re.M)
    assert m, f'找不到 {fn}'
    return m.group(0)


def _code(body):
    """去注释后的可执行代码。"""
    return '\n'.join(l for l in body.splitlines() if not l.lstrip().startswith('#'))


# ══════════════════════════════════════════════════════════════════════
# 1. 不得再整轮 return
# ══════════════════════════════════════════════════════════════════════

def test_paused_no_longer_returns_early():
    body = _body(_read())
    code = _code(body)
    assert not re.search(r'if\s+session\.status\s*==\s*["\']paused["\']\s*:\s*\n\s*return\b', code), \
        'paused 不得再整轮 return（那会连持仓治理/出场巡检一起停掉）'


def test_paused_is_still_whitelisted():
    """paused 仍要能进入巡检体（否则等于换个写法继续跳过）。"""
    body = _body(_read())
    assert re.search(r'\(\s*["\']running["\']\s*,\s*["\']defensive["\']\s*,\s*["\']paused["\']\s*\)', body)


def test_paused_flag_is_set():
    body = _body(_read())
    assert '_session_paused' in body
    assert re.search(r'_session_paused\s*=\s*\(\s*session\.status\s*==\s*["\']paused["\']', body)


# ══════════════════════════════════════════════════════════════════════
# 2. 但必须真的不开新仓
# ══════════════════════════════════════════════════════════════════════

def test_should_run_is_forced_false_when_paused():
    body = _body(_read())
    m = re.search(r'if\s+_session_paused\s+and\s+should_run\s*:(.*?)\n\s{12}\S', body, re.S)
    assert m, '应有「_session_paused 时把 should_run 置 False」的分支'
    assert 'should_run = False' in m.group(1)


def test_should_run_gate_precedes_strategy_creation():
    """守卫必须在 should_run 被消费之前生效。"""
    body = _body(_read())
    i_flag = body.find('_session_paused and should_run')
    i_use = body.find('if should_run:')
    assert i_flag > 0 and i_use > 0
    assert i_flag < i_use, 'paused 守卫必须早于 `if should_run:` 的使用点'


# ══════════════════════════════════════════════════════════════════════
# 3. 任何 paused 都必须免于自动解锁（否则等于静默恢复交易）
# ══════════════════════════════════════════════════════════════════════

def test_any_paused_skips_auto_unlock():
    body = _body(_read())
    assert '_paused_any' in body, '需要 _paused_any 覆盖「任何原因的 paused」'
    # 分支条件必须是 _paused_any，而不是仅 _dd_paused
    assert re.search(r'if\s+_paused_any\s*:', body), \
        '4.6 风控巡检的首要分支应为 _paused_any（否则非 drawdown 原因的 paused 会被自动解锁）'


def test_auto_unlock_is_in_elif_chain_after_paused_guard():
    """`host.paper_auto_unlock_session(...)` 必须挂在 elif 链上（受 paused 守卫保护）。"""
    body = _body(_read())
    i_guard = body.find('if _paused_any:')
    # 锚定**调用点**而不是名字（该名字还出现在 Host 字段定义与注释里）
    i_unlock = body.find('host.paper_auto_unlock_session(db, session)')
    assert i_guard > 0, '找不到 _paused_any 守卫'
    assert i_unlock > i_guard, \
        f'自动解锁调用必须在 paused 守卫之后（guard@{i_guard} unlock@{i_unlock}）'
    # 守卫与该调用之间应存在 elif 链（说明自动解锁受守卫短路）
    seg = body[i_guard:i_unlock]
    assert re.search(r'elif\s+host\.', seg), 'paused 守卫后应接 elif 链'


# ══════════════════════════════════════════════════════════════════════
# 4. 治理/出场巡检仍在 paused 之后（即真的会执行）
# ══════════════════════════════════════════════════════════════════════

def test_governance_calls_are_after_the_paused_guard():
    body = _body(_read())
    i_guard = body.find('_session_paused = (session.status == "paused")')
    assert i_guard > 0
    for needle, label in (
        ('sub_mgr.reconcile(', '子仓对账'),
        ('run_exit_pass', 'ExitAgent 出场巡检'),
    ):
        i = body.find(needle)
        assert i > i_guard, f'{label} 必须位于 paused 守卫之后（即暂停时仍会执行）'
