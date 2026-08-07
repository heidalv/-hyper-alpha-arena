"""Unit tests for P3 M4 — decision_arbiter.log_close_request."""

import json
import os

import pytest

from backend.services import decision_arbiter as da
from backend.services.decision_arbiter import CloseRequest


@pytest.fixture
def isolated_log(tmp_path, monkeypatch):
    """把日志目标重定向到 tmp 目录，避免污染真实 data/."""
    log_path = tmp_path / "decision_arbiter.jsonl"
    monkeypatch.setattr(da, "_LOG_REL_PATH", str(log_path))
    yield log_path


def test_disabled_writes_nothing(isolated_log, monkeypatch):
    monkeypatch.setattr(da, "_is_enabled", lambda: False)
    da.log_close_request(CloseRequest(
        symbol="ETH", source="master", reason_intended="master_running_reduce",
    ))
    assert not isolated_log.exists() or isolated_log.read_text() == ""


def test_enabled_writes_one_line(isolated_log, monkeypatch):
    monkeypatch.setattr(da, "_is_enabled", lambda: True)
    da.log_close_request(CloseRequest(
        symbol="ETH", source="master", reason_intended="master_running_reduce",
        pos_tier="long", pnl_pct=-0.03, would_block=True, block_rule="m1_shadow",
    ))
    txt = isolated_log.read_text(encoding="utf-8").strip().splitlines()
    assert len(txt) == 1
    row = json.loads(txt[0])
    assert row["symbol"] == "ETH"
    assert row["source"] == "master"
    assert row["would_block"] is True
    assert row["block_rule"] == "m1_shadow"
    assert "ts" in row


def test_multiple_writes_append(isolated_log, monkeypatch):
    monkeypatch.setattr(da, "_is_enabled", lambda: True)
    for i in range(3):
        da.log_close_request(CloseRequest(
            symbol=f"SYM{i}", source="ai_reverse",
            reason_intended="ai_reverse",
        ))
    lines = isolated_log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    syms = [json.loads(l)["symbol"] for l in lines]
    assert syms == ["SYM0", "SYM1", "SYM2"]


def test_write_failure_swallowed(isolated_log, monkeypatch):
    """目标目录不存在时，不抛异常。"""
    monkeypatch.setattr(da, "_is_enabled", lambda: True)
    monkeypatch.setattr(da, "_LOG_REL_PATH", "/no/such/dir/file.jsonl")
    # 不抛异常即为通过
    da.log_close_request(CloseRequest(symbol="X", source="master", reason_intended="r"))


def test_read_recent_lines_roundtrip(isolated_log, monkeypatch):
    monkeypatch.setattr(da, "_is_enabled", lambda: True)
    for i in range(5):
        da.log_close_request(CloseRequest(
            symbol=f"S{i}", source="master", reason_intended="r",
        ))
    # read_recent_lines 使用的是模块级 _LOG_REL_PATH，tmp 已被 fixture 替换
    out = da.read_recent_lines(n=3)
    assert len(out) == 3
    assert [r["symbol"] for r in out] == ["S2", "S3", "S4"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
