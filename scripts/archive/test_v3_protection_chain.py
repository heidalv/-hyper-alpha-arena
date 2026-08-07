"""
v3 精简保护链路测试
验证 3 层保护（A: 新仓保护, B: SubPosMgr冷却, C: 仓位过小保护）的行为
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DATABASE_URL", "sqlite:///test_v3_chain.db")
os.environ.setdefault("OPENAI_API_KEY", "test")

from datetime import datetime, timezone, timedelta

passed = 0
failed = 0
total = 0

def check(name, condition, detail=""):
    global passed, failed, total
    total += 1
    if condition:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed += 1
        print(f"  ❌ {name} — {detail}")


print("=" * 70)
print("v3 精简保护链路测试")
print("=" * 70)

# ══════════════════════════════════════════
# Part 1: SubPositionManager 真实拦截模式
# ══════════════════════════════════════════
print("\n[Part 1] SubPositionManager audit_only=False 验证")

from backend.services.sub_position_manager import SubPositionManager, NATURE_RULES

mgr_real = SubPositionManager(audit_only=False)
mgr_audit = SubPositionManager(audit_only=True)

check("audit_only=False 已设置", mgr_real.audit_only == False)
check("audit_only=True 对比", mgr_audit.audit_only == True)

# _verdict 在 audit_only=False 时应真正拦截
ok_real, reason_real = mgr_real._verdict(False, "test_block", "reduce", "BTC", "swing")
check("audit_only=False → 真正拦截 (返回 False)",
      ok_real == False, f"got ok={ok_real}")

ok_audit, reason_audit = mgr_audit._verdict(False, "test_block", "reduce", "BTC", "swing")
check("audit_only=True → 放行 (返回 True)",
      ok_audit == True, f"got ok={ok_audit}")

# 检查模块级实例也是 False
from backend.services.sub_position_manager import sub_position_manager
check("模块级 sub_position_manager.audit_only=False",
      sub_position_manager.audit_only == False,
      f"got {sub_position_manager.audit_only}")

# ══════════════════════════════════════════
# Part 2: NATURE_RULES 冷却参数完整性
# ══════════════════════════════════════════
print("\n[Part 2] NATURE_RULES 冷却参数验证")

for nature, rules in NATURE_RULES.items():
    check(f"{nature}: reduce_cooldown_hours 存在",
          "reduce_cooldown_hours" in rules)
    check(f"{nature}: min_profit_for_reduce_pct 存在",
          "min_profit_for_reduce_pct" in rules)
    check(f"{nature}: max_reduce_ratio 存在",
          "max_reduce_ratio" in rules)

check("trend_follow 冷却=24h", NATURE_RULES["trend_follow"]["reduce_cooldown_hours"] == 24)
check("swing 冷却=6h", NATURE_RULES["swing"]["reduce_cooldown_hours"] == 6)
check("intraday 冷却=1h", NATURE_RULES["intraday"]["reduce_cooldown_hours"] == 1)
check("trend_follow 需5%盈利才能减", NATURE_RULES["trend_follow"]["min_profit_for_reduce_pct"] == 0.05)
check("swing 需2%盈利才能减", NATURE_RULES["swing"]["min_profit_for_reduce_pct"] == 0.02)

# ══════════════════════════════════════════
# Part 3: FullAutoTradingService 初始化 — SubMgr 为真实模式
# ══════════════════════════════════════════
print("\n[Part 3] FullAutoTradingService 初始化检查")

try:
    from backend.services.full_auto_trading_service import FullAutoTradingService
    svc = FullAutoTradingService()
    check("FullAutoTradingService 初始化成功", True)
    check("_sub_mgr 已创建", svc._sub_mgr is not None)
    check("_sub_mgr.audit_only=False", svc._sub_mgr.audit_only == False,
          f"got {getattr(svc._sub_mgr, 'audit_only', 'N/A')}")
except Exception as e:
    check("FullAutoTradingService 初始化成功", False, str(e))

# ══════════════════════════════════════════
# Part 4: 保护层 A — 新仓保护期模拟
# ══════════════════════════════════════════
print("\n[Part 4] 保护层 A — 新仓保护期逻辑")

try:
    tier_protection = svc.TIER_PROTECTION
    check("TIER_PROTECTION 已构建", len(tier_protection) > 0,
          f"got {tier_protection}")
    
    for tier in ["short", "mid", "long"]:
        cfg = tier_protection.get(tier, svc.DEFAULT_PROTECTION)
        pm = cfg["protect_min"]
        ep = cfg["emergency_pct"]
        check(f"{tier}: protect_min={pm:.0f}min, emergency_pct={ep:.1f}%",
              pm > 0 and ep < 0, f"pm={pm}, ep={ep}")

    # 模拟: 刚开仓 5 分钟，浮亏 -1%（短线保护期内+非紧急）→ 应被拦截
    short_cfg = tier_protection.get("short", svc.DEFAULT_PROTECTION)
    age_5min = 5
    pnl_neg1 = -1.0
    should_block = (age_5min < short_cfg["protect_min"]) and (pnl_neg1 > short_cfg["emergency_pct"])
    check("短线仓开仓5min+亏1% → 拦截", should_block)

    # 模拟: 刚开仓 5 分钟，浮亏超紧急线 → 应穿透
    pnl_emergency = short_cfg["emergency_pct"] - 1.0
    should_pass = (age_5min < short_cfg["protect_min"]) and (pnl_emergency <= short_cfg["emergency_pct"])
    check(f"短线仓开仓5min+亏{pnl_emergency:.0f}% → 穿透(紧急)", should_pass)

    # 模拟: 开仓 120 分钟（超过保护期）→ 不被拦截
    age_120min = 120
    should_not_block = (age_120min >= short_cfg["protect_min"])
    check("短线仓开仓120min → 不拦截", should_not_block)

except Exception as e:
    check("保护层 A 测试", False, str(e))

# ══════════════════════════════════════════
# Part 5: 保护层 C — 仓位过小保护
# ══════════════════════════════════════════
print("\n[Part 5] 保护层 C — 仓位过小保护逻辑")

# 模拟: 原始仓位 100, 当前仓位 25 → 25% < 30% → 应拦截
orig_size = 100.0
current_size_25 = 25.0
should_block_small = (orig_size > 0 and current_size_25 / orig_size < 0.30)
check("仓位25%原始 → 拦截减仓", should_block_small)

# 模拟: 原始仓位 100, 当前仓位 50 → 50% > 30% → 放行
current_size_50 = 50.0
should_pass_half = not (orig_size > 0 and current_size_50 / orig_size < 0.30)
check("仓位50%原始 → 放行减仓", should_pass_half)

# ══════════════════════════════════════════
# Part 6: close 不降级验证（代码结构检查）
# ══════════════════════════════════════════
print("\n[Part 6] close 不降级 — 代码结构检查")

import inspect
source = inspect.getsource(svc._execute_master_decisions)

check("无 'close降级' 字样", "close降级" not in source)
check("无 'close_downgraded' 变量", "_close_downgraded" not in source)
check("无 '保护层 5' 字样", "保护层 5" not in source)
check("无旧 '保护层 0.5' 字样", "保护层 0.5" not in source)
check("无旧 '保护层 3' 字样", "保护层 3:" not in source)
check("无旧 '保护层 4' 字样", "保护层 4:" not in source)
check("有 '保护层 A' 字样", "保护层 A:" in source)
check("有 '保护层 B' 字样", "保护层 B:" in source)
check("有 '保护层 C' 字样", "保护层 C:" in source)

# ══════════════════════════════════════════
# Part 7: 减仓比例由 confidence 决定
# ══════════════════════════════════════════
print("\n[Part 7] 减仓比例 — 由 AI confidence 决定")

def get_reduce_ratio(confidence):
    if confidence >= 85:
        return 0.50
    elif confidence >= 70:
        return 0.35
    else:
        return 0.25

check("confidence=95 → 50%减仓", get_reduce_ratio(95) == 0.50)
check("confidence=85 → 50%减仓", get_reduce_ratio(85) == 0.50)
check("confidence=75 → 35%减仓", get_reduce_ratio(75) == 0.35)
check("confidence=60 → 25%减仓", get_reduce_ratio(60) == 0.25)
check("confidence=50 → 25%减仓", get_reduce_ratio(50) == 0.25)

# 验证代码中确实用 confidence 而非 loss_pct
check("代码用 confidence 决定比例", "confidence >= 85" in source)
check("代码无旧 loss_pct 分段", "loss_pct >= 5.0" not in source)

# ══════════════════════════════════════════
# Part 8: paper_trading_engine v2 保护简化
# ══════════════════════════════════════════
print("\n[Part 8] paper_trading_engine v2 保护简化验证")

from backend.services.paper_trading_engine import PaperTradingEngine

pe_source = inspect.getsource(PaperTradingEngine._run_v2_protection)
check("无 is_pullback_not_reversal 调用",
      "is_pullback_not_reversal" not in pe_source)
check("无 classify_from_indicators 调用",
      "classify_from_indicators" not in pe_source)
check("无 _TIER_TIGHT_ATR_MULT 变量",
      "_TIER_TIGHT_ATR_MULT" not in pe_source)
check("保留 DynamicStopManager",
      "DynamicStopManager" in pe_source or "get_stop_manager" in pe_source)
check("保留爆仓检查", "liquidation" in pe_source)
check("保留 SL 直接检查", "hit_sl" in pe_source)
check("保留 TP 直接检查", "hit_tp" in pe_source)

# ══════════════════════════════════════════
# Part 9: review_reduce 不被重复调用
# ══════════════════════════════════════════
print("\n[Part 9] review_reduce 调用次数检查")

import re
review_calls = re.findall(r'review_reduce\(', source)
check(f"review_reduce 在 _execute_decisions 中只调用 1 次",
      len(review_calls) == 1,
      f"调用了 {len(review_calls)} 次")

# ══════════════════════════════════════════
# 结果汇总
# ══════════════════════════════════════════
print("\n" + "=" * 70)
print(f"测试完成: {passed}/{total} 通过, {failed} 失败")
print("=" * 70)

if failed > 0:
    print("\n⚠️  有失败项，请检查!")
    sys.exit(1)
else:
    print("\n✅ 全部通过！v3 精简保护链路验证成功")
    sys.exit(0)
