# -*- coding: utf-8 -*-
"""轮84 P2-4 回归：`check_per_symbol_risk` 在当前配置下**永不执行**（分支遮蔽）。

## 事实链（已实测）

`health_check_cycle.py` 的「4.6 风控巡检」是三选一分支，
而 `check_per_symbol_risk`（per-symbol 日亏冻结 + 全局极端安全网）
**只挂在最后一个 `else`**：

    if _paused_any:                                    ...
    elif host.live_constitutional_enabled(session):    ...
    elif host.paper_loss_locks_disabled(session):      ← 命中这里
        host.paper_auto_unlock_session(db, session)
    else:
        risk_result = host.check_per_symbol_risk(db, session)   ← 到不了

命中原因（实测）：

    data/lock_strength.json            → paper.strength = 0
    lock_strength_service._build_profile('paper', 0)
        disable = s < 8 → True → disable_loss_locks=True
        （同 profile 里 global_extreme_drawdown = 0.99、global_extreme_daily_loss_pct = 0.99）
    → _paper_loss_locks_disabled() 恒为 True

即：**「按币日亏冻结」这一整层在当前配置下不生效**，
同一 profile 里为它准备的极端阈值也随之不可达。

## 定性

这是**配置语义**（strength < 8 表示「关闭亏损锁」）而非代码 bug，
故本轮**不擅自改变行为** —— 只把「本次走了哪个分支、哪层没跑」写进日志，
让「某层风控没在跑」可查，而不是靠读代码才发现。
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_HC = os.path.join(_ROOT, 'backend/services/full_auto/health_check_cycle.py')


def _hc_src():
    return io.open(_HC, encoding='utf-8').read()


# ══════════════════════════════════════════════════════════════════════
# 1. 分支结构事实
# ══════════════════════════════════════════════════════════════════════

def test_paper_strength_zero_disables_loss_locks():
    """实测：paper.strength=0 → disable_loss_locks=True（这是遮蔽的根因）。"""
    from backend.services.lock_strength_service import _build_profile
    p = _build_profile('paper', 0)
    assert p.disable_loss_locks is True
    # 同 profile 里为 per-symbol 层准备的阈值（因此不可达）
    assert p.global_extreme_drawdown >= 0.9
    assert p.global_extreme_daily_loss_pct >= 0.9


def test_higher_strength_enables_the_path():
    """strength ≥ 8 时 disable_loss_locks=False → 才会走到 per_symbol_risk。"""
    from backend.services.lock_strength_service import _build_profile
    assert _build_profile('paper', 20).disable_loss_locks is False


def test_check_per_symbol_risk_is_in_the_final_else():
    """结构事实：per-symbol 巡检只可能在最后一个 else（因此会被前面两个分支遮蔽）。"""
    src = _hc_src()
    i = src.find('host.check_per_symbol_risk(db, session)')
    assert i > 0
    # 往前找最近的同级分支关键字
    before = src[max(0, i - 800):i]
    assert re.search(r'else:\s*\n\s*.*$', before, re.M), \
        'check_per_symbol_risk 应位于 else 分支内'


# ══════════════════════════════════════════════════════════════════════
# 2. 可观测性：必须能看出「这层没跑」
# ══════════════════════════════════════════════════════════════════════

def test_every_branch_logs_which_one_ran():
    """四个分支都必须留下可判读的日志（否则「没跑」只能靠读代码发现）。"""
    src = _hc_src()
    for marker in ("风控巡检分支=live_constitutional",
                   "风控巡检分支=paper_auto_unlock",
                   "风控巡检分支=per_symbol_risk"):
        assert marker in src, f'缺少分支日志：{marker}'


def test_shadowed_branch_log_says_what_is_skipped():
    """被遮蔽的分支要明说「per-symbol 日亏冻结本次不执行」，并指向配置来源。"""
    src = _hc_src()
    i = src.find('风控巡检分支=paper_auto_unlock')
    assert i > 0
    seg = src[i:i + 320]
    assert 'per-symbol' in seg and '不执行' in seg, '日志应说明跳过了什么'
    assert 'lock_strength' in seg, '日志应指向配置来源（便于判断是配置还是 bug）'


def test_report_documents_the_shadowing():
    """审计报告里必须留档 P2-4 的事实与定性。"""
    rep = io.open(os.path.join(_ROOT, 'reports/_轮66_全面断点错误审计_20260918.md'),
                  encoding='utf-8').read()
    assert 'P2-4' in rep


def test_behavior_unchanged_still_calls_auto_unlock():
    """本轮不改变行为：paper_loss_locks_disabled 分支仍调用 paper_auto_unlock_session。

    锚定**分支写法** `elif host.paper_loss_locks_disabled(session):` ——
    裸名字还出现在 Host dataclass 字段定义里，首版因此匹配到错误位置。
    """
    src = _hc_src()
    i = src.find('elif host.paper_loss_locks_disabled(session):')
    assert i > 0, '找不到该分支'
    seg = src[i:i + 500]
    assert 'host.paper_auto_unlock_session(db, session)' in seg
