from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base, scoped_session
from sqlalchemy.pool import QueuePool
import os
import logging
import threading
import time as _time_mod
import queue
from typing import Optional, Tuple, Callable, Any

logger = logging.getLogger(__name__)


def _bootstrap_env() -> None:
    """在读取 DATABASE_URL 前加载项目根目录 .env，避免脚本/定时任务误连空 SQLite。"""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    _here = os.path.dirname(os.path.abspath(__file__))
    for candidate in (
        os.path.join(_here, "..", "..", ".env"),  # Hyper-Alpha-Arena/.env
        os.path.join(_here, "..", ".env"),         # backend/.env（兼容）
    ):
        if os.path.isfile(candidate):
            load_dotenv(candidate, override=False)
            return


_bootstrap_env()

DATABASE_URL = os.environ.get('DATABASE_URL', "sqlite:///./data/alpha_arena.db")

# ═══════════════════════════════════════════════════════════════════
# SQLite 写入队列：单消费线程串行化所有 commit，消除全局互斥锁
#
# 旧方案 _SQLITE_WRITE_LOCK (threading.Lock) 的问题：
#   - 多线程同时 acquire 时阻塞在 Lock 上，持有锁的线程还可能与
#     SQLite WAL 的 filesystem lock 冲突导致 "database is locked"。
#   - 超时线程 detach 后锁无法释放 → 后续所有写操作永久阻塞。
#
# 新方案 WriteQueue：
#   - 所有写操作通过队列投递到唯一的 Writer 线程串行执行。
#   - 写线程内部只有一个活跃 commit，消除了 Python 层锁竞争。
#   - 调用线程通过 Future 等待结果，支持超时。
#   - 即使调用线程超时/detach，写线程仍正常完成 commit。
# ═══════════════════════════════════════════════════════════════════

_SQLITE_WRITE_LOCK = threading.Lock()  # 保留作为兼容后备，仅当 _write_queue 不可用时使用


class _WriteQueue:
    """单线程写队列 — 所有 SQLite commit 串行执行。"""

    def __init__(self):
        self._queue: queue.Queue = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._started = False
        self._lock = threading.Lock()

    def start(self):
        with self._lock:
            if self._started:
                return
            self._thread = threading.Thread(
                target=self._loop, name="sqlite-write-queue", daemon=True,
            )
            self._thread.start()
            self._started = True
            logger.info("[DB] WriteQueue 写线程已启动")

    def _loop(self):
        while True:
            try:
                item = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if item is None:
                break
            db, label, result_event, result_box = item
            try:
                db.commit()
                result_box[0] = True
            except Exception as e:
                result_box[0] = False
                result_box[1] = e
                try:
                    db.rollback()
                except Exception:
                    pass
                logger.debug(f"[DB] WriteQueue commit 失败 ({label}): {e}")
            finally:
                result_event.set()

    def submit_commit(self, db, label: str = "", timeout_s: float = 15.0) -> bool:
        """提交 commit 到写队列，等待结果。超时返回 False 但 commit 仍会在后台完成。"""
        if not self._started:
            self.start()
        result_event = threading.Event()
        result_box: list = [False, None]  # [success, exception]
        self._queue.put((db, label, result_event, result_box))
        if result_event.wait(timeout=timeout_s):
            return result_box[0]
        else:
            logger.warning(f"[DB] WriteQueue commit 超时 ({label})，commit 将在后台完成")
            return False

    def queue_size(self) -> int:
        return self._queue.qsize()


_write_queue = _WriteQueue()


def sqlite_write_commit(db, label: str = "", retries: int = 4) -> bool:
    """带写队列的 commit — SQLite 单写者模型下消除全局锁竞争。"""
    if not DATABASE_URL.startswith("sqlite"):
        try:
            db.commit()
            return True
        except Exception as e:
            logger.error(f"[DB] commit 失败 ({label}): {e}")
            try:
                db.rollback()
            except Exception:
                pass
            return False

    # ── 优先使用 WriteQueue ──
    for attempt in range(max(1, retries)):
        success = _write_queue.submit_commit(db, label=f"{label}#{attempt}", timeout_s=12.0)
        if success:
            return True
        # 检查最后一次的错误
        if attempt < retries - 1:
            wait = 0.1 * (2 ** attempt)
            logger.debug(
                f"[DB] WriteQueue commit 重试 {attempt + 1}/{retries} ({label}) "
                f"等待 {wait:.2f}s"
            )
            _time_mod.sleep(wait)

    # ── WriteQueue 全部失败，回退到 Lock 方式 ──
    logger.warning(f"[DB] WriteQueue 全部失败，回退 Lock 模式 ({label})")
    last_err = None
    for attempt in range(2):
        try:
            with _SQLITE_WRITE_LOCK:
                db.commit()
            return True
        except Exception as e:
            last_err = e
            err_s = str(e).lower()
            if "locked" not in err_s and "busy" not in err_s:
                break
            try:
                db.rollback()
            except Exception:
                pass
            if attempt < 1:
                _time_mod.sleep(0.2)
    logger.error(f"[DB] commit 最终失败 ({label}): {last_err}")
    try:
        db.rollback()
    except Exception:
        pass
    return False


# SQLite 连接池优化参数
# SQLite WAL 单写者模型：pool_size 不宜过大，减少连接争用
_SQLITE_POOL_SIZE = int(os.environ.get("SQLITE_POOL_SIZE", "3"))
_SQLITE_MAX_OVERFLOW = int(os.environ.get("SQLITE_MAX_OVERFLOW", "1"))

POOL_SIZE = int(os.environ.get("DB_POOL_SIZE", "20"))
POOL_MAX_OVERFLOW = int(os.environ.get("DB_MAX_OVERFLOW", "20"))
# P0 修复（2026-07-14）：回滚率 47% 根因——pool_recycle=1800 远超 PG 的 idle_in_transaction_session_timeout（90s），
# 导致长 LLM 期间连接被 PG 服务端掐断。缩短到 300s 确保 recycle 在超时前生效。
POOL_RECYCLE = int(os.environ.get("DB_POOL_RECYCLE", "300"))
POOL_TIMEOUT = int(os.environ.get("DB_POOL_TIMEOUT", "30"))
# [2026-07-17 修复] 此前下面两处 `SET idle_in_transaction_session_timeout = 0` 把这个
# 超时完全禁用了（0=无限），日志里"90s 后会被数据库自动回滚断开"的承诺其实从未生效过——
# pool_recycle 只在连接被"重新 checkout"时才会回收，如果某个协程 checkout 后一直不
# commit/close（真正的泄漏/挂死，例如 dingtalk 重试循环那个 bug），连接会一直被
# 该协程占着，永远不会被 checkout 流程摸到，也就永远不会被 recycle。实测已经出现过
# 单条事务挂起 7000+ 秒(2小时+)的真实案例。改为一个足够覆盖最长 LLM 调用(实测<60s)的
# 兜底值，既不影响正常长耗时操作，又能让真正的泄漏在可控时间内被数据库强制回滚，
# 不再无限堆积拖垫全局连接池。
DB_IDLE_IN_TXN_TIMEOUT_MS = int(os.environ.get("DB_IDLE_IN_TXN_TIMEOUT_MS", "120000"))  # 2分钟兜底（原 5 分钟过长，24h 内观测到 238s 挂起事务仍存活）

if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False, "timeout": 30},
        pool_pre_ping=True,
        poolclass=QueuePool,
        pool_size=_SQLITE_POOL_SIZE,
        max_overflow=_SQLITE_MAX_OVERFLOW,
        pool_recycle=300,
        pool_timeout=15,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=120000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA cache_size=-64000")
        cursor.execute("PRAGMA temp_store=MEMORY")
        cursor.execute("PRAGMA mmap_size=268435456")
        cursor.execute("PRAGMA wal_autocheckpoint=1000")
        cursor.close()

    SessionLocal = sessionmaker(
        autocommit=False, autoflush=False, expire_on_commit=False, bind=engine,
    )

else:
    _url_lower = DATABASE_URL.lower()
    _connect_args = {}
    if _url_lower.startswith("postgresql") or _url_lower.startswith("postgres"):
        _connect_args["connect_timeout"] = int(os.environ.get("DB_CONNECT_TIMEOUT", "12"))
        # application_name 打标：pg_stat_activity 里能一眼分清"是不是本进程开的连接"，
        # 便于排查"僵尸事务"时定位到具体是哪个逻辑库（core/market/analytics/snapshots）泄漏的。
        _connect_args["application_name"] = "hyper-alpha-core"
        # TCP keepalive：防止 PG 服务端/网络层 idle 超时断开连接
        _connect_args["keepalives"] = 1
        _connect_args["keepalives_idle"] = 30   # 30s 后开始发 keepalive
        _connect_args["keepalives_interval"] = 10
        _connect_args["keepalives_count"] = 3
    _eng_kw = dict(
        pool_size=POOL_SIZE,
        max_overflow=POOL_MAX_OVERFLOW,
        pool_recycle=POOL_RECYCLE,
        pool_timeout=POOL_TIMEOUT,
        pool_pre_ping=True,
    )
    if _connect_args:
        _eng_kw["connect_args"] = _connect_args
    engine = create_engine(DATABASE_URL, **_eng_kw)

    # P0 修复：每次从连接池 checkout 连接时，重置事务超时为较大值。
    # 防止 PG 服务端 idle_in_transaction_session_timeout（默认 90s 或更低）在 LLM 调用期间掐断连接。
    @event.listens_for(engine, "connect")
    def _on_pg_connect(dbapi_conn, connection_record):
        try:
            with dbapi_conn.cursor() as cur:
                # 有限兜底值，而非彻底禁用（见上方 DB_IDLE_IN_TXN_TIMEOUT_MS 定义处说明）
                cur.execute(f"SET idle_in_transaction_session_timeout = {DB_IDLE_IN_TXN_TIMEOUT_MS}")
                cur.execute("SET statement_timeout = 300000")  # 5 分钟语句超时兜底
            dbapi_conn.commit()
        except Exception:
            pass

    SessionLocal = sessionmaker(autocommit=False, autoflush=False, expire_on_commit=False, bind=engine)

ScopedSession = scoped_session(SessionLocal)
Base = declarative_base()


# ═══════════════════════════════════════════════════════════════
# 多数据库支持 (V4 §3.7 数据库分层策略)
# alpha_arena.db    — 交易核心 (默认, 保留)
# alpha_market.db   — 市场数据 (高频写入: K线/深度/资金费率)
# alpha_analytics.db — 分析审计 (异步写入: AI决策/风控/LLM用量)
# ═══════════════════════════════════════════════════════════════


def _create_dialect_engine(url: str, label: str):
    """统一创建数据库引擎 — 自动识别 SQLite/PostgreSQL 并配置对应参数。

    SQLite 路径：WAL PRAGMA + 小连接池（3+1）
    PostgreSQL 路径：大连接池（20+20）+ connect_timeout
    """
    if url.startswith("sqlite"):
        eng = create_engine(
            url,
            connect_args={"check_same_thread": False, "timeout": 30},
            pool_pre_ping=True,
            poolclass=QueuePool,
            pool_size=_SQLITE_POOL_SIZE,
            max_overflow=_SQLITE_MAX_OVERFLOW,
            pool_recycle=300,
            pool_timeout=15,
        )

        @event.listens_for(eng, "connect")
        def _set_pragma(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=120000")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA cache_size=-64000")
            cursor.execute("PRAGMA temp_store=MEMORY")
            cursor.execute("PRAGMA mmap_size=268435456")
            cursor.execute("PRAGMA wal_autocheckpoint=1000")
            cursor.close()

        logger.info(f"[DB] {label} SQLite engine created: {url}")
        return eng

    # PostgreSQL / 其他
    _connect_args = {}
    if "postgresql" in url.lower() or "postgres" in url.lower():
        _connect_args["connect_timeout"] = int(os.environ.get("DB_CONNECT_TIMEOUT", "12"))
        _connect_args["application_name"] = f"hyper-alpha-{label.lower()}"
        _connect_args["keepalives"] = 1
        _connect_args["keepalives_idle"] = 30
        _connect_args["keepalives_interval"] = 10
        _connect_args["keepalives_count"] = 3
    eng = create_engine(
        url,
        connect_args=_connect_args if _connect_args else {},
        pool_size=POOL_SIZE,
        max_overflow=POOL_MAX_OVERFLOW,
        pool_recycle=POOL_RECYCLE,
        pool_timeout=POOL_TIMEOUT,
        pool_pre_ping=True,
    )

    # 同主库：checkout 时禁用事务空闲超时
    if "postgresql" in url.lower() or "postgres" in url.lower():
        @event.listens_for(eng, "connect")
        def _on_dialect_connect(dbapi_conn, connection_record):
            try:
                with dbapi_conn.cursor() as cur:
                    # 有限兜底值，而非彻底禁用（见 DB_IDLE_IN_TXN_TIMEOUT_MS 定义处说明）
                    cur.execute(f"SET idle_in_transaction_session_timeout = {DB_IDLE_IN_TXN_TIMEOUT_MS}")
                    cur.execute("SET statement_timeout = 300000")
                dbapi_conn.commit()
            except Exception:
                pass
    logger.info(f"[DB] {label} PostgreSQL engine created: {url}")
    return eng


# 保留旧名称作为兼容别名
_create_sqlite_engine = _create_dialect_engine


# ── Market Database ──
MARKET_DATABASE_URL = os.environ.get(
    "MARKET_DATABASE_URL", "sqlite:///./data/alpha_market.db"
)
market_engine = _create_sqlite_engine(MARKET_DATABASE_URL, "Market")
MarketSessionLocal = sessionmaker(
    autocommit=False, autoflush=False, expire_on_commit=False, bind=market_engine,
)
MarketBase = declarative_base()


def get_market_db():
    db = MarketSessionLocal()
    try:
        yield db
    finally:
        # 同 get_db:防 idle-in-transaction 泄漏 + SET LOCAL 复用陷阱。
        try:
            db.rollback()
        except Exception:
            pass
        db.close()


# ── Analytics Database ──
ANALYTICS_DATABASE_URL = os.environ.get(
    "ANALYTICS_DATABASE_URL", "sqlite:///./data/alpha_analytics.db"
)
analytics_engine = _create_sqlite_engine(ANALYTICS_DATABASE_URL, "Analytics")
AnalyticsSessionLocal = sessionmaker(
    autocommit=False, autoflush=False, expire_on_commit=False, bind=analytics_engine,
)
AnalyticsBase = declarative_base()


def get_analytics_db():
    db = AnalyticsSessionLocal()
    try:
        yield db
    finally:
        # 同 get_db:防 idle-in-transaction 泄漏 + SET LOCAL 复用陷阱。
        try:
            db.rollback()
        except Exception:
            pass
        db.close()


def alembic_at_baseline(
    eng: Any,
    version_table: str = "alembic_version_core",
    target_rev: str = "0001",
) -> bool:
    """检查给定 engine 的库是否已应用 baseline 迁移（指定 revision）。

    用于"下线内联 ALTER TABLE"的开关：当 Alembic 已接管某库的 schema
    （version 表存在且版本 == target_rev）时，遗留的启动期补丁逻辑应当跳过，
    避免与 Alembic 迁移产生冲突/重复 DDL。

    语义：
      - True  → 该库已处于（或晚于）baseline，schema 归 Alembic 管，跳过遗留补丁。
      - False → version 表不存在 / 查询失败 / 版本不匹配，视为尚未迁移，继续兼容旧库。

    注意：本函数判定 ``== target_rev``（精确等于）。对"收口类"迁移（见
    :func:`alembic_at_rev`）需要 "已应用即跳过" 的语义（>=），请用 ``alembic_at_rev``。

    对 SQLite / PostgreSQL 语法一致（均为 ``SELECT version_num FROM <table>``），
    因此三种逻辑库（core/market/analytics）可统一复用，仅 version_table 不同。
    """
    try:
        from sqlalchemy import text as _sa_text

        with eng.connect() as conn:
            row = conn.execute(
                _sa_text(f"SELECT version_num FROM {version_table}")
            ).fetchone()
            return bool(row and row[0] == target_rev)
    except Exception:
        # version 表不存在或查询失败 → 视为未迁移
        return False


def alembic_at_rev(
    eng: Any,
    target_rev: str,
    version_table: str = "alembic_version_core",
) -> bool:
    """检查给定 engine 的库是否已应用（或晚于）指定 revision。

    与 :func:`alembic_at_baseline` 的区别：本函数判定 ``>= target_rev``
    （而非精确等于），用于"收口类"迁移（如 0008 把启动期内联 ALTER 收进 Alembic
    之后）——只要库已经到 target_rev 或更晚，对应的启动期内联补丁就该跳过，
    无论之后又有多少新迁移。

    版本比较：revision ID 形如 "0001".."0008"（4 位零填充数字串），按字符串
    字典序比较与数值序一致，因此直接用 ``>=`` 即可。

    语义：
      - True  → 当前版本 >= target_rev，target_rev 已生效，跳过对应内联补丁。
      - False → version 表不存在 / 查询失败 / 版本早于 target_rev → 继续兼容旧路径。
    """
    try:
        from sqlalchemy import text as _sa_text

        with eng.connect() as conn:
            row = conn.execute(
                _sa_text(f"SELECT version_num FROM {version_table}")
            ).fetchone()
        return bool(row and str(row[0]) >= target_rev)
    except Exception:
        # version 表不存在或查询失败 → 视为尚未迁移
        return False


def get_session_for(model_class):
    """根据 model 类返回对应的 SessionLocal 工厂。

    用法:  db = get_session_for(CryptoKline)()
    """
    if isinstance(model_class, type):
        if issubclass(model_class, MarketBase):
            return MarketSessionLocal
        if issubclass(model_class, AnalyticsBase):
            return AnalyticsSessionLocal
    return SessionLocal


# ═══════════════════════════════════════════════════════════════
# 租户 RLS 钩子:每次事务开始(含 autobegin)设 SET LOCAL app.tenant_id
#
# 根因修复(致命陷阱):代码库有 521 处 db.commit(),autobegin 模式下每次 commit
# 结束当前事务,SET LOCAL 随之失效。下一次查询 autobegin 一个新事务,此时
# current_setting('app.tenant_id', true) 返回 NULL → RLS 策略 fail-closed(隐藏行)
# 或更糟(泄漏)。这是静默数据损坏,不是报错。
#
# 解决:监听 SA 的 "begin" 事件 —— 它在每次事务开始时触发(包括 commit 后
# autobegin 出来的新事务),从 ContextVar 读 tenant_id 重新设 GUC。ContextVar
# 由中间件(JWTAuthMiddleware)在请求入口 set_request_identity() 一次,这里
# 动态读,保证每个 autobegin 的事务都带上正确的租户身份。
#
# 事件选择说明:SA 2.0 没有 Engine 级 "after_begin" 事件(只有 Pool 级 connect
# 和 Connection 级 after_begin,后者无法用 event.listens_for(engine, ...) 注册)。
# "begin" 事件在 Connection 开始事务时触发,实测每次 commit 后的 autobegin 也会
# 触发(fire_count 递增已验证)。若未来 SA 行为变化导致 begin 不触发,可退回
# "before_cursor_execute"(每条 SQL 前触发,开销略大但保证覆盖)。
# ═══════════════════════════════════════════════════════════════


def _install_tenant_rls_hook(eng) -> None:
    """为 engine 注册 begin 钩子:每次事务开始设 SET LOCAL app.tenant_id。

    - tid 为 None(未认证/运维通道/全局)时不设 GUC,current_setting 返回 NULL,
      RLS 策略据此决定(阶段3 策略会把 NULL 当 "无租户" 处理)。
    - 用 inline literal ``str(int(tid))`` 而非 paramstyle 元组:PostgreSQL 的
      SET 语句不接受绑定参数(psycopg 会把 %s 翻译成 $1,SET LOCAL ... = $1 报
      syntax error)。tid 来自 JWT claim 且经 int() 强转,内联是安全的。
    - 异常静默吞:RLS GUC 仅在启用了 RLS 的 PG 上有意义;SQLite/未启用 RLS 的
      开发库会报错(SET LOCAL 在 SQLite 不存在),此时跳过不影响功能。
    """
    from backend.core.tenant import tenant_id_var, is_admin_var

    # 本地单租户模式(AUTH_LOCAL_TENANT=<user_id>):后台线程/未认证请求没有
    # ContextVar 身份时,默认注入该租户。解决 uvicorn 线程池上下文传播不稳定
    # 导致部分请求 RLS 空串/无租户而看不到数据的问题(本地单用户部署专用)。
    _local_tenant: int | None = None
    _local_raw = os.environ.get("AUTH_LOCAL_TENANT", "").strip()
    if _local_raw:
        try:
            _local_tenant = int(_local_raw)
        except ValueError:
            _local_tenant = None

    @event.listens_for(eng, "begin")
    def _set_tenant_guc(conn):
        tid = tenant_id_var.get()
        is_admin = is_admin_var.get()
        if tid is None and _local_tenant is not None:
            tid = _local_tenant
        if tid is not None:
            try:
                # int() 强转防注入(JWT claim 即使被篡改,这里也只会抛 ValueError 被吞掉)
                conn.exec_driver_sql(
                    "SET LOCAL app.tenant_id = '" + str(int(tid)) + "'"
                )
            except Exception:
                pass  # RLS 未启用 / SQLite / GUC 不存在 → 静默跳过
        if is_admin:
            try:
                conn.exec_driver_sql("SET LOCAL app.is_admin = 'on'")
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════
# ORM tenant_id 自动填充:对有 tenant_id 列但 ORM 未声明的表,
# 在 flush 前自动设默认值(防 NotNullViolation)
# ═══════════════════════════════════════════════════════════════
from sqlalchemy import event as _sa_event

@_sa_event.listens_for(SessionLocal, "before_flush")
def _auto_fill_tenant_id(session, _flushcontext, _instances):
    """对所有有 tenant_id 属性但值为 None 的 pending 对象,自动填默认值。"""
    from backend.core.tenant import tenant_id_var
    _default_tid = tenant_id_var.get() or 1
    for obj in session.new:
        if hasattr(obj, 'tenant_id') and obj.tenant_id is None:
            obj.tenant_id = _default_tid


# 注册到三个 engine(core / market / analytics)。
# 必须在三个 engine 都创建之后调用 —— 这里位于 market_engine/analytics_engine
# 定义之后,满足条件。
_install_tenant_rls_hook(engine)
_install_tenant_rls_hook(market_engine)
_install_tenant_rls_hook(analytics_engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        # 防 idle-in-transaction 泄漏:请求结束时若仍有 pending 事务(代码库有
        # 521 处 commit 但并非所有路径都 commit 了,异常分支尤其容易漏),rollback
        # 强制结束事务,使 SET LOCAL 失效、连接干净地归还连接池。这阻断了
        # "连接复用继承上一个租户身份" 的陷阱(spec §4.4.0a):若不 rollback,
        # 下一个请求拿到这条连接时,旧事务的 GUC 可能仍在(虽然 begin 钩子会
        # 重设,但 rollback 是双保险 + 防泄漏)。
        try:
            db.rollback()
        except Exception:
            pass
        db.close()


# Periodic pool status logging (all engines)
# 2026-06-17: 改为可控的 lazy 启动 + 停止标志。
# 原实现在模块 import 时无条件 start 一个 daemon 线程，每个 import 该模块的
# 进程/worker 都起一个（多 worker 下 N 个监控线程），且无停止机制，不利于测试。
# 现保留自动启动（兼容现有行为），但加 _pool_monitor_stop 标志供 stop_pool_monitor()
# 在 lifespan shutdown / 测试 teardown 时优雅停止，并暴露启动状态查询。
_pool_monitor_stop = threading.Event()
_pool_monitor_started = False
_pool_monitor_lock = threading.Lock()



# ═══════════════════════════════════════════════════════════════
# 僵尸事务（idle in transaction）巡检
#
# 背景（2026-07-08 事故）：某处业务代码开了 db session 做查询后，异常分支/提前
# return 没有走到 finally: db.close()，事务就一直挂在 PostgreSQL 里
# "idle in transaction"。攒得越多，越容易把后面的查询卡住（锁等待、连接池耗尽），
# 表现为主循环反复超时、AI 决策做了但下单落库失败，最终看起来像"系统不开仓了"。
#
# 双重保护：
#   1. 数据库层兜底：已对 db_admin 角色设置
#      `ALTER ROLE db_admin SET idle_in_transaction_session_timeout='90s'`，
#      任何连接空占事务超过 90s 会被 PostgreSQL 自己强制回滚+断开，
#      保证不会无限堆积到把系统拖死（这是"断路器"，防止总崩）。
#   2. 这里加的是"可观测性"：每次巡检时把所有挂起 >20s 的事务（pid/存活库/
#      具体 SQL 前 160 字符）打成 WARNING 日志。下次再发生泄漏，直接搜索
#      backend.log 里的 "[DB LeakGuard]" 就能看到是哪条 SQL、哪个库卡住的，
#      不用再像这次一样临时写脚本手工连库排查。
# ═══════════════════════════════════════════════════════════════
_LEAK_GUARD_AGE_SECONDS = int(os.environ.get("DB_LEAK_GUARD_AGE_SECONDS", "20"))
# P0 修复（2026-07-20）：LeakGuard 从"只告警"升级为"主动 kill 超时事务"。
# 原实现只打 WARNING 日志，依赖 PG 服务端 idle_in_transaction_session_timeout 兜底，
# 但 checkout 时会把超时覆盖为 DB_IDLE_IN_TXN_TIMEOUT_MS，导致 90s 承诺失效。
# 现在对超过此阈值的事务直接 pg_terminate_backend，立即释放连接和锁。
_LEAK_GUARD_KILL_SECONDS = int(os.environ.get("DB_LEAK_GUARD_KILL_SECONDS", "120"))


def _check_leaked_transactions() -> None:
    if not (DATABASE_URL.lower().startswith("postgresql") or DATABASE_URL.lower().startswith("postgres")):
        return
    try:
        from sqlalchemy import text as _sa_text
        with engine.connect() as _conn:
            rows = _conn.execute(_sa_text(
                """
                SELECT pid, datname, application_name,
                       EXTRACT(EPOCH FROM (now() - xact_start))::int AS age_s,
                       left(query, 160) AS query
                FROM pg_stat_activity
                WHERE state = 'idle in transaction'
                  AND xact_start IS NOT NULL
                  AND now() - xact_start > (:age_s || ' seconds')::interval
                ORDER BY xact_start ASC
                LIMIT 20
                """
            ), {"age_s": _LEAK_GUARD_AGE_SECONDS}).fetchall()
        if rows:
            logger.warning(
                f"[DB LeakGuard] 发现 {len(rows)} 个挂起 idle-in-transaction 事务 "
                f"(>{_LEAK_GUARD_AGE_SECONDS}s)，{_LEAK_GUARD_KILL_SECONDS}s 后会被强制终止:"
            )
            for r in rows:
                logger.warning(
                    f"[DB LeakGuard]   pid={r.pid} db={r.datname} app={r.application_name} "
                    f"age={r.age_s}s sql={r.query!r}"
                )
            # P0 修复：对超过 kill 阈值的事务主动终止，释放锁和连接
            kill_pids = [r.pid for r in rows if r.age_s and r.age_s >= _LEAK_GUARD_KILL_SECONDS]
            for pid in kill_pids:
                try:
                    with engine.connect() as _kill_conn:
                        _kill_conn.execute(
                            _sa_text("SELECT pg_terminate_backend(:pid)"), {"pid": pid}
                        )
                    logger.warning(
                        f"[DB LeakGuard] 已强制终止 pid={pid} (超过 {_LEAK_GUARD_KILL_SECONDS}s)"
                    )
                except Exception as _kill_err:
                    logger.debug(f"[DB LeakGuard] terminate pid={pid} 失败: {_kill_err}")
    except Exception as _leak_check_err:
        logger.debug(f"[DB LeakGuard] 巡检失败(非致命): {_leak_check_err}")


def _pool_monitor_loop():
    while not _pool_monitor_stop.is_set():
        try:
            # 用 Event.wait 替代 sleep，收到停止信号能立即退出（最长等 120s）
            if _pool_monitor_stop.wait(120):
                break
            for _name, _eng in [
                ("Core", engine), ("Market", market_engine), ("Analytics", analytics_engine),
            ]:
                try:
                    pool = _eng.pool
                    logger.debug(
                        f"[DB Pool/{_name}] size={pool.size()} checkedin={pool.checkedin()} "
                        f"checkedout={pool.checkedout()} overflow={pool.overflow()}"
                    )
                except Exception:
                    pass
            _check_leaked_transactions()
        except Exception:
            pass


def start_pool_monitor() -> None:
    """启动连接池监控线程（进程级单例，重复调用幂等）。"""
    global _pool_monitor_started
    with _pool_monitor_lock:
        if _pool_monitor_started:
            return
        t = threading.Thread(target=_pool_monitor_loop, name="db-pool-monitor", daemon=True)
        t.start()
        _pool_monitor_started = True


def stop_pool_monitor() -> None:
    """停止连接池监控线程（供 lifespan shutdown / 测试 teardown 调用）。"""
    _pool_monitor_stop.set()


# 保留自动启动以兼容现有行为（生产后端 import 即监控）。
start_pool_monitor()
