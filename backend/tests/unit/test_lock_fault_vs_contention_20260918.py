# -*- coding: utf-8 -*-
"""轮85 P2-11 回归：锁的**文件系统故障**被说成「其他进程正在执行」，全线 tick 静默停摆。

## 事故

`_try_acquire_unified_loop_process_lock` 旧实现：

    except (BlockingIOError, PermissionError, OSError):
        # 锁确实被其他进程持有 → 本 tick 让位
        ...
        return None

把三类异常合并：

- `BlockingIOError` → 确实是**锁竞争**（别的进程持锁），让位正确；
- `PermissionError` / `OSError`（只读目录、`data/locks` 权限、ENOSPC、路径异常）
  → 是**文件系统故障**，与锁竞争无关。

合并后的调用方打印：

    logger.info("[FullAuto] 其他进程正在执行统一循环，跳过本次调度 {session_id}")

即把故障说成正常让位（且是 INFO 级）。真实后果是**所有 full-auto tick 停摆**，
而日志里看不到任何异常 —— 没人会去查权限或磁盘。

（同文件 2026-06-11 的注释已经记录过同一类误判：Windows 缺 `fcntl` 时
ImportError 被当成「其他进程在执行」，导致 tick 完全停摆。）

## 修复

返回值改为三态，用私有哨兵 `_LOCK_FS_FAULT` 区分「故障」与「竞争」：

- 文件句柄 → 拿到锁；
- `None` → 锁竞争（`BlockingIOError`），正常让位，INFO 日志不变；
- `_LOCK_FS_FAULT` → 文件系统故障，**ERROR 级**日志并明确指向
  `data/locks` 权限 / 磁盘空间。

仍跳过本 tick（避免双执行），但不再掩盖故障。
"""
import inspect
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from backend.services.full_auto_trading_service import (
    _LOCK_FS_FAULT,
    FullAutoTradingService,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_SRC = os.path.join(_ROOT, 'backend/services/full_auto_trading_service.py')


def _lock_src() -> str:
    return inspect.getsource(FullAutoTradingService._try_acquire_unified_loop_process_lock)


def _read():
    return io.open(_SRC, encoding='utf-8').read()


# ══════════════════════════════════════════════════════════════════════
# 1. 三态契约
# ══════════════════════════════════════════════════════════════════════

def test_fault_sentinel_exists_and_is_unique():
    assert _LOCK_FS_FAULT is not None
    assert _LOCK_FS_FAULT is not False and _LOCK_FS_FAULT != 0
    # 不能与「锁竞争」的 None 混同
    assert _LOCK_FS_FAULT is not None


def test_contention_and_fault_are_separate_except_clauses():
    src = _lock_src()
    assert 'except BlockingIOError:' in src, '锁竞争应单独一个 except'
    assert 'except (PermissionError, OSError)' in src, '文件系统故障应单独一个 except'
    # 旧的合并写法不得保留
    assert 'except (BlockingIOError, PermissionError, OSError)' not in src, \
        '不得再把三类异常合并（那是本缺陷的成因）'


def test_contention_returns_none_fault_returns_sentinel():
    src = _lock_src()
    # 竞争分支 return None
    i = src.find('except BlockingIOError:')
    j = src.find('except (PermissionError, OSError)')
    assert 0 < i < j
    assert 'return None' in src[i:j], '竞争分支应 return None'
    assert 'return _LOCK_FS_FAULT' in src[j:], '故障分支应 return 哨兵'


def test_fault_is_logged_as_warning_in_acquire():
    src = _lock_src()
    i = src.find('except (PermissionError, OSError)')
    seg = src[i:i + 900]
    assert 'logger.warning' in seg, '获取阶段应先记一条 warning'
    assert '文件系统故障' in seg, '日志应明确说这是文件系统故障而非锁竞争'


# ══════════════════════════════════════════════════════════════════════
# 2. 调用方：不得把故障说成「其他进程正在执行」
# ══════════════════════════════════════════════════════════════════════

def test_caller_handles_fault_before_contention():
    """哨兵分支必须早于 `is None` 分支（顺序反了故障又会被说成竞争）。"""
    src = _read()
    i_fault = src.find('if process_lock is _LOCK_FS_FAULT:')
    i_none = src.find('if process_lock is None:', i_fault if i_fault > 0 else 0)
    assert i_fault > 0, '调用方必须显式处理哨兵'
    assert i_none > i_fault, '哨兵判断应在 `is None` 之前'


def test_fault_log_is_error_level_and_actionable():
    """故障分支的日志必须 ERROR 级且可执行；**按分支边界截断并剔除注释**
    （首版两个坑：700 字符窗口越界读到下一分支；修复说明里也引用了「其他进程」字样）。"""
    src = _read()
    i = src.find('if process_lock is _LOCK_FS_FAULT:')
    j = src.find('if process_lock is None:', i)
    assert i > 0 and j > i, '找不到故障分支边界'
    seg = '\n'.join(l for l in src[i:j].splitlines() if not l.lstrip().startswith('#'))
    assert 'logger.error' in seg, '故障应记 ERROR（原为 INFO 且文案误导）'
    assert 'data/locks' in seg, '应指向具体排查对象'
    assert '其他进程' not in seg, '故障分支不得出现「其他进程」字样'


def test_contention_log_unchanged():
    src = _read()
    i = src.find('if process_lock is None:')
    seg = src[i:i + 260]
    assert '其他进程正在执行统一循环' in seg, '锁竞争的日志应保持原样'


# ══════════════════════════════════════════════════════════════════════
# 3. 行为：仍跳过本 tick（不因分类而双执行）
# ══════════════════════════════════════════════════════════════════════

def test_fault_still_skips_the_tick():
    src = _read()
    i = src.find('if process_lock is _LOCK_FS_FAULT:')
    seg = src[i:i + 700]
    assert 'return' in seg, '故障时仍应跳过本 tick（避免双执行）'
