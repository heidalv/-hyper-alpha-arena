# -*- coding: utf-8 -*-
"""[验收轮4 2026-09-14] AI 选币槽位统计修复契约测试。

背景（用户观察「升级 AI 选币后没有任何成交」的机制之一）：
开启 AI 选币后，槽位统计把所有 tier=mid 持仓都当成"AI 中线仓"——而 9/5 起
LLM 主脑在**固定币**（UNI/ASTER/BTC/BNB）上开 mid 仓，固定币仓误占 AI 槽位
（open=4 > max=3 → ai_mid_slot_full）→ AI 候选永不注入。
README 契约：固定交易对与 AI 选币是两张独立表，固定币不占 AI 选币槽位。
长线路径（§5903）已是该口径，中线补齐。

契约：
- exclude_symbols（固定币集合）的 mid 持仓不计入 AI 槽位。
- 非固定币的 mid 持仓照常计入。
- 不传 exclude_symbols 时旧行为（全量计数）可复现。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))


class _Q:
    def __init__(self, rows):
        self._rows = rows
        self.last_sql = ""
        self.last_params = {}

    def scalar(self):
        return self._rows[0] if self._rows else 0


class _Db:
    def __init__(self, count):
        self._count = count
        self.execs = []

    def execute(self, sql, params=None):
        self.execs.append((str(sql), dict(params or {})))
        return _Q([self._count])

    def connection(self):
        return self

    def exec_driver_sql(self, s):
        pass

    def close(self):
        pass


def test_exclude_symbols_adds_not_in_filter():
    import backend.services.auto_coin_selector as acs

    db = _Db(0)
    n = acs.count_open_ai_mid_positions(
        db=db, account_id=14,
        exclude_symbols=["BTC", "ETH", "SOL", "BNB", "VIRTUAL", "ASTER", "XPL", "UNI", "XRP"],
    )
    assert n == 0
    sql = " ".join(e[0] for e in db.execs)
    assert "NOT IN :excl" in sql
    params = [e[1] for e in db.execs if "excl" in e[1]]
    assert params and "BTC" in params[0]["excl"]


def test_no_exclude_keeps_legacy_semantics():
    import backend.services.auto_coin_selector as acs

    db = _Db(4)
    n = acs.count_open_ai_mid_positions(db=db, account_id=14)
    assert n == 4
    sql = " ".join(e[0] for e in db.execs)
    assert "NOT IN :excl" not in sql


def test_empty_exclude_equivalent_to_none():
    import backend.services.auto_coin_selector as acs

    db = _Db(4)
    n = acs.count_open_ai_mid_positions(db=db, account_id=14, exclude_symbols=[])
    assert n == 4
    sql = " ".join(e[0] for e in db.execs)
    assert "NOT IN :excl" not in sql
