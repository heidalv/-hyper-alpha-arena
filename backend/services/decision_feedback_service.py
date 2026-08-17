"""平仓复盘 → 提示词硬约束 / 策略门槛反馈闭环。

读取 DecisionRetrospective 与交易绩效归因，生成下轮 MasterController 注入文本，
并输出可执行的策略门槛调整建议。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class PolicyAdjustment:
    """可落地的策略门槛调整。"""

    key: str
    current_value: Any
    suggested_value: Any
    reason: str
    severity: str = "medium"  # low / medium / high / critical

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "current_value": self.current_value,
            "suggested_value": self.suggested_value,
            "reason": self.reason,
            "severity": self.severity,
        }


@dataclass
class FeedbackBundle:
    prompt_constraints: str = ""
    key_lessons: List[Dict[str, Any]] = field(default_factory=list)
    policy_adjustments: List[PolicyAdjustment] = field(default_factory=list)
    retrospective_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "prompt_constraints": self.prompt_constraints,
            "key_lessons": self.key_lessons,
            "policy_adjustments": [p.to_dict() for p in self.policy_adjustments],
            "retrospective_count": self.retrospective_count,
        }


class DecisionFeedbackService:
    """决策反馈服务 — 连接复盘库与 AI 提示词。"""

    _CACHE_TTL_S = 300
    _cache: Dict[str, Any] = {"at": 0.0, "bundle": None}

    def build_feedback(
        self,
        db=None,
        *,
        account_id: Optional[int] = None,
        strategy_ids: Optional[List[str]] = None,
        lookback_days: int = 14,
    ) -> FeedbackBundle:
        bundle = FeedbackBundle()
        retros = self._load_retrospectives(db, account_id, lookback_days)
        bundle.retrospective_count = len(retros)

        lessons = self._extract_lessons(retros, strategy_ids)

        # 2026-06-11: 教训生成接通净扣费归因 — 从"净值角度"提炼经济学教训
        # （如"scalp 扣费后净亏，少做"），与复盘教训一起注入提示词
        net_lessons: List[Dict[str, Any]] = []
        try:
            attribution = self.build_net_attribution(db, days=7)
            net_lessons = self._derive_net_lessons(attribution)
        except Exception as net_err:
            logger.debug("[Feedback] 净扣费教训提炼跳过: %s", net_err)

        bundle.key_lessons = (net_lessons + lessons)[:12]

        perf_insights = self._load_performance_insights()
        adjustments = self._derive_policy_adjustments(retros, perf_insights)
        bundle.policy_adjustments = adjustments

        bundle.prompt_constraints = self._format_prompt_constraints(
            bundle.key_lessons, adjustments, perf_insights,
        )
        return bundle

    def _derive_net_lessons(self, attribution: Dict[str, Any]) -> List[Dict[str, Any]]:
        """从净扣费归因（build_net_attribution）提炼经济学教训。

        覆盖三类信号：
        - 某 nature 净亏（扣费后）→ 警示少做
        - 某 symbol 净亏且样本充足 → 警示规避
        - 整体手续费占毛利比例过高 → 警示降频

        2026-06-17: 放宽门槛。原逻辑 5 笔 + net_pnl<0(哪怕亏1块) 就标 high severity，
        配合 prompt 硬规则"禁止同方向开仓"导致 AI 全线 hold（连本金都没亏到也停）。
        现：样本门槛 5→20，净亏需同时满足"亏损达本金占比阈值"才算严重，
        severity 从 high 降为 medium（不再触发"禁止开仓"硬规则，仅作软提示）。
        """
        lessons: List[Dict[str, Any]] = []
        if not attribution:
            return lessons

        # 2026-06-17: 放宽参数。MIN_TRADES 5→20；NET_LOSS_PCT_THRESHOLD 净亏需达初始本金的
        # 该比例才算"严重"（默认 10%），避免小额浮亏就全线禁止。
        _MIN_TRADES = 20
        _NET_LOSS_PCT_OF_PRINCIPAL = 0.10  # 净亏达到本金 10% 才警示
        _principal = float(attribution.get("summary", {}).get("principal", 0) or 0) or 10000.0

        by_nature = attribution.get("by_nature") or {}
        for nature, b in by_nature.items():
            if nature == "unknown" or b.get("trades", 0) < _MIN_TRADES:
                continue
            net_pnl = b.get("net_pnl", 0)
            if net_pnl < 0 and abs(net_pnl) >= _principal * _NET_LOSS_PCT_OF_PRINCIPAL:
                lessons.append({
                    "symbol": "*",
                    "strategy_id": None,
                    "was_correct": "no",
                    "exit_reason": "net_attribution",
                    "lesson": (
                        f"{nature} 近7日扣除手续费后净亏 {net_pnl:.0f}"
                        f"（{b['trades']}笔, 胜率{b.get('win_rate', 0):.0%}, "
                        f"手续费 {b.get('fees', 0):.0f}）— 可参考，降低该性质仓位"
                    ),
                    "severity": "medium",
                    "type": "net_economics",
                })

        by_symbol = attribution.get("by_symbol") or {}
        worst = sorted(
            ((s, b) for s, b in by_symbol.items()
             if s != "unknown" and b.get("trades", 0) >= _MIN_TRADES and b.get("net_pnl", 0) < 0
             and abs(b.get("net_pnl", 0)) >= _principal * _NET_LOSS_PCT_OF_PRINCIPAL),
            key=lambda kv: kv[1].get("net_pnl", 0),
        )[:2]
        for sym, b in worst:
            lessons.append({
                "symbol": sym,
                "strategy_id": None,
                "was_correct": "no",
                "exit_reason": "net_attribution",
                "lesson": (
                    f"{sym} 近7日净亏 {b['net_pnl']:.0f}（{b['trades']}笔, "
                    f"胜率{b.get('win_rate', 0):.0%}）— 可参考，适当提高该币开仓门槛"
                ),
                "severity": "medium",
                "type": "net_economics",
            })

        summary = attribution.get("summary") or {}
        fee_ratio = summary.get("fee_gross_ratio")
        if fee_ratio is not None and fee_ratio > 0.30:
            lessons.append({
                "symbol": "*",
                "strategy_id": None,
                "was_correct": "no",
                "exit_reason": "net_attribution",
                "lesson": (
                    f"手续费已吃掉毛利的 {fee_ratio:.0%}（近7日）— "
                    f"可参考，优先做高把握机会以降低频次"
                ),
                "severity": "medium",
                "type": "net_economics",
            })
        return lessons[:4]

    def get_prompt_injection(
        self,
        db=None,
        *,
        account_id: Optional[int] = None,
        strategy_ids: Optional[List[str]] = None,
    ) -> str:
        """供 MasterController 注入的反馈约束文本（带缓存）。"""
        import time

        now = time.time()
        cache_key = f"{account_id}:{','.join(strategy_ids or [])}"
        if (
            self._cache.get("key") == cache_key
            and now - float(self._cache.get("at") or 0) < self._CACHE_TTL_S
            and self._cache.get("bundle")
        ):
            return self._cache["bundle"].prompt_constraints

        bundle = self.build_feedback(db, account_id=account_id, strategy_ids=strategy_ids)
        self._cache.update({"at": now, "key": cache_key, "bundle": bundle})
        return bundle.prompt_constraints

    def get_agent_constraints(
        self,
        db=None,
        *,
        agent_type: str = "swing",
        account_id: Optional[int] = None,
    ) -> str:
        """Agent 只读约束块（swing / trend），避免 Master 收紧而 Agent 仍宽松。"""
        import time

        cache_key = f"agent:{agent_type}:{account_id}"
        now = time.time()
        if (
            self._cache.get("agent_key") == cache_key
            and now - float(self._cache.get("agent_at") or 0) < self._CACHE_TTL_S
            and self._cache.get("agent_text") is not None
        ):
            return self._cache["agent_text"]

        bundle = self.build_feedback(db, account_id=account_id)
        nature_keys = {
            "swing": ("swing",),
            "trend": ("trend_follow", "position", "trend"),
        }.get(agent_type, (agent_type,))
        lines: List[str] = []
        for lesson in bundle.key_lessons or []:
            text = str(lesson.get("lesson") or "")
            low = text.lower()
            if any(n in low for n in nature_keys):
                lines.append(f"- {text}")
        for adj in (bundle.policy_adjustments or []):
            target = str(adj.get("target") or adj.get("nature") or "").lower()
            if target in nature_keys or any(n in target for n in nature_keys):
                lines.append(f"- [策略调整] {adj.get('reason') or adj.get('action') or adj}")

        out = "\n".join(lines[:5])
        self._cache.update({"agent_at": now, "agent_key": cache_key, "agent_text": out})
        return out

    def get_thesis_constraints(
        self,
        db=None,
        *,
        agent_type: str = "swing",
    ) -> str:
        """MLTO 专用：从近期 key_lessons 提取 thesis 相关约束。"""
        bundle = self.build_feedback(db)
        nature_keys = {
            "swing": ("swing", "mid", "mlto"),
            "trend": ("trend", "long", "position", "mlto"),
        }.get(agent_type, (agent_type,))
        lines: List[str] = []
        for lesson in bundle.key_lessons or []:
            text = str(lesson.get("lesson") or "")
            low = text.lower()
            if any(n in low for n in nature_keys) or "thesis" in low or "readiness" in low:
                lines.append(f"- {text[:160]}")
        return "\n".join(lines[:4])

    def run_daily_report(self, db=None, output_dir: str = "data/ai_feedback") -> Dict[str, Any]:
        """每日自动输出亏损规则、最赚钱退出、应禁用的交易性质。

        V5 升级：附加净扣费归因（close_reason/nature/symbol 维度），
        并把动态门槛建议闭环写入 data/v5_runtime_gates.json（带回滚保险丝）。
        """
        bundle = self.build_feedback(db)
        from backend.services.trade_performance_analyzer import (
            analyze_closed_trades,
            render_report_markdown,
            save_report_json,
        )

        perf = analyze_closed_trades(db=db)
        os.makedirs(output_dir, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d")
        perf_path = os.path.join(output_dir, f"trade_perf_{ts}.json")
        save_report_json(perf, perf_path)

        from backend.services.strategy_offline_replay import compare_replay_baseline, save_replay_report
        replay_path = os.path.join(output_dir, f"offline_replay_{ts}.json")
        save_replay_report(replay_path)
        replay = compare_replay_baseline()

        # V5: 净扣费归因 + 动态门槛闭环
        attribution = self.build_net_attribution(db, days=7)
        gates_applied = self.apply_gate_adjustments(attribution)

        report = {
            "date": ts,
            "feedback": bundle.to_dict(),
            "performance": perf.to_dict(),
            "net_attribution": attribution,
            "v5_gates_applied": gates_applied,
            "offline_replay": replay,
            "markdown": render_report_markdown(perf),
        }
        report_path = os.path.join(output_dir, f"daily_feedback_{ts}.json")
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        logger.info(
            "[Feedback] 日报已写入 %s, retrospectives=%d, adjustments=%d, v5_gates=%s",
            report_path,
            bundle.retrospective_count,
            len(bundle.policy_adjustments),
            gates_applied,
        )

        # 本地 LLM 门控优化器：与规则反馈在同一时机各自提交 Governor 仲裁。
        # 仅当配置了 LOCAL_LLM_CONFIG_ID（指向内网 GPU 机）时生效；失败静默跳过，
        # 不影响本日报与规则调参。详见 docs/LOCAL_LLM_TRADING_HOST_GUIDE.md。
        try:
            from backend.services.local_llm.gate_optimizer_service import run_gate_optimization
            llm_result = run_gate_optimization()
            report["local_llm_gate_optimization"] = llm_result
        except Exception as llm_err:  # noqa: BLE001
            logger.debug("[Feedback] 本地 LLM 门控优化跳过: %s", llm_err)

        return report

    # ══════════════════════════════════════════════════
    #  V5 净扣费归因 + 动态门槛闭环
    # ══════════════════════════════════════════════════

    def build_net_attribution(self, db=None, *, days: int = 7) -> Dict[str, Any]:
        """按 close_reason / trade_nature / symbol 维度的净扣费统计。

        所有维度统计：笔数、毛盈亏、手续费、净盈亏、平均盈利/平均亏损、
        Net Profit Factor（净盈利因子 = Σ净赢 / |Σ净亏|）、fee/gross 比。
        """
        result: Dict[str, Any] = {
            "days": days,
            "summary": {},
            "by_close_reason": {},
            "by_nature": {},
            "by_symbol": {},
        }
        own_session = False
        if db is None:
            try:
                from backend.database.connection import SessionLocal
                db = SessionLocal()
                own_session = True
            except Exception as err:
                logger.warning("[Feedback] 净扣费归因无DB: %s", err)
                return result
        def _compute(_db):
            # [2026-08-17 修复] 原读 PaperOrder（已退役空表，反复报 UndefinedTable），
            # 改读 trade_facts 事件流（真实交易事实：pnl/fees/close_reason/tier）。
            from sqlalchemy import text as _sa_text

            # 重置累加器，保证"全新连接重试"时不会在上次半成品结果上叠加。
            result["by_close_reason"] = {}
            result["by_nature"] = {}
            result["by_symbol"] = {}
            result["summary"] = {}

            cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
            rows = _db.execute(_sa_text(
                """
                SELECT symbol, tier, pnl, fees, close_reason
                FROM trade_facts
                WHERE ts >= :cutoff AND pnl IS NOT NULL
                """
            ), {"cutoff": cutoff}).mappings().all()

            def _bucket(store: Dict[str, Dict], key: str, pnl: float, fee: float):
                b = store.setdefault(key, {
                    "trades": 0, "gross_pnl": 0.0, "fees": 0.0, "net_pnl": 0.0,
                    "wins": 0, "win_amount": 0.0, "loss_amount": 0.0,
                })
                net = pnl - fee
                b["trades"] += 1
                b["gross_pnl"] += pnl
                b["fees"] += fee
                b["net_pnl"] += net
                if net > 0:
                    b["wins"] += 1
                    b["win_amount"] += net
                else:
                    b["loss_amount"] += abs(net)

            total: Dict[str, Dict] = {}
            for o in rows:
                pnl = float(o["pnl"] or 0)
                fee = float(o["fees"] or 0)
                _bucket(total, "all", pnl, fee)
                _bucket(result["by_close_reason"], str(o["close_reason"] or "unknown"), pnl, fee)
                _bucket(result["by_nature"], str(o["tier"] or "unknown"), pnl, fee)
                _bucket(result["by_symbol"], str(o["symbol"] or "unknown"), pnl, fee)

            def _finalize(store: Dict[str, Dict]):
                for b in store.values():
                    wins, losses = b["wins"], b["trades"] - b["wins"]
                    b["win_rate"] = round(wins / b["trades"], 3) if b["trades"] else 0
                    b["avg_win"] = round(b["win_amount"] / wins, 2) if wins else 0
                    b["avg_loss"] = round(b["loss_amount"] / losses, 2) if losses else 0
                    b["net_profit_factor"] = (
                        round(b["win_amount"] / b["loss_amount"], 3)
                        if b["loss_amount"] > 0 else None
                    )
                    gross_win = b["win_amount"] + b["fees"]
                    b["fee_gross_ratio"] = (
                        round(b["fees"] / gross_win, 3) if gross_win > 0 else None
                    )
                    for k in ("gross_pnl", "fees", "net_pnl", "win_amount", "loss_amount"):
                        b[k] = round(b[k], 2)

            for store in (total, result["by_close_reason"], result["by_nature"], result["by_symbol"]):
                _finalize(store)
            result["summary"] = total.get("all", {})

        try:
            _compute(db)
        except Exception as err:
            # 传入的 db 常来自长期持有连接的循环（如 scalp/主循环），连接被服务端 90s
            # idle_in_transaction 超时掐断后这里会抛 "Can't reconnect..."。复盘归因是纯
            # 只读统计，用一条【全新短连接】重试一次即可，避免因一次连接抖动整轮丢失。
            logger.warning("[Feedback] 净扣费归因失败，改用全新连接重试: %s", err)
            try:
                from backend.database.connection import SessionLocal
                _retry_db = SessionLocal()
                try:
                    _compute(_retry_db)
                finally:
                    _retry_db.close()
            except Exception as err2:
                logger.warning("[Feedback] 净扣费归因全新连接重试仍失败: %s", err2)
        finally:
            if own_session:
                try:
                    db.close()
                except Exception:
                    pass
        return result

    _V5_GATES_FILE = os.path.join("data", "v5_runtime_gates.json")
    _V5_GATES_FUSE = os.path.join("data", "v5_gates_rollback.flag")

    def apply_gate_adjustments(self, attribution: Dict[str, Any]) -> Dict[str, Any]:
        """把归因结论闭环写入 V5 运行时门槛（unified_gate 每 60s 重读）。

        保险丝：存在 data/v5_gates_rollback.flag 时不再调整并清空运行时覆盖，
        恢复 .env 基准值（建议人工排查后删除 flag 再恢复闭环）。
        """
        applied: Dict[str, Any] = {}
        try:
            if os.path.exists(self._V5_GATES_FUSE):
                from backend.services.runtime_governor import runtime_governor as gov
                gov.withdraw("decision_feedback")  # 撤销本来源全部意图，门槛回基准
                if os.path.exists(self._V5_GATES_FILE):
                    os.remove(self._V5_GATES_FILE)
                logger.warning("[Feedback][V5] 保险丝已熔断，已撤销反馈门槛意图，回退基准值")
                return {"fuse_blown": True}

            summary = (attribution or {}).get("summary") or {}
            by_nature = (attribution or {}).get("by_nature") or {}

            # 规则1：某 nature 7日净亏 且 样本≥10 且 胜率<35% → 禁用该 nature
            # 2026-06-18: paper 模式跳过 nature 禁用。模拟盘目的是训练，禁用整个 nature 类型
            # 等于不让 AI 练这个方向，与训练目标矛盾。live 模式保留（真实资金保护）。
            disabled = []
            _trading_mode = "live"
            try:
                from backend.services.lock_strength_service import get_lock_strength_service as _glss
                _trading_mode = "paper" if _glss().get_profile("paper").disable_loss_locks else "live"
            except Exception:
                pass
            if _trading_mode != "paper":
                for nature, b in by_nature.items():
                    if nature in ("unknown",):
                        continue
                    if (b.get("trades", 0) >= 10
                            and b.get("net_pnl", 0) < 0
                            and b.get("win_rate", 1) < 0.35):
                        disabled.append(nature)
            if disabled:
                applied["disabled_natures"] = disabled[:3]

            # 规则2：整体手续费吃掉毛利 >30% → 收紧当日额度（仅额度开关开启时）
            fee_ratio = summary.get("fee_gross_ratio")
            if fee_ratio is not None and fee_ratio > 0.30:
                try:
                    from backend.config.settings import V5_DAILY_TRADE_CAP_ENABLED
                    if V5_DAILY_TRADE_CAP_ENABLED:
                        applied["max_daily_trades"] = 7
                except Exception:
                    pass

            # 规则3：平均亏损仍 > 平均盈利 → 盈亏比门槛 1.8→2.0
            avg_win = summary.get("avg_win", 0) or 0
            avg_loss = summary.get("avg_loss", 0) or 0
            if avg_loss > 0 and avg_win > 0 and avg_loss > avg_win:
                applied["min_risk_reward"] = 2.0

            # 规则4（反向放松，闭环双向化）：胜率持续向好 + 赚多亏少 + 未触发任何收紧
            #   → 在 [下限, 基准] 区间下调 min_risk_reward / scalp_min_confidence，
            #     在好状态下提高开仓机会。受 unified_gate 的 [1.5,cap]/[60,90] 边界保护，
            #     状态一旦转差，规则1-3 会重新收紧（自纠偏）。
            overall_wr = summary.get("win_rate", 0) or 0
            overall_trades = summary.get("trades", 0) or 0
            _tightening_keys = {"disabled_natures", "max_daily_trades", "min_risk_reward"}
            _no_tightening = not (set(applied.keys()) & _tightening_keys)
            if (_no_tightening and overall_trades >= 20
                    and overall_wr >= 0.55
                    and avg_win > 0 and avg_win >= avg_loss):
                applied["min_risk_reward"] = 1.5
                applied["scalp_min_confidence"] = 62
                logger.info(
                    "[Feedback][V5] 反向放松触发: 胜率%.0f%%(%d笔) 赚>亏 → "
                    "min_rr→1.5 scalp_conf→62",
                    overall_wr * 100, overall_trades,
                )

            # 收敛到 RuntimeGovernor 统一裁决（不再直写 v5_runtime_gates.json）：
            #   触发的 key 提交意图；未触发的 key 撤销本来源旧意图（回基准 / 让位其它来源）。
            from backend.services.runtime_governor import runtime_governor as gov
            _governed = (
                "disabled_natures", "max_daily_trades",
                "min_risk_reward", "scalp_min_confidence",
            )
            for _k in _governed:
                if _k in applied:
                    gov.submit_intent(
                        _k, applied[_k], source="decision_feedback",
                        confidence=0.6, reason="daily_feedback_loop",
                    )
                else:
                    gov.withdraw("decision_feedback", [_k])
            # 清理 legacy 残留文件（v5_runtime_gates.json 已不再作为写入口）
            if os.path.exists(self._V5_GATES_FILE):
                try:
                    os.remove(self._V5_GATES_FILE)
                except Exception:
                    pass
            if applied:
                logger.info("[Feedback][Governor] 提交门槛意图: %s", applied)
            else:
                logger.info("[Feedback][Governor] 归因未触发收紧，撤销本来源意图回基准")
        except Exception as err:
            logger.warning("[Feedback][V5] 门槛闭环写入失败: %s", err)
        return applied

    def _load_retrospectives(self, db, account_id: Optional[int], lookback_days: int) -> List[Any]:
        """复盘表在 analytics 库，始终用 AnalyticsSessionLocal 查询。"""
        try:
            from backend.database.connection import AnalyticsSessionLocal
            from backend.database.models import DecisionRetrospective

            ana_db = AnalyticsSessionLocal()
            try:
                cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
                q = ana_db.query(DecisionRetrospective).filter(
                    DecisionRetrospective.created_at >= cutoff,
                )
                if account_id:
                    q = q.filter(DecisionRetrospective.account_id == account_id)
                return q.order_by(DecisionRetrospective.created_at.desc()).limit(100).all()
            finally:
                ana_db.close()
        except Exception as e:
            logger.debug("[Feedback] analytics 复盘查询失败: %s", e)
            return self._load_retrospectives_sqlite(account_id, lookback_days)

    def _load_retrospectives_sqlite(self, account_id: Optional[int], lookback_days: int) -> List[Dict]:
        import sqlite3

        paths = [
            "data/alpha_analytics.db",
            "../data/alpha_analytics.db",
            "backend/data/alpha_analytics.db",
        ]
        for path in paths:
            if not os.path.isfile(path):
                continue
            try:
                conn = sqlite3.connect(path)
                conn.row_factory = sqlite3.Row
                sql = "SELECT * FROM decision_retrospectives ORDER BY created_at DESC LIMIT 100"
                rows = [dict(r) for r in conn.execute(sql).fetchall()]
                conn.close()
                return rows
            except Exception:
                continue
        return []

    def _extract_lessons(self, retros, strategy_ids: Optional[List[str]]) -> List[Dict[str, Any]]:
        """从复盘记录提炼教训（带时间衰退 + 亏损比例分级）。

        2026-06-18: 根治"一笔亏损记一辈子"。原逻辑每笔 was_correct=no 都标 high severity，
        不管亏多少、多久以前——14天前亏1块也是 high，永久注入 prompt。
        现：severity 按亏损幅度(pnl_pct) + 时间远近(越久越轻)分级：
        - 近3天 + 亏损>5% → high（近期大亏，值得警惕）
        - 近7天 + 亏损>2% → medium（中期中亏，参考）
        - 其他 → info/low（陈旧或小额，仅记录不强调）
        """
        from datetime import datetime, timezone, timedelta
        lessons: List[Dict[str, Any]] = []
        sid_set = set(strategy_ids or [])
        _now = datetime.now(timezone.utc)
        for r in retros:
            if isinstance(r, dict):
                sid = r.get("strategy_id")
                lesson = r.get("lesson_learned") or ""
                symbol = r.get("symbol")
                was_correct = r.get("was_correct")
                exit_reason = r.get("exit_reason")
                pnl_pct = float(r.get("pnl_pct", 0) or 0)
                closed_raw = r.get("closed_at")
            else:
                sid = getattr(r, "strategy_id", None)
                lesson = getattr(r, "lesson_learned", "") or ""
                symbol = getattr(r, "symbol", "")
                was_correct = getattr(r, "was_correct", None)
                exit_reason = getattr(r, "exit_reason", "")
                pnl_pct = float(getattr(r, "pnl_pct", 0) or 0)
                closed_raw = getattr(r, "closed_at", None)

            if sid_set and sid and sid not in sid_set:
                continue
            if not lesson:
                continue

            # 时间衰退：解析 closed_at，算距今天数
            _days_ago = 99  # 默认陈旧
            if closed_raw:
                try:
                    if isinstance(closed_raw, str):
                        ct = datetime.fromisoformat(closed_raw.replace("Z", "+00:00"))
                    else:
                        ct = closed_raw
                    if ct.tzinfo is None:
                        ct = ct.replace(tzinfo=timezone.utc)
                    _days_ago = (_now - ct).days
                except Exception:
                    pass

            # 分级 severity：亏损幅度 + 时间远近
            _loss_pct = abs(pnl_pct) if pnl_pct < 0 else 0
            if was_correct == "yes":
                severity = "info"
                ltype = "win_pattern"
            elif _days_ago <= 3 and _loss_pct >= 5:
                severity = "high"   # 近期大亏
                ltype = "loss_analysis"
            elif _days_ago <= 7 and _loss_pct >= 2:
                severity = "medium"  # 中期中亏
                ltype = "loss_analysis"
            elif _days_ago <= 3 and _loss_pct >= 2:
                severity = "medium"  # 近期小亏
                ltype = "loss_analysis"
            else:
                severity = "low"    # 陈旧或小额，不强调
                ltype = "loss_analysis"

            # 陈旧教训(>7天)或小额亏损降权：lesson 前缀标注时效
            _time_tag = ""
            if _days_ago <= 1:
                _time_tag = "[近1天]"
            elif _days_ago <= 3:
                _time_tag = "[近3天]"
            elif _days_ago <= 7:
                _time_tag = "[近7天]"
            else:
                _time_tag = f"[{_days_ago}天前,仅供参考]"

            lessons.append({
                "symbol": symbol,
                "strategy_id": sid,
                "was_correct": was_correct,
                "exit_reason": exit_reason,
                "lesson": f"{_time_tag} {lesson[:180]}",
                "severity": severity,
                "type": ltype,
            })
        return lessons[:10]

    def _load_performance_insights(self) -> List[str]:
        try:
            from backend.services.trade_performance_analyzer import analyze_closed_trades

            return analyze_closed_trades().insights
        except Exception as e:
            logger.debug("[Feedback] 绩效归因跳过: %s", e)
            return []

    def _derive_policy_adjustments(
        self,
        retros,
        perf_insights: List[str],
    ) -> List[PolicyAdjustment]:
        """提炼「提示词软约束」——明确边界（2026-06-13）：

        本方法产出的 PolicyAdjustment **仅用于注入 LLM 提示词**（见
        _format_prompt_constraints），属于「软约束」：教育主决策，不直接改任何
        运行时硬门槛。历史上易被误读为「自适应已落地」，实则未落地。

        真正的「硬门槛落地」走另一条权威通道，互不重复：
          - maturity_controller + threshold_resolver：数据成熟度驱动的双向松紧；
          - apply_gate_adjustments：按净扣费归因写 runtime_tuning（硬）；
          - OpenCode 提案 → runtime_tuning / policy YAML（硬，带快照回滚）。

        因此这里保持「只生成软约束」，不再尝试把这些建议硬写阈值，避免与上面
        三条硬通道冲突或重复收紧。
        """
        adjustments: List[PolicyAdjustment] = []

        loss_count = sum(
            1 for r in retros
            if (r.get("was_correct") if isinstance(r, dict) else getattr(r, "was_correct", "")) == "no"
        )
        if loss_count >= 5:
            adjustments.append(PolicyAdjustment(
                key="entry_threshold_short",
                current_value=50,
                suggested_value=58,
                reason=f"近 {loss_count} 笔复盘判定错误，提高 short tier 开仓门槛",
                severity="high",
            ))

        sl_losses = sum(
            1 for r in retros
            if "sl" in str(
                r.get("exit_reason") if isinstance(r, dict) else getattr(r, "exit_reason", "")
            ).lower()
            and (r.get("was_correct") if isinstance(r, dict) else getattr(r, "was_correct", "")) == "no"
        )
        if sl_losses >= 3:
            adjustments.append(PolicyAdjustment(
                key="sl_distance_multiplier",
                current_value=1.0,
                suggested_value=1.5,
                reason=f"{sl_losses} 笔 SL 止损复盘为错误，建议放宽 SL 至 ATR×1.5",
                severity="medium",
            ))

        for insight in perf_insights:
            if "short/intraday/scalp" in insight:
                adjustments.append(PolicyAdjustment(
                    key="disable_natures",
                    current_value=[],
                    suggested_value=["scalp"],
                    reason=insight,
                    severity="high",
                ))
                break

        ai_close_losses = sum(
            1 for r in retros
            if "manual" in str(
                r.get("exit_reason") if isinstance(r, dict) else getattr(r, "exit_reason", "")
            ).lower()
        )
        if ai_close_losses >= 2:
            adjustments.append(PolicyAdjustment(
                key="master_close_guard",
                current_value="soft",
                suggested_value="hard",
                reason="AI 主动平仓复盘亏损，强化 master_close_guard",
                severity="critical",
            ))

        return adjustments

    def _format_prompt_constraints(
        self,
        lessons: List[Dict[str, Any]],
        adjustments: List[PolicyAdjustment],
        perf_insights: List[str],
    ) -> str:
        if not lessons and not adjustments and not perf_insights:
            return ""

        lines = [
            "## 🔄 反馈闭环：复盘约束（DecisionRetrospective + 绩效归因）",
            "> 以下约束来自真实平仓复盘，**必须**在开仓前检查。",
            "",
        ]

        if perf_insights:
            lines.append("### 绩效归因摘要")
            for ins in perf_insights[:5]:
                lines.append(f"- {ins}")
            lines.append("")

        if lessons:
            lines.append("### 最近教训")
            for l in lessons[:5]:
                icon = "🔴" if l.get("severity") == "high" else "🔵"
                lines.append(
                    f"{icon} [{l.get('type')}] {l.get('symbol')} "
                    f"exit={l.get('exit_reason')} — {l.get('lesson')}"
                )
            lines.append("")

        if adjustments:
            # 2026-06-18: paper 模式降级为"软约束/教育参考"，避免 AI 把 58 当真门槛拒绝开仓
            _adj_is_paper = False
            try:
                from backend.services.lock_strength_service import get_lock_strength_service as _glss
                _adj_is_paper = _glss().get_profile("paper").disable_loss_locks
            except Exception:
                pass
            _adj_title = "策略门槛调整（软约束/教育参考）" if _adj_is_paper else "策略门槛调整（硬约束）"
            lines.append(f"### {_adj_title}")
            for adj in adjustments:
                if _adj_is_paper:
                    sev = "💡"
                else:
                    sev = {"critical": "⛔", "high": "🔴", "medium": "🟠"}.get(adj.severity, "🟡")
                lines.append(
                    f"{sev} **{adj.key}**: {adj.current_value} → **{adj.suggested_value}** "
                    f"（{adj.reason}{'，仅供参考不强制' if _adj_is_paper else ''}）"
                )
            lines.append("")

        lines.append(
            "**规则**：以上复盘仅供参考。若 symbol 历史亏损严重（severity=critical），"
            "请谨慎评估当前入场时机；但不禁止同方向开仓——以当前行情信号为主，"
            "历史教训作为仓位/止损参考而非一刀切禁令。"
        )
        return "\n".join(lines)


    def sync_lesson_to_strategy_memory(
        self,
        db,
        *,
        strategy_id: Optional[str],
        symbol: str,
        lesson: str,
        was_correct: str,
        exit_reason: str,
        tier: str = "",
        trade_nature: str = "",
    ) -> None:
        """将平仓复盘写入 StrategyMemory.key_lessons，供下轮 Agent 读取。

        P0 修复（2026-06-23）：strategy_memories.strategy_id 有外键约束到
        ai_strategies.strategy_id。当持仓引用了一个已被删除/不存在的策略时，
        插入会触发 ForeignKeyViolation，**污染调用方的 db 会话**，导致后续
        所有 DB 操作失败（日志表现为 "transaction has been rolled back due
        to a previous exception during flush"）。

        修复两点：
        1. 插入前校验父策略存在（DB 已证实 38 个孤儿持仓 strategy_id）；
        2. 用独立会话写入，绝不污染调用方事务（学习是"锦上添花"，绝不能
           因为它失败而打挂平仓主流程）。
        """
        if not db or not strategy_id or not lesson:
            return

        # 独立会话：学习写入失败绝不污染调用方的 db 事务（P0 根因之一）
        from backend.database.connection import SessionLocal
        isolated_db = SessionLocal()
        try:
            from backend.database.models import StrategyMemory, AIStrategy
            from backend.database.connection import sqlite_write_commit

            # ── 修复 1：校验父策略存在，避免 ForeignKeyViolation ──
            # DB 已证实存在 38 个 paper_positions 引用了不存在的 strategy_id。
            #
            # 修复（2026-06-25）：原修复太严——scalp_router / cross_cycle_BTC 等
            # 合法的非 AI 系统策略不在 ai_strategies 表里，被全部跳过，导致平仓学习
            # 11325 次但 strategy_memories 自 6/22 起没更新（46 次跳过/1 次成功）。
            # 现改为：复用 unified_learning 的 _resolve_strategy_id_for_fk 逻辑，
            # 它会为系统策略自动创建占位父行，让 FK 满足。
            try:
                from backend.services.unified_learning_service import unified_learning
                resolved_sid = unified_learning._resolve_strategy_id_for_fk(isolated_db, strategy_id)
            except Exception:
                resolved_sid = strategy_id  # 降级：直接用原值（可能触发 FK 但有 try/except 兜底）
            if not resolved_sid:
                logger.info(
                    "[Feedback] 跳过 key_lessons 写入：strategy_id=%s 无法解析（孤儿），"
                    "避免 ForeignKeyViolation 污染事务",
                    str(strategy_id)[:12],
                )
                return
            strategy_id = resolved_sid  # 用解析后的（可能已创建占位父行）

            mem = isolated_db.query(StrategyMemory).filter(
                StrategyMemory.strategy_id == strategy_id
            ).first()
            if not mem:
                mem = StrategyMemory(strategy_id=strategy_id, key_lessons=[])
                isolated_db.add(mem)
                isolated_db.flush()

            lessons = list(mem.key_lessons or [])
            entry = {
                "type": "loss_analysis" if was_correct == "no" else "win_pattern",
                "ts": datetime.now(timezone.utc).isoformat(),  # [P1-12] 统一时间戳
                "symbol": symbol,
                "tier": tier or "mid",
                "trade_nature": trade_nature or "swing",
                "regime": exit_reason,
                "lesson": lesson[:300],
                "severity": "high" if was_correct == "no" else "info",
            }
            dedupe_key = f"{entry['symbol']}:{entry['type']}:{entry['lesson'][:80]}"
            seen = {f"{l.get('symbol')}:{l.get('type')}:{str(l.get('lesson',''))[:80]}" for l in lessons if isinstance(l, dict)}
            if dedupe_key not in seen:
                lessons.append(entry)
            mem.key_lessons = lessons[-20:]
            sqlite_write_commit(isolated_db, label="strategy_memory_lesson")
            logger.info(
                "[Feedback] key_lessons 更新 strategy=%s symbol=%s type=%s",
                strategy_id[:8], symbol, entry["type"],
            )
        except Exception as err:
            # 独立会话失败不影响调用方：rollback + 降级日志
            try:
                isolated_db.rollback()
            except Exception:
                pass
            logger.debug("[Feedback] key_lessons 写入失败（已隔离，不影响主事务）: %s", err)
        finally:
            try:
                isolated_db.close()
            except Exception:
                pass


decision_feedback_service = DecisionFeedbackService()
