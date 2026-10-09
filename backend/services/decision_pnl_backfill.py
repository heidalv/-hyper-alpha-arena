# -*- coding: utf-8 -*-
"""决策快照 pnl 回填（[2026-10-03 用户指令「继续」· 回填率 7.4% 的修复]。

## 实测问题
`decision_snapshots`：**executed=229 条，但只有 17 条回填了 pnl（7.4%）**，212 条待回填。
后果连锁两条链路：
  · 经验提炼/回放缓冲拿不到真实盈亏（`replay/stats` 长期 300/302 是 synthetic）；
  · 决策质量标签、归因、RL 训练样本量全部被卡住。

## 根因（实测排除法）
· 持有时长不是主因：近 90 天 403 笔已平仓里 **0 笔持有 >48h**（243 笔 <2h、160 笔 2-48h），
  而原窗口是 `now-48h`；
· 真因是**匹配候选歧义后主动跳过**（引擎口径"宁缺勿错"）：同一 symbol 同方向在一段时间内
  有多条未回填快照时，最近邻领先不足 60s 即放弃。
  ⇒ 引擎侧已改为**以 `opened_at` 为中心 ±30min 的开仓窗口**（候选更少、归因更准）。

## 本模块（一次性修复 + 可复用）
按**同一安全规则**（唯一匹配才写）把历史缺口补上：对每条 `pnl IS NULL` 的 executed 快照，
在 `paper_positions`（已平仓）里找 **symbol 相同 + 方向一致 + opened_at 落在 ±30min** 的持仓：
  · 恰好 1 笔 → 回填 `pnl` / `pnl_pct`（并记录 `quality_label` 与来源）；
  · 0 笔或多笔 → **跳过并计数**（绝不猜）。
幂等：只处理 `pnl IS NULL` 的行；可重复调用。
"""
from __future__ import annotations

import os

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

WINDOW_MIN = 30
WIDE_WINDOW_MIN = 360  # 二次尝试：部分流程的快照写于成交后数小时（实测 ETH 差 173min）


def _as_aware(dt) -> Optional[datetime]:
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def backfill_decision_pnl(days: int = 90, limit: int = 2000, dry_run: bool = False) -> Dict[str, Any]:
    """把已平仓持仓的真实盈亏回填到对应的决策快照（唯一匹配才写）。"""
    from backend.database.connection import AnalyticsSessionLocal, SessionLocal
    from backend.database.models import DecisionSnapshot
    from sqlalchemy import text

    since = datetime.now(timezone.utc) - timedelta(days=int(days))

    # [2026-10-03] 主库 RLS 是 FORCE 的：不注入 system identity 时 `paper_positions`
    # 只返回当前租户可见行（实测 403 笔已平仓被过滤成 115 笔，直接导致 204 条
    # 快照"找不到对应持仓"）。这里显式注入，与其它后台服务一致。
    try:
        from backend.core.tenant import set_system_identity

        set_system_identity()
    except Exception as _tid_err:  # 注入失败时仍继续（可能已由上层设置）
        logger.debug("[PnLBackfill] system identity 注入跳过: %s", _tid_err)

    # ── 读已平仓持仓（含真实盈亏）──
    main = SessionLocal()
    positions: List[Dict[str, Any]] = []
    try:
        rows = main.execute(text("""
            SELECT p.id, p.symbol, p.side, p.opened_at, p.closed_at, p.margin,
                   p.unrealized_pnl, p.close_reason, p.entry_price, p.close_price,
                   a.name AS account_name
            FROM paper_positions p
            LEFT JOIN accounts a ON a.id = p.account_id
            WHERE p.status = 'closed' AND p.opened_at IS NOT NULL AND p.closed_at IS NOT NULL
              AND p.opened_at >= :since
              -- [2026-10-03] 排除测试/孵化账户与价格错位行（实测：#4860 是测试克隆仓，
              -- entry 118.97 → close 2673.38，被宽窗口同时匹配给 30 条快照 ⇒ 污染 30 行）
              AND coalesce(a.name, '') NOT LIKE '\_audit%'
              AND coalesce(a.name, '') NOT LIKE '\_deleted%'
              AND coalesce(a.name, '') <> 'Hermes 孵化器'
            ORDER BY p.opened_at DESC
        """), {"since": since}).fetchall()
        positions = [dict(r._mapping) for r in rows]
        # 价格合理性：单笔交易不应出现 >2x 的价格位移（否则视为错位/克隆行，不参与归因）
        _before = len(positions)
        positions = [
            p for p in positions
            if not (p.get("entry_price") and p.get("close_price"))
            or 0.5 <= (float(p["close_price"]) / float(p["entry_price"])) <= 2.0
        ]
        logger.info("[PnLBackfill] 持仓过滤: %d → %d（剔除价格错位 %d）",
                    _before, len(positions), _before - len(positions))
    except Exception as e:
        logger.warning("[PnLBackfill] 读取持仓失败: %s", e)
        return {"error": str(e)[:200]}
    finally:
        main.close()

    adb = AnalyticsSessionLocal()
    stats = {"scanned": 0, "filled": 0, "ambiguous": 0, "no_position": 0, "positions": len(positions)}
    try:
        cands = adb.query(DecisionSnapshot).filter(
            DecisionSnapshot.executed.is_(True),
            DecisionSnapshot.pnl.is_(None),
            DecisionSnapshot.timestamp >= since,
        ).order_by(DecisionSnapshot.timestamp.asc()).limit(int(limit)).all()
        stats["scanned"] = len(cands)
        # [2026-10-03 严重修复 · 多对一泄漏] 原实现只保证"**每条快照**恰好一个持仓"，
        # 却没保证"**一个持仓只被用一次**"：实测同一个测试持仓（pnl 4522.944853309662、
        # 持仓 116842s）被 ±6h 宽窗口**同时匹配给 30 条快照**，把同一笔盈亏重复写进 30 条决策，
        # 直接污染学习数据。现在显式跟踪已消费的持仓 id（同一次运行内一仓只记一次）。
        _used_pos_ids: set = set()

        for snap in cands:
            ts = _as_aware(getattr(snap, "timestamp", None))
            if ts is None:
                stats["no_position"] += 1
                continue
            want = "long" if str(getattr(snap, "direction", "") or "").lower() in ("buy", "long") else "short"
            sym_up = str(getattr(snap, "symbol", "") or "").upper()
            # 两段窗口：±30min 严匹配；无果再 ±6h（两段都要求唯一匹配 —— 宁缺勿错）
            matches: List[Dict[str, Any]] = []
            for _win in (WINDOW_MIN, WIDE_WINDOW_MIN):
                matches = [
                    pos for pos in positions
                    if str(pos.get("symbol") or "").upper() == sym_up
                    and str(pos.get("side") or "").lower() == want
                    and _as_aware(pos.get("opened_at")) is not None
                    and abs((_as_aware(pos.get("opened_at")) - ts).total_seconds()) <= _win * 60
                ]
                if matches:
                    break
            if len(matches) != 1:
                stats["ambiguous" if matches else "no_position"] += 1
                continue
            if int(matches[0].get("id") or 0) in _used_pos_ids:
                stats["position_reused"] = stats.get("position_reused", 0) + 1
                continue
            _used_pos_ids.add(int(matches[0].get("id") or 0))

            pos = matches[0]
            pnl = float(pos.get("unrealized_pnl") or 0)
            margin = float(pos.get("margin") or 0)
            # [2026-10-03] `pnl_pct = pnl/margin` 在保证金极小时会爆表（实测 47 条真实样本里
            # **30 条 |pnl_pct| > 20**，最高上千）。**pnl（真实金额）照写**，
            # 但畸变的比例**不写**（置 None）并计数，避免污染 RL 奖励与质量标签。
            pnl_pct = (pnl / margin) if margin > 0 else None
            implausible = pnl_pct is not None and abs(pnl_pct) > float(
                os.getenv("PNL_PCT_MAX_ABS", "20") or 20
            )
            if implausible:
                pnl_pct = None
                stats["implausible_pct"] = stats.get("implausible_pct", 0) + 1
            if not dry_run:
                snap.pnl = pnl
                snap.pnl_pct = round(pnl_pct, 6) if pnl_pct is not None else None
                if hasattr(snap, "duration_seconds"):
                    oa, ca = _as_aware(pos.get("opened_at")), _as_aware(pos.get("closed_at"))
                    if oa and ca:
                        snap.duration_seconds = int((ca - oa).total_seconds())
                if hasattr(snap, "quality_label") and not getattr(snap, "quality_label", None):
                    snap.quality_label = "win" if pnl > 0 else ("loss" if pnl < 0 else "flat")
            stats["filled"] += 1

        if not dry_run and stats["filled"]:
            adb.commit()
    except Exception as e:
        adb.rollback()
        logger.warning("[PnLBackfill] 回填失败: %s", e)
        stats["error"] = str(e)[:200]
    finally:
        adb.close()

    stats["dry_run"] = bool(dry_run)
    logger.info("[PnLBackfill] %s", stats)
    return stats

