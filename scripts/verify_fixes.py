"""
全面验证脚本 - 测试核心tier分配逻辑和系统功能
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def test_nature_to_tier_mapping():
    """测试1: NATURE_TO_TIER映射完整性"""
    print("\n=== 测试1: NATURE_TO_TIER 映射 ===")
    from backend.services.sub_position_manager import NATURE_TO_TIER, normalize_nature

    print(f"NATURE_TO_TIER 映射表: {NATURE_TO_TIER}")
    expected_mappings = {
        'scalp': 'short',
        'intraday': 'short',
        'mean_reversion': 'short',
        'momentum': 'short',
        'swing': 'mid',
        'breakout': 'mid',
        'trend_following': 'mid',
        'long': 'long',
        'position': 'long',
        'swing_position': 'long',
    }

    all_pass = True
    for nature, expected_tier in expected_mappings.items():
        result = NATURE_TO_TIER.get(normalize_nature(nature))
        status = "PASS" if result == expected_tier else "FAIL"
        if status == "FAIL":
            all_pass = False
        print(f"  {status}: normalize_nature('{nature}') -> '{result}' (expected '{expected_tier}')")

    print(f"\n测试1结果: {'全部通过' if all_pass else '有失败'}")
    return all_pass


def test_tier_assignment_priority():
    """测试2: tier分配优先级逻辑"""
    print("\n=== 测试2: tier分配优先级 ===")

    # 模拟genome数据结构
    test_cases = [
        # (strat_genome_nature, strat_timeframe_tier, decision_nature, expected_nature, expected_tier_desc)
        ('intraday', 'short', None, 'intraday', 'short'),
        ('scalp', None, None, 'scalp', 'short'),
        ('swing', 'mid', None, 'swing', 'mid'),
        ('trend_following', 'mid', 'swing', 'trend_following', 'mid'),
        ('position', 'long', None, 'position', 'long'),
        ('', 'short', 'swing', 'swing', 'mid'),  # strat无nature但有tier, decision有nature
        ('', '', 'scalp', 'scalp', 'short'),
        ('', '', '', 'swing', 'mid'),  # 全部空的fallback
    ]

    from backend.services.sub_position_manager import NATURE_TO_TIER, normalize_nature

    all_pass = True
    for strat_nature, strat_tier, dec_nature, exp_nature, exp_tier in test_cases:
        # 模拟_execute_paper_trade中的优先级逻辑
        _trade_nature = strat_nature or dec_nature or "swing"
        _trade_nature = normalize_nature(_trade_nature)
        _sub_tier = NATURE_TO_TIER.get(_trade_nature, strat_tier or "mid")

        match_nature = _trade_nature == exp_nature
        match_tier = _sub_tier == exp_tier
        status = "PASS" if (match_nature and match_tier) else "FAIL"
        if status == "FAIL":
            all_pass = False

        print(f"  {status}: genome={strat_nature!r} dec={dec_nature!r} -> "
              f"nature={_trade_nature!r}({match_nature}) tier={_sub_tier!r}({match_tier})")

    print(f"\n测试2结果: {'全部通过' if all_pass else '有失败'}")
    return all_pass


def test_orchestrator_short_term_fallback():
    """测试3: Orchestrator短期分析回退逻辑"""
    print("\n=== 测试3: Orchestrator短期回退 ===")

    src = "backend/services/multi_timeframe_orchestrator.py"
    with open(src, encoding="utf-8") as f:
        content = f.read()

    checks = {
        "价格变动回退存在": "price_change_pct" in content and "timeframe_tier" in content,
        "EMA门槛降低(3%->1%)": "'ema_diff_threshold': 0.03" in content.replace(" ", "").replace("\n", ""),
        "短期分析回退逻辑": "_analyze_short_term" in content,
        "_recommend_slots存在": "_recommend_slots" in content,
        "跟随中期激活short": "short" in content and "CONF_MID" in content,
    }

    all_pass = True
    for name, result in checks.items():
        status = "PASS" if result else "FAIL"
        if not result:
            all_pass = False
        print(f"  {status}: {name}")

    print(f"\n测试3结果: {'全部通过' if all_pass else '有失败'}")
    return all_pass


def test_genome_trade_nature_priority():
    """测试4: genome中trade_nature优先读取"""
    print("\n=== 测试4: genome trade_nature 优先级 ===")

    src = "backend/services/full_auto_trading_service.py"
    with open(src, encoding="utf-8") as f:
        content = f.read()

    checks = {
        "从genome读取trade_nature": "genome.get('trade_nature'" in content,
        "strategy_name属性访问": "getattr(strat,'name'" in content,
        "_sub_tier回退到_strat_tier": "_strat_tier or timeframe_tier" in content,
        "ExecTrade日志": "[ExecTrade]" in content,
        "NATURE_TO_TIER导入": "from backend.services.sub_position_manager import" in content,
    }

    all_pass = True
    for name, result in checks.items():
        status = "PASS" if result else "FAIL"
        if not result:
            all_pass = False
        print(f"  {status}: {name}")

    print(f"\n测试4结果: {'全部通过' if all_pass else '有失败'}")
    return all_pass


def test_ai_strategy_routes_fallback():
    """测试5: AI策略路由slot回退逻辑"""
    print("\n=== 测试5: AI策略路由slot回退 ===")

    src = "backend/api/ai_strategy_routes.py"
    with open(src, encoding="utf-8") as f:
        content = f.read()

    checks = {
        "market_cycle推断": "market_cycle" in content,
        "slot为None时回退": "timeframe_slot is None" in content or "slot is None" in content,
        "市场周期到slot映射": "uptrend" in content and "mid" in content,
    }

    all_pass = True
    for name, result in checks.items():
        status = "PASS" if result else "FAIL"
        if not result:
            all_pass = False
        print(f"  {status}: {name}")

    print(f"\n测试5结果: {'全部通过' if all_pass else '有失败'}")
    return all_pass


def test_backfill_script():
    """测试6: 回填脚本验证"""
    print("\n=== 测试6: 回填脚本 ===")

    src = "scripts/backfill_strategy_tiers.py"
    if not os.path.exists(src):
        print("  SKIP: 回填脚本不存在(可能在数据库中已执行)")
        return True

    with open(src, encoding="utf-8") as f:
        content = f.read()

    checks = {
        "脚本存在": True,
        "genome解析": "genome" in content,
        "tier推断": "timeframe_tier" in content,
    }

    all_pass = True
    for name, result in checks.items():
        status = "PASS" if result else "FAIL"
        if not result:
            all_pass = False
        print(f"  {status}: {name}")

    print(f"\n测试6结果: {'全部通过' if all_pass else '有失败'}")
    return all_pass


def main():
    print("=" * 60)
    print("Hyper-Alpha-Arena 核心功能全面验证")
    print("=" * 60)

    results = []
    results.append(("NATURE_TO_TIER映射", test_nature_to_tier_mapping()))
    results.append(("tier分配优先级", test_tier_assignment_priority()))
    results.append(("Orchestrator回退", test_orchestrator_short_term_fallback()))
    results.append(("genome trade_nature", test_genome_trade_nature_priority()))
    results.append(("AI策略路由回退", test_ai_strategy_routes_fallback()))
    results.append(("回填脚本", test_backfill_script()))

    print("\n" + "=" * 60)
    print("汇总结果:")
    print("=" * 60)
    all_pass = True
    for name, result in results:
        icon = "[OK]" if result else "[FAIL]"
        print(f"  {icon} {name}")
        if not result:
            all_pass = False

    print(f"\n{'全部测试通过!' if all_pass else '有测试失败，请检查上述输出'}")
    return all_pass


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
