"""
Snapshot database connection - separate from main database to avoid locks.

重要：不在模块导入时执行真实 TCP 连接。此前在 import 阶段调用 engine.connect() 会在 Windows 上
因 DATABASE_URL / SNAPSHOT_DATABASE_URL 含非 UTF-8 字符（如 GBK 路径）触发 UnicodeDecodeError，
导致整个后端无法启动（表现为进程反复崩溃或“卡死”）。
"""
from sqlalchemy import create_engine, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import OperationalError
import os
import logging
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'), encoding="utf-8")

logger = logging.getLogger(__name__)

# 2026-06-17: 当 PG engine 创建失败回退到本地 SQLite 时置 True。
# 供 /api/health 等健康检查读取，避免快照静默写本地、主流程无感知。
# （之前 fallback 仅 logger.error 一次，运维侧无法发现数据已分叉。）
_SNAPSHOT_FELLBACK_TO_SQLITE: bool = False


def is_snapshot_using_sqlite_fallback() -> bool:
    """快照库是否因 PG 不可用回退到了本地 SQLite（供健康检查/前端展示）。"""
    return _SNAPSHOT_FELLBACK_TO_SQLITE

SNAPSHOT_DATABASE_URL = os.environ.get("SNAPSHOT_DATABASE_URL", "sqlite:///./data/alpha_snapshots.db")
if isinstance(SNAPSHOT_DATABASE_URL, bytes):
    SNAPSHOT_DATABASE_URL = SNAPSHOT_DATABASE_URL.decode("utf-8", errors="replace")

POOL_SIZE = int(os.environ.get("DB_POOL_SIZE", "20"))
POOL_MAX_OVERFLOW = int(os.environ.get("DB_POOL_MAX_OVERFLOW", "20"))
POOL_RECYCLE = int(os.environ.get("DB_POOL_RECYCLE", "1800"))
POOL_TIMEOUT = int(os.environ.get("DB_POOL_TIMEOUT", "30"))


def _normalize_db_url(url: str) -> str:
    if isinstance(url, bytes):
        return url.decode("utf-8", errors="replace")
    return url


def create_snapshot_database_if_missing() -> None:
    """
    若使用 PostgreSQL 且库不存在则创建。在 FastAPI lifespan 中调用（此时模块已加载，不会在 import 时阻塞）。
    """
    url_s = _normalize_db_url(SNAPSHOT_DATABASE_URL)
    if url_s.startswith("sqlite"):
        return
    try:
        url = make_url(url_s)
    except Exception as e:
        logger.warning("[Snapshot] 无法解析 SNAPSHOT_DATABASE_URL，跳过自动建库: %s", e)
        return
    db_name = url.database
    if not db_name:
        return

    try:
        with snapshot_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return
    except OperationalError as exc:
        msg = str(exc).lower()
        if "does not exist" not in msg:
            logger.warning("[Snapshot] 连接快照库失败（非「库不存在」）: %s", exc)
            return
    except UnicodeDecodeError as exc:
        logger.warning(
            "[Snapshot] 连接字符串编码异常（请使用 UTF-8 保存 .env，密码/路径避免非 UTF-8）: %s",
            exc,
        )
        return
    except Exception as exc:
        logger.warning("[Snapshot] 探测快照库时异常: %s", exc)
        return

    logger.warning("Snapshot database %s missing – creating it", db_name)
    admin_url = url.set(database="postgres")
    admin_engine = create_engine(admin_url)
    try:
        with admin_engine.connect() as conn:
            conn = conn.execution_options(isolation_level="AUTOCOMMIT")
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
        logger.info("Snapshot database %s created", db_name)
    except Exception as e:
        logger.error("[Snapshot] 创建数据库失败: %s", e)
    finally:
        admin_engine.dispose()


def _ensure_snapshot_engine():
    """创建 Engine 对象，不在此处连接数据库。"""
    url_s = _normalize_db_url(SNAPSHOT_DATABASE_URL)

    if url_s.startswith("sqlite"):
        return create_engine(
            url_s,
            connect_args={"check_same_thread": False, "timeout": 30},
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=5,
            pool_recycle=300,
            pool_timeout=30,
        )

    try:
        _ca = {}
        if url_s.lower().startswith(("postgresql", "postgres")):
            _ca["connect_timeout"] = int(os.environ.get("DB_CONNECT_TIMEOUT", "12"))
            _ca["application_name"] = "hyper-alpha-snapshots"
        _kw = dict(
            pool_size=POOL_SIZE,
            max_overflow=POOL_MAX_OVERFLOW,
            pool_recycle=POOL_RECYCLE,
            pool_timeout=POOL_TIMEOUT,
            pool_pre_ping=True,
        )
        if _ca:
            _kw["connect_args"] = _ca
        return create_engine(url_s, **_kw)
    except Exception as e:
        global _SNAPSHOT_FELLBACK_TO_SQLITE
        _SNAPSHOT_FELLBACK_TO_SQLITE = True
        # WARNING（非 error）：这是可恢复的容错降级，但必须显眼，让运维知道
        # 快照数据此刻在写本地 SQLite，与主 PG 流程分叉。
        logger.warning(
            "[Snapshot] 无法从 SNAPSHOT_DATABASE_URL 创建引擎，已回退到本地 SQLite（快照数据将与主库分叉）: %s",
            e,
        )
        fallback = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "data", "alpha_snapshots.db")
        )
        os.makedirs(os.path.dirname(fallback), exist_ok=True)
        return create_engine(
            f"sqlite:///{fallback}",
            connect_args={"check_same_thread": False, "timeout": 30},
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=5,
            pool_recycle=300,
            pool_timeout=30,
        )


snapshot_engine = _ensure_snapshot_engine()

SnapshotSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=snapshot_engine)

SnapshotBase = declarative_base()


def get_snapshot_db():
    """Get snapshot database session"""
    db = SnapshotSessionLocal()
    try:
        yield db
    finally:
        db.close()
