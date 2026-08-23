"""S0-7 选币轮换最小持有期 单元测试（2026-08-23）。

验证改动 2（选币最小持有期）的纯函数部分：
  - ``AUTO_COIN_MIN_HOLD_SEC`` 默认 21600（6h），env 可覆盖。
  - ``record_universe_added`` / ``get_universe_added_at`` 记录加入 universe 时间。
  - ``rotation_remove_allowed`` 满期才允许轮换移除；未满期跳过（币留在 universe）。
  - ``record_universe_added_if_absent`` 不覆盖更晚的注入时间。
  - ``clear_universe_added_state`` 清空状态（单测隔离）。

说明：``evaluate_auto_symbols``（轮换撤币路径）依赖 DB 会话与完整选币流程，难以
直接单测；本测试聚焦其调用的纯函数 ``rotation_remove_allowed``，接线位置见
auto_coin_selector.py ``evaluate_auto_symbols`` 内 "S0-7：轮换撤币最小持有期" 分支。
数据失效/退市移除（``_sync_active_pool_from_db`` 目录兜底过滤）不走该门，保持立即执行。

运行：
  cd backend && python -m pytest tests/unit/test_auto_coin_min_hold_20260823.py -q
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest


def _acs():
    import backend.services.auto_coin_selector as acs
    return acs


@pytest.fixture(autouse=True)
def _clean_state():
    _acs().clear_universe_added_state()
    yield
    _acs().clear_universe_added_state()


class TestMinHoldConfig:
    def test_default_min_hold_sec_is_6h(self):
        """AUTO_COIN_MIN_HOLD_SEC 默认 21600 秒（6h）；env 未覆盖时取默认。"""
        acs = _acs()
        assert isinstance(acs.AUTO_COIN_MIN_HOLD_SEC, int)
        assert acs.AUTO_COIN_MIN_HOLD_SEC == 21600

    def test_min_hold_sec_is_positive(self):
        acs = _acs()
        assert acs.AUTO_COIN_MIN_HOLD_SEC > 0


class TestRecordUniverseAdded:
    def test_record_and_get(self):
        acs = _acs()
        acs.record_universe_added("BTC", 1000.0)
        assert acs.get_universe_added_at("BTC") == 1000.0

    def test_symbol_normalized_upper(self):
        acs = _acs()
        acs.record_universe_added("btc", 1000.0)
        assert acs.get_universe_added_at("BTC") == 1000.0
        assert acs.get_universe_added_at(" btc ") == 1000.0

    def test_record_with_datetime(self):
        acs = _acs()
        dt = datetime(2026, 8, 23, 12, 0, 0, tzinfo=timezone.utc)
        acs.record_universe_added("ETH", dt)
        assert acs.get_universe_added_at("ETH") == dt.timestamp()

    def test_record_default_now(self):
        acs = _acs()
        import time
        before = time.time()
        acs.record_universe_added("SOL")
        after = time.time()
        assert before <= acs.get_universe_added_at("SOL") <= after

    def test_record_if_absent_does_not_overwrite(self):
        acs = _acs()
        acs.record_universe_added("DOGE", 1000.0)
        acs.record_universe_added_if_absent("DOGE", 2000.0)
        assert acs.get_universe_added_at("DOGE") == 1000.0

    def test_record_if_absent_fills_empty(self):
        acs = _acs()
        acs.record_universe_added_if_absent("XRP", 3000.0)
        assert acs.get_universe_added_at("XRP") == 3000.0


class TestRotationRemoveAllowed:
    def test_allowed_when_hold_met(self):
        acs = _acs()
        acs.record_universe_added("BTC", 1000.0)
        # 恰好满 21600 秒（默认 6h）→ 允许
        assert acs.rotation_remove_allowed("BTC", now=1000.0 + 21600) is True

    def test_blocked_when_hold_not_met(self):
        acs = _acs()
        acs.record_universe_added("BTC", 1000.0)
        assert acs.rotation_remove_allowed("BTC", now=1000.0 + 21599) is False
        assert acs.rotation_remove_allowed("BTC", now=1000.0) is False

    def test_custom_min_hold_sec(self):
        acs = _acs()
        acs.record_universe_added("BTC", 1000.0)
        assert acs.rotation_remove_allowed("BTC", now=1000.0 + 3600, min_hold_sec=3600) is True
        assert acs.rotation_remove_allowed("BTC", now=1000.0 + 3599, min_hold_sec=3600) is False

    def test_no_record_allowed(self):
        """无加入记录（进程重启状态丢失）时放行，避免轮换永久卡死。"""
        acs = _acs()
        assert acs.rotation_remove_allowed("BTC", now=2000.0) is True

    def test_empty_symbol_allowed(self):
        acs = _acs()
        assert acs.rotation_remove_allowed("", now=2000.0) is True
        assert acs.rotation_remove_allowed(None, now=2000.0) is True

    def test_symbol_case_insensitive(self):
        acs = _acs()
        acs.record_universe_added("btc", 1000.0)
        assert acs.rotation_remove_allowed("BTC", now=1000.0 + 21600) is True


if __name__ == "__main__":
    pytest.main([__file__, "-q", "--tb=short"])
