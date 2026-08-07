"""套利中心升级验收脚本（Phase 0-4 端到端自检）。

2026-07-06 新增：一键验证套利中心是否已从"看着活着实则空转"恢复为"逻辑畅通、
Paper 可运行"。逐项 PASS/FAIL 打印，任一 FAIL 退出码非 0，便于 CI/人工回归。

用法：
    python scripts/verify_arbitrage_center.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_results = []


def check(name: str, cond: bool, detail: str = ""):
    _results.append((name, bool(cond), detail))
    flag = "PASS" if cond else "FAIL"
    print(f"[{flag}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    print("=" * 80)
    print("套利中心升级验收（Phase 0-4）")
    print("=" * 80)

    # ── 病灶A：引擎能 import、ALL_STRATEGIES 就绪 ──
    try:
        from backend.services.rebate_arb.strategies import ALL_STRATEGIES
        from backend.services.rebate_arb.engine import rebate_arb_engine  # noqa: F401

        check("病灶A 引擎可 import", True, f"策略={list(ALL_STRATEGIES.keys())}")
        check("S1/S5 已下线未注册", "S1" not in ALL_STRATEGIES and "S5" not in ALL_STRATEGIES)
        check("SDN delta-neutral 已注册", "SDN" in ALL_STRATEGIES)
    except Exception as e:
        check("病灶A 引擎可 import", False, str(e))

    # ── 病灶B：program_registry 生命周期 + 自检 ──
    try:
        from backend.services.rebate_arb import program_registry as pr

        check("病灶B Aster Stage6 标记 ended",
              pr.strategy_program_status("S8") == "ended")
        check("病灶B HL Season2 标记 active",
              pr.strategy_program_status("S3") == "active")
        check("病灶B 引擎自检跳过 S8",
              rebate_arb_engine._is_strategy_program_active("S8") is False)
        active = [p.program_id for p in pr.active_programs()]
        check("病灶B 活跃项目非空", len(active) > 0, f"active={active}")
    except Exception as e:
        check("病灶B program_registry", False, str(e))

    # ── 病灶B：配置已重指向（S8 配额 0、SDN 上位） ──
    try:
        from backend.config.rebate_config_loader import load_config

        c = load_config()
        pools = c.capital_allocation.strategy_sub_pools
        check("配置 S8 配额=0 且已停用",
              pools.get("S8", 1) == 0 and not c.get_strategy_config("S8").enabled)
        check("配置 SDN 有配额且启用",
              pools.get("SDN", 0) > 0 and c.get_strategy_config("SDN").enabled)
    except Exception as e:
        check("配置重指向", False, str(e))

    # ── 病灶C：IncentiveAggregator 离线兜底不再全空 ──
    try:
        from backend.services.rebate_arb.incentive_aggregator import incentive_aggregator

        d = incentive_aggregator.get_latest_as_dict()
        has_hl = isinstance(d.get("hyperliquid"), dict) and d["hyperliquid"].get("taker_rate", 0) > 0
        check("病灶C 离线激励兜底有数据", has_hl,
              f"hyperliquid taker={d.get('hyperliquid', {}).get('taker_rate')}")
        progs = incentive_aggregator.get_active_programs()
        check("Phase1 覆盖多活跃项目", len(progs) >= 4, f"{len(progs)} 个活跃项目")
    except Exception as e:
        check("病灶C 离线兜底", False, str(e))

    # ── 病灶E：proposal_auto_applier 最小样本门槛 ──
    try:
        import backend.services.rebate_arb.proposal_auto_applier as paa

        check("病灶E 最小样本门槛已设", paa.MIN_SAMPLE_N >= 1, f"N_MIN={paa.MIN_SAMPLE_N}")
    except Exception as e:
        check("病灶E 自污染防护", False, str(e))

    # ── Phase1：资金费矩阵扫描 ──
    try:
        from backend.services.rebate_arb.funding_rate_matrix import scan_funding_matrix

        combos = scan_funding_matrix(
            {"binance": {"BTC/USDT": 0.0001}, "hyperliquid": {"BTC/USDT": -0.00003}},
            min_net_apr=-1e9,
        )
        ok = combos and combos[0].long_exchange == "hyperliquid" and combos[0].short_exchange == "binance"
        check("Phase1 资金费矩阵选对多空腿", ok)
    except Exception as e:
        check("Phase1 资金费矩阵", False, str(e))

    # ── Phase1：诚实积分估值 ──
    try:
        from backend.services.rebate_arb.points_valuation import PointsValuationInput, value_points

        v_no = value_points(PointsValuationInput(program_id="x"))
        check("Phase1 无数据不臆造积分", v_no.estimable is False)
    except Exception as e:
        check("Phase1 积分估值", False, str(e))

    # ── Phase2：SDN 策略诚实/可行 ──
    try:
        sdn = ALL_STRATEGIES["SDN"]
        e0 = sdn.evaluate({}, 300.0)
        e1 = sdn.evaluate(
            {"funding_rates": {"binance": {"BTC/USDT": 0.0002}, "hyperliquid": {"BTC/USDT": -0.00005}}},
            300.0,
        )
        check("Phase2 SDN 无数据判 not viable", e0.is_viable is False)
        check("Phase2 SDN 有数据可行且中性", e1.is_viable and e1.details.get("delta_neutral"))
    except Exception as e:
        check("Phase2 SDN 策略", False, str(e))

    # ── Phase3：双腿执行回滚 + 开关 ──
    try:
        from backend.services.rebate_arb.paper_delta_neutral_executor import PaperDeltaNeutralExecutor
        from backend.services.rebate_arb.arb_switches import get_arb_switch_status

        class Q:
            def __init__(s, m): s.mid = m; s.bid = m * 0.9999; s.ask = m * 1.0001

        plan = {"side_a": {"exchange": "hyperliquid", "symbol": "BTC/USDT", "side": "buy"},
                "side_b": {"exchange": "binance", "symbol": "BTC/USDT", "side": "sell"}}
        ok_exec = PaperDeltaNeutralExecutor(quote_resolver=lambda e, s: Q(100.0)).execute(
            plan, 200.0, combo={"net_funding_per_day": 0.001})
        check("Phase3 双腿中性成交", ok_exec.success and ok_exec.delta_drift_pct == 0.0)
        rb = PaperDeltaNeutralExecutor(
            quote_resolver=lambda e, s: (Q(100.0) if e == "hyperliquid" else None)
        ).execute(plan, 200.0)
        check("Phase3 空腿失败自动回滚", (not rb.success) and rb.rolled_back)
        st = get_arb_switch_status(False)
        check("Phase3 统一开关：实盘恒关", st.live_trading_enabled is False)
    except Exception as e:
        check("Phase3 执行/开关", False, str(e))

    # ── 汇总 ──
    print("-" * 80)
    passed = sum(1 for _, ok, _ in _results if ok)
    total = len(_results)
    print(f"结果: {passed}/{total} 项通过")
    failed = [n for n, ok, _ in _results if not ok]
    if failed:
        print("未通过:", failed)
        return 1
    print("套利中心已恢复：引擎可加载、不刷死项目、数据有兜底、双腿中性可回滚、开关语义清晰。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
