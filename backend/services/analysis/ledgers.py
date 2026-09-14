# -*- coding: utf-8 -*-
"""分析层四张账本 + 配额用量表（v3 方向 2/3/8 的数据底座）。

表（主库，系统级、无租户列；建表走 CREATE TABLE IF NOT EXISTS，首次访问懒建）：

  analysis_runs      每一次模型调用 / 共识合成一行：任务、传输、模型、状态、context_hash、
                     data_cutoff_ms、token/耗时/成本、输出 JSON、consensus_score、consensus_group
  signal_ledger      每条信号（来源、币、方向、强度、置信、期限、生成时上下文 hash）；
                     到期由 score_due() 回填命中 / 收益 bp / 相对 BTC 超额 / Brier
  agent_predictions  每个 Agent 的预测（kind / subject / prediction JSON / 置信 / 期限）；
                     到期评分：direction 类内建按价格评分，其它 kind 由注册的 outcome 评估器给结果
  experiments        实验卡：假设 / 改动 / 预期指标与阈值 / 验证窗口 / 回滚条件 / 生命周期状态
  llm_quota_usage    QuotaGuard 的用量流水（每次调用 token、耗时、成败）

约束：
  - 全部写入幂等（主键为调用方生成的 uuid），失败只记日志不抛给业务；
  - 评分只用真实价格（market 库 crypto_klines 1m 收盘），拿不到价格 → 保持 open 等下轮，
    超过 expires + 48h 仍无价格 → 标 void（不造分）。
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_schema_ready = False
_schema_lock = threading.Lock()

# kind → evaluator(prediction_row) -> Optional[Dict]（返回 {"score":0..1,"outcome":{...}} 或 None=尚不可评）
_OUTCOME_EVALUATORS: Dict[str, Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]] = {}

VOID_AFTER_MS = 48 * 3600 * 1000  # 到期后 48h 仍无法取价 → void


def now_ms() -> int:
    return int(time.time() * 1000)


def fee_summary(days: int = 7, account_id: int = 14) -> Dict[str, Any]:
    """[验收轮2 2026-09-14] E1-F4 费用预算项的数据源（此前缺 fee_summary → F4 恒 inconclusive）。

    口径：近 `days` 天 paper_orders.fee 合计（E1 账户），除以当前权益 → fee_pct。
    失败返回空 dict（调用方按 inconclusive 处理，不假装通过）。
    """
    out: Dict[str, Any] = {}
    try:
        from sqlalchemy import text

        db = _db()
        try:
            row = db.execute(text(
                "SELECT coalesce(sum(o.fee),0) FROM paper_orders o "
                "WHERE o.account_id = :a AND o.created_at >= now() - make_interval(days => :d)"),
                {"a": int(account_id or 14), "d": int(days or 7)}).first()
            fee_usd = float(row[0]) if row and row[0] is not None else 0.0
            eq_row = db.execute(text(
                "SELECT total_equity FROM paper_balances WHERE account_id = :a"),
                {"a": int(account_id or 14)}).first()
            equity = float(eq_row[0]) if eq_row and eq_row[0] is not None else 0.0
            out = {
                "fee_usd": round(fee_usd, 4),
                "equity": round(equity, 4),
                # [验收轮2] F4 门读取的键名是 equity_usd（edge_ledger 口径）
                "equity_usd": round(equity, 4),
                "fee_pct": round(fee_usd / equity, 6) if equity > 0 else None,
                "days": int(days or 7),
                "account_id": int(account_id or 14),
            }
        finally:
            db.close()
    except Exception as exc:
        logger.debug("[ledgers] fee_summary 失败: %s", exc)
    return out


def new_id() -> str:
    return uuid.uuid4().hex


def _db():
    from backend.database.connection import SessionLocal
    from backend.core.tenant import set_system_identity

    set_system_identity()
    return SessionLocal()


def _json(obj: Any) -> Optional[str]:
    if obj is None:
        return None
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        return json.dumps(str(obj), ensure_ascii=False)


def _loads(s: Any) -> Any:
    if s is None:
        return None
    if isinstance(s, (dict, list)):
        return s
    try:
        return json.loads(s)
    except Exception:
        return s


# --------------------------------------------------------------------------- schema
def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        from sqlalchemy import text

        ddl = [
            """
            CREATE TABLE IF NOT EXISTS analysis_runs (
                id VARCHAR(40) PRIMARY KEY,
                created_ms BIGINT NOT NULL,
                task VARCHAR(48) NOT NULL,
                transport VARCHAR(24) NOT NULL,
                model VARCHAR(80),
                role VARCHAR(16) NOT NULL DEFAULT 'primary',
                status VARCHAR(16) NOT NULL,
                consensus_group VARCHAR(40),
                context_hash VARCHAR(64),
                data_cutoff_ms BIGINT,
                system_prompt_hash VARCHAR(64),
                prompt_excerpt TEXT,
                context_pack JSONB,
                output_text TEXT,
                output_json JSONB,
                consensus_score DOUBLE PRECISION,
                input_tokens INTEGER,
                output_tokens INTEGER,
                latency_ms INTEGER,
                cost_usd DOUBLE PRECISION,
                error TEXT,
                meta JSONB
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_analysis_runs_task_created ON analysis_runs (task, created_ms DESC)",
            "CREATE INDEX IF NOT EXISTS idx_analysis_runs_group ON analysis_runs (consensus_group)",
            """
            CREATE TABLE IF NOT EXISTS signal_ledger (
                id VARCHAR(40) PRIMARY KEY,
                created_ms BIGINT NOT NULL,
                source VARCHAR(64) NOT NULL,
                symbol VARCHAR(32) NOT NULL,
                direction SMALLINT NOT NULL,
                strength DOUBLE PRECISION,
                confidence DOUBLE PRECISION,
                horizon_ms BIGINT NOT NULL,
                expires_ms BIGINT NOT NULL,
                entry_price DOUBLE PRECISION,
                regime VARCHAR(32),
                context_hash VARCHAR(64),
                analysis_run_id VARCHAR(40),
                payload JSONB,
                status VARCHAR(12) NOT NULL DEFAULT 'open',
                scored_ms BIGINT,
                exit_price DOUBLE PRECISION,
                ret_bp DOUBLE PRECISION,
                btc_ret_bp DOUBLE PRECISION,
                excess_bp DOUBLE PRECISION,
                hit SMALLINT,
                brier DOUBLE PRECISION
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_signal_ledger_status_exp ON signal_ledger (status, expires_ms)",
            "CREATE INDEX IF NOT EXISTS idx_signal_ledger_source_created ON signal_ledger (source, created_ms DESC)",
            "CREATE INDEX IF NOT EXISTS idx_signal_ledger_symbol_created ON signal_ledger (symbol, created_ms DESC)",
            """
            CREATE TABLE IF NOT EXISTS agent_predictions (
                id VARCHAR(40) PRIMARY KEY,
                created_ms BIGINT NOT NULL,
                agent VARCHAR(48) NOT NULL,
                kind VARCHAR(32) NOT NULL,
                subject VARCHAR(48) NOT NULL DEFAULT '*',
                prediction JSONB NOT NULL,
                confidence DOUBLE PRECISION,
                horizon_ms BIGINT NOT NULL,
                expires_ms BIGINT NOT NULL,
                context_hash VARCHAR(64),
                analysis_run_id VARCHAR(40),
                status VARCHAR(12) NOT NULL DEFAULT 'open',
                scored_ms BIGINT,
                outcome JSONB,
                score DOUBLE PRECISION,
                brier DOUBLE PRECISION
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_agent_pred_status_exp ON agent_predictions (status, expires_ms)",
            "CREATE INDEX IF NOT EXISTS idx_agent_pred_agent_created ON agent_predictions (agent, created_ms DESC)",
            """
            CREATE TABLE IF NOT EXISTS experiments (
                id VARCHAR(40) PRIMARY KEY,
                created_ms BIGINT NOT NULL,
                source VARCHAR(80) NOT NULL,
                title VARCHAR(200) NOT NULL,
                hypothesis TEXT NOT NULL,
                change JSONB NOT NULL,
                expected_metrics JSONB NOT NULL,
                window_hours INTEGER NOT NULL,
                rollback_condition TEXT,
                status VARCHAR(16) NOT NULL DEFAULT 'proposed',
                started_ms BIGINT,
                ends_ms BIGINT,
                config_hash_before VARCHAR(64),
                config_hash_after VARCHAR(64),
                result JSONB,
                decision VARCHAR(16),
                decided_ms BIGINT,
                decided_by VARCHAR(64),
                analysis_run_id VARCHAR(40),
                notes TEXT
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_experiments_status ON experiments (status, created_ms DESC)",
            """
            CREATE TABLE IF NOT EXISTS llm_quota_usage (
                id BIGSERIAL PRIMARY KEY,
                ts_ms BIGINT NOT NULL,
                transport VARCHAR(24) NOT NULL,
                model VARCHAR(80),
                task VARCHAR(48),
                task_class VARCHAR(16),
                input_tokens INTEGER,
                output_tokens INTEGER,
                latency_ms INTEGER,
                ok BOOLEAN,
                cost_usd DOUBLE PRECISION,
                run_id VARCHAR(40)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_llm_quota_usage_ts ON llm_quota_usage (transport, ts_ms DESC)",
        ]
        db = _db()
        try:
            for stmt in ddl:
                db.execute(text(stmt))
            db.commit()
            _schema_ready = True
        except Exception as exc:
            db.rollback()
            logger.warning("[analysis.ledgers] 建表失败（本轮降级为仅日志）: %s", exc)
        finally:
            db.close()


# --------------------------------------------------------------------------- analysis_runs
@dataclass
class AnalysisRun:
    task: str
    transport: str
    model: Optional[str] = None
    role: str = "primary"
    status: str = "ok"
    consensus_group: Optional[str] = None
    context_hash: Optional[str] = None
    data_cutoff_ms: Optional[int] = None
    system_prompt_hash: Optional[str] = None
    prompt_excerpt: Optional[str] = None
    context_pack: Optional[Dict[str, Any]] = None
    output_text: Optional[str] = None
    output_json: Optional[Dict[str, Any]] = None
    consensus_score: Optional[float] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    latency_ms: Optional[int] = None
    cost_usd: Optional[float] = None
    error: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None
    id: str = field(default_factory=new_id)
    created_ms: int = field(default_factory=now_ms)


def record_run(run: AnalysisRun) -> str:
    """写一行 analysis_runs（幂等 upsert，失败只记日志）。返回 run.id。"""
    ensure_schema()
    from sqlalchemy import text

    row = asdict(run)
    for k in ("context_pack", "output_json", "meta"):
        row[k] = _json(row[k])
    if row.get("prompt_excerpt") and len(row["prompt_excerpt"]) > 8000:
        row["prompt_excerpt"] = row["prompt_excerpt"][:8000]
    if row.get("output_text") and len(row["output_text"]) > 60000:
        row["output_text"] = row["output_text"][:60000]
    db = _db()
    try:
        db.execute(
            text(
                """
                INSERT INTO analysis_runs (id, created_ms, task, transport, model, role, status, consensus_group,
                    context_hash, data_cutoff_ms, system_prompt_hash, prompt_excerpt, context_pack, output_text,
                    output_json, consensus_score, input_tokens, output_tokens, latency_ms, cost_usd, error, meta)
                VALUES (:id, :created_ms, :task, :transport, :model, :role, :status, :consensus_group,
                    :context_hash, :data_cutoff_ms, :system_prompt_hash, :prompt_excerpt, CAST(:context_pack AS JSONB),
                    :output_text, CAST(:output_json AS JSONB), :consensus_score, :input_tokens, :output_tokens,
                    :latency_ms, :cost_usd, :error, CAST(:meta AS JSONB))
                ON CONFLICT (id) DO UPDATE SET
                    status = EXCLUDED.status, output_text = EXCLUDED.output_text, output_json = EXCLUDED.output_json,
                    consensus_score = EXCLUDED.consensus_score, input_tokens = EXCLUDED.input_tokens,
                    output_tokens = EXCLUDED.output_tokens, latency_ms = EXCLUDED.latency_ms,
                    cost_usd = EXCLUDED.cost_usd, error = EXCLUDED.error, meta = EXCLUDED.meta
                """
            ),
            row,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning("[analysis.ledgers] record_run 失败 task=%s transport=%s: %s", run.task, run.transport, exc)
    finally:
        db.close()
    return run.id


def list_runs(
    *,
    task: Optional[str] = None,
    consensus_group: Optional[str] = None,
    transport: Optional[str] = None,
    since_ms: Optional[int] = None,
    limit: int = 100,
    include_context: bool = False,
) -> List[Dict[str, Any]]:
    ensure_schema()
    from sqlalchemy import text

    cols = (
        "id, created_ms, task, transport, model, role, status, consensus_group, context_hash, data_cutoff_ms, "
        "system_prompt_hash, prompt_excerpt, output_text, output_json, consensus_score, input_tokens, "
        "output_tokens, latency_ms, cost_usd, error, meta"
    )
    if include_context:
        cols += ", context_pack"
    where, params = ["1=1"], {"limit": max(1, min(int(limit), 1000))}
    if task:
        where.append("task = :task")
        params["task"] = task
    if consensus_group:
        where.append("consensus_group = :grp")
        params["grp"] = consensus_group
    if transport:
        where.append("transport = :tr")
        params["tr"] = transport
    if since_ms:
        where.append("created_ms >= :since")
        params["since"] = int(since_ms)
    db = _db()
    try:
        rows = db.execute(
            text(f"SELECT {cols} FROM analysis_runs WHERE {' AND '.join(where)} ORDER BY created_ms DESC LIMIT :limit"),
            params,
        ).mappings().all()
        out = []
        for r in rows:
            d = dict(r)
            for k in ("output_json", "meta", "context_pack"):
                if k in d:
                    d[k] = _loads(d[k])
            out.append(d)
        return out
    except Exception as exc:
        logger.warning("[analysis.ledgers] list_runs 失败: %s", exc)
        return []
    finally:
        db.close()


def get_run(run_id: str) -> Optional[Dict[str, Any]]:
    ensure_schema()
    from sqlalchemy import text

    db = _db()
    try:
        r = db.execute(text("SELECT * FROM analysis_runs WHERE id = :id"), {"id": run_id}).mappings().first()
        if not r:
            return None
        d = dict(r)
        for k in ("output_json", "meta", "context_pack"):
            d[k] = _loads(d.get(k))
        return d
    except Exception as exc:
        logger.warning("[analysis.ledgers] get_run 失败: %s", exc)
        return None
    finally:
        db.close()


def runs_summary(days: int = 7) -> Dict[str, Any]:
    """近 N 天：按 task×transport 的次数 / 成功率 / token / 成本 / 平均共识分。看板与验收用。"""
    ensure_schema()
    from sqlalchemy import text

    since = now_ms() - int(days) * 86400 * 1000
    db = _db()
    try:
        rows = db.execute(
            text(
                """
                SELECT task, transport, role,
                       COUNT(*) AS n,
                       SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) AS n_ok,
                       COALESCE(SUM(input_tokens), 0) AS in_tok,
                       COALESCE(SUM(output_tokens), 0) AS out_tok,
                       COALESCE(SUM(cost_usd), 0) AS cost,
                       AVG(consensus_score) AS avg_consensus,
                       AVG(latency_ms) AS avg_latency_ms,
                       MAX(created_ms) AS last_ms
                FROM analysis_runs WHERE created_ms >= :since
                GROUP BY task, transport, role ORDER BY task, transport, role
                """
            ),
            {"since": since},
        ).mappings().all()
        days_with_brief = db.execute(
            text(
                """
                SELECT COUNT(DISTINCT (created_ms / 86400000)) FROM analysis_runs
                WHERE task = 'daily_brief' AND role = 'consensus' AND status IN ('ok','degraded') AND created_ms >= :since
                """
            ),
            {"since": since},
        ).scalar()
        return {
            "days": days,
            "rows": [dict(r) for r in rows],
            "daily_brief_days": int(days_with_brief or 0),
        }
    except Exception as exc:
        logger.warning("[analysis.ledgers] runs_summary 失败: %s", exc)
        return {"days": days, "rows": [], "daily_brief_days": 0, "error": str(exc)}
    finally:
        db.close()


# --------------------------------------------------------------------------- signal_ledger
def record_signal(
    *,
    source: str,
    symbol: str,
    direction: int,
    horizon_ms: int,
    strength: Optional[float] = None,
    confidence: Optional[float] = None,
    entry_price: Optional[float] = None,
    regime: Optional[str] = None,
    context_hash: Optional[str] = None,
    analysis_run_id: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    created_ms: Optional[int] = None,
    signal_id: Optional[str] = None,
) -> Optional[str]:
    """信号入账（所有信号源——因子 / Master LLM / thesis / 事件 / Agent / 双模型共识——先进账本再谈权重）。

    direction: +1 多 / -1 空 / 0 中性（中性的命中定义：到期 |ret| < 50bp）。
    entry_price 缺省时评分阶段按 created_ms 从 K 线补。
    """
    ensure_schema()
    from sqlalchemy import text

    created = int(created_ms or now_ms())
    sid = signal_id or new_id()
    sym = (symbol or "").upper().strip()
    if not sym or direction not in (-1, 0, 1) or horizon_ms <= 0:
        logger.warning("[signal_ledger] 非法信号被拒 source=%s symbol=%s dir=%s h=%s", source, symbol, direction, horizon_ms)
        return None
    db = _db()
    try:
        db.execute(
            text(
                """
                INSERT INTO signal_ledger (id, created_ms, source, symbol, direction, strength, confidence, horizon_ms,
                    expires_ms, entry_price, regime, context_hash, analysis_run_id, payload, status)
                VALUES (:id, :created, :source, :symbol, :direction, :strength, :confidence, :horizon,
                    :expires, :entry, :regime, :ch, :run, CAST(:payload AS JSONB), 'open')
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                "id": sid,
                "created": created,
                "source": source[:64],
                "symbol": sym[:32],
                "direction": int(direction),
                "strength": strength,
                "confidence": confidence,
                "horizon": int(horizon_ms),
                "expires": created + int(horizon_ms),
                "entry": entry_price,
                "regime": regime,
                "ch": context_hash,
                "run": analysis_run_id,
                "payload": _json(payload or {}),
            },
        )
        db.commit()
        return sid
    except Exception as exc:
        db.rollback()
        logger.warning("[signal_ledger] 入账失败 source=%s symbol=%s: %s", source, symbol, exc)
        return None
    finally:
        db.close()


def list_signals(
    *,
    source: Optional[str] = None,
    symbol: Optional[str] = None,
    status: Optional[str] = None,
    since_ms: Optional[int] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    ensure_schema()
    from sqlalchemy import text

    where, params = ["1=1"], {"limit": max(1, min(int(limit), 2000))}
    if source:
        where.append("source = :source")
        params["source"] = source
    if symbol:
        where.append("symbol = :symbol")
        params["symbol"] = symbol.upper()
    if status:
        where.append("status = :status")
        params["status"] = status
    if since_ms:
        where.append("created_ms >= :since")
        params["since"] = int(since_ms)
    db = _db()
    try:
        rows = db.execute(
            text(f"SELECT * FROM signal_ledger WHERE {' AND '.join(where)} ORDER BY created_ms DESC LIMIT :limit"),
            params,
        ).mappings().all()
        out = []
        for r in rows:
            d = dict(r)
            d["payload"] = _loads(d.get("payload"))
            out.append(d)
        return out
    except Exception as exc:
        logger.warning("[signal_ledger] list 失败: %s", exc)
        return []
    finally:
        db.close()


def signal_source_stats(days: int = 30) -> List[Dict[str, Any]]:
    """按信号源：N / 已评分 / 命中率 / 平均收益 bp / 平均超额 bp / 平均 Brier（SignalReview Agent 与看板的核心输入）。"""
    ensure_schema()
    from sqlalchemy import text

    since = now_ms() - int(days) * 86400 * 1000
    db = _db()
    try:
        rows = db.execute(
            text(
                """
                SELECT source,
                       COUNT(*) AS n,
                       SUM(CASE WHEN status = 'scored' THEN 1 ELSE 0 END) AS n_scored,
                       AVG(CASE WHEN status = 'scored' THEN hit END) AS hit_rate,
                       AVG(CASE WHEN status = 'scored' THEN ret_bp END) AS avg_ret_bp,
                       AVG(CASE WHEN status = 'scored' THEN excess_bp END) AS avg_excess_bp,
                       AVG(CASE WHEN status = 'scored' THEN brier END) AS avg_brier,
                       MAX(created_ms) AS last_ms
                FROM signal_ledger WHERE created_ms >= :since
                GROUP BY source ORDER BY n DESC
                """
            ),
            {"since": since},
        ).mappings().all()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("[signal_ledger] stats 失败: %s", exc)
        return []
    finally:
        db.close()


# --------------------------------------------------------------------------- agent_predictions
def record_prediction(
    *,
    agent: str,
    kind: str,
    prediction: Dict[str, Any],
    horizon_ms: int,
    subject: str = "*",
    confidence: Optional[float] = None,
    context_hash: Optional[str] = None,
    analysis_run_id: Optional[str] = None,
    created_ms: Optional[int] = None,
    prediction_id: Optional[str] = None,
) -> Optional[str]:
    """Agent 预测落库。kind：direction（内建评分，prediction 需含 direction/symbol 或 subject 为币）、
    regime / trading_state / numeric / event_impact 等由 register_outcome_evaluator(kind) 注册评估器评分。"""
    ensure_schema()
    from sqlalchemy import text

    created = int(created_ms or now_ms())
    pid = prediction_id or new_id()
    if horizon_ms <= 0 or not isinstance(prediction, dict):
        return None
    db = _db()
    try:
        db.execute(
            text(
                """
                INSERT INTO agent_predictions (id, created_ms, agent, kind, subject, prediction, confidence, horizon_ms,
                    expires_ms, context_hash, analysis_run_id, status)
                VALUES (:id, :created, :agent, :kind, :subject, CAST(:pred AS JSONB), :conf, :horizon, :expires,
                    :ch, :run, 'open')
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                "id": pid,
                "created": created,
                "agent": agent[:48],
                "kind": kind[:32],
                "subject": (subject or "*")[:48],
                "pred": _json(prediction),
                "conf": confidence,
                "horizon": int(horizon_ms),
                "expires": created + int(horizon_ms),
                "ch": context_hash,
                "run": analysis_run_id,
            },
        )
        db.commit()
        return pid
    except Exception as exc:
        db.rollback()
        logger.warning("[agent_predictions] 入账失败 agent=%s kind=%s: %s", agent, kind, exc)
        return None
    finally:
        db.close()


def list_predictions(
    *, agent: Optional[str] = None, kind: Optional[str] = None, status: Optional[str] = None, limit: int = 200
) -> List[Dict[str, Any]]:
    ensure_schema()
    from sqlalchemy import text

    where, params = ["1=1"], {"limit": max(1, min(int(limit), 2000))}
    if agent:
        where.append("agent = :agent")
        params["agent"] = agent
    if kind:
        where.append("kind = :kind")
        params["kind"] = kind
    if status:
        where.append("status = :status")
        params["status"] = status
    db = _db()
    try:
        rows = db.execute(
            text(f"SELECT * FROM agent_predictions WHERE {' AND '.join(where)} ORDER BY created_ms DESC LIMIT :limit"),
            params,
        ).mappings().all()
        out = []
        for r in rows:
            d = dict(r)
            d["prediction"] = _loads(d.get("prediction"))
            d["outcome"] = _loads(d.get("outcome"))
            out.append(d)
        return out
    except Exception as exc:
        logger.warning("[agent_predictions] list 失败: %s", exc)
        return []
    finally:
        db.close()


def agent_credibility(days: int = 30) -> List[Dict[str, Any]]:
    """Agent × kind 可信度矩阵：N / 已评分 / 平均 score / 平均 Brier / 最近一次。"""
    ensure_schema()
    from sqlalchemy import text

    since = now_ms() - int(days) * 86400 * 1000
    db = _db()
    try:
        rows = db.execute(
            text(
                """
                SELECT agent, kind,
                       COUNT(*) AS n,
                       SUM(CASE WHEN status = 'scored' THEN 1 ELSE 0 END) AS n_scored,
                       AVG(CASE WHEN status = 'scored' THEN score END) AS avg_score,
                       AVG(CASE WHEN status = 'scored' THEN brier END) AS avg_brier,
                       MAX(created_ms) AS last_ms
                FROM agent_predictions WHERE created_ms >= :since
                GROUP BY agent, kind ORDER BY agent, kind
                """
            ),
            {"since": since},
        ).mappings().all()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("[agent_predictions] credibility 失败: %s", exc)
        return []
    finally:
        db.close()


def register_outcome_evaluator(kind: str, fn: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]) -> None:
    """注册非 direction 类预测的结果评估器：fn(row) → {"score": 0..1, "outcome": {...}} 或 None（尚不可评）。"""
    _OUTCOME_EVALUATORS[kind] = fn


# --------------------------------------------------------------------------- experiments
EXPERIMENT_STATUSES = ("proposed", "running", "evaluating", "adopted", "rejected", "extended", "rolled_back")


def create_experiment(
    *,
    source: str,
    title: str,
    hypothesis: str,
    change: Dict[str, Any],
    expected_metrics: List[Dict[str, Any]],
    window_hours: int,
    rollback_condition: Optional[str] = None,
    config_hash_before: Optional[str] = None,
    analysis_run_id: Optional[str] = None,
    notes: Optional[str] = None,
    experiment_id: Optional[str] = None,
) -> Optional[str]:
    """实验卡：任何复盘建议必须写成 假设 / 改动 / 预期指标与阈值 / 验证窗口 / 回滚条件。

    expected_metrics: [{"metric": "net_bp", "op": ">", "threshold": 10, "scope": "lane:trend_e1"}, ...]
    """
    ensure_schema()
    from sqlalchemy import text

    if not title or not hypothesis or not isinstance(change, dict) or not expected_metrics or window_hours <= 0:
        logger.warning("[experiments] 实验卡字段不完整被拒: %s", title)
        return None
    eid = experiment_id or new_id()
    db = _db()
    try:
        db.execute(
            text(
                """
                INSERT INTO experiments (id, created_ms, source, title, hypothesis, change, expected_metrics, window_hours,
                    rollback_condition, status, config_hash_before, analysis_run_id, notes)
                VALUES (:id, :created, :source, :title, :hyp, CAST(:change AS JSONB), CAST(:metrics AS JSONB), :wh,
                    :rb, 'proposed', :chb, :run, :notes)
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                "id": eid,
                "created": now_ms(),
                "source": source[:80],
                "title": title[:200],
                "hyp": hypothesis,
                "change": _json(change),
                "metrics": _json(expected_metrics),
                "wh": int(window_hours),
                "rb": rollback_condition,
                "chb": config_hash_before,
                "run": analysis_run_id,
                "notes": notes,
            },
        )
        db.commit()
        return eid
    except Exception as exc:
        db.rollback()
        logger.warning("[experiments] 创建失败: %s", exc)
        return None
    finally:
        db.close()


def transition_experiment(
    experiment_id: str,
    status: str,
    *,
    result: Optional[Dict[str, Any]] = None,
    decision: Optional[str] = None,
    decided_by: Optional[str] = None,
    config_hash_after: Optional[str] = None,
    notes: Optional[str] = None,
) -> bool:
    """状态迁移：proposed→running（记 started/ends）→evaluating→adopted/rejected/extended；任何态→rolled_back。"""
    ensure_schema()
    from sqlalchemy import text

    if status not in EXPERIMENT_STATUSES:
        return False
    db = _db()
    try:
        row = db.execute(text("SELECT window_hours, status FROM experiments WHERE id = :id"), {"id": experiment_id}).first()
        if not row:
            return False
        sets, params = ["status = :status"], {"id": experiment_id, "status": status}
        if status == "running":
            started = now_ms()
            sets += ["started_ms = :started", "ends_ms = :ends"]
            params["started"] = started
            params["ends"] = started + int(row[0]) * 3600 * 1000
        if status == "extended":
            sets.append("ends_ms = COALESCE(ends_ms, :now) + :ext")
            params["now"] = now_ms()
            params["ext"] = int(row[0]) * 3600 * 1000
        if result is not None:
            sets.append("result = CAST(:result AS JSONB)")
            params["result"] = _json(result)
        if decision is not None:
            sets += ["decision = :decision", "decided_ms = :dms", "decided_by = :dby"]
            params["decision"] = decision[:16]
            params["dms"] = now_ms()
            params["dby"] = (decided_by or "rules")[:64]
        if config_hash_after is not None:
            sets.append("config_hash_after = :cha")
            params["cha"] = config_hash_after
        if notes is not None:
            sets.append("notes = :notes")
            params["notes"] = notes
        db.execute(text(f"UPDATE experiments SET {', '.join(sets)} WHERE id = :id"), params)
        db.commit()
        return True
    except Exception as exc:
        db.rollback()
        logger.warning("[experiments] 迁移失败 id=%s → %s: %s", experiment_id, status, exc)
        return False
    finally:
        db.close()


def list_experiments(*, status: Optional[str] = None, source: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    ensure_schema()
    from sqlalchemy import text

    where, params = ["1=1"], {"limit": max(1, min(int(limit), 1000))}
    if status:
        where.append("status = :status")
        params["status"] = status
    if source:
        where.append("source = :source")
        params["source"] = source
    db = _db()
    try:
        rows = db.execute(
            text(f"SELECT * FROM experiments WHERE {' AND '.join(where)} ORDER BY created_ms DESC LIMIT :limit"),
            params,
        ).mappings().all()
        out = []
        for r in rows:
            d = dict(r)
            for k in ("change", "expected_metrics", "result"):
                d[k] = _loads(d.get(k))
            out.append(d)
        return out
    except Exception as exc:
        logger.warning("[experiments] list 失败: %s", exc)
        return []
    finally:
        db.close()


def experiment_source_stats(days: int = 90) -> List[Dict[str, Any]]:
    """元评分：按建议来源统计 提出数 / 采纳率 / 否决率（低于阈值的来源自动降权的依据）。"""
    ensure_schema()
    from sqlalchemy import text

    since = now_ms() - int(days) * 86400 * 1000
    db = _db()
    try:
        rows = db.execute(
            text(
                """
                SELECT source, COUNT(*) AS n,
                       SUM(CASE WHEN status = 'adopted' THEN 1 ELSE 0 END) AS n_adopted,
                       SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) AS n_rejected,
                       SUM(CASE WHEN status IN ('running','evaluating','extended') THEN 1 ELSE 0 END) AS n_active
                FROM experiments WHERE created_ms >= :since GROUP BY source ORDER BY n DESC
                """
            ),
            {"since": since},
        ).mappings().all()
        out = []
        for r in rows:
            d = dict(r)
            decided = int(d["n_adopted"] or 0) + int(d["n_rejected"] or 0)
            d["adoption_rate"] = (int(d["n_adopted"] or 0) / decided) if decided else None
            out.append(d)
        return out
    except Exception as exc:
        logger.warning("[experiments] source stats 失败: %s", exc)
        return []
    finally:
        db.close()


# --------------------------------------------------------------------------- scoring
def _kline_base(symbol: str) -> str:
    """'BTCUSDT' / 'ETH/USDT:USDT' / 'SOL-PERP' → K 线库的 base 符号（'BTC' / 'ETH' / 'SOL'）。"""
    s = (symbol or "").upper().replace("-", "").replace("/", "").replace(":", "")
    changed = True
    while changed:
        changed = False
        for suf in ("USDT", "USDC", "USD", "PERP"):
            if s.endswith(suf) and len(s) > len(suf):
                s = s[: -len(suf)]
                changed = True
    return s


def price_at(symbol: str, ts_ms: int, *, max_lag_min: int = 180) -> Optional[float]:
    """market 库 crypto_klines（binance, 1m）中 ≤ ts 的最近收盘价；超过 max_lag_min 分钟视为无价。"""
    try:
        from sqlalchemy import text
        from backend.database.connection import MarketSessionLocal
    except Exception:
        return None
    base = _kline_base(symbol)
    ts = int(ts_ms // 1000)
    db = MarketSessionLocal()
    try:
        row = db.execute(
            text(
                """
                SELECT close_price, timestamp FROM crypto_klines
                WHERE symbol = :s AND period = '1m' AND exchange = 'binance' AND timestamp <= :ts AND timestamp >= :lo
                ORDER BY timestamp DESC LIMIT 1
                """
            ),
            {"s": base, "ts": ts, "lo": ts - max_lag_min * 60},
        ).first()
        if row and row[0]:
            return float(row[0])
        return None
    except Exception as exc:
        logger.debug("[analysis.ledgers] price_at 失败 %s@%s: %s", symbol, ts_ms, exc)
        return None
    finally:
        db.close()


def _is_benchmark_symbol(symbol: Optional[str]) -> bool:
    """标的本身就是基准（BTC）——此时「相对 BTC 的超额」没有意义。"""
    s = str(symbol or "").upper().strip()
    for suffix in ("USDT", "USDC", "USD", "PERP", "-", "_", "/"):
        s = s.replace(suffix, "")
    return s in ("BTC", "XBT")


def _score_direction(direction: int, p0: float, p1: float, b0: Optional[float], b1: Optional[float],
                     confidence: Optional[float], symbol: Optional[str] = None):
    ret = (p1 / p0 - 1.0) * 1e4
    if direction == 0:
        ret_bp = -abs(ret)  # 中性：越不动越好
        hit = 1 if abs(ret) < 50 else 0
    else:
        ret_bp = direction * ret
        hit = 1 if ret_bp > 0 else 0
    btc_ret_bp = ((b1 / b0 - 1.0) * 1e4) if (b0 and b1) else None
    # [2026-09-04] 标的即基准时不再扣减基准。
    # 原实现对 symbol=BTC 的信号也减 BTC 基准，而 ret_bp = direction×ret_btc、
    # btc_ret_bp = ret_btc，相减**恒等于 0**。后果不是少了一个指标那么简单：
    # signal_review 的 verdict / IC / 半衰期 / regime 分层全部基于 excess_bp，
    # excess 恒 0 会让 _verdict 的三个分支（ex_hi<0 / ex_lo>0 / ex_mean<-5）
    # 全部不成立 → BTC 信号永远返回 keep，再差也停不掉。
    # 实测 e5_5_news_hedge：7 笔里 6 笔是 BTC，命中率 28.6%、平均 -115bp，
    # 却因 avg_excess 被稀释到 -2.5bp 而长期停在 observe。
    if btc_ret_bp is not None and direction != 0 and not _is_benchmark_symbol(symbol):
        excess_bp = ret_bp - direction * btc_ret_bp
    else:
        excess_bp = ret_bp
    p_hit = float(confidence) if confidence is not None else 0.5
    p_hit = min(1.0, max(0.0, p_hit))
    brier = (p_hit - hit) ** 2
    return ret_bp, btc_ret_bp, excess_bp, hit, brier


def score_due(limit: int = 500) -> Dict[str, Any]:
    """到期评分：signal_ledger 与 agent_predictions 里 status=open 且 expires_ms ≤ now 的行。

    返回统计 {"signals_scored", "signals_void", "predictions_scored", "predictions_void", "pending"}。
    由定时任务 analysis_ledger_scoring 每 15 分钟调用。

    [2026-09-07] 两段式：先读到期行并释放事务 → 全部 price_at 算完 → 再批量 UPDATE。
    旧实现边 UPDATE 边查价，未提交写事务挂着 dig market 库可达数十秒，
    LeakGuard 点名 score_due idle-in-transaction。
    """
    ensure_schema()
    from sqlalchemy import text
    from backend.database.connection import release_idle_txn

    now = now_ms()
    stats = {"signals_scored": 0, "signals_void": 0, "predictions_scored": 0, "predictions_void": 0, "pending": 0}
    db = _db()
    try:
        rows = [
            dict(r)
            for r in db.execute(
                text(
                    "SELECT id, symbol, direction, confidence, created_ms, expires_ms, entry_price FROM signal_ledger "
                    "WHERE status = 'open' AND expires_ms <= :now ORDER BY expires_ms LIMIT :lim"
                ),
                {"now": now, "lim": int(limit)},
            ).mappings().all()
        ]
        release_idle_txn(db, where="score_due.signals_pre_price")

        btc_cache: Dict[int, Optional[float]] = {}

        def btc_at(ts: int) -> Optional[float]:
            key = ts // 60000
            if key not in btc_cache:
                btc_cache[key] = price_at("BTC", ts)
            return btc_cache[key]

        signal_updates: List[Dict[str, Any]] = []
        signal_voids: List[Any] = []
        for r in rows:
            p0 = r["entry_price"] or price_at(r["symbol"], r["created_ms"])
            p1 = price_at(r["symbol"], r["expires_ms"])
            if not p0 or not p1:
                if now - r["expires_ms"] > VOID_AFTER_MS:
                    signal_voids.append(r["id"])
                else:
                    stats["pending"] += 1
                continue
            ret_bp, btc_ret_bp, excess_bp, hit, brier = _score_direction(
                int(r["direction"]), p0, p1, btc_at(r["created_ms"]), btc_at(r["expires_ms"]),
                r["confidence"], r["symbol"],
            )
            signal_updates.append({
                "now": now, "p0": p0, "p1": p1, "ret": ret_bp, "bret": btc_ret_bp,
                "ex": excess_bp, "hit": hit, "brier": brier, "id": r["id"],
            })

        for vid in signal_voids:
            db.execute(
                text("UPDATE signal_ledger SET status='void', scored_ms=:now WHERE id=:id"),
                {"now": now, "id": vid},
            )
            stats["signals_void"] += 1
        for u in signal_updates:
            db.execute(
                text(
                    "UPDATE signal_ledger SET status='scored', scored_ms=:now, entry_price=:p0, exit_price=:p1, ret_bp=:ret, "
                    "btc_ret_bp=:bret, excess_bp=:ex, hit=:hit, brier=:brier WHERE id=:id"
                ),
                u,
            )
            stats["signals_scored"] += 1
        if signal_voids or signal_updates:
            db.commit()
        else:
            release_idle_txn(db, where="score_due.signals_noop")

        preds = [
            dict(r)
            for r in db.execute(
                text(
                    "SELECT id, agent, kind, subject, prediction, confidence, created_ms, expires_ms FROM agent_predictions "
                    "WHERE status = 'open' AND expires_ms <= :now ORDER BY expires_ms LIMIT :lim"
                ),
                {"now": now, "lim": int(limit)},
            ).mappings().all()
        ]
        release_idle_txn(db, where="score_due.preds_pre_eval")

        pred_updates: List[Dict[str, Any]] = []
        pred_voids: List[Any] = []
        for r in preds:
            pred = _loads(r["prediction"]) or {}
            res: Optional[Dict[str, Any]] = None
            if r["kind"] == "direction":
                sym = pred.get("symbol") or r["subject"]
                direction = int(pred.get("direction", 0) or 0)
                p0 = pred.get("entry_price") or price_at(sym, r["created_ms"])
                p1 = price_at(sym, r["expires_ms"])
                if p0 and p1:
                    ret_bp, btc_ret_bp, excess_bp, hit, brier = _score_direction(
                        direction, p0, p1, btc_at(r["created_ms"]), btc_at(r["expires_ms"]), r["confidence"]
                    )
                    res = {"score": float(hit), "brier": brier, "outcome": {"ret_bp": ret_bp, "btc_ret_bp": btc_ret_bp, "excess_bp": excess_bp, "p0": p0, "p1": p1}}
            else:
                fn = _OUTCOME_EVALUATORS.get(r["kind"])
                if fn is not None:
                    try:
                        res = fn(dict(r, prediction=pred))
                    except Exception as exc:
                        logger.warning("[agent_predictions] 评估器异常 kind=%s: %s", r["kind"], exc)
                        res = None
            if res is None:
                if now - r["expires_ms"] > VOID_AFTER_MS:
                    pred_voids.append(r["id"])
                else:
                    stats["pending"] += 1
                continue
            score = float(res.get("score", 0.0))
            brier = res.get("brier")
            if brier is None:
                p = float(r["confidence"]) if r["confidence"] is not None else 0.5
                brier = (min(1.0, max(0.0, p)) - score) ** 2
            pred_updates.append({
                "now": now, "out": _json(res.get("outcome") or {}),
                "score": score, "brier": float(brier), "id": r["id"],
            })

        for vid in pred_voids:
            db.execute(
                text("UPDATE agent_predictions SET status='void', scored_ms=:now WHERE id=:id"),
                {"now": now, "id": vid},
            )
            stats["predictions_void"] += 1
        for u in pred_updates:
            db.execute(
                text(
                    "UPDATE agent_predictions SET status='scored', scored_ms=:now, outcome=CAST(:out AS JSONB), score=:score, brier=:brier WHERE id=:id"
                ),
                u,
            )
            stats["predictions_scored"] += 1
        if pred_voids or pred_updates:
            db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning("[analysis.ledgers] score_due 失败: %s", exc)
        stats["error"] = str(exc)
    finally:
        db.close()
    return stats
