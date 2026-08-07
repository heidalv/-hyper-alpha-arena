import logging
import logging.handlers
import os as _os_log_init
import sys
import threading
import time
from pathlib import Path

# ── 确保 qaa_architecture_package 和 backend 父目录在 sys.path 最前面 ──
# 必须在任何 backend.* / qaa.* 导入之前执行，避免 ImportError: unknown location
_BACKEND_DIR = Path(__file__).resolve().parent  # backend/
_PROJECT_DIR = _BACKEND_DIR.parent             # Hyper-Alpha-Arena/
_QAA_PKG_DIR = _PROJECT_DIR / "qaa_architecture_package"
for _d in (str(_PROJECT_DIR), str(_QAA_PKG_DIR)):
    if _d not in sys.path:
        sys.path.insert(0, _d)

# 先加载 .env，再由 rollout 仅填补未显式配置的项（用户 .env 优先）
try:
    from dotenv import load_dotenv
    load_dotenv(str(_PROJECT_DIR / ".env"), override=False)
except Exception:
    pass

# 量化框架升级激进 rollout（必须在任何读 env 的业务模块 import 之前）
try:
    from backend.config.framework_rollout import apply_aggressive_rollout
    apply_aggressive_rollout()
except Exception:
    pass

# P0.5 环境变量严格校验：env-rollout 后、业务 import 前扫描一次。
# 捕获"匹配系统前缀但未登记"的 flag（疑似拼写/遗留/静默禁用），
# 以及被设为 falsy 的安全关键 flag。默认 warn；ENV_STRICT=error 时硬失败。
try:
    from backend.config.env_registry import validate_strict
    validate_strict()
except SystemExit:
    raise  # ENV_STRICT=error 时直接终止启动
except Exception:
    pass  # 校验器自身故障不阻塞启动（但应告警）

# Set UTF-8 encoding for Windows console
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# ── 日志双输出初始化（2026-05-08 修复 logs/ 目录长期为空）──
# 之前只有 backend/utils/monitoring.py 定义了 configure_logging 但无人调用，
# 导致所有错误只在控制台一闪而过。现在在 import 阶段就接好 RotatingFileHandler。
def _bootstrap_logging() -> None:
    _backend_dir = Path(__file__).resolve().parent
    _logs_dir = _backend_dir.parent / "logs"
    _logs_dir.mkdir(parents=True, exist_ok=True)
    _root = logging.getLogger()
    # 已被其它入口配置过则跳过，避免重复 handler
    if any(getattr(h, "_hyper_alpha_arena_file", False) for h in _root.handlers):
        return
    _level_name = _os_log_init.getenv("BACKEND_LOG_LEVEL", "INFO").upper()
    _level = getattr(logging, _level_name, logging.INFO)
    _root.setLevel(_level)

    # ── trace_id 过滤器：注入 trace_id_short 到每条日志记录 ──
    from backend.utils.trace_context import install_trace_filter
    install_trace_filter()  # 幂等，装在 root logger

    # 自定义 Formatter：安全取 trace_id_short（uvicorn reload 子进程可能没装 filter）
    class _SafeTraceFormatter(logging.Formatter):
        def format(self, record):
            if not hasattr(record, 'trace_id_short'):
                record.trace_id_short = '-'
            if not hasattr(record, 'trace_id'):
                record.trace_id = ''
            return super().format(record)

    _fmt = _SafeTraceFormatter(
        "%(asctime)s [%(levelname)s] [tr=%(trace_id_short)s] %(name)s:%(lineno)d - %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )
    _has_console = any(isinstance(h, logging.StreamHandler) and not getattr(h, "_hyper_alpha_arena_file", False) for h in _root.handlers)
    if not _has_console:
        _ch = logging.StreamHandler(sys.stdout)
        _ch.setLevel(_level)
        _ch.setFormatter(_fmt)
        _root.addHandler(_ch)
    _fh = logging.handlers.RotatingFileHandler(
        filename=str(_logs_dir / "backend.log"),
        maxBytes=20 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    _fh.setLevel(_level)
    _fh.setFormatter(_fmt)
    _fh._hyper_alpha_arena_file = True  # type: ignore[attr-defined]
    _root.addHandler(_fh)
    _err_fh = logging.handlers.RotatingFileHandler(
        filename=str(_logs_dir / "backend.error.log"),
        maxBytes=10 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    _err_fh.setLevel(logging.WARNING)
    _err_fh.setFormatter(_fmt)
    _err_fh._hyper_alpha_arena_file = True  # type: ignore[attr-defined]
    _root.addHandler(_err_fh)
    # uvicorn 不会自动 propagate；强制接入
    for _name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        _lg = logging.getLogger(_name)
        _lg.setLevel(_level)
        _lg.propagate = True
    # Hyperliquid/ccxt WS 正常过期重连不应刷 ERROR
    for _name in ("websockets", "websocket"):
        logging.getLogger(_name).setLevel(logging.WARNING)
    logging.getLogger(__name__).info(
        f"[Logging] 日志初始化完成: level={_level_name} dir={_logs_dir}"
    )


_bootstrap_logging()
logger = logging.getLogger(__name__)

import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

# Load environment variables from .env file
# uv run --directory backend 将 CWD 设为 backend/，需向上查找根目录的 .env
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

# ══════════════════════════════════════════════════════════════
# Production safety gate: refuse to start without BACKEND_API_KEY
# and a strong JWT_SECRET in production environments
# ══════════════════════════════════════════════════════════════
_ENVIRONMENT = os.getenv("ENVIRONMENT", "development").strip().lower()
_BACKEND_KEY_SET = bool(os.getenv("BACKEND_API_KEY"))
if _ENVIRONMENT == "production" and not _BACKEND_KEY_SET:
    import sys as _sys
    print(
        "FATAL: ENVIRONMENT=production but BACKEND_API_KEY is not set.\n"
        "       Refusing to start with open authentication.\n"
        "       Set BACKEND_API_KEY in .env or via environment variable.",
        file=_sys.stderr,
    )
    _sys.exit(1)
elif _ENVIRONMENT == "production":
    # C2: 生产环境强制 JWT_SECRET 为强随机值(>=16 字符),否则攻击者可用已知弱
    # 默认密钥("dev-only-change-me-in-prod")伪造任意用户/admin 的 JWT。
    _JWT_SECRET = os.getenv("JWT_SECRET", "")
    if (
        not _JWT_SECRET
        or _JWT_SECRET == "dev-only-change-me-in-prod"
        or len(_JWT_SECRET) < 16
    ):
        import sys as _sys
        print(
            "FATAL: ENVIRONMENT=production but JWT_SECRET is missing, weak, or the\n"
            "       known dev default. Refusing to start: attacker could forge JWTs.\n"
            "       Set JWT_SECRET to a strong random value (>=16 chars) in .env.",
            file=_sys.stderr,
        )
        _sys.exit(1)
    logging.getLogger(__name__).info(
        "[startup] Production mode: API key authentication ENFORCED, JWT_SECRET verified"
    )
elif not _BACKEND_KEY_SET:
    logging.getLogger(__name__).warning(
        "[startup] BACKEND_API_KEY not set — API write operations are UNPROTECTED "
        "(acceptable for local development only)"
    )

from backend.config.settings import DEFAULT_TRADING_CONFIGS
from backend.database.connection import (
    AnalyticsBase,
    Base,
    MarketBase,
    MarketSessionLocal,
    SessionLocal,
    analytics_engine,
    engine,
    market_engine,
)
from backend.database.models import SystemConfig, TradingConfig, User
from backend.version import __version__

app = FastAPI(
    title="Hyper Alpha Arena API",
    version=__version__,
    description="Cryptocurrency perpetual contract trading platform with AI-powered decision making"
)

# Health check endpoint
# [2026-07-17 修复] 此前这个路径直接塞了 7+ 处动态 import + 跨子系统统计调用
# （rollout/event_sourcing/ml_activation/resource_guard/promotion_gate/
# orchestrator/qaa_rag），全部同步执行、中间没有任何 await 让出事件循环。
# 正常情况下这些调用很快，但本项目是"单进程 + 大量 LLM 调用线程/APScheduler
# 后台线程"架构，所有线程共享同一个 GIL——一旦有并发 LLM 流式请求占着 GIL，
# 事件循环线程要把这 7+ 段代码全部跑完才能返回 200，等于要连续抢到 7+ 次 GIL
# 时间片，抢不到任何一次都会让整个请求卡住，越重的 handler 越容易被"千刀万剐"
# 式拖慢。而这个端点恰恰是 backend-watchdog.ps1 用来判断"后端是否存活"的探针
# ——探针本身太重导致误判 down、频繁重启，重启又触发新一轮 LLM/因子预热爆发，
# 形成"重启→卡顿→误判死亡→再重启"的恶性循环。
# 修复：/api/health 只做最基础的存活确认（一次 GIL 时间片内就能跑完），原来的
# 详细诊断信息搬到 /api/health/detailed，需要人工排查时再单独调用。
@app.get("/api/health")
async def health_check():
    return {"status": "healthy", "message": "Trading API is running", "version": __version__}


@app.get("/api/health/detailed")
async def health_check_detailed():
    rollout = {}
    event_sourcing = {}
    try:
        from backend.config.framework_rollout import _AGGRESSIVE_DEFAULTS
        rollout = {k: os.environ.get(k) for k in sorted(_AGGRESSIVE_DEFAULTS)}
    except Exception:
        pass
    try:
        from backend.services.event_sourcing import is_enabled as es_enabled
        from backend.services.event_sourcing.phase2 import (
            get_reconcile_stats,
            is_phase2_read_enabled,
            is_phase2_reconcile_enabled,
        )
        from backend.services.event_sourcing.phase3 import (
            get_phase3_stats,
            is_phase3_enabled,
            is_projection_read_active,
        )
        from backend.services.event_sourcing.phase4 import (
            get_phase4_stats,
            is_write_retirement_enabled,
        )
        event_sourcing = {
            "enabled": es_enabled(),
            "phase2_read": is_phase2_read_enabled(),
            "phase2_reconcile": is_phase2_reconcile_enabled(),
            "phase3": is_phase3_enabled(),
            "phase4_write_retire_db": is_write_retirement_enabled(),
            "projection_read_active": is_projection_read_active(),
            "reconcile": get_reconcile_stats(),
            "phase3_stats": get_phase3_stats(),
            "phase4_stats": get_phase4_stats(),
        }
    except Exception:
        pass
    ml_activation = {}
    resource_guard = {}
    promotion_gate = {}
    try:
        from backend.services.ml.activation_service import get_activation_stats
        ml_activation = get_activation_stats()
    except Exception:
        pass
    try:
        from backend.services.resource_guard import get_guard_stats
        from backend.services.resource_guard import is_enabled as rg_enabled
        resource_guard = {"enabled": rg_enabled(), **get_guard_stats()}
    except Exception:
        pass
    try:
        from backend.services.promotion_gate_service import is_enabled as pg_enabled
        from backend.services.promotion_scan_service import get_scan_stats
        promotion_gate = {"enabled": pg_enabled(), **get_scan_stats()}
    except Exception:
        pass
    orchestrator = {}
    qaa_rag = {}
    try:
        from backend.services.full_auto.orchestrator import get_orchestrator
        from backend.services.full_auto_trading_service import full_auto_trading_service
        orchestrator = get_orchestrator(full_auto_trading_service).get_loop_stats()
    except Exception:
        pass
    try:
        from backend.services.qaa_trade_memory_bridge import get_qaa_trade_memory_stats
        qaa_rag = get_qaa_trade_memory_stats()
    except Exception:
        pass
    return {
        "status": "healthy",
        "message": "Trading API is running",
        "version": __version__,
        "framework_rollout": rollout,
        "event_sourcing": event_sourcing,
        "ml_activation": ml_activation,
        "resource_guard": resource_guard,
        "promotion_gate": promotion_gate,
        "full_auto_orchestrator": orchestrator,
        "qaa_rag": qaa_rag,
    }

# [阶段0] /api/rebuild-frontend 已删(前后端分离,后端不再构建前端)

# CORS: restrict origins in production, allow all in development
_ALLOWED_ORIGINS_ENV = os.getenv("FRONTEND_ORIGIN", "").strip()
if _ALLOWED_ORIGINS_ENV:
    _allowed_origins = [o.strip() for o in _ALLOWED_ORIGINS_ENV.split(",") if o.strip()]
elif _ENVIRONMENT == "production":
    _allowed_origins = []
    logging.getLogger(__name__).warning(
        "[startup] FRONTEND_ORIGIN not set in production — CORS restricted to same-origin only"
    )
else:
    _allowed_origins = ["*"]
    logging.getLogger(__name__).info(
        "[startup] Development mode: CORS allow all origins"
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 阶段2 Task 2.3: JWT 鉴权中间件(校验 access token + 注入身份),
# 保留 X-API-Key(BACKEND_API_KEY)运维通道。位置: RateLimit 之后, CORS 之前。
from .middleware.auth import JWTAuthMiddleware

app.add_middleware(JWTAuthMiddleware)

# Rate limit middleware (protects LLM generation + secret endpoints)
from .middleware.rate_limit import RateLimitMiddleware

app.add_middleware(RateLimitMiddleware)

# Trace ID middleware (最外层：为每个请求绑定 trace_id，贯穿日志)
# 必须最后 add（Starlette 反转顺序，最后 add = 最外层执行）
from .middleware.trace import TraceMiddleware

app.add_middleware(TraceMiddleware)

# [阶段0] 静态文件挂载(/static /assets)已删(前后端分离,后端不再托管前端)
# [阶段0] frontend_watcher_thread / last_build_time 全局已删
# [阶段0] build_frontend() 函数已删(后端不再构建前端,由 CI/Vercel 等独立构建)
# [阶段0] watch_frontend_files() 轮询热构建函数已删

def _ensure_columns_safe(eng, inspector, columns=None):
    """Ensure new columns exist on existing tables.
    
    Works with both SQLite and PostgreSQL.
    Uses PRAGMA table_info for SQLite, information_schema for PG.
    Silently skips if column already exists.
    """
    from sqlalchemy import text as sa_text
    
    # Format: (table_name, column_name, column_type_sql)
    REQUIRED_COLUMNS = columns or [
        ("ai_strategies", "target_symbols", "TEXT"),
        ("ai_strategies", "primary_symbol", "VARCHAR(20) DEFAULT 'BTC'"),
        ("ai_strategies", "timeframe", "VARCHAR(10) DEFAULT '15m'"),
        ("ai_strategies", "max_leverage", "FLOAT DEFAULT 3.0"),
        ("ai_strategies", "default_leverage", "FLOAT DEFAULT 1.0"),
        ("ai_strategies", "leverage_mode", "VARCHAR(20) DEFAULT 'cross'"),
        ("ai_strategies", "snowball_enabled", "BOOLEAN DEFAULT 0"),
        ("ai_strategies", "snowball_max_adds", "INTEGER DEFAULT 3"),
        ("ai_strategies", "snowball_profit_threshold", "FLOAT DEFAULT 0.05"),
        ("ai_strategies", "auto_mode", "VARCHAR(20) DEFAULT 'semi_auto'"),
        ("ai_strategies", "analysis_intervals", "TEXT"),
        ("ai_strategies", "last_short_analysis_at", "TIMESTAMP"),
        ("ai_strategies", "last_mid_analysis_at", "TIMESTAMP"),
        ("ai_strategies", "last_long_analysis_at", "TIMESTAMP"),
        ("ai_strategies", "analysis_results_cache", "TEXT"),
        ("accounts", "trading_mode", "VARCHAR(10) DEFAULT 'live'"),
        ("arbitrage_profiles", "paper_account_mode", "VARCHAR(32) DEFAULT 'legacy_ai_paper'"),
        ("arbitrage_profiles", "arbitrage_paper_account_id", "INTEGER"),
        ("full_auto_sessions", "paper_account_mode", "VARCHAR(32) DEFAULT 'legacy_ai_paper'"),
        ("full_auto_sessions", "arbitrage_paper_account_id", "INTEGER"),
        ("prompt_templates", "required_placeholders", "TEXT"),
        ("prompt_templates", "is_legacy", "VARCHAR(10) DEFAULT 'true'"),
        ("prompt_templates", "updated_by", "VARCHAR(100)"),
        ("llm_configurations", "model_deep", "VARCHAR(100)"),
        ("strategy_memories", "performance_by_freq", "JSON"),
        # performance_by_regime: 与 performance_by_freq 同为后加的 JSON 列，
        # 被 strategy_learning_service / unified_learning_service / strategy_coordinator
        # 等多处读写。旧库升级时缺列会报 operational error，这里幂等补齐。
        ("strategy_memories", "performance_by_regime", "JSON"),
        # VIP 共用 AI 选币开关
        ("users", "coin_select_enabled", "VARCHAR(10) DEFAULT 'false'"),
        ("users", "coin_select_auto_follow", "VARCHAR(10) DEFAULT 'false'"),
        ("users", "coin_select_default_session", "VARCHAR(64)"),
        ("accounts", "ai_coin_select_enabled", "VARCHAR(10) DEFAULT 'false'"),
        ("full_auto_sessions", "auto_coin_max_slots", "INTEGER DEFAULT 5"),
    ]
    
    is_sqlite = str(eng.url).startswith("sqlite")
    
    with eng.begin() as conn:
        for table, col_name, col_def in REQUIRED_COLUMNS:
            try:
                if is_sqlite:
                    result = conn.execute(sa_text(f"PRAGMA table_info({table})"))
                    existing = {row[1] for row in result}
                else:
                    result = conn.execute(sa_text(
                        f"SELECT column_name FROM information_schema.columns "
                        f"WHERE table_name = '{table}'"
                    ))
                    existing = {row[0] for row in result}
                
                if col_name not in existing:
                    conn.execute(sa_text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}"))
                    logger.info(f"[startup] ✅ Added column {table}.{col_name}")
                    
                    # Set defaults for existing rows
                    if col_name == "target_symbols":
                        conn.execute(sa_text(
                            f"UPDATE {table} SET {col_name} = '[\"BTC\"]' WHERE {col_name} IS NULL"
                        ))
                    elif col_name == "primary_symbol":
                        conn.execute(sa_text(
                            f"UPDATE {table} SET {col_name} = 'BTC' WHERE {col_name} IS NULL"
                        ))
                    elif col_name == "timeframe":
                        conn.execute(sa_text(
                            f"UPDATE {table} SET {col_name} = '15m' WHERE {col_name} IS NULL"
                        ))
            except Exception as e:
                # Column might already exist or table doesn't exist - both are fine
                logger.info(f"[startup] Column check {table}.{col_name}: {e}")


@app.on_event("startup")
def on_startup():
    # [2026-07-12 修复] 增大 anyio 线程池容量（默认 40）。
    # LLM 流式调用大量使用 asyncio.to_thread，占满默认线程池后，
    # 同步 API 端点（如 /api/account/list）排队等待 5-9s → 前端超时。
    try:
        import anyio.to_thread
        anyio.to_thread.current_default_thread_limiter().total_tokens = 100
        logger.info("[Startup] anyio 线程池容量设为 100 (默认40)")
    except Exception as _e:
        logger.debug(f"[Startup] anyio 线程池设置跳过: {_e}")

    # [阶段0] 前端文件监视线程启动块已删(后端不再热构建前端,前端由独立构建/托管)

    # Create tables — multi-database support (V4 §3.7)
    # 确保 strategic_analyst ORM 模型已注册到 AnalyticsBase.metadata
    try:
        from backend.services.strategic_analyst.db_models import (  # noqa: F401
            CrossMarketCorrelationRecord,
            MacroRegimeStateRecord,
            NewCoinOpportunityRecord,
            StrategicMacroSnapshot,
            StrategicMemoryRecord,
            StrategicReportRecord,
            TrendPredictionRecord,
        )
    except Exception:
        try:
            from services.strategic_analyst.db_models import (  # noqa: F401
                CrossMarketCorrelationRecord,
                MacroRegimeStateRecord,
                NewCoinOpportunityRecord,
                StrategicMacroSnapshot,
                StrategicMemoryRecord,
                StrategicReportRecord,
                TrendPredictionRecord,
            )
        except Exception as _db_model_err:
            logger.warning(f"[startup] 战略分析师 ORM 模型导入失败: {_db_model_err}")
    try:
        from backend.services.mlto.db_models import (  # noqa: F401
            MltoDebateLog,
            MltoMemoryEvent,
            MltoSignalWeight,
            MltoThesis,
            MltoThesisEvent,
        )
    except Exception as _mlto_err:
        logger.warning(f"[startup] MLTO ORM 导入失败: {_mlto_err}")
    # 确保 VIP 选币表注册到 metadata（create_all 可见）
    try:
        from backend.database.models import (  # noqa: F401
            CoinSelectAdoption,
            CoinSelectCandidate,
            CoinSelectScan,
        )
    except Exception as _cs_err:
        logger.warning(f"[startup] CoinSelect ORM 导入失败: {_cs_err}")
    Base.metadata.create_all(bind=engine)
    MarketBase.metadata.create_all(bind=market_engine)
    AnalyticsBase.metadata.create_all(bind=analytics_engine)

    # 幂等补齐 Hyperliquid 快照表（修复 no such table: hyperliquid_account_snapshots）
    try:
        from database.models import HyperliquidAccountSnapshot, HyperliquidPosition
        HyperliquidAccountSnapshot.__table__.create(bind=engine, checkfirst=True)
        HyperliquidPosition.__table__.create(bind=engine, checkfirst=True)
        logger.info("[startup] Hyperliquid 快照表已确认存在(主库)")
    except Exception as _hl_err:
        logger.info(f"[startup] Hyperliquid 表补齐跳过(主库,非致命): {_hl_err}")

    # 资产曲线 / WS get_snapshot 走独立快照库，须单独建表
    try:
        from database.snapshot_connection import create_snapshot_database_if_missing, snapshot_engine
        from database.snapshot_models import HyperliquidAccountSnapshot as SnapHLAccount
        from database.snapshot_models import HyperliquidTrade
        create_snapshot_database_if_missing()
        SnapHLAccount.__table__.create(bind=snapshot_engine, checkfirst=True)
        HyperliquidTrade.__table__.create(bind=snapshot_engine, checkfirst=True)
        logger.info("[startup] Hyperliquid 快照表已确认存在(快照库)")
    except Exception as _snap_err:
        logger.info(f"[startup] Hyperliquid 表补齐跳过(快照库,非致命): {_snap_err}")

    # 量化框架升级开关状态（激进 rollout 可见性）
    try:
        from backend.config.framework_rollout import _AGGRESSIVE_DEFAULTS
        _rollout = {k: os.environ.get(k, "?") for k in sorted(_AGGRESSIVE_DEFAULTS)}
        logger.info("[FrameworkRollout] 当前生效开关: %s", _rollout)
    except Exception as _ro_err:
        logger.debug("[FrameworkRollout] 状态日志跳过: %s", _ro_err)

    # 整改#9 Phase 3：启动时 replay + DB→事件引导（投影为默认读路径预备）
    try:
        from backend.services.event_sourcing.phase3 import is_phase3_enabled, warm_startup_projection
        if is_phase3_enabled():
            from backend.database.connection import SessionLocal
            _es_db = SessionLocal()
            try:
                n = warm_startup_projection(_es_db)
                if n:
                    logger.info("[EventSourcing#9 Phase3] 启动引导 %d 个仓位进事件流", n)
            finally:
                _es_db.close()
    except Exception as _es3_err:
        logger.debug("[EventSourcing#9 Phase3] 启动预热跳过: %s", _es3_err)

    # ── 阶段1 任务1.3：下线内联 ALTER TABLE ─────────────────────────────
    # Alembic baseline (0001) 已接管 schema 后，遗留的启动期内联补丁
    # （schema_validator + _ensure_columns_safe）必须跳过：否则它们会与
    # Alembic 的 DDL 冲突或重复执行。仅在"尚未迁移"的旧库上继续兼容。
    # 注：_ensure_columns_safe / validate_and_sync_schema 仅操作 core(engine)
    #     与 analytics 库，因此按这两个库的 baseline 状态分别 gate。
    from backend.database.connection import alembic_at_baseline, alembic_at_rev
    _core_at_baseline = alembic_at_baseline(engine)
    _analytics_at_baseline = alembic_at_baseline(
        analytics_engine, version_table="alembic_version_analytics"
    )

    # ── 阶2 收口:迁移 0008 把下面的启动期内联 ALTER 收进了 Alembic ──────
    # 库已到 0008(或更晚)时,这些 schema 修补由迁移负责,启动期一律跳过,
    # schema 单一由 Alembic 管。按三个逻辑库分别判 rev(各自独立 version 表)。
    _core_at_0008 = alembic_at_rev(engine, "0008")
    _market_at_0008 = alembic_at_rev(
        market_engine, "0008", version_table="alembic_version_market"
    )
    _analytics_at_0008 = alembic_at_rev(
        analytics_engine, "0008", version_table="alembic_version_analytics"
    )

    # Run schema validator to auto-fix missing columns
    if _core_at_baseline:
        logger.info(
            "[startup] DB at Alembic baseline 0001 — core schema owned by Alembic, "
            "skipping legacy schema_validator"
        )
    else:
        try:
            from database.schema_validator import validate_and_sync_schema
            validate_and_sync_schema()
            logger.info(
                "[startup] Legacy schema_validator ran (core DB not at Alembic baseline)"
            )
        except Exception as e:
            logger.info(f"Schema validation error: {e}")

    # Auto-fix: ensure new columns exist on already-created tables (SQLite compatible)
    # core 库补丁
    if _core_at_baseline:
        logger.info(
            "[startup] DB at Alembic baseline 0001 — core schema owned by Alembic, "
            "skipping legacy _ensure_columns_safe (core)"
        )
    else:
        try:
            from sqlalchemy import inspect as sa_inspect
            inspector = sa_inspect(engine)
            _ensure_columns_safe(engine, inspector)
            logger.info(
                "[startup] Legacy _ensure_columns_safe ran on core "
                "(DB not at Alembic baseline)"
            )
        except Exception as e:
            logger.info(f"[startup] Column auto-fix error (non-fatal): {e}")

    # analytics 库补丁
    if _analytics_at_baseline:
        logger.info(
            "[startup] DB at Alembic baseline 0001 — analytics schema owned by Alembic, "
            "skipping legacy _ensure_columns_safe (analytics)"
        )
    else:
        try:
            from sqlalchemy import inspect as sa_inspect
            analytics_inspector = sa_inspect(analytics_engine)
            _ensure_columns_safe(analytics_engine, analytics_inspector, columns=[
                ("llm_usage_logs", "prompt_cache_hit_tokens", "INTEGER DEFAULT 0"),
                ("llm_usage_logs", "prompt_cache_miss_tokens", "INTEGER DEFAULT 0"),
                ("llm_usage_logs", "estimated_cost_cny", "FLOAT DEFAULT 0.0"),
                # [add] mlto_thesis 深度思维链快照列：捞回 reasoning 模型的 reasoning_content，
                # 供中长线 thesis 复盘/学习。schema_validator 不扫描 AnalyticsBase，
                # 故在此显式幂等补齐（ALTER TABLE ... ADD COLUMN，已存在则跳过）。
                ("mlto_thesis", "reasoning_snapshot", "TEXT"),
                # [v6 4.2] 注入的回测智慧 id 列表：qual_layer 注入时解析标记写入，
                # 平仓结算时读回评估智慧效果（evaluate_wisdom_result）。
                ("mlto_thesis", "wisdom_ids_json", "TEXT"),
            ])
            logger.info(
                "[startup] Legacy _ensure_columns_safe ran on analytics "
                "(DB not at Alembic baseline)"
            )
        except Exception as e:
            logger.info(f"[startup] Column auto-fix error (non-fatal): {e}")

    # [2026-07-09 性能修复] 创建热表索引（幂等 CREATE INDEX IF NOT EXISTS）
    # create_all 不会给已存在的表补索引，而 paper_orders/paper_positions 等热表
    # 缺索引导致 get_summary 的 count(*) 全表扫描（LeakGuard 日志 age 高达 133s）。
    try:
        from database.query_optimizer import create_missing_indexes, tune_autovacuum_for_hot_tables
        create_missing_indexes(engine)
        create_missing_indexes(market_engine)
        # [2026-07-11 修复] analytics 库（ai_decision_logs 等表所在库）此前从未跑过
        # 建索引：idx_decision_account_time 这条语句一直只对 engine/market_engine 执行，
        # 而 ai_decision_logs 实际在 analytics 库里，导致该索引从未真正创建成功
        # （每次都因"表不存在"被静默跳过）。补上对 analytics_engine 的调用。
        create_missing_indexes(analytics_engine)
        logger.info("[startup] 热表索引创建完成")

        # [2026-07-11 阶段2] 定时VACUUM的低风险落地：本机无pg_cron，收紧热表
        # autovacuum触发阈值代替显式定时任务，见函数文档注释。三个库都跑一遍，
        # 每个库里不存在的表会被静默跳过（同一份清单跨库复用）。
        tune_autovacuum_for_hot_tables(engine)
        tune_autovacuum_for_hot_tables(market_engine)
        tune_autovacuum_for_hot_tables(analytics_engine)
        logger.info("[startup] 热表autovacuum阈值调优完成")
    except Exception as e:
        logger.info(f"[startup] 索引创建错误(非致命): {e}")

    # [fix 2026-06-30] 扩容 signal_trade_feedback.signal_type: VARCHAR(30)→100
    # 旧值 30 太短，factor:cloud_microstructure_kyle 等 AI 生成因子名(含前缀32字符)
    # 导致 bulk_save 整批回滚 → 开仓零快照 → IC 闭环收不到样本。幂等扩容。
    #
    # [阶段2 收口] 此 widening 已并入迁移 0008;库到 0008+ 后 schema 由 Alembic 管,
    # 启动期跳过(仅未到 0008 的老库继续兼容)。
    if _core_at_0008:
        logger.info(
            "[startup] DB at Alembic rev 0008 — signal_trade_feedback.signal_type widening "
            "owned by Alembic, skipping legacy inline patch"
        )
    else:
        try:
            with engine.begin() as _conn:
                _cur_len = _conn.execute(text(
                    "SELECT character_maximum_length FROM information_schema.columns "
                    "WHERE table_name='signal_trade_feedback' AND column_name='signal_type'"
                )).scalar()
                if _cur_len and int(_cur_len) < 100:
                    _conn.execute(text(
                        "ALTER TABLE signal_trade_feedback ALTER COLUMN signal_type TYPE VARCHAR(100)"
                    ))
                    logger.info(f"[startup] ✅ signal_trade_feedback.signal_type 扩容 {int(_cur_len)}→100")
        except Exception as _e_st:
            logger.info(f"[startup] signal_type 扩容跳过(非致命，已有代码层截断兜底): {_e_st}")

    # DeepSeek: merge duplicate configs (same API key → one Flash+Pro entry)
    try:
        from backend.services.llm_config_consolidation import consolidate_deepseek_configs
        with SessionLocal() as _db:
            result = consolidate_deepseek_configs(_db)
            if result.get("groups_merged"):
                logger.info("[startup] DeepSeek 配置合并: %s", result)
    except Exception as e:
        logger.info(f"[startup] DeepSeek consolidate skipped: {e}")

    # 阶段4 Task 4.1: admin bootstrap。
    # 迁移 0006 已把 default 用户升 admin 并加了 role 列;但 default 创建时
    # password_hash=NULL,无法登录。这里按 ADMIN_INIT_PASSWORD env 设初始密码
    # (明文或 bcrypt hash 均可)。env 未设则只警告,不阻断启动。
    try:
        from backend.core.admin_bootstrap import ensure_admin_password
        with SessionLocal() as _db:
            _status = ensure_admin_password(_db)
            if _status == "ok-set":
                logger.info("[startup] admin bootstrap: 初始密码已从 ADMIN_INIT_PASSWORD 设置")
            elif _status == "ok-exists":
                logger.debug("[startup] admin bootstrap: default admin 已有密码,跳过")
            elif _status == "warn-no-env":
                logger.warning(
                    "[startup] admin bootstrap: default admin 无密码且 ADMIN_INIT_PASSWORD 未设 "
                    "— admin 无法登录;请设置 ADMIN_INIT_PASSWORD 或手动改密"
                )
            elif _status == "warn-no-user":
                logger.warning("[startup] admin bootstrap: default admin 用户不存在(迁移 0006 未跑?)")
    except Exception as _e_admin:
        logger.info(f"[startup] admin bootstrap 跳过(非致命): {_e_admin}")

    # Start DingTalk background tasks
    try:
        import asyncio

        from .services.dingtalk import get_background_tasks, get_volatility_monitor

        # [fix] P2-2: 添加停止信号，shutdown 时优雅取消后台循环
        _dingtalk_stop = asyncio.Event()

        # 在后台线程中启动异步任务
        def start_dingtalk_tasks():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            async def run_tasks():
                try:
                    # 启动后台任务
                    bg_tasks = get_background_tasks()
                    await bg_tasks.start()

                    # 启动波动监控器
                    volatility_monitor = get_volatility_monitor()
                    await volatility_monitor.start()

                    # 保持运行（可通过 _dingtalk_stop 信号优雅退出）
                    while not _dingtalk_stop.is_set():
                        await asyncio.wait_for(_dingtalk_stop.wait(), timeout=300)

                    # 收到停止信号 → 优雅关闭
                    logger.info("[Shutdown] DingTalk tasks stopping gracefully...")
                    await bg_tasks.stop()
                    await volatility_monitor.stop()

                except asyncio.TimeoutError:
                    pass  # 正常轮询
                except Exception as e:
                    logger.info(f"DingTalk tasks error: {e}")

            loop.run_until_complete(run_tasks())

        dingtalk_thread = threading.Thread(target=start_dingtalk_tasks, daemon=True)
        dingtalk_thread.start()
        logger.info("DingTalk notification background tasks started")

        # 注入到 app.state 供 shutdown 使用
        app.state._dingtalk_stop = _dingtalk_stop

    except Exception as e:
        logger.info(f"Failed to start DingTalk tasks: {e}")

    # Seed trading configs if empty
    db: Session = SessionLocal()
    try:
        # Skip PostgreSQL-specific migrations on SQLite
        is_sqlite = str(engine.url).startswith("sqlite")
        logger.info(f"[startup] Database type check: is_sqlite={is_sqlite}, url={str(engine.url)[:50]}")
        
        if not is_sqlite:
            # Ensure AI decision log table has snapshot columns (backport for PostgreSQL)
            # ai_decision_logs 在 Analytics 数据库中
            #
            # [阶段2 收口] 迁移 0008 已把这 3 列并入 Alembic;analytics 库到 0008+ 后
            # 启动期跳过,仅未到 0008 的老库继续兼容。
            if _analytics_at_0008:
                logger.info(
                    "[startup] DB at Alembic rev 0008 — ai_decision_logs snapshot columns "
                    "owned by Alembic, skipping legacy inline patch"
                )
            else:
                try:
                    with analytics_engine.begin() as analytics_conn:
                        result = analytics_conn.execute(text("""
                            SELECT column_name FROM information_schema.columns
                            WHERE table_name = 'ai_decision_logs'
                        """))
                        columns = {row[0] for row in result}

                        if "prompt_snapshot" not in columns:
                            analytics_conn.execute(text("ALTER TABLE ai_decision_logs ADD COLUMN prompt_snapshot TEXT"))
                        if "reasoning_snapshot" not in columns:
                            analytics_conn.execute(text("ALTER TABLE ai_decision_logs ADD COLUMN reasoning_snapshot TEXT"))
                        if "decision_snapshot" not in columns:
                            analytics_conn.execute(text("ALTER TABLE ai_decision_logs ADD COLUMN decision_snapshot TEXT"))
                    logger.info("[startup] Analytics schema migration (ai_decision_logs) completed")
                except Exception as migration_err:
                    logger.info(f"[startup] Failed to ensure AI decision log snapshot columns: {migration_err}")

            # 三周期独立分析字段迁移（short/mid/long bias + confidence）
            #
            # [阶段2 收口] 迁移 0008 已把这 6 列并入 Alembic;analytics 库到 0008+ 后跳过。
            if _analytics_at_0008:
                logger.info(
                    "[startup] DB at Alembic rev 0008 — ai_decision_logs 三周期字段 "
                    "owned by Alembic, skipping legacy inline patch"
                )
            else:
                try:
                    with analytics_engine.begin() as analytics_conn:
                        result = analytics_conn.execute(text("""
                            SELECT column_name FROM information_schema.columns
                            WHERE table_name = 'ai_decision_logs'
                        """))
                        columns = {row[0] for row in result}
                        _tf_cols = [
                            ("short_bias", "VARCHAR(20)"),
                            ("short_confidence", "FLOAT"),
                            ("mid_bias", "VARCHAR(20)"),
                            ("mid_confidence", "FLOAT"),
                            ("long_bias", "VARCHAR(20)"),
                            ("long_confidence", "FLOAT"),
                        ]
                        for _col, _typ in _tf_cols:
                            if _col not in columns:
                                analytics_conn.execute(text(f"ALTER TABLE ai_decision_logs ADD COLUMN {_col} {_typ}"))
                    logger.info("[startup] AI决策日志三周期字段迁移完成 (short/mid/long bias+confidence)")
                except Exception as migration_err:
                    logger.info(f"[startup] 三周期字段迁移跳过或失败: {migration_err}")

            # Ensure global_sampling_configs has sampling_depth column
            #
            # [阶段2 收口] 迁移 0008 已把这列并入 Alembic;core 库到 0008+ 后跳过。
            if _core_at_0008:
                logger.info(
                    "[startup] DB at Alembic rev 0008 — global_sampling_configs.sampling_depth "
                    "owned by Alembic, skipping legacy inline patch"
                )
            else:
                try:
                    result = db.execute(text("""
                        SELECT column_name FROM information_schema.columns
                        WHERE table_name = 'global_sampling_configs'
                    """))
                    columns = {row[0] for row in result}

                    if "sampling_depth" not in columns:
                        db.execute(text("ALTER TABLE global_sampling_configs ADD COLUMN sampling_depth INTEGER NOT NULL DEFAULT 10"))
                        logger.info("[startup] Added sampling_depth column to global_sampling_configs")
                    db.commit()
                except Exception as migration_err:
                    db.rollback()
                    logger.info(f"[startup] Failed to ensure global_sampling_configs.sampling_depth: {migration_err}")

            # Ensure crypto_klines has exchange & environment columns
            # crypto_klines 在 Market 数据库中
            # [fix] P0-2: 先建表再追加列，避免 PostgreSQL 下 "relation does not exist"
            #
            # [阶段2 收口] 迁移 0008 已把 exchange/environment 列及索引并入 Alembic;
            # market 库到 0008+ 后跳过。注:crypto_klines 表本体仍由 baseline create_all
            # 兜底(0008 只补列/索引,不负责建表)。
            if _market_at_0008:
                logger.info(
                    "[startup] DB at Alembic rev 0008 — crypto_klines exchange/environment columns "
                    "owned by Alembic, skipping legacy inline patch"
                )
            else:
                try:
                    with market_engine.begin() as market_conn:
                        market_conn.execute(text("""
                            CREATE TABLE IF NOT EXISTS crypto_klines (
                                id SERIAL PRIMARY KEY,
                                symbol VARCHAR(20) NOT NULL,
                                market VARCHAR(20) NOT NULL DEFAULT 'CRYPTO',
                                period VARCHAR(10) NOT NULL,
                                timestamp BIGINT NOT NULL,
                                open DOUBLE PRECISION,
                                high DOUBLE PRECISION,
                                low DOUBLE PRECISION,
                                close DOUBLE PRECISION,
                                volume DOUBLE PRECISION,
                                exchange VARCHAR(20) NOT NULL DEFAULT 'binance',
                                environment VARCHAR(20) NOT NULL DEFAULT 'mainnet',
                                created_at TIMESTAMP DEFAULT NOW()
                            )
                        """))
                        logger.info("[startup] crypto_klines table ensured (CREATE IF NOT EXISTS)")
                        result = market_conn.execute(text("""
                            SELECT column_name FROM information_schema.columns
                            WHERE table_name = 'crypto_klines'
                        """))
                        columns = {row[0] for row in result}

                        if "exchange" not in columns:
                            market_conn.execute(text("""
                                ALTER TABLE crypto_klines
                                ADD COLUMN exchange VARCHAR(20) NOT NULL DEFAULT 'binance'
                            """))
                            market_conn.execute(text("""
                                CREATE INDEX IF NOT EXISTS idx_crypto_klines_exchange ON crypto_klines(exchange)
                            """))
                            logger.info("[startup] Added exchange column to crypto_klines")

                        if "environment" not in columns:
                            market_conn.execute(text("""
                                ALTER TABLE crypto_klines
                                ADD COLUMN environment VARCHAR(20) NOT NULL DEFAULT 'mainnet'
                            """))
                            market_conn.execute(text("""
                                CREATE INDEX IF NOT EXISTS idx_crypto_klines_environment ON crypto_klines(environment)
                            """))
                            market_conn.execute(text("""
                                CREATE INDEX IF NOT EXISTS idx_crypto_klines_symbol_period_env ON crypto_klines(symbol, period, environment)
                            """))
                            logger.info("[startup] Added environment column to crypto_klines")
                    logger.info("[startup] Market schema migration (crypto_klines) completed")
                except Exception as migration_err:
                    logger.info(f"[startup] Failed to ensure crypto_klines columns: {migration_err}")
        else:
            # SQLite: 确保唯一索引存在（INSERT OR IGNORE 需要它）
            try:
                db.execute(text("""
                    CREATE UNIQUE INDEX IF NOT EXISTS
                    uix_klines_exchange_symbol_market_period_ts_env
                    ON crypto_klines(exchange, symbol, market, period, timestamp, environment)
                """))
                db.commit()
                logger.info("[startup] SQLite unique index on crypto_klines ensured")
            except Exception as idx_err:
                db.rollback()
                logger.info(f"[startup] SQLite index check: {idx_err}")

        if db.query(TradingConfig).count() == 0:
            for cfg in DEFAULT_TRADING_CONFIGS.values():
                db.add(
                    TradingConfig(
                        version="v1",
                        market=cfg.market,
                        min_commission=cfg.min_commission,
                        commission_rate=cfg.commission_rate,
                        exchange_rate=cfg.exchange_rate,
                        min_order_quantity=cfg.min_order_quantity,
                        lot_size=cfg.lot_size,
                    )
                )
            db.commit()

        # Ensure default user exists
        default_user = db.query(User).filter(User.username == "default").first()
        if not default_user:
            default_user = User(
                username="default",
                email=None,
                password_hash=None,
                is_active="true"
            )
            db.add(default_user)
            db.commit()
            db.refresh(default_user)
        
        # No default account creation - users must create their own accounts

    finally:
        db.close()

    # ============================================================
    # Upgrade: Initialize Hyperliquid trading mode config & fix NULL environment data
    # ============================================================
    db = SessionLocal()
    try:
        is_sqlite = str(engine.url).startswith('sqlite')
        
        if not is_sqlite:
            # Step 1: Initialize hyperliquid_trading_mode config if missing
            config = db.query(SystemConfig).filter(
                SystemConfig.key == "hyperliquid_trading_mode"
            ).first()

            if not config:
                config = SystemConfig(
                    key="hyperliquid_trading_mode",
                    value="testnet",
                    description="Global Hyperliquid trading environment: 'testnet' or 'mainnet'. Controls which network all AI Traders connect to."
                )
                db.add(config)
                db.commit()
                logger.info("✓ [Upgrade] Initialized global hyperliquid_trading_mode to 'testnet'")
            else:
                logger.info(f"[OK] [Upgrade] Global hyperliquid_trading_mode already configured: {config.value}")

            # Step 2: One-time migration - fix NULL hyperliquid_environment in ai_decision_logs
            try:
                with analytics_engine.begin() as analytics_conn:
                    null_count = analytics_conn.execute(text("""
                        SELECT COUNT(*) FROM ai_decision_logs WHERE hyperliquid_environment IS NULL
                    """)).scalar()

                    if null_count > 0:
                        logger.info(f"[WARNING] [Upgrade] Found {null_count} ai_decision_logs with NULL hyperliquid_environment, fixing...")
                        analytics_conn.execute(text("""
                            UPDATE ai_decision_logs
                            SET hyperliquid_environment = 'testnet'
                            WHERE hyperliquid_environment IS NULL
                        """))
                        logger.info(f"[OK] [Upgrade] Updated {null_count} records from NULL to 'testnet' (ModelChat fix)")
                    else:
                        logger.info("[OK] [Upgrade] No NULL hyperliquid_environment records found, data is clean")
            except Exception as e:
                logger.info(f"[ERROR] [Upgrade] Hyperliquid environment upgrade failed: {e}")
        else:
            logger.info("[startup] Skipping Hyperliquid migrations (SQLite mode)")

    except Exception as e:
        db.rollback()
        logger.info(f"[ERROR] [Upgrade] Hyperliquid environment upgrade failed: {e}")
    finally:
        db.close()

    # Ensure prompt templates exist
    db = SessionLocal()
    try:
        from services.prompt_initializer import seed_prompt_templates
        seed_prompt_templates(db)
    finally:
        db.close()

    # [2026-07-30] OpenCode prompts 已禁用
    # try:
    #     from scripts.emit_opencode_prompts import emit_all
    #     if emit_all(quiet=True) == 0:
    #         logger.info("[startup] OpenCode prompts synced to backend/prompts/")
    # except Exception as emit_err:
    #     logger.info(f"[startup] OpenCode prompt emit skipped: {emit_err}")
    
    # Initialize system log collector
    from services.system_logger import setup_system_logger
    setup_system_logger()

    # Load and apply global sampling configuration (use watchlist if available)
    try:
        from database.models import GlobalSamplingConfig
        from services.hyperliquid_symbol_service import get_selected_symbols as get_hyperliquid_selected_symbols
        from services.sampling_pool import sampling_pool
        from services.trading_commands import AI_TRADING_SYMBOLS

        db = SessionLocal()
        try:
            symbols = get_hyperliquid_selected_symbols() or AI_TRADING_SYMBOLS
            global_config = db.query(GlobalSamplingConfig).first()
            if global_config and global_config.sampling_depth:
                for symbol in symbols:
                    sampling_pool.set_max_samples(symbol, global_config.sampling_depth)
                logger.info(f"[OK] Sampling pool configured: depth={global_config.sampling_depth} for {len(symbols)} symbols")
            else:
                logger.info(f"⚠ No global sampling config found, using default depth={sampling_pool.default_max_samples} for {len(symbols)} symbols")
        finally:
            db.close()
    except Exception as e:
        logger.info(f"[ERROR] Failed to load global sampling config: {e}")

    # Clean up any leftover backfill tasks from previous runs
    try:
        from database.models import KlineCollectionTask
        db = MarketSessionLocal()
        try:
            # Delete all running and pending backfill tasks
            deleted_count = db.query(KlineCollectionTask).filter(
                KlineCollectionTask.status.in_(['running', 'pending'])
            ).delete(synchronize_session=False)
            db.commit()
            if deleted_count > 0:
                logger.info(f"[OK] Cleaned up {deleted_count} leftover backfill tasks")
        finally:
            db.close()
    except Exception as e:
        logger.info(f"⚠ Failed to clean up backfill tasks: {e}")

    # Initialize services
    # 同步服务在当前线程初始化，async 服务（如K线采集器）用 create_task 在主事件循环启动
    import asyncio as _startup_asyncio
    
    def _init_sync_services():
        """初始化同步服务（在后台线程运行）"""
        import time
        time.sleep(1)
        try:
            logger.info("[后台] 正在初始化同步服务...")
            from services.startup import initialize_sync_services
            initialize_sync_services()

            # D2: 注入 SystemCoordinator 到 TradingDecisionInterface
            # 修复前 Kelly/DRL/PortfolioRisk 永久 pass-through
            try:
                from backend.config.settings import ENABLE_COORDINATOR
                if ENABLE_COORDINATOR:
                    from backend.services.rl.system_coordinator import system_coordinator
                    from backend.services.trading_decision_interface import inject_coordinator
                    inject_coordinator(system_coordinator)
                    logger.info("[后台] SystemCoordinator 已注入到 TDI, DRL/Kelly/PortfolioRisk 已激活")
            except Exception as _tdi_err:
                logger.warning(f"[后台] TDI Coordinator 注入失败: {_tdi_err}")

            logger.info("[后台] 同步服务初始化完成")

            # 阶段4：数据中台启动门禁（错源硬检 + 覆盖率软告警）
            try:
                from backend.services.data_center_gate import run_startup_gate
                _gate = run_startup_gate(block_on_hard_fail=False)
                if _gate.get("hard_ok"):
                    logger.info("[后台] DataCenterGate 硬门通过")
                else:
                    logger.error("[后台] DataCenterGate 硬门失败: %s", _gate.get("checks"))
            except Exception as _gate_err:
                logger.warning(f"[后台] DataCenterGate 跳过: {_gate_err}")

            # ── 套利域插件 bootstrap ──
            try:
                from qaa.domains.arbitrage.plugin import ArbitragePlugin
                _arb_plugin = ArbitragePlugin()
                logger.info(f"[后台] ArbitragePlugin 已加载: domain={_arb_plugin.domain_id}, "
                            f"agents={list(_arb_plugin.get_agent_cards().keys())}")
            except Exception as _arb_err:
                logger.debug(f"[后台] ArbitragePlugin 加载跳过: {_arb_err}")

        except Exception as e:
            logger.info(f"[后台] 同步服务初始化失败: {e}")
            import traceback
            traceback.print_exc()
    
    services_thread = threading.Thread(target=_init_sync_services, name="services-init", daemon=True)
    services_thread.start()

    async def _run_full_history_backfill():
        """根因2修复：后台执行全历史数据回填（不阻塞启动）。"""
        await _startup_asyncio.sleep(10)  # 等实时采集器先稳定
        try:
            from backend.services.data.backfill_full_history import backfill_all
            report = await backfill_all(
                symbols=["BTC-PERP", "ETH-PERP"],
                periods=["1d", "4h", "1h", "5m"],
            )
            logger.info(
                f"[async] 全历史回填完成: 检查 {len(report.get('checked', []))}, "
                f"回填 {len(report.get('backfilled', []))}, "
                f"错误 {len(report.get('errors', []))}"
            )
        except Exception as e:
            logger.warning(f"[async] 全历史回填异常: {e}", exc_info=False)

    # 异步服务（K线采集器等）直接在主事件循环启动
    async def _start_async_services():
        """启动需要事件循环的异步服务"""
        await _startup_asyncio.sleep(3)  # 等待同步服务先初始化

        # 独立数据中心进程模式：采集由 backend.workers.market_data_center 负责，
        # 主 API 只读 Market DB，重启交易服务不再中断行情写入。
        _dc_mode = (os.environ.get("DATA_CENTER_MODE") or "embedded").strip().lower()
        _dc_external = _dc_mode in ("standalone", "external", "worker", "separate")
        if _dc_external:
            logger.info(
                "[async] DATA_CENTER_MODE=%s → 跳过内嵌 K线/Ticker/LiveKline/回填/新鲜度巡检 "
                "（请确认 scripts/start-data-center.bat 已在跑，health :9100）",
                _dc_mode,
            )

        if not _dc_external:
            try:
                from services.kline_realtime_collector import realtime_collector
                await realtime_collector.start()
            except Exception as e:
                logger.info(f"[async] K线采集器启动失败: {e}")
                import traceback
                traceback.print_exc()

            # 秒级实时行情：asterdex 2s ticker 轮询 + 当前 K 线引擎
            try:
                from backend.services.asterdex_ticker_poller import asterdex_ticker_poller
                asterdex_ticker_poller.start()
                logger.info("[async] AsterdexTickerPoller 秒级轮询已启动")
            except Exception as e:
                logger.info(f"[async] AsterdexTickerPoller 启动失败: {e}")
            try:
                from backend.services.live_kline_engine import live_kline_engine
                live_kline_engine.start()
                logger.info("[async] LiveKlineEngine 已启动")
            except Exception as e:
                logger.info(f"[async] LiveKlineEngine 启动失败: {e}")
        else:
            # 仍可读；不启动写端
            pass

        # 总览多交易所 24h 数据后台预热（避免 binance/okx/hyperliquid 首次请求等待 4~20s）
        try:
            from backend.api.market_data_routes import start_overview_warmup
            start_overview_warmup()
            logger.info("[async] MarketOverview 多交易所预热已启动")
        except Exception as e:
            logger.info(f"[async] MarketOverview 预热启动失败: {e}")

        # M1 深度回填：启动即后台补 5m/15m/1h 等目标深度
        if not _dc_external:
            try:
                from backend.services.kline_history_sync import depth_backfill_runner
                depth_backfill_runner.start()
                logger.info("[async] KlineDepthBackfill 深度回填已启动")
            except Exception as e:
                logger.info(f"[async] KlineDepthBackfill 启动失败: {e}")

        # M3 因子暴露快照（每 10 分钟，热币）
        try:
            from backend.services.factor_engine.exposure_service import (
                FEATURE_FACTOR_EXPOSURE_ENABLED,
                factor_exposure_service,
            )
            if FEATURE_FACTOR_EXPOSURE_ENABLED:
                from backend.services.scheduler import task_scheduler as _expo_sched
                _expo_sched.start()

                def _exposure_snapshot_tick():
                    try:
                        from backend.services.kline_realtime_collector import (
                            get_trade_universe_symbols,
                        )
                        syms = list(get_trade_universe_symbols() or [])[:60]
                        factor_exposure_service.snapshot(syms, ["5m", "15m", "1h", "4h"])
                    except Exception:
                        pass

                _expo_sched.add_interval_task(
                    task_func=_exposure_snapshot_tick,
                    interval_seconds=600,
                    task_id="factor_exposure_snapshot",
                    max_instances=1,
                )
                logger.info("[async] FactorExposure 快照任务已注册（600s）")
        except Exception as e:
            logger.info(f"[async] FactorExposure 快照注册失败: {e}")

        # 根因2修复：全历史数据回填纳入自动启动流程。
        # 数据中台整改：系统启动时自动检查并补齐全历史数据（上市日起），
        # 而非仅靠实时采集器补当前分钟。受 KLINE_FULL_HISTORY_AUTOFILL 控制（默认开）。
        # 独立数据中心模式下由 worker 负责，避免双进程抢写。
        if (not _dc_external) and os.environ.get("KLINE_FULL_HISTORY_AUTOFILL", "true").lower() in ("1", "true", "yes", "on"):
            try:
                # 异步触发，不阻塞启动（回填在后台跑）
                _startup_asyncio.create_task(
                    _run_full_history_backfill(),
                    name="full-history-backfill",
                )
                logger.info("[async] 全历史数据回填已调度（后台执行）")
            except Exception as e:
                logger.info(f"[async] 全历史回填调度失败: {e}")

        # S1: 启动事件总线 (EventBus)
        try:
            from backend.services.event_bus import event_bus
            await event_bus.start()
            logger.info("[async] EventBus 事件总线已启动")
        except Exception as e:
            logger.info(f"[async] EventBus 启动失败: {e}")

        # S1: 启动事件总线 (EventBus)
        try:
            from backend.services.event_bus import event_bus
            await event_bus.start()
            logger.info("[async] EventBus 事件总线已启动")
        except Exception as e:
            logger.info(f"[async] EventBus 启动失败: {e}")

        # S2: 启动自动选币调度器 (AutoCoinScheduler)
        # AUTO_COIN_ENABLED 总开关（默认 true 保持历史行为）：false 时不启动调度器
        try:
            from backend.config.settings import AUTO_COIN_ENABLED
            if AUTO_COIN_ENABLED:
                from backend.services.auto_coin_selector import auto_coin_scheduler
                await auto_coin_scheduler.start()
                logger.info("[async] AutoCoinScheduler 自动选币调度器已启动")
            else:
                logger.info("[async] AutoCoinScheduler 已被 AUTO_COIN_ENABLED=false 禁用")
        except Exception as e:
            logger.info(f"[async] AutoCoinScheduler 启动失败: {e}")

        # VIP 共用 AI 选币（平台看板，管理员 LLM）
        try:
            from backend.config.settings import COIN_SELECT_PLATFORM_ENABLED
            if COIN_SELECT_PLATFORM_ENABLED:
                from backend.services.coin_select_platform_service import coin_select_platform_scheduler
                await coin_select_platform_scheduler.start()
                logger.info("[async] CoinSelectPlatformScheduler 已启动")
            else:
                logger.info("[async] CoinSelectPlatformScheduler 已禁用")
        except Exception as e:
            logger.info(f"[async] CoinSelectPlatformScheduler 启动失败: {e}")

        # CoinRank 反馈回写（24h/72h hit）
        try:
            from backend.services.coin_rank.feedback import coin_rank_feedback_scheduler
            await coin_rank_feedback_scheduler.start()
            logger.info("[async] CoinRankFeedbackScheduler 已启动")
        except Exception as e:
            logger.info(f"[async] CoinRankFeedbackScheduler 启动失败: {e}")

        # 市场数据质量修复调度器：默认关闭，需显式环境变量开启。
        try:
            from backend.services.kline_quality_repair_service import kline_quality_repair_service
            result = kline_quality_repair_service.start()
            logger.info(f"[async] KlineQualityRepairService 启动检查: {result}")
        except Exception as e:
            logger.info(f"[async] KlineQualityRepairService 启动失败: {e}")

        # MarketData V2 raw event 旁路调度器：默认关闭，打开后持续写 raw_market_events。
        try:
            from backend.services.market_data_v2_scheduler import market_data_v2_scheduler
            result = market_data_v2_scheduler.start()
            logger.info(f"[async] MarketDataV2Scheduler 启动检查: {result}")
        except Exception as e:
            logger.info(f"[async] MarketDataV2Scheduler 启动失败: {e}")

        # K线数据新鲜度巡检：检测交易币种 K线缺失/停滞，critical 推飞书告警。
        # 解决「数据不全无法分辨、无法通知」的可观测性缺口。
        if not _dc_external:
            try:
                from backend.services.kline_freshness_inspector import kline_freshness_inspector
                result = kline_freshness_inspector.start()
                logger.info(f"[async] KlineFreshnessInspector 启动: {result}")
            except Exception as e:
                logger.info(f"[async] KlineFreshnessInspector 启动失败: {e}")

        # 市场数据高吞吐索引：幂等执行，避免多交易所写入和 shadow compare 卡住热路径。
        try:
            from backend.services.market_data_db_optimizer import market_data_db_optimizer
            result = market_data_db_optimizer.ensure_indexes()
            logger.info(f"[async] MarketDataDbOptimizer 索引检查: {result}")
        except Exception as e:
            logger.info(f"[async] MarketDataDbOptimizer 索引检查失败: {e}")

        # SnapshotStore 生产调度器：默认关闭，打开后让 UnifiedDataPool/QAA 有新底座快照可读。
        try:
            from backend.services.snapshot_scheduler import snapshot_scheduler
            result = snapshot_scheduler.start()
            logger.info(f"[async] SnapshotScheduler 启动检查: {result}")
        except Exception as e:
            logger.info(f"[async] SnapshotScheduler 启动失败: {e}")

        # 因子进化闭环：每日凌晨3点挖掘+清洗+上线，每小时在线权重更新
        try:
            from backend.services.evolution.factor_evolution_loop import (
                run_factor_evolution_loop,
                run_online_weight_update,
            )
            from backend.services.scheduler import task_scheduler
            task_scheduler.start()
            # 每日因子进化（凌晨3点）
            task_scheduler.add_cron_task(
                task_func=run_factor_evolution_loop,
                hour=3, minute=0,
                task_id="factor_evolution_daily",
                max_instances=1,
            )
            # 每小时在线权重更新
            task_scheduler.add_interval_task(
                task_func=run_online_weight_update,
                interval_seconds=3600,
                task_id="factor_online_weight_hourly",
                max_instances=1,
            )
            logger.info("[async] 因子进化闭环已注册（每日3点 + 每小时权重）")
        except Exception as e:
            logger.info(f"[async] 因子进化注册失败: {e}")

        # 算力中心历史指标采样线程（v6 第十章：60s 周期 CPU/内存/GPU 落库）
        try:
            from backend.services.compute.compute_metrics import start_sampler as _start_compute_sampler
            _start_compute_sampler()
        except Exception as _cs_err:
            logger.info(f"[async] 算力指标采样启动失败: {_cs_err}")

        # Universe品种筛选五步管线（规划文档§5.3，P1新增）：
        # 每周全量重建（周一凌晨2点，避开因子进化3点档），每4h流动性复查降级。
        # 启动时立即跑一次全量重建，保证刚重启就有真实数据可用，不用等到下周一。
        try:
            from backend.services.alpha.universe_manager import (
                run_universe_liquidity_recheck,
                run_universe_rebuild,
            )
            from backend.services.scheduler import task_scheduler
            task_scheduler.start()
            task_scheduler.add_cron_task(
                task_func=run_universe_rebuild,
                hour=2, minute=0, day_of_week="mon",
                task_id="universe_rebuild_weekly",
                max_instances=1,
            )
            task_scheduler.add_interval_task(
                task_func=run_universe_liquidity_recheck,
                interval_seconds=4 * 3600,
                task_id="universe_liquidity_recheck_4h",
                max_instances=1,
            )
            import threading as _threading
            _threading.Thread(target=run_universe_rebuild, daemon=True).start()
            logger.info("[async] Universe品种筛选管线已注册（每周一2点全量重建 + 每4h流动性复查，启动时已异步触发首次重建）")
        except Exception as e:
            logger.info(f"[async] Universe管线注册失败: {e}")

        # 日志 / 审计清理：防 JSONL 与报告无限堆砌撑盘
        try:
            from backend.services.log_retention_service import run_log_retention
            from backend.services.scheduler import task_scheduler
            task_scheduler.start()
            task_scheduler.add_cron_task(
                task_func=run_log_retention,
                hour=4, minute=30,
                task_id="log_retention_daily",
                max_instances=1,
            )
            import threading as _thr_log
            _thr_log.Thread(target=run_log_retention, daemon=True, name="log-retention-boot").start()
            logger.info("[async] 日志清理已注册（每日 4:30 + 启动时异步跑一轮）")
        except Exception as e:
            logger.info(f"[async] 日志清理注册失败: {e}")

        # Parity Score验证管线（规划文档§4.5，P3新增）：每周对比实盘成交与回测回放，
        # <0.85告警/<0.70冻结该nature新开仓。周日凌晨4点（避开因子进化3点、Universe周一2点档）。
        try:
            from backend.services.backtest_engine.parity_score import run_parity_score_pipeline
            from backend.services.scheduler import task_scheduler

            task_scheduler.start()
            task_scheduler.add_cron_task(
                task_func=run_parity_score_pipeline,
                hour=4, minute=0, day_of_week="sun",
                task_id="parity_score_weekly",
                max_instances=1,
            )
            logger.info("[async] Parity Score验证管线已注册（每周日4点）")
        except Exception as e:
            logger.info(f"[async] Parity Score管线注册失败: {e}")

        # 因子瘦身审计（规划文档§3.2，P0）：此前只有CLI手动跑过一次（--apply），
        # 从未挂进定时任务——脚本本身写完就一直"躺在那里"，不会自动淘汰新退化的因子。
        # 周二凌晨3点半（避开周一2点Universe重建、每天3点因子进化，错开负载高峰）。
        try:
            from backend.services.factor_engine.factor_slimming_audit import run_audit as _run_slimming_audit
            from backend.services.scheduler import task_scheduler as _slim_scheduler

            _slim_scheduler.start()
            _slim_scheduler.add_cron_task(
                task_func=_run_slimming_audit,
                hour=3, minute=30, day_of_week="tue",
                task_id="factor_slimming_weekly",
                max_instances=1,
                apply_changes=True,
            )
            logger.info("[async] 因子瘦身审计已注册（每周二3:30，apply=True）")
        except Exception as e:
            logger.info(f"[async] 因子瘦身审计注册失败: {e}")

        # 极端行情模拟压力测试（规划文档§4.3+§3.4验收，P1）：把插针/清算窗口喂给生产环境
        # ScalpExecutionGate/ExitStateMachine真实判定，验证硬拦截逻辑没有被后续改动破坏。
        # 之前只能手动CLI跑，同样从未挂定时任务。压力回归性质，周频即可，选周三3:45
        # 错开周二3:30因子瘦身、周日4:00 Parity Score、周一2:00 Universe重建。
        # [注] task_scheduler.add_cron_task 目前只暴露 hour/minute/second/day_of_week，
        # 没有"月第N日"语义，因此用周频代替规划里设想的月频，效果等价（更频繁的回归只会
        # 更早发现问题，不会更晚）。
        try:
            from backend.services.backtest_engine.extreme_scenario import run_and_save as _run_extreme_scenario
            from backend.services.scheduler import task_scheduler as _extreme_scheduler

            _extreme_scheduler.start()
            _extreme_scheduler.add_cron_task(
                task_func=_run_extreme_scenario,
                hour=3, minute=45, day_of_week="wed",
                task_id="extreme_scenario_weekly",
                max_instances=1,
                symbols=["BTC", "ETH", "SOL", "BNB", "XRP"],
            )
            logger.info("[async] 极端行情模拟压力测试已注册（每周三3:45）")
        except Exception as e:
            logger.info(f"[async] 极端行情模拟压力测试注册失败: {e}")

        # ── S3-3 止血修复（04 综合方案 §3.5 质量门）：中长线周度绩效报表 ──
        # 此前脚本写完只能手动跑，没有定时产出——"周度"报表不应要人手动记得跑。
        # 每周一 4:00（避开周二因子瘦身 3:30、周三极端场景 3:45 的负载窗口）
        # 生成近 14 天 swing/trend_follow 的胜率/盈亏比/同向再开率/分档TP触达率，
        # 写入 backend/data/midlong_reports/latest.md，供人工或后续告警读取。
        try:
            from backend.scripts.midlong_weekly_report import run_and_save as _run_midlong_report
            from backend.services.scheduler import task_scheduler as _midlong_report_scheduler

            _midlong_report_scheduler.start()
            _midlong_report_scheduler.add_cron_task(
                task_func=_run_midlong_report,
                hour=4, minute=0, day_of_week="mon",
                task_id="midlong_weekly_report",
                max_instances=1,
                days=14,
            )
            logger.info("[async] 中长线周度绩效报表已注册（每周一4:00）")
        except Exception as e:
            logger.info(f"[async] 中长线周度绩效报表注册失败: {e}")

        # ── P3：中长线 Walk-Forward 薄钩子（代理趋势策略，证明回测基建可挂）──
        # 紧接周报 4:00，周一 4:20 跑 BTC/ETH/SOL 日线 WFO，写 wfo_latest.json。
        try:
            from backend.scripts.midlong_walk_forward_hook import run_and_save as _run_midlong_wfo
            from backend.services.scheduler import task_scheduler as _midlong_wfo_scheduler

            _midlong_wfo_scheduler.start()
            _midlong_wfo_scheduler.add_cron_task(
                task_func=_run_midlong_wfo,
                hour=4, minute=20, day_of_week="mon",
                task_id="midlong_wfo_weekly",
                max_instances=1,
                symbols=["BTC", "ETH", "SOL"],
                lookback_days=400,
            )
            logger.info("[async] 中长线 Walk-Forward 已注册（每周一4:20）")
        except Exception as e:
            logger.info(f"[async] 中长线 Walk-Forward 注册失败: {e}")

    try:
        loop = _startup_asyncio.get_running_loop()
        loop.create_task(_start_async_services())
        logger.info("K线采集器已安排在事件循环中启动")
    except Exception as e:
        logger.info(f"无法在事件循环中启动异步服务: {e}")
    
    logger.info("Services initialization started")


@app.on_event("shutdown")
async def on_shutdown():
    from services.startup import shutdown_services
    await shutdown_services()

    # S1: 停止事件总线
    try:
        from backend.services.event_bus import event_bus
        await event_bus.stop()
    except Exception:
        pass

    # [fix] P2-2: 发送 DingTalk 停止信号
    try:
        _dt_stop = getattr(app.state, "_dingtalk_stop", None)
        if _dt_stop is not None:
            _dt_stop.set()
            logger.info("[Shutdown] DingTalk stop signal sent")
    except Exception:
        pass

    # Windows + uvicorn --reload：worker 优雅 shutdown 后若进程未硬退出，会留下孤儿
    # spawn 进程继续占 8000、scheduler 报 cannot schedule after shutdown（页面卡死）。
    if os.environ.get("RUN_MAIN") == "true" and sys.platform == "win32":
        # [fix] P1-3: 硬退出前先做优雅清理，防止丢失 DB 写入和 WAL 残留
        logger.info("[Shutdown] 优雅清理: 关闭 scheduler + 释放 DB 连接池...")
        try:
            from backend.services.scheduler import stop_scheduler
            stop_scheduler()
        except Exception:
            pass
        try:
            from backend.database.connection import analytics_engine as _ana_eng
            from backend.database.connection import engine as _core_eng
            from backend.database.connection import market_engine as _mkt_eng
            for _name, _eng in [("Core", _core_eng), ("Market", _mkt_eng), ("Analytics", _ana_eng)]:
                try:
                    _eng.dispose()
                    logger.debug("[Shutdown] %s engine disposed", _name)
                except Exception:
                    pass
        except Exception:
            pass
        logger.info("[Shutdown] reload worker hard-exit (Windows orphan prevention)")
        os._exit(0)


# API routes
from .api.account_routes import router as account_router
from .api.ai_trading_routes import router as ai_trading_router
from .api.arena_routes import router as arena_router
from .api.config_routes import router as config_router
from .api.crypto_routes import router as crypto_router
from .api.hyperliquid_action_routes import router as hyperliquid_action_router
from .api.hyperliquid_routes import router as hyperliquid_router
from .api.market_data_routes import router as market_data_router
from .api.market_data_v2_routes import router as market_data_v2_router
from .api.order_routes import router as order_router
from .api.prompt_routes import router as prompt_router
from .api.ranking_routes import router as ranking_router
from .api.sampling_routes import router as sampling_router
from .api.system_control_routes import router as system_control_router
from .api.system_log_routes import router as system_log_router

try:
    from .api.binance_routes import router as binance_router  # type: ignore
except ModuleNotFoundError:
    binance_router = None
from backend.api.gap_closure_routes import router as gap_closure_router
# [2026-08-06] learning_dashboard_api（P3.1 假仪表盘/假健康）已弃用：前端不再消费 /api/learning/dashboard/*，
# 真实健康由 /api/learning/health 提供。注销挂载，避免与 learning_core 重复接口混淆。
# from backend.services.learning_dashboard_api import router as learning_dashboard_router

from .api.ai_signal_prompt_integration_routes import router as ai_signal_prompt_integration_router
from .api.ai_strategy_routes import router as ai_strategy_router
from .api.analytics_routes import router as analytics_router
from .api.arbitrage_profile_routes import router as arbitrage_profile_router
from .api.auto_coin_routes import router as auto_coin_router
from .api.coin_select_routes import router as coin_select_router
from .api.backtest_routes import router as backtest_router
from .api.candlestick_pattern_routes import router as candlestick_pattern_router
from .api.comprehensive_analysis_routes import router as comprehensive_analysis_router
from .api.dashboard_routes import router as dashboard_router  # 交易矩阵仪表盘
from .api.dingtalk_routes import router as dingtalk_router
from .api.evolution_routes import router as evolution_router
from .api.full_auto_routes import router as full_auto_router
from .api.intelligence_routes import router as intelligence_router
from .api.intelligent_learning_routes import router as intelligent_learning_router
from .api.kline_analysis_routes import router as kline_analysis_router
from .api.kline_routes import router as kline_router
from .api.learning_core_routes import router as learning_core_router  # 统一进化学习内核
from .api.learning_loop_routes import router as learning_loop_router
from .api.learning_health_routes import router as learning_health_router  # v6 8.3: 真实闭环健康 + wisdom 五步统计
from .api.llm_config_routes import router as llm_config_router
from .api.llm_usage_routes import router as llm_usage_router
from .api.market_flow_routes import router as market_flow_router
from .api.market_regime_routes import router as market_regime_router
from .api.mlto_routes import router as mlto_router
from .api.paper_trading_routes import router as paper_trading_router
from .api.live_trading_routes import router as live_trading_router
from .api.parity_score_routes import router as parity_score_router
# 阶段 3 本地仓位协调器: 净仓位视图 / 子仓位分解 / 手动对账
from .api.position_routes import router as position_router
from .api.prompt_training_routes import router as prompt_training_router
from .api.rag_routes import router as rag_router
from .api.resonance_routes import router as resonance_router
from .api.risk_routes import router as risk_router
from .api.rl_routes import router as rl_router
from .api.signal_routes import router as signal_router
from .api.smart_signal_routes import router as smart_signal_router
from .api.strategy_template_routes import router as strategy_template_router
from .api.system_monitor_routes import router as system_monitor_router
from .api.user_routes import router as user_router
from .api.vault_routes import router as vault_router
# 阶段2 auth: register/login/refresh/logout/me (Task 2.2)
from backend.api.auth_routes import router as auth_router
# 阶段4 Task 4.3: 管理后台 /api/admin/* (require_admin 守卫 + 审计)
from backend.api.admin_routes import router as admin_router

# Removed: AI account routes merged into account_routes (unified AI trader accounts)

app.include_router(market_data_router)
app.include_router(market_data_v2_router)
app.include_router(order_router)
app.include_router(account_router)
app.include_router(config_router)
app.include_router(ranking_router)
app.include_router(crypto_router)
app.include_router(arena_router)
app.include_router(system_log_router)
app.include_router(vault_router)
app.include_router(system_control_router)
app.include_router(prompt_router)
app.include_router(sampling_router)
app.include_router(ai_trading_router)  # 新增：AI交易路由
app.include_router(hyperliquid_action_router)
app.include_router(hyperliquid_router)
if binance_router is not None:
    app.include_router(binance_router)
app.include_router(user_router)
# 阶段2 auth 端点 /api/auth/* (Task 2.2): register/login/refresh/logout/me
app.include_router(auth_router)
logger.info("[Auth] /api/auth/* 已挂载")
# 阶段4 Task 4.3: 管理后台 /api/admin/* (整组挂 require_admin 守卫 + 审计)
app.include_router(admin_router)
logger.info("[Admin] /api/admin/* 已挂载(require_admin 守卫)")
app.include_router(kline_router)
app.include_router(kline_analysis_router)
app.include_router(candlestick_pattern_router)
app.include_router(resonance_router)
app.include_router(comprehensive_analysis_router)
# app.include_router(learning_dashboard_router)  # P3.1: 学习仪表盘 API（已弃用，见 import 处注释）
app.include_router(gap_closure_router)  # GAP 闭环：审计 / Governor / Replay
app.include_router(market_flow_router)
app.include_router(parity_score_router)
app.include_router(signal_router)
app.include_router(market_regime_router)
app.include_router(analytics_router)
app.include_router(dingtalk_router)
app.include_router(smart_signal_router)
app.include_router(ai_signal_prompt_integration_router)
app.include_router(llm_config_router, prefix="/api")
app.include_router(llm_usage_router)
app.include_router(ai_strategy_router)
app.include_router(prompt_training_router)
app.include_router(paper_trading_router)
app.include_router(live_trading_router)
app.include_router(position_router)  # 阶段 3: 本地仓位协调器 /api/positions/*
app.include_router(strategy_template_router)
app.include_router(full_auto_router)
app.include_router(mlto_router)
app.include_router(arbitrage_profile_router)
# 阶段 5.1: 统一账户 API（AI + 套利 双表共存归一化视图）
try:
    from .api.unified_account_routes import router as unified_account_router
    app.include_router(unified_account_router)
    logger.info("[UnifiedAccount] /api/unified-account/* 已挂载")
except Exception as _ua_err:
    logger.warning(f"[UnifiedAccount] 路由挂载失败: {_ua_err}")
try:
    from .api.arbitrage_paper_routes import router as arbitrage_paper_router
    app.include_router(arbitrage_paper_router)
    logger.info("[ArbitragePaper] /api/arbitrage-paper/* 已挂载")
except Exception as _arb_paper_err:
    logger.warning(f"[ArbitragePaper] 挂载失败（非致命）: {_arb_paper_err}")
app.include_router(auto_coin_router)
app.include_router(coin_select_router)
app.include_router(backtest_router)
app.include_router(intelligence_router)
# AI 学习中心所需路由（原先遗漏挂载，导致前端 /api/rl/* 与 /api/evolution/* 全部 404）
app.include_router(rl_router)
app.include_router(evolution_router)
app.include_router(learning_loop_router)
app.include_router(learning_health_router)  # /api/learning/health + /api/learning/wisdom/stats
app.include_router(intelligent_learning_router)
app.include_router(learning_core_router)  # 统一进化学习内核 /api/learning/*
app.include_router(dashboard_router)  # 交易矩阵仪表盘 /api/dashboard/*
# [2026-07-30] OpenCode 路由已禁用（无用功能 + 资源浪费）
# try:
#     from .api.opencode_routes import router as opencode_router
#     app.include_router(opencode_router)
#     logger.info("[OpenCode] /api/opencode/* 已挂载")
# except Exception as _oc_err:
#     logger.warning(f"[OpenCode] 挂载失败（非致命）: {_oc_err}")
try:
    from .api.hermes_routes import router as hermes_router
    app.include_router(hermes_router)
    logger.info("[Hermes] /api/hermes/* 已挂载")
except Exception as _hm_err:
    logger.warning(f"[Hermes] 挂载失败（非致命）: {_hm_err}")
try:
    from .api.training_phase_routes import router as training_phase_router
    app.include_router(training_phase_router)
    logger.info("[TrainingPhase] /api/training-phase/* 已挂载")
except Exception as _tp_err:
    logger.warning(f"[TrainingPhase] 挂载失败（非致命）: {_tp_err}")
try:
    from .api.assistant_routes import router as assistant_router
    app.include_router(assistant_router)
    logger.info("[AlphaAssistant] /api/assistant/* 已挂载")
except Exception as _as_err:
    logger.warning(f"[AlphaAssistant] 挂载失败（非致命）: {_as_err}")
try:
    from .api.feishu_assistant_routes import router as feishu_assistant_router
    app.include_router(feishu_assistant_router)
    logger.info("[FeishuAssistant] /api/feishu/* 已挂载")
except Exception as _fs_err:
    logger.warning(f"[FeishuAssistant] 挂载失败（非致命）: {_fs_err}")
app.include_router(rag_router)
app.include_router(risk_router)
app.include_router(system_monitor_router)

# QAA v3.0 架构健康监控 API
try:
    from .api.qaa_routes import router as qaa_router
    app.include_router(qaa_router)
    logger.info("[QAA] /api/qaa/* 健康监控端点已挂载")
except Exception as _qaa_err:
    logger.warning(f"[QAA] 健康监控端点挂载失败（非致命）: {_qaa_err}")

# Exchange Hub & 套利 API
try:
    from .api.exchange_routes import router as exchange_router
    app.include_router(exchange_router)
    logger.info("[ExchangeHub] /api/exchange/* 已挂载")
except Exception as _ex_err:
    logger.warning(f"[ExchangeHub] 挂载失败（非致命）: {_ex_err}")

# 积分/返利套利 API
try:
    from .api.rebate_routes import router as rebate_router
    app.include_router(rebate_router, prefix="/api/rebate", tags=["rebate"])
    logger.info("[RebateArb] /api/rebate/* 已挂载")
except Exception as _rb_err:
    logger.warning(f"[RebateArb] 挂载失败（非致命）: {_rb_err}")

# 套利系统独立 API（V3 协调器 + 跨交易所 + 风控 + 监控）
try:
    from .api.arbitrage_routes import router as arbitrage_router
    app.include_router(arbitrage_router, prefix="/api/arbitrage", tags=["Arbitrage"])
    logger.info("[Arbitrage] /api/arbitrage/* 已挂载")
except Exception as _arb_err:
    logger.warning(f"[Arbitrage] 挂载失败（非致命）: {_arb_err}")

# 因子系统统一 API（同步管理 + 因子值 + 信号查询）
try:
    from .api.factor_sync_routes import router as factor_sync_router
    app.include_router(factor_sync_router)
    logger.info("[FactorSync] /api/factors/* 已挂载")
except Exception as _fs_err:
    logger.warning(f"[FactorSync] 挂载失败（非致命）: {_fs_err}")

# 算力中心 API（v6 第十章：硬件/进化/配置/本地LLM/历史指标）
try:
    from .api.compute_routes import router as compute_router
    app.include_router(compute_router)
    logger.info("[Compute] /api/compute/* 已挂载")
except Exception as _cp_err:
    logger.warning(f"[Compute] 挂载失败（非致命）: {_cp_err}")

# 深挖第 3 轮 (2026-05-08)：系统健康观测 API（LLM 烧钱排行 / 风控事件 / Session 健康）
try:
    from .api.system_health_routes import router as system_health_router
    app.include_router(system_health_router)
    logger.info("[SystemHealth] /api/system-health/* 已挂载")
except Exception as _sh_err:
    logger.warning(f"[SystemHealth] 挂载失败（非致命）: {_sh_err}")

# 全市场数据中台
try:
    from .api.market_intelligence_routes import router as market_intel_router
    app.include_router(market_intel_router)
    logger.info("[MarketIntel] /api/market-intel/* 已挂载")
except Exception as _mi_err:
    logger.warning(f"[MarketIntel] 挂载失败（非致命）: {_mi_err}")

try:
    from .api.scalp_config_routes import router as scalp_config_router
    app.include_router(scalp_config_router)
    logger.info("[ScalpConfig] /api/scalp-config/* 已挂载")
except Exception as _sc_err:
    logger.warning(f"[ScalpConfig] 挂载失败（非致命）: {_sc_err}")

try:
    from .api.strategy_config_routes import router as strategy_config_router
    app.include_router(strategy_config_router)
    logger.info("[StrategyConfig] /api/strategy-config/* 已挂载")
except Exception as _stc_err:
    logger.warning(f"[StrategyConfig] 挂载失败（非致命）: {_stc_err}")

try:
    from .api.strategy_prompt_routes import router as strategy_prompt_router
    app.include_router(strategy_prompt_router)
    logger.info("[StrategyPrompt] /api/strategy-prompt/* 已挂载")
except Exception as _sp_err:
    logger.warning(f"[StrategyPrompt] 挂载失败（非致命）: {_sp_err}")

# 监控端点
try:
    from .utils.monitoring import create_monitoring_endpoints
    create_monitoring_endpoints(app)
    logger.info("[监控] 监控端点已启用")
except Exception as e:
    logger.info(f"[监控] 监控端点加载失败: {e}")

# ATAS - 高级自动化交易系统 (安全加载，不影响主系统)
try:
    from .api.atas_routes import router as atas_router
    app.include_router(atas_router, prefix="/api/atas")
    logger.info("[ATAS] 模块加载成功")
except Exception as e:
    logger.info(f"[ATAS] 模块加载失败 (ATAS 功能将不可用): {e}")

# ATAS V2 - 新一代策略中心 (回测引擎、风险管理、系统监控)
try:
    from .api.atas_v2_routes import router as atas_v2_router
    app.include_router(atas_v2_router, prefix="/api")
    logger.info("✅ [ATAS V2] 新一代策略中心加载成功")
    logger.info("   ├─ 回测引擎: 已启用")
    logger.info("   ├─ 风险管理: 已启用")
    logger.info("   ├─ 系统监控: 已启用")
    logger.info("   └─ API端点: /api/atas/v2/*")
except Exception as e:
    logger.info(f"❌ [ATAS V2] 模块加载失败: {e}")
    import traceback
    traceback.print_exc()

# ATAS V2 可视化策略中心 (拖拽式策略设计器)
try:
    from .api.visual_strategy_routes import router as visual_strategy_router
    app.include_router(visual_strategy_router, prefix="/api")
    logger.info("✅ [ATAS V2 Visual] 可视化策略中心加载成功")
    logger.info("   ├─ 节点类型: 28种 (数据源3/指标8/条件5/信号4/执行3/风控5)")
    logger.info("   ├─ 代码编译器: 已启用")
    logger.info("   ├─ 策略执行器: 已启用")
    logger.info("   └─ API端点: /api/atas/v2/strategies/*")
except Exception as e:
    logger.info(f"❌ [ATAS V2 Visual] 模块加载失败: {e}")
    import traceback
    traceback.print_exc()

# app.include_router(ai_account_router, prefix="/api")  # Removed - merged into account_router

# Strategy route aliases for frontend compatibility
from fastapi import Depends, HTTPException

from .database.connection import SessionLocal


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/api/accounts/{account_id}/strategy")
async def get_account_strategy_alias(account_id: int, db: Session = Depends(get_db)):
    """Alias for strategy config endpoint"""
    from api.account_routes import get_account_strategy
    return await get_account_strategy(account_id, db)

@app.put("/api/accounts/{account_id}/strategy")
async def update_account_strategy_alias(account_id: int, payload: dict, db: Session = Depends(get_db)):
    """Alias for strategy config endpoint"""
    from api.account_routes import update_account_strategy
    from pydantic import ValidationError
    from schemas.account import StrategyConfigUpdate
    try:
        strategy_update = StrategyConfigUpdate(**payload)
        return await update_account_strategy(account_id, strategy_update, db)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/accounts/{account_id}/strategy/status")
async def get_account_strategy_status_alias(account_id: int, db: Session = Depends(get_db)):
    """Alias for strategy status endpoint (frontend uses /api/accounts plural)"""
    from api.account_routes import get_account_strategy_status
    return await get_account_strategy_status(account_id, db)

@app.get("/api/strategy/status")
async def get_strategy_manager_status():
    """Get strategy manager status"""
    from .services.trading_strategy import get_strategy_status
    return get_strategy_status()

# WebSocket endpoint
from .api.ws import websocket_endpoint

app.websocket("/ws")(websocket_endpoint)

# Serve auth config file
@app.get("/auth-config.json")
async def serve_auth_config():
    """Serve the auth configuration file"""
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    config_path = os.path.join(static_dir, "auth-config.json")

    if os.path.exists(config_path):
        return FileResponse(
            config_path,
            media_type="application/json",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0"
            }
        )
    else:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Auth config not found")

# [阶段0] serve_root() GET / 已删(后端不再托管前端首页)
# [阶段0] serve_spa() catch-all 已删(后端不再做 SPA fallback,前端路由由独立前端服务处理)

# ─────────────────────────────────────────────────────────────
# [2026-08-05 浏览器直连] 可选：后端同源托管前端静态产物。
# 目的：当手机/其他电脑想"不安装任何客户端、直接用浏览器打开一个网址"访问
# 本系统时，开启 BACKEND_SERVE_WEB=true 即可让 FastAPI 同时提供前端页面与
# API —— 前端页面与后端 API 同源，浏览器零配置、无需填后端地址。
# 默认关闭（保持前后端分离架构）；仅在需要浏览器直连时开启。
# ─────────────────────────────────────────────────────────────
_BACKEND_SERVE_WEB = os.getenv("BACKEND_SERVE_WEB", "").strip().lower() in ("1", "true", "yes")
if _BACKEND_SERVE_WEB:
    try:
        from fastapi.staticfiles import StaticFiles
        from fastapi.responses import JSONResponse as _JSONResponse
        from fastapi import HTTPException as _HTTPException

        _WEB_OUT_DIR = os.path.normpath(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "frontend-next", "out",
        ))
        _WEB_INDEX = os.path.join(_WEB_OUT_DIR, "index.html")

        if os.path.isdir(_WEB_OUT_DIR):
            logging.getLogger(__name__).info(
                "[web-serve] BACKEND_SERVE_WEB=true → 同源托管前端页面: %s", _WEB_OUT_DIR
            )

            # Next.js 静态导出资源目录（/_next/*、/favicon.ico 等）
            app.mount(
                "/_next",
                StaticFiles(directory=os.path.join(_WEB_OUT_DIR, "_next")),
                name="web-static-assets",
            )

            @app.get("/{full_path:path}", include_in_schema=False)
            async def _serve_web_page(full_path: str):
                """前端路由回退：/xxx → xxx.html；不存在则回 index.html。
                /api、/ws 等后端路径已在此路由之前注册，优先匹配，不会被拦截。"""
                # 保护：未匹配到后端路由的 /api 请求应返回 404，而非前端页面
                if full_path.startswith("api/") or full_path.startswith("ws"):
                    raise _HTTPException(status_code=404, detail="Not Found")

                # 1) 精确静态文件（favicon.ico、*.svg、*.txt 等）
                if full_path:
                    exact = os.path.join(_WEB_OUT_DIR, full_path)
                    if os.path.isfile(exact):
                        return FileResponse(exact)

                # 2) Next 静态导出约定：/login → login.html
                if full_path and not full_path.endswith("/"):
                    html_path = os.path.join(_WEB_OUT_DIR, full_path + ".html")
                    if os.path.isfile(html_path):
                        return FileResponse(html_path)

                # 3) 目录 → 目录下的 index.html
                if full_path:
                    dir_index = os.path.join(_WEB_OUT_DIR, full_path, "index.html")
                    if os.path.isfile(dir_index):
                        return FileResponse(dir_index)

                # 4) 兜底 → 首页（SPA 行为，保证任意路径都能打开登录页）
                if os.path.isfile(_WEB_INDEX):
                    return FileResponse(_WEB_INDEX)

                raise _HTTPException(status_code=404, detail="Not Found")
        else:
            logging.getLogger(__name__).warning(
                "[web-serve] BACKEND_SERVE_WEB=true 但前端产物不存在: %s", _WEB_OUT_DIR
            )
    except Exception as _exc:  # 托管失败不影响 API 主流程
        logging.getLogger(__name__).exception("[web-serve] 前端托管初始化失败，已跳过: %s", _exc)
