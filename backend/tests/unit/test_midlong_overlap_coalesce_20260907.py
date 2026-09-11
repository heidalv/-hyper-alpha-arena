# -*- coding: utf-8 -*-
"""mid brain overlapping：抢锁失败应 coalesce，而非整轮丢弃。"""
from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import patch

from backend.services.mlto import brain as brain_mod


def test_overlapping_batch_coalesces_into_pending():
    brain_mod._PENDING_BATCH_SYMS.clear()
    lock = threading.Lock()
    lock.acquire()
    try:
        with patch.object(brain_mod, "_named_lock", return_value=lock):
            out = brain_mod.run_midlong_brain_batch(
                host=SimpleNamespace(),
                session=SimpleNamespace(session_id="fa_test"),
                symbols=["BTC", "ETH"],
                tier="mid",
                market_summary={},
            )
        assert out == []
        key = "fa_test:mid"
        assert brain_mod._PENDING_BATCH_SYMS.get(key) == {"BTC", "ETH"}
    finally:
        lock.release()
        brain_mod._PENDING_BATCH_SYMS.clear()
