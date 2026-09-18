# -*- coding: utf-8 -*-
"""轮82 P2-3 回归：风控「拦截」被自己的日志副作用关掉（7 处同类）。

## 事故形态

多处风控写成这样：

    try:
        if <冻结 / 超预算 / 方向冲突 / 趋势锁定 / 手续费不足>:
            host.append_event(session, "xxx_block", "...")   # ← 可能抛
            continue                                          # ← 与副作用同处一个 try
    except Exception:
        pass                                                  # ← 静默

`append_event` 抛异常时 `continue` **不会执行** ——
「拦截」直接变成「放行」，而且一行日志都没有。
**风控被自己的日志副作用关掉了。**

`master_execution.py` 内共 7 处（含审计点名的编排器冻结块）：

| # | 事件 | 拦截语义 |
|---|---|---|
| 1 | `orchestrator_frozen_block` | 编排器冻结，不允许新开仓 |
| 2 | `pace_symmetric_block` | Pace shadow 对称禁开 |
| 3 | `layer_budget_block` | 该层预算已满/不足 |
| 4 | `orchestrator_gate_block` | 编排器硬门控 |
| 5 | `direction_gate_block` | DCP tier 方向约束 |
| 6 | `trend_lock_block` | 趋势锁定（禁止反向开仓） |
| 7 | `fee_threshold_block` | 预期利润 < 手续费×3 |

其中 #6 更隐蔽：`append_event` 在 for 循环内、`continue` 在循环外，
记录失败时连 `_same_dir_trend` 都仍是 False。

## 修复

新增 `_emit_block_event(host, session, event, message, *, sym, action)`：
**判定归判定，记录归记录** —— 记录失败降级为一条 WARNING，裁决照原样发生。
7 处全部改为「先判定（try 只包判定）→ 再记录 → 再 continue」。
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from backend.services.full_auto.master_execution import _emit_block_event

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_SRC = os.path.join(_ROOT, 'backend/services/full_auto/master_execution.py')


def _read():
    return io.open(_SRC, encoding='utf-8').read()


def _code():
    return '\n'.join(l for l in _read().splitlines() if not l.lstrip().startswith('#'))


# ══════════════════════════════════════════════════════════════════════
# 1. helper 本身：记录失败绝不上抛
# ══════════════════════════════════════════════════════════════════════

class _HostOk:
    def __init__(self):
        self.events = []

    def append_event(self, session, event, msg):
        self.events.append((event, msg))


class _HostBoom:
    def append_event(self, session, event, msg):
        raise RuntimeError('append_event 炸了')


def test_emit_block_event_records_normally():
    h = _HostOk()
    _emit_block_event(h, object(), "x_block", "msg", sym="BTC", action="buy")
    assert h.events == [("x_block", "msg")]


def test_emit_block_event_swallows_failure():
    """记录失败**不得上抛** —— 上抛会让调用方的拦截被外层 except 吞掉。"""
    _emit_block_event(_HostBoom(), object(), "x_block", "msg", sym="BTC", action="buy")


def test_emit_block_event_logs_warning_on_failure(caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        _emit_block_event(_HostBoom(), object(), "x_block", "msg", sym="BTC", action="buy")
    assert any('拦截事件记录失败' in r.getMessage() for r in caplog.records), \
        '记录失败必须可见（原实现是完全静默）'


# ══════════════════════════════════════════════════════════════════════
# 2. 结构性守卫：判定与副作用不得同处一个 try
# ══════════════════════════════════════════════════════════════════════

def _risky_sites(src: str):
    """复刻审计用的扫描：try 体内既有 continue/return 又有 append_event，
    且 except 体只有 pass。"""
    lines = src.splitlines()
    hits = []
    for i, l in enumerate(lines):
        if not re.match(r'\s*try:\s*$', l):
            continue
        ind = len(l) - len(l.lstrip())
        j, body = i + 1, []
        while j < len(lines):
            lj = lines[j]
            if lj.strip() and (len(lj) - len(lj.lstrip())) <= ind and re.match(r'\s*(except|finally)', lj):
                break
            if lj.strip() and (len(lj) - len(lj.lstrip())) < ind:
                break
            body.append(lj)
            j += 1
        txt = '\n'.join(body)
        if (re.search(r'\b(continue|return)\b', txt) and 'append_event' in txt
                and j < len(lines) and re.match(r'\s*except\s+Exception\s*:\s*$', lines[j])
                and j + 1 < len(lines) and lines[j + 1].strip() == 'pass'):
            hits.append(i + 1)
    return hits


def test_no_guard_swallows_its_own_block():
    """核心守卫：不得再有「拦截 + 记录」同处一个 try 且 except 仅 pass 的写法。"""
    hits = _risky_sites(_read())
    assert not hits, f'仍有 {len(hits)} 处同类风险点（行号 {hits}）'


def test_all_seven_events_use_the_helper():
    """7 个拦截事件都必须经 helper 记录（证明不是只修了审计点名的那一处）。"""
    code = _code()
    for event in ("orchestrator_frozen_block", "pace_symmetric_block",
                  "layer_budget_block", "orchestrator_gate_block",
                  "direction_gate_block", "trend_lock_block", "fee_threshold_block"):
        assert f'"{event}"' in code, f'{event} 应仍存在'
        # 该事件名附近应出现 helper 调用
        i = code.find(f'"{event}"')
        seg = code[max(0, i - 300): i + 300]
        assert '_emit_block_event(' in seg, f'{event} 未走 _emit_block_event'


def test_judgement_errors_are_visible_not_silent():
    """判定本身抛异常时：保持「不拦截」但必须 warning（原为完全静默）。"""
    src = _read()
    for label in ("编排器冻结判定异常", "Pace 对称禁开判定异常", "层预算判定异常",
                  "编排器硬门控判定异常", "DCP tier判定异常", "趋势锁定判定异常",
                  "手续费门槛判定异常"):
        assert label in src, f'缺少「{label}」告警（判定异常不应静默）'


def test_frozen_block_still_continues():
    """编排器冻结块必须仍然 `continue`（不因重构而失去拦截力）。"""
    code = _code()
    i = code.find('"orchestrator_frozen_block"')
    assert i > 0
    seg = code[i:i + 600]
    assert 'continue' in seg, '冻结块必须 continue'


def test_no_guard_block_emits_event_inside_its_decision_try():
    """判定 try 内不得出现**拦截性** append_event。

    注意范围：只有「同一 try 内既有 continue/return 又有 append_event」才是风险
    （记录失败会把拦截一起吞掉）。文件里另有约 40 处 append_event 位于 try 内，
    但那些是**成功/通知类**副作用，其 try 内没有控制流裁决，不受本缺陷影响 ——
    首版测试写成「任何 try 内都不得有 append_event」，误报 49 处。
    """
    bad = _risky_sites(_read())
    assert not bad, f'这些判定 try 内仍有拦截性 append_event（行号 {bad}）'
