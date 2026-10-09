# -*- coding: utf-8 -*-
"""Hermes L4 **独立孵化通道**（[2026-10-03 用户指令「继续」，补齐最后一段断链]）。

## 为什么需要它（实测证据）
1. `strategy_genesis_candidates` 里 **345 条 `paper_status='incubating'`**，但全库只有
   **12 个 `gen_*` 策略**存在，且 `account_id` **全部是 14（用户的模拟账户）** —— 其余策略
   已被归档，所以 `_get_paper_performance()` 永远返回 0 笔（`paper_trades` 全 0）。
2. 即便策略存在，`check_incubation_results()` 也只能**唤醒**策略；真正执行它们的是 FullAuto
   会话。用户的会话是停止的 ⇒ 候选永远攒不到 `MIN_PAPER_TRADES=30` 笔 ⇒ `validated`/`promoted`
   恒为 0（成熟度 L4=0）。
3. 结论：L4 缺的是**一条独立于用户会话的纸面通道**（设计缺陷）。而且孵化交易此前落在用户
   账户上，会污染用户自己的绩效账 —— 这也是缺陷。

## 本模块提供
* `ensure_incubator_account(db)`  幂等准备专用纸面账户（默认名 `Hermes 孵化器`）；
* `repair_candidate_strategies(db, limit)`  重建/改绑 incubating 候选的策略到孵化账户；
* `channel_status(db)`  真实状态（候选 / 有策略 / 活跃 / 账户 / 会话 / 阻塞原因）；
* `start_channel(db, limit)` / `stop_channel(db)`  起停**专用孵化会话**。

## 开关与回滚
* `HERMES_L4_INCUBATION_ENABLED`（**默认 false**）：不改它时 `start_channel` 只返回阻塞原因，
  不会自动开始纸面交易 —— 由前端「启动孵化通道」按钮显式开启。
* `HERMES_INCUBATOR_BALANCE`（默认 1000）：孵化账户初始资金。
* 回滚：停会话 + 删除该账户即可（候选状态不受影响）。
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

INCUBATOR_ACCOUNT_NAME = "Hermes 孵化器"
DEFAULT_INCUBATOR_BALANCE = float(os.getenv("HERMES_INCUBATOR_BALANCE", "1000"))


def _enabled() -> bool:
    return str(os.getenv("HERMES_L4_INCUBATION_ENABLED", "false")).strip().lower() in (
        "1", "true", "yes", "on",
    )


def _max_active() -> int:
    try:
        return max(1, int(os.getenv("HERMES_GENESIS_MAX_ACTIVE_INCUBATIONS", "12")))
    except Exception:
        return 12


def get_incubator_account(db: Session):
    """按名称取孵化账户（不存在返回 None）。"""
    from backend.database.models import Account

    return (
        db.query(Account)
        .filter(Account.name == INCUBATOR_ACCOUNT_NAME)
        .order_by(Account.id.asc())
        .first()
    )


def ensure_incubator_account(db: Session) -> Dict[str, Any]:
    """幂等准备专用孵化纸面账户（字段从既有 paper 账户克隆，避免漏必填列）。"""
    from backend.database.models import Account

    acc = get_incubator_account(db)
    created = False
    if acc is None:
        # 用既有 paper 账户做模板克隆非唯一字段（Account 必填列较多，逐个列举易漏）
        tpl = (
            db.query(Account)
            .filter(Account.trading_mode == "paper", Account.is_active == "true")
            .order_by(Account.id.asc())
            .first()
        )
        if tpl is None:
            return {"ok": False, "error": "没有可克隆的 paper 账户"}
        data = {
            c.name: getattr(tpl, c.name)
            for c in Account.__table__.columns
            if c.name not in ("id", "name", "created_at", "updated_at")
        }
        data["name"] = INCUBATOR_ACCOUNT_NAME
        data["trading_mode"] = "paper"
        data["account_type"] = getattr(tpl, "account_type", None) or "PAPER"
        data["is_active"] = "true"
        data["auto_trading_enabled"] = "true"
        acc = Account(**data)
        db.add(acc)
        db.commit()
        db.refresh(acc)
        created = True

    # 钱包初始化（幂等：已存在则跳过）
    try:
        from backend.database.models import PaperBalance
        from backend.services.paper_trading_engine import paper_engine

        if not db.query(PaperBalance).filter(PaperBalance.account_id == acc.id).first():
            paper_engine.initialize_account(db, int(acc.id), DEFAULT_INCUBATOR_BALANCE)
    except Exception as e:  # 初始化失败不阻断策略改绑
        logger.warning("[Hermes:L4] 孵化账户初始化失败: %s", e)

    return {"ok": True, "account_id": int(acc.id), "created": created, "name": acc.name}


def repair_candidate_strategies(db: Session, limit: Optional[int] = None) -> Dict[str, Any]:
    """把 incubating 候选的策略重建/改绑到孵化账户（只处理评分最高的 limit 个）。

    为什么需要：345 条 incubating 里绝大多数候选**根本没有策略行**（或已被归档），
    只唤醒不重建 ⇒ 永远 0 笔。这里对每个候选：
      · 有策略行 → 改绑孵化账户 + 置 active/auto_execute（不再借用用户账户）；
      · 无策略行 → 重建（复用 genesis engine 的 `_create_paper_strategy`，再改绑）。
    """
    from backend.services.hermes_db import hermes_fetchall
    from backend.database.models import AIStrategy
    from backend.services.hermes_strategy_genesis_engine import strategy_genesis

    acc_info = ensure_incubator_account(db)
    if not acc_info.get("ok"):
        return acc_info
    incubator_id = int(acc_info["account_id"])

    rows = hermes_fetchall(
        "SELECT * FROM strategy_genesis_candidates WHERE paper_status='incubating' "
        "ORDER BY viability_score DESC, created_at DESC LIMIT ?",
        (int(limit or _max_active()),),
    )

    repaired, created, rebound, failed = 0, 0, 0, 0
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for c in rows:
        try:
            sid = strategy_genesis._strategy_id_for_candidate(c)
            row = db.query(AIStrategy).filter(AIStrategy.strategy_id == sid).first()
            if row is None:
                import json as _json
                config = _json.loads(c.get("variant_config") or "{}")
                if not config:
                    failed += 1
                    continue
                if not strategy_genesis._create_paper_strategy(sid, config):
                    failed += 1
                    continue
                created += 1
                row = db.query(AIStrategy).filter(AIStrategy.strategy_id == sid).first()
            if row is None:
                failed += 1
                continue

            changed = False
            if int(row.account_id or 0) != incubator_id:
                row.account_id = incubator_id
                rebound += 1
                changed = True
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
            if genome.get("hermes_candidate_id") != c.get("id") or genome.get("incubator") is not True:
                genome.update({
                    "source": "hermes_genesis",
                    "paper_only": True,
                    "incubation": True,
                    "incubator": True,
                    "hermes_candidate_id": c.get("id"),
                })
                row.genome = genome
                changed = True
            if changed:
                repaired += 1
            db.commit()
        except Exception as e:
            db.rollback()
            failed += 1
            logger.warning("[Hermes:L4] 候选 %s 策略修复失败: %s", c.get("id"), e)

    return {"ok": True, "account_id": incubator_id, "candidates": len(rows or []),
            "repaired": repaired, "created": created, "rebound": rebound, "failed": failed}


def channel_status(db: Session) -> Dict[str, Any]:
    """孵化通道真实状态 + 阻塞原因（供界面显示，不猜）。"""
    from backend.database.models import AIStrategy, FullAutoSession
    from backend.services.hermes_db import hermes_fetchone

    acc = get_incubator_account(db)
    stat = hermes_fetchone(
        "SELECT count(*) AS incubating FROM strategy_genesis_candidates WHERE paper_status='incubating'"
    ) or {}
    incubating = int(stat.get("incubating") or 0)

    with_strategy, active = 0, 0
    if acc is not None:
        with_strategy = db.query(AIStrategy).filter(
            AIStrategy.account_id == acc.id, AIStrategy.strategy_id.like("gen\\_%", escape="\\")
        ).count()
        active = db.query(AIStrategy).filter(
            AIStrategy.account_id == acc.id, AIStrategy.status == "active",
            AIStrategy.strategy_id.like("gen\\_%", escape="\\"),
        ).count()

    session = None
    if acc is not None:
        session = (
            db.query(FullAutoSession)
            .filter(FullAutoSession.account_id == acc.id)
            .order_by(FullAutoSession.created_at.desc())
            .first()
        )

    running = bool(session is not None and str(session.status) in ("running", "defensive"))
    blocks: List[str] = []
    if not _enabled():
        blocks.append("总开关未开：HERMES_L4_INCUBATION_ENABLED=false（界面可一键开启）")
    if acc is None:
        blocks.append("孵化账户不存在（点「修复/改绑策略」会创建）")
    elif with_strategy == 0:
        blocks.append("孵化账户下还没有候选策略（点「修复/改绑策略」重建）")
    elif active == 0:
        blocks.append("候选策略都不在 active（点「修复/改绑策略」唤醒）")
    if not running:
        blocks.append("没有运行中的孵化会话 ⇒ 不会有 paper 成交，候选无法攒到 30 笔")

    return {
        "enabled": _enabled(),
        "incubating_candidates": incubating,
        "incubator_account_id": int(acc.id) if acc else None,
        "incubator_account_name": acc.name if acc else None,
        "strategies_bound": with_strategy,
        "strategies_active": active,
        "session_id": getattr(session, "session_id", None),
        "session_status": getattr(session, "status", None),
        "session_running": running,
        "min_paper_trades": 30,
        "blocking": blocks,
        "hint": "候选达标条件：paper 交易 ≥30 笔、胜率 ≥45%、单笔均盈 ≥$1",
    }


def start_channel(db: Session, limit: Optional[int] = None) -> Dict[str, Any]:
    """起一个**专用孵化会话**（纸面）。默认关闭时只返回阻塞原因，不擅自开始交易。"""
    if not _enabled():
        return {"ok": False, "error": "未开启：设置 HERMES_L4_INCUBATION_ENABLED=true 后重启，或用界面上的开关",
                "blocking": channel_status(db).get("blocking")}

    repair = repair_candidate_strategies(db, limit)
    if not repair.get("ok"):
        return repair

    from backend.database.models import AIStrategy, FullAutoSession
    from backend.services.full_auto_trading_service import full_auto_service

    acc_id = int(repair["account_id"])
    existing = (
        db.query(FullAutoSession)
        .filter(FullAutoSession.account_id == acc_id,
                FullAutoSession.status.in_(["running", "defensive", "paused"]))
        .first()
    )
    if existing:
        return {"ok": True, "already_running": True, "session_id": existing.session_id,
                "status": existing.status, "repair": repair}

    symbols = [r[0] for r in db.query(AIStrategy.target_symbols)
               .filter(AIStrategy.account_id == acc_id, AIStrategy.status == "active").all()
               if r[0]]
    flat: List[str] = []
    for s in symbols:
        for item in (s if isinstance(s, list) else [s]):
            t = str(item).strip().upper()
            if t and t not in flat:
                flat.append(t)
    flat = flat[:10] or ["BTC", "ETH"]

    res = full_auto_service.start_session(
        db, account_id=acc_id, symbols=flat, risk_level="moderate",
        trading_mode="paper", risk_mode="ai_dynamic", paper_account_id=acc_id,
        auto_coin_enabled=False,
    )
    return {"ok": bool(res.get("success", True)), "symbols": flat, "repair": repair, "start": res}


def stop_channel(db: Session) -> Dict[str, Any]:
    """停掉孵化会话（不影响候选状态）。"""
    from backend.database.models import FullAutoSession
    from backend.services.full_auto_trading_service import full_auto_service

    acc = get_incubator_account(db)
    if acc is None:
        return {"ok": True, "stopped": [], "note": "无孵化账户"}
    rows = db.query(FullAutoSession).filter(
        FullAutoSession.account_id == acc.id,
        FullAutoSession.status.in_(["running", "defensive", "paused"]),
    ).all()
    stopped = []
    for s in rows:
        try:
            full_auto_service.stop_session(db, s.session_id)
            stopped.append(s.session_id)
        except Exception as e:
            logger.warning("[Hermes:L4] 停会话 %s 失败: %s", s.session_id, e)
    return {"ok": True, "stopped": stopped, "account_id": int(acc.id)}
