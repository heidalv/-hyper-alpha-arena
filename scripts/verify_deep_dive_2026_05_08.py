"""
深挖第 2 轮验证脚本（2026-05-08）

验证 5 个深挖修复 + 1 个额外修复（datetime typo 链式发现的 llm_config_service）：
1. live 路径 duration_seconds + opened_at（trading_commands.py）
2. UnifiedRiskGate facade（unified_risk_gate.py）
3. guard 拦截事件统一落盘（reentry_cooldown 入口注入）
4. 幽灵 LLM 调用 caller 追踪（llm_config_service.py）
5. FullAutoSession 启动健康摘要（full_auto_trading_service.py）

跑法（项目根）:
    PYTHONPATH=. backend/.venv/bin/python scripts/verify_deep_dive_2026_05_08.py
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timedelta
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
def verify_live_duration() -> bool:
    section("深挖 1：live 路径 duration_seconds + opened_at")
    p = ROOT / "backend/services/trading_commands.py"
    ok, miss = file_contains(
        p,
        "_live_opened_at = None",
        "_live_duration = max(0, int((_now - _opened_aware).total_seconds()))",
        "duration_seconds=_live_duration",
        "opened_at=_live_opened_at",
        '"opened_at_source": _opened_at_source,',
    )
    if ok:
        check("trading_commands.py 已替换 duration_seconds=0 / opened_at=None", True)
        return True
    return check("trading_commands.py 修复缺失", False, f"missing: {miss}")


def verify_unified_risk_gate() -> bool:
    section("深挖 2：UnifiedRiskGate facade")
    p = ROOT / "backend/services/unified_risk_gate.py"
    if not p.exists():
        return check("unified_risk_gate.py 存在", False, "未创建")
    ok, miss = file_contains(
        p,
        "def unified_check",
        "def record_guard_block",
        '"deterministic"', '"stateful"',
        "RiskControlEvent",
        'event_type="unified_blocked"',
        'event_type="guard_blocked"',
    )
    if not ok:
        return check("unified_risk_gate.py 内容完整", False, f"missing: {miss}")

    # paper_engine 已切到 unified_check
    pe = ROOT / "backend/services/paper_trading_engine.py"
    pe_ok, pe_miss = file_contains(
        pe,
        "from backend.services.unified_risk_gate import unified_check",
        "_ures = unified_check(",
        '"blocked_layer": _ures.blocked_layer,',
    )
    if not pe_ok:
        return check("paper_trading_engine 已切换到 unified_check", False, f"missing: {pe_miss}")

    # 实际 import 成功
    try:
        from backend.services.unified_risk_gate import (
            unified_check as _uc, record_guard_block as _rgb, UnifiedRiskResult as _UR,
        )
        _ = _uc, _rgb, _UR
        return check("unified_risk_gate.py 模块可导入 + paper 已接入", True)
    except Exception as e:
        return check("unified_risk_gate.py 模块可导入", False, f"import error: {e}")


def verify_guard_log_injection() -> bool:
    section("深挖 3：guard 拦截事件统一落盘")
    p = ROOT / "backend/services/full_auto_trading_service.py"
    text = p.read_text(encoding="utf-8")
    n = text.count("from backend.services.unified_risk_gate import record_guard_block")
    n_call = text.count("record_guard_block(")
    okC = n >= 2 and n_call >= 2
    return check(
        f"full_auto_trading_service 已注入 record_guard_block ({n} 处 import / {n_call} 次调用)",
        okC,
    )


def verify_caller_tracking() -> bool:
    section("深挖 4：LLM 调用 caller 追踪")
    p = ROOT / "backend/services/llm_config_service.py"
    ok, miss = file_contains(
        p,
        "def _detect_caller_module",
        '"call_type": f"sync:{_resolved_caller}"',
        '"call_type": f"async:{_resolved_caller}"',
    )
    if not ok:
        return check("llm_config_service.py 增加 caller 解析", False, f"missing: {miss}")

    # 直接验证 _detect_caller_module 在导入后能用
    try:
        from backend.services.llm_config_service import _detect_caller_module
        c = _detect_caller_module()
        ok2 = isinstance(c, str) and c != "" and ":" in c
        return check(
            f"_detect_caller_module() 返回有效 caller: {c!r}", ok2,
            "(返回值应该形如 'verify_deep_dive_2026_05_08:verify_caller_tracking')",
        )
    except Exception as e:
        return check("_detect_caller_module 可调用", False, f"error: {e}")


def verify_session_health_summary() -> bool:
    section("深挖 5：FullAutoSession 启动健康摘要")
    p = ROOT / "backend/services/full_auto_trading_service.py"
    ok, miss = file_contains(
        p,
        "[FullAuto] 启动健康摘要",
        "24h 决策快照",
        "24h AI 决策日志",
        "当前没有任何 running/defensive/paused 会话",
    )
    return check(
        "restore_running_sessions 增加健康摘要 + 空会话警告",
        ok,
        f"missing: {miss}" if not ok else "",
    )


def verify_data_state() -> bool:
    section("深挖辅助：数据状态")
    db_path = ROOT / "data" / "alpha_arena.db"
    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM full_auto_sessions")
    n_sess = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM strategy_trades")
    n_trades = c.fetchone()[0]
    c.execute("""
      SELECT COUNT(*) FROM strategy_trades
      WHERE position_size = exit_price AND position_size > 1
    """)
    n_bad = c.fetchone()[0]
    c.execute("""
      SELECT COUNT(*) FROM strategy_trades
      WHERE opened_at = closed_at OR
            ABS(strftime('%s', closed_at) - strftime('%s', opened_at)) < 1
    """)
    n_zero_dur = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM risk_control_events")
    n_evt = c.fetchone()[0]
    print(f"  · full_auto_sessions: {n_sess}")
    print(f"  · strategy_trades: {n_trades}")
    print(f"  · strategy_trades.position_size==exit_price (Bug B 残留): {n_bad}")
    print(f"  · strategy_trades.opened_at==closed_at (Bug C 残留): {n_zero_dur}")
    print(f"  · risk_control_events: {n_evt}")
    return True  # 这一项是观察性统计，不计 PASS/FAIL


CHECKS = [
    verify_live_duration,
    verify_unified_risk_gate,
    verify_guard_log_injection,
    verify_caller_tracking,
    verify_session_health_summary,
]


def main() -> int:
    print(line("═"))
    print("Hyper-Alpha-Arena 深挖第 2 轮 — 修复验证（2026-05-08）")
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
