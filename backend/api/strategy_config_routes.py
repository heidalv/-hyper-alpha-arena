# -*- coding: utf-8 -*-
"""
中长线策略配置 API — /api/strategy-config/{mid|long}

复用短线配置架构：GET 读取 + PUT 修改 + 预设 + EV模拟。
中线和长线参数定义不同，通过 tier 参数区分。
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/strategy-config", tags=["strategy-config"])

# ════════════════════════════════════════
# 中线(mid)参数定义
# ════════════════════════════════════════

_MID_PARAMS: Dict[str, Dict[str, Any]] = {
    "tp_pct":            {"env": "TIER_MID_TP_PCT",          "default": "0.07",  "type": "float", "min": 0.03,  "max": 0.15,  "label": "止盈(TP)",    "unit": "%"},
    "sl_pct":            {"env": "TIER_MID_SL_PCT",          "default": "0.035", "type": "float", "min": 0.015, "max": 0.08,  "label": "止损(SL)",    "unit": "%"},
    "min_sl_pct":        {"env": "TIER_MID_MIN_SL",          "default": "0.02",  "type": "float", "min": 0.01,  "max": 0.04,  "label": "SL下限",      "unit": "%"},
    "max_sl_pct":        {"env": "TIER_MID_MAX_SL",          "default": "0.05",  "type": "float", "min": 0.03,  "max": 0.10,  "label": "SL上限",      "unit": "%"},
    "min_tp_pct":        {"env": "TIER_MID_MIN_TP",          "default": "0.04",  "type": "float", "min": 0.02,  "max": 0.08,  "label": "TP下限",      "unit": "%"},
    "max_tp_pct":        {"env": "TIER_MID_MAX_TP",          "default": "0.09",  "type": "float", "min": 0.05,  "max": 0.20,  "label": "TP上限",      "unit": "%"},
    "paper_conf":        {"env": "SWING_PAPER_CONF",         "default": "48",    "type": "int",   "min": 30,    "max": 80,    "label": "Paper置信度", "unit": "分"},
    "live_conf":         {"env": "SWING_LIVE_CONF",          "default": "55",    "type": "int",   "min": 40,    "max": 90,    "label": "Live置信度",  "unit": "分"},
    "mtf_strong_conf":   {"env": "MIDLONG_MTF_STRONG_CONF",  "default": "0.7",   "type": "float", "min": 0.5,   "max": 1.0,   "label": "MTF强约束",   "unit": ""},
    "mtf_conflict_mult": {"env": "MIDLONG_MTF_CONFLICT_MULT","default": "0.6",   "type": "float", "min": 0.3,   "max": 1.0,   "label": "MTF冲突折扣", "unit": ""},
    "budget":            {"env": "TIER_MID_BUDGET",          "default": "0.35",  "type": "float", "min": 0.10,  "max": 0.60,  "label": "中线层预算",  "unit": "%"},
    "max_margin":        {"env": "TIER_MID_MAX_MARGIN",      "default": "0.35",  "type": "float", "min": 0.10,  "max": 0.60,  "label": "最大保证金",  "unit": "%"},
    "analysis_interval": {"env": "TIER_MID_ANALYSIS_INTERVAL","default": "600",  "type": "int",   "min": 120,   "max": 3600,  "label": "分析间隔",    "unit": "秒"},
    "ai_tick":           {"env": "TIER_MID_AI_TICK_SEC",     "default": "45",    "type": "int",   "min": 30,    "max": 300,   "label": "AI tick",     "unit": "秒"},
    # [2026-07-17] default/max 随 settings.py TIER_PROTECTION_PARAMS 同步上调
    # (30min → 12h，与 TIER_PROMPT_HINTS 对 AI 的承诺对齐)
    "min_hold":          {"env": "TIER_MID_MIN_HOLD_SEC",    "default": "43200", "type": "int",   "min": 300,   "max": 86400, "label": "最小持仓",    "unit": "秒"},
    "max_hold":          {"env": "TIER_MID_MAX_HOLD_SEC",    "default": "172800","type": "int",   "min": 3600,  "max": 604800,"label": "最大持仓",    "unit": "秒"},
    "scan_batch":        {"env": "MIDLONG_SCAN_BATCH",       "default": "3",     "type": "int",   "min": 1,     "max": 10,    "label": "每批扫描币数","unit": "个"},
    "active_exit":       {"env": "MIDLONG_ACTIVE_EXIT_ENABLED","default": "true","type": "bool",  "min": 0,     "max": 1,     "label": "主动退出",    "unit": ""},
    "exit_invalidate":   {"env": "MIDLONG_EXIT_INVALIDATE_CONF","default": "0.68","type":"float", "min": 0.5,   "max": 0.9,   "label": "反向置信度",  "unit": ""},
    "exit_min_hold":     {"env": "MIDLONG_EXIT_MIN_HOLD_SEC","default": "3600",  "type": "int",   "min": 600,   "max": 14400, "label": "退出保护期",  "unit": "秒"},
}

# ════════════════════════════════════════
# 长线(long)参数定义
# ════════════════════════════════════════

_LONG_PARAMS: Dict[str, Dict[str, Any]] = {
    "sl_pct":            {"env": "TIER_LONG_SL_PCT",         "default": "0.08",  "type": "float", "min": 0.04,  "max": 0.20,  "label": "初始止损(SL)", "unit": "%"},
    "sl_safety_net":     {"env": "TREND_SL_SAFETY_NET",      "default": "0.04",  "type": "float", "min": 0.02,  "max": 0.10,  "label": "SL安全网",     "unit": "%"},
    # D14 分批止盈
    "staged_tp_enabled": {"env": "RISK_USE_LONG_TIER_STAGED_TP","default":"true","type": "bool",  "min": 0,     "max": 1,     "label": "分批止盈",     "unit": ""},
    "tp1_trigger":       {"env": "LONG_TP1_TRIGGER",         "default": "0.08",  "type": "float", "min": 0.04,  "max": 0.15,  "label": "TP1触发",      "unit": "%"},
    "tp1_reduce":        {"env": "LONG_TP1_REDUCE",          "default": "0.30",  "type": "float", "min": 0.1,   "max": 0.5,   "label": "TP1减仓",      "unit": "%"},
    "tp2_trigger":       {"env": "LONG_TP2_TRIGGER",         "default": "0.15",  "type": "float", "min": 0.08,  "max": 0.25,  "label": "TP2触发",      "unit": "%"},
    "tp2_reduce":        {"env": "LONG_TP2_REDUCE",          "default": "0.30",  "type": "float", "min": 0.1,   "max": 0.5,   "label": "TP2减仓",      "unit": "%"},
    "tp3_trigger":       {"env": "LONG_TP3_TRIGGER",         "default": "0.25",  "type": "float", "min": 0.15,  "max": 0.40,  "label": "TP3触发",      "unit": "%"},
    "tp3_reduce":        {"env": "LONG_TP3_REDUCE",          "default": "0.30",  "type": "float", "min": 0.1,   "max": 0.5,   "label": "TP3减仓",      "unit": "%"},
    "trailing_atr_mult": {"env": "LONG_TRAILING_ATR_MULT",   "default": "2.0",   "type": "float", "min": 1.0,   "max": 4.0,   "label": "Trailing ATR", "unit": "x"},
    # 信号门禁
    "paper_min_score":   {"env": "PAPER_TREND_MIN_SCORE_TO_OPEN","default":"40","type": "int",   "min": 25,    "max": 70,    "label": "Paper最低分",  "unit": "分"},
    "live_min_score":    {"env": "TREND_MIN_SCORE_TO_OPEN",  "default": "50",    "type": "int",   "min": 35,    "max": 80,    "label": "Live最低分",   "unit": "分"},
    "max_opens_per_week":{"env": "TREND_MAX_OPENS_PER_WEEK", "default": "6",     "type": "int",   "min": 1,     "max": 20,    "label": "每周开单上限","unit": "个"},
    # 仓位与时间
    "budget":            {"env": "TIER_LONG_BUDGET",         "default": "0.40",  "type": "float", "min": 0.15,  "max": 0.70,  "label": "长线层预算",  "unit": "%"},
    "max_margin":        {"env": "TIER_LONG_MAX_MARGIN",     "default": "0.40",  "type": "float", "min": 0.15,  "max": 0.70,  "label": "最大保证金",  "unit": "%"},
    "analysis_interval": {"env": "TIER_LONG_ANALYSIS_INTERVAL","default":"1800", "type": "int",   "min": 600,   "max": 7200,  "label": "分析间隔",    "unit": "秒"},
    "ai_tick":           {"env": "TIER_LONG_AI_TICK_SEC",    "default": "90",    "type": "int",   "min": 60,    "max": 600,   "label": "AI tick",     "unit": "秒"},
    # [2026-07-17] default/max 随 settings.py TIER_PROTECTION_PARAMS 同步上调
    # (2h → 72h/3天，"长线至少持仓3天以上"才算长线，与 max_hold=7天 组成合理区间)
    "min_hold":          {"env": "TIER_LONG_MIN_HOLD_SEC",   "default": "259200","type": "int",   "min": 1800,  "max": 345600,"label": "最小持仓",    "unit": "秒"},
    "max_hold":          {"env": "TIER_LONG_MAX_HOLD_SEC",   "default": "604800","type": "int",   "min": 86400, "max": 2592000,"label": "最大持仓",    "unit": "秒"},
    "review_interval":   {"env": "TREND_REVIEW_INTERVAL_SEC","default": "5400",  "type": "int",   "min": 1800,  "max": 14400, "label": "复查间隔",    "unit": "秒"},
    "review_max":        {"env": "TREND_REVIEW_MAX_PER_TICK","default": "2",     "type": "int",   "min": 1,     "max": 5,     "label": "每次复查数",  "unit": "个"},
    # MLTO
    "thesis_ledger":     {"env": "MIDLONG_THESIS_LEDGER_ENABLED","default":"true","type":"bool",  "min": 0,     "max": 1,     "label": "论点账本",     "unit": ""},
    "mlto_controls":     {"env": "MIDLONG_MLTO_CONTROLS_EXEC","default": "true","type": "bool",  "min": 0,     "max": 1,     "label": "MLTO控开单",   "unit": ""},
}

_GROUPS = {
    "tp_sl":     {"title": "止盈/止损", "order": 1},
    "staged_tp": {"title": "分批止盈(D14)", "order": 2},
    "signal":    {"title": "信号门禁", "order": 3},
    "position":  {"title": "仓位与时间", "order": 4},
    "exit":      {"title": "主动退出", "order": 5},
    "mlto":      {"title": "MLTO论点账本(高级)", "order": 6},
}

# 分组归属
_MID_GROUPS = {
    "tp_pct":"tp_sl", "sl_pct":"tp_sl", "min_sl_pct":"tp_sl", "max_sl_pct":"tp_sl",
    "min_tp_pct":"tp_sl", "max_tp_pct":"tp_sl",
    "paper_conf":"signal", "live_conf":"signal", "mtf_strong_conf":"signal", "mtf_conflict_mult":"signal",
    "budget":"position", "max_margin":"position", "analysis_interval":"position", "ai_tick":"position",
    "min_hold":"position", "max_hold":"position", "scan_batch":"position",
    "active_exit":"exit", "exit_invalidate":"exit", "exit_min_hold":"exit",
}

_LONG_GROUPS = {
    "sl_pct":"tp_sl", "sl_safety_net":"tp_sl",
    "staged_tp_enabled":"staged_tp", "tp1_trigger":"staged_tp", "tp1_reduce":"staged_tp",
    "tp2_trigger":"staged_tp", "tp2_reduce":"staged_tp",
    "tp3_trigger":"staged_tp", "tp3_reduce":"staged_tp", "trailing_atr_mult":"staged_tp",
    "paper_min_score":"signal", "live_min_score":"signal", "max_opens_per_week":"signal",
    "budget":"position", "max_margin":"position", "analysis_interval":"position",
    "ai_tick":"position", "min_hold":"position", "max_hold":"position",
    "review_interval":"position", "review_max":"position",
    "thesis_ledger":"mlto", "mlto_controls":"mlto",
}

# 为每个参数补 group 字段
for _k, _v in _MID_PARAMS.items():
    _v["group"] = _MID_GROUPS.get(_k, "position")
for _k, _v in _LONG_PARAMS.items():
    _v["group"] = _LONG_GROUPS.get(_k, "position")


# ════════════════════════════════════════
# 预设
# ════════════════════════════════════════

_MID_PRESETS = {
    "conservative": {"name": "保守", "description": "高门槛少交易", "params": {"tp_pct":0.06,"sl_pct":0.03,"paper_conf":55,"budget":0.25}},
    "balanced":     {"name": "均衡", "description": "默认推荐", "params": {"tp_pct":0.07,"sl_pct":0.035,"paper_conf":48,"budget":0.35}},
    "aggressive":   {"name": "激进", "description": "低门槛多信号", "params": {"tp_pct":0.08,"sl_pct":0.03,"paper_conf":40,"budget":0.40}},
}

_LONG_PRESETS = {
    "conservative": {"name": "保守", "description": "大止损少开仓", "params": {"sl_pct":0.10,"paper_min_score":50,"budget":0.30,"max_opens_per_week":3}},
    "balanced":     {"name": "均衡", "description": "默认推荐", "params": {"sl_pct":0.08,"paper_min_score":40,"budget":0.40,"max_opens_per_week":6}},
    "aggressive":   {"name": "激进", "description": "多开仓追趋势", "params": {"sl_pct":0.06,"paper_min_score":35,"budget":0.50,"max_opens_per_week":10}},
}


# ════════════════════════════════════════
# 读写（复用短线逻辑）
# ════════════════════════════════════════

def _get_params(tier: str) -> Dict[str, Dict[str, Any]]:
    return _MID_PARAMS if tier == "mid" else _LONG_PARAMS

def _get_presets(tier: str) -> Dict:
    return _MID_PRESETS if tier == "mid" else _LONG_PRESETS

def _read_config(tier: str) -> Dict[str, Any]:
    result = {}
    for key, defn in _get_params(tier).items():
        raw = os.environ.get(defn["env"], defn["default"])
        if defn["type"] == "float":
            result[key] = float(raw)
        elif defn["type"] == "int":
            result[key] = int(float(raw))
        elif defn["type"] == "bool":
            result[key] = str(raw).lower() in ("1", "true", "yes", "on")
        else:
            result[key] = raw
    return result

def _write_config(tier: str, updates: Dict[str, Any]) -> int:
    count = 0
    params = _get_params(tier)
    for key, value in updates.items():
        defn = params.get(key)
        if not defn:
            continue
        os.environ[defn["env"]] = "true" if (defn["type"] == "bool" and value) else str(value)
        count += 1
    # 持久化到 .env（简化版：追加不存在的）
    try:
        env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env")
        lines = []
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        env_updates = {}
        for key, value in updates.items():
            defn = params.get(key)
            if not defn: continue
            env_updates[defn["env"]] = "true" if (defn["type"] == "bool" and value) else str(value)
        existing = set()
        new_lines = []
        for line in lines:
            s = line.strip()
            if "=" in s and not s.startswith("#"):
                k = s.split("=", 1)[0].strip()
                if k in env_updates:
                    new_lines.append(f"{k}={env_updates[k]}\n")
                    existing.add(k)
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)
        missing = set(env_updates.keys()) - existing
        if missing:
            new_lines.append(f"\n# ── {tier}策略配置页写入 ──\n")
            for k in missing:
                new_lines.append(f"{k}={env_updates[k]}\n")
        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
    except Exception as e:
        logger.warning(f"[StrategyConfig] .env持久化失败: {e}")
    return count


# ════════════════════════════════════════
# 实测统计
# ════════════════════════════════════════

def _get_stats(tier: str) -> Dict:
    # 中长线合并：mid 已并入 long，二者 trade_nature 统一为 trend_follow
    # （旧仓位仍可能带 swing，统计口径向前看以 trend_follow 为准）
    nature = "trend_follow"
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import PaperPosition
        from datetime import datetime, timedelta
        db = SessionLocal()
        try:
            cutoff = datetime.now() - timedelta(days=7)
            rows = db.query(PaperPosition).filter(
                PaperPosition.trade_nature == nature,
                PaperPosition.status == "closed",
                PaperPosition.opened_at >= cutoff,
            ).all()
            if not rows:
                return {}
            pnls = [float(getattr(r, "unrealized_pnl", 0) or 0) + float(getattr(r, "partial_realized_pnl", 0) or 0) for r in rows]
            wins = sum(1 for p in pnls if p > 0)
            total = sum(pnls)
            avg_win = sum(p for p in pnls if p > 0) / max(wins, 1)
            losses = [p for p in pnls if p < 0]
            avg_loss = abs(sum(losses) / max(len(losses), 1)) if losses else 0
            holds = []
            for r in rows:
                if r.opened_at and r.closed_at:
                    holds.append((r.closed_at - r.opened_at).total_seconds() / 3600)
            return {
                "trades": len(rows),
                "win_rate": round(wins / len(rows), 4),
                "net_pnl": round(total, 2),
                "profit_factor": round(avg_win / max(avg_loss, 0.001), 2),
                "avg_hold_hours": round(sum(holds) / max(len(holds), 1), 1) if holds else 0,
            }
        finally:
            db.close()
    except Exception as e:
        logger.debug(f"[StrategyConfig] stats失败: {e}")
        return {}


# ════════════════════════════════════════
# API 端点
# ════════════════════════════════════════

@router.get("/{tier}")
async def get_config(tier: str):
    """读取中线/长线配置 + 统计。"""
    if tier not in ("mid", "long"):
        return {"error": "tier must be 'mid' or 'long'"}
    params = _get_params(tier)
    config = _read_config(tier)
    return {
        "tier": tier,
        "config": config,
        "param_defs": params,
        "groups": _GROUPS,
        "stats": _get_stats(tier),
        "fetched_at": time.time(),
    }


class UpdateRequest(BaseModel):
    updates: Dict[str, Any]


@router.put("/{tier}")
async def update_config(tier: str, req: UpdateRequest):
    """修改配置。"""
    if tier not in ("mid", "long"):
        return {"error": "tier must be 'mid' or 'long'"}
    count = _write_config(tier, req.updates)
    config = _read_config(tier)
    return {"success": True, "updated_count": count, "config": config}


@router.get("/{tier}/presets")
async def get_presets(tier: str):
    """获取预设方案。"""
    if tier not in ("mid", "long"):
        return {"error": "tier must be 'mid' or 'long'"}
    return _get_presets(tier)

