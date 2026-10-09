# -*- coding: utf-8 -*-
"""[新目标 R4] `thesis_id` 兜底解析（让不可见交易进入学习）的单测。

根因（§98/§99）：`_build_midlong_agent_envelope` 只在 `mlto_result` 存在时才写 `thesis_id`，
而 `mlto_result` 来自已下线的 MLTO orchestrator（生产零调用）⇒ 活路径信封永远没有 thesis_id
⇒ `record_outcome` 直接 `skip=no_thesis`；实测 158 笔平仓仅 46% 产生 postmortem、真实回写仅 2 笔。
"""
from __future__ import annotations

from pathlib import Path

import pytest

import backend.services.unified_learning_service as U

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("MLTO_LEARNING_THESIS_FALLBACK", raising=False)
    yield


def test_switch_default_on_and_rollback(monkeypatch):
    assert U._thesis_fallback_enabled() is True
    monkeypatch.setenv("MLTO_LEARNING_THESIS_FALLBACK", "false")
    assert U._thesis_fallback_enabled() is False
    monkeypatch.setenv("MLTO_LEARNING_THESIS_FALLBACK", "garbage")
    assert U._thesis_fallback_enabled() is False  # 非法值 fail-closed（与本仓其它开关同约定）


def test_resolver_site_guards_and_documents_approximation():
    """源码守卫：兜底必须 ① 受开关保护 ② 在原有 tag_meta 兜底之后 ③ 带近似性说明 ④ fail-safe。"""
    src = (ROOT / "backend/services/unified_learning_service.py").read_text(encoding="utf-8")
    # 锚定**调用点的注释块**（不是 helper 的定义处）：注释在前、`if ... and _thesis_fallback_enabled():` 紧随其后。
    i = src.find('# [新目标 R4] **论题兜底解析**')
    assert i >= 0, "未找到兜底解析注释块"
    seg = src[i:i + 3400]
    assert 'and _thesis_fallback_enabled():' in seg, "兜底调用点必须受开关保护"
    assert "thesis_store" in seg, "必须用 thesis_store 解析"
    assert "session_id" in seg and "symbol" in seg and "tier" in seg, "必须按 (session,symbol,tier) 解析"
    assert "近似性" in seg, "必须写明'平仓时刻论题 ≠ 开仓时刻论题'的近似性"
    assert "except Exception" in seg, "解析失败必须 fail-safe"
    # 原有 tag_meta 兜底必须保留（不能被替换掉）
    assert "source_attribution" in src and "tag_meta" in src


def test_module_imports_and_dataclass_intact():
    """回归守卫：曾经把 helper 插到 @dataclass 与 class 之间导致导入崩溃，这里锁住。"""
    assert hasattr(U, "TradeOutcome")
    import dataclasses
    assert dataclasses.is_dataclass(U.TradeOutcome)


# ── [R4 补强] 长线车道没有 session_id，必须能跨会话解析；且时效开关 fail-closed ──

def test_call_site_uses_helper_and_is_guarded():
    """源码守卫：调用点必须 ① 受开关保护 ② 走抽出来的只读 helper ③ fail-safe。"""
    src = (ROOT / "backend/services/unified_learning_service.py").read_text(encoding="utf-8")
    i = src.find('# [新目标 R4] **论题兜底解析**')
    assert i >= 0, "未找到兜底解析注释块"
    seg = src[i:i + 2600]
    assert "_resolve_fallback_thesis" in seg, "调用点必须走可单测的只读 helper"
    assert "and _thesis_fallback_enabled():" in seg, "调用点必须受开关保护"
    assert "except Exception" in seg, "解析失败必须 fail-safe"
    assert "via=" in seg, "必须记录解析路径（session / cross_session）"


def test_helper_implemented_with_cross_session_path():
    """helper 本体必须同时有 session 内与跨会话两条路径。"""
    src = (ROOT / "backend/services/unified_learning_service.py").read_text(encoding="utf-8")
    i = src.find("def _resolve_fallback_thesis(")
    assert i >= 0, "未找到 _resolve_fallback_thesis"
    body = src[i:i + 1400]
    assert "thesis_store" in body
    assert ".get(" in body, "缺 session 内解析"
    assert "find_latest" in body, "缺跨会话解析（长线车道唯一可用路径）"


def test_helper_session_then_cross_session(monkeypatch):
    """功能：session 内优先；解析不到或无 session_id（长线车道实测形态）⇒ 跨会话。

    第 3 项返回值 = **用于归因的 session_id**：跨会话时必须返回论题所属会话
    （长线车道 meta 里没有 session_id，空串会让 OWM 权重落到空会话桶）。
    """
    from types import SimpleNamespace

    from backend.services.mlto import thesis_store as TS

    out = SimpleNamespace(symbol="BTC", tier="long")
    monkeypatch.setattr(TS, "get", lambda sid, sym, tier: SimpleNamespace(thesis_id="th-sess", session_id=sid))
    monkeypatch.setattr(TS, "find_latest", lambda sym, tier, **k: SimpleNamespace(
        thesis_id="th-cross", session_id="fa_live_session"))
    assert U._resolve_fallback_thesis(
        {"session_id": "fa_x", "timeframe_tier": "long"}, out
    ) == ("th-sess", "session", "fa_x", "long")

    monkeypatch.setattr(TS, "get", lambda *a, **k: None)
    assert U._resolve_fallback_thesis(
        {"session_id": "fa_x", "timeframe_tier": "long"}, out
    ) == ("th-cross", "cross_session", "fa_x", "long")
    # 长线车道真实形态：open_metadata 里既无 thesis_id 也无 session_id
    # ⇒ session 必须由**解析到的论题**带出来，否则 OWM 更新落空会话桶
    assert U._resolve_fallback_thesis({}, out) == ("th-cross", "cross_session", "fa_live_session", "long")


def test_resolver_returns_empty_when_nothing_found(monkeypatch):
    """解析不到时必须返回空串（下游据此走 mlto_block_skip=no_thesis 分支，不得乱归因）。"""
    from types import SimpleNamespace

    from backend.services.mlto import thesis_store as TS

    monkeypatch.setattr(TS, "get", lambda *a, **k: None)
    monkeypatch.setattr(TS, "find_latest", lambda *a, **k: None)
    out = SimpleNamespace(symbol="BTC", tier="long")
    assert U._resolve_fallback_thesis({}, out) == ("", "", "", "long")


def test_helper_fail_safe_and_blank_symbol(monkeypatch):
    from types import SimpleNamespace

    from backend.services.mlto import thesis_store as TS

    out = SimpleNamespace(symbol="", tier="mid")
    assert U._resolve_fallback_thesis({}, out)[0] == "", "无 symbol 必须返回空"

    def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(TS, "get", _boom)
    monkeypatch.setattr(TS, "find_latest", _boom)
    with pytest.raises(RuntimeError):
        # helper 本体不吞异常（由调用点的 try/except 兜底），这里锁住"异常可见"
        U._resolve_fallback_thesis({"session_id": "s"}, SimpleNamespace(symbol="BTC", tier="mid"))


def test_find_latest_disabled_when_max_age_zero(monkeypatch):
    """回滚开关：MLTO_THESIS_FALLBACK_MAX_AGE_H=0 ⇒ 直接返回 None，且**不碰数据库**。"""
    from backend.services.mlto import thesis_store as TS

    monkeypatch.setenv("MLTO_THESIS_FALLBACK_MAX_AGE_H", "0")
    assert TS._fallback_max_age_hours() == 0.0

    def _boom(*a, **k):  # 若真去连库就会炸，证明短路发生在连库之前
        raise AssertionError("开关关闭时不应访问数据库")

    monkeypatch.setattr(TS, "AnalyticsSessionLocal", _boom, raising=False)
    assert TS.find_latest("BTC", "long") is None


def test_find_latest_max_age_invalid_fail_closed(monkeypatch):
    from backend.services.mlto import thesis_store as TS

    monkeypatch.setenv("MLTO_THESIS_FALLBACK_MAX_AGE_H", "garbage")
    assert TS._fallback_max_age_hours() == 0.0
    monkeypatch.setenv("MLTO_THESIS_FALLBACK_MAX_AGE_H", "-3")
    assert TS._fallback_max_age_hours() == 0.0
    monkeypatch.setenv("MLTO_THESIS_FALLBACK_MAX_AGE_H", "6")
    assert TS._fallback_max_age_hours() == 6.0


def test_find_latest_rejects_blank_keys(monkeypatch):
    from backend.services.mlto import thesis_store as TS

    monkeypatch.setenv("MLTO_THESIS_FALLBACK_MAX_AGE_H", "6")
    assert TS.find_latest("", "long") is None
    assert TS.find_latest("BTC", "") is None
