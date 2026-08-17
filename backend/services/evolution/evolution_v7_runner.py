"""
V7 三周期因子自进化 — 可执行入口（复用现有 factor_evolution_loop）。

用法（在项目根目录，backend/.venv 已存在）:
    backend\\.venv\\Scripts\\python.exe -m backend.services.evolution.evolution_v7_runner run --periods 4h,15m,5m
    backend\\.venv\\Scripts\\python.exe -m backend.services.evolution.evolution_v7_runner run --periods 4h --quick
    backend\\.venv\\Scripts\\python.exe -m backend.services.evolution.evolution_v7_runner memory

每轮结束后自动写入长期记忆 SQLite；下一轮 Codegen LLM 会检索并注入历史
教训/成功配方（见 evolution_memory_v7.py 与 factor_evolution_loop.py）。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from typing import List, Optional

# 三周期映射到现有因子进化周期：
#   L 大周期方向背景 -> 4h（可扩展 8h/1d）
#   M 中周期结构择时 -> 15m（可扩展 1h/30m）
#   S 小周期触发执行 -> 5m（可扩展 1m）
DEFAULT_PERIODS = ["4h", "15m", "5m"]
VALID_PERIODS = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "8h", "1d"}


def _parse_periods(raw: str) -> List[str]:
    periods = []
    for p in (raw or "").split(","):
        p = p.strip().lower()
        if not p:
            continue
        if p not in VALID_PERIODS:
            print(f"[V7] 非法周期: {p}; 支持 {sorted(VALID_PERIODS)}")
            raise SystemExit(2)
        periods.append(p)
    return periods or DEFAULT_PERIODS


def run_one_period(period: str, symbols: Optional[List[str]], quick: bool) -> dict:
    from backend.services.evolution import factor_evolution_loop as loop

    print(f"\n[V7] ===== 周期 {period} 开始 quick={quick} =====", flush=True)
    t0 = time.time()
    report = loop.run_factor_evolution_loop(
        symbols=symbols,
        period=period,
        quick=quick,
        source="v7_orchestrator",
    )
    report.setdefault("v7_memory_period", period)
    report.setdefault("v7_started_at", datetime.now(timezone.utc).isoformat())
    report["v7_wall_sec"] = round(time.time() - t0, 1)

    # factor_evolution_loop 已在 finally 中自动写记忆；仅当显式关闭
    # V7_MEMORY_ENABLED 时由 runner 补写，保证三周期闭环始终有记忆。
    if "v7_lessons_recorded" not in report:
        try:
            from backend.services.evolution.evolution_memory_v7 import record_report
            report["v7_lessons_recorded"] = record_report(period, report)
        except Exception as exc:  # noqa: BLE001
            print(f"[V7] 记忆写入失败(不阻断主流程): {exc}")
            report["v7_memory_error"] = str(exc)
    print(f"[V7] {period} 完成: {json.dumps(report, ensure_ascii=False, default=str)[:800]}")
    return report


def cmd_run(args) -> int:
    periods = _parse_periods(args.periods)
    symbols = None
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    all_reports = {}
    for period in periods:
        try:
            all_reports[period] = run_one_period(period, symbols, bool(args.quick))
        except KeyboardInterrupt:
            print(f"\n[V7] 用户中断于 {period}")
            return 130
        except Exception as exc:  # noqa: BLE001
            print(f"[V7] 周期 {period} 异常: {exc}")
            all_reports[period] = {"error": str(exc)}
            if args.stop_on_error:
                return 1
    print("\n[V7] 三周期进化汇总:")
    for p, r in all_reports.items():
        if r.get("error"):
            print(f"  {p}: error={r.get('error')} {r.get('message','')}")
        else:
            print(
                f"  {p}: candidates={r.get('candidates')} evaluated={r.get('evaluated')} "
                f"survivors={r.get('survivors')} promoted={r.get('promoted')} "
                f"degraded={r.get('degraded')} active={r.get('active_total')} "
                f"lessons={r.get('v7_lessons_recorded')}"
            )
    try:
        from backend.services.evolution.evolution_memory_v7 import stats
        print(f"\n[V7] 长期记忆: {json.dumps(stats(), ensure_ascii=False, indent=2)}")
    except Exception as exc:  # noqa: BLE001
        print(f"[V7] 记忆统计失败: {exc}")
    return 0


def cmd_memory(args) -> int:
    from backend.services.evolution.evolution_memory_v7 import memory_report, stats
    print(json.dumps(stats(), ensure_ascii=False, indent=2))
    for item in memory_report(args.limit):
        print(
            f"#{item['id']} [{item['kind']}|{item['cycle']}|{item['period']}] "
            f"q={item['quality']} used={item['use_count']} {item['title']}"
        )
        print(f"    {item['summary'][:180]}")
    return 0


def cmd_check(args) -> int:
    print("[V7] 依赖与接线检查")
    from backend.services.evolution.evolution_memory_v7 import build_codegen_context, init_db
    init_db()
    ctx = build_codegen_context("4h", limit=3)
    print(f"  长期记忆 DB: OK ({'有历史教训' if ctx else '暂无教训（首轮运行后自动积累）'})")
    if ctx:
        print(ctx)
    try:
        from backend.services.evolution.factor_evolution_loop import run_factor_evolution_loop
        print("  现有 factor_evolution_loop 接线: OK")
        _ = run_factor_evolution_loop
    except Exception as exc:
        print(f"  factor_evolution_loop 导入失败: {exc}")
        return 1
    try:
        from backend.services.evolution.factor_evolution_loop import _mine_candidates
        import inspect
        src = inspect.getsource(_mine_candidates)
        print("  Codegen 记忆注入: " + ("OK" if "evolution_memory_v7" in src else "未注入（请检查 factor_evolution_loop.py）"))
        return 0 if "evolution_memory_v7" in src else 1
    except Exception as exc:
        print(f"  注入检查失败: {exc}")
        return 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="V7 三周期因子自进化")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="运行三周期因子进化")
    p_run.add_argument("--periods", default=",".join(DEFAULT_PERIODS), help="逗号分隔: 4h,15m,5m")
    p_run.add_argument("--symbols", default="", help="可选逗号分隔币种，默认按现有 resolve_evolution_symbols")
    p_run.add_argument("--quick", action="store_true", help="快速模式：只跑种子/模板，跳过 GP/MCTS（先验证闭环）")
    p_run.add_argument("--stop-on-error", action="store_true")
    p_run.set_defaults(func=cmd_run)

    p_mem = sub.add_parser("memory", help="查看长期记忆")
    p_mem.add_argument("--limit", type=int, default=30)
    p_mem.set_defaults(func=cmd_memory)

    p_chk = sub.add_parser("check", help="检查记忆接线")
    p_chk.set_defaults(func=cmd_check)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
