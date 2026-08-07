# -*- coding: utf-8 -*-
"""
短线策略配置 API — /api/scalp-config/*

提供：
- GET  /           读取当前全部配置 + 实测统计 + EV 计算
- PUT  /           修改配置（热生效 + 持久化到 .env）
- POST /simulate   传入假设参数计算 EV（不修改实际配置）
- GET  /presets    获取 4 套预设方案
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/scalp-config", tags=["scalp-config"])


# ════════════════════════════════════════
# 参数定义表（key → 环境变量名 + 默认值 + 类型 + 范围 + 描述）
# ════════════════════════════════════════

_PARAM_DEFS: Dict[str, Dict[str, Any]] = {
    # ── 1. 止盈/止损 ──
    "tp_pct":            {"env": "TIER_SHORT_TP_PCT",       "default": "0.018", "type": "float", "min": 0.008, "max": 0.05,  "group": "tp_sl",     "label": "目标止盈(TP)",     "unit": "%"},
    "sl_pct":            {"env": "TIER_SHORT_SL_PCT",       "default": "0.012", "type": "float", "min": 0.005, "max": 0.03,  "group": "tp_sl",     "label": "止损(SL)",        "unit": "%"},
    "atr_sl_mult":       {"env": "TIER_SHORT_ATR_SL_MULT",  "default": "1.0",   "type": "float", "min": 0.5,   "max": 3.0,   "group": "tp_sl",     "label": "ATR止损倍数",      "unit": "x"},
    "atr_tp_mult":       {"env": "TIER_SHORT_ATR_TP_MULT",  "default": "1.8",   "type": "float", "min": 1.0,   "max": 5.0,   "group": "tp_sl",     "label": "ATR止盈倍数",      "unit": "x"},
    "max_sl_pct":        {"env": "TIER_SHORT_MAX_SL",       "default": "0.020", "type": "float", "min": 0.01,  "max": 0.06,  "group": "tp_sl",     "label": "SL上限",          "unit": "%"},
    "max_tp_pct":        {"env": "TIER_SHORT_MAX_TP",       "default": "0.025", "type": "float", "min": 0.015, "max": 0.10,  "group": "tp_sl",     "label": "TP上限",          "unit": "%"},
    "min_rr":            {"env": "SCALP_MIN_RR_NEW",        "default": "1.3",   "type": "float", "min": 1.0,   "max": 3.0,   "group": "tp_sl",     "label": "最低盈亏比",       "unit": ""},

    # ── 2. 时间管理 ──
    "max_hold_sec":      {"env": "SCALP_MAX_HOLD_SEC",      "default": "2700",  "type": "int",   "min": 600,   "max": 7200,  "group": "time",      "label": "最大持仓时间",     "unit": "秒"},
    "roi_t1_sec":        {"env": "SCALP_ROI_T1",            "default": "600",   "type": "int",   "min": 300,   "max": 1200,  "group": "time",      "label": "ROI阶段1(保持100%)","unit": "秒"},
    "roi_t2_sec":        {"env": "SCALP_ROI_T2",            "default": "1200",  "type": "int",   "min": 600,   "max": 2400,  "group": "time",      "label": "ROI阶段2(衰减70%)","unit": "秒"},
    "roi_t3_sec":        {"env": "SCALP_ROI_T3",            "default": "1800",  "type": "int",   "min": 900,   "max": 3600,  "group": "time",      "label": "ROI阶段3(衰减40%)","unit": "秒"},

    # ── 3. 信号门禁 ──
    "execute_threshold": {"env": "SCALP_FACTOR_EXECUTE_THRESHOLD","default":"45","type": "int",  "min": 25,    "max": 80,    "group": "signal",    "label": "执行阈值",         "unit": "分"},
    "confirm_threshold": {"env": "SCALP_FACTOR_CONFIRM_THRESHOLD", "default":"35","type": "int",  "min": 20,    "max": 60,    "group": "signal",    "label": "探索阈值",         "unit": "分"},
    "ev_min_pct":        {"env": "SCALP_EV_MIN_PCT",        "default": "0.0",   "type": "float", "min": -0.005,"max": 0.01,  "group": "signal",    "label": "EV最低期望",       "unit": "%"},
    "ev_tp_realization": {"env": "SCALP_EV_TP_REALIZATION", "default": "0.55",  "type": "float", "min": 0.3,   "max": 1.0,   "group": "signal",    "label": "TP实现率(趋势)",   "unit": ""},
    "ev_gate_enabled":   {"env": "SCALP_EV_GATE_ENABLED",   "default": "true",  "type": "bool",  "min": 0,     "max": 1,     "group": "signal",    "label": "EV门控",           "unit": ""},

    # ── 4. 仓位管理 ──
    "position_pct":      {"env": "SCALP_SIZE_PCT",          "default": "0.30",  "type": "float", "min": 0.05,  "max": 0.60,  "group": "position",  "label": "单笔仓位占比",     "unit": "%"},
    "leverage":          {"env": "SCALP_DEFAULT_LEVERAGE",  "default": "10",    "type": "int",   "min": 1,     "max": 50,    "group": "position",  "label": "杠杆",            "unit": "x"},
    "tier_budget":       {"env": "TIER_SHORT_BUDGET",       "default": "0.15",  "type": "float", "min": 0.05,  "max": 0.40,  "group": "position",  "label": "短线层预算",       "unit": "%"},
    "max_opens_per_tick":{"env": "SCALP_MAX_OPENS_PER_TICK","default": "1",     "type": "int",   "min": 1,     "max": 5,     "group": "position",  "label": "每tick最大开仓",   "unit": "个"},
    "open_cooldown":     {"env": "SCALP_OPEN_COOLDOWN_SEC", "default": "300",   "type": "int",   "min": 60,    "max": 1800,  "group": "position",  "label": "开仓冷却",         "unit": "秒"},

    # ── 5. 平仓策略 ──
    "ai_reverse_disabled":{"env": "SCALP_AI_REVERSE_DISABLED","default": "true","type": "bool",  "min": 0,     "max": 1,     "group": "exit",      "label": "AI反向平仓(禁用)", "unit": ""},
    "liq_magnet_disabled":{"env": "SCALP_LIQ_MAGNET_OPEN_DISABLED","default":"true","type":"bool","min": 0,    "max": 1,     "group": "exit",      "label": "磁吸开仓(禁用)",   "unit": ""},
    "reduce_min_loss":   {"env": "SCALP_REDUCE_MIN_LOSS_PCT","default": "0.20", "type": "float", "min": 0.05,  "max": 0.50,  "group": "exit",      "label": "AI减仓浮亏门槛",   "unit": "%"},

    # ── 6. MR打法 ──
    "mr_enabled":        {"env": "SCALP_RANGING_MR_ENABLED","default": "true",  "type": "bool",  "min": 0,     "max": 1,     "group": "mr",        "label": "MR打法",          "unit": ""},
    "mr_min_range":      {"env": "SCALP_MR_MIN_RANGE_PCT",  "default": "0.015", "type": "float", "min": 0.008, "max": 0.03,  "group": "mr",        "label": "MR最小振幅",       "unit": "%"},
    "mr_max_range":      {"env": "SCALP_MR_MAX_RANGE_PCT",  "default": "0.050", "type": "float", "min": 0.02,  "max": 0.08,  "group": "mr",        "label": "MR最大振幅",       "unit": "%"},
    "mr_rsi_os":         {"env": "SCALP_MR_RSI_OS",         "default": "40",    "type": "float", "min": 20,    "max": 50,    "group": "mr",        "label": "MR RSI超卖",       "unit": ""},
    "mr_rsi_ob":         {"env": "SCALP_MR_RSI_OB",         "default": "60",    "type": "float", "min": 50,    "max": 80,    "group": "mr",        "label": "MR RSI超买",       "unit": ""},
    "mr_size_mult":      {"env": "SCALP_MR_SIZE_MULTIPLIER","default": "1.3",   "type": "float", "min": 0.5,   "max": 2.0,   "group": "mr",        "label": "MR仓位加成",       "unit": "x"},

    # ── 7. 风险过滤 ──
    "liquidity_filter":  {"env": "SCALP_LIQUIDITY_FILTER_ENABLED","default":"true","type":"bool","min": 0,    "max": 1,     "group": "risk",      "label": "流动性过滤",       "unit": ""},
    "min_volume_usd":    {"env": "SCALP_MIN_VOLUME_USD",    "default": "50000000","type":"int",  "min": 10000000,"max":500000000,"group": "risk",    "label": "最低24h成交额",    "unit": "$"},
}

# 分组定义
_GROUPS = {
    "tp_sl":    {"title": "止盈/止损",      "icon": "Target",    "order": 1},
    "time":     {"title": "时间管理",       "icon": "Clock",     "order": 2},
    "signal":   {"title": "信号门禁",       "icon": "Shield",    "order": 3},
    "position": {"title": "仓位管理",       "icon": "Wallet",    "order": 4},
    "exit":     {"title": "平仓策略",       "icon": "LogOut",    "order": 5},
    "mr":       {"title": "均值回归(MR)",  "icon": "Repeat",    "order": 6},
    "risk":     {"title": "风险过滤",       "icon": "AlertTriangle","order": 7},
}


# ════════════════════════════════════════
# EV 计算引擎
# ════════════════════════════════════════

def _calculate_ev(tp: float, sl: float, p_win: float, tp_real: float = 0.55,
                  sl_real: float = 1.0, leverage: int = 10, position_pct: float = 0.30,
                  trades_per_day: int = 3) -> Dict[str, float]:
    """计算期望值 + 盈亏平衡胜率 + 日化/月化收益。"""
    round_trip_cost = 0.0021  # Hyperliquid taker 往返 + intraday 滑点

    ev_pct = p_win * tp * tp_real - (1 - p_win) * sl * sl_real - round_trip_cost
    rr = tp / sl if sl > 0 else 0

    # 盈亏平衡胜率
    denom = tp * tp_real + sl * sl_real
    breakeven_win = (sl * sl_real + round_trip_cost) / denom if denom > 0 else 1.0

    # 日化/月化（基于保证金收益率）
    daily_return = ev_pct * leverage * position_pct * trades_per_day
    monthly_return = daily_return * 30  # 加密 24/7

    return {
        "ev_pct": round(ev_pct, 6),
        "rr": round(rr, 2),
        "breakeven_win": round(breakeven_win, 4),
        "daily_return": round(daily_return, 6),
        "monthly_return": round(monthly_return, 6),
        "round_trip_cost": round_trip_cost,
        "fee_ratio": round(round_trip_cost / max(tp * tp_real, 0.001), 4),  # 手续费占盈利比
    }


# ════════════════════════════════════════
# 预设方案
# ════════════════════════════════════════

_PRESETS = {
    "conservative": {
        "name": "保守",
        "description": "保本优先，低回撤，适合验证阶段",
        "params": {
            "tp_pct": 0.015, "sl_pct": 0.010, "max_hold_sec": 1800,
            "execute_threshold": 50, "ev_min_pct": 0.001,
            "position_pct": 0.20, "ai_reverse_disabled": True,
            "liq_magnet_disabled": True, "mr_enabled": True, "min_volume_usd": 100000000,
        }
    },
    "balanced": {
        "name": "均衡",
        "description": "推荐方案，兼顾胜率和频率",
        "params": {
            "tp_pct": 0.018, "sl_pct": 0.012, "max_hold_sec": 2700,
            "execute_threshold": 45, "ev_min_pct": 0.0,
            "position_pct": 0.30, "ai_reverse_disabled": True,
            "liq_magnet_disabled": True, "mr_enabled": True, "min_volume_usd": 50000000,
        }
    },
    "aggressive": {
        "name": "激进",
        "description": "高频高量，追求日化2%+",
        "params": {
            "tp_pct": 0.018, "sl_pct": 0.012, "max_hold_sec": 1200,
            "execute_threshold": 35, "ev_min_pct": -0.002,
            "position_pct": 0.40, "ai_reverse_disabled": True,
            "liq_magnet_disabled": True, "mr_enabled": True, "min_volume_usd": 20000000,
            "max_opens_per_tick": 2, "open_cooldown": 120,
        }
    },
    "pure_mr": {
        "name": "纯均值回归",
        "description": "只做震荡市高抛低吸，高胜率低回撤",
        "params": {
            "tp_pct": 0.018, "sl_pct": 0.012, "max_hold_sec": 1800,
            "execute_threshold": 30, "ev_min_pct": 0.0,
            "position_pct": 0.25, "mr_size_mult": 1.5,
            "mr_min_range": 0.006, "mr_max_range": 0.060,
            "ai_reverse_disabled": True, "liq_magnet_disabled": True, "mr_enabled": True,
        }
    },
}


# ════════════════════════════════════════
# 读取/写入 .env
# ════════════════════════════════════════

_ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env")


def _read_current_config() -> Dict[str, Any]:
    """从 os.environ 读取当前生效的配置值。"""
    result = {}
    for key, defn in _PARAM_DEFS.items():
        env_key = defn["env"]
        raw = os.environ.get(env_key, defn["default"])
        if defn["type"] == "float":
            result[key] = float(raw)
        elif defn["type"] == "int":
            result[key] = int(float(raw))
        elif defn["type"] == "bool":
            result[key] = str(raw).lower() in ("1", "true", "yes", "on")
        else:
            result[key] = raw
    return result


def _write_env(updates: Dict[str, Any]) -> int:
    """更新 os.environ（热生效）+ 持久化到 .env 文件。返回更新条数。"""
    count = 0
    # 1. 更新 os.environ（立即热生效——settings.py 下次 os.getenv 就读到新值）
    for key, value in updates.items():
        defn = _PARAM_DEFS.get(key)
        if not defn:
            continue
        env_key = defn["env"]
        if defn["type"] == "bool":
            os.environ[env_key] = "true" if value else "false"
        else:
            os.environ[env_key] = str(value)
        count += 1

    # 2. 持久化到 .env（重启后不丢）
    try:
        # 读取现有 .env
        lines = []
        if os.path.exists(_ENV_FILE):
            with open(_ENV_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()

        # 构建要写入的 key→value 映射
        env_updates = {}
        for key, value in updates.items():
            defn = _PARAM_DEFS.get(key)
            if not defn:
                continue
            env_key = defn["env"]
            if defn["type"] == "bool":
                env_updates[env_key] = "true" if value else "false"
            else:
                env_updates[env_key] = str(value)

        # 更新已存在的行 + 收集不存在的
        existing_keys = set()
        new_lines = []
        for line in lines:
            stripped = line.strip()
            if "=" in stripped and not stripped.startswith("#"):
                k = stripped.split("=", 1)[0].strip()
                if k in env_updates:
                    new_lines.append(f"{k}={env_updates[k]}\n")
                    existing_keys.add(k)
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)

        # 追加不存在的
        missing = set(env_updates.keys()) - existing_keys
        if missing:
            new_lines.append("\n# ── 短线配置页写入 ──\n")
            for k in missing:
                new_lines.append(f"{k}={env_updates[k]}\n")

        with open(_ENV_FILE, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
    except Exception as e:
        logger.warning(f"[ScalpConfig] .env 持久化失败(热生效不受影响): {e}")

    return count


# ════════════════════════════════════════
# API 端点
# ════════════════════════════════════════

@router.get("/")
async def get_config():
    """读取当前全部配置 + 实测统计 + EV 计算。"""
    config = _read_current_config()

    # EV 计算（用当前配置 + 假设胜率 55%）
    ev = _calculate_ev(
        tp=config["tp_pct"], sl=config["sl_pct"],
        p_win=0.55, tp_real=config["ev_tp_realization"],
        leverage=config["leverage"], position_pct=config["position_pct"],
    )

    # 实测统计（从 DB 查最近 7 天）
    stats = {}
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import PaperPosition
        from datetime import datetime, timedelta, timezone
        from sqlalchemy import func

        db = SessionLocal()
        try:
            cutoff = datetime.now() - timedelta(days=7)
            rows = db.query(PaperPosition).filter(
                PaperPosition.trade_nature == "scalp",
                PaperPosition.status == "closed",
                PaperPosition.opened_at >= cutoff,
            ).all()

            if rows:
                pnls = [float(getattr(r, "unrealized_pnl", 0) or 0) + float(getattr(r, "partial_realized_pnl", 0) or 0) for r in rows]
                wins = sum(1 for p in pnls if p > 0)
                total_pnl = sum(pnls)
                avg_win = sum(p for p in pnls if p > 0) / max(wins, 1)
                losses = [p for p in pnls if p < 0]
                avg_loss = abs(sum(losses) / max(len(losses), 1)) if losses else 0

                stats = {
                    "trades": len(rows),
                    "win_rate": round(wins / len(rows), 4),
                    "net_pnl": round(total_pnl, 2),
                    "profit_factor": round(avg_win / max(avg_loss, 0.001), 2),
                    "avg_win": round(avg_win, 4),
                    "avg_loss": round(avg_loss, 4),
                }
        finally:
            db.close()
    except Exception as e:
        logger.debug(f"[ScalpConfig] 统计读取失败: {e}")

    return {
        "config": config,
        "param_defs": _PARAM_DEFS,
        "groups": _GROUPS,
        "stats": stats,
        "ev": ev,
        "fetched_at": time.time(),
    }


@router.put("/")
async def update_config(updates: Dict[str, Any]):
    """修改配置（热生效 + 持久化）。"""
    # 验证参数范围
    errors = []
    validated = {}
    for key, value in updates.items():
        defn = _PARAM_DEFS.get(key)
        if not defn:
            errors.append(f"未知参数: {key}")
            continue
        if defn["type"] in ("float", "int"):
            try:
                v = float(value)
                if v < defn["min"] or v > defn["max"]:
                    errors.append(f"{defn['label']}({key})={v} 超出范围[{defn['min']}, {defn['max']}]")
                    continue
                validated[key] = int(v) if defn["type"] == "int" else v
            except (ValueError, TypeError):
                errors.append(f"{defn['label']}({key})={value} 不是有效数字")
        elif defn["type"] == "bool":
            validated[key] = bool(value)
        else:
            validated[key] = value

    if errors:
        return {"success": False, "errors": errors}

    count = _write_env(validated)
    config = _read_current_config()
    ev = _calculate_ev(
        tp=config["tp_pct"], sl=config["sl_pct"],
        p_win=0.55, tp_real=config["ev_tp_realization"],
        leverage=config["leverage"], position_pct=config["position_pct"],
    )

    return {
        "success": True,
        "updated_count": count,
        "config": config,
        "ev": ev,
    }


class SimulateRequest(BaseModel):
    tp_pct: float = 0.018
    sl_pct: float = 0.012
    p_win: float = 0.55
    tp_realization: float = 0.55
    leverage: int = 10
    position_pct: float = 0.30
    trades_per_day: int = 3


@router.post("/simulate")
async def simulate(req: SimulateRequest):
    """传入假设参数计算 EV（不修改实际配置）。"""
    ev = _calculate_ev(
        tp=req.tp_pct, sl=req.sl_pct, p_win=req.p_win,
        tp_real=req.tp_realization, leverage=req.leverage,
        position_pct=req.position_pct, trades_per_day=req.trades_per_day,
    )
    # 额外计算不同胜率下的 EV 曲线
    sensitivity = []
    for pw in range(35, 80, 5):
        pw_f = pw / 100.0
        e = _calculate_ev(tp=req.tp_pct, sl=req.sl_pct, p_win=pw_f,
                         tp_real=req.tp_realization, leverage=req.leverage,
                         position_pct=req.position_pct, trades_per_day=req.trades_per_day)
        sensitivity.append({"p_win": pw_f, "ev_pct": e["ev_pct"], "daily": e["daily_return"]})

    return {**ev, "sensitivity": sensitivity}


@router.get("/presets")
async def get_presets():
    """获取内置预设 + 自定义预设 + 当前匹配状态。"""
    custom = _load_custom_presets()
    return {**_PRESETS, **{k: v for k, v in custom.items()}}


# ════════════════════════════════════════
# 自定义预设存储（JSON 文件持久化）
# ════════════════════════════════════════

_CUSTOM_PRESETS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "data", "scalp_custom_presets.json"
)


def _load_custom_presets() -> Dict[str, Any]:
    """读取自定义预设。"""
    try:
        import json
        if os.path.exists(_CUSTOM_PRESETS_FILE):
            with open(_CUSTOM_PRESETS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_custom_presets(data: Dict[str, Any]) -> None:
    """写入自定义预设。"""
    try:
        import json
        os.makedirs(os.path.dirname(_CUSTOM_PRESETS_FILE), exist_ok=True)
        with open(_CUSTOM_PRESETS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[ScalpConfig] 自定义预设保存失败: {e}")


def _match_preset(config: Dict[str, Any], all_presets: Dict[str, Any]) -> str:
    """检测当前配置匹配哪个预设，返回 preset key 或 'custom'。"""
    for key, preset in all_presets.items():
        params = preset.get("params", {})
        if not params:
            continue
        matched = True
        for pk, pv in params.items():
            cv = config.get(pk)
            if cv is None:
                continue
            # 数值比较容忍小误差
            if isinstance(pv, (int, float)) and isinstance(cv, (int, float)):
                if abs(float(cv) - float(pv)) > 1e-9:
                    matched = False
                    break
            elif isinstance(pv, bool) or isinstance(cv, bool):
                if bool(cv) != bool(pv):
                    matched = False
                    break
            elif str(cv) != str(pv):
                matched = False
                break
        if matched:
            return key
    return "custom"


class SavePresetRequest(BaseModel):
    name: str
    description: str = ""
    params: Dict[str, Any]


@router.post("/presets/custom")
async def save_custom_preset(req: SavePresetRequest):
    """保存自定义预设（用户可命名）。"""
    import time as _time
    custom = _load_custom_presets()
    # 用 name 生成 key（去空格+小写），如重复则覆盖
    key = "custom_" + req.name.strip().lower().replace(" ", "_").replace("/", "_")[:30]
    custom[key] = {
        "name": req.name.strip(),
        "description": req.description or f"用户自定义（{_time.strftime('%m-%d %H:%M')}）",
        "params": req.params,
        "is_custom": True,
        "saved_at": _time.time(),
    }
    _save_custom_presets(custom)
    return {"success": True, "key": key, "preset": custom[key]}


@router.delete("/presets/custom/{preset_key}")
async def delete_custom_preset(preset_key: str):
    """删除自定义预设。"""
    custom = _load_custom_presets()
    if preset_key in custom:
        del custom[preset_key]
        _save_custom_presets(custom)
        return {"success": True}
    return {"success": False, "error": "预设不存在"}


@router.get("/current-preset")
async def get_current_preset():
    """检测当前配置匹配哪个预设。"""
    config = _read_current_config()
    all_presets = {**_PRESETS, **_load_custom_presets()}
    matched_key = _match_preset(config, all_presets)
    matched_preset = all_presets.get(matched_key)
    return {
        "preset_key": matched_key,
        "preset_name": matched_preset["name"] if matched_preset else "自定义",
        "is_custom": matched_key == "custom" or (matched_preset or {}).get("is_custom", False),
    }

