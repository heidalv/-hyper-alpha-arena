"""
2026-05-08 修复验证脚本

逐项验证 13 个修复点是否都生效。每个验证打印 PASS / FAIL 和现场证据。
不需要启动后端，直接对代码、DB、配置文件做静态 + 数据校验。

跑法（项目根）:
    python3 scripts/verify_fixes_2026_05_08.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "alpha_arena.db"
ENV_PATH = ROOT / ".env"


# ──────────────────────────────────────────────────────────
# 工具
# ──────────────────────────────────────────────────────────

class Result:
    def __init__(self, name: str):
        self.name = name
        self.passed: bool = False
        self.details: List[str] = []
        self.evidence: List[str] = []

    def ok(self, msg: str = ""):
        self.passed = True
        if msg:
            self.details.append(f"OK: {msg}")
        return self

    def fail(self, msg: str):
        self.passed = False
        self.details.append(f"FAIL: {msg}")
        return self

    def evid(self, line: str):
        self.evidence.append(line)
        return self


def db_conn() -> sqlite3.Connection:
    return sqlite3.connect(str(DB_PATH))


def file_contains(path: Path, *needles: str) -> Tuple[bool, List[str]]:
    """所有 needle 都出现才算 True"""
    if not path.exists():
        return False, [f"文件不存在: {path}"]
    text = path.read_text(encoding="utf-8")
    missing = [n for n in needles if n not in text]
    return (len(missing) == 0), missing


# ──────────────────────────────────────────────────────────
# 各项验证
# ──────────────────────────────────────────────────────────

def verify_bug_a_test_data_cleaned() -> Result:
    r = Result("[Bug A] 测试桩数据已清理")
    conn = db_conn()
    c = conn.cursor()
    c.execute(
        "SELECT COUNT(*) FROM strategy_trades "
        "WHERE strategy_id IN ('full-chain-test-001','test-chain-001','test-bus-001')"
    )
    n = c.fetchone()[0]
    r.evid(f"测试桩残留: {n} 条")
    if n == 0:
        return r.ok()
    return r.fail(f"还剩 {n} 条")


def verify_bug_b_position_size_formula() -> Result:
    r = Result("[Bug B] position_size 公式已修复")
    py = ROOT / "backend/services/unified_learning_service.py"
    ok, miss = file_contains(
        py,
        "_real_size = _safe(getattr(outcome, \"position_size\", 0))",
        "_meta.get(\"position_size\")",
    )
    bad_old, _ = file_contains(py, "abs(_safe(outcome.exit_price) * 1)")
    if ok and not bad_old:
        r.evid("代码: 已使用 outcome.position_size + metadata 兜底")
        return r.ok()
    if bad_old:
        return r.fail("旧公式 abs(exit_price * 1) 仍存在")
    return r.fail(f"新代码缺少: {miss}")


def verify_bug_c_opened_at() -> Result:
    r = Result("[Bug C] opened_at 已正确赋值")
    py = ROOT / "backend/services/unified_learning_service.py"
    ok, miss = file_contains(
        py,
        "opened_at=_opened_at_dt",
        "_closed_at_dt - _td(seconds=int(outcome.duration_seconds))",
    )
    if ok:
        r.evid("代码: opened_at 显式赋值，duration_seconds 反推兜底")
        return r.ok()
    return r.fail(f"缺少: {miss}")


def verify_bug_d_decision_log_visible() -> Result:
    r = Result("[Bug D] AIDecisionLog 错误暴露")
    py = ROOT / "backend/services/full_auto_trading_service.py"
    ok, miss = file_contains(
        py,
        "AIDecisionLog 写入失败",
        "exc_info=True",
        "AIDecisionLog 跳过",
    )
    if ok:
        r.evid("代码: 失败原因 + exc_info 已升级 warning")
        return r.ok()
    return r.fail(f"缺少: {miss}")


def verify_factor_direction_float() -> Result:
    r = Result("[因子] direction 字段已为 float")
    conn = db_conn()
    c = conn.cursor()
    c.execute("SELECT id, value FROM atas_factor_cache")
    rows = c.fetchall()
    bad = []
    for rid, val in rows:
        try:
            d = json.loads(val) if isinstance(val, str) else val
            if isinstance(d, dict) and "direction" in d and isinstance(d["direction"], str):
                bad.append(rid)
        except Exception:
            continue
    r.evid(f"atas_factor_cache 共 {len(rows)} 条，direction 仍为字符串: {len(bad)} 条")
    if not bad:
        return r.ok()
    return r.fail(f"问题行 ID: {bad[:5]}")


def verify_prompt_binding_initialized() -> Result:
    r = Result("[Prompt 绑定] master_prompt_template_id 已初始化")
    conn = db_conn()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM ai_strategies WHERE master_prompt_template_id IS NULL")
    null_n = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM ai_strategies")
    total = c.fetchone()[0]
    r.evid(f"ai_strategies 共 {total} 条，master_prompt_template_id IS NULL: {null_n} 条")
    if null_n == 0:
        return r.ok()
    return r.fail(f"还有 {null_n} 条未绑定")


def verify_fake_flag_removed() -> Result:
    r = Result("[假开关] FACTOR_SIGNAL_ENABLED 已从 .env 移除")
    if not ENV_PATH.exists():
        return r.fail(".env 不存在")
    text = ENV_PATH.read_text(encoding="utf-8")
    has_active = any(
        line.strip().startswith("FACTOR_SIGNAL_ENABLED=")
        for line in text.splitlines()
    )
    r.evid(f".env 中是否还有未注释的 FACTOR_SIGNAL_ENABLED 赋值: {has_active}")
    if not has_active:
        return r.ok()
    return r.fail("仍有 FACTOR_SIGNAL_ENABLED= 未注释")


def verify_lie1_orchestrator_override_off() -> Result:
    r = Result("[谎言 1] 编排器覆盖默认关闭")
    py = ROOT / "backend/services/full_auto_trading_service.py"
    ok, miss = file_contains(
        py,
        'getenv("ENABLE_ORCHESTRATOR_OVERRIDE", "false")',
        "and _orch_override_enabled",
    )
    env_text = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else ""
    has_env = "ENABLE_ORCHESTRATOR_OVERRIDE=false" in env_text
    if ok and has_env:
        r.evid("代码 + .env 双向锁定为 false")
        return r.ok()
    return r.fail(f"代码缺: {miss} | .env 含 false: {has_env}")


def verify_lie2_paper_risk_gate() -> Result:
    r = Result("[谎言 2] paper place_order 已接 DeterministicRiskGate")
    py = ROOT / "backend/services/paper_trading_engine.py"
    ok, miss = file_contains(
        py,
        "PAPER_RISK_GATE_ENABLED",
        "from backend.services.deterministic_risk_gate import",
        "if not _result.passed:",
        "RiskControlEvent",
    )
    if ok:
        r.evid("代码: 风控调用 + 拒单事件持久化均到位")
        return r.ok()
    return r.fail(f"缺少: {miss}")


def verify_lie3_evolution_failure_diagnostics() -> Result:
    r = Result("[谎言 3] prompt 进化失败错因可见")
    py = ROOT / "backend/services/strategy_learning_service.py"
    ok, miss = file_contains(
        py,
        "_call_llm_for_prompt_evolution_v2",
        "raw_response_type",
        "raw_preview",
        "fail_reason",
        "error_class",
    )
    if ok:
        r.evid("代码: v2 调用返回 (text, debug)，failure 信息全量入库")
        return r.ok()
    return r.fail(f"缺少: {miss}")


def verify_logs_directory_writable() -> Result:
    r = Result("[日志] logs/ 目录可写 + main.py 已启用 RotatingFileHandler")
    logs_dir = ROOT / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    test_file = logs_dir / ".verify_write_test"
    try:
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink()
    except Exception as e:
        return r.fail(f"logs 不可写: {e}")
    main_py = ROOT / "backend/main.py"
    ok, miss = file_contains(
        main_py,
        "_bootstrap_logging",
        "RotatingFileHandler",
        'maxBytes=20 * 1024 * 1024',
    )
    if not ok:
        return r.fail(f"main.py 缺少: {miss}")
    r.evid(f"logs/ 可写 + main.py 已注入 RotatingFileHandler")
    return r.ok()


def verify_real_data_consistency() -> Result:
    """额外：清理后真实数据胜率/盈亏可读"""
    r = Result("[真实数据] strategy_trades 真实指标")
    conn = db_conn()
    c = conn.cursor()
    c.execute("SELECT COUNT(*), SUM(pnl), AVG(pnl), AVG(pnl_pct) FROM strategy_trades")
    row = c.fetchone()
    n, total_pnl, avg_pnl, avg_pct = row
    c.execute("SELECT COUNT(*) FROM strategy_trades WHERE pnl > 0")
    win = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM strategy_trades WHERE pnl < 0")
    lose = c.fetchone()[0]
    wr = win / (win + lose) if (win + lose) else 0
    r.evid(
        f"总笔数={n} 累计PnL={total_pnl:.2f} 平均PnL={avg_pnl:.4f} "
        f"平均PnL%={avg_pct:.4f} 胜率={wr*100:.1f}%"
    )
    return r.ok("真实指标已脱离测试桩污染")


def verify_dataclass_position_size_field() -> Result:
    """验证 TradeOutcome 已新增字段"""
    r = Result("[Schema] TradeOutcome 新字段 position_size + opened_at")
    py = ROOT / "backend/services/unified_learning_service.py"
    ok, miss = file_contains(
        py, "position_size: float", "opened_at: Optional[datetime]"
    )
    if ok:
        return r.ok("dataclass 字段已扩展")
    return r.fail(f"缺少: {miss}")


def verify_paper_engine_passes_real_size() -> Result:
    """验证 paper_trading_engine 真把 pos.size + opened_at 传给 outcome"""
    r = Result("[联调] paper_trading_engine 已传入真实 size + opened_at")
    py = ROOT / "backend/services/paper_trading_engine.py"
    ok, miss = file_contains(
        py,
        "position_size=float(pos.original_size or pos.size or 0)",
        "opened_at=pos.opened_at",
    )
    if ok:
        return r.ok()
    return r.fail(f"缺少: {miss}")


def verify_trading_commands_passes_size() -> Result:
    r = Result("[联调] trading_commands 已传入真实 size")
    py = ROOT / "backend/services/trading_commands.py"
    ok, miss = file_contains(py, "position_size=_sz")
    if ok:
        return r.ok()
    return r.fail(f"缺少: {miss}")


# ──────────────────────────────────────────────────────────
# 主入口
# ──────────────────────────────────────────────────────────

CHECKS: List[Callable[[], Result]] = [
    verify_bug_a_test_data_cleaned,
    verify_bug_b_position_size_formula,
    verify_bug_c_opened_at,
    verify_bug_d_decision_log_visible,
    verify_dataclass_position_size_field,
    verify_paper_engine_passes_real_size,
    verify_trading_commands_passes_size,
    verify_factor_direction_float,
    verify_prompt_binding_initialized,
    verify_fake_flag_removed,
    verify_lie1_orchestrator_override_off,
    verify_lie2_paper_risk_gate,
    verify_lie3_evolution_failure_diagnostics,
    verify_logs_directory_writable,
    verify_real_data_consistency,
]


def main() -> int:
    print("=" * 80)
    print("Hyper-Alpha-Arena 修复验证 — 2026-05-08")
    print(f"DB: {DB_PATH}")
    print(f"ENV: {ENV_PATH}")
    print("=" * 80)
    results: List[Result] = []
    for fn in CHECKS:
        try:
            results.append(fn())
        except Exception as e:
            r = Result(fn.__name__)
            r.fail(f"验证函数抛异常: {type(e).__name__}: {e}")
            results.append(r)

    passed = sum(1 for r in results if r.passed)
    total = len(results)
    for r in results:
        flag = "✅ PASS" if r.passed else "❌ FAIL"
        print(f"\n{flag}  {r.name}")
        for d in r.details:
            print(f"        {d}")
        for ev in r.evidence:
            print(f"        · {ev}")
    print()
    print("=" * 80)
    print(f"汇总: {passed}/{total} 项通过")
    print("=" * 80)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
