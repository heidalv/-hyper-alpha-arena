# -*- coding: utf-8 -*-
"""[2026-09-24 新目标 ①] 活主脑 prompt 落盘归档单测。

背景：`data/prompt_archives/trend_agent` 自 2026-08-17 停跑（台账 §45），此后"模型实际看到什么"
只能靠离线重建；R33/R34 更证明只看指标名会误判（`kline` 命中了分析师评分键 `kline_deep`）。
归档后可直接从磁盘核对上下文块原文与长度。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.services.mlto import brain as B


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "_CONTEXT_AUDIT_TS", {}, raising=False)
    monkeypatch.setenv("MIDLONG_BRAIN_PROMPT_AUDIT", "true")
    monkeypatch.setenv("MIDLONG_BRAIN_PROMPT_ARCHIVE", "true")
    monkeypatch.setenv("MIDLONG_BRAIN_PROMPT_ARCHIVE_DIR", str(tmp_path / "prompt_archives" / "brain"))
    monkeypatch.delenv("MIDLONG_BRAIN_PROMPT_ARCHIVE_MAX_BYTES", raising=False)
    monkeypatch.delenv("MIDLONG_BRAIN_PROMPT_ARCHIVE_DAYS", raising=False)
    yield


def _files():
    d = Path(B._prompt_archive_dir())
    return sorted(d.rglob("*.json")) if d.exists() else []


def test_switch_and_defaults(monkeypatch):
    assert B._prompt_archive_enabled() is True
    assert B._prompt_archive_max_bytes() == 400000
    assert B._prompt_archive_days() == 7
    monkeypatch.setenv("MIDLONG_BRAIN_PROMPT_ARCHIVE", "false")
    assert B._prompt_archive_enabled() is False


def test_archive_writes_payload_with_scope():
    B._audit_context_prompt("BTC", "long", '{"K线":1,"RSI":55}')
    fs = _files()
    assert len(fs) == 1, fs
    obj = json.loads(fs[0].read_text(encoding="utf-8"))
    assert obj["symbol"] == "BTC" and obj["tier"] == "long"
    assert obj["prompt_chars"] == len('{"K线":1,"RSI":55}')
    assert "context_pack.to_prompt_text" in obj["scope"], "必须写明归档范围，避免被当成整条 prompt"
    assert obj["truncated"] is False
    assert "RSI" in obj["text"]


def test_archive_truncates_at_cap(monkeypatch):
    monkeypatch.setenv("MIDLONG_BRAIN_PROMPT_ARCHIVE_MAX_BYTES", "10")
    B._audit_context_prompt("ETH", "mid", "X" * 100)
    obj = json.loads(_files()[0].read_text(encoding="utf-8"))
    assert obj["truncated"] is True
    assert obj["archived_chars"] == 10
    assert obj["prompt_chars"] == 100


def test_archive_disabled_writes_nothing(monkeypatch):
    monkeypatch.setenv("MIDLONG_BRAIN_PROMPT_ARCHIVE", "false")
    B._audit_context_prompt("BTC", "long", "payload")
    assert _files() == []


def test_no_archive_when_audit_throttled():
    """节流生效时第二次调用不该再落盘（归档沿用体检节流点）。"""
    B._audit_context_prompt("BTC", "long", "one")
    B._audit_context_prompt("BTC", "long", "two")
    assert len(_files()) == 1


def test_prune_keeps_newest_days(tmp_path, monkeypatch):
    base = tmp_path / "prompt_archives" / "brain"
    for n in ("20260101", "20260102", "20260103", "notadate"):
        (base / n).mkdir(parents=True)
        (base / n / "x.json").write_text("{}", encoding="utf-8")
    removed = B._prune_prompt_archive(str(base), 2)
    left = sorted(p.name for p in base.iterdir())
    assert removed == 1
    assert "20260101" not in left and "20260103" in left
    assert "notadate" in left, "非 YYYYMMDD 目录一律不动"


def test_prune_refuses_outside_prompt_archives(tmp_path):
    other = tmp_path / "important"
    (other / "20260101").mkdir(parents=True)
    assert B._prune_prompt_archive(str(other), 1) == 0
    assert (other / "20260101").exists()


def test_archive_failure_does_not_raise(tmp_path, monkeypatch):
    """目录不可创建时必须 fail-safe（只 debug，不影响主流程）。"""
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file", encoding="utf-8")   # 用文件占位 ⇒ makedirs 必失败
    monkeypatch.setenv("MIDLONG_BRAIN_PROMPT_ARCHIVE_DIR", str(blocker / "sub"))
    B._audit_context_prompt("BTC", "long", "payload")  # 不应抛异常
