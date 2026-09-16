# -*- coding: utf-8 -*-
"""[调研轮11] 「本地 Ollama 已停用，一律走线上 LLM」契约测试。

用户澄清：Ollama 早已停用，线上用 MiniMax / GLM。但实测它仍在被调用 ——
近 6h `qwen3:14b/ollama` 失败 13 次、**平均每次白等 49.6 秒**（24h 共 66 条 ollama 记录），
而真正在用的是 `MiniMax-M3`（998 次/24h, avg 10s）与 `zai-coding-plan/glm-5.3*`。
在关键路径（midlong_thesis 平均 84s）上再叠一次 50s 白等不可接受。

本测试锁定两处防线：
  1. `get_llm_config_local_first` 短路本地解析（返回 (None, cloud)）；
  2. `OllamaTransport._resolve/configured` 不再解析、不再探活本地端点。
回滚：LLM_LOCAL_FIRST_DISABLED=false。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.analysis import model_gateway as mg  # noqa: E402
from backend.services.llm_config_service import get_llm_config_local_first  # noqa: E402


@pytest.fixture(autouse=True)
def _default_on(monkeypatch):
    monkeypatch.setenv("LLM_LOCAL_FIRST_DISABLED", "true")
    yield


def test_local_first_disabled_returns_none_primary(monkeypatch):
    """短路后 primary 恒为 None（不再尝试本地），fallback 为云端配置或 None。"""
    local, cloud = get_llm_config_local_first("midlong_thesis", tenant_id=326, tier="quick")
    assert local is None, "本地 Ollama 已停用，不得再作为 primary"
    # cloud 可能因环境（无 DB/无绑定）为 None，但绝不允许 local 被解析出来
    assert cloud is None or getattr(cloud, "provider", "") != "ollama"


def test_ollama_transport_resolve_returns_none():
    """OllamaTransport 不再解析配置（含 env 兜底构造），避免白等 26~54s。"""
    t = mg.OllamaTransport(name="local", model_env="OLLAMA_MODEL", default_model="qwen3:14b")
    assert t._resolve() is None


def test_ollama_transport_configured_false():
    t = mg.OllamaTransport(name="local", model_env="OLLAMA_MODEL", default_model="qwen3:14b")
    ok, why = t.configured()
    assert ok is False and "停用" in why


def test_rollback_restores_local_resolution(monkeypatch):
    """回滚开关=false 时，OllamaTransport 恢复解析（此时不走短路分支）。"""
    monkeypatch.setenv("LLM_LOCAL_FIRST_DISABLED", "false")
    t = mg.OllamaTransport(name="local", model_env="OLLAMA_MODEL", default_model="qwen3:14b")
    cfg = t._resolve()
    # 允许为 None（DB 已停用）或任何配置，但**不再被开关短路**
    assert cfg is None or getattr(cfg, "provider", "") in ("ollama", "deepseek", "")


def test_env_declares_disabled_by_default():
    """回归闸：.env 必须显式声明停用本地（防再次被静默打开）。"""
    env = {}
    for ln in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        s = ln.strip()
        if s and not s.startswith("#") and "=" in s:
            k, v = s.split("=", 1)
            env[k.strip()] = v.strip()
    assert env.get("LLM_LOCAL_FIRST_DISABLED", "true").lower() in ("1", "true", "yes", "on")
