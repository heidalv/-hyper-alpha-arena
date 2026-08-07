"""OpenCode 层全面验收脚本（无需 pytest）。"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except ImportError:
    pass

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} — {detail}")


def main():
    print("=== OpenCode 层全面验收 ===\n")

    # 1. 模块导入
    print("1. 模块导入")
    try:
        from backend.services.strategy_runtime_report import run_report_tick, load_latest_report
        from backend.services.paper_pace_controller import paper_pace_controller
        from backend.services.runtime_tuning_store import get_tuning_float, get_all_tuning
        from backend.services.decision_policy_engine import evaluate
        from backend.services.opencode_bridge import get_bridge_status, health_check
        from backend.services.opencode_context_pack import build_context_pack
        from backend.services.opencode_action_router import route_analysis_result
        from backend.services.opencode_shadow_worker import shadow_status
        from backend.database.models import OpenCodeInsightDB, OpenCodeEvolutionProposalDB
        check("所有核心模块导入", True)
    except Exception as e:
        check("所有核心模块导入", False, str(e))
        return

    # 2. SRR
    print("\n2. StrategyRuntimeReport")
    from backend.database.connection import SessionLocal
    db = SessionLocal()
    try:
        paths = run_report_tick(windows=["6h", "24h"], domains=["ai", "arb"])
        check("SRR 生成报告", len(paths) >= 2, f"paths={paths}")
        latest = load_latest_report("24h", "ai")
        check("SRR latest 可读", latest is not None and "window" in latest)
    except Exception as e:
        check("SRR", False, str(e))
    finally:
        db.close()

    # 3. Pace
    print("\n3. PaperPaceController")
    paper_pace_controller.set_gear("turbo", manual=False)
    check("turbo tick=45", paper_pace_controller.get_tick_seconds() == 45)
    paper_pace_controller.force_downshift(1)
    check("降档后 gear=warm", paper_pace_controller.gear == "warm")
    paper_pace_controller.set_gear("turbo", manual=False)

    # 4. RuntimeTuning
    print("\n4. RuntimeTuningStore")
    v0 = get_tuning_float("master_reduce_min_loss_pct", 0.05)
    from backend.services.runtime_tuning_store import apply_patches, rollback_snapshot
    apply_patches({"master_reduce_min_loss_pct": 0.08}, proposal_id=99999)
    v1 = get_tuning_float("master_reduce_min_loss_pct", 0.05)
    check("热改生效", abs(v1 - 0.08) < 0.001, f"v1={v1}")
    rollback_snapshot(99999)
    from backend.services.runtime_tuning_store import invalidate_cache
    invalidate_cache()
    v2 = get_tuning_float("master_reduce_min_loss_pct", 0.05)
    check("rollback 恢复", abs(v2 - v0) < 0.001, f"v2={v2}")

    # 5. Policy YAML
    print("\n5. DecisionPolicyEngine")
    r = evaluate("master_close", {"action": "reduce", "floating_loss_pct": 0.02, "risk_score": 50})
    check("YAML block reduce", r.effect == "block", r.effect)

    # 6. master_close_guard + policy
    print("\n6. MasterCloseGuard + Policy")
    from backend.services.master_close_guard import check_master_close_hardfact
    r2 = check_master_close_hardfact(
        tier="mid", action="close", entry_price=100, mark_price=99, sl_price=90,
        unrealized_pnl=-1, margin=100, risk_score=40,
    )
    check("小亏 close 被拦", r2.allow is False)

    # 7. Context Pack + 数据质量
    print("\n7. ContextPack")
    db = SessionLocal()
    try:
        from backend.database.connection import DATABASE_URL
        pack = build_context_pack(db, window="24h", domain="ai")
        check("ContextPack 字段齐全", all(k in pack for k in ("runtime_report", "pace_gear", "whitelist_keys", "data_quality")))
        dq = pack.get("data_quality") or {}
        closed = int(dq.get("runtime_report_total_closed") or 0)
        check(
            "SRR 24h 已平仓 >= 5（分析就绪）",
            dq.get("sufficient_for_analysis") is True and closed >= 5,
            f"closed={closed} db={DATABASE_URL.split('@')[-1] if '@' in DATABASE_URL else DATABASE_URL[:40]}",
        )
    except Exception as e:
        check("ContextPack", False, str(e))
    finally:
        db.close()

    # 8. Bridge status
    print("\n8. OpenCode Bridge")
    st = get_bridge_status()
    check("bridge status", "enabled" in st)
    hc = health_check()
    check("serve health (可能离线)", True, f"healthy={hc}")

    # 9. Shadow worker
    print("\n9. Shadow Worker")
    ss = shadow_status()
    check("shadow status", "port" in ss)

    # 10. API routes
    print("\n10. API Routes")
    try:
        from backend.api.opencode_routes import opencode_status, get_paper_pace
        check("opencode_status", "pace" in opencode_status())
        check("paper_pace", "gear" in get_paper_pace())
    except Exception as e:
        check("API routes", False, str(e))

    # 11. DB tables
    print("\n11. DB 表")
    db = SessionLocal()
    try:
        from backend.database.connection import engine, Base
        from backend.database import models  # noqa: F401
        Base.metadata.create_all(bind=engine)
        db.query(OpenCodeInsightDB).limit(1).all()
        db.query(OpenCodeEvolutionProposalDB).limit(1).all()
        check("OpenCode ORM 表可查询", True)
    except Exception as e:
        check("OpenCode ORM 表", False, str(e))
    finally:
        db.close()

    # 12. position_hold_time tuning
    print("\n12. position_hold_time + pace")
    from backend.services.position_hold_time import resolve_tier_review_seconds
    class _Pos:
        trade_nature = "swing"
        timeframe_tier = "short"
    sec = resolve_tier_review_seconds(_Pos())
    check("hold sec > 0", sec > 0, str(sec))

    print(f"\n=== 结果: {PASS} PASS / {FAIL} FAIL ===")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
