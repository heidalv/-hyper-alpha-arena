"""
System config API routes
"""

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, Dict, List
import json
import logging
import os

from backend.database.connection import SessionLocal
from backend.database.models import SystemConfig, GlobalSamplingConfig
from backend.services.trading_pairs_config import (
    TRADING_PAIRS_CONFIG_KEY,
    ensure_trading_pairs_seeded,
    get_user_trading_pairs,
    invalidate_trading_pairs_cache,
    save_user_trading_pairs,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/config", tags=["config"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class ConfigUpdateRequest(BaseModel):
    key: str
    value: str
    description: Optional[str] = None


MARGIN_MODE_KEY = "global_margin_mode"


@router.get("/margin-mode")
async def get_margin_mode(db: Session = Depends(get_db)):
    """获取全局保证金模式 (isolated / cross)"""
    try:
        cfg = db.query(SystemConfig).filter(SystemConfig.key == MARGIN_MODE_KEY).first()
        mode = cfg.value if cfg else "isolated"
        return {
            "margin_mode": mode,
            "is_cross": mode == "cross",
            "label": "全仓" if mode == "cross" else "逐仓",
            "description": "全仓：所有仓位共享保证金，资金利用率高但风险联动" if mode == "cross"
                else "逐仓：每个仓位独立保证金，单个爆仓不影响其他仓位",
        }
    except Exception as e:
        logger.error(f"Failed to get margin mode: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/margin-mode")
async def set_margin_mode(body: dict, db: Session = Depends(get_db)):
    """一键切换全局保证金模式"""
    try:
        mode = body.get("margin_mode", "").lower().strip()
        if mode not in ("cross", "isolated"):
            raise HTTPException(status_code=400, detail="margin_mode must be 'cross' or 'isolated'")

        cfg = db.query(SystemConfig).filter(SystemConfig.key == MARGIN_MODE_KEY).first()
        if cfg:
            cfg.value = mode
        else:
            cfg = SystemConfig(key=MARGIN_MODE_KEY, value=mode)
            db.add(cfg)
        db.commit()

        logger.info(f"Global margin mode switched to: {mode}")
        return {
            "margin_mode": mode,
            "is_cross": mode == "cross",
            "label": "全仓" if mode == "cross" else "逐仓",
            "description": "全仓：所有仓位共享保证金，资金利用率高但风险联动" if mode == "cross"
                else "逐仓：每个仓位独立保证金，单个爆仓不影响其他仓位",
            "message": f"已切换为{'全仓' if mode == 'cross' else '逐仓'}模式",
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to set margin mode: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/check-required")
async def check_required_configs(db: Session = Depends(get_db)):
    """Check if required configs are set"""
    try:
        return {
            "has_required_configs": True,
            "missing_configs": []
        }
    except Exception as e:
        logger.error(f"Failed to check required configs: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to check required configs: {str(e)}")


@router.get("/global-sampling")
async def get_global_sampling_config(db: Session = Depends(get_db)):
    """Get global sampling configuration"""
    try:
        config = db.query(GlobalSamplingConfig).first()
        if not config:
            # Create default config
            config = GlobalSamplingConfig(sampling_interval=18, sampling_depth=10)
            db.add(config)
            db.commit()
            db.refresh(config)

        return {
            "sampling_interval": config.sampling_interval,
            "sampling_depth": config.sampling_depth
        }
    except Exception as e:
        logger.error(f"Failed to get global sampling config: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get global sampling config: {str(e)}")


@router.put("/global-sampling")
async def update_global_sampling_config(payload: dict, db: Session = Depends(get_db)):
    """Update global sampling configuration"""
    try:
        sampling_interval = payload.get("sampling_interval")
        sampling_depth = payload.get("sampling_depth")

        # Validate sampling_interval if provided
        if sampling_interval is not None:
            if not isinstance(sampling_interval, int) or sampling_interval < 5 or sampling_interval > 60:
                raise HTTPException(
                    status_code=400,
                    detail="sampling_interval must be between 5 and 60 seconds"
                )

        # Validate sampling_depth if provided
        if sampling_depth is not None:
            if not isinstance(sampling_depth, int) or sampling_depth < 10 or sampling_depth > 60:
                raise HTTPException(
                    status_code=400,
                    detail="sampling_depth must be between 10 and 60"
                )

        config = db.query(GlobalSamplingConfig).first()
        if not config:
            config = GlobalSamplingConfig(
                sampling_interval=sampling_interval or 18,
                sampling_depth=sampling_depth or 10
            )
            db.add(config)
        else:
            if sampling_interval is not None:
                config.sampling_interval = sampling_interval
            if sampling_depth is not None:
                config.sampling_depth = sampling_depth

        db.commit()
        db.refresh(config)

        # Trigger sampling pool reconfiguration (use watchlist if available)
        try:
            logger.info(f"[DEBUG] Starting sampling pool update to depth={config.sampling_depth}")
            from services.sampling_pool import sampling_pool
            from services.trading_commands import AI_TRADING_SYMBOLS
            from services.hyperliquid_symbol_service import get_selected_symbols as get_hyperliquid_selected_symbols

            symbols = get_hyperliquid_selected_symbols() or AI_TRADING_SYMBOLS
            for symbol in symbols:
                sampling_pool.set_max_samples(symbol, config.sampling_depth)

            logger.info(f"[DEBUG] Sampling pool updated: depth={config.sampling_depth} for {len(symbols)} symbols")
            logger.info(f"Sampling pool updated: depth={config.sampling_depth} for {len(symbols)} symbols")
        except Exception as pool_err:
            logger.info(f"[ERROR] Failed to update sampling pool: {pool_err}")
            logger.warning(f"Failed to update sampling pool: {pool_err}")

        return {
            "sampling_interval": config.sampling_interval,
            "sampling_depth": config.sampling_depth
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update global sampling config: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update global sampling config: {str(e)}")


# ═══════════════════════════════════════════════════════
#  外部 API Key 管理（Coinalyze / CryptoPanic / Whale Alert）
# ═══════════════════════════════════════════════════════

ALLOWED_EXT_KEYS = {
    "COINALYZE_API_KEY": "Coinalyze 免费合约数据",
    "CRYPTOPANIC_API_KEY": "CryptoPanic 新闻聚合",
}


class ExtKeyUpdateRequest(BaseModel):
    key_name: str
    key_value: str


@router.get("/external-keys")
async def get_external_keys(db: Session = Depends(get_db)):
    """获取已配置的外部 API Key 状态（不返回明文）"""
    result: Dict[str, dict] = {}
    for key_name, label in ALLOWED_EXT_KEYS.items():
        cfg = db.query(SystemConfig).filter(SystemConfig.key == key_name).first()
        has_value = bool(cfg and cfg.value and cfg.value.strip())
        if not has_value:
            has_value = bool(os.environ.get(key_name, "").strip())
        result[key_name] = {
            "label": label,
            "configured": has_value,
            "masked": (cfg.value[:4] + "***" + cfg.value[-4:]) if (cfg and cfg.value and len(cfg.value) > 8) else ("****" if has_value else ""),
        }
    return result


@router.post("/external-keys")
async def save_external_key(req: ExtKeyUpdateRequest, db: Session = Depends(get_db)):
    """保存外部 API Key（存入数据库并立即注入环境变量）"""
    if req.key_name not in ALLOWED_EXT_KEYS:
        raise HTTPException(status_code=400, detail=f"不支持的 Key 名称: {req.key_name}")

    cfg = db.query(SystemConfig).filter(SystemConfig.key == req.key_name).first()
    if cfg:
        cfg.value = req.key_value
        cfg.description = ALLOWED_EXT_KEYS[req.key_name]
    else:
        cfg = SystemConfig(key=req.key_name, value=req.key_value, description=ALLOWED_EXT_KEYS[req.key_name])
        db.add(cfg)
    db.commit()

    os.environ[req.key_name] = req.key_value

    if req.key_name == "COINALYZE_API_KEY":
        try:
            from backend.services.derivatives_analytics_service import derivatives_analytics
            derivatives_analytics._coinalyze_key = req.key_value
            derivatives_analytics._cache.clear()
            logger.info("[Config] Coinalyze Key 已更新并刷新缓存")
        except Exception:
            pass

    logger.info(f"[Config] 外部 API Key [{req.key_name}] 已保存")
    return {"status": "ok", "key_name": req.key_name}


# ═══════════════════════════════════════════════════════
#  常用交易对配置（全局 user_trading_pairs）
# ═══════════════════════════════════════════════════════

class TradingPairsUpdateRequest(BaseModel):
    symbols: List[str]


def _get_hl_tradable_set() -> set:
    """获取 Hyperliquid 交易所当前可交易的币种集合"""
    try:
        from backend.services.hyperliquid_symbol_service import get_available_symbols
        available = get_available_symbols()
        return {entry["symbol"].upper() for entry in available if entry.get("symbol")}
    except Exception as e:
        logger.warning(f"Failed to load HL symbols: {e}")
        return set()


def _enrich_symbols(symbols: List[str], hl_set: set) -> List[dict]:
    """为每个交易对附加交易所验证状态"""
    result = []
    for sym in symbols:
        status = "verified" if sym in hl_set else "unverified"
        result.append({"symbol": sym, "status": status})
    return result


@router.get("/trading-pairs")
async def get_trading_pairs(db: Session = Depends(get_db)):
    """获取用户配置的常用交易对列表（含交易所验证状态）"""
    try:
        symbols = ensure_trading_pairs_seeded(db)

        hl_set = _get_hl_tradable_set()

        hl_all = []
        try:
            from backend.services.hyperliquid_symbol_service import get_available_symbols
            hl_all = [entry["symbol"] for entry in get_available_symbols()]
        except Exception:
            pass

        return {
            "symbols": symbols,
            "symbols_detail": _enrich_symbols(symbols, hl_set),
            "builtin": hl_all,
            "exchange_symbols": hl_all,
            "exchange": "Hyperliquid",
            "format_info": "内部使用大写短码(如BTC)，下单自动转为 BTC/USDC:USDC 格式",
        }
    except Exception as e:
        logger.error(f"Failed to get trading pairs: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/trading-pairs")
async def update_trading_pairs(req: TradingPairsUpdateRequest, db: Session = Depends(get_db)):
    """保存用户配置的常用交易对列表"""
    try:
        cleaned = save_user_trading_pairs(req.symbols, db=db)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    try:
        hl_set = _get_hl_tradable_set()
        hl_all = []
        try:
            from backend.services.hyperliquid_symbol_service import get_available_symbols
            hl_all = [entry["symbol"] for entry in get_available_symbols()]
        except Exception:
            pass

        logger.info(f"[Config] Trading pairs updated: {cleaned}")

        # Trigger K-line backfill for newly added symbols (non-blocking)
        try:
            import asyncio
            from services.kline_realtime_collector import realtime_collector
            if realtime_collector.running:
                loop = asyncio.get_event_loop()
                loop.create_task(realtime_collector._initial_backfill())
                logger.info("[Config] K-line backfill triggered for updated trading pairs")
        except Exception as _bf_err:
            logger.warning(f"[Config] K-line backfill trigger failed (non-fatal): {_bf_err}")

        return {
            "symbols": cleaned,
            "symbols_detail": _enrich_symbols(cleaned, hl_set),
            "builtin": hl_all,
            "exchange_symbols": hl_all,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update trading pairs: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/trading-pairs/refresh-exchange")
async def refresh_exchange_symbols():
    """强制刷新 Hyperliquid 可交易币种缓存"""
    try:
        from backend.services.hyperliquid_symbol_service import refresh_hyperliquid_symbols
        refreshed = refresh_hyperliquid_symbols(environment="mainnet")
        return {
            "exchange_symbols": [entry["symbol"] for entry in refreshed],
            "count": len(refreshed),
        }
    except Exception as e:
        logger.error(f"Failed to refresh exchange symbols: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════════
# 交易门禁配置可视化 API（精简后的9道核心门）
# ═══════════════════════════════════════════════════════════════

# 门禁定义：键名、所属链路、说明、默认值
_GATE_DEFINITIONS = [
    # ═══════════════════════════════════════════════════════════
    #  共享硬门禁（hard gate，违反则绝对否决开仓；适用于所有策略层）
    #  业界依据：风控底线用 hard gate，判断性信号用 soft（r/algotrading/QuantConnect）
    # ═══════════════════════════════════════════════════════════
    {"key": "V5_MIN_RISK_REWARD", "layer": "共享", "category": "hard", "name": "最低盈亏比",
     "desc": "TP:SL 最低比例，低于此值否决开仓（业界基准1:2，震荡市可1.5）", "default": 1.8, "type": "float", "min": 1.0, "max": 5.0},
    {"key": "V5_MIN_TP_PCT", "layer": "共享", "category": "hard", "name": "最小止盈距离",
     "desc": "TP 距离下限，确保覆盖往返手续费（否则数学期望为负）", "default": 0.012, "type": "float", "min": 0.003, "max": 0.05},
    {"key": "V5_MAX_TRADE_RISK_PCT", "layer": "共享", "category": "hard", "name": "单笔最大风险",
     "desc": "单笔最大亏损占权益比例（SizingAgent最后一道闸，业界1-2%）", "default": 0.015, "type": "float", "min": 0.005, "max": 0.05},
    {"key": "V5_MAX_DAILY_TRADES", "layer": "共享", "category": "hard", "name": "每日开仓上限",
     "desc": "全局每日开仓笔数上限（防过度交易，0=不限制）", "default": 50, "type": "int", "min": 0, "max": 200},
    {"key": "V5_SCALP_MIN_CONFIDENCE", "layer": "共享", "category": "hard", "name": "短线最低置信度",
     "desc": "短线开仓最低置信度。注：scalp走独立调度器不经此门，此值仅对非独立路径生效(实盘)", "default": 70, "type": "int", "min": 30, "max": 95},
    {"key": "V5_TREND_FOLLOW_MIN_CONFIDENCE", "layer": "共享", "category": "hard", "name": "趋势仓最低置信度",
     "desc": "趋势仓开仓最低置信度(实盘值)。纸盘实际用 PAPER_TREND_MIN_SCORE_TO_OPEN", "default": 72, "type": "int", "min": 30, "max": 95},

    # ═══════════════════════════════════════════════════════════
    #  短线 scalp 门禁
    # ═══════════════════════════════════════════════════════════
    {"key": "SCALP_FACTOR_CONFIRM_THRESHOLD", "layer": "短线", "category": "soft", "name": "因子确认门槛",
     "desc": "因子score≥此值才触发开仓信号", "default": 30, "type": "int", "min": 10, "max": 80},
    {"key": "SCALP_FACTOR_EXECUTE_THRESHOLD", "layer": "短线", "category": "soft", "name": "因子直通门槛",
     "desc": "因子score≥此值在scalp_factor_router直通开仓(不经过veto复审)", "default": 40, "type": "int", "min": 10, "max": 80},
    {"key": "SCALP_VETO_BAND_LOW", "layer": "短线", "category": "hard", "name": "Veto下限",
     "desc": "score低于此值直接拒绝（veto复审区间下界）", "default": 25, "type": "int", "min": 10, "max": 60},
    {"key": "SCALP_RANGE_MAX_LONG", "layer": "短线", "category": "soft", "name": "做多区间上限",
     "desc": "价格在区间此比例以上时禁追多(高分豁免)", "default": 0.97, "type": "float", "min": 0.5, "max": 1.0},
    {"key": "SCALP_RANGE_MIN_SHORT", "layer": "短线", "category": "soft", "name": "做空区间下限",
     "desc": "价格在区间此比例以下时禁追空(高分豁免)", "default": 0.03, "type": "float", "min": 0.0, "max": 0.5},
    {"key": "SCALP_RANGE_HIGH_SCORE_EXEMPT", "layer": "短线", "category": "soft", "name": "高分豁免阈值",
     "desc": "score≥此值时豁免追高/追空拦截", "default": 50, "type": "int", "min": 20, "max": 100},
    {"key": "SCALP_OPEN_COOLDOWN_SEC", "layer": "短线", "category": "hard", "name": "开仓冷却(秒)",
     "desc": "同symbol两次开仓最小间隔", "default": 60, "type": "int", "min": 0, "max": 600},
    {"key": "LAYER_BUDGET_SCALP", "layer": "短线", "category": "hard", "name": "层预算占比",
     "desc": "短线层可用资金占总权益比例", "default": 0.6, "type": "float", "min": 0.1, "max": 1.0},
    {"key": "SCALP_SIZE_PCT", "layer": "短线", "category": "hard", "name": "单笔资金占比",
     "desc": "单笔notional占总权益比例", "default": 0.3, "type": "float", "min": 0.05, "max": 0.8},
    {"key": "DYNAMIC_LEVERAGE_MAX", "layer": "短线", "category": "hard", "name": "最大杠杆",
     "desc": "动态杠杆上限（职业交易员惯例≤10x）", "default": 10, "type": "int", "min": 1, "max": 50},

    # ═══════════════════════════════════════════════════════════
    #  中长线 MLTO 门禁
    # ═══════════════════════════════════════════════════════════
    {"key": "MIDLONG_OPEN_READINESS_MIN_MID", "layer": "中长线", "category": "hard", "name": "中线就绪度门槛",
     "desc": "中线thesis readiness≥此值才能开仓（ai_first默认45）", "default": 45, "type": "int", "min": 10, "max": 90},
    {"key": "MIDLONG_OPEN_READINESS_MIN_LONG", "layer": "中长线", "category": "hard", "name": "长线就绪度门槛",
     "desc": "长线thesis readiness≥此值才能开仓（ai_first默认50）", "default": 50, "type": "int", "min": 10, "max": 90},
    {"key": "MIDLONG_THESIS_MIN_REVIEWS", "layer": "中长线", "category": "hard", "name": "最少复核次数",
     "desc": "thesis至少被LLM复核几次才能开仓（ai_first默认1）", "default": 1, "type": "int", "min": 1, "max": 10},
    {"key": "PAPER_TREND_MIN_SCORE_TO_OPEN", "layer": "中长线", "category": "hard", "name": "纸盘趋势评分门槛",
     "desc": "纸盘TrendAgent最低开仓评分（实盘固定50）", "default": 38, "type": "int", "min": 20, "max": 70},
]


@router.get("/trading-gates")
def get_trading_gates():
    """获取所有核心交易门禁的当前配置值。"""
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"), override=True)
    result = []
    for gate in _GATE_DEFINITIONS:
        key = gate["key"]
        raw = os.getenv(key)
        if raw is None:
            val = gate["default"]
        elif gate["type"] == "int":
            # 布尔开关类(max==1)：把 "true"/"false"/"yes"/"no" 转成 1/0
            if gate.get("max") == 1:
                val = 1 if str(raw).strip().lower() in ("true", "1", "yes", "on") else 0
            else:
                val = int(float(raw))
        else:
            val = float(raw)
        result.append({**gate, "current": val})
    return {"gates": result}


@router.put("/trading-gates")
def update_trading_gate(update: dict):
    """更新单个门禁配置值（写入 .env，热更新）。"""
    key = update.get("key")
    value = update.get("value")
    if not key or value is None:
        raise HTTPException(status_code=400, detail="需要 key 和 value")

    # 查找门禁定义，确定写入格式（布尔开关写 true/false，数值写数字）
    gate_def = next((g for g in _GATE_DEFINITIONS if g["key"] == key), None)
    is_bool_switch = gate_def and gate_def.get("max") == 1
    if is_bool_switch:
        # 布尔开关：写 "true"/"false"（.env 惯例，代码用 .lower() in (...) 判断）
        env_value = "true" if int(float(value)) == 1 else "false"
    else:
        env_value = str(value)

    env_path = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
    env_path = os.path.abspath(env_path)

    # 读现有 .env
    lines = []
    found = False
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        for i, line in enumerate(lines):
            if line.strip().startswith(f"{key}="):
                lines[i] = f"{key}={env_value}\n"
                found = True
                break
    if not found:
        lines.append(f"{key}={env_value}\n")

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(lines)

    # 热更新到环境变量
    os.environ[key] = env_value

    logger.info(f"[Config] 门禁更新: {key}={env_value}")
    return {"success": True, "key": key, "value": env_value}
