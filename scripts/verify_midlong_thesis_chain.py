"""MLTO 研判链验收 — 5 tick ingest + readiness 单调性 + 模块导入。"""

from __future__ import annotations

import os
import sys
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

PASS = FAIL = 0


def check(name: str, ok: bool, detail: str = ""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} — {detail}")


def _mock_qual_tick(thesis_summary: str, direction: str = "long", delta: int = 3):
    from backend.services.mlto.types import QualUpdateResult
    return QualUpdateResult(
        direction=direction,
        conviction_delta=delta,
        thesis_summary=thesis_summary,
        cited_event_ids=[],
        missing_evidence=[],
        invalidation={},
        recommend_open=False,
    )


def main():
    print("=== verify_midlong_thesis_chain ===\n")

    # 1. 模块导入（旧 run_mlto_tick 已删，主路径是 LLM 主脑）
    try:
        from backend.services.mlto.brain import can_open, thesis_is_fresh, midlong_new_open_halted
        from backend.services.mlto import thesis_store, decision_hub, evidence_ingest
        from backend.services.mlto.db_models import MltoThesis, MltoMemoryEvent
        check("MLTO 主脑模块导入", True)
        check("can_open 可调用", callable(can_open) and callable(thesis_is_fresh))
        check("紧急停机 helper", callable(midlong_new_open_halted))
    except Exception as exc:
        check("MLTO 主脑模块导入", False, str(exc))
        print(f"\n合计 PASS={PASS} FAIL={FAIL}")
        sys.exit(1)

    # 2. Settings flags
    try:
        from backend.config import settings
        check("MIDLONG_THESIS_LEDGER_ENABLED 默认 true", getattr(settings, "MIDLONG_THESIS_LEDGER_ENABLED", False))
        check("MIDLONG_QUANT_BRIEF_HARD_GATE 默认 false", not getattr(settings, "MIDLONG_QUANT_BRIEF_HARD_GATE", True))
        check("MIDLONG_THESIS_OPEN_GATE 默认 true", getattr(settings, "MIDLONG_THESIS_OPEN_GATE", False))
    except Exception as exc:
        check("Settings flags", False, str(exc))

    # 3. Decision Hub fuse
    try:
        from backend.services.mlto.types import Signal
        sigs = [
            Signal("quant_trend", 0.72, 0.8, "quant"),
            Signal("orch_mid", 0.65, 0.7, "orch"),
        ]
        hub = decision_hub.fuse_signals(sigs, "mid")
        check("Decision Hub composite > 0", hub.composite > 0)
        check("Decision Hub open_readiness 0-100", 0 <= hub.open_readiness <= 100)
    except Exception as exc:
        check("Decision Hub", False, str(exc))

    # 4. 主脑闸 + thesis 落库（不再模拟已删除的 run_mlto_tick）
    session_id = f"verify-{uuid.uuid4().hex[:8]}"
    symbol = "WIF"
    try:
        from datetime import datetime, timedelta, timezone
        from backend.database.connection import AnalyticsSessionLocal, AnalyticsBase, analytics_engine
        from backend.services.mlto.db_models import MltoThesis  # noqa: F401
        from backend.services.mlto.types import ThesisDTO

        now = datetime.now(timezone.utc)
        fresh = ThesisDTO(
            thesis_id="v1", session_id=session_id, symbol=symbol, tier="mid",
            direction="long", recommend_open=True, accepted=True,
            expires_at=now + timedelta(hours=2), analysis_run_id="verify-fresh",
        )
        check("新鲜 accepted 论题可开", can_open(fresh))
        stale = ThesisDTO(
            thesis_id="v2", session_id=session_id, symbol=symbol, tier="mid",
            direction="long", recommend_open=True, accepted=True,
            expires_at=now - timedelta(minutes=1),
        )
        check("过期论题不可开", not can_open(stale) and not thesis_is_fresh(stale))

        AnalyticsBase.metadata.create_all(bind=analytics_engine)
        db = AnalyticsSessionLocal()
        t = thesis_store.get_or_create(session_id, symbol, "mid", db=db)
        t.direction = "long"
        t.recommend_open = True
        t.accepted = True
        t.expires_at = now + timedelta(hours=2)
        t.analysis_run_id = "verify-run"
        thesis_store._persist(db, t)
        thesis_store.clear_cache()
        rows = thesis_store.list_session_theses(session_id, db=db)
        check("DB 持久化 thesis", len(rows) >= 1)
        db.close()
    except Exception as exc:
        check("主脑闸/thesis 落库", False, str(exc))

    # 5. regime_reset + DB 恢复
    try:
        from backend.database.connection import AnalyticsSessionLocal, AnalyticsBase, analytics_engine
        from backend.services.mlto.db_models import MltoThesis, MltoDebateLog
        from backend.services.mlto.types import ThesisDTO, HubDecision

        AnalyticsBase.metadata.create_all(bind=analytics_engine)
        adb = AnalyticsSessionLocal()
        sid = f"restore-{uuid.uuid4().hex[:8]}"
        t = thesis_store.get_or_create(sid, "BTC", "mid", "hash_a", db=adb)
        t.thesis_summary = "restore test"
        t.review_count = 3
        thesis_store._persist(adb, t)
        tid = t.thesis_id
        thesis_store.clear_cache()
        t2 = thesis_store.get_or_create(sid, "BTC", "mid", "hash_b", db=adb)
        check("重启后 DB 恢复 thesis", t2.thesis_id == tid and t2.review_count >= 3)
        thesis_store.apply_regime_reset(t2, "hash_b", db=adb)
        from backend.services.mlto.db_models import MltoThesisEvent
        ev_count = (
            adb.query(MltoThesisEvent)
            .filter(MltoThesisEvent.thesis_id == tid, MltoThesisEvent.event_type == "regime_reset")
            .count()
        )
        check("regime_reset 事件", ev_count >= 1)
        adb.close()
    except Exception as exc:
        check("regime_reset/DB恢复", False, str(exc))

    # 6. debate + tranche
    try:
        from backend.services.mlto import debate_layer, tranche_gate
        from backend.services.mlto.types import PerceptionPacket, ThesisDTO, HubDecision

        pkt = PerceptionPacket(
            symbol="ETH", tier="mid", session_id="d", ts=time.time(),
            price=100,
            market_summary_sym={},
            orchestrator={"mid_bias": "bullish"},
            quant_brief={},
            analyst_reports={},
        )
        mem = []
        check("灰区 should_debate", debate_layer.should_debate(0.55, "mid"))
        sig = debate_layer.run_debate(pkt, mem, 0.55)
        check("debate_signal 0-1", 0 <= sig <= 1)
        th = ThesisDTO(thesis_id="d1", session_id="d", symbol="ETH", tier="mid")
        hub = HubDecision(action="BUILD", direction="long", composite=0.72, adjusted=0.72,
                          consistency=0.8, open_readiness=72, reason_text="test")
        margin = tranche_gate.compute_margin_pct(th, hub, has_position=False)
        check("BUILD 首仓 margin <= 30%", margin <= 0.30, f"margin={margin}")
    except Exception as exc:
        check("debate/tranche", False, str(exc))

    # 7. 主脑 can_open + 非法 hub 动作拦截（WAIT 在阶段3b 已是合法动作）
    try:
        from backend.services.mlto import open_gate
        from backend.services.mlto.types import PerceptionPacket, ThesisDTO, HubDecision

        th = ThesisDTO(
            thesis_id="g1", session_id="g", symbol="X", tier="mid",
            review_count=5, open_readiness=50, direction="long",
            recommend_open=True, accepted=True,
        )
        th.stable_since = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        th.updated_at = th.stable_since
        hub = HubDecision(
            action="HOLD", direction="long", composite=0.35, adjusted=0.35,
            consistency=0.7, open_readiness=35, reason_text="hold",
        )
        pkt = PerceptionPacket(
            symbol="X", tier="mid", session_id="g", ts=time.time(), price=1,
            market_summary_sym={"price": 1},
            orchestrator={},
            quant_brief={},
            analyst_reports={},
            pre_screener_passed=True,
        )
        ok, reason = open_gate.allow(th, hub, pkt, {})
        check("非法 hub HOLD 拦截", not ok and "hub_action" in reason, reason)
        hold_th = ThesisDTO(
            thesis_id="g2", session_id="g", symbol="X", tier="mid",
            direction="long", recommend_open=False, accepted=True,
            expires_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc)
            + __import__("datetime").timedelta(hours=2),
            analysis_run_id="verify-hold",
        )
        check("主脑 can_open 拒绝 hold 论题", not can_open(hold_th))
    except Exception as exc:
        check("open_gate / can_open", False, str(exc))

    # 8. Agent update_thesis API
    try:
        from backend.services.swing_agent import swing_agent
        from backend.services.trend_agent import trend_agent
        check("SwingAgent.update_thesis 存在", callable(getattr(swing_agent, "update_thesis", None)))
        check("TrendAgent.update_thesis 存在", callable(getattr(trend_agent, "update_thesis", None)))
    except Exception as exc:
        check("Agent update_thesis", False, str(exc))

    # 9. API routes 注册
    try:
        from backend.api.mlto_routes import router
        paths = [getattr(r, "path", "") for r in router.routes]
        check("thesis/summary route", any("thesis/summary" in p for p in paths))
    except Exception as exc:
        check("mlto_routes", False, str(exc))

    print(f"\n合计 PASS={PASS} FAIL={FAIL}")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
