#!/usr/bin/env python3
"""
Rebate S3/S8 小资金验证脚本（默认 Paper）

用途：在切换 Live 前，验证策略评估 → 风控 → 执行 → 平仓 全链路。

用法:
  cd Hyper-Alpha-Arena
  python3 scripts/validate_rebate_s3_s8.py
  python3 scripts/validate_rebate_s3_s8.py --size-usd 50 --symbol ETH
  python3 scripts/validate_rebate_s3_s8.py --live --confirm-live  # 真实下单，谨慎

退出码: 0=全部通过, 1=有失败项
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

# 确保 backend 在 path
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _seed_paper_prices(symbols: list[str]) -> None:
    """为 Paper 模拟注入价格（无行情服务时）"""
    from backend.services.price_cache import price_cache

    defaults = {
        "ETH": 3500.0,
        "BTC": 95000.0,
        "SOL": 180.0,
    }
    for sym in symbols:
        base = sym.split("/")[0].upper()
        price = defaults.get(base, 100.0)
        for fmt in {sym, base, f"{base}/USDT:USDT", f"{base}/USDT"}:
            price_cache.record(fmt, "CRYPTO", price)


def _check(name: str, ok: bool, detail: str = "") -> bool:
    mark = "PASS" if ok else "FAIL"
    line = f"  [{mark}] {name}"
    if detail:
        line += f" — {detail}"
    print(line)
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Rebate S3/S8 for small capital")
    parser.add_argument("--equity", type=float, default=300.0, help="模拟账户权益 (U)")
    parser.add_argument("--size-usd", type=float, default=50.0, help="单次验证仓位 (U)")
    parser.add_argument("--symbol", type=str, default="ETH", help="交易对")
    parser.add_argument("--live", action="store_true", help="Live 模式（真实下单）")
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="Live 模式必须同时指定此 flag",
    )
    parser.add_argument(
        "--strategies",
        type=str,
        default="S3,S8",
        help="逗号分隔策略 ID，默认 S3,S8",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="绕过 R3 活跃天数等前置风控，仅用于链路验证",
    )
    args = parser.parse_args()

    if args.live and not args.confirm_live:
        print("错误: Live 模式需要 --live --confirm-live")
        return 1

    mode = "live" if args.live else "paper"
    strategy_ids = [s.strip().upper() for s in args.strategies.split(",") if s.strip()]

    print("=" * 60)
    print(f"Rebate S3/S8 验证 | 模式={mode} | 权益=${args.equity:.0f} | 仓位=${args.size_usd:.0f}")
    print("=" * 60)

    results: list[bool] = []

    # 1. 配置检查
    try:
        from backend.config.rebate_config_loader import load_config
        cfg = load_config()
        results.append(_check("配置加载", True))
    except Exception as e:
        results.append(_check("配置加载", False, str(e)))
        return 1

    for sid in strategy_ids:
        key_map = {
            "S3": "S3_points_mining",
            "S8": "S8_asterdex_rh",
        }
        cfg_key = key_map.get(sid, "")
        item = cfg.strategies.get(cfg_key) if cfg_key else None
        enabled = item.enabled if item else False
        results.append(_check(f"{sid} 策略已启用", enabled, cfg_key))

    results.append(_check(
        "auto_execute 默认关闭",
        not cfg.engine.auto_execute,
        "全自动 tick 不会擅自开仓",
    ))

    # 2. 引擎初始化
    from backend.services.rebate_arb.engine import rebate_arb_engine
    from backend.services.rebate_arb.capital_coordinator import capital_coordinator

    if args.force:
        _orig_build = rebate_arb_engine._build_risk_context

        def _validation_risk_context() -> dict:
            ctx = _orig_build()
            ctx["active_days_this_week"] = max(
                ctx.get("active_days_this_week", 0), 7
            )
            ctx["wash_trade_score"] = 0.0
            return ctx

        rebate_arb_engine._build_risk_context = _validation_risk_context  # type: ignore[method-assign]
        print("  [INFO] --force: 已注入验证用风控上下文 (active_days=7)")

    capital_coordinator.initialize(args.equity)
    results.append(_check("资金池初始化", True, f"equity=${args.equity:.0f}"))

    _seed_paper_prices([args.symbol, "BTC", "ETH", "SOL"])
    results.append(_check("Paper 价格注入", True))

    # 3. 扫描
    incentive_data = {
        "hyperliquid": {
            "daily_points_rate": 50,
            "hype_price": 25.0,
            "points_balance": 0,
            "maker_rate": 0.0002,
            "taker_rate": 0.00035,
        },
        "asterdex": {
            "rebate_rate": 0.10,
            "maker_rate": 0.00005,
            "taker_rate": 0.0005,
            "rh_multiplier": 1.0,
        },
    }
    evaluations = rebate_arb_engine.scan_all_strategies(
        incentive_data=incentive_data,
        funding_rates={},
        account_equity=args.equity,
    )
    eval_map = {e.strategy_type.value: e for e in evaluations}
    results.append(_check("策略扫描", len(evaluations) > 0, f"{len(evaluations)} 条评估"))

    # 4. 逐策略验证
    opened: list[str] = []

    for sid in strategy_ids:
        print(f"\n--- 验证 {sid} ---")
        ev = eval_map.get(sid)
        if ev is None:
            results.append(_check(f"{sid} 评估存在", False))
            continue
        results.append(_check(
            f"{sid} is_viable",
            ev.is_viable,
            f"月预期=${ev.expected_monthly_value:.2f}",
        ))
        if not ev.is_viable:
            continue

        exec_result = rebate_arb_engine.execute_strategy(
            strategy_type=sid,
            size_usd=min(args.size_usd, cfg.engine.max_position_usd),
            symbol=args.symbol,
            opportunity=ev.details if hasattr(ev, "details") else {},
            mode=mode,
        )
        results.append(_check(
            f"{sid} 执行",
            exec_result.success,
            exec_result.error or f"position={exec_result.position_id}",
        ))
        if exec_result.success and exec_result.position_id:
            opened.append(exec_result.position_id)
            # 逐策略独立验证：平仓后再测下一策略，避免 R6 总敞口叠加
            close_result = rebate_arb_engine.close_position(
                exec_result.position_id, reason="validation_step"
            )
            ok_close = close_result.get("success", False)
            results.append(_check(
                f"{sid} 即时平仓",
                ok_close,
                exec_result.position_id,
            ))
            if ok_close and exec_result.position_id in opened:
                opened.remove(exec_result.position_id)

    # 5. 仓位检查
    time.sleep(0.5)
    from backend.services.rebate_arb.models import RebatePositionStatus
    all_positions = rebate_arb_engine.get_all_positions()
    active = [p for p in all_positions if p.status == RebatePositionStatus.ACTIVE]
    results.append(_check(
        "活跃仓位注册",
        len(active) >= len(opened) or len(opened) == 0,
        f"active={len(active)} opened={len(opened)}",
    ))

    # 6. 平仓清理
    for pos_id in opened:
        close_result = rebate_arb_engine.close_position(pos_id, reason="validation_script")
        ok = getattr(close_result, "success", False) or (
            isinstance(close_result, dict) and close_result.get("success")
        )
        if not ok and hasattr(close_result, "success"):
            ok = close_result.success
        results.append(_check(f"平仓 {pos_id}", bool(ok)))

    # 7. 汇总
    passed = sum(1 for r in results if r)
    total = len(results)
    print("\n" + "=" * 60)
    print(f"结果: {passed}/{total} 通过")
    if args.live:
        print("⚠️  Live 模式已执行真实下单，请人工核对交易所持仓")
    else:
        print("Paper 模式验证完成。Live 前请: python3 scripts/validate_rebate_s3_s8.py --live --confirm-live")
    print("=" * 60)

    report = {
        "mode": mode,
        "equity": args.equity,
        "size_usd": args.size_usd,
        "passed": passed,
        "total": total,
        "strategies": strategy_ids,
        "opened_positions": opened,
        "timestamp": time.time(),
    }
    report_path = os.path.join(_ROOT, "backend", "data", "rebate_s3_s8_validation.json")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"报告已写入: {report_path}")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
