# -*- coding: utf-8 -*-
"""轮72 P1 回归：`open_notionals()` 失败即放行 → 四道敞口帽按空账本计算。

## 事故

`position_construction.open_notionals()` 在任何 DB 异常下返回全 0 且只记 DEBUG：

    out = {"symbol": 0.0, "cluster": 0.0, "lane": 0.0, "total": 0.0}
    try:
        rows = db.execute(text("SELECT ... FROM paper_positions WHERE ... status='open'")).fetchall()
    except Exception as exc:
        logger.debug("[PositionConstruction] 读取在手名义失败: %s", exc)
        return out                      # ← 空账本 = 未消耗任何帽额

调用方（`paper_trading_engine.py:1179`）把这些 0 当「在手名义」喂给 `clamp()`：

    room = cap − open_notional = cap − 0 = cap

于是四道帽（单币 / 相关簇 / 车道 / gross）**被整额叠加在真实仓位之上**而不是夹紧 ——
被声明为「所有车道必经的唯一权威」的模块被静默绕过，且日志里没有 error 级痕迹。

仓内已有同类处置先例：`paper_trading_engine.py:2208-2211` 把等价吞噬从 debug 升为
warning，理由正是「fail-open 必须可见」。

## 修复

`open_notionals` 增加机器可判的 `ok` 字段；三个调用点全部 fail-closed。
"""
import io
import os
import re
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from backend.services.position_construction import open_notionals

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


# ══════════════════════════════════════════════════════════════════════
# 1. 成功路径：ok=True，数字可信
# ══════════════════════════════════════════════════════════════════════

def _db_with(rows):
    db = MagicMock()
    db.execute.return_value.fetchall.return_value = rows
    return db


def test_ok_true_on_success():
    db = _db_with([("BTC", "long", "trend_follow", 1.0, 100.0, 100.0)])
    out = open_notionals(db, 14, "BTC", lane="long")
    assert out["ok"] is True
    assert out["symbol"] == 100.0
    assert out["total"] == 100.0


def test_ok_true_with_no_positions():
    """没有在手仓位 → ok=True 且全 0（这才是「真的零敞口」）。"""
    out = open_notionals(_db_with([]), 14, "BTC", lane="long")
    assert out["ok"] is True
    assert out["symbol"] == 0.0 and out["total"] == 0.0


# ══════════════════════════════════════════════════════════════════════
# 2. 失败路径：ok=False，且不得与「零敞口」混淆
# ══════════════════════════════════════════════════════════════════════

def test_ok_false_on_db_error():
    db = MagicMock()
    db.execute.side_effect = RuntimeError("relation does not exist")
    out = open_notionals(db, 14, "BTC", lane="long")
    assert out["ok"] is False, 'DB 异常必须显式标记 ok=False'
    assert out["symbol"] == 0.0 and out["total"] == 0.0   # 占位值，语义不是「零敞口」


def test_error_is_distinguishable_from_empty():
    """核心不变式：读取失败 ≠ 没有仓位。"""
    empty = open_notionals(_db_with([]), 14, "BTC", lane="long")
    failed = open_notionals(MagicMock(**{"execute.side_effect": RuntimeError("boom")}), 14, "BTC", lane="long")
    assert empty["ok"] is True and failed["ok"] is False
    # 裸数字完全一样 —— 所以调用方**只能**靠 ok 分辨
    assert empty["symbol"] == failed["symbol"] == 0.0


def test_failure_logs_at_warning_not_debug():
    """fail-open 必须可见：读失败不得只记 DEBUG。"""
    src = io.open(os.path.join(_ROOT, 'backend/services/position_construction.py'), encoding='utf-8').read()
    m = re.search(r'def open_notionals\(.*?(?=\ndef |\Z)', src, re.S)
    body = m.group(0)
    assert 'logger.warning' in body, 'open_notionals 读取失败应记 warning'
    assert 'logger.debug' not in body.split('except')[1][:400], '读取失败分支不得再只记 debug'


# ══════════════════════════════════════════════════════════════════════
# 3. 三个调用点必须 fail-closed
# ══════════════════════════════════════════════════════════════════════

def _src(rel):
    return io.open(os.path.join(_ROOT, rel), encoding='utf-8').read()


def test_paper_engine_fails_closed():
    src = _src('backend/services/paper_trading_engine.py')
    i = src.find('_open = _pc.open_notionals(')
    assert i > 0
    window = src[i:i + 1200]
    assert '_open.get("ok", False)' in window, 'paper_engine 调用点必须检查 ok'
    assert '"success": False' in window and 'blocked' in window, '读失败应拒单'


def test_trading_commands_fails_closed():
    src = _src('backend/services/trading_commands.py')
    i = src.find('_open = _pc.open_notionals(')
    assert i > 0
    window = src[i:i + 900]
    assert '_open.get("ok", False)' in window, 'trading_commands 调用点必须检查 ok'


def test_trend_e1_fails_closed():
    src = _src('backend/services/trend_e1_engine.py')
    i = src.find('opens = pc.open_notionals(')
    assert i > 0
    window = src[i:i + 700]
    assert 'opens.get("ok", False)' in window, 'trend_e1 调用点必须检查 ok'
    assert 'continue' in window, '读失败应跳过开仓'


# ══════════════════════════════════════════════════════════════════════
# 4. 语义：未夹紧的名义确实会顶穿帽额（说明为何必须 fail-closed）
# ══════════════════════════════════════════════════════════════════════

def test_clamp_grants_full_room_when_book_is_empty():
    """演示危害：同一笔单，空账本被允许、真实账本被夹。

    这解释了为什么「读取失败返回 0」不能容忍。
    """
    from backend.services.position_construction import LaneLimits, clamp

    lim = LaneLimits.for_lane("long")
    eq, px = 10_000.0, 100.0

    empty = clamp(lane="long", symbol="BTC", equity=eq, price=px,
                  notional=5_000.0, leverage=3.0, stop_distance_pct=0.03,
                  symbol_open_notional=0.0, cluster_open_notional=0.0,
                  lane_open_notional=0.0, limits=lim)
    loaded = clamp(lane="long", symbol="BTC", equity=eq, price=px,
                   notional=5_000.0, leverage=3.0, stop_distance_pct=0.03,
                   symbol_open_notional=0.34 * eq, cluster_open_notional=0.0,
                   lane_open_notional=0.34 * eq, limits=lim)

    # 空账本 → 名义更大（未受在手仓位挤压）；真实账本 → 被夹到更小
    assert empty.notional >= loaded.notional, (empty.notional, loaded.notional)
    assert empty.notional > 0
