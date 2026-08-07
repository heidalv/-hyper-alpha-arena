"""
测试：分层置信度门控系统
验证长线仓不会被短线置信度下降误杀

测试场景矩阵：
  T1: 长线仓 + 短线暴跌但长线仍高 → 应拦截
  T2: 长线仓 + 长线也跌破门槛      → 应放行
  T3: 中线仓 + 短线暴跌但中线仍高 → 应拦截
  T4: 中线仓 + 中线跌破门槛        → 应放行
  T5: 短线仓 + 短线暴跌            → 应放行
  T6: 短线仓 + 短线仍高            → 应拦截
  T7: 长线仓 + 紧急亏损 override   → 即使置信度高也应放行
  T8: AI 强制信号 (conf>=92)       → 即使置信度高也应放行
  T9: hold→enter 覆盖用对应tier置信度而非max
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "Hyper-Alpha-Arena"))

passed = 0
failed = 0
results = []

def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        results.append(f"  ✅ {name}")
    else:
        failed += 1
        results.append(f"  ❌ {name} — {detail}")


# ═══════════ Part 1: _get_tier_confidence 提取测试 ═══════════

print("=" * 60)
print("Part 1: _get_tier_confidence 精准提取")
print("=" * 60)

from backend.services.full_auto_trading_service import FullAutoTradingService as FATS

market_summary = {
    "BTC": {
        "orchestrator": {
            "long_conf": 0.72,
            "mid_conf": 0.55,
            "short_conf": 0.15,
            "recommended_nature": "trend_follow",
            "action": "enter",
        }
    },
    "ETH": {
        "orchestrator": {
            "long_conf": 0.20,
            "mid_conf": 0.65,
            "short_conf": 0.80,
            "recommended_nature": "intraday",
        }
    }
}

# T-basic: 按 tier 提取正确字段
btc_long = FATS._get_tier_confidence(market_summary, "BTC", "long")
btc_mid = FATS._get_tier_confidence(market_summary, "BTC", "mid")
btc_short = FATS._get_tier_confidence(market_summary, "BTC", "short")
check("BTC long_conf=0.72", abs(btc_long - 0.72) < 0.001, f"got={btc_long}")
check("BTC mid_conf=0.55", abs(btc_mid - 0.55) < 0.001, f"got={btc_mid}")
check("BTC short_conf=0.15", abs(btc_short - 0.15) < 0.001, f"got={btc_short}")

eth_long = FATS._get_tier_confidence(market_summary, "ETH", "long")
eth_short = FATS._get_tier_confidence(market_summary, "ETH", "short")
check("ETH long_conf=0.20", abs(eth_long - 0.20) < 0.001, f"got={eth_long}")
check("ETH short_conf=0.80", abs(eth_short - 0.80) < 0.001, f"got={eth_short}")

# 容错：缺失 symbol
missing = FATS._get_tier_confidence(market_summary, "DOGE", "mid")
check("缺失symbol→0.0", missing == 0.0, f"got={missing}")

# 容错：None market_summary
none_ms = FATS._get_tier_confidence(None, "BTC", "long")
check("None market_summary→0.0", none_ms == 0.0, f"got={none_ms}")

# 容错：缺失 orchestrator
no_orch = FATS._get_tier_confidence({"BTC": {"price": 100}}, "BTC", "mid")
check("无orchestrator→0.0", no_orch == 0.0, f"got={no_orch}")


# ═══════════ Part 2: 门控逻辑仿真测试 ═══════════

print("\n" + "=" * 60)
print("Part 2: 分层置信度门控 — 核心场景")
print("=" * 60)

GATE = FATS._TIER_CONF_GATE  # {"long": 0.45, "mid": 0.40, "short": 0.35}

def simulate_gate(pos_tier, tier_conf, pnl_pct, ai_confidence,
                  emergency_pct=-80.0):
    """
    仿真保护层 0.5 的逻辑（不需要实例化整个服务）。
    返回 True = 拦截（不平仓），False = 放行（允许平仓）
    """
    gate_threshold = GATE.get(pos_tier, 0.40)
    if (tier_conf >= gate_threshold
            and pnl_pct > emergency_pct
            and ai_confidence < 92):
        return True   # 拦截
    return False      # 放行


# ── T1: 长线仓 + 短线暴跌但长线仍高 → 应拦截 ──
# 场景: BTC trend_follow 仓位，long_conf=0.72(高), short_conf=0.15(暴跌)
# AI 看到短线跌了想 close，但长线置信度仍然 72%
blocked = simulate_gate(
    pos_tier="long", tier_conf=0.72, pnl_pct=-2.0, ai_confidence=75)
check("T1: 长线仓+长线置信72%→拦截close", blocked is True,
      f"blocked={blocked}, 长线仓不应被短线噪音杀死")

# ── T2: 长线仓 + 长线也跌破门槛 → 应放行 ──
blocked = simulate_gate(
    pos_tier="long", tier_conf=0.30, pnl_pct=-2.0, ai_confidence=75)
check("T2: 长线仓+长线置信30%→放行close", blocked is False,
      f"blocked={blocked}, 长线趋势已反转应放行")

# ── T3: 中线仓 + 短线暴跌但中线仍高 → 应拦截 ──
blocked = simulate_gate(
    pos_tier="mid", tier_conf=0.55, pnl_pct=-1.5, ai_confidence=65)
check("T3: 中线仓+中线置信55%→拦截", blocked is True,
      f"blocked={blocked}, 中线趋势仍在")

# ── T4: 中线仓 + 中线跌破门槛 → 应放行 ──
blocked = simulate_gate(
    pos_tier="mid", tier_conf=0.35, pnl_pct=-3.0, ai_confidence=70)
check("T4: 中线仓+中线置信35%→放行", blocked is False,
      f"blocked={blocked}, 中线已转弱应放行")

# ── T5: 短线仓 + 短线暴跌 → 应放行 ──
blocked = simulate_gate(
    pos_tier="short", tier_conf=0.10, pnl_pct=-1.0, ai_confidence=60)
check("T5: 短线仓+短线置信10%→放行", blocked is False,
      f"blocked={blocked}, 短线已无信号")

# ── T6: 短线仓 + 短线仍高 → 应拦截 ──
blocked = simulate_gate(
    pos_tier="short", tier_conf=0.50, pnl_pct=-0.5, ai_confidence=60)
check("T6: 短线仓+短线置信50%→拦截", blocked is True,
      f"blocked={blocked}, 短线信号仍在")

# ── T7: 长线仓 + 紧急亏损 override → 即使置信度高也放行 ──
blocked = simulate_gate(
    pos_tier="long", tier_conf=0.72, pnl_pct=-85.0, ai_confidence=75,
    emergency_pct=-80.0)
check("T7: 长线仓+紧急亏损-85%→放行(override)", blocked is False,
      f"blocked={blocked}, 紧急亏损应突破门控")

# ── T8: AI 强制信号 (conf>=92) → 即使置信度高也放行 ──
blocked = simulate_gate(
    pos_tier="long", tier_conf=0.72, pnl_pct=-2.0, ai_confidence=95)
check("T8: AI强制信号conf=95→放行(override)", blocked is False,
      f"blocked={blocked}, AI极高置信度应突破门控")

# ── T8b: AI 不够强制 (conf=91) → 门控生效 ──
blocked = simulate_gate(
    pos_tier="long", tier_conf=0.72, pnl_pct=-2.0, ai_confidence=91)
check("T8b: AI conf=91<92→门控生效拦截", blocked is True,
      f"blocked={blocked}, conf<92不应突破门控")


# ═══════════ Part 3: 完整端到端 — tier_conf 配合 market_summary ═══════════

print("\n" + "=" * 60)
print("Part 3: 端到端联合测试 — 从 market_summary 到门控判定")
print("=" * 60)

def e2e_gate(market_summary, sym, pos_tier, pnl_pct, ai_conf, emergency=-80.0):
    """端到端：market_summary → _get_tier_confidence → 门控判定"""
    tier_conf = FATS._get_tier_confidence(market_summary, sym, pos_tier)
    threshold = GATE.get(pos_tier, 0.40)
    blocked = (tier_conf >= threshold and pnl_pct > emergency and ai_conf < 92)
    return blocked, tier_conf

# 场景A: BTC long_conf=0.72, short_conf=0.15
# 长线 trend_follow 仓被 AI close 请求
bl_a, conf_a = e2e_gate(market_summary, "BTC", "long", pnl_pct=-3.0, ai_conf=70)
check("E2E-A: BTC长线仓(long_conf=72%)→拦截close",
      bl_a is True, f"blocked={bl_a}, conf={conf_a}")

# 场景B: 同一 market_summary，BTC 短线仓被 close
bl_b, conf_b = e2e_gate(market_summary, "BTC", "short", pnl_pct=-1.0, ai_conf=70)
check("E2E-B: BTC短线仓(short_conf=15%)→放行close",
      bl_b is False, f"blocked={bl_b}, conf={conf_b}")

# 场景C: ETH long_conf=0.20 (低), 长线仓被 close → 应放行
bl_c, conf_c = e2e_gate(market_summary, "ETH", "long", pnl_pct=-2.0, ai_conf=65)
check("E2E-C: ETH长线仓(long_conf=20%)→放行close",
      bl_c is False, f"blocked={bl_c}, conf={conf_c}")

# 场景D: ETH mid_conf=0.65, 中线 swing 仓被 reduce → 应拦截
bl_d, conf_d = e2e_gate(market_summary, "ETH", "mid", pnl_pct=-1.5, ai_conf=68)
check("E2E-D: ETH中线仓(mid_conf=65%)→拦截reduce",
      bl_d is True, f"blocked={bl_d}, conf={conf_d}")


# ═══════════ Part 4: hold→enter 覆盖 — 用对应 tier 置信度 ═══════════

print("\n" + "=" * 60)
print("Part 4: hold→enter 覆盖逻辑 — 不再用 max")
print("=" * 60)

# 模拟覆盖逻辑中的 tier 置信度选取
_NATURE_TO_TIER = FATS._NATURE_TO_TIER_MAP

def simulate_hold_override_conf(orch_data: dict) -> float:
    """仿真 hold→enter 覆盖中的置信度计算（新版: 按 recommended_nature 选 tier）"""
    _lc = float(orch_data.get("long_conf", 0) or 0)
    _mc = float(orch_data.get("mid_conf", 0) or 0)
    _sc = float(orch_data.get("short_conf", 0) or 0)
    _rec_nature = str(orch_data.get("recommended_nature", "") or "")
    _rec_tier = _NATURE_TO_TIER.get(_rec_nature, "mid")
    _tier_conf_map = {"long": _lc, "mid": _mc, "short": _sc}
    return _tier_conf_map.get(_rec_tier, _mc) * 100

def simulate_hold_override_conf_OLD(orch_data: dict) -> float:
    """旧版: max(long, mid, short) * 100"""
    _lc = float(orch_data.get("long_conf", 0) or 0)
    _mc = float(orch_data.get("mid_conf", 0) or 0)
    _sc = float(orch_data.get("short_conf", 0) or 0)
    return max(_lc, _mc, _sc) * 100

# 场景: long_conf=0.30, mid_conf=0.20, short_conf=0.80
# 旧版 max = 0.80*100 = 80 → 会误覆盖为 enter (≥60)
# 新版: recommended=intraday → tier=short → 0.80*100=80 ← 正确, 短线确实强
orch_case1 = {
    "long_conf": 0.30, "mid_conf": 0.20, "short_conf": 0.80,
    "recommended_nature": "intraday",
}
new1 = simulate_hold_override_conf(orch_case1)
old1 = simulate_hold_override_conf_OLD(orch_case1)
check("Override-1: intraday推荐, 新旧均用short_conf",
      abs(new1 - 80.0) < 0.1 and abs(old1 - 80.0) < 0.1,
      f"new={new1:.1f}, old={old1:.1f}")

# 场景: long_conf=0.30, mid_conf=0.20, short_conf=0.80
# recommended=trend_follow → tier=long → 新版 0.30*100=30 (不会开仓, 正确!)
# 旧版 max = 80 → 会用 80% 置信度强制开一个 trend_follow 仓 (错误！)
orch_case2 = {
    "long_conf": 0.30, "mid_conf": 0.20, "short_conf": 0.80,
    "recommended_nature": "trend_follow",
}
new2 = simulate_hold_override_conf(orch_case2)
old2 = simulate_hold_override_conf_OLD(orch_case2)
check("Override-2: trend_follow推荐+long_conf低, 新版正确阻止",
      new2 < 60.0, f"new={new2:.1f} (应<60阻止开仓)")
check("Override-2: 旧版会误开仓",
      old2 >= 60.0, f"old={old2:.1f} (旧版max=80会误开仓)")

# 场景: long_conf=0.75, mid_conf=0.20, short_conf=0.10
# recommended=trend_follow → 新版用 long=0.75*100=75 → 合理开仓
orch_case3 = {
    "long_conf": 0.75, "mid_conf": 0.20, "short_conf": 0.10,
    "recommended_nature": "trend_follow",
}
new3 = simulate_hold_override_conf(orch_case3)
check("Override-3: trend_follow推荐+long_conf高→正确开仓",
      new3 >= 60.0, f"new={new3:.1f} (应≥60允许开仓)")

# 场景: long_conf=0.10, mid_conf=0.70, short_conf=0.10
# recommended=swing → 新版用 mid=0.70*100=70 → 合理开仓
orch_case4 = {
    "long_conf": 0.10, "mid_conf": 0.70, "short_conf": 0.10,
    "recommended_nature": "swing",
}
new4 = simulate_hold_override_conf(orch_case4)
old4 = simulate_hold_override_conf_OLD(orch_case4)
check("Override-4: swing推荐+mid_conf高→新版正确用mid",
      abs(new4 - 70.0) < 0.1, f"new={new4:.1f}")
check("Override-4: 旧版也是70(max=mid)",
      abs(old4 - 70.0) < 0.1, f"old={old4:.1f}")


# ═══════════ Part 5: 边界/极端场景 ═══════════

print("\n" + "=" * 60)
print("Part 5: 边界和极端场景")
print("=" * 60)

# 置信度恰好在门槛上
blocked_exact = simulate_gate(
    pos_tier="long", tier_conf=0.45, pnl_pct=-2.0, ai_confidence=70)
check("边界: long_conf==门槛0.45→拦截(>=)", blocked_exact is True,
      f"blocked={blocked_exact}")

blocked_just_below = simulate_gate(
    pos_tier="long", tier_conf=0.449, pnl_pct=-2.0, ai_confidence=70)
check("边界: long_conf=0.449<门槛→放行", blocked_just_below is False,
      f"blocked={blocked_just_below}")

# AI conf 恰好 92 → 应该放行（>= 92 突破门控）
blocked_92 = simulate_gate(
    pos_tier="long", tier_conf=0.72, pnl_pct=-2.0, ai_confidence=92)
check("边界: AI conf==92→放行(突破门控)", blocked_92 is False,
      f"blocked={blocked_92}")

# PnL 恰好在紧急线上
blocked_emg_exact = simulate_gate(
    pos_tier="long", tier_conf=0.72, pnl_pct=-80.0, ai_confidence=70,
    emergency_pct=-80.0)
check("边界: pnl==-80==emergency→放行", blocked_emg_exact is False,
      f"blocked={blocked_emg_exact}")

blocked_emg_above = simulate_gate(
    pos_tier="long", tier_conf=0.72, pnl_pct=-79.9, ai_confidence=70,
    emergency_pct=-80.0)
check("边界: pnl=-79.9>emergency→拦截", blocked_emg_above is True,
      f"blocked={blocked_emg_above}")


# ═══════════ 结果汇总 ═══════════

print("\n" + "=" * 60)
print("📊 测试结果汇总")
print("=" * 60)
for r in results:
    print(r)
print(f"\n{'='*60}")
print(f"  通过: {passed}  失败: {failed}  总计: {passed + failed}")
if failed == 0:
    print("  🎉 全部通过！长线仓不会被短线置信度杀死！")
else:
    print(f"  ⚠️ 有 {failed} 项失败，需要排查！")
print("=" * 60)

sys.exit(0 if failed == 0 else 1)
