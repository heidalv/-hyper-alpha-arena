"""pair_selector_watcher 调度入口单测。"""
from __future__ import annotations


def test_watcher_disabled_short_circuits(monkeypatch):
    monkeypatch.setenv("PAIR_SELECTOR_WATCHER_ENABLED", "0")
    from backend.services.scalp import pair_selector_watcher as w

    report = w.run_pair_selector_watcher()
    assert report.get("skipped") is True


def test_watcher_empty_symbols(monkeypatch):
    monkeypatch.setenv("PAIR_SELECTOR_WATCHER_ENABLED", "true")
    from backend.services.scalp import pair_selector_watcher as w

    monkeypatch.setattr(w, "_active_auto_symbols", lambda: [])
    monkeypatch.setattr(
        "backend.services.scalp.scalp_heartbeat.touch",
        lambda *a, **k: None,
    )
    report = w.run_pair_selector_watcher()
    assert report["checked"] == 0
    assert report["started"] == []


def test_watcher_starts_one_unprocessed_symbol(monkeypatch):
    monkeypatch.setenv("PAIR_SELECTOR_WATCHER_ENABLED", "true")
    from backend.services.scalp import pair_selector_watcher as w

    started = []

    class _T:
        def __init__(self, target=None, args=None, daemon=None, name=None):
            self._args = args or ()

        def start(self):
            started.append(self._args[0] if self._args else None)
            # 不真正跑 worker，直接清 processing
            with w._lock:
                w._processing.discard(self._args[0])

    monkeypatch.setattr(w, "_active_auto_symbols", lambda: ["DOGE", "ENA"])
    monkeypatch.setattr(w.threading, "Thread", _T)
    monkeypatch.setattr(
        "backend.services.scalp.pair_selector.processed_within_hours",
        lambda sym, hours=24: False,
    )
    monkeypatch.setattr(
        "backend.services.scalp.pair_selector.auto_promote_best",
        lambda sym: None,
    )
    monkeypatch.setattr(
        "backend.services.scalp.scalp_heartbeat.touch",
        lambda *a, **k: None,
    )
    with w._lock:
        w._processing.clear()

    report = w.run_pair_selector_watcher()
    assert report["checked"] == 2
    assert report["started"] == ["DOGE"]  # 每 tick 只启一个
    assert started == ["DOGE"]
