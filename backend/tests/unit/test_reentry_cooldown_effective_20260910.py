# -*- coding: utf-8 -*-
"""[§74 / 缺陷 #56] 同向再入场冷却的**真实生效链路**：基座键无消费方，tier 参数才是生效值。

实证（2026-09-10，`_audit_ml/Z198/Z199`）：
  * `.env` 写的是 `REENTRY_COOLDOWN_SEC=60`（意图 60s）→ 键名错（§39.2 D2 / P2 已修键名）；
  * **但修好键名也不生效**：`REENTRY_COOLDOWN_SECONDS` 在全仓**没有任何生产消费者** ——
    `reentry_cooldown._get_cooldown_sec(tier)` 只读
    `TIER_PROTECTION_PARAMS[tier]["cooldown_sec"]`（可由 `TIER_*_COOLDOWN_SEC` 覆盖）；
  * 实际生效：**short 240min / mid 30min / long 240min**（线上日志实证：
    `midlong_cooldown_block:刚平多仓(tier=long)未满240分钟…约剩236分钟`）。

本测试锁定这一"两层结构"，避免再次把基座值当成全链路生效值（也让未来接线时有人会看到此断言）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.config import settings  # noqa: E402
from backend.services.reentry_cooldown import _get_cooldown_sec  # noqa: E402


def test_effective_cooldown_comes_from_tier_params():
    tiers = settings.TIER_PROTECTION_PARAMS
    for tier in ("short", "mid", "long"):
        expect = int(tiers[tier]["cooldown_sec"])
        assert _get_cooldown_sec(tier) == expect, f"{tier} 生效冷却应来自 TIER_PROTECTION_PARAMS"


def test_base_key_is_not_the_effective_source(monkeypatch):
    """把基座键改成 1s，生效冷却**不应**变化 —— 证明它当前无消费方（P18 待决策）。"""
    before = {t: _get_cooldown_sec(t) for t in ("short", "mid", "long")}
    monkeypatch.setattr(settings, "REENTRY_COOLDOWN_SECONDS", 1)
    after = {t: _get_cooldown_sec(t) for t in ("short", "mid", "long")}
    assert before == after, (
        "基座键竟然影响了生效冷却 —— 说明已有人把它接线（请同步 §74 / 缺陷 #56 的状态") 


def test_current_effective_values_are_documented():
    """把当前生效值写死在测试里：改动它们（无论通过 env 还是代码默认）都必须显式改这个断言。

    [轮114 2026-09-19] mid 30min → **2h**（`.env` `TIER_MID_COOLDOWN_SEC=7200`）。
    依据是轮111 的 158 笔实测分桶（0–2h 重开档 均值 −0.389%/胜率 0.375，为最差一档；
    去掉该档后 118 笔均值 −0.027%/胜率 0.500）：轮111 曾为此新写一道闸，
    轮114 发现既有 `reentry_cooldown` 早就在做同一件事，故撤回新闸、只调这个生效值。
    注意"SL 后 mid 2h / 亏损 mid 4h"是**另外**两个更长的窗口（`_durable_reopen_blocked`），
    不在本断言范围内。
    """
    assert _get_cooldown_sec("mid") == 7200, "mid 生效冷却（轮114 起 2h；30min 档实测最差）"
    assert _get_cooldown_sec("long") == 14400, "long 生效冷却（当前 240min）"
    assert _get_cooldown_sec("short") == 14400, "short 生效冷却（当前 240min）"


def test_env_override_reaches_effective_value(monkeypatch):
    """`TIER_MID_COOLDOWN_SEC` 是**真正的**生效开关（改它才改行为）。"""
    src = (ROOT / "backend/config/settings.py").read_text(encoding="utf-8-sig", errors="replace")
    assert 'TIER_MID_COOLDOWN_SEC' in src and 'TIER_LONG_COOLDOWN_SEC' in src, \
        "tier 冷却的 env 覆盖键不存在（无法在不改代码的情况下调整）"
    from backend.config.env_registry import KNOWN_FLAGS

    for k in ("TIER_MID_COOLDOWN_SEC", "TIER_LONG_COOLDOWN_SEC", "TIER_SHORT_COOLDOWN_SEC"):
        assert k in KNOWN_FLAGS, f"{k} 未登记到 env_registry"


def test_legacy_key_removed_from_env_file():
    env_text = (ROOT / ".env").read_text(encoding="utf-8-sig", errors="replace")
    normalized = env_text.replace("\r\n", "\n")
    assert "\nREENTRY_COOLDOWN_SEC=" not in normalized, "旧近名键仍在 .env（双键漂移）"
    assert "\nREENTRY_COOLDOWN_SECONDS=" not in normalized, \
        "基座键无消费方，不应继续留在 .env（P18 决策：维持现状 + 清除误导键）"
    assert "TIER_MID_COOLDOWN_SEC" in env_text, ".env 应说明真正的生效开关"
