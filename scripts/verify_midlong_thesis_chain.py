"""MLTO 研判链验收 — 5 tick ingest + readiness 单调性 + 模块导入。"""

from __future__ import annotations

import os
import sys
import time
import uuid
from unittest.mock import patch

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

    # 1. 模块导入
    try:
        from backend.services.mlto.orchestrator import run_mlto_tick, MltoOrchestrator
        from backend.services.mlto import thesis_store, decision_hub, evidence_ingest
        from backend.services.mlto.db_models import MltoThesis, MltoMemoryEvent
        check("MLTO 模块导入", True)
    except Exception as exc:
        check("MLTO 模块导入", False, str(exc))
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

    # 4. 5 tick 模拟（mock LLM，内存 DB 可选）
    session_id = f"verify-{uuid.uuid4().hex[:8]}"
    symbol = "WIF"
    readiness_series: list[int] = []

    summaries = [
        "WIF 4h 结构偏多，等待放量确认",
        "衍生品 funding 转正，多头叙事加强",
        "1h 回踩 EMA 支撑，中线入场窗口",
        "Hub 与 LLM 方向一致，就绪度提升",
        "证据链完整，接近开单阈值",
        "多周期共振加强，review 持续累积",
        "open_readiness 稳步上升",
        "接近 BUILD 阈值，仍待 stable 满足",
    ]

    tick_idx = [0]
    tick_count = 8

    def _fake_qual(*_a, **_kw):
        i = min(tick_idx[0], len(summaries) - 1)
        tick_idx[0] += 1
        return _mock_qual_tick(summaries[i], "long", delta=4)

    market_summary = {
        symbol: {
            "current_price": 2.5,
            "orchestrator": {
                "mid_bias": "bullish",
                "mid_confidence": 0.55 + 0.05 * tick_idx[0],
                "long_bias": "bullish",
                "long_confidence": 0.6,
                "recommended_slots": ["mid"],
                "slot_actions": {"mid": "create"},
            },
            "indicators_1h": {"rsi": 58, "ema_trend": "bullish", "macd_hist": 0.05, "vol_ratio": 1.1},
            "indicators_4h": {"rsi": 52, "ema_trend": "bullish"},
        }
    }

    class _Session:
        account_id = 1

        def __init__(self, sid: str):
            self.session_id = sid

    try:
        from backend.database.connection import AnalyticsSessionLocal, AnalyticsBase, analytics_engine
        from backend.services.mlto.db_models import MltoThesis  # noqa: F401 — register tables

        AnalyticsBase.metadata.create_all(bind=analytics_engine)
        db = AnalyticsSessionLocal()
        mock_session = _Session(session_id)

        with patch("backend.services.mlto.qual_layer.update_thesis", side_effect=_fake_qual):
            for tick in range(tick_count):
                market_summary[symbol]["orchestrator"]["mid_confidence"] = 0.5 + tick * 0.04
                result = run_mlto_tick(
                    session_id=session_id,
                    symbol=symbol,
                    tier="mid",
                    market_summary=market_summary,
                    analyst_reports={},
                    session=mock_session,
                    db=db,
                    portfolio={"positions": []},
                    persistence_state={},
                    slot_action="create",
                    trading_mode="paper",
                )
                if result.thesis:
                    readiness_series.append(result.thesis.open_readiness)
                check(f"tick {tick + 1} 有 thesis", result.thesis is not None)
                check(f"tick {tick + 1} reason 含 MLTO", "[MLTO]" in (result.reason or ""))

        check(f"{tick_count} tick 完成", tick_idx[0] >= tick_count)
        check(f"review_count >= {tick_count}", (result.thesis.review_count if result.thesis else 0) >= tick_count)

        # readiness 非严格单调，但末 tick 应 >= 首 tick（证据累积）
        if len(readiness_series) >= 2:
            check(
                "open_readiness 末 tick >= 首 tick",
                readiness_series[-1] >= readiness_series[0],
                f"{readiness_series}",
            )

        rows = thesis_store.list_session_theses(session_id, db=db)
        check("DB 持久化 thesis", len(rows) >= 1)

        db.close()
    except Exception as exc:
        check("8 tick 模拟", False, str(exc))

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

    # 7. hub BUILD 阈值不开仓（open_gate）
    try:
        from backend.services.mlto import open_gate
        from backend.services.mlto.types import PerceptionPacket, ThesisDTO, HubDecision

        th = ThesisDTO(
            thesis_id="g1", session_id="g", symbol="X", tier="mid",
            review_count=5, open_readiness=50, direction="long",
        )
        th.stable_since = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        th.updated_at = th.stable_since
        hub = HubDecision(
            action="WAIT", direction="long", composite=0.35, adjusted=0.35,
            consistency=0.7, open_readiness=35, reason_text="wait",
        )
        pkt = PerceptionPacket(
            symbol="X", tier="mid", session_id="g", ts=time.time(), price=1,
            market_summary_sym={},
            orchestrator={},
            quant_brief={},
            analyst_reports={},
            pre_screener_passed=True,
        )
        ok, reason = open_gate.allow(th, hub, pkt, {})
        check("hub WAIT 零开仓", not ok and "hub_action" in reason)
    except Exception as exc:
        check("open_gate WAIT", False, str(exc))

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
