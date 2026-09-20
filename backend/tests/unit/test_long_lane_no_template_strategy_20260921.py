# -*- coding: utf-8 -*-
"""[轮151 2026-09-21] long 车道不再从模板族建策略（止住 tpl_long_* 空转）。

## 实测缺陷
补建路径 `try_create_from_template` **优先从模板创建** ⇒ 铸出 `tpl_long_swing_*`，
又被轮40 的「模板族只做 mid」在执行层拒掉（`proposal_execution`）：
```
01:51…02:19 [Agent独立] UNI tier=long 模板族策略禁止开多（sid=tpl_long_swing）  ← 15 次/小时
[BlockCooldown] UNI tier=long 属于确定性拒绝(long_template_source_block) 不装冷却
```
且全库 `tpl_long*` 策略积压 **669 个（9 个 active）** —— 铸了又拒、拒了再铸。

## 修法
按**同一配置**（`MIDLONG_LONG_BLOCK_TEMPLATE_SOURCES`）在**建策略这一步**就跳过模板路径，
让 `auto_create_strategy` 走非模板回退（前缀不是 `tpl_` ⇒ 不再撞该规则）。
闸本身不动；只是不再制造注定被拒的策略。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.full_auto import strategy_creation as SC  # noqa: E402


class _DB:
    """记录"是否查过模板表"——用来证明 long 分支**根本没碰数据库**。

    注意：不能用"抛异常"来断言：`try_create_from_template` 内部有宽 `except`，
    异常会被吞掉（首版测试因此两条假通过 → 已改为记录式）。
    """

    def __init__(self):
        self.queried = False

    def query(self, *a, **k):
        self.queried = True
        raise RuntimeError("stop-after-query")   # 只要走到查询就够，不需要真返回数据


def test_long_tier_skips_template_creation_without_db_access(monkeypatch):
    monkeypatch.delenv("MIDLONG_LONG_BLOCK_TEMPLATE_SOURCES", raising=False)
    db = _DB()
    out = SC.try_create_from_template(db, "UNI", "long",
                                      account_id=188, risk_level="mid", trading_mode="paper")
    assert out is None
    assert db.queried is False, "long 车道必须**在查模板表之前**返回（否则又会铸出 tpl_long_*）"


def test_long_tier_respects_switch_off(monkeypatch):
    """关掉轮40 的规则时，long 允许再用模板族（会去查模板表）。"""
    import backend.config.settings as S

    monkeypatch.setattr(S, "MIDLONG_LONG_BLOCK_TEMPLATE_SOURCES", False, raising=False)
    db = _DB()
    SC.try_create_from_template(db, "UNI", "long",
                                account_id=188, risk_level="mid", trading_mode="paper")
    assert db.queried is True, "开关关闭时不应跳过模板路径"


def test_mid_tier_still_uses_templates(monkeypatch):
    """mid 车道照旧走模板优先（轮40 只限制 long）。"""
    db = _DB()
    SC.try_create_from_template(db, "UNI", "mid",
                                account_id=188, risk_level="mid", trading_mode="paper")
    assert db.queried is True, "mid 车道不应被跳过"


def test_source_guard_long_skip_present():
    src = (ROOT / "backend/services/full_auto/strategy_creation.py").read_text(
        encoding="utf-8", errors="replace")
    assert "long 车道跳过模板族建策略" in src, "跳过分支被摘掉了"
    i_skip = src.find("long 车道跳过模板族建策略")
    i_query = src.find("db.query(StrategyTemplate)")
    assert 0 < i_skip < i_query, "跳过必须发生在查询模板表**之前**（否则仍会建）"
