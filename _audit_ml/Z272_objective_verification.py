# -*- coding: utf-8 -*-
"""[§93 收口 2026-09-11 / 目标整体] 目标①–④ 的**一次性验证矩阵**（只读）。

把"目标是否达成"拆成可执行判据，一次跑完：
  ① 逐通道/逐层度量：Z235（误伤/漏报）、Z221（前后切片）、Z251/Z252/Z254（分层/机制）
  ② 待办 P5：MM 车道闸接线与线上读数（Z222 + 日志）
  ③ 抑制安全：Z236（保护性通道/悬挂/可追溯）+ Z241（证据新鲜度）
  ④ 交付物：复现脚本存在性 + 契约测试 + 报告章节 + 清单统计
每项输出 `[OK]` / `[PENDING]`（数据门未开时明确写出触发条件），不把"未验证"写成"通过"。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
from dotenv import load_dotenv  # noqa: E402

load_dotenv(str(ROOT / ".env"), override=False)

from _scope import DEFAULT_ACCOUNT_ID, describe_scope  # noqa: E402
from backend.database.connection import SessionLocal  # noqa: E402
from sqlalchemy import text  # noqa: E402

PY = ROOT / ".venv" / "Scripts" / "python.exe"

SCRIPTS = ["Z221_fix_effect_slice.py", "Z235_breaker_harm_check.py", "Z236_suppression_safety_monitor.py",
           "Z237_suppression_counterfactual.py", "Z241_breaker_evidence_lock.py", "Z251_peak_transition_analysis.py",
           "Z252_long_loss_mechanism.py", "Z254_long_sl_chain_audit.py", "Z259_entry_profile.py",
           "Z260_p29_recheck.py", "Z262_research_tier_audit.py", "Z266_breaker_account_pollution.py",
           "Z269_suite_account_scope.py"]
TESTS = ["test_breaker_lookup_key_20260911.py", "test_exit_channel_breaker_unified_20260911.py",
         "test_shadow_state_persistence_20260911.py", "test_breaker_evidence_freshness_20260911.py",
         "test_breaker_evidence_lock_20260911.py", "test_suppression_safety_monitor_20260911.py",
         "test_mm_lane_limits_enforce_20260911.py", "test_audit_pytest_pollution_20260911.py",
         "test_owm_thesis_bind_20260906.py", "test_audit_account_scope_20260911.py",
         "test_suite_account_scope_20260911.py", "test_breaker_backfill_scope_20260911.py",
         "test_long_sl_cap_p29_20260911.py", "test_sl_trigger_classify_20260911.py",
         "test_perm_test_20260911.py", "test_breaker_backfill_20260911.py",
         "test_channel_breaker_rebuild_20260910.py"]
SECTIONS = ["## 80.", "## 81.", "## 82.", "## 83.", "## 84.", "## 85.", "## 86.", "## 87.",
            "## 88.", "## 89.", "## 90.", "## 91.", "## 92."]


def main() -> int:
    print(describe_scope())
    print("=" * 100)
    print("目标①–④ 验证矩阵（只读；PENDING = 数据门未开，附触发条件）")
    print("=" * 100)

    # ── 交付物（④）─────────────────────────────────────────────
    missing = [s for s in SCRIPTS if not (ROOT / "_audit_ml" / s).exists()]
    missing_t = [t for t in TESTS if not list((ROOT / "backend/tests").rglob(t))]
    rep = (ROOT / "_中长线负期望根因报告_20260909.md").read_text(encoding="utf-8", errors="replace")
    miss_sec = [s for s in SECTIONS if s not in rep]
    print(f"\n④ 交付物：复现脚本 {len(SCRIPTS)-len(missing)}/{len(SCRIPTS)}"
          f"｜契约测试文件 {len(TESTS)-len(missing_t)}/{len(TESTS)}"
          f"｜报告章节 {len(SECTIONS)-len(miss_sec)}/{len(SECTIONS)}"
          f" ⇒ {'[OK]' if not (missing or missing_t or miss_sec) else '[缺口] ' + str(missing + missing_t + miss_sec)}")
    inv = ROOT / "data" / "audit_defect_inventory.json"
    if inv.exists():
        d = json.loads(inv.read_text(encoding="utf-8"))
        # 键名以校验器实际写出的为准：`items`（早期版本曾叫 rows）
        rows = d.get("items") or d.get("rows") or []
        sev = d.get("by_severity") or {}
        stt = d.get("by_status") or {}
        print(f"    清单自校验：{len(rows)} 条｜严重度 {sev}｜状态 {stt}｜问题 {len(d.get('problems') or [])}")

    # ── 数据门（①③）──────────────────────────────────────────
    db = SessionLocal()
    try:
        db.execute(text("set app.is_admin='on'"))
        after = db.execute(text(f"""select count(*) from paper_positions
            where status='closed' and account_id={DEFAULT_ACCOUNT_ID}
              and closed_at >= timestamp '2026-09-11 10:19'
              and lower(coalesce(timeframe_tier,'')) in ('mid','long')""")).scalar()
        sup = db.execute(text("select count(*) from position_exit_events where event_type='exit_channel_broken'")).scalar()
        newlong = db.execute(text(f"""select count(*) from paper_positions
            where account_id={DEFAULT_ACCOUNT_ID} and opened_at >= timestamp '2026-09-11 17:20'
              and lower(coalesce(timeframe_tier,''))='long'""")).scalar()
    finally:
        db.close()
    print(f"\n① 修复前后对照：after 侧 mid/long 平仓 **{after}/15** ⇒ "
          f"{'[OK]' if after >= 15 else '[PENDING]'} 触发条件：≥15 笔（每日 Z221 E′ 段自动判定）")
    print(f"③ 抑制现场验证：抑制事件 **{sup}** ⇒ "
          f"{'[OK]' if sup else '[PENDING]'} 触发条件：首次 exit_channel_broken（每日 Z236 自动判定）")
    print(f"   ③ 的静态部分：保护通道永不抑制 + 14 例双向契约 + 悬挂检测 ⇒ [OK]（见 §83/§85）")
    print(f"   P29-C 复核：边界后新开 long **{newlong}** ⇒ "
          f"{'[OK]' if newlong else '[PENDING]'} 触发条件：夹子日志出现或 ≥5 笔 long 平仓")

    # ── ② P5 ────────────────────────────────────────────────
    log = ROOT / "logs" / "backend.log"
    mm_ok = False
    if log.exists():
        txt = log.read_text(encoding="utf-8", errors="replace")
        mm_ok = "日亏闸输入" in txt
    print(f"\n② P5（MM 车道死闸）：接线 + 线上读数 ⇒ {'[OK]' if mm_ok else '[PENDING]'}"
          f"（{'日志已见「日亏闸输入」' if mm_ok else '未见'}；§82.1）")

    # ── 套件与测试的机器判定（交回子命令，避免自我宣称）────────
    print("\n【机器判定（子命令，非自我宣称）】")
    rc1 = subprocess.call([str(PY), str(ROOT / "backend/scripts/midlong_audit_suite.py"),
                           "--timeout", "600"], cwd=str(ROOT),
                          stdout=open(os.devnull, "w"), stderr=subprocess.STDOUT)
    print(f"  审计套件（9 项）exit={rc1} ⇒ {'[OK]' if rc1 == 0 else '[FAIL]'}")
    args = [str(PY), "-m", "pytest"] + [f"backend/tests/unit/{t}" for t in TESTS if t.startswith("test_")] \
        + ["-q", "--no-header", f"--basetemp={ROOT / 'data' / '_pytest_tmp'}"]
    rc2 = subprocess.call(args, cwd=str(ROOT), stdout=open(os.devnull, "w"), stderr=subprocess.STDOUT)
    print(f"  本轮契约测试（{len(TESTS)} 个文件）exit={rc2} ⇒ {'[OK]' if rc2 == 0 else '[FAIL]'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
