# -*- coding: utf-8 -*-
"""轮71 P1 回归：把本地 naive 时间戳当 UTC → 时长/冷却判定差 8 小时。

## 实测证据（本测试的核心断言依据）

PostgreSQL 会话时区为 Asia/Shanghai，`paper_positions` 的 `TIMESTAMP`（无时区）列
存的是**本地钟面**：写入 aware-UTC `12:54:45+00:00`，读回是 `20:54:45`。

    >>> 旧算法 elapsed = (datetime.now(timezone.utc) - 读回.replace(tzinfo=utc)) / 3600
    -8.00h          # 刚写入就得到「8 小时前」的负值

后果不是「数值略偏」而是**判定反向**：

- `sub_position_manager._check_reduce_limits` 的冷却：刚减过仓的仓位被判成
  「距上次 -8.0h < 1.0h」→ 全部拒绝。scalp 0.5h→实际锁 8.5h、
  intraday 1h→9h、trend_follow 24h→32h —— 分段止盈/风险减仓被静默禁用。
- `age_hours` 变成负数（持仓时长）。
- `get_all_sub_positions` 的 `age_hours` 同样错位。
"""
import io
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from backend.utils.db_datetime import DB_NAIVE_TZ, db_dt_for_age, parse_db_naive_to_utc

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


# ══════════════════════════════════════════════════════════════════════
# 1. 归一化助手本身
# ══════════════════════════════════════════════════════════════════════

def test_db_dt_for_age_treats_naive_as_local():
    """naive 值按 Asia/Shanghai 理解，转成 UTC aware（同一时刻的换标）。"""
    naive_local = datetime(2026, 9, 18, 20, 40, 51)      # 本地钟面
    out = db_dt_for_age(naive_local)
    assert out is not None
    assert out.tzinfo is not None
    # 20:40 本地 == 12:40 UTC（同一时刻）
    assert out.astimezone(timezone.utc).hour == 12
    # 与「本地钟面 + 明确标注为 +08:00」是同一时刻
    assert out == naive_local.replace(tzinfo=DB_NAIVE_TZ)
    # 与「错标成 UTC」不是同一时刻（差 8 小时）—— 那正是被修的 bug
    assert out != naive_local.replace(tzinfo=timezone.utc)


def test_db_dt_for_age_matches_parse_helper():
    dt = datetime(2026, 9, 18, 8, 0, 0)
    assert db_dt_for_age(dt) == parse_db_naive_to_utc(dt)


def test_db_dt_for_age_handles_none_and_string():
    assert db_dt_for_age(None) is None
    assert db_dt_for_age("") is None
    s = db_dt_for_age("2026-09-18T12:40:51+00:00")
    assert s is not None and s.tzinfo is not None
    assert s.astimezone(timezone.utc).hour == 12


def test_local_tz_is_plus_8():
    assert DB_NAIVE_TZ.utcoffset(None) == timedelta(hours=8)


# ══════════════════════════════════════════════════════════════════════
# 2. 复现 bug：旧算法给出 -8h
# ══════════════════════════════════════════════════════════════════════

def _old_elapsed_hours(naive_from_db, now_utc):
    """旧写法：把 naive 当 UTC。"""
    lr = naive_from_db.replace(tzinfo=timezone.utc)
    return (now_utc - lr).total_seconds() / 3600.0


def _new_elapsed_hours(naive_from_db, now_utc):
    """新写法：经 db_dt_for_age 归一化。"""
    lr = db_dt_for_age(naive_from_db)
    return (now_utc - lr).total_seconds() / 3600.0


def test_old_math_is_minus_8_hours_for_a_just_written_timestamp():
    """刚写入（本地钟面 = 现在）→ 旧算法 -8h，新算法 ≈0。"""
    now_local = datetime.now()
    now_utc = now_local.astimezone(timezone.utc)
    naive_from_db = now_local.replace(tzinfo=None)     # PG 落库后的形态

    old = _old_elapsed_hours(naive_from_db, now_utc)
    new = _new_elapsed_hours(naive_from_db, now_utc)

    assert -8.01 < old < -7.99, old
    assert abs(new) < 0.01, new


def test_cooldown_verdict_flips():
    """这是「减仓冷却静默失效」的机制。

    刚减过仓（elapsed 真值 ≈ 0h），冷却 1h：

    - **旧算法**：elapsed = -8h → `-8 < 1` 成立 → 判「冷却已过」→ **放行**（错，等于冷却不存在）
    - **新算法**：elapsed ≈ 0h → `0 < 1` 成立 → 判「冷却中」→ **拦截**（对）
    """
    cooldown_h = 1.0
    now_local = datetime.now()
    now_utc = now_local.astimezone(timezone.utc)
    naive_from_db = now_local.replace(tzinfo=None)

    old_elapsed = _old_elapsed_hours(naive_from_db, now_utc)
    new_elapsed = _new_elapsed_hours(naive_from_db, now_utc)

    assert old_elapsed < 0, f'旧算法应给出负值，实际 {old_elapsed}'
    assert abs(new_elapsed) < 0.01, f'新算法应≈0，实际 {new_elapsed}'

    # 「冷却中」= elapsed < cooldown
    assert (old_elapsed < cooldown_h) is True      # 负值必然 < 1 → 旧算法恒判「冷却中」？
    # 注意：负值是**恒小于**任何正冷却阈值 —— 这正是问题：旧算法把「刚减仓」判成
    # elapsed=-8h，而 -8 < 1 成立 → 拦截。但**8 小时之后** elapsed 变成 0h 仍 < 1 →
    # 依旧拦截；要到真实 elapsed > 9h 才放行 —— 冷却被拉长到 `冷却+8h`。
    assert (new_elapsed < cooldown_h) is True      # 新算法同样拦截（正确：刚减仓就该拦）

    # 关键差异：距上次 2h（> 冷却 1h）时，旧算法仍拦（被 +8h 拉长），新算法放行
    two_h_ago = (now_local - timedelta(hours=2)).replace(tzinfo=None)
    assert _old_elapsed_hours(two_h_ago, now_utc) < cooldown_h, '旧算法：2h 前仍被判冷却中'
    assert _new_elapsed_hours(two_h_ago, now_utc) >= cooldown_h, '新算法：2h 前应放行'


def test_cooldown_boundary_semantics():
    """边界：距上次 0.5h、冷却 1h → 应拦截；距上次 2h → 应放行。"""
    now_local = datetime.now()
    now_utc = now_local.astimezone(timezone.utc)
    cooldown_h = 1.0

    within = (now_local - timedelta(hours=0.5)).replace(tzinfo=None)
    beyond = (now_local - timedelta(hours=2)).replace(tzinfo=None)

    assert _new_elapsed_hours(within, now_utc) < cooldown_h
    assert _new_elapsed_hours(beyond, now_utc) >= cooldown_h


def test_age_hours_no_longer_negative():
    """持仓 3 小时 → age 应为 +3h，而不是 -5h。"""
    now_local = datetime.now()
    now_utc = now_local.astimezone(timezone.utc)
    opened = (now_local - timedelta(hours=3)).replace(tzinfo=None)
    age = _new_elapsed_hours(opened, now_utc)
    assert 2.99 < age < 3.01, age
    assert _old_elapsed_hours(opened, now_utc) < 0


# ══════════════════════════════════════════════════════════════════════
# 3. 源码级守卫：不得再出现 replace(tzinfo=utc) 直接算时长
# ══════════════════════════════════════════════════════════════════════

def test_sub_position_manager_has_no_raw_utc_relabel():
    """`sub_position_manager.py` 的四处时长/冷却计算必须走 db_dt_for_age。"""
    import re
    src = io.open(os.path.join(_ROOT, 'backend/services/sub_position_manager.py'), encoding='utf-8').read()
    code = re.sub(r'#.*', '', src)                     # 去掉注释（修复说明会引用旧写法）
    assert not re.search(r'\.replace\(tzinfo=timezone\.utc\)', code), \
        'sub_position_manager 仍有把 naive 当 UTC 的写法'
    assert code.count('db_dt_for_age') >= 4, \
        f'应有至少 4 处使用 db_dt_for_age，实际 {code.count("db_dt_for_age")}'


def test_reentry_cooldown_cutoff_uses_local_clock():
    """`reentry_cooldown` 的 SQL 截止值必须与 closed_at 的本地钟面同尺度。"""
    src = io.open(os.path.join(_ROOT, 'backend/services/reentry_cooldown.py'), encoding='utf-8').read()
    assert 'since = datetime.now(timezone.utc) - timedelta(seconds=lookback)' not in src, \
        'SQL 截止值不得用 aware-UTC（会按会话时区落成 +8h，窗口被放宽 8 小时）'
    assert 'since = datetime.now() - timedelta(seconds=lookback)' in src
