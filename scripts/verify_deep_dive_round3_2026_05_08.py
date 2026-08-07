"""
深挖第 3 轮验证脚本（2026-05-08）

验证 10 个修复项 + 数据完整性：
1-5. 5 个 guard 拦截点接入 record_guard_block
6.   full_auto_trading_service 主链路切 unified_check
7.   trading_commands (live) 主链路切 unified_check
8.   decision_snapshots 孤儿处置（占位 session）
9.   strategy_trades 历史污染数据标记 + 学习查询过滤
10.  新增 system_health 后端 API

跑法（项目根）:
    PYTHONPATH=. backend/.venv/bin/python scripts/verify_deep_dive_round3_2026_05_08.py
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))


def line(c: str = "─") -> str:
    return c * 80


def section(title: str):
    print()
    print(line("═"))
    print(f"  {title}")
    print(line("═"))


def check(label: str, ok: bool, details: str = ""):
    flag = "✅ PASS" if ok else "❌ FAIL"
    print(f"  {flag}  {label}")
    if details:
        for ln in details.splitlines():
            print(f"           {ln}")
    return ok


def file_contains(path: Path, *needles: str) -> Tuple[bool, List[str]]:
    if not path.exists():
        return False, [f"file not found: {path}"]
    text = path.read_text(encoding="utf-8")
    missing = [n for n in needles if n not in text]
    return len(missing) == 0, missing


# ──────────────────────────────────────────────────
def verify_guard_fee():
    section("修复 1：fee_guard 落盘（sub_position_manager._verdict）")
    p = ROOT / "backend/services/sub_position_manager.py"
    return check(
        "_verdict 接收 db/account_id 并写 record_guard_block",
        file_contains(p,
            "from backend.services.unified_risk_gate import record_guard_block",
            "guard_name=(\"fee_guard\" if _is_fee_related",
            "db: Optional[Session] = None",
            "account_id: Optional[int] = None",
        )[0],
    )


def verify_guard_master():
    section("修复 2：master_close_guard 落盘")
    p = ROOT / "backend/services/full_auto_trading_service.py"
    return check(
        "master_close 拦截分支已注入 record_guard_block",
        file_contains(p, 'guard_name="master_close_guard"')[0],
    )


def verify_guard_liq_filter():
    section("修复 3：liquidity_filter 落盘")
    p = ROOT / "backend/services/trading_commands.py"
    return check(
        "trading_commands.liquidity_filter 分支已注入",
        file_contains(p,
            'guard_name="liquidity_filter"',
            "liq_result.volume_24h_usd",
        )[0],
    )


def verify_guard_liq_monitor():
    section("修复 4：liquidation_monitor 落盘")
    p = ROOT / "backend/services/liquidation_monitor.py"
    return check(
        "DANGER/CRITICAL 事件落库",
        file_contains(p,
            'guard_name="liquidation_monitor"',
            "LiquidationRiskLevel.DANGER, LiquidationRiskLevel.CRITICAL",
        )[0],
    )


def verify_guard_profit():
    section("修复 5：profit_drawdown_guard 落盘")
    p = ROOT / "backend/services/paper_trading_engine.py"
    return check(
        "profit drawdown action 落库",
        file_contains(p,
            'guard_name="profit_drawdown_guard"',
            '"drawdown_ratio": _dd_action.get("drawdown_ratio")',
        )[0],
    )


def verify_unified_fullauto():
    section("修复 6：full_auto_trading_service 主链路切 unified_check")
    p = ROOT / "backend/services/full_auto_trading_service.py"
    txt = p.read_text(encoding="utf-8")
    n_uc = txt.count("from backend.services.unified_risk_gate import unified_check")
    n_call = txt.count("unified_check(")
    return check(
        f"unified_check import {n_uc} 处 / 调用 {n_call} 次（应 ≥ 2）",
        n_uc >= 2 and n_call >= 2,
    )


def verify_unified_live():
    section("修复 7：trading_commands (live) 主链路切 unified_check")
    p = ROOT / "backend/services/trading_commands.py"
    txt = p.read_text(encoding="utf-8")
    n = txt.count("unified_check as _uc_live")
    return check(
        f"live 路径 unified_check 注入 ({n} 处，应 ≥ 2)",
        n >= 2,
    )


def verify_orphan_sessions():
    section("修复 8：decision_snapshots 孤儿处置")
    db_path = ROOT / "data" / "alpha_arena.db"
    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()
    c.execute("""
      SELECT COUNT(*) FROM decision_snapshots ds
      LEFT JOIN full_auto_sessions fa ON fa.id = ds.session_id
      WHERE fa.id IS NULL AND ds.session_id IS NOT NULL
    """)
    orphans = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM full_auto_sessions WHERE status='legacy'")
    legacy = c.fetchone()[0]
    conn.close()
    return check(
        f"孤儿快照 {orphans} 条 / legacy 占位 session {legacy} 条",
        orphans == 0 and legacy >= 1,
    )


def verify_legacy_dirty_marks():
    section("修复 9：strategy_trades legacy_dirty 标记 + 学习查询过滤")
    db_path = ROOT / "data" / "alpha_arena.db"
    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()
    c.execute("""
      SELECT COUNT(*) FROM strategy_trades
      WHERE decision_context LIKE '%legacy_dirty%true%'
    """)
    marked = c.fetchone()[0]
    conn.close()

    p1 = ROOT / "backend/services/strategy_learning_service.py"
    p2 = ROOT / "backend/services/learning_loop_service.py"
    p3 = ROOT / "backend/services/rl/system_coordinator.py"

    ok_p1 = file_contains(p1, "_exclude_legacy_dirty")[0]
    ok_p2 = file_contains(p2, '"legacy_dirty": true')[0]
    ok_p3 = file_contains(p3, '"legacy_dirty": true')[0]

    return check(
        f"标记 {marked} 条 + 3 个学习服务过滤注入: SLS={ok_p1} LLS={ok_p2} SC={ok_p3}",
        marked >= 1 and ok_p1 and ok_p2 and ok_p3,
    )


def verify_system_health_api():
    section("修复 10：系统健康 API")
    p = ROOT / "backend/api/system_health_routes.py"
    if not p.exists():
        return check("system_health_routes.py 存在", False)
    ok, miss = file_contains(p,
        "/llm-cost-ranking",
        "/risk-events",
        "/session-summary",
        "tags=[\"system-health\"]",
    )
    if not ok:
        return check("system_health_routes 内容完整", False, f"missing: {miss}")
    main_p = ROOT / "backend/main.py"
    main_ok = file_contains(main_p,
        "from .api.system_health_routes import router as system_health_router",
        "app.include_router(system_health_router)",
    )[0]
    return check("路由文件 + main.py 挂载完整", main_ok)


def verify_data_state():
    section("数据状态")
    db_path = ROOT / "data" / "alpha_arena.db"
    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM full_auto_sessions")
    print(f"  · full_auto_sessions: {c.fetchone()[0]}")
    c.execute("SELECT status, COUNT(*) FROM full_auto_sessions GROUP BY status")
    for st, n in c.fetchall():
        print(f"    └─ {st}: {n}")
    c.execute("SELECT COUNT(*) FROM risk_control_events")
    print(f"  · risk_control_events: {c.fetchone()[0]}")
    c.execute("""
      SELECT COUNT(*) FROM strategy_trades
      WHERE decision_context LIKE '%legacy_dirty%true%'
    """)
    print(f"  · strategy_trades.legacy_dirty=true: {c.fetchone()[0]}")
    conn.close()


CHECKS = [
    verify_guard_fee,
    verify_guard_master,
    verify_guard_liq_filter,
    verify_guard_liq_monitor,
    verify_guard_profit,
    verify_unified_fullauto,
    verify_unified_live,
    verify_orphan_sessions,
    verify_legacy_dirty_marks,
    verify_system_health_api,
]


def main() -> int:
    print(line("═"))
    print("Hyper-Alpha-Arena 深挖第 3 轮 — 修复验证（2026-05-08）")
    print(line("═"))
    results = []
    for fn in CHECKS:
        try:
            results.append((fn.__name__, fn()))
        except Exception as e:
            import traceback
            traceback.print_exc()
            results.append((fn.__name__, False))

    verify_data_state()

    print()
    print(line("═"))
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    for name, ok in results:
        flag = "✅" if ok else "❌"
        print(f"  {flag} {name}")
    print(line("─"))
    print(f"  汇总: {passed}/{total} 项通过")
    print(line("═"))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
