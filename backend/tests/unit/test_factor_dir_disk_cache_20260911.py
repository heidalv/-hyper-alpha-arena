# -*- coding: utf-8 -*-
"""[F38e] 因子方向磁盘缓存 round-trip 契约测试（不跑因子引擎，纯缓存逻辑）。"""
import os
import time

import pytest

import backend.services.live_pipeline_backtest_engine as eng


@pytest.fixture()
def _tmp_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(eng, "_FACTOR_DIR_DISK_DIR", tmp_path)
    monkeypatch.setattr(eng, "_FACTOR_DIR_DISK_ENABLED", True)
    return tmp_path


def test_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(eng, "_FACTOR_DIR_DISK_DIR", tmp_path)
    monkeypatch.setattr(eng, "_FACTOR_DIR_DISK_ENABLED", True)
    tss = [1000, 2000, 3000, 4000, 5000]
    series = [0, 0, 1, -1, 0]
    eng._factor_dir_disk_save("BTC", "1h", tss, series)
    got = eng._factor_dir_disk_load("BTC", "1h")
    assert got is not None
    gtss, gseries = got
    assert gtss == tss and gseries == series


def test_merge_extends_tail(tmp_path, monkeypatch):
    monkeypatch.setattr(eng, "_FACTOR_DIR_DISK_DIR", tmp_path)
    monkeypatch.setattr(eng, "_FACTOR_DIR_DISK_ENABLED", True)
    tss = [1000, 2000, 3000]
    series = [0, 1, -1]
    eng._factor_dir_disk_save("BTC", "1h", tss, series)
    # 后续窗口从 2000 开始，覆盖到 4000（新增一根）
    eng._factor_dir_disk_save("BTC", "1h", [2000, 3000, 4000], [5, 6, 7])
    got = eng._factor_dir_disk_load("BTC", "1h")
    gtss, gseries = got
    # 锚点保持最早，新值按时间戳合并
    assert gtss == [1000, 2000, 3000, 4000]
    assert gseries == [0, 5, 6, 7]


def test_ttl_expired(tmp_path, monkeypatch):
    monkeypatch.setattr(eng, "_FACTOR_DIR_DISK_DIR", tmp_path)
    monkeypatch.setattr(eng, "_FACTOR_DIR_DISK_ENABLED", True)
    eng._factor_dir_disk_save("BTC", "1h", [1000], [1])
    old = time.time() - 999999
    p = eng._factor_dir_disk_path("BTC", "1h")
    os.utime(p, (old, old))
    assert eng._factor_dir_disk_load("BTC", "1h") is None


def test_disabled_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(eng, "_FACTOR_DIR_DISK_DIR", tmp_path)
    monkeypatch.setattr(eng, "_FACTOR_DIR_DISK_ENABLED", False)
    eng._factor_dir_disk_save("BTC", "1h", [1000], [1])
    assert eng._factor_dir_disk_load("BTC", "1h") is None
    assert not any(tmp_path.iterdir())
