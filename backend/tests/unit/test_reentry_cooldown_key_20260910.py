# -*- coding: utf-8 -*-
"""[P2 / §73 执行 2026-09-10] 再入场冷却键名修复的契约测试。

背景（§39.2 D2）：`.env` 里写的是 `REENTRY_COOLDOWN_SEC=60`，而代码只读
`REENTRY_COOLDOWN_SECONDS`（默认 600）⇒ **60s 的意图静默变成 600s（10 倍）**。

修法：规范键优先、旧近名键兜底并**告警**；`.env` 键名已改为规范写法；线上生效值实测 **60**。

本测试锁定解析逻辑（纯函数，避免依赖 `.env` 的实际加载）：
  1. 规范键存在 ⇒ 用规范键，且**不告警**；
  2. 只有旧键 ⇒ 用旧键，且**必须告警**（不再静默）；
  3. 两者都没有 ⇒ 默认 600；
  4. 空串按"未设"处理（防 `KEY=` 这种写法把 0/默认吃掉）。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.config.settings import _resolve_reentry_cooldown  # noqa: E402


def test_canonical_key_wins_and_is_silent(caplog):
    with caplog.at_level(logging.WARNING):
        assert _resolve_reentry_cooldown("60", "9999") == 60
    assert not [r for r in caplog.records if "REENTRY_COOLDOWN_SEC" in r.getMessage()]


def test_legacy_key_is_honored_and_warns(caplog):
    with caplog.at_level(logging.WARNING):
        assert _resolve_reentry_cooldown(None, "45") == 45
    msgs = [r.getMessage() for r in caplog.records if "REENTRY_COOLDOWN_SEC" in r.getMessage()]
    assert msgs, "旧键生效但没有任何告警 ⇒ 静默失效回归"


def test_default_when_both_absent(caplog):
    with caplog.at_level(logging.WARNING):
        assert _resolve_reentry_cooldown(None, None) == 600
        assert _resolve_reentry_cooldown("", "") == 600, "空串应视作未设"
    assert not [r for r in caplog.records if "REENTRY_COOLDOWN_SEC" in r.getMessage()]


def test_env_no_longer_sets_the_ineffective_base_key():
    """[P18 执行 2026-09-10] 该基座键**无消费方**，已从 `.env` 移除并留说明注释，
    以免运维继续改一个"改了不生效"的键（真正的开关是 `TIER_*_COOLDOWN_SEC`）。"""
    from backend.config import settings

    assert hasattr(settings, "REENTRY_COOLDOWN_SECONDS")
    env_text = (ROOT / ".env").read_text(encoding="utf-8-sig", errors="replace")
    normalized = env_text.replace("\r\n", "\n")
    assert "\nREENTRY_COOLDOWN_SEC=" not in normalized, "旧近名键仍在 .env（双键漂移）"
    assert "\nREENTRY_COOLDOWN_SECONDS=" not in normalized, \
        "基座键仍在 .env —— 它无消费方，留着会误导（应改 TIER_*_COOLDOWN_SEC）"
    assert "TIER_MID_COOLDOWN_SEC" in env_text, ".env 未说明真正的生效开关"
    # 未设基座键 ⇒ 取代码默认 600（仅作为 settings 属性存在，不影响行为）
    assert settings.REENTRY_COOLDOWN_SECONDS == 600


def test_near_miss_detector_would_have_caught_it():
    """回归：审计工具必须能识别这对近名键（否则同类问题会再次静默）。"""
    from backend.scripts.audit_config_effective import detect_near_miss

    hits = detect_near_miss(["REENTRY_COOLDOWN_SEC"], ["REENTRY_COOLDOWN_SECONDS"])
    assert hits, "近名键探测器失效"
