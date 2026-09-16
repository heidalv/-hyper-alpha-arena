# -*- coding: utf-8 -*-
"""[2026-09-16 调研轮8] AI 选币链路修复契约测试。

修的是「AI 选了不下单」的三段断点：
  1. 扫描配额：AI 候选与固定币共用游标 ⇒ 2026-09-04 关闭 AI 中线候选 ⇒ AI 永不参与扫描。
     修复 = `plan_mid_scan_batch` 拆独立配额（本测试覆盖）。
  2. 槽位假满：`get_fixed_symbols_for_session` 异常返回空集 ⇒ "全部 mid 减空集" 把固定币
     仓算成 AI 槽位 ⇒ `ai_mid_slot_full`。修复 = `count_open_ai_mid_positions` 支持
     `include_symbols` 正向识别（本测试覆盖签名与 settings 契约）。
  3. 总闸：`AUTO_COIN_ENABLED=false` 使 AutoCoinScheduler 不启动、平台看板停摆。
     修复 = .env 打开（本测试做**回归闸**，防止再被关掉）。
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.config import settings  # noqa: E402
from backend.services.auto_coin_selector import count_open_ai_mid_positions  # noqa: E402
from backend.services.full_auto.loops.midlong_loop import plan_mid_scan_batch  # noqa: E402


# ───────────────── 1) 扫描配额拆分 ─────────────────

def test_ai_gets_dedicated_slot_and_fixed_keeps_rest():
    """batch=3、ai_slots=1：固定币拿 2 个名额，AI 拿 1 个（互不挤占）。"""
    plan = plan_mid_scan_batch(
        mid_universe=["ETH", "BTC", "SOL", "BNB", "FET", "AMAT"],
        ai_scan=["FET", "AMAT"],
        fixed_mid={"ETH", "BTC", "SOL", "BNB"},
        batch_n=3, ai_slots=1,
    )
    assert len(plan["batch"]) == 3
    assert sum(1 for s in plan["batch"] if s in ("FET", "AMAT")) == 1, plan["batch"]
    assert sum(1 for s in plan["batch"] if s in ("ETH", "BTC", "SOL", "BNB")) == 2, plan["batch"]


def test_fixed_rotation_not_displaced_across_ticks():
    """固定币游标独立推进：连续 4 个 tick 能覆盖全部 4 个固定币（验证不被 AI 挤占）。"""
    fixed = ["ETH", "BTC", "SOL", "BNB"]
    universe = fixed + ["FET"]
    seen_fixed = []
    fix_cur = ai_cur = 0
    for _ in range(4):
        plan = plan_mid_scan_batch(
            mid_universe=universe, ai_scan=["FET"], fixed_mid=set(fixed),
            batch_n=3, ai_slots=1, fix_cursor=fix_cur, ai_cursor=ai_cur,
        )
        seen_fixed += [s for s in plan["batch"] if s in fixed]
        fix_cur, ai_cur = plan["next_fix_cursor"], plan["next_ai_cursor"]
    assert set(seen_fixed) == set(fixed), f"固定币轮转被挤占: {seen_fixed}"


def test_ai_slots_zero_is_legacy_behaviour():
    """ai_slots=0 → 不扫 AI（等价 2026-09-04 旧行为，可回滚）。"""
    plan = plan_mid_scan_batch(
        mid_universe=["ETH", "BTC", "SOL", "FET"], ai_scan=["FET"],
        fixed_mid={"ETH", "BTC", "SOL"}, batch_n=3, ai_slots=0,
    )
    assert "FET" not in plan["batch"]
    assert len(plan["batch"]) == 3


def test_ai_dedup_against_fixed_and_empty_inputs():
    """AI 名单与固定币重名要剔除；空 AI 名单不影响固定币扫描。"""
    plan = plan_mid_scan_batch(
        mid_universe=["ETH", "BTC", "FET"], ai_scan=["ETH", "FET"],
        fixed_mid={"ETH", "BTC"}, batch_n=3, ai_slots=1,
    )
    assert plan["batch"].count("ETH") == 1
    assert "FET" in plan["batch"]
    plan2 = plan_mid_scan_batch(
        mid_universe=["ETH", "BTC"], ai_scan=[], fixed_mid={"ETH", "BTC"},
        batch_n=3, ai_slots=1,
    )
    assert sorted(plan2["batch"]) == ["BTC", "ETH"]


def test_ai_slots_larger_than_batch_is_clamped():
    plan = plan_mid_scan_batch(
        mid_universe=["ETH", "BTC", "FET", "AMAT"], ai_scan=["FET", "AMAT"],
        fixed_mid={"ETH", "BTC"}, batch_n=2, ai_slots=5,
    )
    assert len(plan["batch"]) <= 2, plan["batch"]


# ───────────────── 2) 槽位正向识别 ─────────────────

def test_count_open_ai_mid_positions_accepts_include_symbols():
    params = inspect.signature(count_open_ai_mid_positions).parameters
    assert "include_symbols" in params, "缺少正向识别参数 → 固定币集合异常时仍会假满"


def test_include_takes_precedence_documented():
    doc = inspect.getdoc(count_open_ai_mid_positions) or ""
    assert "include_symbols" in doc and "exclude" in doc


# ───────────────── 3) 开关与 .env 回归闸 ─────────────────

def test_scan_slots_setting_declared():
    assert hasattr(settings, "MIDLONG_MID_AI_SCAN_SLOTS")
    assert int(settings.MIDLONG_MID_AI_SCAN_SLOTS) >= 0


def _env_map() -> dict:
    out = {}
    for ln in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        s = ln.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def test_env_ai_coin_chain_enabled():
    """回归闸：AI 选币三段总闸不得再被静默关掉（关了就等于「选了不下单」）。"""
    env = _env_map()
    assert env.get("AUTO_COIN_ENABLED", "").lower() in ("1", "true", "yes", "on"), \
        "AUTO_COIN_ENABLED 未开 → AutoCoinScheduler/看板停摆"
    assert env.get("MIDLONG_MID_AI_CANDIDATES_ENABLED", "").lower() in ("1", "true", "yes", "on"), \
        "MIDLONG_MID_AI_CANDIDATES_ENABLED 未开 → 中线不扫 AI 候选"
    assert int(env.get("MIDLONG_MID_AI_SCAN_SLOTS", "1")) >= 1, \
        "MIDLONG_MID_AI_SCAN_SLOTS=0 → AI 候选无扫描配额"
