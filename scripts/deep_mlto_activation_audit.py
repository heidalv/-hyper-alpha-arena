#!/usr/bin/env python3
"""深度审计：MLTO 是「代码存在」还是「生产路径真在跑」。"""
from __future__ import annotations

import glob
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PASS = FAIL = WARN = 0


def ok(name: str, cond: bool, detail: str = "", warn: bool = False) -> None:
    global PASS, FAIL, WARN
    if cond:
        PASS += 1
        print(f"  [PASS] {name}: {detail}")
    elif warn:
        WARN += 1
        print(f"  [WARN] {name}: {detail}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}: {detail}")


def scan_logs(log_dir: Path) -> dict:
    patterns = {
        "mlto_thesis_llm": re.compile(r"MLTO:thesis_update|LLM thesis_update"),
        "mlto_exec": re.compile(r"\[MLTO\] exec "),
        "mlto_maintain": re.compile(r"\[MLTO\] maintain tick"),
        "swing_analyze": re.compile(r"SwingAgent:analyze"),
        "tick_throttle": re.compile(r"批量限流|统一分析限流"),
        "prescreener_midlong": re.compile(r"PreScreener.*(mid|long) tier", re.I),
    }
    counts = {k: 0 for k in patterns}
    for fp in sorted(log_dir.glob("backend.log*"))[:5]:
        try:
            text = fp.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for key, pat in patterns.items():
            counts[key] += len(pat.findall(text))
    return counts


def main() -> int:
    print("=" * 60)
    print("MLTO 深度激活审计（诚实版）")
    print("=" * 60)

    from backend.config import settings as s

    print("\n=== 1) 配置开关（当前进程 import 值）===")
    ok("MIDLONG_THESIS_LEDGER_ENABLED", s.MIDLONG_THESIS_LEDGER_ENABLED, str(s.MIDLONG_THESIS_LEDGER_ENABLED))
    ok("MIDLONG_MLTO_CONTROLS_EXEC", s.MIDLONG_MLTO_CONTROLS_EXEC, str(s.MIDLONG_MLTO_CONTROLS_EXEC))
    ok("MIDLONG_AI_MANDATORY", s.MIDLONG_AI_MANDATORY, str(s.MIDLONG_AI_MANDATORY))
    ok("MIDLONG_THESIS_OPEN_GATE", getattr(s, "MIDLONG_THESIS_OPEN_GATE", True), str(getattr(s, "MIDLONG_THESIS_OPEN_GATE", True)))
    ok("HYBRID 短线", s.HYBRID_SIGNAL_MODE_ENABLED, str(s.HYBRID_SIGNAL_MODE_ENABLED))

    print("\n=== 2) 代码路径是否接线 ===")
    src = (ROOT / "backend/services/full_auto_trading_service.py").read_text(encoding="utf-8")
    ok("Execute 含 _execute_mlto_lane", "_execute_mlto_lane" in src)
    ok("Execute 受 MIDLONG_MLTO_CONTROLS_EXEC 门控", "MIDLONG_MLTO_CONTROLS_EXEC" in src)
    ok("统一循环含 _maintain_mlto_theses_for_session", "_maintain_mlto_theses_for_session" in src)
    orch = (ROOT / "backend/services/mlto/orchestrator.py").read_text(encoding="utf-8")
    ok("MLTO tick 内仍调 PreScreener", "SignalPreScreener" in orch, warn=True)
    gate = (ROOT / "backend/services/mlto/open_gate.py").read_text(encoding="utf-8")
    ok("open_gate 仍检查 pre_screener", "pre_screener_passed" in gate, warn=True)

    print("\n=== 3) 运行时 DB / API 状态 ===")
    session_id = "fa_e55efe8e92"
    try:
        import urllib.request
        with urllib.request.urlopen(
            f"http://127.0.0.1:8000/api/mlto/sessions/{session_id}/thesis/summary", timeout=15
        ) as r:
            summary = json.loads(r.read().decode())
        theses = summary.get("theses") or []
        pending = sum(1 for t in theses if t.get("pending"))
        with_text = sum(1 for t in theses if (t.get("thesis_summary") or "").strip())
        can_open = sum(1 for t in theses if (t.get("gate_status") or {}).get("can_open"))
        rev3 = sum(1 for t in theses if int(t.get("review_count") or 0) >= 3)
        ok("API thesis/summary 有数据", len(theses) > 0, f"rows={len(theses)}")
        ok("至少一条 LLM 研判摘要", with_text >= 1, f"with_summary={with_text}/{len(theses)}")
        ok("存在可开单 thesis", can_open >= 1, f"can_open={can_open}", warn=(can_open == 0 and with_text > 0))
        ok("存在 review>=3", rev3 >= 1, f"review>=3={rev3}", warn=(rev3 == 0 and with_text > 0))
        ok("无 pending 占位", pending == 0, f"pending={pending}", warn=pending > 0)
    except Exception as exc:
        ok("API thesis/summary", False, str(exc))

    try:
        from backend.database.connection import AnalyticsSessionLocal
        from backend.services.mlto import db_models

        db = AnalyticsSessionLocal()
        try:
            n_t = db.query(db_models.MltoThesis).filter(db_models.MltoThesis.session_id == session_id).count()
            n_e = db.query(db_models.MltoThesisEvent).count()
            n_m = db.query(db_models.MltoMemoryEvent).count()
            ok("DB mlto_thesis 有记录", n_t > 0, f"count={n_t}")
            ok("DB thesis_events 有记录", n_e > 0, f"count={n_e}")
            ok("DB memory_events 有记录", n_m > 0, f"count={n_m}")
        finally:
            db.close()
    except Exception as exc:
        ok("DB analytics", False, str(exc))

    print("\n=== 4) 生产日志证据（近几份 backend.log*）===")
    log_counts = scan_logs(ROOT / "logs")
    for k, v in log_counts.items():
        if k == "mlto_thesis_llm":
            ok("日志有 MLTO LLM 调用", v > 0, f"hits={v}", warn=v == 0)
        elif k == "mlto_exec":
            ok("日志有 [MLTO] exec", v > 0, f"hits={v}", warn=v == 0)
        elif k == "swing_analyze":
            ok("日志 SwingAgent:analyze（旧路径）", True, f"hits={v}", warn=v > 50)
        elif k == "tick_throttle":
            ok("日志 tick 限流", True, f"hits={v}", warn=v > 0)
        else:
            print(f"  [INFO] {k}: {v}")

    print("\n=== 5) 验收脚本盲区（说明）===")
    print("  · verify_midlong_thesis_chain：mock tick，不测 FullAuto 5min 循环")
    print("  · verify_mlto_runtime：只查 API 存在 + 1 条 session，不查 can_open")
    print("  · mlto_design_audit：静态 import/文件存在，不等于生产激活")

    print("\n" + "=" * 60)
    print(f"合计 PASS={PASS} FAIL={FAIL} WARN={WARN}")
    if FAIL:
        print("结论：代码在，但生产链路未完全激活或门控/路径有缺陷")
        return 1
    if WARN:
        print("结论：部分激活；存在门控过严、双路径、限流等运行问题")
        return 2
    print("结论：配置与运行时一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
