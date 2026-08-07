"""Hermes Layer 4: 策略创生引擎

从已验证成功的提案模式中提取策略 DNA，生成新策略变体，
在 paper 环境孵化验证，达标后自动晋升至 live。
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.services.hermes_db import (
    hermes_execute,
    hermes_fetchall,
    hermes_fetchone,
    get_main_session,
)
from backend.services.hermes_proposal_wisdom_engine import proposal_wisdom

logger = logging.getLogger(__name__)

# 孵化达标条件
MIN_PAPER_TRADES = 30
MIN_PAPER_WIN_RATE = 0.45
MIN_PAPER_AVG_PNL = 1.0  # $/笔
MIN_PAPER_DAYS = 3
MAX_ACTIVE_INCUBATIONS = int(os.getenv("HERMES_GENESIS_MAX_ACTIVE_INCUBATIONS", "12"))


class StrategyGenesisEngine:
    """Layer 4: 策略创生引擎。

    流程：
    1. 提取成功模式 → 策略 DNA
    2. LLM 生成策略变体
    3. paper 环境孵化
    4. 达标 → 自动晋升 live
    """

    def extract_successful_patterns(self) -> List[Dict[str, Any]]:
        """从 proposal_wisdom + param_effect_patterns 提取高置信度成功模式。"""
        patterns = hermes_fetchall(
            """SELECT param_key, direction, market_condition, sample_count,
                      avg_pnl_impact, confidence_avg, pattern_summary
               FROM param_effect_patterns
               WHERE outcome='improved' AND confidence_avg >= 0.6 AND sample_count >= 3
               ORDER BY avg_pnl_impact * confidence_avg DESC
               LIMIT 20"""
        )

        # 补充 wisdom 中策略级别的成功模式
        wisdom_records = hermes_fetchall(
            """SELECT DISTINCT focus, market_condition, outcome,
                      AVG(pnl_impact) as avg_impact, COUNT(*) as cnt
               FROM proposal_wisdom_records
               WHERE outcome='improved'
               GROUP BY focus, market_condition
               ORDER BY AVG(pnl_impact) DESC"""
        )

        result = []
        for p in patterns:
            result.append({
                "source": "pattern_library",
                "param_key": p["param_key"],
                "direction": p["direction"],
                "market_condition": p["market_condition"],
                "sample_count": p["sample_count"],
                "avg_pnl_impact": round(float(p["avg_pnl_impact"] or 0), 2),
                "confidence": round(float(p["confidence_avg"] or 0), 3),
            })
        for w in wisdom_records:
            result.append({
                "source": "wisdom_aggregate",
                "focus": w["focus"],
                "market_condition": w["market_condition"],
                "sample_count": w["cnt"],
                "avg_pnl_impact": round(float(w["avg_impact"] or 0), 2),
            })

        # 冷启动：Agent 平仓智慧作为 seed
        if len(result) < 3:
            agent_rows = hermes_fetchall(
                """SELECT agent_type, outcome, COUNT(*) AS cnt,
                          AVG(pnl) AS avg_pnl
                   FROM agent_decision_wisdom
                   GROUP BY agent_type, outcome
                   HAVING cnt >= 2
                   ORDER BY cnt DESC LIMIT 10"""
            )
            for a in agent_rows:
                result.append({
                    "source": "agent_wisdom",
                    "param_key": f"agent_{a['agent_type']}_{a['outcome']}",
                    "market_condition": "agent_decision",
                    "sample_count": a["cnt"],
                    "avg_pnl_impact": round(float(a["avg_pnl"] or 0), 2),
                    "confidence": 0.5,
                })

        return result

    def get_top_strategies_for_breeding(self) -> List[Dict[str, Any]]:
        """获取当前表现最好的策略作为育种模板。"""
        db = get_main_session()
        try:
            from backend.database.models import AIStrategy, StrategyMemory

            pairs = (
                db.query(AIStrategy, StrategyMemory)
                .join(StrategyMemory, AIStrategy.strategy_id == StrategyMemory.strategy_id)
                .filter(
                    AIStrategy.status == "active",
                    StrategyMemory.total_trades >= 15,
                    StrategyMemory.win_rate >= 0.45,
                )
                .order_by(
                    (StrategyMemory.win_rate * StrategyMemory.total_trades).desc()
                )
                .limit(10)
                .all()
            )

            templates = []
            for strat, mem in pairs:
                templates.append({
                    "strategy_id": strat.strategy_id,
                    "name": strat.name,
                    "symbols": getattr(strat, "target_symbols", None) or [getattr(strat, "primary_symbol", "BTC")],
                    "timeframe": strat.timeframe or "15m",
                    "win_rate": float(mem.win_rate or 0),
                    "sharpe": float(mem.sharpe_ratio or 0),
                    "total_trades": int(mem.total_trades or 0),
                    "tier": getattr(strat, "timeframe_tier", None) or "mid",
                })
            return templates
        finally:
            db.close()

    def generate_strategy_variants(self) -> List[Dict[str, Any]]:
        """调用 LLM 生成策略变体。"""
        patterns = self.extract_successful_patterns()
        templates = self.get_top_strategies_for_breeding()

        if not patterns and not templates:
            logger.info("[Hermes:L4] 无足够数据生成策略变体")
            return []

        # 获取当前市况分布
        wisdom_ctx = proposal_wisdom.build_wisdom_context(limit=10)

        system_prompt = self._load_l4_system_prompt()
        user_text = (
            f"## 策略创生分析\n\n"
            f"### 成功模式 ({len(patterns)} 条)\n"
            + "\n".join(
                f"- [{p.get('source','')}] {p.get('param_key',p.get('focus','?'))}: "
                f"{p.get('direction','')} 在 {p.get('market_condition','?')} 市况下 "
                f"avg PnL ${p.get('avg_pnl_impact',0):+.2f} "
                f"(n={p.get('sample_count',0)})"
                for p in patterns[:10]
            )
            + f"\n\n### 当前优质策略 ({len(templates)} 个)\n"
            + "\n".join(
                f"- {t['name']}({t['strategy_id']}): "
                f"胜率{t['win_rate']:.0%} 夏普{t['sharpe']:.2f} {t['total_trades']}笔"
                for t in templates[:5]
            )
            + f"\n\n### 历史智慧\n{wisdom_ctx[:2000]}\n\n"
            f"请基于上述成功模式和策略模板，生成 2-3 个新策略变体。"
        )

        result = self._call_llm(system_prompt, user_text)
        candidates = result.get("candidates") or []
        llm_error = result.get("error")

        for c in candidates:
            wid = hermes_execute(
                """INSERT INTO strategy_genesis_candidates
                   (source_wisdom_ids, template_seed, variant_name, variant_config,
                    paper_status, viability_score, created_at)
                   VALUES (?,?,?,?,?,?,datetime('now'))""",
                (
                    json.dumps([p.get("param_key", "") for p in patterns[:5]], ensure_ascii=False),
                    c.get("parent_pattern", templates[0].get("strategy_id", "") if templates else ""),
                    c.get("name", f"genesis_{uuid.uuid4().hex[:8]}"),
                    json.dumps(c.get("config", {}), ensure_ascii=False),
                    "queued",
                    round(float(c.get("viability_score", 0.5) or 0.5), 3),
                ),
            )
            if wid:
                logger.info("[Hermes:L4] 策略创生候选: id=%d name=%s", wid, c.get("name", "?"))

        self._last_llm_error = llm_error
        self._last_llm_parsed_ok = bool(candidates) or not llm_error
        return candidates

    def incubate_candidates(self) -> int:
        """将 queued 候选部署到 paper 环境孵化。"""
        candidates = hermes_fetchall(
            "SELECT * FROM strategy_genesis_candidates WHERE paper_status='queued' LIMIT 5"
        )
        deployed = 0
        for c in candidates:
            try:
                config = json.loads(c.get("variant_config", "{}"))
                if not config:
                    continue

                # 构建 AIStrategy 记录（paper 环境）
                strategy_id = self._strategy_id_for_candidate(c)
                # 创建失败：标记为 failed，避免「无策略在跑却被标 incubating」的孤儿候选
                if not self._create_paper_strategy(strategy_id, config):
                    hermes_execute(
                        "UPDATE strategy_genesis_candidates SET paper_status='failed' WHERE id=?",
                        (c["id"],),
                    )
                    logger.warning("[Hermes:L4] 创建策略失败，候选标 failed: id=%d", c["id"])
                    continue

                hermes_execute(
                    "UPDATE strategy_genesis_candidates SET paper_status='incubating' WHERE id=?",
                    (c["id"],),
                )
                deployed += 1
                logger.info("[Hermes:L4] 孵化部署: id=%d strategy=%s", c["id"], strategy_id)
            except Exception as e:
                logger.error("[Hermes:L4] 孵化部署失败 id=%d: %s", c["id"], e)
        return deployed

    def check_incubation_results(self) -> Dict[str, Any]:
        """检查孵化中的候选策略是否达标。"""
        candidates = hermes_fetchall(
            """SELECT * FROM strategy_genesis_candidates
               WHERE paper_status='incubating'
               ORDER BY viability_score DESC, created_at DESC"""
        )
        validated = 0
        rejected = 0

        for idx, c in enumerate(candidates):
            try:
                strategy_id = self._strategy_id_for_candidate(c)
                if idx < MAX_ACTIVE_INCUBATIONS:
                    self._ensure_incubation_strategy_running(c, strategy_id)
                perf = self._get_paper_performance(strategy_id)

                total_trades = int(perf.get("total_closed", 0))
                wr = float(perf.get("win_rate", 0))
                avg_pnl = (float(perf.get("total_pnl", 0)) / max(total_trades, 1))
                days = self._candidate_age_days(c)

                hermes_execute(
                    """UPDATE strategy_genesis_candidates
                       SET paper_pnl=?, paper_win_rate=?, paper_trades=?, paper_days=?
                       WHERE id=?""",
                    (round(avg_pnl, 2), round(wr, 3), total_trades, round(days, 1), c["id"]),
                )

                if (
                    total_trades >= MIN_PAPER_TRADES
                    and wr >= MIN_PAPER_WIN_RATE
                    and avg_pnl >= MIN_PAPER_AVG_PNL
                ):
                    hermes_execute(
                        "UPDATE strategy_genesis_candidates SET paper_status='validated', validated_at=datetime('now') WHERE id=?",
                        (c["id"],),
                    )
                    validated += 1
                    logger.info("[Hermes:L4] 孵化达标: id=%d name=%s trades=%d wr=%.0f%%", c["id"], c["variant_name"], total_trades, wr * 100)
                elif total_trades >= MIN_PAPER_TRADES * 2 and wr < 0.35:
                    hermes_execute(
                        "UPDATE strategy_genesis_candidates SET paper_status='rejected' WHERE id=?",
                        (c["id"],),
                    )
                    rejected += 1
                    logger.info("[Hermes:L4] 孵化不达标: id=%d name=%s trades=%d wr=%.0f%%", c["id"], c["variant_name"], total_trades, wr * 100)
            except Exception as e:
                logger.error("[Hermes:L4] 检查孵化 %d: %s", c["id"], e)

        return {"validated": validated, "rejected": rejected}

    def propose_promote_validated(self, candidate_id: int) -> Dict[str, Any]:
        """validated 候选 → RuntimeGovernor 晋升 live。"""
        from backend.services.hermes_db import hermes_fetchone
        c = hermes_fetchone(
            "SELECT * FROM strategy_genesis_candidates WHERE id=?",
            (candidate_id,),
        )
        if not c:
            return {"ok": False, "error": "候选不存在"}
        if c.get("paper_status") != "validated":
            return {"ok": False, "error": f"状态非 validated: {c.get('paper_status')}"}
        strategy_id = self._strategy_id_for_candidate(c)
        from backend.services.runtime_governor import runtime_governor
        patch = runtime_governor.propose_genesis_promote(
            candidate_id, strategy_id,
            reason=f"Hermes L4 孵化达标晋升: {c.get('variant_name')}",
        )
        return {"ok": True, "candidate_id": candidate_id, "strategy_id": strategy_id, "governor_patch_id": patch.patch_id}

    def auto_propose_validated_promotions(self) -> Dict[str, Any]:
        """扫描 validated 候选并提交 Governor 审批。"""
        rows = hermes_fetchall(
            "SELECT id FROM strategy_genesis_candidates WHERE paper_status='validated'",
        )
        proposed = 0
        for r in rows:
            res = self.propose_promote_validated(int(r["id"]))
            if res.get("ok"):
                proposed += 1
        return {"proposed": proposed}

    def get_stats(self) -> Dict[str, int]:
        """获取策略创生统计。"""
        stats = hermes_fetchall(
            "SELECT paper_status, COUNT(*) as cnt FROM strategy_genesis_candidates GROUP BY paper_status"
        )
        result = {
            "total": 0,
            "queued": 0,
            "incubating": 0,
            "validated": 0,
            "rejected": 0,
            "promoted_live": 0,
            "failed": 0,
        }
        for r in stats:
            status = r["paper_status"] or "unknown"
            result[status] = result.get(status, 0) + int(r["cnt"])
            result["total"] += int(r["cnt"])
        return result

    # ──── 私有辅助 ────

    @staticmethod
    def _strategy_id_for_candidate(candidate: Dict[str, Any]) -> str:
        """生成稳定且不超过 ai_strategies.strategy_id 长度限制的候选策略ID。"""
        raw_name = str(candidate.get("variant_name") or "genesis")
        safe_name = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in raw_name)
        cid = int(candidate.get("id") or 0)
        suffix = f"_{cid}"
        base = f"gen_{safe_name}"
        return (base[: 50 - len(suffix)] + suffix)[:50]

    @staticmethod
    def _candidate_age_days(candidate: Dict[str, Any]) -> float:
        """按候选创建时间计算孵化天数；解析失败则回退旧字段。"""
        created = candidate.get("created_at")
        if created:
            try:
                dt = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0)
            except Exception:
                pass
        return float(candidate.get("paper_days") or 0)

    def _ensure_incubation_strategy_running(self, candidate: Dict[str, Any], strategy_id: str) -> bool:
        """确保高分孵化候选处于 paper-only active 状态。

        历史版本会把候选创建成 draft/auto_execute=false，导致 180 个候选全部没有
        paper trades。这里只唤醒前 MAX_ACTIVE_INCUBATIONS 个高分候选，避免一次性
        激活过多实验策略。
        """
        db = get_main_session()
        try:
            from backend.database.models import AIStrategy
            from backend.database.connection import sqlite_write_commit

            row = db.query(AIStrategy).filter(AIStrategy.strategy_id == strategy_id).first()
            if row:
                changed = False
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                if row.status != "active":
                    row.status = "active"
                    row.activated_at = row.activated_at or now
                    changed = True
                if not row.auto_execute:
                    row.auto_execute = True
                    changed = True
                if row.require_confirmation:
                    row.require_confirmation = False
                    changed = True
                if row.auto_mode != "full_auto":
                    row.auto_mode = "full_auto"
                    changed = True
                genome = dict(row.genome) if isinstance(row.genome, dict) else {}
                if genome.get("hermes_candidate_id") != candidate.get("id"):
                    genome.update({
                        "source": "hermes_genesis",
                        "paper_only": True,
                        "hermes_candidate_id": candidate.get("id"),
                    })
                    row.genome = genome
                    changed = True
                if changed:
                    sqlite_write_commit(db, label="hermes_genesis_reactivate")
                    logger.info("[Hermes:L4] 唤醒孵化策略: candidate=%s strategy=%s", candidate.get("id"), strategy_id)
                return True

            config = json.loads(candidate.get("variant_config", "{}") or "{}")
            if not config:
                return False
            return self._create_paper_strategy(strategy_id, config)
        except Exception as e:
            logger.warning("[Hermes:L4] 唤醒孵化策略失败 id=%s: %s", candidate.get("id"), e)
            return False
        finally:
            db.close()

    def _create_paper_strategy(self, strategy_id: str, config: Dict[str, Any]) -> bool:
        """在 paper 环境创建策略记录。成功返回 True，失败返回 False（避免孤儿候选）。"""
        db = get_main_session()
        try:
            from backend.database.models import AIStrategy

            symbols = config.get("target_symbols") or config.get("symbols") or ["BTC"]
            if isinstance(symbols, str):
                symbols = [symbols]
            primary = config.get("primary_symbol") or (symbols[0] if symbols else "BTC")
            account_id = self._resolve_default_account_id(db)
            parent_id = config.get("parent_strategy_id") or config.get("template_seed")
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            genome = config.get("genome") if isinstance(config.get("genome"), dict) else {}
            if not genome:
                genome = {
                    "source": "hermes_genesis",
                    "parent_strategy_id": parent_id,
                    "generation": 1,
                }
            genome.update({
                "source": "hermes_genesis",
                "paper_only": True,
                "incubation": True,
            })

            s = AIStrategy(
                strategy_id=strategy_id,
                name=config.get("name", strategy_id),
                description=f"[Hermes创生] {config.get('description', '')}",
                account_id=account_id,
                target_symbols=symbols,
                primary_symbol=primary,
                timeframe=config.get("timeframe", "15m"),
                timeframe_tier=config.get("timeframe_tier", "mid"),
                status="active",
                max_position_size=float(config.get("max_position_size", 0.10)),
                stop_loss_pct=float(config.get("stop_loss_pct", 0.03)),
                take_profit_pct=float(config.get("take_profit_pct", 0.06)),
                auto_execute=True,
                require_confirmation=False,
                auto_mode="full_auto",
                learning_enabled=True,
                parent_strategy_id=str(parent_id) if parent_id else None,
                lineage_generation=int(config.get("lineage_generation", 1)),
                genome=genome,
                created_at=now,
                activated_at=now,
            )
            db.add(s)
            from backend.database.connection import sqlite_write_commit
            sqlite_write_commit(db)
            return True
        except Exception as e:
            logger.error("[Hermes:L4] 创建 paper 策略失败: %s", e)
            return False
        finally:
            db.close()

    def _resolve_default_account_id(self, db) -> int:
        """解析 paper 模拟账户；无 paper 账户时回退首个活跃账户。"""
        from backend.database.models import Account

        acct = (
            db.query(Account)
            .filter(Account.trading_mode == "paper", Account.is_active == "true")
            .order_by(Account.id.asc())
            .first()
        )
        if not acct:
            acct = (
                db.query(Account)
                .filter(Account.is_active == "true")
                .order_by(Account.id.asc())
                .first()
            )
        if not acct:
            raise ValueError("无可用账户，无法创建创生策略")
        return int(acct.id)

    def _get_paper_performance(self, strategy_id: str) -> Dict[str, Any]:
        """获取 paper 策略的表现数据。"""
        db = get_main_session()
        try:
            from backend.database.models import StrategyTrade
            trades = (
                db.query(StrategyTrade)
                .filter(
                    StrategyTrade.strategy_id == strategy_id,
                    StrategyTrade.status == "closed",
                )
                .all()
            )
            total = len(trades)
            wins = sum(1 for t in trades if (t.pnl or 0) > 0)
            total_pnl = sum(t.pnl or 0 for t in trades)
            return {
                "total_closed": total,
                "win_rate": wins / max(total, 1),
                "total_pnl": total_pnl,
            }
        finally:
            db.close()

    def _load_l4_system_prompt(self) -> str:
        from backend.services.hermes_db import resolve_hermes_prompt_path

        prompt_path = resolve_hermes_prompt_path("tasks", "task_strategy_genesis.md")
        try:
            if os.path.isfile(prompt_path):
                with open(prompt_path, encoding="utf-8") as f:
                    return f.read()
        except Exception:
            pass
        return (
            "You are Hermes, a strategy genesis specialist. "
            "From validated successful proposal patterns, extract strategy DNA "
            "and generate novel strategy variants for paper environment incubation. "
            "Output JSON with: candidates (array of {name, parent_pattern, config, "
            "differentiators, risk_assessment, viability_score})."
        )

    @staticmethod
    def _json_output_protocol(expected_fields: str) -> str:
        """强制 LLM 直出 JSON、禁用工具/代码探索的输出协议（同 L3）。

        plan agent 在含上下文的开放提问下会进入代码探索工具循环，耗尽生成也不
        产 JSON，导致 L4 永远 0 候选。本协议前置约束，确保直出可解析 JSON。
        """
        return (
            "CRITICAL OUTPUT PROTOCOL (override any tool-use impulse):\n"
            "- Do NOT explore the codebase, do NOT call any tools, do NOT read files.\n"
            "- Do NOT narrate, explain, or output prose. Do NOT output markdown.\n"
            "- Your ENTIRE response must be a single valid JSON object, nothing else.\n"
            "- Base the answer solely on the data provided in the user message.\n"
            f"- Output schema: {expected_fields}\n"
            "- If insufficient data, still return the JSON object with an empty array "
            "and a short \"note\" field. Begin your response with '{'.\n\n"
        )

    def _call_llm(self, system_prompt: str, user_text: str) -> Dict[str, Any]:
        try:
            from backend.services.opencode_bridge import (
                collect_http_agent_stream_text,
                _agent_plan,
                _model,
                _extract_json,
            )
            # 前置 JSON 输出协议，压制 plan agent 的代码探索冲动
            protocol = self._json_output_protocol(
                '{"candidates": [{name, parent_pattern, config, differentiators, '
                'risk_assessment, viability_score}]}'
            )
            raw, err = collect_http_agent_stream_text(
                system_prompt=protocol + system_prompt,
                user_text=user_text,
                agent=_agent_plan(),
                model_slug=_model(),
                session_title="Hermes L4: Strategy Genesis",
                log_prefix="Hermes:L4",
                idle_timeout_s=900.0,
                max_duration_s=7200.0,
            )
            if err:
                return {"candidates": [], "error": err}
            return _extract_json(raw or "")
        except Exception as e:
            return {"candidates": [], "error": str(e)}


# 全局单例
strategy_genesis = StrategyGenesisEngine()
