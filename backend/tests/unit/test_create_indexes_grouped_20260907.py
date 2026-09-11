# -*- coding: utf-8 -*-
"""create_missing_indexes 按库分组，缺表不刷 WARNING。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from backend.database import query_optimizer as qo


def test_create_missing_indexes_groups_and_skips_quietly():
    logs = []

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def begin(self):
            return self

        def commit(self):
            return None

        def rollback(self):
            return None

        def execute(self, sql):
            s = str(sql)
            if "paper_orders" in s:
                raise Exception('UndefinedTable) 关系 "paper_orders" 不存在')
            return None

    engine = MagicMock()
    engine.connect.return_value = _Conn()

    with patch.object(qo, "logger") as lg:
        lg.debug.side_effect = lambda *a, **k: logs.append(("debug", a))
        lg.warning.side_effect = lambda *a, **k: logs.append(("warning", a))
        lg.info.side_effect = lambda *a, **k: logs.append(("info", a))
        qo.create_missing_indexes(engine, group="market")

    # market 组不含 paper_orders；不应出现 paper_orders WARNING
    warn_msgs = [str(a) for level, a in logs if level == "warning"]
    assert not any("paper_orders" in m for m in warn_msgs), warn_msgs
