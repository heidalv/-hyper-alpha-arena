"""情景记忆引擎（2026-09-07 · 海马体式 Episodic Memory）。

设计映射（海马体四机制）：
- 情景记忆：每次论题刷新存一个带市场指纹的情景（snapshot_episode）
- 模式完成：主脑写新论题前，按当前指纹检索相似历史情景+结局（retrieve_similar）
- 记忆巩固：平仓回填结局（backfill_outcome）+ 每日睡眠巩固蒸馏（consolidate_daily）
- 遗忘：检索按时间衰减，旧情景权重低

纯增量：新表 mlto_episodes（create_all 自动建），失败静默不影响主链路。
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ──────────────────────────────────────────────────────────────────
# 市场指纹
# ──────────────────────────────────────────────────────────────────

def market_fingerprint(
    symbol: str,
    market_summary: Optional[Dict[str, Any]] = None,
    *,
    tier: str = "mid",
) -> Dict[str, Any]:
    """计算当前市场指纹（纯规则，无前视）。

    字段：regime（趋势/震荡/极端）· vol_bucket（波动档）· trend_dir（1h 方向）·
    dist_inv_pct（现价距失效价%，衡量价格位置）。全部可从 market_summary 廉价获得。
    """
    ms = (market_summary or {}).get(str(symbol or "").upper()) or {}
    if not isinstance(ms, dict):
        ms = {}

    # regime：编排器 market_cycle / regime 字段优先；缺失时从 波动+趋势 推断
    # （中长线循环的 market_summary 不填 market_cycle，实测线上全是 unknown——
    # 回退到 vol/trend 推断，别让指纹少一个区分维度）
    regime = str(
        ms.get("market_cycle") or ms.get("regime") or ""
    ).strip().lower()
    if regime not in ("trending", "ranging", "extreme", "breakout"):
        regime = ""  # 待推断
    if not regime:
        orch = ms.get("orchestrator") if isinstance(ms.get("orchestrator"), dict) else {}
        regime = str(orch.get("regime") or orch.get("market_regime") or "").strip().lower()
    if regime not in ("trending", "ranging", "extreme", "breakout"):
        regime = ""

    # 波动档：volatility_value（小数或百分数归一）
    vol = 0.0
    for k in ("volatility_value", "volatility_pct"):
        try:
            v = ms.get(k)
            if v is not None:
                vol = float(v)
                break
        except (TypeError, ValueError):
            continue
    if vol > 1:
        vol = vol / 100.0
    if vol < 0.008:
        vol_bucket = "low"
    elif vol < 0.015:
        vol_bucket = "mid"
    else:
        vol_bucket = "high"

    # 趋势方向：1h 涨跌或编排器 mid_bias
    trend_dir = "flat"
    chg = 0.0
    for k in ("price_change_1h", "price_change_1h_pct", "change_1h"):
        try:
            v = ms.get(k)
            if v is not None:
                chg = float(v)
                break
        except (TypeError, ValueError):
            continue
    if abs(chg) > 1:
        chg = chg / 100.0
    if chg >= 0.005:
        trend_dir = "up"
    elif chg <= -0.005:
        trend_dir = "down"
    else:
        # 回退编排器 mid_bias
        orch = ms.get("orchestrator") if isinstance(ms.get("orchestrator"), dict) else {}
        mb = str(orch.get("mid_bias") or "").lower()
        if mb == "bullish":
            trend_dir = "up"
        elif mb == "bearish":
            trend_dir = "down"

    # regime 最终回退：波动+趋势推断（高波动强趋势=trending，低波动盘整=ranging）
    if not regime:
        if vol_bucket == "high" and trend_dir in ("up", "down"):
            regime = "trending"
        elif vol_bucket == "low" and trend_dir == "flat":
            regime = "ranging"
        elif trend_dir in ("up", "down"):
            regime = "trending"
        else:
            regime = "ranging"

    return {
        "regime": regime,
        "vol_bucket": vol_bucket,
        "vol_pct": round(vol * 100, 2),
        "trend_dir": trend_dir,
        "tier": tier,
    }


# ──────────────────────────────────────────────────────────────────
# 写入情景（论题刷新时快照）
# ──────────────────────────────────────────────────────────────────

def snapshot_episode(
    *,
    session_id: str,
    symbol: str,
    tier: str,
    thesis_id: Optional[str],
    direction: str,
    accepted: bool,
    recommend_open: bool,
    market_summary: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """论题刷新后快照一个情景。返回 episode_id；失败静默返回 None。"""
    try:
        from backend.database.connection import AnalyticsSessionLocal
        from backend.services.mlto.db_models import MltoEpisode

        fp = market_fingerprint(symbol, market_summary, tier=tier)
        ep_id = "ep_" + uuid.uuid4().hex[:16]
        with AnalyticsSessionLocal() as db:
            db.add(MltoEpisode(
                episode_id=ep_id,
                thesis_id=thesis_id,
                session_id=str(session_id or ""),
                symbol=str(symbol or "").upper(),
                tier=str(tier or "mid"),
                direction=str(direction or "neutral"),
                fingerprint_json=json.dumps(fp, ensure_ascii=False),
                accepted=1 if accepted else 0,
                recommend_open=1 if recommend_open else 0,
                opened=0,
            ))
            db.commit()
        return ep_id
    except Exception as exc:
        logger.debug("[Episodic] snapshot 失败 %s %s: %s", symbol, tier, exc)
        return None


# ──────────────────────────────────────────────────────────────────
# 结局回填（平仓时）
# ──────────────────────────────────────────────────────────────────

def backfill_outcome(
    *,
    session_id: str,
    symbol: str,
    tier: str,
    pnl: float,
    pct: float,
    hold_hours: float,
    close_reason: str,
) -> bool:
    """平仓后回填最近一个「已接受但未结局」的情景。失败静默返回 False。"""
    try:
        from backend.database.connection import AnalyticsSessionLocal
        from backend.services.mlto.db_models import MltoEpisode

        with AnalyticsSessionLocal() as db:
            row = (
                db.query(MltoEpisode)
                .filter(
                    MltoEpisode.session_id == str(session_id or ""),
                    MltoEpisode.symbol == str(symbol or "").upper(),
                    MltoEpisode.tier == str(tier or "mid"),
                    MltoEpisode.outcome_ts.is_(None),
                )
                .order_by(MltoEpisode.created_at.desc())
                .first()
            )
            if row is None:
                return False
            row.opened = 1
            row.outcome_pnl = round(float(pnl), 4)
            row.outcome_pct = round(float(pct), 4)
            row.outcome_hold_hours = round(float(hold_hours), 2)
            row.outcome_close_reason = str(close_reason or "")[:100]
            row.outcome_ts = _utcnow()
            db.commit()
        return True
    except Exception as exc:
        logger.debug("[Episodic] backfill 失败 %s %s: %s", symbol, tier, exc)
        return False


# ──────────────────────────────────────────────────────────────────
# 相似情景检索（主脑写论题前）
# ──────────────────────────────────────────────────────────────────

def _fp_similarity(a: Dict[str, Any], b: Dict[str, Any]) -> int:
    """指纹相似度：regime/vol_bucket/trend_dir 各 1 分，满分 3。"""
    score = 0
    for k in ("regime", "vol_bucket", "trend_dir"):
        if a.get(k) and a.get(k) == b.get(k):
            score += 1
    return score


def retrieve_similar(
    *,
    session_id: str,
    symbol: str,
    tier: str,
    market_summary: Optional[Dict[str, Any]] = None,
    k: int = 5,
    days: int = 14,
) -> List[Dict[str, Any]]:
    """检索与当前指纹相似的历史情景（带结局）。按 相似度+时间衰减 排序。"""
    try:
        from backend.database.connection import AnalyticsSessionLocal
        from backend.services.mlto.db_models import MltoEpisode

        cur_fp = market_fingerprint(symbol, market_summary, tier=tier)
        with AnalyticsSessionLocal() as db:
            rows = (
                db.query(MltoEpisode)
                .filter(
                    MltoEpisode.symbol == str(symbol or "").upper(),
                    MltoEpisode.tier == str(tier or "mid"),
                )
                .order_by(MltoEpisode.created_at.desc())
                .limit(200)
                .all()
            )
        now = _utcnow()
        scored = []
        for r in rows:
            try:
                fp = json.loads(r.fingerprint_json or "{}")
            except Exception:
                fp = {}
            sim = _fp_similarity(cur_fp, fp)
            if sim <= 0:
                continue
            # 时间衰减：每天 -5%（海马体遗忘曲线）
            age_days = 0.0
            try:
                created = r.created_at
                if created is not None:
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=timezone.utc)
                    age_days = max(0.0, (now - created).total_seconds() / 86400.0)
            except Exception:
                age_days = 0.0
            decay = 0.95 ** age_days
            scored.append((sim * decay, sim, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        out = []
        for _score, sim, r in scored[:k]:
            out.append({
                "symbol": r.symbol,
                "direction": r.direction,
                "regime": (json.loads(r.fingerprint_json or "{}")).get("regime"),
                "vol_bucket": (json.loads(r.fingerprint_json or "{}")).get("vol_bucket"),
                "similarity": sim,
                "opened": bool(r.opened),
                "outcome_pct": r.outcome_pct,
                "outcome_close_reason": r.outcome_close_reason,
                "age_hours": round(age_days * 24, 1) if False else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            })
        return out
    except Exception as exc:
        logger.debug("[Episodic] retrieve 失败 %s %s: %s", symbol, tier, exc)
        return []


def format_similar_for_feed(episodes: List[Dict[str, Any]]) -> str:
    """把相似情景格式化成主脑 feed 的紧凑文本。"""
    if not episodes:
        return ""
    lines = []
    for ep in episodes:
        if not ep.get("opened"):
            outcome = "未开仓"
        elif ep.get("outcome_pct") is None:
            outcome = "持仓中"
        else:
            pct = float(ep.get("outcome_pct") or 0)
            outcome = f"{'+' if pct >= 0 else ''}{pct:.1f}%（{ep.get('outcome_close_reason') or '—'}）"
        lines.append(
            f"{ep.get('regime')}/{ep.get('vol_bucket')}波动 {ep.get('direction')} → {outcome}"
        )
    return "；".join(lines)


# ──────────────────────────────────────────────────────────────────
# 每日睡眠巩固（海马体→新皮层：短期经历蒸馏成长期规则）
# ──────────────────────────────────────────────────────────────────

_CONSOLIDATION_PATH = "backend/data/analysis/latest_episodic_consolidation.json"


def consolidate_daily(*, days: int = 7, min_samples: int = 3) -> Dict[str, Any]:
    """每日睡眠巩固：回放近 N 天带结局的情景，按指纹分组蒸馏出规则。

    产出写入 latest_episodic_consolidation.json，主脑 feed 经
    read_consolidated_lessons() 注入。失败静默返回 error。
    """
    try:
        from backend.database.connection import AnalyticsSessionLocal
        from backend.services.mlto.db_models import MltoEpisode

        with AnalyticsSessionLocal() as db:
            rows = (
                db.query(MltoEpisode)
                .filter(MltoEpisode.opened == 1, MltoEpisode.outcome_pct.isnot(None))
                .order_by(MltoEpisode.created_at.desc())
                .limit(2000)
                .all()
            )
        # 按 (tier, regime, vol_bucket, direction) 分组统计
        groups: Dict[tuple, List[float]] = {}
        for r in rows:
            try:
                fp = json.loads(r.fingerprint_json or "{}")
            except Exception:
                fp = {}
            key = (
                str(r.tier or "mid"),
                str(fp.get("regime") or "unknown"),
                str(fp.get("vol_bucket") or "unknown"),
                str(r.direction or "neutral"),
            )
            groups.setdefault(key, []).append(float(r.outcome_pct or 0))

        rules = []
        for (tier, regime, vol, direction), pcts in sorted(
            groups.items(), key=lambda kv: -len(kv[1])
        ):
            n = len(pcts)
            if n < min_samples:
                continue
            wins = sum(1 for p in pcts if p > 0)
            avg = sum(pcts) / n
            rules.append({
                "tier": tier, "regime": regime, "vol_bucket": vol, "direction": direction,
                "n": n, "win_rate": round(wins / n, 3), "avg_pct": round(avg, 2),
            })

        # 蒸馏成中文规则文本（主脑可直接读）
        tier_cn = {"short": "日内", "mid": "中线", "long": "长线"}
        regime_cn = {"trending": "趋势市", "ranging": "震荡市", "extreme": "极端市", "unknown": "未知市"}
        vol_cn = {"low": "低波动", "mid": "中波动", "high": "高波动"}
        dir_cn = {"long": "做多", "short": "做空", "neutral": "中性"}
        lines = []
        for r in rules[:15]:
            lines.append(
                f"{tier_cn.get(r['tier'], r['tier'])}·{regime_cn.get(r['regime'], r['regime'])}·"
                f"{vol_cn.get(r['vol_bucket'], r['vol_bucket'])}·{dir_cn.get(r['direction'], r['direction'])}："
                f"{r['n']} 笔 胜率{r['win_rate']*100:.0f}% 均盈{r['avg_pct']:+.2f}%"
            )
        out = {
            "generated_at": _utcnow().isoformat(),
            "days": days,
            "rules": rules,
            "lessons_text": "\n".join(lines),
            "total_episodes": len(rows),
        }
        import os
        os.makedirs(os.path.dirname(_CONSOLIDATION_PATH), exist_ok=True)
        with open(_CONSOLIDATION_PATH, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        logger.info(
            "[Episodic] 睡眠巩固完成: %d 情景 → %d 条规则", len(rows), len(rules),
        )
        return out
    except Exception as exc:
        logger.warning("[Episodic] 睡眠巩固失败: %s", exc)
        return {"error": str(exc)[:200]}


def read_consolidated_lessons() -> str:
    """读最近一次睡眠巩固的蒸馏规则（主脑 feed 注入用）。失败返回空。"""
    try:
        import os
        if not os.path.exists(_CONSOLIDATION_PATH):
            return ""
        with open(_CONSOLIDATION_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        return str(d.get("lessons_text") or "")[:800]
    except Exception:
        return ""
