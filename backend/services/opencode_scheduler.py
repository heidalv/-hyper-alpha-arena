"""OpenCode / SRR 定时任务注册。"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

SRR_JOB_ID = "strategy_runtime_report_tick"
MATURITY_JOB_ID = "maturity_controller_tick"
OPENCODE_6H_JOB = "opencode_digest_6h"
OPENCODE_24H_JOB = "opencode_analysis_24h"
OPENCODE_PROPOSAL_EVAL = "opencode_proposal_eval"
OPENCODE_PROPOSAL_REVIEW = "opencode_proposal_review"
PACE_EVAL_JOB = "paper_pace_eval"
SIDECAR_WATCHDOG_JOB = "opencode_sidecar_watchdog"
HEALTH_DIGEST_JOB = "opencode_health_digest_1h"


_opencode_registered = False


# ─────────────────────────────────────────────────────────────
# Hermes 任务时间轴元数据 + 运行追踪器
# 为前端「四层几点开始/预计几点/是否运行」提供数据源
# ─────────────────────────────────────────────────────────────

# job_id -> {layer, label, interval_s, desc}
HERMES_TASK_META: dict = {
    "hermes_wisdom_accumulate":     {"layer": "L1", "label": "智慧积累",   "interval_s": 3600,  "desc": "扫描提案提取智慧记录"},
    "hermes_meta_analysis":         {"layer": "L1", "label": "模式库更新", "interval_s": 21600, "desc": "聚合参数效果模式"},
    "hermes_prompt_optimize":       {"layer": "L2", "label": "Prompt优化",  "interval_s": 43200, "desc": "LLM 生成优化 prompt"},
    "hermes_ab_test_eval":          {"layer": "L2", "label": "A/B评估",     "interval_s": 14400, "desc": "评估运行中 A/B 测试"},
    "hermes_architecture_evolution": {"layer": "L3", "label": "架构进化",   "interval_s": 86400, "desc": "发现架构改进提案"},
    "hermes_strategy_genesis":      {"layer": "L4", "label": "策略创生",   "interval_s": 86400, "desc": "生成并孵化新策略"},
    "hermes_genesis_check":         {"layer": "L4", "label": "孵化检查",   "interval_s": 21600, "desc": "检查孵化达标情况"},
}


class _HermesRunTracker:
    """记录每个定时任务的上次开始/结束时间、状态、是否运行中。

    内存 + DB 双写：mark_start/mark_done 同步到 task_run_log 表，
    __init__ 启动时从 DB 恢复，解决"重启后运行记录丢失"问题。
    DB 写失败不阻断主流程（降级为纯内存态）。
    """

    def __init__(self):
        self._state: dict = {}  # job_id -> {last_started_at, last_finished_at, last_status, is_running, last_error}
        self._restore_from_db()

    def _restore_from_db(self) -> None:
        """启动时从 task_run_log 恢复内存态（DB 不可用时静默降级）。"""
        try:
            from backend.services.hermes_db import get_all_task_runs, init_hermes_db
            init_hermes_db()
            for job_id, row in get_all_task_runs().items():
                self._state[job_id] = {
                    "last_started_at": _parse_iso(row.get("last_started_at")),
                    "last_finished_at": _parse_iso(row.get("last_finished_at")),
                    "last_status": row.get("last_status"),
                    # 重启时一律视为未运行（进程已重启，旧的 running 状态无效）
                    "is_running": False,
                    "last_error": row.get("last_error"),
                }
            if self._state:
                logger.info("[Tracker] 从 DB 恢复 %d 个任务的运行记录", len(self._state))
        except Exception as e:
            logger.debug("[Tracker] DB 恢复失败(降级为空内存态): %s", e)

    def mark_start(self, job_id: str):
        now = datetime.now(timezone.utc)
        self._state[job_id] = {
            "last_started_at": now,
            "last_finished_at": self._state.get(job_id, {}).get("last_finished_at"),
            "last_status": "running",
            "is_running": True,
            "last_error": None,
        }
        self._db_upsert(job_id, last_started_at=now.isoformat(), last_status="running", last_error=None)

    def mark_done(self, job_id: str, error: Optional[str] = None):
        now = datetime.now(timezone.utc)
        started = self._state.get(job_id, {}).get("last_started_at")
        duration_ms = int((now - started).total_seconds() * 1000) if started else None
        st = self._state.setdefault(job_id, {})
        st["last_finished_at"] = now
        st["is_running"] = False
        st["last_status"] = "error" if error else "ok"
        st["last_error"] = error
        self._db_upsert(
            job_id,
            last_finished_at=now.isoformat(),
            last_status=st["last_status"],
            last_error=error,
            last_run_duration_ms=duration_ms,
        )
        # run_count 自增（独立语句，避免覆盖其他列）
        try:
            from backend.services.hermes_db import increment_task_run_count
            increment_task_run_count(job_id)
        except Exception as e:
            logger.debug("[Tracker] run_count 自增失败 %s: %s", job_id, e)

    def snapshot(self, job_id: str) -> dict:
        return dict(self._state.get(job_id, {}))

    @staticmethod
    def _db_upsert(job_id: str, **fields) -> None:
        """写 DB（失败静默，不阻断主流程）。"""
        try:
            from backend.services.hermes_db import upsert_task_run
            upsert_task_run(job_id, **fields)
        except Exception as e:
            logger.debug("[Tracker] DB 写入失败 %s: %s", job_id, e)


def _parse_iso(s: Optional[str]):
    """ISO8601 字符串 -> datetime（失败返回 None）。"""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


hermes_run_tracker = _HermesRunTracker()


def _fmt_iso(dt) -> Optional[str]:
    """datetime -> ISO8601 字符串；None 透传。"""
    if dt is None:
        return None
    try:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return None


def get_hermes_schedule_status() -> list:
    """返回 7 个 Hermes 任务的时间轴状态，供前端「几点开始/预计几点/是否运行」展示。

    每项字段：
      job_id, layer, label, desc, interval_s,
      last_started_at, last_finished_at, last_status, is_running, last_error,
      next_run_time, registered
    next_run_time 来自 APScheduler（内存态，重启后从注册时刻重新计算）。
    """
    # 从 APScheduler 取每个 job 的 next_run_time 与注册状态
    next_run_map: dict = {}
    registered = False
    try:
        from backend.services.scheduler import task_scheduler
        if task_scheduler and task_scheduler.scheduler:
            registered = True
            for job in task_scheduler.scheduler.get_jobs():
                if job.id in HERMES_TASK_META:
                    next_run_map[job.id] = job.next_run_time
    except Exception as e:
        logger.debug("[Hermes] schedule_status 取调度器状态失败: %s", e)

    result = []
    for jid, meta in HERMES_TASK_META.items():
        snap = hermes_run_tracker.snapshot(jid)
        result.append({
            "job_id": jid,
            "layer": meta["layer"],
            "label": meta["label"],
            "desc": meta["desc"],
            "interval_s": meta["interval_s"],
            "last_started_at": _fmt_iso(snap.get("last_started_at")),
            "last_finished_at": _fmt_iso(snap.get("last_finished_at")),
            "last_status": snap.get("last_status"),
            "is_running": bool(snap.get("is_running")),
            "last_error": snap.get("last_error"),
            "next_run_time": _fmt_iso(next_run_map.get(jid)),
            "registered": registered and (jid in next_run_map),
        })
    return result


def register_opencode_jobs() -> None:
    """注册 OpenCode 定时任务（幂等：重复调用跳过）"""
    global _opencode_registered
    if _opencode_registered:
        logger.info("[OpenCodeJobs] 已注册，跳过重复调用")
        return

    from backend.services.scheduler import task_scheduler
    from backend.database.connection import SessionLocal

    def _pace_eval_interval() -> int:
        try:
            from backend.config.settings import PAPER_PACE_EVAL_INTERVAL_S
            return int(PAPER_PACE_EVAL_INTERVAL_S or 1800)
        except Exception:
            return 1800

    if not task_scheduler.is_running():
        task_scheduler.start()

    def _srr_tick():
        try:
            from backend.services.strategy_runtime_report import run_report_tick
            paths = run_report_tick(windows=["6h", "24h"], domains=["ai", "arb"])
            logger.info("[SRR] 生成 %d 份报告", len(paths))

            # P3-10: 事件驱动 — SRR 完成后立即触发一次 OpenCode 分析
            # （不再等待下一个定时器，缩短提案反馈循环）
            if paths:
                try:
                    db = SessionLocal()
                    try:
                        from backend.config.settings import OPENCODE_ENABLED
                        if OPENCODE_ENABLED:
                            from backend.services.opencode_bridge import run_scheduled_analysis
                            # 快速检查：有足够数据才分析
                            from backend.services.strategy_runtime_report import get_or_build_runtime_report
                            runtime = get_or_build_runtime_report(
                                db, window="24h", domain="ai", force_refresh=False,
                            ) or {}
                            tc = int(runtime.get("total_closed") or 0)
                            if tc >= 5:
                                logger.info(
                                    "[SRR→OpenCode] P3-10 事件驱动: SRR 完成(tc=%d)，"
                                    "立即触发 24h AI 分析", tc,
                                )
                                run_scheduled_analysis(db, window="24h", domain="ai")
                            else:
                                logger.debug(
                                    "[SRR→OpenCode] P3-10 数据不足(tc=%d)，跳过事件驱动分析",
                                    tc,
                                )
                    finally:
                        db.close()
                except Exception as ev_err:
                    logger.debug("[SRR→OpenCode] P3-10 事件驱动失败(非致命): %s", ev_err)
        except Exception as err:
            logger.error("[SRR] tick 失败: %s", err, exc_info=True)

    def _maturity_tick():
        try:
            from backend.services.maturity_controller import run_maturity_tick
            run_maturity_tick()
        except Exception as err:
            logger.error("[Maturity] tick 失败: %s", err, exc_info=True)

    def _opencode_6h():
        db = SessionLocal()
        try:
            from backend.config.settings import OPENCODE_ENABLED
            if not OPENCODE_ENABLED:
                return
            from backend.services.opencode_bridge import run_scheduled_analysis
            run_scheduled_analysis(db, window="6h", domain="ai")
            # 2026-06-26: 新增 — 策略深度诊断（每6h选一个亏损最多的策略做128K深度复盘）
            try:
                from sqlalchemy import text
                worst = db.execute(text("""
                    SELECT strategy_id, count(*) as n, sum(pnl) as total_pnl
                    FROM strategy_trades
                    WHERE status='closed' AND closed_at >= NOW() - INTERVAL '24 hours'
                    GROUP BY strategy_id
                    HAVING sum(pnl) < 0
                    ORDER BY sum(pnl) ASC
                    LIMIT 1
                """)).fetchone()
                if worst and worst[0]:
                    from backend.services.opencode_bridge import run_strategy_deep_dive_enhanced
                    result = run_strategy_deep_dive_enhanced(db, worst[0])
                    logger.info("[OpenCodeJobs] 策略深度诊断 %s: %d findings", worst[0], len(result.get("findings", [])))
            except Exception as _dd_err:
                logger.debug(f"[OpenCodeJobs] 策略深度诊断跳过: {_dd_err}")
        except Exception as err:
            logger.error("[OpenCode] 6h 失败: %s", err, exc_info=True)
        finally:
            db.close()

    def _deep_loss_review_3h():
        """每3h深度亏损复盘 — 取近3h最大亏损交易，用 OpenCode 做128K上下文归因。"""
        db = SessionLocal()
        try:
            from backend.config.settings import OPENCODE_ENABLED
            if not OPENCODE_ENABLED:
                return
            from sqlalchemy import text
            worst = db.execute(text("""
                SELECT symbol, side, entry_price, exit_price, pnl, pnl_pct,
                       strategy_id,
                       COALESCE(decision_context ->> 'close_reason', 'unknown') AS close_reason,
                       closed_at
                FROM strategy_trades
                WHERE status='closed' AND pnl < -50
                AND closed_at >= NOW() - INTERVAL '3 hours'
                ORDER BY pnl ASC LIMIT 3
            """)).fetchall()
            if not worst:
                return
            from backend.services.llm_config_service import get_default_model_slug
            from backend.services.opencode_bridge import run_http_agent_message
            model_slug = get_default_model_slug(tier="deep") or "deepseek/deepseek-v4-pro"
            trades_text = "\n".join([
                f"- {r[0]} {r[1]} entry={float(r[2]):.1f} exit={float(r[3]):.1f} pnl={float(r[4]):+.1f}({float(r[5])*100:+.1f}%) reason={r[7]}"
                for r in worst
            ])
            system = (
                "你是加密永续合约交易复盘专家。深度分析亏损原因，输出 JSON。"
                "重点分析：方向是否逆势？止损是否被猎杀？入场时机是否错误？仓位是否过大？"
            )
            user = f"""## 近3小时最大亏损交易
{trades_text}

## 复盘要求
对每笔亏损交易深度归因：
1. 方向错误？当时的市场趋势是什么？为什么选了反向？
2. 止损猎杀？是否被主力扫了止损密集区？
3. 入场时机？是否追涨杀跌？是否在阻力/阻力位附近入场？
4. 仓位/杠杆？是否过度冒险？
5. 改进建议：具体的可执行调整（参数/逻辑/门控）

只返回 JSON：
{{"analyses": [{{"symbol": "", "root_cause": "", "lessons": [], "suggestions": []}}]}}
"""
            resp_text, err = run_http_agent_message(
                system_prompt=system, user_text=user,
                agent="alpha-arena", model_slug=model_slug,
                session_title="深度亏损复盘", timeout_s=180,
            )
            if resp_text:
                logger.info("[OpenCodeJobs] 深度亏损复盘完成: %d 字符", len(resp_text))
                # 写入 strategy_memories 作为教训
                try:
                    import json
                    from backend.services.decision_feedback_service import decision_feedback_service
                    from backend.database.connection import AnalyticsSessionLocal
                    adb = AnalyticsSessionLocal()
                    try:
                        result = json.loads(resp_text)
                        for analysis in result.get("analyses", []):
                            for suggestion in analysis.get("suggestions", [])[:3]:
                                decision_feedback_service.store_loss_lesson(
                                    adb,
                                    strategy_id=analysis.get("symbol", ""),
                                    symbol=analysis.get("symbol", ""),
                                    pnl=-abs(float(worst[0][4])),
                                    lesson=suggestion,
                                    regime="",
                                )
                        adb.commit()
                    finally:
                        adb.close()
                except Exception:
                    pass
        except Exception as err:
            logger.error("[OpenCodeJobs] 深度亏损复盘失败: %s", err, exc_info=True)
        finally:
            db.close()

    def _market_anomaly_5m():
        """每5分钟市场异常检测 — 监控波动率跳变/资金费率极端/清算级联。"""
        try:
            from backend.config.settings import OPENCODE_ENABLED
            if not OPENCODE_ENABLED:
                return
            from backend.services.crypto_alpha_signals import crypto_alpha
            from backend.services.intelligence_signal_engine import intelligence_signal_engine
            anomalies = []
            for sym in ["BTC", "ETH"]:
                # 1. 清算级联检测
                lm = crypto_alpha.liquidation_magnet(sym)
                if lm.available and lm.severity == "high":
                    anomalies.append(f"⚠️ {sym} 清算级联(high): {lm.note}")
                # 2. 资金费率极端
                intel = intelligence_signal_engine.compute_trading_signal(sym)
                if intel and intel.funding:
                    if abs(intel.funding.rate) > 0.001:
                        anomalies.append(f"⚠️ {sym} 资金费率极端({intel.funding.rate*100:.3f}%): {intel.funding.description}")
                # 3. 恐贪极端
                if intel and (intel.fear_greed_index < 15 or intel.fear_greed_index > 85):
                    zone = "极度恐惧" if intel.fear_greed_index < 15 else "极度贪婪"
                    anomalies.append(f"⚠️ {sym} {zone}(恐贪={intel.fear_greed_index:.0f})")
            if anomalies:
                logger.warning("[OpenCodeJobs] 市场异常: %s", " | ".join(anomalies))
                # 注入到 session event（前端可见）
                try:
                    from backend.services.full_auto_trading_service import full_auto_service
                    full_auto_service._broadcast_market_alert(" | ".join(anomalies))
                except Exception:
                    pass
        except Exception as err:
            logger.debug(f"[OpenCodeJobs] 市场异常检测跳过: {err}")

    def _opencode_24h():
        db = SessionLocal()
        try:
            from backend.config.settings import OPENCODE_ENABLED
            if not OPENCODE_ENABLED:
                return
            from backend.services.opencode_bridge import run_scheduled_analysis
            run_scheduled_analysis(db, window="24h", domain="ai")
            run_scheduled_analysis(db, window="24h", domain="arb")
        except Exception as err:
            logger.error("[OpenCode] 24h 失败: %s", err, exc_info=True)
        finally:
            db.close()

    def _proposal_eval():
        db = SessionLocal()
        try:
            from backend.services.opencode_proposal_applier import evaluate_applied_proposals
            n = evaluate_applied_proposals(db)
            if n:
                logger.info("[OpenCode] 评估 %d 个提案", n)
            # 洞察生命周期：关闭已恢复/过期的 open 洞察，避免无限喂收紧信号
            from backend.services.opencode_action_router import resolve_stale_insights
            resolve_stale_insights(db)
        except Exception as err:
            logger.error("[OpenCode] proposal eval: %s", err, exc_info=True)
        finally:
            db.close()

    def _proposal_review():
        db = SessionLocal()
        try:
            from backend.config.settings import OPENCODE_AUTO_REVIEW
            if not OPENCODE_AUTO_REVIEW:
                return
            from backend.services.opencode_proposal_reviewer import review_pending_proposals
            out = review_pending_proposals(db, limit=10)
            if out.get("reviewed"):
                logger.info("[OpenCode] 评审 %d 个提案", out["reviewed"])
        except Exception as err:
            logger.error("[OpenCode] proposal review: %s", err, exc_info=True)
        finally:
            db.close()

    def _pace_eval():
        try:
            from backend.services.paper_pace_controller import paper_pace_controller
            from backend.services.strategy_runtime_report import load_latest_report

            report = load_latest_report("24h", "ai")
            if report and report.get("rule_breaches"):
                from backend.services.opencode_action_router import route_analysis_result
                db = SessionLocal()
                try:
                    route_analysis_result(
                        db,
                        {"severity": "major", "findings": [{"message": b} for b in report["rule_breaches"]]},
                        window="24h",
                        domain="ai",
                    )
                finally:
                    db.close()
            paper_pace_controller.evaluate_from_reports()
        except Exception as err:
            logger.error("[PaperPace] eval: %s", err, exc_info=True)

    def _sidecar_watchdog():
        try:
            from backend.services.opencode_sidecar import ensure_sidecar
            ensure_sidecar()
        except Exception as err:
            logger.error("[OpenCodeSidecar] 看门狗失败: %s", err, exc_info=True)

    def _health_digest_tick():
        db = SessionLocal()
        try:
            from backend.services.log_insight_escalation_service import run_health_digest_tick
            out = run_health_digest_tick(db)
            esc = out.get("escalation") or {}
            if esc.get("created") or esc.get("resolved"):
                logger.info(
                    "[OpenCodeHealth] digest tick: created=%s resolved=%s errors_24h=%s",
                    esc.get("created"), esc.get("resolved"), out.get("digest_errors_24h"),
                )
            try:
                from backend.services.assistant_badge_service import build_assistant_badge
                from backend.services.assistant_feishu_notify import notify_p0_errors_if_needed

                badge = build_assistant_badge()
                if badge.get("p0_count", 0) > 0:
                    notify_p0_errors_if_needed(
                        p0_count=int(badge["p0_count"]),
                        hint=str(badge.get("hint") or ""),
                    )
            except Exception:
                pass
        except Exception as err:
            logger.error("[OpenCodeHealth] 1h digest 失败: %s", err, exc_info=True)
        finally:
            db.close()

    def _assistant_daily_report():
        try:
            from datetime import datetime, timezone
            from backend.config.settings import ASSISTANT_DAILY_REPORT_HOUR_UTC
            from backend.services.openclaw_notify import get_notifier

            hour = datetime.now(timezone.utc).hour
            if hour != ASSISTANT_DAILY_REPORT_HOUR_UTC:
                return
            cfg = get_notifier().get_config()
            if not cfg.get("assistant_daily_report_enabled", True):
                return
            from backend.services.assistant_feishu_notify import push_assistant_daily_report

            out = push_assistant_daily_report()
            if out.get("ok"):
                logger.info("[AlphaAssistant] 日报已推送飞书")
        except Exception as err:
            logger.error("[AlphaAssistant] daily report: %s", err, exc_info=True)

    # ══════════════════════════════════════════════════════
    #  Phase 6-7: 新增定时任务 — 决策审计 + 跨周期挖掘 + 市场叙事
    # ══════════════════════════════════════════════════════

    def _decision_audit_12h():
        """每12h审计一次决策质量（Phase 1整合：承接 evolution experience_distill 的 LLM 提炼职能）"""
        try:
            from backend.database.connection import SessionLocal
            from backend.services.opencode_bridge import run_decision_audit
            db = SessionLocal()
            try:
                result = run_decision_audit(db, since_hours=24)
                if result.get("overall_grade"):
                    from backend.services.opencode_action_router import persist_strategic_audit_insights
                    n_ins = persist_strategic_audit_insights(db, "decision_audit", result)
                    logger.info(
                        "[OpenCodeJobs] 决策审计: grade=%s blind_spots=%s insights=%d",
                        result.get("overall_grade"),
                        len(result.get("blind_spots") or []),
                        n_ins,
                    )
            finally:
                db.close()
        except Exception as err:
            logger.error("[OpenCodeJobs] decision_audit_24h: %s", err, exc_info=True)

    def _cross_cycle_3d():
        """每3天挖掘一次跨周期联动模式（Phase 1整合：承接 evolution cross_market_lessons）"""
        try:
            from backend.database.connection import SessionLocal
            from backend.services.opencode_bridge import run_cross_cycle_pattern_mining
            db = SessionLocal()
            try:
                result = run_cross_cycle_pattern_mining(db)
                patterns = result.get("patterns") or []
                if patterns or result.get("actionable_summary") or result.get("summary"):
                    from backend.services.opencode_action_router import persist_strategic_audit_insights
                    n_ins = persist_strategic_audit_insights(db, "cross_cycle", result, window="3d")
                    logger.info(
                        "[OpenCodeJobs] 跨周期模式: %d patterns insights=%d",
                        len(patterns),
                        n_ins,
                    )
            finally:
                db.close()
        except Exception as err:
            logger.error("[OpenCodeJobs] cross_cycle_7d: %s", err, exc_info=True)

    def _regime_journal_12h():
        """每12h更新一次市场状态叙事（Phase 1整合：承接 evolution narrative_update）"""
        try:
            from backend.database.connection import SessionLocal
            from backend.services.opencode_bridge import run_regime_narrative_update
            db = SessionLocal()
            try:
                result = run_regime_narrative_update(db)
                if result.get("dominant_trend"):
                    from backend.services.opencode_action_router import persist_strategic_audit_insights
                    n_ins = persist_strategic_audit_insights(db, "regime_journal", result)
                    logger.info(
                        "[OpenCodeJobs] 市场叙事: trend=%s conf=%.2f insights=%d",
                        result.get("dominant_trend"),
                        result.get("confidence", 0),
                        n_ins,
                    )
            finally:
                db.close()
        except Exception as err:
            logger.error("[OpenCodeJobs] regime_journal_24h: %s", err, exc_info=True)

    # P2-8: 策略代码级深度审计 — 每 12h 扫描活跃策略
    def _strategy_code_audit_12h():
        """每12h扫描已平仓≥10笔的活跃策略（不限胜率：负期望值策略同样需要诊断），注入128K上下文做代码级审计。"""
        try:
            from backend.database.connection import SessionLocal
            from backend.database.models import StrategyTrade, AIStrategy
            from backend.services.opencode_bridge import run_strategy_code_audit
            db = SessionLocal()
            try:
                # 扫描所有活跃策略，找满足审计条件的
                active_strategies = (
                    db.query(AIStrategy)
                    .filter(AIStrategy.status == "active")
                    .all()
                )
                audited = 0
                for s in active_strategies:
                    sid = s.strategy_id
                    # 快速预筛选：查最近7天闭仓交易
                    from datetime import timedelta
                    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
                    closed_count = (
                        db.query(StrategyTrade)
                        .filter(
                            StrategyTrade.strategy_id == sid,
                            StrategyTrade.status == "closed",
                            StrategyTrade.closed_at >= cutoff,
                        )
                        .count()
                    )
                    if closed_count < 10:
                        continue
                    result = run_strategy_code_audit(db, sid)
                    if result.get("overall_assessment"):
                        from backend.services.opencode_action_router import persist_strategic_audit_insights
                        n_ins = persist_strategic_audit_insights(
                            db, "strategy_code_audit", result, strategy_id=sid,
                        )
                        audited += 1
                        logger.info(
                            "[OpenCodeJobs] 策略代码审计 %s: assessment=%s suggestions=%d insights=%d",
                            sid,
                            result.get("overall_assessment"),
                            len(result.get("suggestions") or []),
                            n_ins,
                        )
                if audited:
                    logger.info("[OpenCodeJobs] 策略代码审计完成: %d 个策略", audited)
            finally:
                db.close()
        except Exception as err:
            logger.error("[OpenCodeJobs] strategy_code_audit: %s", err, exc_info=True)

    # ── Hermes 自进化系统 tick 函数 ──
    # 每个 tick 用 hermes_run_tracker 记录开始/结束/状态，供前端时间轴展示

    def _hermes_wisdom_tick():
        """L1: 1h 积累提案智慧"""
        from backend.services.hermes_orchestrator import hermes
        hermes_run_tracker.mark_start("hermes_wisdom_accumulate")
        try:
            hermes.accumulate_wisdom()
            hermes_run_tracker.mark_done("hermes_wisdom_accumulate")
        except Exception as err:
            logger.error("[Hermes] wisdom_tick: %s", err, exc_info=True)
            hermes_run_tracker.mark_done("hermes_wisdom_accumulate", error=str(err))

    def _hermes_meta_tick():
        """L1: 6h 更新模式库"""
        from backend.services.hermes_orchestrator import hermes
        hermes_run_tracker.mark_start("hermes_meta_analysis")
        try:
            hermes.run_meta_analysis()
            hermes_run_tracker.mark_done("hermes_meta_analysis")
        except Exception as err:
            logger.error("[Hermes] meta_tick: %s", err, exc_info=True)
            hermes_run_tracker.mark_done("hermes_meta_analysis", error=str(err))

    def _hermes_prompt_opt_tick():
        """L2: 12h Prompt优化周期"""
        from backend.services.hermes_orchestrator import hermes
        hermes_run_tracker.mark_start("hermes_prompt_optimize")
        try:
            hermes.run_prompt_optimization()
            hermes_run_tracker.mark_done("hermes_prompt_optimize")
        except Exception as err:
            logger.error("[Hermes] prompt_opt_tick: %s", err, exc_info=True)
            hermes_run_tracker.mark_done("hermes_prompt_optimize", error=str(err))

    def _hermes_ab_eval_tick():
        """L2: 4h 评估A/B测试"""
        from backend.services.hermes_orchestrator import hermes
        hermes_run_tracker.mark_start("hermes_ab_test_eval")
        try:
            hermes.evaluate_ab_tests()
            hermes_run_tracker.mark_done("hermes_ab_test_eval")
        except Exception as err:
            logger.error("[Hermes] ab_eval_tick: %s", err, exc_info=True)
            hermes_run_tracker.mark_done("hermes_ab_test_eval", error=str(err))

    def _hermes_arch_evo_tick():
        """L3: 24h 架构进化分析。

        消费 orchestrator 的返回结果：ok=False（LLM 报错或解析失败/零产出）
        时记为 error，避免「status=ok 但永远 0 产出」的静默死链。
        """
        from backend.services.hermes_orchestrator import hermes
        hermes_run_tracker.mark_start("hermes_architecture_evolution")
        try:
            r = hermes.run_architecture_evolution()
            if not r.get("ok"):
                # LLM 失败或解析后 0 产出：记 error，把真正原因写进 last_error
                err = r.get("llm_error") or r.get("error") or "L3 解析失败：LLM 返回 0 条提案"
                logger.error("[Hermes] arch_evo_tick 未产出: %s parsed_ok=%s", err, r.get("parsed_ok"))
                hermes_run_tracker.mark_done("hermes_architecture_evolution", error=str(err))
            else:
                logger.info(
                    "[Hermes] arch_evo_tick 完成: new_proposals=%d", r.get("new_proposals", 0)
                )
                hermes_run_tracker.mark_done("hermes_architecture_evolution")
        except Exception as err:
            logger.error("[Hermes] arch_evo_tick: %s", err, exc_info=True)
            hermes_run_tracker.mark_done("hermes_architecture_evolution", error=str(err))

    def _hermes_genesis_tick():
        """L4: 24h 策略创生。同 L3，消费 ok 标志。"""
        from backend.services.hermes_orchestrator import hermes
        hermes_run_tracker.mark_start("hermes_strategy_genesis")
        try:
            r = hermes.run_strategy_genesis()
            if not r.get("ok"):
                err = r.get("llm_error") or r.get("error") or "L4 解析失败：LLM 返回 0 个候选"
                logger.error("[Hermes] genesis_tick 未产出: %s parsed_ok=%s", err, r.get("parsed_ok"))
                hermes_run_tracker.mark_done("hermes_strategy_genesis", error=str(err))
            else:
                logger.info(
                    "[Hermes] genesis_tick 完成: candidates=%d deployed=%d",
                    r.get("candidates_generated", 0),
                    r.get("deployed", 0),
                )
                hermes_run_tracker.mark_done("hermes_strategy_genesis")
        except Exception as err:
            logger.error("[Hermes] genesis_tick: %s", err, exc_info=True)
            hermes_run_tracker.mark_done("hermes_strategy_genesis", error=str(err))

    def _hermes_genesis_check_tick():
        """L4: 6h 检查孵化结果"""
        from backend.services.hermes_orchestrator import hermes
        hermes_run_tracker.mark_start("hermes_genesis_check")
        try:
            hermes.check_genesis_candidates()
            hermes_run_tracker.mark_done("hermes_genesis_check")
        except Exception as err:
            logger.error("[Hermes] genesis_check_tick: %s", err, exc_info=True)
            hermes_run_tracker.mark_done("hermes_genesis_check", error=str(err))

    jobs = [
        (_srr_tick, 21600, SRR_JOB_ID),                 # 6h
        (_maturity_tick, 600, MATURITY_JOB_ID),         # 10min
        (_opencode_6h, 21600, OPENCODE_6H_JOB),         # 6h (ai) — 保留高频覆盖面
        # P0-1b 修复（2026-06-25）：原 (_opencode_24h, 86400) 间隔太长，
        # 一次 SRR 竞态导致 tc=0 就要再等 24h，提案停摆 3 天。
        # 改为 4h(14400s) + 首次延迟 5min(300s) 等 SRR 就绪，每天产出 6 次。
        (_opencode_24h, 14400, OPENCODE_24H_JOB),       # 4h (ai + arb, P0 降间隔)
        (_proposal_eval, 3600, OPENCODE_PROPOSAL_EVAL),   # 1h
        (_proposal_review, 900, OPENCODE_PROPOSAL_REVIEW),  # 15min
        (_pace_eval, _pace_eval_interval(), PACE_EVAL_JOB),
        (_sidecar_watchdog, 120, SIDECAR_WATCHDOG_JOB),  # 2min
        (_health_digest_tick, 3600, HEALTH_DIGEST_JOB),  # 1h: log digest + ERROR→insight
        (_assistant_daily_report, 3600, "assistant_daily_report_1h"),  # 每小时检查是否到日报点
        # Phase 1 整合: 频率调整 — 决策审计 12h / 市场叙事 12h / 跨周期 6h
        # 修复（2026-06-25）：原 cross_cycle 间隔 3d(259200s) 太长，配合频繁重启几乎永远不执行。
        # 改为 6h(21600s)，让跨周期模式挖掘每天能产出 4 次。
        (_decision_audit_12h, 43200, "opencode_decision_audit_12h"),         # 12h: 决策质量审计
        (_regime_journal_12h, 43200, "opencode_regime_journal_12h"),         # 12h: 市场状态叙事
        (_cross_cycle_3d, 21600, "opencode_cross_cycle_3d"),                 # 原 3d(259200s) → 6h(21600s): 跨周期模式挖掘
        (_deep_loss_review_3h, 10800, "opencode_deep_loss_review_3h"),       # 3h: 深度亏损复盘
        (_market_anomaly_5m, 300, "opencode_market_anomaly_5m"),             # 5min: 市场异常检测
        # P2-8: 策略代码级深度审计 — 每 12h 扫描一次活跃策略（不限胜率）
        (_strategy_code_audit_12h, 43200, "opencode_strategy_code_audit"),   # 12h: 策略代码审计

        # ── Hermes 自进化系统 (P0) ──
        (_hermes_wisdom_tick, 3600, "hermes_wisdom_accumulate"),               # 1h: L1 提案智慧积累
        (_hermes_meta_tick, 21600, "hermes_meta_analysis"),                     # 6h: L1 模式库更新
        (_hermes_prompt_opt_tick, 43200, "hermes_prompt_optimize"),             # 12h: L2 Prompt自优化
        (_hermes_ab_eval_tick, 14400, "hermes_ab_test_eval"),                   # 4h: L2 A/B测试评估
        (_hermes_arch_evo_tick, 86400, "hermes_architecture_evolution"),        # 24h: L3 架构进化
        (_hermes_genesis_tick, 86400, "hermes_strategy_genesis"),               # 24h: L4 策略创生
        (_hermes_genesis_check_tick, 21600, "hermes_genesis_check"),            # 6h: L4 孵化检查
    ]
    try:
        from backend.config.settings import HERMES_L2_AB_ENABLED
        if not HERMES_L2_AB_ENABLED:
            jobs = [j for j in jobs if j[2] != "hermes_ab_test_eval"]
    except Exception:
        pass
    def _wrap_with_tracker(func, job_id: str):
        """给非 Hermes 任务包一层持久化 tracker（Hermes 任务内部已自带，不重复包）。"""
        def _wrapped():
            hermes_run_tracker.mark_start(job_id)
            try:
                ret = func()
                hermes_run_tracker.mark_done(job_id)
                return ret
            except Exception as err:
                hermes_run_tracker.mark_done(job_id, error=str(err))
                raise
        return _wrapped if func else func

    _hermes_job_ids = set(HERMES_TASK_META.keys())

    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    _now = _dt.now(_tz.utc)

    def _resolve_next_run(job_id: str, interval: int):
        """根据 DB 的 last_finished_at 恢复下次运行时间（解决重启相位重置）。

        - 有记录且未到期: last_finished + interval（保持原相位）
        - 有记录但已过期: now + 5s（立即补跑，该跑了）
        - 无记录: 返回 None → add_interval_task 用默认 now+60s
        """
        try:
            from backend.services.hermes_db import get_task_run
            row = get_task_run(job_id)
        except Exception as e:
            logger.debug("[OpenCodeJobs] 读取 %s 运行记录失败(用默认相位): %s", job_id, e)
            return None
        if not row or not row.get("last_finished_at"):
            return None
        try:
            last = _parse_iso(row["last_finished_at"])
        except Exception:
            return None
        if last is None:
            return None
        if last.tzinfo is None:
            last = last.replace(tzinfo=_tz.utc)
        candidate = last + _td(seconds=interval)
        if candidate <= _now:
            return _now + _td(seconds=5)  # 已过期，5s 后补跑
        return candidate

    for func, interval, jid in jobs:
        try:
            if task_scheduler.scheduler and task_scheduler.scheduler.get_job(jid):
                task_scheduler.remove_task(jid)
            # Hermes 任务内部已调用 tracker；非 Hermes 任务这里包一层
            actual_func = func if jid in _hermes_job_ids else _wrap_with_tracker(func, jid)
            _next = _resolve_next_run(jid, interval)
            task_scheduler.add_interval_task(
                task_func=actual_func, interval_seconds=interval, task_id=jid,
                next_run_time=_next,
            )
            _phase = ("恢复相位 " + _next.strftime("%H:%M:%S")) if _next else "首次 now+60s"
            logger.info("[OpenCodeJobs] 注册 %s 每 %ds [%s]", jid, interval, _phase)
        except Exception as err:
            logger.error("[OpenCodeJobs] %s 失败: %s", jid, err)

    _opencode_registered = True
    logger.info("[OpenCodeJobs] ✅ 所有 OpenCode 定时任务已注册（共 %d 个）", len(jobs) + 1)

    # ── Hermes 首次启动触发：如果已有足够智慧，立即执行 L2/L3/L4 ──
    # 避免等 12h/24h 才第一次触发，用户看到的一直是 0
    def _hermes_bootstrap():
        import time as _bt
        _bt.sleep(10)  # 等调度器和其他服务就绪
        try:
            from backend.services.hermes_db import hermes_fetchall
            wisdom_count = len(hermes_fetchall(
                "SELECT id FROM proposal_wisdom_records", ()
            ))
            if wisdom_count < 10:
                logger.info("[Hermes:Bootstrap] wisdom=%d 不足，跳过 L2/L3/L4 首次触发", wisdom_count)
                return
            from backend.services.hermes_orchestrator import hermes as _h
            logger.info("[Hermes:Bootstrap] wisdom=%d, 触发 L2/L3/L4 首次执行...", wisdom_count)
            # L2: Prompt 优化
            try:
                _l2 = _h.run_prompt_optimization()
                logger.info("[Hermes:Bootstrap] L2 完成: %s", _l2.get("task_trading_runtime_analysis", _l2))
            except Exception as _e:
                logger.warning("[Hermes:Bootstrap] L2 失败: %s", _e)
            # L3: 架构进化
            try:
                _l3 = _h.run_architecture_evolution()
                logger.info("[Hermes:Bootstrap] L3 完成: %s", _l3)
            except Exception as _e:
                logger.warning("[Hermes:Bootstrap] L3 失败: %s", _e)
            # L4: 策略创生
            try:
                _l4 = _h.run_strategy_genesis()
                logger.info("[Hermes:Bootstrap] L4 完成: %s", _l4)
            except Exception as _e:
                logger.warning("[Hermes:Bootstrap] L4 失败: %s", _e)
        except Exception as _e:
            logger.error("[Hermes:Bootstrap] 异常: %s", _e, exc_info=True)

    import threading as _th
    _bt_thread = _th.Thread(target=_hermes_bootstrap, daemon=True, name="hermes-bootstrap")
    _bt_thread.start()

    def _opencode_pending_bootstrap():
        import time as _pt
        _pt.sleep(15)
        try:
            from backend.config.settings import (
                OPENCODE_ENABLED,
                OPENCODE_PENDING_DRAIN_ON_STARTUP,
                OPENCODE_PENDING_DRAIN_LIMIT,
                OPENCODE_PENDING_DRAIN_ROUNDS,
            )
            if not OPENCODE_ENABLED or not OPENCODE_PENDING_DRAIN_ON_STARTUP:
                return
            db = SessionLocal()
            try:
                from backend.services.opencode_proposal_reviewer import drain_pending_proposals
                out = drain_pending_proposals(
                    db,
                    limit=int(OPENCODE_PENDING_DRAIN_LIMIT or 30),
                    max_rounds=int(OPENCODE_PENDING_DRAIN_ROUNDS or 3),
                )
                if out.get("drained"):
                    logger.info(
                        "[OpenCode:Bootstrap] pending drain: %d proposals in %d rounds",
                        out.get("drained"), out.get("rounds"),
                    )
            finally:
                db.close()
        except Exception as err:
            logger.warning("[OpenCode:Bootstrap] pending drain 失败: %s", err)

    _th.Thread(
        target=_opencode_pending_bootstrap, daemon=True, name="opencode-pending-drain"
    ).start()

    try:
        from backend.services.training_orchestrator import register_training_jobs
        register_training_jobs()
    except Exception as err:
        logger.error("[OpenCodeJobs] training orchestrator: %s", err)
