# -*- coding: utf-8 -*-
"""证据包构建 —— 通道B的输入（evidence_refs 全锚定，仅当日可得信息）。

组件（设计 §3.2）：
    - factor_exposure_summary：通道A特征摘要（top 正/负贡献 z）
    - thesis_signal：ai_coin_unified 状态层（short/mid 档 = 分析师观点的选币表达）
    - hit_stats：本中心 score_log 的滚动命中统计（记忆库该类信号历史准确率的本地版）
    - news_digest：v1 预留（None，不阻塞）
token 纪律：to_prompt 序列化后 ≤4000 字符。
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

from backend.services.hybrid_scoring import config
from backend.services.hybrid_scoring.features import FEATURES

logger = logging.getLogger(__name__)

# [F320 2026-09-18 复查修复] 原指向 <hybrid_scoring>/data/ai_coin_unified —— **该目录不存在**
# ⇒ _thesis_map() 的 glob 恒空（且异常静默）⇒ thesis tier 永远进不了混合打分 ⇒ 流B 的
# A1 通道（tier→0.9/0.8/0.6）运行时零产出（实测 score_log 中 A1=0 条、ic_stats per_arm_days.A1=0）。
# 真目录 = backend/services/data/ai_coin_unified（实测存在，含 5 个 json），与 ai_coin_unified.py 同源。
_UNIFIED_DIR = Path(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "ai_coin_unified"))

_MAX_PROMPT_CHARS = 4000


def _thesis_map() -> Dict[str, Dict[str, object]]:
    """读 ai_coin_unified 状态文件 → {sym: {tier, reason, age_hours}}。只读，异常静默。"""
    out: Dict[str, Dict[str, object]] = {}
    try:
        for fp in list(_UNIFIED_DIR.glob("*.json"))[:50]:
            try:
                d = json.loads(fp.read_text(encoding="utf-8"))
            except Exception:
                continue
            for tier in ("short", "mid", "long"):
                seg = d.get(tier) or {}
                for sym in seg.get("symbols") or []:
                    ts = float(seg.get("updated_at") or 0)
                    out[str(sym).upper()] = {
                        "tier": tier,
                        "reason": str(seg.get("reason") or "")[:80],
                        "age_hours": round((time.time() - ts) / 3600.0, 1) if ts else None,
                        "session": d.get("session_id"),
                    }
    except Exception as e:  # noqa: BLE001
        logger.debug("[HybridScore.evidence] thesis 读取失败: %s", str(e)[:100])
    return out


def _hit_stats() -> Dict[str, Dict[str, float]]:
    """本中心 score_log 滚动命中（ret_24h>0 占比）与均值，按币。"""
    stats: Dict[str, Dict[str, float]] = {}
    try:
        path = config.score_log_path()
        if not path.exists():
            return stats
        for line in path.read_text(encoding="utf-8").splitlines()[-2000:]:
            try:
                e = json.loads(line)
            except Exception:
                continue
            o = e.get("outcome") or {}
            if o.get("ret_24h") is None:
                continue
            s = stats.setdefault(e.get("symbol"), {"n": 0, "hit": 0, "sum_ret": 0.0})
            s["n"] += 1
            s["hit"] += 1 if float(o["ret_24h"]) > 0 else 0
            s["sum_ret"] += float(o["ret_24h"])
        for s in stats.values():
            s["hit_rate"] = round(s["hit"] / s["n"], 3) if s["n"] else 0.0
    except Exception as e:  # noqa: BLE001
        logger.debug("[HybridScore.evidence] hit 统计失败: %s", str(e)[:100])
    return stats


def _news_digest() -> Dict[str, Dict[str, object]]:
    """[2026-09-18 流B补全] news_events 近24h 按币聚合（标题+方向+强度，≤3条/币）。"""
    out: Dict[str, Dict[str, object]] = {}
    try:
        url = None
        from pathlib import Path as _P
        envf = _P(__file__).resolve().parents[3] / ".env"
        for l in envf.read_text(encoding="utf-8", errors="ignore").splitlines():
            if l.strip().startswith("MARKET_DATABASE_URL"):
                url = l.split("=", 1)[1].strip().strip('"').replace("postgresql+psycopg://", "postgresql://", 1)
        if not url:
            return out
        import psycopg
        with psycopg.connect(url, connect_timeout=8) as con:
            cur = con.cursor()
            cur.execute("SET statement_timeout='15000'")
            rows = cur.execute(
                "SELECT affected_symbols, title, impact_direction, impact_strength, published_at "
                "FROM news_events WHERE published_at > NOW() - INTERVAL '24 hours' "
                "ORDER BY published_at DESC LIMIT 200").fetchall()
        for syms, title, direction, strength, pub in rows:
            if not isinstance(syms, list):
                continue
            for s in syms:
                sym = str(s).upper().strip()
                if not sym:
                    continue
                bucket = out.setdefault(sym, {"n": 0, "items": []})
                bucket["n"] += 1
                if len(bucket["items"]) < 3:
                    bucket["items"].append({
                        "t": str(title)[:60], "dir": str(direction or "?")[:8],
                        "s": float(strength or 0), "at": str(pub)[:16]})
    except Exception:
        pass
    return out


def build_packs(channel_a_rows: Dict[str, Dict[str, object]]) -> Dict[str, Dict[str, object]]:
    thesis = _thesis_map()
    hits = _hit_stats()
    news = _news_digest()
    packs: Dict[str, Dict[str, object]] = {}
    for sym, row in channel_a_rows.items():
        feats = row.get("features") or {}
        top_pos = sorted(((c, float(feats.get(c) or 0)) for c in FEATURES),
                         key=lambda kv: -kv[1])[:3]
        top_neg = sorted(((c, float(feats.get(c) or 0)) for c in FEATURES),
                         key=lambda kv: kv[1])[:2]
        th = thesis.get(sym)
        hs = hits.get(sym)
        packs[sym] = {
            "symbol": sym,
            "factor_exposure_summary": {
                "channel_a_score": round(float(row.get("score") or 0), 3),
                "channel_a_rank": row.get("rank"),
                "top_positive": [[c, round(v, 2)] for c, v in top_pos if v > 0],
                "top_negative": [[c, round(v, 2)] for c, v in top_neg if v < 0],
            },
            "thesis_signal": th or {"present": False},
            "hit_stats": hs or {"n": 0},
            "news_digest": news.get(sym) or {"n": 0},
            "evidence_refs": {
                "kline": f"crypto_klines(period={config.PANEL_PERIOD})",
                "thesis": "ai_coin_unified/<session>.json",
                "hit": "hybrid_scoring/score_log.jsonl",
            },
        }
    return packs


def to_prompt(packs: Dict[str, Dict[str, object]]) -> str:
    """证据包 → LLM 输入文本（≤_MAX_PROMPT_CHARS，超长截尾币种）。"""
    lines = []
    for sym, p in packs.items():
        lines.append(json.dumps(p, ensure_ascii=False, separators=(",", ":")))
    text = "\n".join(lines)
    while len(text) > _MAX_PROMPT_CHARS and len(lines) > 3:
        lines = lines[:-1]
        text = "\n".join(lines)
    return text
