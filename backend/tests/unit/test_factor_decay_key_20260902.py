# -*- coding: utf-8 -*-
"""[2026-09-02] 因子衰减监控 key 错位修复单测。

实证背景：data/factor_decay_status.json 里只有 3 个键，全部是
strategy_ASTER / strategy_BTC / strategy_XPL，且全部 trend=dead /
recommendation=retire，真实因子零覆盖。成因：
  1. paper_trading_engine 平仓时调 record_ic(f"strategy_{symbol}", ±0.05)，
     key 是策略/品种名而非因子 ID → get_factor_weight_penalty(因子ID) 永远
     miss、恒返回 1.0，衰减惩罚从未对任何真实因子生效；
  2. IC 用 ±0.05 占位常数，盈亏各半时 recent≈0 < retire_ic(0.01)，把这些假
     键全判成 dead/retire；
  3. STATUS_PATH 是相对路径，非仓库根 cwd 下读不到已存状态（penalty 归零 =
     衰减因子满权复活，正是 P0-2 要防的）；
  4. P0-2 只持久化了 _decay_status，_ic_history 是纯内存态 → 重启清零后要
     重新累积 20 轮评估才能再次给出非 stable 判定。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

from backend.services.factor_engine.factor_decay_monitor import (
    FactorDecayMonitor,
    decay_monitor,
)


def test_status_path_is_absolute():
    """状态文件路径必须绝对化，不能依赖进程 cwd。"""
    assert os.path.isabs(FactorDecayMonitor.STATUS_PATH)
    assert FactorDecayMonitor.STATUS_PATH.replace("\\", "/").endswith(
        "data/factor_decay_status.json")


def test_record_ic_rejects_strategy_key(caplog):
    """strategy_* 这类非因子键必须被拒绝，防止再次污染状态文件。"""
    decay_monitor._ic_history.pop("strategy_BTC", None)
    with caplog.at_level("WARNING"):
        decay_monitor.record_ic("strategy_BTC", -0.05)
    assert "strategy_BTC" not in decay_monitor._ic_history
    assert any("record_ic 只接受因子 ID" in r.message or "拒绝非因子键" in r.message
               for r in caplog.records)


def test_record_ic_accepts_real_factor_id():
    """真实因子 ID 正常入历史。"""
    fid = "ai_gen_unit_test_probe"
    decay_monitor._ic_history.pop(fid, None)
    decay_monitor.record_ic(fid, 0.042)
    assert decay_monitor._ic_history[fid] == [0.042]
    decay_monitor._ic_history.pop(fid, None)


def test_record_ic_ignores_nan_and_blank():
    """NaN / 空 key 不入历史（否则会污染 mean/std 计算）。"""
    fid = "ai_gen_unit_test_nan"
    decay_monitor._ic_history.pop(fid, None)
    decay_monitor.record_ic(fid, float("nan"))
    decay_monitor.record_ic("", 0.1)
    decay_monitor.record_ic(fid, "not-a-number")  # type: ignore[arg-type]
    assert fid not in decay_monitor._ic_history
    assert "" not in decay_monitor._ic_history


def test_paper_engine_no_longer_records_strategy_ic():
    """paper_trading_engine 的**活代码**里不应再有 strategy_ 前缀的 record_ic。

    只扫非注释行——修复说明本身会在注释里引用原错误调用。
    """
    src_path = os.path.join(
        os.path.dirname(FactorDecayMonitor.STATUS_PATH),  # <repo>/data
        "..", "backend", "services", "paper_trading_engine.py",
    )
    with open(os.path.normpath(src_path), "r", encoding="utf-8") as f:
        lines = f.readlines()
    offenders = [
        (i + 1, ln.strip())
        for i, ln in enumerate(lines)
        if not ln.lstrip().startswith("#") and "record_ic(" in ln and "strategy_" in ln
    ]
    assert not offenders, f"平仓路径仍在按策略名写因子衰减 IC: {offenders}"


def test_status_roundtrip_persists_ic_history(tmp_path, monkeypatch):
    """v2 状态文件必须同时持久化 IC 历史，重启后不清零。"""
    target = tmp_path / "factor_decay_status.json"
    monkeypatch.setattr(FactorDecayMonitor, "STATUS_PATH", str(target))

    mon = FactorDecayMonitor()
    _saved_hist = dict(mon._ic_history)
    _saved_status = dict(mon._decay_status)
    try:
        mon._ic_history.clear()
        mon._decay_status.clear()
        for i in range(25):
            mon.record_ic("ai_gen_persist_probe", 0.05 + i * 0.001)
        mon.evaluate_all_factors()          # 内部会 _save_status

        raw = json.loads(target.read_text(encoding="utf-8"))
        assert raw["_meta"]["version"] == 2
        assert "ai_gen_persist_probe" in raw["ic_history"]
        assert len(raw["ic_history"]["ai_gen_persist_probe"]) == 25
        assert "ai_gen_persist_probe" in raw["status"]

        # 模拟重启：清空内存后重新加载
        mon._ic_history.clear()
        mon._decay_status.clear()
        mon._load_status()
        assert len(mon._ic_history.get("ai_gen_persist_probe", [])) == 25, (
            "重启后 IC 历史丢失 → 衰减评估要重新累积 20 轮才生效"
        )
    finally:
        mon._ic_history.clear()
        mon._ic_history.update(_saved_hist)
        mon._decay_status.clear()
        mon._decay_status.update(_saved_status)


def test_status_loader_backward_compatible_with_v1(tmp_path, monkeypatch):
    """v1 旧格式（顶层即 factor_id → status）必须仍能读。"""
    target = tmp_path / "old.json"
    target.write_text(json.dumps({
        "ai_gen_legacy": {"current_ic": 0.02, "historical_ic": 0.03,
                          "decay_rate": -0.1, "half_life_days": 7.0,
                          "trend": "declining", "recommendation": "reduce"},
    }), encoding="utf-8")
    monkeypatch.setattr(FactorDecayMonitor, "STATUS_PATH", str(target))

    mon = FactorDecayMonitor()
    _saved_status = dict(mon._decay_status)
    try:
        mon._decay_status.clear()
        mon._load_status()
        assert "ai_gen_legacy" in mon._decay_status
        assert mon._decay_status["ai_gen_legacy"].recommendation == "reduce"
    finally:
        mon._decay_status.clear()
        mon._decay_status.update(_saved_status)


def test_live_status_file_has_no_strategy_keys():
    """线上状态文件不应再含 strategy_* 假键。"""
    p = FactorDecayMonitor.STATUS_PATH
    if not os.path.exists(p):
        pytest.skip("状态文件尚未生成")
    raw = json.loads(open(p, encoding="utf-8").read() or "{}")
    status = raw.get("status", raw) or {}
    bad = [k for k in status if str(k).startswith("strategy_")]
    assert not bad, f"仍有 strategy_* 假键: {bad}"
