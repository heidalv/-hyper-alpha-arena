# -*- coding: utf-8 -*-
"""轮80 P2 回归：master 决策快照的 executed 回写被覆盖（学习数据静默丢失）。

## P2-1（真缺陷，已修）

    :1361  _snap_entry = snap                     # 快照刚建好
    :1362  _pending_snapshots.append(snap)
    ...
    :1369  _snap_entry = None                     # ← 同一轮稍后又被清成 None

而 `host.mark_master_decision_executed(_snap_entry, _dec_log_entry, db)` 在本函数内
出现 **8 次**，收到的一律是 None → `DecisionSnapshot.executed` 永不置位。
`executed == True` 是学习侧 `local_llm/dataset_builder.py:98`、
`experience_retriever.py:202`、`trade_attribution_service.py:124` 的**硬过滤条件**
→ master 车道的决策从不进入学习数据（无报错、无日志）。

修：两个句柄都改到**循环头**复位（`for dec in decisions:` 下方第一件事），
赋值点不再被覆盖。复位必须留在循环头而不是删除 —— `_snap_entry = snap` 在 try 内，
try 提前失败时变量未定义，后续回写会抛 NameError。

## P2-2（审计误判，本次澄清）

审计称 `:3019/3109` 的 pyramid/dca 块「~170 行不可达 → AI 输出 pyramid/dca 静默落空、
加仓功能整段失效」。**不成立**：同一个 `for dec in decisions:` 的**顶层**分支链里
`:2809 elif action == "pyramid" and mode == "running" and pos:` 与
`:2895 elif action == "dca" ...` 是真正生效的实现；`:3076` 那两段是**嵌套冗余副本**
（外层条件 `elif action in ("buy","sell")` 与 `action == "pyramid"` 互斥，故不可达）。
即加仓/补仓功能正常，只是同文件里存在重复代码。

本测试把这个可达性事实**写成断言**，防止将来有人「修」错方向
（例如把外层条件放开成 `("buy","sell","pyramid","dca")` —— 那会让从未在生产执行过的
嵌套副本变成活代码，属资金风险）。
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_SRC = os.path.join(_ROOT, 'backend/services/full_auto/master_execution.py')


def _read():
    return io.open(_SRC, encoding='utf-8').read()


def _code():
    return '\n'.join(l for l in _read().splitlines() if not l.lstrip().startswith('#'))


# ══════════════════════════════════════════════════════════════════════
# P2-1：快照句柄不得在同一轮被覆盖
# ══════════════════════════════════════════════════════════════════════

def test_snapshot_assignment_not_overwritten_later():
    """`_snap_entry = snap` 之后不得再出现同轮的无条件 `_snap_entry = None`。"""
    lines = _read().splitlines()
    i_assign = next(i for i, l in enumerate(lines)
                    if re.match(r'\s*_snap_entry = snap\s*$', l))
    tails = [
        (i + 1, l) for i, l in enumerate(lines)
        if i > i_assign and re.match(r'\s*_snap_entry = None\s*$', l)
    ]
    assert not tails, f'赋值后仍有覆盖（行号 {[t[0] for t in tails]}）'


def test_both_handles_reset_at_loop_head():
    """两个句柄必须在循环头复位（保证 try 失败时仍已定义）。"""
    lines = _read().splitlines()
    i_loop = next(i for i, l in enumerate(lines)
                  if re.match(r'    for dec in decisions:\s*$', l))
    # 循环头之后 12 行内应出现两次复位
    window = '\n'.join(lines[i_loop + 1:i_loop + 13])
    assert re.search(r'_snap_entry = None', window), '循环头后应复位 _snap_entry'
    assert re.search(r'_dec_log_entry = None', window), '循环头后应复位 _dec_log_entry'


def test_reset_precedes_assignment():
    lines = _read().splitlines()
    i_reset = next(i for i, l in enumerate(lines) if re.match(r'\s*_snap_entry = None\s*$', l))
    i_assign = next(i for i, l in enumerate(lines) if re.match(r'\s*_snap_entry = snap\s*$', l))
    assert i_reset < i_assign, '复位必须在赋值之前（循环头）'


def test_mark_executed_call_sites_still_exist():
    """回写点不能被顺手删掉（修复的是「传进去的是 None」，不是「不该回写」）。

    共 8 处；其中 `:2098` 是多行写法
    （`mark_master_decision_executed(\\n    _snap_entry, _dec_log_entry, db,`），
    首版测试用 `mark_master_decision_executed\\(_snap_entry` 严格同行匹配，只数到 7 —— 按 `\\s*` 放宽。
    """
    code = _code()
    n = len(re.findall(r'mark_master_decision_executed\(\s*_snap_entry', code))
    assert n == 8, f'回写点应为 8 处，实际 {n}'


# ══════════════════════════════════════════════════════════════════════
# P2-2：pyramid/dca 的顶层实现必须可达；嵌套副本必须被标注为冗余
# ══════════════════════════════════════════════════════════════════════

def test_top_level_pyramid_and_dca_handlers_exist():
    """缩进 8 的顶层 elif 才是可达实现。"""
    lines = _read().splitlines()
    top = [l for l in lines
           if len(l) - len(l.lstrip()) == 8
           and re.match(r'\s*elif action == "(pyramid|dca)"', l)]
    assert len(top) == 2, f'顶层应有 pyramid 与 dca 两个可达分支，实际 {top}'
    assert any('pyramid' in t for t in top)
    assert any('dca' in t for t in top)


def test_nested_copies_are_documented_as_redundant():
    """嵌套副本必须带「冗余/不可达」标注，避免被误当成唯一实现。"""
    src = _read()
    i = src.find('if action == "pyramid" and _same_dir_pos:')
    assert i > 0
    seg = src[i:i + 1400]
    assert '冗余' in seg or '不可达' in seg, '嵌套副本应标注为冗余/不可达'
    assert '2809' in seg or '顶层' in seg, '标注应指向上方的真实实现'


def test_outer_guard_still_buy_sell_only():
    """外层条件不得被扩成含 pyramid/dca（那会让冗余副本变成活代码）。"""
    code = _code()
    assert 'elif action in ("buy", "sell") and mode == "running":' in code, \
        '外层的 buy/sell 限定应保持不变'


def test_audit_corrected_in_report():
    """审计报告里应对 P2-2 留下更正说明。"""
    rep = os.path.join(_ROOT, 'reports/_轮66_全面断点错误审计_20260918.md')
    src = io.open(rep, encoding='utf-8').read()
    assert 'P2-2' in src
