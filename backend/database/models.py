from sqlalchemy import Column, Integer, BigInteger, String, DECIMAL, TIMESTAMP, ForeignKey, UniqueConstraint, Float, Date, DateTime, Text, Boolean, JSON, Index, Numeric
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import datetime

from .connection import Base, MarketBase, AnalyticsBase


class User(Base):
    """User account for authentication and account management.

    Supports login (username/email + bcrypt password_hash) and a tier field
    (free/pro/vip) consumed by auth/middleware. A legacy default user may keep
    password_hash=NULL (no password set yet) — see user_repo.user_has_password.
    """
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False)
    email = Column(String(100), nullable=True)
    password_hash = Column(String(255), nullable=True)  # bcrypt hash (passlib); NULL = not set
    is_active = Column(String(10), nullable=False, default="true")
    tier = Column(String(20), nullable=False, server_default="free")  # free/pro/vip
    # 阶段4 admin bootstrap:user/admin。default 用户升 admin(迁移 0006)。
    # JWT claim role(见 core.security.create_access_token)据此写入;
    # 中间件(Task 4.2)再据 token role 设 app.is_admin GUC → RLS 短路。
    role = Column(String(20), nullable=False, server_default="user")  # user/admin
    # VIP 共用 AI 选币：功能开关 / 自动跟投短线 / 默认会话
    coin_select_enabled = Column(String(10), nullable=False, server_default="false")
    coin_select_auto_follow = Column(String(10), nullable=False, server_default="false")
    coin_select_default_session = Column(String(64), nullable=True)

    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(
        TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )

    # Relationships
    accounts = relationship("Account", back_populates="user")
    auth_sessions = relationship("UserAuthSession", back_populates="user")
    subscription = relationship("UserSubscription", back_populates="user", uselist=False)


class Account(Base):
    """Trading Account with AI model configuration"""
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    version = Column(String(100), nullable=False, default="v1")
    
    # Account Identity
    name = Column(String(100), nullable=False)  # Display name (e.g., "GPT Trader", "Claude Analyst")
    account_type = Column(String(20), nullable=False, default="AI")  # "AI" or "MANUAL"
    is_active = Column(String(10), nullable=False, default="true")
    auto_trading_enabled = Column(String(10), nullable=False, default="true")
    
    # AI Model Configuration (for AI accounts)
    # Legacy fields - kept for backward compatibility, prefer llm_config_id
    model = Column(String(100), nullable=True, default="gpt-4")  # AI model name
    base_url = Column(String(500), nullable=True, default="https://api.openai.com/v1")  # API endpoint
    api_key = Column(String(500), nullable=True)  # API key for authentication
    
    # NEW: Reference to unified LLM configuration library
    llm_config_id = Column(Integer, ForeignKey("llm_configurations.id"), nullable=True)
    # NEW: Dual-model support — separate config for deep reasoning tasks
    llm_config_id_deep = Column(Integer, ForeignKey("llm_configurations.id"), nullable=True)
    
    # Trading Account Balances (USD for CRYPTO market)
    initial_capital = Column(DECIMAL(18, 2), nullable=False, default=10000.00)
    current_cash = Column(DECIMAL(18, 2), nullable=False, default=10000.00)
    frozen_cash = Column(DECIMAL(18, 2), nullable=False, default=0.00)

    # Hyperliquid Trading Configuration
    hyperliquid_enabled = Column(String(10), nullable=False, default="false")
    hyperliquid_environment = Column(String(20), nullable=True)  # "testnet" | "mainnet" | null
    hyperliquid_testnet_private_key = Column(String(500), nullable=True)  # Encrypted storage
    hyperliquid_mainnet_private_key = Column(String(500), nullable=True)  # Encrypted storage
    max_leverage = Column(Integer, nullable=True, default=20)  # Maximum allowed leverage
    default_leverage = Column(Integer, nullable=True, default=10)  # Default leverage for orders

    # Binance Trading Configuration
    binance_enabled = Column(String(10), nullable=False, default="false")
    binance_market_type = Column(String(20), nullable=True)  # "spot" | "futures" | null
    binance_testnet = Column(String(10), nullable=False, default="false")  # Use testnet
    binance_api_credentials = Column(String(1000), nullable=True)  # Encrypted API key:secret
    binance_max_leverage = Column(Integer, nullable=True, default=20)  # Maximum leverage for futures

    # Trading Mode: "live" = real exchange, "paper" = simulated
    trading_mode = Column(String(10), nullable=False, default="live")

    # VIP AI 选币：账户级开关（与用户级开关同时满足才可采纳）
    ai_coin_select_enabled = Column(String(10), nullable=False, server_default="false")

    # 交易员绑定的交易所 (asterdex/binance/hyperliquid/bybit/okx/gateio)
    # 默认 asterdex（首选，返利/积分生态）；老账户不批量改，仅新账户生效
    selected_exchange = Column(String(32), nullable=False, default="asterdex")

    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(
        TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )

    # Relationships
    user = relationship("User", back_populates="accounts")
    paper_balance = relationship("PaperBalance", back_populates="account", uselist=False, cascade="all, delete-orphan")
    positions = relationship("Position", back_populates="account")
    orders = relationship("Order", back_populates="account")
    prompt_binding = relationship(
        "AccountPromptBinding",
        back_populates="account",
        uselist=False,
        cascade="all, delete-orphan",
    )
    llm_config = relationship("LLMConfiguration", foreign_keys=[llm_config_id], back_populates="accounts")
    llm_config_deep = relationship("LLMConfiguration", foreign_keys=[llm_config_id_deep])
    personality = relationship(
        "TraderPersonality",
        back_populates="account",
        uselist=False,
        cascade="all, delete-orphan",
    )


class LLMConfiguration(Base):
    """
    Unified LLM Configuration Repository
    Stores all LLM model configurations that can be shared across:
    - AI Traders (accounts)
    - ATAS Strategy Generation
    - Signal Analysis
    - Any module requiring LLM access
    """
    __tablename__ = "llm_configurations"

    id = Column(Integer, primary_key=True, index=True)
    
    # Configuration Identity
    name = Column(String(100), nullable=False)  # e.g., "GPT-4o Trading", "DeepSeek Analysis"
    provider = Column(String(50), nullable=False)  # openai, deepseek, qwen, volcengine, custom
    description = Column(String(500), nullable=True)
    
    # Model Configuration
    model = Column(String(100), nullable=False)  # quick/flash default, e.g. deepseek-v4-flash
    model_deep = Column(String(100), nullable=True)  # optional pro/reasoner model, same API key
    base_url = Column(String(500), nullable=False)  # API endpoint
    api_key = Column(String(500), nullable=False)  # Encrypted API key

    # Usage routing: comma-separated usage keys (trading, coin_select, factor_mining,
    # journal, assistant, kline_analysis, evolution, news_intel). Empty = general purpose.
    usage_scope = Column(String(500), nullable=True)
    
    # Status & Flags
    is_default = Column(String(10), nullable=False, default="false")  # Is this the default config
    is_active = Column(String(10), nullable=False, default="true")  # Is this config enabled
    
    # Test Status
    last_tested_at = Column(TIMESTAMP, nullable=True)
    test_status = Column(String(20), nullable=True)  # success, failed, pending
    test_message = Column(String(500), nullable=True)
    
    # Usage Statistics
    usage_count = Column(Integer, nullable=False, default=0)  # How many times used
    last_used_at = Column(TIMESTAMP, nullable=True)
    
    # Timestamps
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(
        TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )

    # BYOK (§6.4): 配置归属租户(users.id)。NOT NULL(0004 回填到 default admin)。
    # 由应用层在创建/更新时 stamp;RLS 策略(0005)据此过滤跨租户读写。
    tenant_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    # Relationships - accounts using this config
    accounts = relationship("Account", foreign_keys="[Account.llm_config_id]", back_populates="llm_config")


class UserAuthSession(Base):
    __tablename__ = "user_auth_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    session_token = Column(String(64), unique=True, nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())

    user = relationship("User", back_populates="auth_sessions")


class RefreshToken(Base):
    """阶段2 auth: 服务端存储的 refresh token 记录(用于撤销 / 轮换)。

    与 ``UserAuthSession`` 是两套机制:
      - ``UserAuthSession``: 旧的 opaque session token(user_repo.create_auth_session
        有 timezone NameError bug,已弃用,不要复用)。
      - ``RefreshToken``: JWT refresh token 的 jti 服务端账本。客户端拿到 refresh
        JWT 后本地保存(Electron safeStorage),服务端只存 jti + revoked + expires_at。
        /api/auth/refresh 做轮换(旧 jti revoked=true,签发新 jti);
        /api/auth/logout 撤销当前 jti。

    ``revoked`` 用 String("true"/"false"),与 ``User.is_active`` 风格一致(本仓库历史
    上 boolean 字段普遍用 String 存储,迁移/兼容更稳)。
    """
    __tablename__ = "refresh_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    jti = Column(String(64), unique=True, nullable=False, index=True)  # JWT id
    revoked = Column(String(10), nullable=False, default="false")  # "true"/"false" (match is_active style)
    expires_at = Column(TIMESTAMP, nullable=False)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())

    user = relationship("User")


class AdminAuditLog(Base):
    """阶段4 Task 4.3: 管理员操作审计日志。

    GLOBAL 表(不带 tenant_id / 不挂 RLS 策略)—— 它只记录"哪个 admin 对哪个
    user 做了什么",本身没有租户维度;访问控制完全由路由层 ``require_admin``
    依赖兜住(role != admin → 403),非 admin 根本到不了这张表。

    ``detail`` 用 ``JSON``(SQLAlchemy 通用类型):PostgreSQL 上映射为 JSONB,
    SQLite 上映射为 TEXT(JSON 序列化),开发库与生产库都能跑。比直接 import
    ``JSONB``(sqlite 不存在)更稳,与本仓库既有 ``Column(JSON)`` 用法一致。
    """
    __tablename__ = "admin_audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    admin_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    action = Column(String(100), nullable=False)  # e.g. set_tier / set_status
    target_user_id = Column(Integer, nullable=True, index=True)
    detail = Column(JSON, nullable=True)  # 灵活载荷: {old,new} / {is_active} / {reason}
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())

    admin_user = relationship("User")


class Position(Base):
    __tablename__ = "positions"

    id = Column(Integer, primary_key=True, index=True)
    version = Column(String(100), nullable=False, default="v1")
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False, index=True)
    symbol = Column(String(20), nullable=False)
    name = Column(String(100), nullable=False)
    market = Column(String(10), nullable=False)
    quantity = Column(DECIMAL(18, 8), nullable=False, default=0)  # Support fractional crypto amounts
    available_quantity = Column(DECIMAL(18, 8), nullable=False, default=0)
    avg_cost = Column(DECIMAL(18, 6), nullable=False, default=0)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(
        TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )

    account = relationship("Account", back_populates="positions")


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    version = Column(String(100), nullable=False, default="v1")
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    order_no = Column(String(32), unique=True, nullable=False)
    symbol = Column(String(20), nullable=False)  # e.g., 'BTC/USD'
    name = Column(String(100), nullable=False)   # e.g., 'Bitcoin'
    market = Column(String(10), nullable=False, default="CRYPTO")
    side = Column(String(10), nullable=False)
    order_type = Column(String(20), nullable=False)
    price = Column(DECIMAL(18, 6))
    quantity = Column(DECIMAL(18, 8), nullable=False)  # Support fractional crypto amounts
    filled_quantity = Column(DECIMAL(18, 8), nullable=False, default=0)
    status = Column(String(20), nullable=False)

    # Hyperliquid specific fields
    hyperliquid_environment = Column(String(20), nullable=True)  # "testnet" | "mainnet" | null
    leverage = Column(Integer, nullable=True, default=1)  # Position leverage (1-50)
    margin_mode = Column(String(20), nullable=True, default="isolated")  # "cross" or "isolated"
    reduce_only = Column(String(10), nullable=True, default="false")  # Only close positions
    hyperliquid_order_id = Column(String(50), nullable=True)  # OID from Hyperliquid API
    liquidation_price = Column(DECIMAL(18, 6), nullable=True)  # Liquidation price for position

    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(
        TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )

    account = relationship("Account", back_populates="orders")
    trades = relationship("Trade", back_populates="order")


class Trade(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    symbol = Column(String(20), nullable=False)  # e.g., 'BTC/USD'
    name = Column(String(100), nullable=False)   # e.g., 'Bitcoin'
    market = Column(String(10), nullable=False, default="CRYPTO")
    side = Column(String(10), nullable=False)
    price = Column(DECIMAL(18, 6), nullable=False)
    quantity = Column(DECIMAL(18, 8), nullable=False)  # Support fractional crypto amounts
    commission = Column(DECIMAL(18, 6), nullable=False, default=0)
    trade_time = Column(TIMESTAMP, server_default=func.current_timestamp())

    # Hyperliquid environment tracking
    hyperliquid_environment = Column(String(20), nullable=True)  # "testnet" | "mainnet" | null (paper)

    order = relationship("Order", back_populates="trades")


class TradingConfig(Base):
    __tablename__ = "trading_configs"

    id = Column(Integer, primary_key=True, index=True)
    version = Column(String(100), nullable=False, default="v1")
    market = Column(String(10), nullable=False)
    min_commission = Column(Float, nullable=False)
    commission_rate = Column(Float, nullable=False)
    exchange_rate = Column(Float, nullable=False, default=1.0)
    min_order_quantity = Column(Integer, nullable=False, default=1)
    lot_size = Column(Integer, nullable=False, default=1)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(
        TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )

    __table_args__ = (UniqueConstraint('market', 'version'),)


class SystemConfig(Base):
    __tablename__ = "system_configs"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), unique=True, nullable=False)
    value = Column(Text, nullable=True)
    description = Column(String(500), nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(
        TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )


class CryptoPrice(MarketBase):
    __tablename__ = "crypto_prices"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    market = Column(String(10), nullable=False, default="CRYPTO")
    price = Column(DECIMAL(18, 6), nullable=False)
    price_date = Column(Date, nullable=False, index=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(
        TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )

    __table_args__ = (UniqueConstraint('symbol', 'market', 'price_date'),)


class CryptoKline(MarketBase):
    __tablename__ = "crypto_klines"

    id = Column(Integer, primary_key=True, index=True)
    exchange = Column(String(20), nullable=False, default="asterdex", index=True)
    symbol = Column(String(20), nullable=False, index=True)
    market = Column(String(10), nullable=False, default="CRYPTO")
    period = Column(String(10), nullable=False)  # 1m, 5m, 15m, 30m, 1h, 1d
    timestamp = Column(Integer, nullable=False, index=True)
    datetime_str = Column(String(50), nullable=False)
    environment = Column(String(20), nullable=False, default="mainnet", index=True)  # testnet or mainnet
    open_price = Column(DECIMAL(18, 6), nullable=True)
    high_price = Column(DECIMAL(18, 6), nullable=True)
    low_price = Column(DECIMAL(18, 6), nullable=True)
    close_price = Column(DECIMAL(18, 6), nullable=True)
    volume = Column(DECIMAL(18, 2), nullable=True)
    amount = Column(DECIMAL(18, 2), nullable=True)
    change = Column(DECIMAL(18, 6), nullable=True)
    percent = Column(DECIMAL(10, 4), nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())

    __table_args__ = (UniqueConstraint('exchange', 'symbol', 'market', 'period', 'timestamp', 'environment'),)


class SymbolCatalog(MarketBase):
    """四所可交易目录 — 选币硬门与 P1 全量采集名单来源。"""
    __tablename__ = "symbol_catalog"

    id = Column(Integer, primary_key=True, index=True)
    exchange = Column(String(20), nullable=False, index=True)
    symbol = Column(String(32), nullable=False, index=True)
    status = Column(String(20), nullable=False, default="trading")  # trading / delisted / unknown
    contract_type = Column(String(20), nullable=False, default="perp")
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp())

    __table_args__ = (UniqueConstraint("exchange", "symbol", name="uq_symbol_catalog_ex_sym"),)


class KlineSyncHeartbeat(MarketBase):
    """K线同步心跳 — 看板与门禁用。"""
    __tablename__ = "kline_sync_heartbeat"

    id = Column(Integer, primary_key=True, index=True)
    exchange = Column(String(20), nullable=False, index=True)
    period = Column(String(10), nullable=False, default="*")
    pool = Column(String(8), nullable=False, default="p0")  # p0 / p1 / p2
    last_success_at = Column(TIMESTAMP, nullable=True)
    symbols_ok = Column(Integer, nullable=False, default=0)
    symbols_fail = Column(Integer, nullable=False, default=0)
    meta_json = Column(Text, nullable=True)
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp())

    __table_args__ = (UniqueConstraint("exchange", "period", "pool", name="uq_kline_sync_hb_ex_period_pool"),)


class CryptoPriceTick(MarketBase):
    __tablename__ = "crypto_price_ticks"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    market = Column(String(10), nullable=False, default="CRYPTO")
    price = Column(DECIMAL(18, 8), nullable=False)
    event_time = Column(TIMESTAMP, nullable=False, index=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())


class AccountAssetSnapshot(Base):
    __tablename__ = "account_asset_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False, index=True)
    total_assets = Column(DECIMAL(18, 6), nullable=False)
    cash = Column(DECIMAL(18, 6), nullable=False)
    positions_value = Column(DECIMAL(18, 6), nullable=False)
    trigger_symbol = Column(String(20), nullable=True)
    trigger_market = Column(String(10), nullable=True, default="CRYPTO")
    event_time = Column(TIMESTAMP, nullable=False, index=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)

    account = relationship("Account")


class AccountStrategyConfig(Base):
    __tablename__ = "account_strategy_configs"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False, unique=True)
    price_threshold = Column(Float, nullable=False, default=1.0)  # Deprecated, kept for compatibility
    trigger_interval = Column(Integer, nullable=False, default=150)  # Trigger interval (seconds)
    # Note: Foreign key constraint exists at DB level (via migration), but not in ORM
    # because signal_pools table is managed via raw SQL, not SQLAlchemy models
    signal_pool_id = Column(Integer, nullable=True)
    enabled = Column(String(10), nullable=False, default="true")
    last_trigger_at = Column(TIMESTAMP, nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(
        TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )

    account = relationship("Account")


class GlobalSamplingConfig(Base):
    __tablename__ = "global_sampling_configs"

    id = Column(Integer, primary_key=True, index=True)
    sampling_interval = Column(Integer, nullable=False, default=18)  # Sampling interval (seconds)
    sampling_depth = Column(Integer, nullable=False, default=10)  # Sampling pool depth (10-60)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(
        TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )


class UserSubscription(Base):
    """User subscription for premium features"""
    __tablename__ = "user_subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True)
    subscription_type = Column(String(20), nullable=False, default="free")  # "free" | "premium"
    expires_at = Column(TIMESTAMP, nullable=True)  # NULL for free tier or lifetime premium
    max_sampling_depth = Column(Integer, nullable=False, default=10)  # Free: 10, Premium: up to 60
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(
        TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )

    # Relationship
    user = relationship("User", back_populates="subscription")


class AIDecisionLog(AnalyticsBase):
    __tablename__ = "ai_decision_logs"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, nullable=False, index=True)  # cross-db FK removed
    decision_time = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)
    reason = Column(String(1000), nullable=False)  # AI reasoning for the decision
    operation = Column(String(10), nullable=False)  # buy/sell/hold
    symbol = Column(String(20), nullable=True)  # symbol for buy/sell operations
    prev_portion = Column(DECIMAL(10, 6), nullable=False, default=0)  # previous balance portion
    target_portion = Column(DECIMAL(10, 6), nullable=False)  # target balance portion
    total_balance = Column(DECIMAL(18, 2), nullable=False)  # total balance at decision time
    executed = Column(String(10), nullable=False, default="false")  # whether the decision was executed
    order_id = Column(Integer, nullable=True, index=True)  # cross-db FK removed; linked order
    prompt_snapshot = Column(Text, nullable=True)
    reasoning_snapshot = Column(Text, nullable=True)
    decision_snapshot = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())

    # Hyperliquid environment tracking
    hyperliquid_environment = Column(String(20), nullable=True)  # "testnet" | "mainnet" | null (paper)
    wallet_address = Column(String(100), nullable=True, index=True)

    # Decision tracking fields for analysis
    prompt_template_id = Column(Integer, nullable=True, index=True)  # Link to strategy/prompt template
    signal_trigger_id = Column(Integer, nullable=True, index=True)  # Link to signal trigger
    hyperliquid_order_id = Column(String(100), nullable=True, index=True)  # Main order ID from Hyperliquid
    tp_order_id = Column(String(100), nullable=True)  # Take profit order ID
    sl_order_id = Column(String(100), nullable=True)  # Stop loss order ID
    realized_pnl = Column(DECIMAL(18, 6), nullable=True)  # Realized PnL (filled on user refresh)
    pnl_updated_at = Column(TIMESTAMP, nullable=True)  # When PnL was last updated

    # AI strategy tracking fields (新增)
    ai_strategy_id = Column(String(50), nullable=True, index=True)  # AI策略ID
    strategy_version = Column(Integer, nullable=True)  # 策略版本
    decision_quality_score = Column(DECIMAL(10, 4), nullable=True)  # 决策质量评分
    wisdom_applied = Column(JSON, nullable=True)  # 本次决策使用的交易智慧ID列表
    decision_source = Column(String(20), nullable=False, default="llm", index=True)  # D1: "llm" | "rule_engine" | "hybrid"

    # 三周期独立分析结果（由 MultiTimeframeOrchestrator 注入）
    short_bias = Column(String(20), nullable=True)       # short-term: "bullish" | "bearish" | "neutral"
    short_confidence = Column(Float, nullable=True)      # 0.0 ~ 1.0
    mid_bias = Column(String(20), nullable=True)         # mid-term: "bullish" | "bearish" | "neutral"
    mid_confidence = Column(Float, nullable=True)        # 0.0 ~ 1.0
    long_bias = Column(String(20), nullable=True)        # long-term: "bullish" | "bearish" | "neutral"
    long_confidence = Column(Float, nullable=True)       # 0.0 ~ 1.0

    # Relationships removed — cross-database (Account, Order in alpha_arena.db)
    # account_id / order_id columns kept for manual join queries


class ScalpVetoAudit(AnalyticsBase):
    """Scalp Flash Veto 审计 — 每笔 35-44 分 band 的 veto 决策。"""
    __tablename__ = "scalp_veto_audit"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, nullable=False, index=True, default=0)
    symbol = Column(String(20), nullable=False, index=True)
    score = Column(Integer, nullable=False, default=0)
    verdict = Column(String(20), nullable=False, default="accept")
    latency_ms = Column(Integer, nullable=False, default=0)
    source = Column(String(30), nullable=False, default="fallback")
    lane_decision_id = Column(String(32), nullable=True, index=True)
    rationale = Column(String(500), nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())


class DecisionRetrospective(AnalyticsBase):
    """D7: 决策复盘 — 平仓时自动记录判断对错+教训"""
    __tablename__ = "decision_retrospectives"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, nullable=False, index=True)  # cross-db FK removed
    symbol = Column(String(20), nullable=False, index=True)
    side = Column(String(10), nullable=False)
    entry_price = Column(DECIMAL(18, 6), nullable=False)
    exit_price = Column(DECIMAL(18, 6), nullable=False)
    realized_pnl = Column(DECIMAL(18, 6), nullable=False)
    pnl_pct = Column(DECIMAL(10, 4), nullable=False)
    exit_reason = Column(String(50), nullable=False)
    was_correct = Column(String(10), nullable=True)
    decision_snapshot = Column(Text, nullable=True)
    mistake_analysis = Column(Text, nullable=True)
    lesson_learned = Column(Text, nullable=True)
    market_regime_at_entry = Column(String(30), nullable=True)
    market_regime_at_exit = Column(String(30), nullable=True)
    holding_minutes = Column(Integer, nullable=True)
    strategy_id = Column(String(50), nullable=True, index=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())

    # Relationship removed — cross-database (Account in alpha_arena.db)


class PromptTemplate(Base):
    __tablename__ = "prompt_templates"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), nullable=False, index=True)  # Removed unique constraint to allow copies
    name = Column(String(200), nullable=False)
    description = Column(String(500), nullable=True)
    template_text = Column(Text, nullable=False)
    system_template_text = Column(Text, nullable=False)

    # User-level template support
    is_system = Column(String(10), nullable=False, default="false")  # System templates cannot be deleted
    is_deleted = Column(String(10), nullable=False, default="false")  # Soft delete
    created_by = Column(String(100), nullable=False, default="system")  # Creator identifier

    # Template validation & migration tracking
    # required_placeholders: JSON list of placeholder keys this template declares as mandatory
    #   e.g. ["factor_engine_status", "kline_technical_analysis", "confidence_calibration"]
    #   When non-empty, the renderer verifies these exist in the context BEFORE format_map.
    required_placeholders = Column(JSON, nullable=True)
    # is_legacy: if "true", this template uses the old implicit-injection path
    #   (string-replace of === 输出格式 === anchor). New templates set this to "false"
    #   and rely on declared required_placeholders instead.
    is_legacy = Column(String(10), nullable=False, default="true")

    updated_by = Column(String(100), nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(
        TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )

    account_bindings = relationship(
        "AccountPromptBinding",
        back_populates="prompt_template",
        cascade="all, delete-orphan",
    )


class AccountPromptBinding(Base):
    __tablename__ = "account_prompt_bindings"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False, unique=True)
    prompt_template_id = Column(Integer, ForeignKey("prompt_templates.id"), nullable=False)
    updated_by = Column(String(100), nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(
        TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )

    account = relationship("Account", back_populates="prompt_binding")
    prompt_template = relationship("PromptTemplate", back_populates="account_bindings")


class AIStrategy(Base):
    """AI驱动的交易策略 - 与 ai_strategies 表对应"""

    __tablename__ = "ai_strategies"

    id = Column(Integer, primary_key=True, index=True)
    strategy_id = Column(String(50), unique=True, nullable=False, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)

    # 提示词配置
    master_prompt_template_id = Column(Integer, ForeignKey("prompt_templates.id"), nullable=True)
    prompt_version = Column(Integer, nullable=True, default=1)
    prompt_variables = Column(JSON, nullable=True)

    # LLM 绑定：策略级覆盖（可选）。为空时跟随账户绑定，再回退全局默认。
    llm_config_id = Column(Integer, ForeignKey("llm_configurations.id"), nullable=True)
    llm_config_id_deep = Column(Integer, ForeignKey("llm_configurations.id"), nullable=True)

    # 触发配置
    signal_pool_ids = Column(JSON, nullable=True)  # List[int]，存为JSON
    trigger_mode = Column(String(20), nullable=True, default="hybrid")
    trigger_interval = Column(Integer, nullable=True)

    # 因子配置
    enabled_factors = Column(JSON, nullable=True)  # List[str]，存为JSON
    factor_weights = Column(JSON, nullable=True)   # Dict[str, float]

    # 风险配置
    max_position_size = Column(Float, nullable=True, default=0.2)
    stop_loss_pct = Column(Float, nullable=True, default=0.05)
    take_profit_pct = Column(Float, nullable=True, default=0.10)
    max_daily_loss = Column(Float, nullable=True, default=0.10)

    # 杠杆配置（合约交易核心）
    max_leverage = Column(Float, nullable=True, default=20.0)
    default_leverage = Column(Float, nullable=True, default=10.0)
    leverage_mode = Column(String(20), nullable=True, default="isolated")  # cross / isolated

    # 滚仓配置
    snowball_enabled = Column(Boolean, nullable=True, default=False)
    snowball_max_adds = Column(Integer, nullable=True, default=3)  # 最多追加次数
    snowball_profit_threshold = Column(Float, nullable=True, default=0.05)  # 盈利5%后可滚仓

    # 执行配置
    auto_execute = Column(Boolean, nullable=False, default=False)
    require_confirmation = Column(Boolean, nullable=False, default=True)
    min_confidence = Column(Float, nullable=True, default=0.6)

    # 交易对配置
    target_symbols = Column(JSON, nullable=True)  # List[str]，如 ["BTC", "ETH", "SOL"]
    primary_symbol = Column(String(20), nullable=True, default="BTC")  # 主要交易标的
    timeframe = Column(String(10), nullable=True, default="15m")  # 交易时间周期

    # 学习配置
    learning_enabled = Column(Boolean, nullable=False, default=True)
    optimization_target = Column(String(20), nullable=True, default="sharpe")
    training_frequency = Column(String(20), nullable=True, default="weekly")

    # 状态
    status = Column(String(20), nullable=False, default="draft")
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    activated_at = Column(TIMESTAMP, nullable=True)
    last_executed_at = Column(TIMESTAMP, nullable=True)
    updated_at = Column(
        TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )

    # 自主运行配置 (Phase 1)
    auto_mode = Column(String(20), nullable=True, default="semi_auto")  # full_auto / semi_auto / signal_only
    analysis_intervals = Column(JSON, nullable=True)  # {"short": 300, "mid": 1800, "long": 14400}
    last_short_analysis_at = Column(TIMESTAMP, nullable=True)
    last_mid_analysis_at = Column(TIMESTAMP, nullable=True)
    last_long_analysis_at = Column(TIMESTAMP, nullable=True)
    analysis_results_cache = Column(JSON, nullable=True)  # 最新三周期分析缓存

    # 策略周期槽位（用于准确识别策略属于哪个周期）
    # v3 整改: server_default='mid'，避免 legacy 策略归档后全部落到 "mid" 偏斜
    timeframe_tier = Column(String(10), nullable=True, server_default="mid")  # short / mid / long
    # 策略基因组（统一参数结构，用于进化和自适应）
    genome = Column(JSON, nullable=True)
    # 上次交易时间（用于频率控制）
    last_trade_at = Column(TIMESTAMP, nullable=True)
    # v3 整改: 进化血缘追踪 — 新策略由哪条父策略/模板衍生而来
    #   - parent_strategy_id: 父策略的 strategy_id（可能是另一条 AIStrategy 的 strategy_id 或 StrategyTemplate.template_id）
    #   - lineage_generation: 代数（原生=0，子代=parent+1）
    parent_strategy_id = Column(String(50), nullable=True, index=True)
    lineage_generation = Column(Integer, nullable=True, default=0)
    # 归档追踪
    archived_at = Column(TIMESTAMP, nullable=True)
    archive_reason = Column(String(500), nullable=True)

    # Relationships
    account = relationship("Account")


class StrategyAnalysisLog(AnalyticsBase):
    """策略自主分析日志 - 记录每次自主分析的结果和决策"""

    __tablename__ = "strategy_analysis_logs"

    id = Column(Integer, primary_key=True, index=True)
    strategy_id = Column(String(50), index=True)  # cross-db FK removed
    analysis_type = Column(String(10), nullable=False)  # short / mid / long / synthesize
    symbol = Column(String(20), nullable=True)

    market_cycle = Column(String(20), nullable=True)
    volatility_regime = Column(String(20), nullable=True)
    trend_direction = Column(String(20), nullable=True)
    trend_strength = Column(Float, nullable=True)
    current_price = Column(Float, nullable=True)
    atr_value = Column(Float, nullable=True)

    analysis_result = Column(JSON, nullable=True)
    decision_made = Column(String(20), nullable=True)  # trade / hold / skip
    decision_details = Column(JSON, nullable=True)

    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())


class StrategyOptimizationLog(AnalyticsBase):
    """策略优化日志 - 记录每次自主回测优化的过程"""

    __tablename__ = "strategy_optimization_logs"

    id = Column(Integer, primary_key=True, index=True)
    strategy_id = Column(String(50), index=True)  # cross-db FK removed
    iteration = Column(Integer, nullable=False, default=1)

    backtest_sharpe = Column(Float, nullable=True)
    backtest_win_rate = Column(Float, nullable=True)
    backtest_max_drawdown = Column(Float, nullable=True)
    backtest_total_return = Column(Float, nullable=True)
    backtest_profit_factor = Column(Float, nullable=True)

    passed = Column(Boolean, nullable=False, default=False)
    ai_suggestions = Column(JSON, nullable=True)
    parameter_changes = Column(JSON, nullable=True)

    status = Column(String(20), nullable=False, default="running")  # running / passed / failed
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())


class StrategyMemory(Base):
    """策略记忆聚合表 - 对应 strategy_memories"""

    __tablename__ = "strategy_memories"

    id = Column(Integer, primary_key=True, index=True)
    strategy_id = Column(String(50), ForeignKey("ai_strategies.strategy_id"), index=True)

    total_trades = Column(Integer, nullable=False, default=0)
    win_rate = Column(Float, nullable=False, default=0.0)
    avg_profit = Column(Float, nullable=False, default=0.0)
    avg_loss = Column(Float, nullable=False, default=0.0)
    sharpe_ratio = Column(Float, nullable=False, default=0.0)
    max_drawdown = Column(Float, nullable=False, default=0.0)

    performance_by_regime = Column(JSON, nullable=True)
    successful_patterns = Column(JSON, nullable=True)
    failed_patterns = Column(JSON, nullable=True)
    key_lessons = Column(JSON, nullable=True)
    performance_by_freq = Column(JSON, nullable=True)   # M-7 多频率复评指标 (key=频率 如 "15m"/"1h"/"4h")

    # ── 整改项4: 策略记忆增量改进 ────────────────────
    partial_pnl = Column(Float, default=0.0, nullable=True)          # 累计部分平仓PnL
    partial_close_count = Column(Integer, default=0, nullable=True)   # 减仓总次数
    last_reduce_at = Column(DateTime, nullable=True)                  # 最后一次减仓时间

    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp())


class StrategyTrade(Base):
    """策略级交易明细 - 对应 strategy_trades"""

    __tablename__ = "strategy_trades"

    id = Column(Integer, primary_key=True, index=True)
    strategy_id = Column(String(50), ForeignKey("ai_strategies.strategy_id"), index=True)

    # 交易信息
    symbol = Column(String(20), nullable=False)
    side = Column(String(10), nullable=False)  # long/short
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=True)
    position_size = Column(Float, nullable=False)
    leverage = Column(Float, nullable=True, default=1.0)

    # 决策上下文
    decision_context = Column(JSON, nullable=True)
    signal_context = Column(JSON, nullable=True)
    ai_reasoning = Column(Text, nullable=True)

    # 结果
    pnl = Column(Float, nullable=True)
    pnl_pct = Column(Float, nullable=True)
    holding_period = Column(Integer, nullable=True)

    # 质量评分
    decision_quality_score = Column(Float, nullable=True)
    execution_quality_score = Column(Float, nullable=True)

    # 状态
    status = Column(String(20), nullable=False, default="open")
    opened_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    closed_at = Column(TIMESTAMP, nullable=True)


class PromptTrainingRecord(Base):
    """提示词训练记录 - 对应 prompt_training_records"""

    __tablename__ = "prompt_training_records"

    id = Column(Integer, primary_key=True, index=True)
    strategy_id = Column(String(50), ForeignKey("ai_strategies.strategy_id"), index=True)
    base_prompt_id = Column(Integer, ForeignKey("prompt_templates.id"), nullable=False)
    optimized_prompt_id = Column(Integer, ForeignKey("prompt_templates.id"), nullable=True)
    training_metrics = Column(JSON, nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())


class SignalPerformanceHistory(Base):
    """信号表现历史 - 对应 signal_performance_history"""

    __tablename__ = "signal_performance_history"

    id = Column(Integer, primary_key=True, index=True)
    signal_id = Column(Integer, ForeignKey("signal_pools.id"), index=True)
    strategy_id = Column(String(50), ForeignKey("ai_strategies.strategy_id"), index=True)
    period_start = Column(TIMESTAMP, nullable=False)
    period_end = Column(TIMESTAMP, nullable=False)

    total_triggers = Column(Integer, nullable=False, default=0)
    successful_triggers = Column(Integer, nullable=False, default=0)
    win_rate = Column(Float, nullable=False, default=0.0)
    avg_profit = Column(Float, nullable=False, default=0.0)
    avg_loss = Column(Float, nullable=False, default=0.0)
    sharpe_ratio = Column(Float, nullable=False, default=0.0)

    market_regime = Column(String(50), nullable=True)
    regime_confidence = Column(Float, nullable=True)

    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())



class HyperliquidWallet(Base):
    """Store Hyperliquid wallet configurations per AI Trader per environment

    One-to-many relationship with Account. Each AI Trader can have multiple wallets
    (one for testnet, one for mainnet).
    """
    __tablename__ = "hyperliquid_wallets"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False, index=True)

    # Environment (testnet or mainnet)
    environment = Column(String(20), nullable=False)  # 'testnet' or 'mainnet'

    # Wallet credentials (encrypted)
    private_key_encrypted = Column(String(500), nullable=False)
    wallet_address = Column(String(100), nullable=False, index=True)  # Parsed from private key

    # Trading configuration
    max_leverage = Column(Integer, nullable=False, default=20)  # Maximum allowed leverage (1-50)
    default_leverage = Column(Integer, nullable=False, default=10)  # Default leverage for new orders

    # Status
    is_active = Column(String(10), nullable=False, default="true")

    # Metadata
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(
        TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )

    # Unique constraint: one wallet per account per environment
    __table_args__ = (
        UniqueConstraint('account_id', 'environment', name='uq_hyperliquid_wallets_account_environment'),
    )

    # Relationships
    account = relationship("Account")


class HyperliquidAccountSnapshot(Base):
    """Store Hyperliquid account state snapshots for audit and analysis"""
    __tablename__ = "hyperliquid_account_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    environment = Column(String(20), nullable=False, index=True)  # "testnet" | "mainnet"
    wallet_address = Column(String(100), nullable=True, index=True)
    snapshot_time = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)

    # Account state
    total_equity = Column(DECIMAL(18, 6), nullable=False)
    available_balance = Column(DECIMAL(18, 6), nullable=False)
    used_margin = Column(DECIMAL(18, 6), nullable=False)
    maintenance_margin = Column(DECIMAL(18, 6), nullable=False)

    # Snapshot metadata
    trigger_event = Column(String(50), nullable=True)  # "pre_decision", "post_order", etc.
    snapshot_data = Column(Text, nullable=True)  # JSON of full API response

    account = relationship("Account")


class HyperliquidPosition(Base):
    """Store Hyperliquid position snapshots"""
    __tablename__ = "hyperliquid_positions"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    environment = Column(String(20), nullable=False, index=True)  # "testnet" | "mainnet"
    wallet_address = Column(String(100), nullable=True, index=True)
    snapshot_time = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)

    symbol = Column(String(20), nullable=False)
    position_size = Column(DECIMAL(18, 8), nullable=False)  # Signed: positive=long, negative=short
    entry_price = Column(DECIMAL(18, 6), nullable=False)
    current_price = Column(DECIMAL(18, 6), nullable=False)
    position_value = Column(DECIMAL(18, 6), nullable=False)
    unrealized_pnl = Column(DECIMAL(18, 6), nullable=False)
    margin_used = Column(DECIMAL(18, 6), nullable=False)
    liquidation_price = Column(DECIMAL(18, 6), nullable=True)
    leverage = Column(Integer, nullable=False)

    # Link to order that created/modified this position
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)

    account = relationship("Account")
    order = relationship("Order")


class HyperliquidExchangeAction(Base):
    """Track every POST /exchange action for Hyperliquid accounts"""
    __tablename__ = "hyperliquid_exchange_actions"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False, index=True)
    environment = Column(String(20), nullable=False, index=True)
    wallet_address = Column(String(100), nullable=False, index=True)
    action_type = Column(String(50), nullable=False)  # e.g., create_order, set_leverage
    status = Column(String(20), nullable=False, default="success")  # success | error
    symbol = Column(String(20), nullable=True)
    side = Column(String(10), nullable=True)
    leverage = Column(Integer, nullable=True)
    size = Column(DECIMAL(24, 12), nullable=True)
    price = Column(DECIMAL(18, 6), nullable=True)
    notional = Column(DECIMAL(26, 10), nullable=True)
    request_weight = Column(Integer, nullable=False, default=1)
    request_payload = Column(Text, nullable=True)
    response_payload = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)

    account = relationship("Account")


class PerpFunding(MarketBase):
    """Store perpetual contract funding rate data from multiple exchanges"""
    __tablename__ = "perp_funding"

    id = Column(Integer, primary_key=True, index=True)
    exchange = Column(String(20), nullable=False, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    timestamp = Column(BigInteger, nullable=False, index=True)
    funding_rate = Column(DECIMAL(18, 8), nullable=False)
    mark_price = Column(DECIMAL(18, 6), nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())

    __table_args__ = (UniqueConstraint('exchange', 'symbol', 'timestamp'),)


class PriceSample(MarketBase):
    """Store price sampling data for persistent sampling pools"""
    __tablename__ = "price_samples"

    id = Column(Integer, primary_key=True, index=True)
    exchange = Column(String(20), nullable=False, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    price = Column(DECIMAL(18, 8), nullable=False)
    sample_time = Column(TIMESTAMP, nullable=False, index=True)
    account_id = Column(Integer, nullable=True)  # cross-db FK removed
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())

    # Relationship removed — cross-database (Account in alpha_arena.db)


class UserExchangeConfig(Base):
    """Store user exchange selection preferences"""
    __tablename__ = "user_exchange_config"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True)
    selected_exchange = Column(String(20), nullable=False, default="asterdex")
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(
        TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )

    # Relationships
    user = relationship("User")


class ExchangeCredential(Base):
    """Store API credentials for exchanges (Binance/Bybit/OKX/Gate.io/Asterdex).
    Hyperliquid uses its own HyperliquidWallet system; other exchanges use this table."""
    __tablename__ = "exchange_credentials"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    # 与 users.id 对齐；禁止长期保持 NULL（NULL 在 RLS 下会被当成「全局行」）
    tenant_id = Column(Integer, nullable=True, index=True)
    exchange = Column(String(32), nullable=False, index=True)
    label = Column(String(100), default="")
    api_key_encrypted = Column(Text, default="")
    api_secret_encrypted = Column(Text, default="")
    passphrase_encrypted = Column(Text, default="")
    testnet = Column(Boolean, default=True)
    enabled = Column(Boolean, default=False)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(
        TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )

    account = relationship("Account")
    user = relationship("User", foreign_keys=[user_id])


class KlineCollectionTask(MarketBase):
    """Store K-line data collection task status"""
    __tablename__ = "kline_collection_tasks"

    id = Column(Integer, primary_key=True, index=True)
    exchange = Column(String(20), nullable=False, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    start_time = Column(TIMESTAMP, nullable=False)
    end_time = Column(TIMESTAMP, nullable=False)
    period = Column(String(10), nullable=False, default="1m")
    status = Column(String(20), nullable=False, default="pending", index=True)
    progress = Column(Integer, nullable=False, default=0)
    total_records = Column(Integer, default=0)
    collected_records = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(
        TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )


class KlineAIAnalysisLog(AnalyticsBase):
    """Store K-line AI analysis logs for chart insights"""
    __tablename__ = "kline_ai_analysis_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)  # cross-db FK removed
    account_id = Column(Integer, nullable=False, index=True)  # cross-db FK removed

    # Analysis context
    symbol = Column(String(20), nullable=False, index=True)
    period = Column(String(10), nullable=False)  # K-line period (1m, 5m, 1h, etc.)
    user_message = Column(Text, nullable=True)  # User's custom question

    # AI model info
    model_used = Column(String(100), nullable=False)

    # Snapshots
    prompt_snapshot = Column(Text, nullable=True)  # Full prompt sent to AI
    analysis_result = Column(Text, nullable=True)  # AI's analysis response (Markdown)

    # Metadata
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)

    # Relationships removed — cross-database (User, Account in alpha_arena.db)


class AiPromptConversation(Base):
    """AI Prompt Generation Conversation Sessions"""
    __tablename__ = "ai_prompt_conversations"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(200), nullable=False, default="New Strategy Prompt")
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)
    updated_at = Column(
        TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )

    # Relationships
    user = relationship("User")
    messages = relationship(
        "AiPromptMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="AiPromptMessage.created_at"
    )


class AiPromptMessage(Base):
    """Messages in AI Prompt Generation Conversations"""
    __tablename__ = "ai_prompt_messages"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(Integer, ForeignKey("ai_prompt_conversations.id"), nullable=False, index=True)
    role = Column(String(20), nullable=False)  # "user" or "assistant"
    content = Column(Text, nullable=False)  # Message content (markdown)

    # For assistant messages: extracted prompt from ```prompt``` code block
    prompt_result = Column(Text, nullable=True)

    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)

    # Relationships
    conversation = relationship("AiPromptConversation", back_populates="messages")


class AiSignalConversation(Base):
    """AI Signal Creation Conversation Sessions"""
    __tablename__ = "ai_signal_conversations"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(200), nullable=False, default="New Signal")
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)
    updated_at = Column(
        TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )

    # Relationships
    user = relationship("User")
    messages = relationship(
        "AiSignalMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="AiSignalMessage.created_at"
    )


class AiSignalMessage(Base):
    """Messages in AI Signal Creation Conversations"""
    __tablename__ = "ai_signal_messages"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(Integer, ForeignKey("ai_signal_conversations.id"), nullable=False, index=True)
    role = Column(String(20), nullable=False)  # "user" or "assistant"
    content = Column(Text, nullable=False)  # Message content (markdown)

    # For assistant messages: extracted signal configs from ```signal-config``` code blocks
    signal_configs = Column(Text, nullable=True)  # JSON array of signal configurations

    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)

    # Relationships
    conversation = relationship("AiSignalConversation", back_populates="messages")


class AiAttributionConversation(Base):
    """AI Attribution Analysis Conversation Sessions"""
    __tablename__ = "ai_attribution_conversations"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(200), nullable=False, default="New Analysis")
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)
    updated_at = Column(
        TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )

    # Relationships
    user = relationship("User")
    messages = relationship(
        "AiAttributionMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="AiAttributionMessage.created_at"
    )


class AiAttributionMessage(Base):
    """Messages in AI Attribution Analysis Conversations"""
    __tablename__ = "ai_attribution_messages"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(Integer, ForeignKey("ai_attribution_conversations.id"), nullable=False, index=True)
    role = Column(String(20), nullable=False)  # "user" or "assistant"
    content = Column(Text, nullable=False)  # Message content (markdown)

    # For assistant messages: extracted diagnosis results from AI analysis
    diagnosis_result = Column(Text, nullable=True)  # JSON: diagnosis cards and prompt suggestions

    # Reasoning and analysis process storage (like AIDecisionLog.reasoning_snapshot)
    reasoning_snapshot = Column(Text, nullable=True)  # AI reasoning process
    analysis_log = Column(Text, nullable=True)  # JSON: tool calls and results log

    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)

    # Relationships
    conversation = relationship("AiAttributionConversation", back_populates="messages")


class AlphaAssistantConversation(Base):
    """Alpha 悬浮助手会话（Web / 飞书）"""
    __tablename__ = "alpha_assistant_conversations"

    id = Column(Integer, primary_key=True, index=True)
    session_uuid = Column(String(64), unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(200), nullable=False, default="新对话")
    channel = Column(String(20), nullable=False, default="web", index=True)  # web | feishu
    feishu_chat_id = Column(String(100), nullable=True, index=True)
    feishu_open_id = Column(String(100), nullable=True, index=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)
    updated_at = Column(
        TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )

    user = relationship("User")
    messages = relationship(
        "AlphaAssistantMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="AlphaAssistantMessage.created_at",
    )


class AlphaAssistantMessage(Base):
    """Alpha 助手消息"""
    __tablename__ = "alpha_assistant_messages"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(
        Integer, ForeignKey("alpha_assistant_conversations.id"), nullable=False, index=True
    )
    role = Column(String(20), nullable=False)  # user | assistant
    content = Column(Text, nullable=False)
    tool_result_json = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)

    conversation = relationship("AlphaAssistantConversation", back_populates="messages")


# ============================================================================
# Market Flow Data Tables (for fund flow analysis)
# ============================================================================

class ScalpSignalLog(Base):
    """短线信号日志（用于元标签/因子科研）。

    记录 scalp_factor_router 每次"触发的信号"（有方向且分数过门槛）+ 当时的因子快照，
    事后由结算任务回填 horizon 后的方向净收益与输赢标签。攒够数据后可在【真实信号】
    上训练元标签模型（预测"这一单会不会赢"），替代离线代理信号。
    """
    __tablename__ = "scalp_signal_log"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    symbol = Column(String(20), nullable=False, index=True)
    signal_ts = Column(BigInteger, nullable=False, index=True)   # 信号时刻（秒）
    direction = Column(String(10), nullable=True)                # long/short/neutral
    action = Column(String(10), nullable=True)                   # buy/sell/hold
    factor_score = Column(Float, nullable=True)
    threshold = Column(Float, nullable=True)
    entry_price = Column(Float, nullable=True)
    session_id = Column(String(64), nullable=True, index=True)
    account_id = Column(Integer, nullable=True)
    features_json = Column(Text, nullable=True)                  # 因子快照(JSON)：breakdown + 订单流字段

    # ── 事后结算 ──
    horizon_sec = Column(Integer, nullable=True)                 # 结算周期（秒）
    settled = Column(Boolean, nullable=False, default=False, index=True)
    settle_ts = Column(BigInteger, nullable=True)
    exit_price = Column(Float, nullable=True)
    fwd_ret = Column(Float, nullable=True)                       # 信号方向上的毛收益
    net_ret = Column(Float, nullable=True)                       # 扣往返成本后的净收益
    win = Column(Boolean, nullable=True)                         # net_ret>0
    settle_note = Column(String(64), nullable=True)

    __table_args__ = (
        Index("ix_scalp_signal_settle", "settled", "signal_ts"),
    )


class MarketTradesAggregated(MarketBase):
    """15-second aggregated trade data for CVD and Taker Volume analysis"""
    __tablename__ = "market_trades_aggregated"

    id = Column(Integer, primary_key=True, index=True)
    exchange = Column(String(20), nullable=False, default="asterdex", index=True)
    symbol = Column(String(20), nullable=False, index=True)
    timestamp = Column(BigInteger, nullable=False, index=True)  # milliseconds
    taker_buy_volume = Column(DECIMAL(24, 8), nullable=False, default=0)
    taker_sell_volume = Column(DECIMAL(24, 8), nullable=False, default=0)
    taker_buy_count = Column(Integer, nullable=False, default=0)
    taker_sell_count = Column(Integer, nullable=False, default=0)
    taker_buy_notional = Column(DECIMAL(24, 6), nullable=False, default=0)
    taker_sell_notional = Column(DECIMAL(24, 6), nullable=False, default=0)
    # [v6-S2-1] L2 重建层附加深度列：桶末帧前5档名义深度（px*sz，USD口径）；无订单簿帧时 NULL
    bid_depth_top5 = Column(DECIMAL(24, 6), nullable=True)
    ask_depth_top5 = Column(DECIMAL(24, 6), nullable=True)
    vwap = Column(DECIMAL(18, 6), nullable=True)
    high_price = Column(DECIMAL(18, 6), nullable=True)
    low_price = Column(DECIMAL(18, 6), nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())

    __table_args__ = (
        UniqueConstraint('exchange', 'symbol', 'timestamp',
                         name='market_trades_aggregated_exchange_symbol_timestamp_key'),
    )


class MarketOrderbookSnapshots(MarketBase):
    """Order book snapshots for depth ratio and liquidity analysis"""
    __tablename__ = "market_orderbook_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    exchange = Column(String(20), nullable=False, default="asterdex", index=True)
    symbol = Column(String(20), nullable=False, index=True)
    timestamp = Column(BigInteger, nullable=False, index=True)  # milliseconds
    best_bid = Column(DECIMAL(18, 6), nullable=True)
    best_ask = Column(DECIMAL(18, 6), nullable=True)
    spread = Column(DECIMAL(18, 6), nullable=True)
    bid_depth_5 = Column(DECIMAL(24, 8), nullable=False, default=0)
    ask_depth_5 = Column(DECIMAL(24, 8), nullable=False, default=0)
    bid_depth_10 = Column(DECIMAL(24, 8), nullable=False, default=0)
    ask_depth_10 = Column(DECIMAL(24, 8), nullable=False, default=0)
    bid_orders_count = Column(Integer, nullable=False, default=0)
    ask_orders_count = Column(Integer, nullable=False, default=0)
    raw_levels = Column(Text, nullable=True)  # JSON string of full orderbook
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())

    __table_args__ = (
        UniqueConstraint('exchange', 'symbol', 'timestamp',
                         name='market_orderbook_snapshots_exchange_symbol_timestamp_key'),
    )


class SymbolAuxTimeseries(MarketBase):
    """链上/社交/宏观指标时间序列 — 按采集时刻存储，对齐 K 线时 merge_asof。"""
    __tablename__ = "symbol_aux_timeseries"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(32), nullable=False, index=True)
    timestamp_ms = Column(BigInteger, nullable=False, index=True)
    fear_greed = Column(Float, nullable=True)
    btc_dominance = Column(Float, nullable=True)
    tvl = Column(Float, nullable=True)
    exchange_net_flow = Column(Float, nullable=True)
    whale_tx_count = Column(Integer, nullable=True)
    whale_tx_volume = Column(Float, nullable=True)
    active_addresses = Column(Float, nullable=True)
    social_score = Column(Float, nullable=True)
    news_sentiment = Column(Float, nullable=True)
    discussion_volume = Column(Float, nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())

    __table_args__ = (
        UniqueConstraint(
            "symbol", "timestamp_ms",
            name="symbol_aux_timeseries_symbol_timestamp_key",
        ),
    )


class MarketAssetMetrics(MarketBase):
    """Asset metrics snapshots for OI, Funding Rate, and Premium analysis"""
    __tablename__ = "market_asset_metrics"

    id = Column(Integer, primary_key=True, index=True)
    exchange = Column(String(20), nullable=False, default="asterdex", index=True)
    symbol = Column(String(20), nullable=False, index=True)
    timestamp = Column(BigInteger, nullable=False, index=True)  # milliseconds
    open_interest = Column(DECIMAL(24, 8), nullable=True)
    funding_rate = Column(DECIMAL(18, 8), nullable=True)
    mark_price = Column(DECIMAL(18, 6), nullable=True)
    oracle_price = Column(DECIMAL(18, 6), nullable=True)
    mid_price = Column(DECIMAL(18, 6), nullable=True)
    premium = Column(DECIMAL(18, 8), nullable=True)
    day_notional_volume = Column(DECIMAL(24, 6), nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())

    __table_args__ = (
        UniqueConstraint('exchange', 'symbol', 'timestamp',
                         name='market_asset_metrics_exchange_symbol_timestamp_key'),
    )


# ============================================================================
# Signal System Tables (for signal-based trading triggers)
# ============================================================================

class SignalDefinition(Base):
    """Signal definitions for market condition triggers"""
    __tablename__ = "signal_definitions"

    id = Column(Integer, primary_key=True, index=True)
    signal_name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    trigger_condition = Column(Text, nullable=False)  # JSONB stored as text
    enabled = Column(Boolean, nullable=True, default=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(),
                        onupdate=func.current_timestamp())


class SignalPool(Base):
    """Signal pools for grouping multiple signals"""
    __tablename__ = "signal_pools"

    id = Column(Integer, primary_key=True, index=True)
    pool_name = Column(String(100), nullable=False)
    signal_ids = Column(Text, nullable=False, default="[]")  # JSONB stored as text
    symbols = Column(Text, nullable=False, default="[]")  # JSONB stored as text
    logic = Column(String(10), nullable=True, default="OR")  # AND/OR/WEIGHTED logic
    weights = Column(Text, nullable=True)  # JSON: {signal_id: weight}, for WEIGHTED logic
    weight_threshold = Column(Float, nullable=True, default=0.5)  # Trigger threshold for WEIGHTED
    enabled = Column(Boolean, nullable=True, default=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())


class SignalTriggerLog(Base):
    """Logs of signal triggers for audit and analysis"""
    __tablename__ = "signal_trigger_logs"

    id = Column(Integer, primary_key=True, index=True)
    signal_id = Column(Integer, nullable=True)
    pool_id = Column(Integer, nullable=True)
    symbol = Column(String(20), nullable=False)
    trigger_value = Column(Text, nullable=True)  # JSONB stored as text
    triggered_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    market_regime = Column(Text, nullable=True)  # JSON: {"regime", "direction", "confidence", "reason"}


class TraderTriggerConfig(Base):
    """Configuration for trader trigger settings"""
    __tablename__ = "trader_trigger_config"

    trader_id = Column(String(36), primary_key=True)  # UUID as string
    scheduled_enabled = Column(Boolean, nullable=True, default=True)
    scheduled_interval = Column(Integer, nullable=True, default=30)
    signal_pool_id = Column(Integer, nullable=True)
    last_trigger_time = Column(TIMESTAMP, nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(),
                        onupdate=func.current_timestamp())


class MarketRegimeConfig(Base):
    """Configuration for Market Regime classification thresholds"""
    __tablename__ = "market_regime_configs"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    is_default = Column(Boolean, nullable=True, default=False)
    rolling_window = Column(Integer, nullable=True, default=48)
    # Breakout thresholds
    breakout_cvd_z = Column(Float, nullable=True, default=1.5)
    breakout_oi_z = Column(Float, nullable=True, default=1.0)
    breakout_price_atr = Column(Float, nullable=True, default=0.5)
    breakout_taker_high = Column(Float, nullable=True, default=1.8)
    breakout_taker_low = Column(Float, nullable=True, default=0.55)
    # Absorption thresholds
    absorption_cvd_z = Column(Float, nullable=True, default=1.5)
    absorption_price_atr = Column(Float, nullable=True, default=0.3)
    # Trap thresholds
    trap_cvd_z = Column(Float, nullable=True, default=1.0)
    trap_oi_z = Column(Float, nullable=True, default=-1.0)
    # Exhaustion thresholds
    exhaustion_cvd_z = Column(Float, nullable=True, default=1.0)
    exhaustion_rsi_high = Column(Float, nullable=True, default=70.0)
    exhaustion_rsi_low = Column(Float, nullable=True, default=30.0)
    # Stop Hunt thresholds
    stop_hunt_range_atr = Column(Float, nullable=True, default=1.0)
    stop_hunt_close_atr = Column(Float, nullable=True, default=0.3)
    # Noise thresholds
    noise_cvd_z = Column(Float, nullable=True, default=0.5)
    # Timestamps
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(),
                        onupdate=func.current_timestamp())


# ============================================================================
# DINGTALK NOTIFICATION SYSTEM
# ============================================================================

class DingTalkBot(Base):
    """钉钉机器人配置表"""
    __tablename__ = "dingtalk_bots"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    webhook_url = Column(Text, nullable=False)
    sign_secret = Column(String(255), nullable=True)
    enabled = Column(Boolean, default=True)

    # 推送开关配置
    notify_on_position_opened = Column(Boolean, default=True)
    notify_on_position_closed = Column(Boolean, default=True)
    notify_on_stop_loss_triggered = Column(Boolean, default=True)
    notify_on_take_profit_triggered = Column(Boolean, default=True)
    notify_on_position_scheduled = Column(Boolean, default=False)

    # 频率控制
    position_schedule_interval = Column(Integer, default=3600)  # 默认1小时
    max_notifications_per_hour = Column(Integer, default=20)

    # 波动预警配置
    volatility_alert_enabled = Column(Boolean, default=False)
    volatility_threshold = Column(DECIMAL(5, 2), default=5.0)
    volatility_timeframe = Column(Integer, default=300)  # 默认5分钟

    # 过滤配置
    account_ids = Column(Text, nullable=True)  # JSON数组
    symbol_filter = Column(Text, nullable=True)  # JSON数组

    # 统计信息
    total_sent_count = Column(Integer, default=0)
    last_sent_at = Column(TIMESTAMP, nullable=True)
    last_error_at = Column(TIMESTAMP, nullable=True)
    last_error_message = Column(Text, nullable=True)

    # 元数据
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(),
                        onupdate=func.current_timestamp())

    # Relationships
    notifications = relationship("DingTalkNotification", back_populates="bot", cascade="all, delete-orphan")
    stats = relationship("DingTalkNotificationStats", back_populates="bot", cascade="all, delete-orphan")


class DingTalkNotification(Base):
    """钉钉推送记录表"""
    __tablename__ = "dingtalk_notifications"

    id = Column(Integer, primary_key=True, index=True)
    bot_id = Column(Integer, ForeignKey("dingtalk_bots.id", ondelete="CASCADE"))
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)

    # 消息内容
    event_type = Column(String(50), nullable=False)  # position_opened/closed/sl_tp/volatility/scheduled
    message_type = Column(String(20), default="text")  # text/markdown/card
    title = Column(String(200), nullable=True)
    content = Column(Text, nullable=False)
    raw_data = Column(Text, nullable=True)  # JSON字符串

    # 推送状态
    status = Column(String(20), default="pending")  # pending/sent/failed
    dingtalk_msg_id = Column(String(100), nullable=True)

    # 响应信息
    response_code = Column(Integer, nullable=True)
    response_body = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0)

    # 时间戳
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    sent_at = Column(TIMESTAMP, nullable=True)

    # 关联数据
    position_id = Column(String(100), nullable=True)
    order_id = Column(String(100), nullable=True)
    symbol = Column(String(50), nullable=True)

    # Relationships
    bot = relationship("DingTalkBot", back_populates="notifications")
    account = relationship("Account")


class DingTalkNotificationStats(Base):
    """钉钉推送统计表"""
    __tablename__ = "dingtalk_notification_stats"

    id = Column(Integer, primary_key=True, index=True)
    bot_id = Column(Integer, ForeignKey("dingtalk_bots.id", ondelete="CASCADE"))
    date = Column(Date, nullable=False)

    # 统计数据
    total_sent = Column(Integer, default=0)
    total_success = Column(Integer, default=0)
    total_failed = Column(Integer, default=0)

    # 详细统计（JSON格式）
    event_breakdown = Column(Text, nullable=True)  # JSON字符串
    avg_response_time_ms = Column(Integer, nullable=True)
    max_response_time_ms = Column(Integer, nullable=True)
    error_breakdown = Column(Text, nullable=True)  # JSON字符串

    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(),
                        onupdate=func.current_timestamp())

    # Relationships
    bot = relationship("DingTalkBot", back_populates="stats")

    # Unique constraint
    __table_args__ = (UniqueConstraint('bot_id', 'date', name='uq_bot_date'),)


# ============================================================================
# CRYPTO market trading configuration constants
# ============================================================================
CRYPTO_MIN_COMMISSION = 0.1  # $0.1 minimum commission
CRYPTO_COMMISSION_RATE = 0.001  # 0.1% commission rate
CRYPTO_MIN_ORDER_QUANTITY = 1
CRYPTO_LOT_SIZE = 1


# ============================================================================
# Binance Position Tracking
# ============================================================================

class BinancePosition(Base):
    """
    Binance trading positions (futures and spot)

    Tracks positions opened on Binance exchange separately from Hyperliquid positions.
    Supports both futures (with leverage) and spot trading.
    """
    __tablename__ = "binance_positions"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, nullable=False, index=True)  # cross-db FK + CASCADE removed

    # Position identifiers
    position_id = Column(String(100), nullable=False, unique=True, index=True)  # Binance position ID
    order_id = Column(String(100), nullable=True)  # Opening order ID

    # Symbol information
    symbol = Column(String(50), nullable=False, index=True)  # Trading pair (e.g., "ETH/USDT", "ETHUSDT")

    # Position details
    side = Column(String(10), nullable=False)  # 'long' or 'short'
    size = Column(DECIMAL(18, 8), nullable=False)  # Position size (positive for both long and short)

    # Price information
    entry_price = Column(DECIMAL(18, 8), nullable=True)  # Average entry price
    mark_price = Column(DECIMAL(18, 8), nullable=True)  # Current mark price from exchange
    liquidation_price = Column(DECIMAL(18, 8), nullable=True)  # Liquidation price (for futures)

    # Profit and loss
    unrealized_pnl = Column(DECIMAL(18, 8), nullable=True)  # Unrealized PnL
    realized_pnl = Column(DECIMAL(18, 8), nullable=True)  # Realized PnL (after position closed)

    # Leverage and margin (futures only)
    leverage = Column(Integer, nullable=True)  # Leverage multiplier (1-125 for futures)
    margin_type = Column(String(20), nullable=True)  # 'cross' or 'isolated'
    notional_value = Column(DECIMAL(18, 8), nullable=True)  # Position value (size * mark_price)

    # Take Profit and Stop Loss
    tp_order_id = Column(String(100), nullable=True)  # Take profit order ID
    tp_price = Column(DECIMAL(18, 8), nullable=True)  # Take profit price
    sl_order_id = Column(String(100), nullable=True)  # Stop loss order ID
    sl_price = Column(DECIMAL(18, 8), nullable=True)  # Stop loss price

    # Position status
    status = Column(String(20), nullable=False, default='open', index=True)  # 'open', 'closed', 'closing'
    position_side = Column(String(20), nullable=True)  # 'LONG' or 'SHORT' (for futures dual-side mode)

    # Timestamps
    opened_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    closed_at = Column(TIMESTAMP, nullable=True)
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(),
                        onupdate=func.current_timestamp())
    synced_at = Column(TIMESTAMP, server_default=func.current_timestamp())  # Last sync with Binance API


class RiskControlConfig(Base):
    """
    Risk Control Configuration per account
    风控配置表 - 每个账户可配置不同的风控参数
    """
    __tablename__ = "risk_control_configs"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True)

    # 单币种最大仓位比例 (占总权益)
    max_single_symbol_ratio = Column(Float, nullable=False, default=0.30)  # 30%

    # 日亏损熔断阈值
    daily_loss_limit_ratio = Column(Float, nullable=False, default=0.05)  # 5%

    # 总仓位限制 (总仓位价值 / 总权益 的最大倍数)
    max_total_position_multiple = Column(Float, nullable=False, default=3.0)  # 3倍

    # 保证金使用率限制
    max_margin_usage_ratio = Column(Float, nullable=False, default=0.70)  # 70%

    # 熔断冷却时间 (小时)
    circuit_breaker_cooldown_hours = Column(Integer, nullable=False, default=24)

    # 启用开关
    enable_single_symbol_limit = Column(String(10), nullable=False, default="true")
    enable_daily_loss_breaker = Column(String(10), nullable=False, default="true")
    enable_total_position_limit = Column(String(10), nullable=False, default="true")
    enable_margin_check = Column(String(10), nullable=False, default="true")

    # 新增完整风控字段
    max_trade_amount = Column(Float, nullable=True, default=1000.0)          # 单笔最大金额 (USD)
    daily_trade_count_limit = Column(Integer, nullable=True, default=50)     # 每日交易次数上限
    max_concurrent_positions = Column(Integer, nullable=True, default=10)    # 最大同时持仓数
    per_symbol_max_position = Column(Integer, nullable=True, default=3)      # 单品种最大仓位数
    global_stop_loss_pct = Column(Float, nullable=True, default=0.10)        # 账户级止损百分比

    # 新增风控启用开关
    enable_trade_amount_limit = Column(String(10), nullable=False, default="true")
    enable_trade_count_limit = Column(String(10), nullable=False, default="true")
    enable_concurrent_position_limit = Column(String(10), nullable=False, default="true")

    # 状态
    is_active = Column(String(10), nullable=False, default="true")

    # Timestamps
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(),
                        onupdate=func.current_timestamp())

    # Unique constraint: one config per account
    __table_args__ = (
        UniqueConstraint('account_id', name='uq_risk_control_configs_account'),
    )


class RiskControlEvent(AnalyticsBase):
    """
    Risk Control Event Log
    风控事件日志 - 记录熔断等风控事件
    """
    __tablename__ = "risk_control_events"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, nullable=False, index=True)  # cross-db FK + CASCADE removed

    # Event type: circuit_breaker, position_limit_blocked, margin_blocked, etc.
    event_type = Column(String(50), nullable=False, index=True)

    # Event timestamp
    event_time = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)

    # Event details (JSON string)
    details = Column(Text, nullable=True)

    # Resolution timestamp (when the event was resolved, e.g., cooldown ended)
    resolved_at = Column(TIMESTAMP, nullable=True)

    # Timestamps
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())


# ============================================================================
# Smart Signal Generation System Models (MDSPG)
# ============================================================================

class GeneratedSignalHistory(AnalyticsBase):
    """
    智能生成的信号历史记录
    Tracks AI-generated signal configurations and their performance
    """
    __tablename__ = "generated_signal_history"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    strategy_type = Column(String(50), nullable=False)  # trend/reversal/breakout/scalping
    direction = Column(String(10), nullable=True)  # long/short/auto
    risk_level = Column(String(20), nullable=True)  # conservative/moderate/aggressive
    time_window = Column(String(10), nullable=True)  # 5m/15m/1h etc.
    
    # Generated signal configuration
    signal_config = Column(JSON, nullable=False)  # Complete trigger_condition
    
    # Market context at creation
    market_regime_at_creation = Column(String(50), nullable=True)  # breakout/absorption/noise etc.
    market_direction_at_creation = Column(String(20), nullable=True)  # bullish/bearish/neutral
    
    # Backtest metrics at creation
    backtest_metrics = Column(JSON, nullable=True)  # {win_rate, sharpe, total_triggers, etc.}
    effectiveness_score = Column(Float, nullable=True)  # 0-100 综合评分
    
    # Tracking
    is_active = Column(Boolean, nullable=False, default=True)
    actual_performance = Column(JSON, nullable=True)  # Post-creation tracking
    signal_id_created = Column(Integer, nullable=True)  # If user created a signal from this
    
    # Timestamps
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(),
                        onupdate=func.current_timestamp())


class PatternDefinition(AnalyticsBase):
    """
    交易模式定义库
    Stores reusable trading pattern templates with historical performance
    """
    __tablename__ = "pattern_definitions"

    id = Column(Integer, primary_key=True, index=True)
    pattern_name = Column(String(100), nullable=False, unique=True)
    pattern_type = Column(String(50), nullable=False)  # reversal/continuation/breakout/momentum
    
    # Pattern conditions
    conditions = Column(JSON, nullable=False)  # List of {metric, operator, threshold, time_window}
    direction = Column(String(10), nullable=False)  # long/short
    typical_hold_bars = Column(Integer, nullable=True)  # Typical holding period
    
    # Performance metrics (updated periodically)
    historical_win_rate = Column(Float, nullable=True)
    historical_avg_return = Column(Float, nullable=True)
    historical_sharpe = Column(Float, nullable=True)
    sample_count = Column(Integer, nullable=True, default=0)
    
    # Market regime affinity (which regimes this pattern works best in)
    best_regimes = Column(JSON, nullable=True)  # ["breakout", "continuation"]
    
    # Metadata
    is_system = Column(Boolean, nullable=False, default=False)  # System preset vs user created
    is_active = Column(Boolean, nullable=False, default=True)
    description = Column(Text, nullable=True)
    
    # Timestamps
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(),
                        onupdate=func.current_timestamp())
    last_backtested_at = Column(TIMESTAMP, nullable=True)


class MarketAnalysisSnapshot(AnalyticsBase):
    """
    市场分析快照
    Stores periodic market state snapshots for historical analysis
    """
    __tablename__ = "market_analysis_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    timestamp = Column(BigInteger, nullable=False, index=True)  # Unix timestamp in ms
    period = Column(String(10), nullable=False)  # 5m/15m/1h etc.
    
    # Market regime classification
    regime_type = Column(String(50), nullable=True)  # breakout/absorption/noise etc.
    regime_direction = Column(String(20), nullable=True)  # bullish/bearish/neutral
    regime_confidence = Column(Float, nullable=True)  # 0-1 confidence score
    
    # Indicator values snapshot
    indicator_snapshot = Column(JSON, nullable=True)  # {rsi, macd, boll_position, cvd, oi_delta, etc.}
    
    # Adaptive trading parameters at this point
    adaptive_parameters = Column(JSON, nullable=True)  # {position_size_modifier, stop_loss_atr, etc.}
    
    # Price context
    price = Column(Float, nullable=True)
    atr = Column(Float, nullable=True)
    volatility_percentile = Column(Float, nullable=True)  # Where current vol ranks historically
    
    # Timestamps
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())

    __table_args__ = (
        UniqueConstraint('symbol', 'timestamp', 'period', name='uq_market_snapshot_symbol_ts_period'),
    )


# ============================================
# ATAS V2 Models
# ============================================

class ATASStrategy(Base):
    """ATAS V2 策略表"""
    __tablename__ = "atas_strategies"

    id = Column(Integer, primary_key=True, index=True)
    strategy_id = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    
    # 策略类型与来源
    strategy_type = Column(String(50), nullable=False)
    generation_method = Column(String(50), nullable=False)
    
    # 策略逻辑 (JSON格式)
    entry_logic = Column(JSON, nullable=True)
    exit_logic = Column(JSON, nullable=True)
    position_sizing = Column(JSON, nullable=True)
    risk_params = Column(JSON, nullable=True)
    
    # 策略代码
    code_python = Column(Text, nullable=True)
    code_pinescript = Column(Text, nullable=True)
    
    # 因子依赖
    required_factors = Column(JSON, nullable=True)  # List of factor IDs
    factor_weights = Column(JSON, nullable=True)  # Dict of factor weights
    
    # AI元数据
    ai_model = Column(String(100), nullable=True)
    prompt_template_id = Column(Integer, nullable=True)
    user_input = Column(Text, nullable=True)
    generation_timestamp = Column(TIMESTAMP, nullable=True)
    confidence_score = Column(Float, nullable=True)
    
    # 状态与版本
    status = Column(String(50), nullable=False, default='draft')
    version = Column(Integer, nullable=False, default=1)
    parent_strategy_id = Column(Integer, nullable=True)
    
    # 性能统计
    total_trades = Column(Integer, nullable=False, default=0)
    win_rate = Column(Float, nullable=False, default=0)
    sharpe_ratio = Column(Float, nullable=False, default=0)
    max_drawdown = Column(Float, nullable=False, default=0)
    total_return = Column(Float, nullable=False, default=0)
    
    # 时间戳
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp())
    last_run_at = Column(TIMESTAMP, nullable=True)
    
    # 标签与分类
    tags = Column(JSON, nullable=True)
    category = Column(String(100), nullable=True)
    
    # 权限与可见性
    is_public = Column(Boolean, nullable=False, default=False)
    is_template = Column(Boolean, nullable=False, default=False)


class ATASFactor(Base):
    """ATAS V2 因子定义表"""
    __tablename__ = "atas_factors"

    id = Column(Integer, primary_key=True, index=True)
    factor_id = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    display_name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    
    # 因子分类
    category = Column(String(50), nullable=False, index=True)
    subcategory = Column(String(100), nullable=True)
    
    # 因子计算
    calculation_method = Column(String(50), nullable=False)
    code = Column(Text, nullable=True)
    dependencies = Column(JSON, nullable=True)
    
    # 因子参数
    parameters = Column(JSON, nullable=True)
    required_data_fields = Column(JSON, nullable=True)
    lookback_period = Column(Integer, nullable=False, default=20)
    
    # 因子性能
    ic = Column(Float, nullable=True)
    ir = Column(Float, nullable=True)
    turnover = Column(Float, nullable=True)
    coverage = Column(Float, nullable=True)
    group_returns = Column(JSON, nullable=True)
    
    # 计算性能
    avg_compute_time_ms = Column(Float, nullable=True)
    cache_enabled = Column(Boolean, nullable=False, default=True)
    cache_ttl = Column(Integer, nullable=False, default=3600)
    
    # 状态
    status = Column(String(50), nullable=False, default='active')
    version = Column(Integer, nullable=False, default=1)
    
    # 时间戳
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp())
    last_calculated_at = Column(TIMESTAMP, nullable=True)
    
    # 使用统计
    usage_count = Column(Integer, nullable=False, default=0)
    
    # 标签
    tags = Column(JSON, nullable=True)


class ATASFactorCache(Base):
    """ATAS V2 因子缓存表"""
    __tablename__ = "atas_factor_cache"

    id = Column(Integer, primary_key=True, index=True)
    cache_key = Column(String(255), unique=True, nullable=False, index=True)
    factor_id = Column(String(64), nullable=False)
    symbol = Column(String(50), nullable=False)
    timeframe = Column(String(20), nullable=False)
    
    # 缓存值
    value = Column(JSON, nullable=False)
    
    # 元数据
    calculated_at = Column(TIMESTAMP, nullable=False)
    expires_at = Column(TIMESTAMP, nullable=False, index=True)
    
    # 计算信息
    compute_time_ms = Column(Float, nullable=True)
    data_points = Column(Integer, nullable=True)


class ATASPromptTemplate(Base):
    """ATAS V2 AI提示词模板表"""
    __tablename__ = "atas_prompt_templates"

    id = Column(Integer, primary_key=True, index=True)
    template_id = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    
    # 模板分类
    category = Column(String(100), nullable=False, index=True)
    strategy_type = Column(String(50), nullable=True)
    
    # 提示词内容
    system_prompt = Column(Text, nullable=False)
    user_prompt_template = Column(Text, nullable=False)
    
    # AI模型配置
    recommended_model = Column(String(100), nullable=True)
    temperature = Column(Float, nullable=False, default=0.7)
    max_tokens = Column(Integer, nullable=False, default=2000)
    
    # 变量定义
    template_variables = Column(JSON, nullable=True)
    
    # 输出格式
    expected_output_format = Column(String(50), nullable=True)
    output_schema = Column(JSON, nullable=True)
    
    # 性能统计
    usage_count = Column(Integer, nullable=False, default=0)
    avg_generation_time_seconds = Column(Float, nullable=True)
    success_rate = Column(Float, nullable=True)
    avg_confidence_score = Column(Float, nullable=True)
    
    # 版本与状态
    version = Column(Integer, nullable=False, default=1)
    status = Column(String(50), nullable=False, default='active')
    parent_template_id = Column(Integer, nullable=True)
    
    # 示例
    example_inputs = Column(JSON, nullable=True)
    example_outputs = Column(JSON, nullable=True)
    
    # 时间戳
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp())
    
    # 标签
    tags = Column(JSON, nullable=True)


class ATASAIGenerationHistory(Base):
    """ATAS V2 AI生成历史表"""
    __tablename__ = "atas_ai_generation_history"

    id = Column(Integer, primary_key=True, index=True)
    generation_id = Column(String(64), unique=True, nullable=False, index=True)
    
    # 输入
    user_input = Column(Text, nullable=False)
    prompt_template_id = Column(Integer, nullable=True)
    market_context = Column(JSON, nullable=True)
    
    # AI配置
    ai_model = Column(String(100), nullable=False)
    temperature = Column(Float, nullable=True)
    max_tokens = Column(Integer, nullable=True)
    
    # 输出
    generated_strategy_id = Column(String(64), nullable=True)
    generated_code = Column(Text, nullable=True)
    generated_params = Column(JSON, nullable=True)
    confidence_score = Column(Float, nullable=True)
    
    # 执行信息
    status = Column(String(50), nullable=False, default='success')
    error_message = Column(Text, nullable=True)
    execution_time_seconds = Column(Float, nullable=True)
    
    # 用户反馈
    user_rating = Column(Integer, nullable=True)
    user_feedback = Column(Text, nullable=True)
    accepted = Column(Boolean, nullable=True)
    
    # 时间戳
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())


class VisualStrategy(Base):
    """
    可视化策略表
    存储通过可视化设计器创建的策略
    """
    __tablename__ = "visual_strategies"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    # 策略基本信息
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    version = Column(Integer, nullable=False, default=1)
    
    # 策略内容
    nodes = Column(JSON, nullable=False)  # 节点配置
    edges = Column(JSON, nullable=False)  # 连接配置
    generated_code = Column(Text, nullable=True)  # 生成的代码
    
    # 状态
    status = Column(String(20), nullable=False, default='draft')  # draft, active, archived
    
    # 性能指标
    backtest_result = Column(JSON, nullable=True)
    live_performance = Column(JSON, nullable=True)
    
    # 时间戳
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(
        TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )
    
    # Relationships
    executions = relationship("StrategyExecution", back_populates="strategy", cascade="all, delete-orphan")


class StrategyExecution(Base):
    """
    策略执行历史表
    记录每次策略执行的详细信息
    """
    __tablename__ = "strategy_executions"

    id = Column(Integer, primary_key=True, index=True)
    strategy_id = Column(Integer, ForeignKey("visual_strategies.id"), nullable=False)
    
    # 执行类型
    execution_type = Column(String(20), nullable=False)  # backtest, paper, live
    
    # 执行参数
    start_time = Column(TIMESTAMP, nullable=True)
    end_time = Column(TIMESTAMP, nullable=True)
    symbols = Column(JSON, nullable=True)
    config = Column(JSON, nullable=True)
    
    # 执行结果
    status = Column(String(20), nullable=False, default='pending')  # pending, running, completed, failed
    result = Column(JSON, nullable=True)
    logs = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    
    # 性能指标
    total_trades = Column(Integer, nullable=True)
    win_rate = Column(Float, nullable=True)
    profit_loss = Column(Float, nullable=True)
    sharpe_ratio = Column(Float, nullable=True)
    max_drawdown = Column(Float, nullable=True)
    
    # 时间戳
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(
        TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )
    
    # Relationships
    strategy = relationship("VisualStrategy", back_populates="executions")


class StrategyNodeTemplate(Base):
    """
    策略节点模板库
    存储可用的节点类型和配置模板
    """
    __tablename__ = "strategy_node_templates"

    id = Column(Integer, primary_key=True, index=True)
    
    # 节点基本信息
    category = Column(String(50), nullable=False)  # data_source, indicator, condition, signal, execution, risk
    name = Column(String(100), nullable=False)
    type = Column(String(50), nullable=False, unique=True)
    icon = Column(String(50), nullable=True)
    description = Column(Text, nullable=True)
    
    # 配置Schema
    config_schema = Column(JSON, nullable=True)
    
    # 代码生成模板
    code_template = Column(Text, nullable=True)
    
    # 状态
    is_active = Column(String(10), nullable=False, default='true')
    
    # 使用统计
    usage_count = Column(Integer, nullable=False, default=0)
    
    # 时间戳
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(
        TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )


class CustomTradingStyle(Base):
    """用户自定义交易风格"""
    __tablename__ = "custom_trading_styles"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(50), unique=True, nullable=False, index=True)   # 唯一标识 e.g. custom_my_style
    name = Column(String(100), nullable=False)                          # 显示名称
    description = Column(String(500), nullable=True)                    # 风格简介
    template = Column(Text, nullable=True)                              # 策略需求模板
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(
        TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp()
    )


# ──────────────────────────────────────────────
# Paper Trading (内置模拟交易)
# ──────────────────────────────────────────────

class PaperBalance(Base):
    """虚拟资金账户 — 每个 Account 在 paper 模式下拥有一份"""
    __tablename__ = "paper_balances"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False, unique=True, index=True)

    initial_balance = Column(Float, nullable=False, default=100000.0)
    total_equity = Column(Float, nullable=False, default=100000.0)
    available_balance = Column(Float, nullable=False, default=100000.0)
    frozen_margin = Column(Float, nullable=False, default=0.0)
    unrealized_pnl = Column(Float, nullable=False, default=0.0)
    realized_pnl = Column(Float, nullable=False, default=0.0)
    total_fee_paid = Column(Float, nullable=False, default=0.0)

    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp())
    last_reset_at = Column(TIMESTAMP, nullable=True)

    account = relationship("Account", back_populates="paper_balance")


class PaperPosition(Base):
    """虚拟持仓"""
    __tablename__ = "paper_positions"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False, index=True)
    strategy_id = Column(String(50), nullable=True, index=True)

    symbol = Column(String(20), nullable=False, index=True)
    side = Column(String(10), nullable=False)           # long / short
    size = Column(Float, nullable=False)
    entry_price = Column(Float, nullable=False)
    mark_price = Column(Float, nullable=False, default=0.0)
    leverage = Column(Float, nullable=False, default=1.0)
    margin = Column(Float, nullable=False, default=0.0)
    unrealized_pnl = Column(Float, nullable=False, default=0.0)
    liquidation_price = Column(Float, nullable=False, default=0.0)

    tp_price = Column(Float, nullable=True)
    sl_price = Column(Float, nullable=True)
    trailing_stop_price = Column(Float, nullable=True)

    status = Column(String(20), nullable=False, default="open", index=True)  # open / closed / liquidated
    close_price = Column(Float, nullable=True)
    close_reason = Column(String(100), nullable=True)    # manual / tp / sl / trailing / liquidation / pos_mgmt_*

    # 分批止盈累计（用于平仓时给学习系统完整的 PnL）
    partial_realized_pnl = Column(Float, nullable=False, default=0.0)
    partial_fee_paid = Column(Float, nullable=False, default=0.0)
    original_size = Column(Float, nullable=True)

    # 已触发的止盈级别（0=未触发, 1=L1已平30%, 2=L2已平30%, 3=L3已全平）
    tp_level_reached = Column(Integer, nullable=False, default=0)

    # 多策略逐仓 + 加仓补仓支持
    timeframe_tier = Column(String(10), nullable=True, index=True)  # short / mid / long
    add_count = Column(Integer, nullable=False, default=0)
    original_margin = Column(Float, nullable=True, default=0.0)
    last_add_at = Column(TIMESTAMP, nullable=True)
    dca_count = Column(Integer, nullable=False, default=0)
    dca_total_added = Column(Float, nullable=False, default=0.0)

    # 虚拟子仓位身份标签
    trade_nature = Column(String(20), nullable=True, index=True)  # trend_follow / swing / intraday
    expected_hold_hours = Column(Float, nullable=True)
    reduce_count = Column(Integer, nullable=False, default=0)
    last_reduce_at = Column(TIMESTAMP, nullable=True)

    # B 方案退出防护状态：重启后也能恢复峰值利润、健康分和分批/追踪状态
    peak_unrealized_pnl = Column(Float, nullable=False, default=0.0)
    peak_pnl_pct = Column(Float, nullable=False, default=0.0)
    health_score = Column(Float, nullable=True)
    health_regime = Column(String(30), nullable=True)
    exit_state_json = Column(Text, nullable=True)

    opened_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    closed_at = Column(TIMESTAMP, nullable=True)
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp())

    # No unique constraint — multiple positions (open or closed) can coexist


class LiveSubPosition(Base):
    """Live 子仓位账本:各 tier 的虚拟子仓位,对交易所呈现统一净仓位。

    设计动机:HL One-Way mode 下交易所端 per-symbol per-account 只有一个净仓位与
    一个杠杆档位,但本地策略按 trade_nature(scalp / trend_follow)分仓独立决策。
    本表是 live 侧的子仓位账本:本地按 trade_nature 拆分跟踪,交易所只见聚合净仓。
    下单时由 LivePositionManager 计算差额(净变化)只发一笔给交易所,并在此记账。
    """
    __tablename__ = "live_sub_positions"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    side = Column(String(10), nullable=False)                  # long / short
    trade_nature = Column(String(20), nullable=False, index=True)  # scalp / trend_follow
    timeframe_tier = Column(String(10), nullable=False)        # short / mid / long
    size = Column(Float, nullable=False)                       # 本 tier 名义大小
    leverage = Column(Float, nullable=False, default=1.0)
    margin = Column(Float, nullable=False, default=0.0)
    entry_price = Column(Float, nullable=False)
    status = Column(String(20), nullable=False, default="open")  # open / closed
    exchange_order_id = Column(String(100), nullable=True)
    tenant_id = Column(Integer, nullable=True)                 # multi-tenant
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp())


class PositionExitEvent(Base):
    """持仓退出事件流：记录 staged/trailing/AI 减仓等退出质量证据。"""
    __tablename__ = "position_exit_events"

    id = Column(Integer, primary_key=True, index=True)
    position_id = Column(Integer, ForeignKey("paper_positions.id"), nullable=False, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False, index=True)
    strategy_id = Column(String(50), nullable=True, index=True)

    symbol = Column(String(20), nullable=False, index=True)
    side = Column(String(10), nullable=False)
    trade_nature = Column(String(20), nullable=True, index=True)

    event_type = Column(String(40), nullable=False, index=True)
    quantity = Column(Float, nullable=True)
    price = Column(Float, nullable=True)
    pnl = Column(Float, nullable=True)
    fee = Column(Float, nullable=True)
    close_ratio = Column(Float, nullable=True)

    peak_pnl_at_event = Column(Float, nullable=True)
    peak_pnl_pct_at_event = Column(Float, nullable=True)
    pnl_at_event = Column(Float, nullable=True)
    pnl_pct_at_event = Column(Float, nullable=True)
    retention_ratio = Column(Float, nullable=True)
    health_score = Column(Float, nullable=True)
    health_regime = Column(String(30), nullable=True)
    reversal_level = Column(String(40), nullable=True)
    exit_channel = Column(String(100), nullable=True)
    metadata_json = Column(Text, nullable=True)

    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)


class PaperOrder(Base):
    """虚拟订单"""
    __tablename__ = "paper_orders"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False, index=True)
    strategy_id = Column(String(50), nullable=True, index=True)
    exchange = Column(String(32), nullable=True, index=True)  # 下单时锁定的交易所

    symbol = Column(String(20), nullable=False, index=True)
    side = Column(String(10), nullable=False)           # buy / sell
    order_type = Column(String(20), nullable=False)     # market / limit / stop_market
    price = Column(Float, nullable=True)                # limit price
    quantity = Column(Float, nullable=False)
    filled_quantity = Column(Float, nullable=False, default=0.0)
    filled_price = Column(Float, nullable=True)
    leverage = Column(Float, nullable=False, default=1.0)

    tp_price = Column(Float, nullable=True)
    sl_price = Column(Float, nullable=True)

    fee = Column(Float, nullable=True, default=0.0)
    pnl = Column(Float, nullable=True)                  # realized PnL (for close orders)
    entry_price = Column(Float, nullable=True)          # 开仓均价（平仓单记录对应持仓成本）

    # None=开仓, tp/sl/manual/trailing/liquidation=全平, partial_tp/manual_partial=部分平仓
    close_reason = Column(String(100), nullable=True)

    trade_nature = Column(String(20), nullable=True)  # 子仓位身份标签

    status = Column(String(20), nullable=False, default="pending", index=True)  # pending / filled / cancelled / rejected
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    filled_at = Column(TIMESTAMP, nullable=True)
    tenant_id = Column(Integer, nullable=False, default=1)  # RLS multi-tenant


class PaperFundingLedger(Base):
    """纸面仿真 — 资金费率结算流水（research 模式）"""
    __tablename__ = "paper_funding_ledger"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False, index=True)
    position_id = Column(Integer, nullable=True, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    side = Column(String(10), nullable=False)        # long / short
    notional = Column(Float, nullable=False)          # 结算时名义价值
    funding_rate = Column(Float, nullable=False)      # 实际费率
    payment = Column(Float, nullable=False)           # 正=收入, 负=支出
    settled_at = Column(TIMESTAMP, server_default=func.current_timestamp())


# ═══════════════════════════════════════════════════
# 策略模板库
# ═══════════════════════════════════════════════════

class StrategyTemplate(Base):
    """可复用的策略模板 — 内置 / 导入 / 历史策略自动晋升"""
    __tablename__ = "strategy_templates"

    id = Column(Integer, primary_key=True, index=True)
    template_id = Column(String(50), unique=True, nullable=False, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)

    # 分类
    category = Column(String(50), nullable=True)       # trend / range / breakout / momentum / swing / scalping
    market_regime = Column(String(50), nullable=True)   # bull / bear / sideways / all
    risk_level = Column(String(20), nullable=True)      # conservative / moderate / aggressive
    timeframe = Column(String(10), nullable=True)       # 15m / 1h / 4h / 1d
    tier = Column(String(10), nullable=True)            # short / mid / long

    # 策略配置（完整 JSON，可直接用于创建 AIStrategy）
    strategy_config = Column(JSON, nullable=False)

    # 来源与评级
    source = Column(String(30), nullable=True)          # builtin / imported / promoted
    source_url = Column(Text, nullable=True)
    author = Column(String(100), nullable=True)
    version = Column(String(20), nullable=True, default="1.0")

    # 绩效统计
    backtest_win_rate = Column(Float, nullable=True)
    backtest_sharpe = Column(Float, nullable=True)
    backtest_max_drawdown = Column(Float, nullable=True)
    backtest_total_trades = Column(Integer, nullable=True)
    live_usage_count = Column(Integer, nullable=True, default=0)
    live_avg_return = Column(Float, nullable=True)

    # 状态
    is_active = Column(Boolean, nullable=True, default=True)
    rating = Column(Float, nullable=True, default=0.0)
    tags = Column(JSON, nullable=True)

    # 血统追踪（P1-1）：指向父模板的 template_id，便于追溯进化链条
    parent_template_id = Column(String(50), nullable=True, index=True)

    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp())


class FullAutoSession(Base):
    """全自动交易会话 — 用户只选交易对+开启，AI 自主完成一切"""
    __tablename__ = "full_auto_sessions"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String(50), unique=True, nullable=False, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)  # 交易员账户（提供LLM）
    paper_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)  # 模拟账户（提供资金池）
    paper_account_mode = Column(String(32), nullable=True, default="legacy_ai_paper")
    arbitrage_paper_account_id = Column(Integer, ForeignKey("arbitrage_paper_accounts.id"), nullable=True, index=True)

    # 用户配置（仅这些需要用户决定）
    symbols = Column(JSON, nullable=False)                   # ["BTC", "ETH", "SOL"] — 三周期并集/旧兼容
    # 分周期固定币：{"short":[...],"mid":[...],"long":[...]}；空/缺省回退 symbols
    fixed_symbols_by_tier = Column(JSON, nullable=True)
    risk_level = Column(String(20), nullable=True, default="moderate")
    risk_mode = Column(String(20), nullable=True, default="ai_dynamic")  # ai_dynamic / conservative / aggressive
    trading_mode = Column(String(10), nullable=True, default="paper")

    auto_coin_enabled = Column(Boolean, nullable=True, default=False)
    arb_enabled = Column(Boolean, nullable=True, default=False)
    auto_coin_symbols = Column(JSON, nullable=True, default=[])
    # AI 选币槽位：本会话最多同时持有的自动选币数量（5~10，默认 5）— 短线专用
    auto_coin_max_slots = Column(Integer, nullable=True, default=5)
    # 中线 AI 选币（与短线隔离；默认关）
    auto_coin_mid_enabled = Column(Boolean, nullable=True, default=False)
    auto_coin_mid_max_slots = Column(Integer, nullable=True, default=3)
    active_exchange = Column(String(20), nullable=True)

    # AI 动态风控评估结果
    current_risk_assessment = Column(JSON, nullable=True)

    # 会话状态
    status = Column(String(20), nullable=False, default="running")  # running / defensive / paused / stopped
    started_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    stopped_at = Column(TIMESTAMP, nullable=True)

    # AI 管理的策略
    active_strategy_ids = Column(JSON, nullable=True, default=[])
    terminated_strategy_ids = Column(JSON, nullable=True, default=[])
    total_strategies_created = Column(Integer, nullable=True, default=0)

    # 全局风控
    max_concurrent_strategies = Column(Integer, nullable=True, default=25)
    max_total_drawdown_pct = Column(Float, nullable=True, default=0.30)
    daily_loss_limit_pct = Column(Float, nullable=True, default=0.05)

    # 绩效追踪
    total_pnl = Column(Float, nullable=True, default=0.0)
    total_trades = Column(Integer, nullable=True, default=0)
    winning_trades = Column(Integer, nullable=True, default=0)
    peak_balance = Column(Float, nullable=True, default=0.0)
    max_drawdown = Column(Float, nullable=True, default=0.0)
    current_drawdown = Column(Float, nullable=True, default=0.0)
    pause_reason = Column(String(30), nullable=True)

    # 熔断与防守模式持久化（P0-4: 防止重启丢失风控状态）
    circuit_breaker_until = Column(TIMESTAMP, nullable=True)
    defensive_entered_at = Column(TIMESTAMP, nullable=True)
    recovery_until = Column(TIMESTAMP, nullable=True)

    # 健康检查配置
    health_check_interval = Column(Integer, nullable=True, default=300)
    strategy_min_lifetime = Column(Integer, nullable=True, default=3600)
    strategy_max_consecutive_losses = Column(Integer, nullable=True, default=5)

    # 最新分析
    last_health_check_at = Column(TIMESTAMP, nullable=True)
    last_market_summary = Column(JSON, nullable=True)
    analyst_reports = Column(JSON, nullable=True)          # 多路分析师最新报告
    event_log = Column(JSON, nullable=True, default=[])

    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp())


class BacktestRun(Base):
    """回测运行记录"""
    __tablename__ = "backtest_runs"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(String(50), unique=True, nullable=False, index=True)
    template_id = Column(String(50), nullable=True, index=True)

    symbol = Column(String(20), nullable=False)
    timeframe = Column(String(10), nullable=False)
    tier = Column(String(10), nullable=True)            # short / mid / long
    start_date = Column(String(20), nullable=False)
    end_date = Column(String(20), nullable=False)
    initial_capital = Column(Float, nullable=False, default=10000.0)
    leverage = Column(Float, nullable=False, default=1.0)

    strategy_name = Column(String(200), nullable=True)
    strategy_config = Column(JSON, nullable=True)
    risk_params = Column(JSON, nullable=True)
    generation = Column(Integer, nullable=True, default=0)

    status = Column(String(20), nullable=False, default="pending")
    progress = Column(Float, nullable=True, default=0.0)
    bars_processed = Column(Integer, nullable=True, default=0)
    bars_total = Column(Integer, nullable=True, default=0)
    error_message = Column(Text, nullable=True)

    total_return = Column(Float, nullable=True)
    annualized_return = Column(Float, nullable=True)
    max_drawdown = Column(Float, nullable=True)
    sharpe_ratio = Column(Float, nullable=True)
    win_rate = Column(Float, nullable=True)
    profit_factor = Column(Float, nullable=True)
    total_trades = Column(Integer, nullable=True, default=0)
    avg_trade_return = Column(Float, nullable=True)
    max_consecutive_wins = Column(Integer, nullable=True)
    max_consecutive_losses = Column(Integer, nullable=True)
    avg_holding_bars = Column(Float, nullable=True)
    final_equity = Column(Float, nullable=True)

    parent_run_id = Column(String(50), nullable=True)
    mutation_description = Column(Text, nullable=True)
    is_champion = Column(Boolean, nullable=True, default=False)
    equity_curve = Column(JSON, nullable=True)

    started_at = Column(TIMESTAMP, nullable=True)
    completed_at = Column(TIMESTAMP, nullable=True)
    duration_seconds = Column(Float, nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())


class BacktestTrade(Base):
    """回测交易明细"""
    __tablename__ = "backtest_trades"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(String(50), ForeignKey("backtest_runs.run_id"), nullable=False, index=True)
    symbol = Column(String(20), nullable=False)
    side = Column(String(10), nullable=False)
    entry_bar = Column(Integer, nullable=False)
    exit_bar = Column(Integer, nullable=True)
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=True)
    quantity = Column(Float, nullable=False)
    leverage = Column(Float, nullable=True, default=1.0)
    pnl = Column(Float, nullable=True)
    pnl_pct = Column(Float, nullable=True)
    fee = Column(Float, nullable=True)
    exit_reason = Column(String(30), nullable=True)
    entry_time = Column(String(30), nullable=True)
    exit_time = Column(String(30), nullable=True)


# ══════════════════════════════════════════════════════════════
#  交易智慧（回测进化产出，注入AI决策提示词）
# ══════════════════════════════════════════════════════════════

class TradingWisdom(Base):
    """
    交易智慧：从回测进化中提取的经验和知识，
    编译为提示词片段注入 AI 决策。
    """
    __tablename__ = "trading_wisdom"

    id = Column(Integer, primary_key=True, index=True)
    template_id = Column(String(50), nullable=False, index=True)
    tier = Column(String(10), nullable=True)
    wisdom_type = Column(String(20), nullable=False)     # risk / regime / signal / lesson
    content = Column(JSON, nullable=False)               # 结构化智慧数据
    prompt_fragment = Column(Text, nullable=True)         # 编译后的提示词片段
    confidence = Column(Float, nullable=True, default=0.5)
    sample_count = Column(Integer, nullable=True, default=0)
    effectiveness_score = Column(Float, nullable=True)    # 实盘效果评分
    applied_count = Column(Integer, nullable=True, default=0)
    # ── 阶段2(S2-10) wisdom 闭环：质量样本计数（验证强度排序依据）──
    # evaluation_count = 通过质量闸门后的有效评估样本数（净扣费口径）
    # quality_hit_count = 其中净盈利样本数（质量闸门内 PnL 为正）
    evaluation_count = Column(Integer, nullable=True, default=0)
    quality_hit_count = Column(Integer, nullable=True, default=0)
    is_active = Column(Boolean, nullable=True, default=True)
    last_updated = Column(TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp())
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())


# ══════════════════════════════════════════════════════════════
#  智能多周期交易系统 — 新闻 / 鲸鱼 / 复盘
# ══════════════════════════════════════════════════════════════

class NewsEvent(MarketBase):
    """新闻情报事件"""
    __tablename__ = "news_events"

    id = Column(Integer, primary_key=True, index=True)
    source = Column(String(50), nullable=False)
    title = Column(Text, nullable=False)
    url = Column(Text, nullable=True)
    published_at = Column(TIMESTAMP, nullable=True)
    impact_direction = Column(Float, nullable=True)
    impact_strength = Column(Integer, nullable=True)
    impact_duration = Column(String(20), nullable=True)
    affected_symbols = Column(JSON, nullable=True)
    event_category = Column(String(50), nullable=True)
    confidence = Column(Float, nullable=True)
    ai_summary = Column(Text, nullable=True)
    raw_data = Column(JSON, nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())


class WhaleActivity(MarketBase):
    """鲸鱼异动记录"""
    __tablename__ = "whale_activities"

    id = Column(Integer, primary_key=True, index=True)
    activity_type = Column(String(30), nullable=False)
    symbol = Column(String(20), nullable=True, index=True)
    direction = Column(String(10), nullable=True)
    amount_usd = Column(Float, nullable=True)
    from_entity = Column(String(100), nullable=True)
    to_entity = Column(String(100), nullable=True)
    blockchain = Column(String(20), nullable=True)
    tx_hash = Column(String(200), nullable=True)
    ai_interpretation = Column(Text, nullable=True)
    signal_direction = Column(Float, nullable=True)
    timestamp = Column(TIMESTAMP, nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())


class TradeJournal(Base):
    """AI交易复盘日志"""
    __tablename__ = "trade_journals"

    id = Column(Integer, primary_key=True, index=True)
    period_type = Column(String(10), nullable=False)
    period_date = Column(String(20), nullable=False)
    total_trades = Column(Integer, nullable=True)
    total_pnl = Column(Float, nullable=True)
    win_rate = Column(Float, nullable=True)
    best_strategy = Column(String(200), nullable=True)
    worst_strategy = Column(String(200), nullable=True)
    ai_analysis = Column(Text, nullable=True)
    improvement_actions = Column(JSON, nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())


class StrategyRegimeScore(Base):
    """环境-绩效矩阵：记录每个策略模板在不同市场状态下的表现"""
    __tablename__ = "strategy_regime_scores"

    id = Column(Integer, primary_key=True, index=True)
    template_id = Column(String(50), nullable=False, index=True)
    regime = Column(String(30), nullable=False, index=True)  # trending_up/trending_down/ranging/volatile/breakout
    source = Column(String(10), nullable=False)               # backtest / live / paper

    sample_count = Column(Integer, nullable=False, default=0)
    win_rate = Column(Float, nullable=True, default=0.0)
    avg_pnl_pct = Column(Float, nullable=True, default=0.0)
    sharpe = Column(Float, nullable=True, default=0.0)
    max_drawdown = Column(Float, nullable=True, default=0.0)
    profit_factor = Column(Float, nullable=True, default=1.0)
    composite_score = Column(Float, nullable=True, default=0.0)

    decay_factor = Column(Float, nullable=True, default=1.0)
    last_updated = Column(TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp())

    # AI学习系统整合扩展字段
    drl_sharpe = Column(Float, nullable=True, default=None)           # DRL影子Sharpe
    kelly_avg_fraction = Column(Float, nullable=True, default=None)   # Kelly平均仓位比例
    multi_symbol_correlation = Column(Text, nullable=True, default=None)  # JSON: 多币种相关性
    param_version = Column(Integer, nullable=True, default=0)        # 参数版本号（乐观锁）

    __table_args__ = (
        UniqueConstraint("template_id", "regime", "source", name="uq_template_regime_source"),
    )


# ═══════════════════════════════════════════════════
# 拟人仓位管理系统
# ═══════════════════════════════════════════════════

class TradeMemoryRecord(Base):
    """
    交易记忆记录 — 每笔已平仓交易的完整上下文快照。
    用于仓位管理器根据历史表现动态调整。
    """
    __tablename__ = "trade_memory_records"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False, index=True)
    symbol = Column(String(20), nullable=False, index=True)

    side = Column(String(10), nullable=False)           # long / short
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=False)
    size = Column(Float, nullable=False)
    leverage = Column(Float, nullable=False, default=3.0)
    margin_used = Column(Float, nullable=False, default=0)

    pnl = Column(Float, nullable=False, default=0)
    pnl_pct = Column(Float, nullable=False, default=0)  # 收益率 (相对保证金)
    fee = Column(Float, nullable=False, default=0)
    hold_seconds = Column(Integer, nullable=False, default=0)

    # 开仓时的市场上下文（用于模式匹配）
    market_regime = Column(String(30), nullable=True)    # trending_up/trending_down/ranging/volatile
    signal_source = Column(String(30), nullable=True)    # llm/rule_engine/fallback/orchestrator
    confidence_at_entry = Column(Float, nullable=True)
    volatility_at_entry = Column(Float, nullable=True)   # ATR%

    close_reason = Column(String(100), nullable=True)     # tp/sl/trailing/ai_reverse/manual/liquidation

    opened_at = Column(TIMESTAMP, nullable=False)
    closed_at = Column(TIMESTAMP, server_default=func.current_timestamp())


class TraderMentalState(Base):
    """
    交易员心理状态 — 拟人化状态机。
    模拟真实交易员的心理周期：自信/正常/谨慎/冻结/冷却。
    每个 account_id 一条记录，持续更新。
    """
    __tablename__ = "trader_mental_states"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False, unique=True, index=True)

    # 状态机：aggressive / normal / cautious / frozen / cooldown
    state = Column(String(20), nullable=False, default="normal")
    state_reason = Column(String(200), nullable=True)

    # 连续统计
    consecutive_wins = Column(Integer, nullable=False, default=0)
    consecutive_losses = Column(Integer, nullable=False, default=0)
    streak_pnl = Column(Float, nullable=False, default=0)  # 连续段累计盈亏

    # 当日统计
    daily_trades = Column(Integer, nullable=False, default=0)
    daily_pnl = Column(Float, nullable=False, default=0)
    daily_max_drawdown = Column(Float, nullable=False, default=0)  # 当日最大回撤(%)

    # 仓位调节系数（由状态机输出，0.0~1.5）
    size_multiplier = Column(Float, nullable=False, default=1.0)
    leverage_cap = Column(Integer, nullable=False, default=20)  # 当前状态允许的最大杠杆

    # 冷却期
    cooldown_until = Column(TIMESTAMP, nullable=True)

    # 记忆摘要（最近 N 笔统计）
    recent_win_rate = Column(Float, nullable=False, default=0.5)  # 最近20笔胜率
    recent_avg_pnl_pct = Column(Float, nullable=False, default=0) # 最近20笔平均收益率
    recent_best_regime = Column(String(30), nullable=True)        # 最近最赚钱的市场环境
    recent_worst_regime = Column(String(30), nullable=True)       # 最近最亏钱的市场环境

    last_trade_at = Column(TIMESTAMP, nullable=True)
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp())


class TraderPersonality(Base):
    """
    AI 交易员性格档案 — 每个 Account 对应一份独立性格。
    性格参数直接影响仓位管理（PositionMemoryManager）和 AI Prompt 角色扮演。
    """
    __tablename__ = "trader_personalities"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False, unique=True, index=True)

    # --- 身份 ---
    display_name = Column(String(50), nullable=True)      # "趋势之王", "稳健老将"
    description = Column(String(500), nullable=True)       # 一句话描述
    benchmark_trader = Column(String(50), nullable=True)   # 对标人物: "Jesse Livermore" / null=自定义

    # --- 交易风格 ---
    trading_style = Column(String(30), nullable=False, default="trend_following")
    time_horizon = Column(String(20), nullable=False, default="swing_trader")

    # --- 量化性格参数 ---
    risk_appetite = Column(Integer, nullable=False, default=5)        # 1~10
    min_confidence = Column(Float, nullable=False, default=0.30)      # 最低开仓置信度
    loss_tolerance = Column(Integer, nullable=False, default=5)       # 1~10
    win_aggression = Column(Integer, nullable=False, default=5)       # 1~10
    max_position_pct = Column(Float, nullable=False, default=0.15)    # 单仓上限
    preferred_leverage = Column(Integer, nullable=False, default=10)   # 偏好杠杆
    max_leverage = Column(Integer, nullable=False, default=20)        # 杠杆上限

    # --- 专属技能 ---
    specialty_symbols = Column(String(200), nullable=True)   # JSON array: ["BTC","ETH"]
    special_skills = Column(String(500), nullable=True)      # 技能描述

    # --- Prompt 注入 ---
    custom_prompt = Column(Text, nullable=True)              # 角色扮演 + 自定义规则

    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(),
                        onupdate=func.current_timestamp())

    # Relationship
    account = relationship("Account", back_populates="personality")


class LLMUsageLog(AnalyticsBase):
    """
    LLM API 调用用量日志。
    每次调用大模型 API 后记录 token 用量和估算费用，
    用于在设置页展示"大模型用量"详情。
    """
    __tablename__ = "llm_usage_logs"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, nullable=True, index=True)  # cross-db FK removed
    llm_config_id = Column(Integer, nullable=True, index=True)  # cross-db FK removed

    provider = Column(String(50), nullable=False)
    model = Column(String(100), nullable=False)

    prompt_tokens = Column(Integer, nullable=False, default=0)
    completion_tokens = Column(Integer, nullable=False, default=0)
    total_tokens = Column(Integer, nullable=False, default=0)
    reasoning_tokens = Column(Integer, nullable=True)
    prompt_cache_hit_tokens = Column(Integer, nullable=False, default=0)
    prompt_cache_miss_tokens = Column(Integer, nullable=False, default=0)

    estimated_cost_usd = Column(Float, nullable=False, default=0.0)
    estimated_cost_cny = Column(Float, nullable=False, default=0.0)

    call_type = Column(String(128), nullable=True)
    duration_ms = Column(Integer, nullable=True)
    success = Column(String(10), nullable=False, default="true")
    error_message = Column(String(500), nullable=True)

    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)


class SignalTradeFeedback(Base):
    """
    信号-交易反馈记录 — 每笔交易开仓时活跃的信号快照。
    用于计算每种信号的实际交易贡献度，驱动自适应权重优化。
    """
    __tablename__ = "signal_trade_feedback"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False, index=True)
    trade_id = Column(Integer, nullable=True, index=True)
    symbol = Column(String(20), nullable=False, index=True)

    signal_type = Column(String(30), nullable=False, index=True)
    signal_value = Column(Float, nullable=True)
    signal_direction = Column(String(20), nullable=True)

    trade_pnl = Column(Float, nullable=True)
    trade_pnl_pct = Column(Float, nullable=True)
    trade_side = Column(String(10), nullable=True)

    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)


class SignalWeightHistory(Base):
    """
    信号权重历史 — 每次自适应权重更新后的快照。
    用于审计和回溯权重变化对交易质量的影响。
    """
    __tablename__ = "signal_weight_history"

    id = Column(Integer, primary_key=True, index=True)
    weights_json = Column(JSON, nullable=False)
    performance_json = Column(JSON, nullable=True)
    update_reason = Column(String(200), nullable=True)
    computed_at = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)


class DecisionSnapshot(AnalyticsBase):
    """
    决策快照 — 每次 AI 交易决策的完整上下文快照。
    用于自反思经验库：交易-反思-学习闭环。
    """
    __tablename__ = "decision_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, nullable=True, index=True)
    strategy_id = Column(String(50), nullable=True, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    tier = Column(String(10), nullable=True)

    timestamp = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)
    market_snapshot_json = Column(JSON, nullable=True)
    ai_reasoning = Column(Text, nullable=True)
    action = Column(String(20), nullable=True)
    direction = Column(String(10), nullable=True)
    confidence = Column(Float, nullable=True)

    entry_price = Column(Float, nullable=True)
    exit_price = Column(Float, nullable=True)
    pnl = Column(Float, nullable=True)
    pnl_pct = Column(Float, nullable=True)
    duration_seconds = Column(Integer, nullable=True)

    regime_at_decision = Column(String(30), nullable=True, index=True)
    volatility_at_decision = Column(Float, nullable=True)
    quality_label = Column(String(20), nullable=True, index=True)
    lesson_extracted = Column(Text, nullable=True)

    # v2 — 提案—评估—执行可回放（2026-07-05 GAP 设计）
    proposal_id = Column(String(64), nullable=True, index=True)
    trace_id = Column(String(64), nullable=True, index=True)
    source_lane = Column(String(32), nullable=True, index=True)
    proposal_json = Column(JSON, nullable=True)
    evaluate_verdict_json = Column(JSON, nullable=True)
    gate_blocks_json = Column(JSON, nullable=True)
    orchestrator_json = Column(JSON, nullable=True)
    executed = Column(Boolean, default=False, nullable=True)
    execution_channel = Column(String(16), nullable=True)
    content_hash = Column(String(64), nullable=True)
    prev_hash = Column(String(64), nullable=True)


# ═══════════════════════════════════════════════════════════════
# V3 System Tables (Reference: docs/SYSTEM_UPGRADE_DESIGN_V3.md §7.4)
# ═══════════════════════════════════════════════════════════════

class ArbitragePosition(Base):
    """套利仓位记录 — 记录资金费率套利和跨交易所套利的对冲仓位"""
    __tablename__ = "arbitrage_positions"

    id = Column(Integer, primary_key=True, index=True)
    position_id = Column(String(64), unique=True, nullable=False)
    symbol = Column(String(32), nullable=False, index=True)
    strategy = Column(String(32), nullable=False, index=True)  # funding_long/funding_short/cross_exchange/basis
    long_size = Column(DECIMAL(20, 8), nullable=True)
    long_entry_price = Column(DECIMAL(20, 8), nullable=True)
    short_size = Column(DECIMAL(20, 8), nullable=True)
    short_entry_price = Column(DECIMAL(20, 8), nullable=True)
    delta = Column(DECIMAL(20, 8), nullable=True)
    accumulated_funding = Column(DECIMAL(20, 8), default=0)
    status = Column(String(16), default='active', index=True)  # active/closing/closed
    entry_time = Column(TIMESTAMP, nullable=False)
    close_time = Column(TIMESTAMP, nullable=True)
    close_reason = Column(String(64), nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())

    # V3 新增字段
    funding_payments_count = Column(Integer, default=0)
    exchange_long = Column(String(32), nullable=True)        # 多腿交易所
    exchange_short = Column(String(32), nullable=True)       # 空腿交易所
    entry_z_score = Column(DECIMAL(10, 4), nullable=True)    # 入场Z-score（跨所）
    entry_spread_pct = Column(DECIMAL(10, 8), nullable=True) # 入场价差%
    entry_basis_pct = Column(DECIMAL(10, 8), nullable=True)  # 入场基差%
    liquidation_price_long = Column(DECIMAL(20, 8), nullable=True)   # 多腿强平价
    liquidation_price_short = Column(DECIMAL(20, 8), nullable=True)  # 空腿强平价
    maintenance_margin = Column(DECIMAL(20, 8), nullable=True)       # 维持保证金
    entry_edge = Column(DECIMAL(10, 8), nullable=True)       # 入场边缘（用于衰减追踪）
    mode = Column(String(16), default='paper')                # paper/live
    size_usd = Column(DECIMAL(20, 8), nullable=True)         # 仓位大小USD
    pnl = Column(DECIMAL(20, 8), nullable=True)              # 已实现盈亏


class AnomalyEvent(Base):
    """异常事件日志 — 记录市场异常检测结果"""
    __tablename__ = "anomaly_events"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(String(128), unique=True, nullable=False)
    symbol = Column(String(32), nullable=False, index=True)
    anomaly_type = Column(String(32), nullable=False, index=True)  # price/volume/funding/oi/factor
    severity = Column(DECIMAL(5, 4), nullable=True)
    z_score = Column(DECIMAL(10, 4), nullable=True)
    description = Column(Text, nullable=True)
    raw_value = Column(DECIMAL(20, 8), nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)


class StrategyHypothesis(Base):
    """策略假设记录 — LLM-GA混合生成的策略假设及回测结果"""
    __tablename__ = "strategy_hypotheses"

    id = Column(Integer, primary_key=True, index=True)
    hypothesis_id = Column(String(128), unique=True, nullable=False)
    name = Column(String(128), nullable=True)
    description = Column(Text, nullable=True)
    market_regime = Column(String(32), nullable=True, index=True)
    param_ranges = Column(JSON, nullable=True)  # JSONB in PostgreSQL
    backtest_sharpe = Column(DECIMAL(10, 4), nullable=True)
    promoted = Column(Boolean, default=False, index=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())


class FactorQualityReport(AnalyticsBase):
    """因子质量报告 — 定期因子健康度评估报告"""
    __tablename__ = "factor_quality_reports"

    id = Column(Integer, primary_key=True, index=True)
    factor_id = Column(String(64), nullable=False, index=True)
    report_date = Column(Date, nullable=False, index=True)
    ic_mean = Column(DECIMAL(10, 6), nullable=True)
    icir = Column(DECIMAL(10, 6), nullable=True)
    coverage = Column(DECIMAL(5, 4), nullable=True)
    grade = Column(String(2), nullable=True)  # A+/A/B/C/D
    is_alive = Column(Boolean, nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())

    __table_args__ = (
        UniqueConstraint('factor_id', 'report_date', name='uq_factor_quality_factor_date'),
    )


class FactorPerformanceLog(AnalyticsBase):
    """因子表现日志 — 记录因子在交易决策中的实时表现"""
    __tablename__ = "factor_performance_logs"

    id = Column(Integer, primary_key=True, index=True)
    factor_name = Column(String(50), nullable=False, index=True)
    factor_category = Column(String(30), nullable=False)
    ic_value = Column(DECIMAL(10, 6), nullable=True)
    decay_rate = Column(DECIMAL(10, 6), nullable=True)
    current_weight = Column(DECIMAL(10, 6), nullable=True)
    market_regime = Column(String(30), nullable=True)
    symbol = Column(String(20), nullable=True, index=True)
    timeframe = Column(String(10), nullable=True)
    recorded_at = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)


# ═══════════════════════════════════════════════════════════════════════════
# 因子进化闭环 (FactorEvolutionLoop) — 持久化
# ═══════════════════════════════════════════════════════════════════════════

class FactorEvolutionLog(AnalyticsBase):
    """因子进化日志 — 记录每次因子状态转换 + 评估指标。"""
    __tablename__ = "factor_evolution_log"

    id = Column(Integer, primary_key=True, index=True)
    factor_id = Column(String(64), nullable=False, index=True)
    expr_ast = Column(JSON, nullable=True)             # 因子表达式 AST
    source = Column(String(64), nullable=True)          # 来源(rev/mom/vol/miner/perp/...)
    phase = Column(String(20), nullable=False)          # 阶段(mine/evaluate/purge/promote/monitor/degrade)
    state_from = Column(String(20), nullable=True)
    state_to = Column(String(20), nullable=True)
    action = Column(String(20), nullable=True)          # promote/deweigh/quarantine/replace
    reason = Column(Text, nullable=True)
    metrics = Column(JSON, nullable=True)               # {icir, monotonicity_p, turnover, ...}
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)


class FactorActiveSet(AnalyticsBase):
    """活跃因子集 — 当前在线的因子及其参数。"""
    __tablename__ = "factor_active_set"

    id = Column(Integer, primary_key=True, index=True)
    factor_id = Column(String(64), nullable=False, unique=True, index=True)
    expr_ast = Column(JSON, nullable=False)             # 因子表达式 AST
    expr_id = Column(String(64), nullable=True)          # 表达式版本 ID
    source = Column(String(64), nullable=True)
    state = Column(String(20), nullable=False, default="ACTIVE")  # FactorState
    icir = Column(Float, nullable=True)
    incremental_corr = Column(Float, nullable=True)
    capacity_usd = Column(Float, nullable=True)
    current_weight = Column(JSON, nullable=True)         # 在线权重 {timeframe: weight}
    last_net_ic = Column(Float, nullable=True)           # M2: 净 IC（扣费后）
    turnover = Column(Float, nullable=True)              # M2: 换手率
    evaluated_cycles = Column(Integer, nullable=True, default=0)  # M2: 已评估轮数
    activated_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    deactivated_at = Column(TIMESTAMP, nullable=True)
    last_evaluated_at = Column(TIMESTAMP, nullable=True)


# ═══════════════════════════════════════════════════════════════════════════
# 战略分析师模块 (Strategic Analyst)
# ═══════════════════════════════════════════════════════════════════════════

try:
    from backend.services.strategic_analyst.db_models import (
        StrategicMacroSnapshot,
        StrategicReportRecord,
        NewCoinOpportunityRecord,
        StrategicMemoryRecord,
        CrossMarketCorrelationRecord,
    )
except ImportError:
    from services.strategic_analyst.db_models import (
        StrategicMacroSnapshot,
        StrategicReportRecord,
        NewCoinOpportunityRecord,
        StrategicMemoryRecord,
        CrossMarketCorrelationRecord,
    )


class FactorSyncConfig(Base):
    """因子同步配置 — 云端因子库连接与同步状态"""
    __tablename__ = "factor_sync_config"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    repo_url = Column(String(500), nullable=False)
    branch = Column(String(50), nullable=False, default="main")
    sync_path = Column(String(200), nullable=True)  # 仓库内的因子目录路径
    enabled = Column(Boolean, nullable=False, default=True)
    auto_sync = Column(Boolean, nullable=False, default=False)
    sync_interval_hours = Column(Integer, nullable=False, default=24)
    last_sync_at = Column(TIMESTAMP, nullable=True)
    last_sync_status = Column(String(20), nullable=True)  # success/failed/running
    last_sync_log = Column(Text, nullable=True)
    factors_downloaded = Column(Integer, nullable=False, default=0)
    factors_registered = Column(Integer, nullable=False, default=0)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp())


class CloudFactorDefinition(Base):
    """云端因子定义缓存 — 从远程因子库下载的因子元数据"""
    __tablename__ = "cloud_factor_definitions"

    id = Column(Integer, primary_key=True, index=True)
    factor_id = Column(String(64), nullable=False, unique=True, index=True)
    source_repo = Column(String(500), nullable=False)
    name = Column(String(255), nullable=False)
    display_name = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    category = Column(String(50), nullable=False)
    subcategory = Column(String(100), nullable=True)
    calculation_code = Column(Text, nullable=False)
    parameters = Column(JSON, nullable=True)
    required_data_fields = Column(JSON, nullable=True)
    dependencies = Column(JSON, nullable=True)
    version = Column(String(20), nullable=True)
    author = Column(String(100), nullable=True)
    tags = Column(JSON, nullable=True)
    localized = Column(Boolean, nullable=False, default=False)  # 是否已转化为本地 BaseFactor
    localized_path = Column(String(500), nullable=True)  # 本地 Python 文件路径
    localized_at = Column(TIMESTAMP, nullable=True)
    status = Column(String(20), nullable=False, default="downloaded")  # downloaded/validated/localized/active/error
    error_message = Column(Text, nullable=True)
    downloaded_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp())


class MarketRegimeHistory(Base):
    """市场状态历史 — 记录市场状态分类结果"""
    __tablename__ = "market_regime_history"

    id = Column(Integer, primary_key=True, index=True)
    symbol = Column(String(32), nullable=True, index=True)
    regime = Column(String(32), nullable=False)  # trending_up/trending_down/ranging/volatile/quiet/crisis
    confidence = Column(DECIMAL(5, 4), nullable=True)
    features = Column(JSON, nullable=True)  # JSONB in PostgreSQL
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)


# ═══════════════════════════════════════════════════════════════
# AI学习系统整合表 (Reference: .qoder/plans/AI学习系统深度整合方案)
# ═══════════════════════════════════════════════════════════════

class MultiSymbolKelly(Base):
    """多币种Kelly仓位汇总 — 记录每个币种的Kelly仓位及组合风险贡献"""
    __tablename__ = "multi_symbol_kelly"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(TIMESTAMP, nullable=False, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    kelly_fraction = Column(Float, nullable=True, default=0.0)
    adjusted_size = Column(Float, nullable=True, default=0.0)
    portfolio_fraction = Column(Float, nullable=True, default=0.0)   # 占总仓位的比例
    risk_contribution = Column(Float, nullable=True, default=0.0)    # 风险贡献度
    correlation_with_others = Column(Float, nullable=True, default=0.0)
    calculation_window = Column(Integer, nullable=True, default=252)  # 计算窗口

    __table_args__ = (
        Index('idx_msk_symbol_ts', 'symbol', 'timestamp'),
    )


class DRLPerformance(Base):
    """DRL表现追踪 — 记录DRL预测vs实际结果，用于准确率评估和重训练触发"""
    __tablename__ = "drl_performance"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(TIMESTAMP, nullable=False, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    predicted_direction = Column(Float, nullable=True)    # -1~1
    actual_direction = Column(Float, nullable=True)       # -1~1
    predicted_size = Column(Float, nullable=True)         # 0~1
    actual_pnl = Column(Float, nullable=True)
    regime = Column(String(30), nullable=True)
    is_correct = Column(Boolean, nullable=True)
    model_version = Column(String(50), nullable=True, index=True)  # 模型版本追踪
    observation_hash = Column(String(64), nullable=True)           # 观察值哈希，用于去重

    __table_args__ = (
        Index('idx_drl_perf_symbol_ts', 'symbol', 'timestamp'),
    )


class DRLPerformanceDaily(Base):
    """DRL表现日聚合 — drl_performance的按日聚合，用于长期趋势分析和归档"""
    __tablename__ = "drl_performance_daily"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(Date, nullable=False, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    model_version = Column(String(50), nullable=True)
    avg_accuracy = Column(Float, nullable=True, default=0.0)
    avg_pnl = Column(Float, nullable=True, default=0.0)
    trade_count = Column(Integer, nullable=True, default=0)
    correct_count = Column(Integer, nullable=True, default=0)
    avg_predicted_confidence = Column(Float, nullable=True, default=0.0)

    __table_args__ = (
        UniqueConstraint('date', 'symbol', 'model_version', name='uq_drl_daily_symbol_date_ver'),
    )


class SystemCoordinatorState(Base):
    """系统协调状态 — 三系统（进化/DRL/Kelly）的协调状态和事务支持"""
    __tablename__ = "system_coordinator_state"

    id = Column(Integer, primary_key=True, index=True)
    last_evolution_at = Column(TIMESTAMP, nullable=True)
    last_drl_training_at = Column(TIMESTAMP, nullable=True)
    current_regime = Column(String(30), nullable=True)
    regime_confidence = Column(Float, nullable=True, default=0.0)
    auto_tuning_enabled = Column(Boolean, nullable=True, default=True)
    sync_status = Column(String(20), nullable=True, default='idle')  # idle / syncing / error
    # 事务支持字段
    active_transaction_id = Column(String(50), nullable=True)
    locked_systems = Column(Text, nullable=True)       # JSON: 被锁定的系统列表
    param_versions = Column(Text, nullable=True)        # JSON: 各系统参数版本号
    last_correlation_update_at = Column(TIMESTAMP, nullable=True)
    last_kelly_update_at = Column(TIMESTAMP, nullable=True)
    # LearningLoop 心跳（P1-3）：每次 _tick_coordinator 完成后写入
    last_loop_tick_at = Column(TIMESTAMP, nullable=True)
    # DRL 模型版本号（P1-2）：PPO save 时写入 timestamp，load 时校验
    drl_model_version = Column(String(64), nullable=True)
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp())


class CoordinatorAction(Base):
    """协调器决策日志 — 每次 check_and_coordinate 插入一行（P1-3）

    用于事后复盘：LearningLoop 观测到什么条件 → 触发了哪些后续任务 →
    后续任务是否成功。与 SystemCoordinatorState 的"最新状态"形成对照。
    """
    __tablename__ = "coordinator_actions"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    ts = Column(TIMESTAMP, nullable=False, server_default=func.current_timestamp(), index=True)
    # 触发条件与动作（JSON）：{"trigger_evolution": bool, "reasons": [...], ...}
    action = Column(JSON, nullable=True)
    # 本次 tick 实际派发的后续 job 列表：["emergency_evolution", "drl_retrain", ...]
    triggered_jobs = Column(JSON, nullable=True)
    # 本次 tick 跳过的原因（如冷却、无样本等），便于判断闭环是否在空转
    skipped_reasons = Column(JSON, nullable=True)


class EvolutionEvent(Base):
    """进化事件记录 — 每次进化（每周GA / 紧急 / 手动）完成后写入一行（2026-06-22）

    用于事后复盘：进化类型、模板数、晋升数、最优fitness、NSGA-II目标值。
    替代此前无 ORM 模型的"幽灵表" evolution_events。
    """
    __tablename__ = "evolution_events"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    evolution_type = Column(String(20), nullable=False, index=True)  # weekly / emergency / manual
    trigger_reason = Column(String(100), nullable=True)              # all_new / 连亏 / 定时 / 手动
    template_count = Column(Integer, nullable=True, default=0)       # 参与进化的模板数
    promoted_count = Column(Integer, nullable=True, default=0)       # 晋升的模板数
    best_fitness = Column(Float, nullable=True)                      # 最优 composite_fitness
    best_sharpe = Column(Float, nullable=True)                       # 最优 Sharpe
    best_profit_factor = Column(Float, nullable=True)                # 最优 profit_factor
    best_max_drawdown = Column(Float, nullable=True)                 # 最优 max_drawdown
    objectives_json = Column(Text, nullable=True)                    # NSGA-II 多目标 JSON
    details_json = Column(Text, nullable=True)                       # 补充详情 JSON
    success = Column(Boolean, nullable=False, default=False)        # 是否成功
    error_message = Column(Text, nullable=True)                      # 失败原因
    duration_seconds = Column(Float, nullable=True)                  # 耗时
    created_at = Column(TIMESTAMP, nullable=False, server_default=func.current_timestamp(), index=True)


class AutoCoinSelection(Base):
    """AI 自动选币记录 — 每次选出/注入/淘汰一个币种写入一行"""
    __tablename__ = "auto_coin_selections"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    session_id = Column(String(50), ForeignKey("full_auto_sessions.session_id"), nullable=False, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    exchange = Column(String(20), nullable=True, default="asterdex")
    action = Column(String(20), nullable=False)  # injected / skipped / removed
    scanner_score = Column(Float, nullable=True)
    scanner_rank = Column(Integer, nullable=True)
    ai_confidence = Column(Float, nullable=True)
    ai_reason = Column(Text, nullable=True)
    suggested_tier = Column(String(10), nullable=True)  # short / mid / long
    risk_note = Column(Text, nullable=True)
    removal_reason = Column(Text, nullable=True)
    # 多租户归属(0004 tenant 隔离迁移加列,NOT NULL)。应用层 stamp —— RLS 据此过滤。
    tenant_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    # ── 阶段A 反馈闭环：选中时刻价格 + 后续表现回填 ──
    # injected 时写 price_at_selection;24h/72h 回填价格并据此判定 hit;平仓后回填 pnl。
    price_at_selection = Column(Numeric(20, 8), nullable=True)
    price_after_24h = Column(Numeric(20, 8), nullable=True)
    price_after_72h = Column(Numeric(20, 8), nullable=True)
    realized_pnl = Column(Numeric(16, 4), nullable=True)
    hit_24h = Column(Boolean, nullable=True)
    hit_72h = Column(Boolean, nullable=True)
    # ── 阶段2(S2-9) 因子快照：IC 加权 + 相关性去重的样本源 ──
    # injected 时写入当次评分因子快照（五维分数 + 链上分数 + 综合分），
    # 待 hit_24h / realized_pnl 回填后即可离线计算因子 IC，驱动权重自适应。
    factor_snapshot_json = Column(JSON, nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())


class ObservationPoolLabel(Base):
    """5.2 观察池批量打标（v6 10.2.3 本地 LLM）— 选币观察池样本的 LLM 打标记录。

    auto_coin_selections 注入样本（观察池）满足 min_samples=3 后，
    batch_labeler 用本地 ollama 模型批量打标（regime/sentiment/quality），
    作为因子 IC 反馈闭环之外的样本质量/特征增强通道。
    selection_id 唯一：同一注入样本只打标一次（幂等）。
    """
    __tablename__ = "observation_pool_labels"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    selection_id = Column(
        Integer, ForeignKey("auto_coin_selections.id"), nullable=False, unique=True, index=True
    )
    symbol = Column(String(20), nullable=False, index=True)
    session_id = Column(String(50), nullable=True, index=True)
    llm_config_id = Column(Integer, nullable=True)
    model = Column(String(100), nullable=True)
    regime_label = Column(String(30), nullable=True)      # trend / range / breakout / riskoff / unknown
    sentiment_bias = Column(String(20), nullable=True)    # bullish / bearish / neutral
    quality = Column(String(20), nullable=True)           # usable / marginal / reject
    confidence = Column(Float, nullable=True)
    reasoning = Column(Text, nullable=True)
    raw_json = Column(JSON, nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    # 多租户归属：应用层 stamp，RLS 据此过滤。
    tenant_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)


class CoinSelectScan(Base):
    """VIP 共用 AI 选币：平台级扫描批次（管理员 LLM）。"""
    __tablename__ = "coin_select_scans"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    scan_id = Column(String(64), unique=True, nullable=False, index=True)
    status = Column(String(20), nullable=False, default="running")  # running|done|failed
    exchange = Column(String(32), nullable=True)
    started_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    finished_at = Column(TIMESTAMP, nullable=True)
    duration_sec = Column(Float, nullable=True)
    candidates_scanned = Column(Integer, default=0)
    candidates_ai = Column(Integer, default=0)
    board_scalp = Column(Integer, default=0)
    board_midlong = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    admin_tenant_id = Column(Integer, nullable=True)
    meta_json = Column(JSON, nullable=True)


class CoinSelectCandidate(Base):
    """VIP 共用 AI 选币看板条目（短线/长线）。"""
    __tablename__ = "coin_select_candidates"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    scan_id = Column(String(64), nullable=False, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    horizon = Column(String(16), nullable=False, index=True)  # scalp | midlong
    tier_label = Column(String(20), nullable=True)  # strong | watch | reject
    score = Column(Float, nullable=True)
    factor_match = Column(Float, nullable=True)
    factor_detail = Column(JSON, nullable=True)
    market_scores = Column(JSON, nullable=True)
    ai_verdict = Column(String(32), nullable=True)  # approve|watch|reject
    ai_reason = Column(Text, nullable=True)
    confidence = Column(Float, nullable=True)
    direction_bias = Column(String(16), nullable=True)  # long|short|neutral
    risk_notes = Column(Text, nullable=True)
    invalidation = Column(Text, nullable=True)
    valid_until = Column(TIMESTAMP, nullable=True)
    listed = Column(Boolean, nullable=False, default=True)  # 管理员可下架
    adopt_count = Column(Integer, nullable=False, default=0)
    raw_json = Column(JSON, nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)


class CoinSelectAdoption(Base):
    """VIP 将看板币采纳进自己会话的审计。"""
    __tablename__ = "coin_select_adoptions"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    session_id = Column(String(50), nullable=False, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    horizon = Column(String(16), nullable=False)
    candidate_id = Column(Integer, nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())


# ═══════════════════════════════════════════════════════════════
# Rebate Arbitrage Tables (Reference: rebate-arb-automation.md)
# ═══════════════════════════════════════════════════════════════

class RebatePositionDB(Base):
    """返利/积分套利仓位 — 完整生命周期追踪"""
    __tablename__ = "rebate_positions"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    position_id = Column(String(64), unique=True, nullable=False, index=True)
    strategy_type = Column(String(8), nullable=False, index=True)  # S1-S8
    source_exchange = Column(String(32), nullable=False, index=True)
    target_exchange = Column(String(32), nullable=True)
    symbol = Column(String(32), nullable=False)
    side_a_size = Column(Float, default=0.0)
    side_b_size = Column(Float, default=0.0)
    entry_price_a = Column(Float, default=0.0)
    entry_price_b = Column(Float, default=0.0)
    current_pnl = Column(Float, default=0.0)
    accumulated_rebate = Column(Float, default=0.0)
    accumulated_points = Column(Float, default=0.0)
    entry_time = Column(Float, nullable=False)
    close_time = Column(Float, nullable=True)
    max_hold_seconds = Column(Float, default=2592000.0)
    status = Column(String(16), default='active', index=True)  # active/closing/closed/error
    paper_mode = Column(Boolean, default=True)
    metadata_json = Column(Text, default='{}')
    # 阶段 4.2: 软关联 arbitrage_paper_accounts.id（nullable，老数据留空）
    # 由 unified_account_service 用于按账户查询套利仓位
    owner_account_id = Column(Integer, nullable=True, index=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp())

    orders = relationship("RebateOrderDB", back_populates="position", lazy="dynamic")


class RebateOrderDB(Base):
    """返利套利订单 — 记录每腿下单及成交信息"""
    __tablename__ = "rebate_orders"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    position_id = Column(String(64), ForeignKey("rebate_positions.position_id"), nullable=False, index=True)
    exchange = Column(String(32), nullable=False, index=True)
    leg = Column(String(2), nullable=False)  # "A" or "B"
    exchange_order_id = Column(String(128), nullable=True)
    symbol = Column(String(32), nullable=False)
    side = Column(String(8), nullable=False)  # buy/sell
    order_type = Column(String(16), nullable=False)  # market/limit
    size = Column(Float, nullable=False)
    price = Column(Float, nullable=True)
    filled_size = Column(Float, default=0.0)
    filled_price = Column(Float, default=0.0)
    status = Column(String(16), default='pending', index=True)  # pending/filled/partial/cancelled/error
    fee_paid = Column(Float, default=0.0)
    rebate_received = Column(Float, default=0.0)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp())

    position = relationship("RebatePositionDB", back_populates="orders")


class RebateIncentiveSnapshotDB(Base):
    """交易所激励数据快照 — 时间序列存储费率/积分/VIP状态"""
    __tablename__ = "rebate_incentive_snapshots"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    exchange = Column(String(32), nullable=False, index=True)
    snapshot_time = Column(TIMESTAMP, nullable=False, index=True)
    fee_tier_name = Column(String(32), nullable=True)
    maker_rate = Column(Float, default=0.0)
    taker_rate = Column(Float, default=0.0)
    rebate_rate = Column(Float, default=0.0)
    points_balance = Column(Float, default=0.0)
    points_multiplier = Column(Float, default=1.0)
    volume_30d = Column(Float, default=0.0)
    data_json = Column(Text, default='{}')
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())


class RebatePerformanceLogDB(Base):
    """返利套利绩效日志 — 记录每笔仓位结算结果"""
    __tablename__ = "rebate_performance_logs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    position_id = Column(String(64), nullable=False, index=True)
    strategy_type = Column(String(8), nullable=False, index=True)
    total_pnl = Column(Float, default=0.0)
    total_rebate = Column(Float, default=0.0)
    total_points = Column(Float, default=0.0)
    hold_hours = Column(Float, default=0.0)
    # Text：除平仓原因外，资金协调器还以 position_id='__capital_allocation_state__'
    # 的特殊行复用本字段持久化分配状态 JSON（远超 64 字符）
    close_reason = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())


class ArbitrageProfileDB(Base):
    """AI 交易员专用套利档案 — Account 级预设，FullAuto 启动时可覆盖。"""
    __tablename__ = "arbitrage_profiles"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False, unique=True, index=True)
    enabled = Column(Boolean, default=False, index=True)
    mode = Column(String(16), default="paper")  # paper/live
    paper_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True, index=True)
    paper_account_mode = Column(String(32), default="legacy_ai_paper", index=True)
    arbitrage_paper_account_id = Column(Integer, ForeignKey("arbitrage_paper_accounts.id"), nullable=True, index=True)
    enabled_strategies_json = Column(Text, default='["S3","S8"]')
    strategy_overrides_json = Column(Text, default='{}')
    wash_trade_profile = Column(String(32), default="balanced")
    ai_config_source = Column(String(32), default="manual")
    linked_llm_config_id = Column(Integer, ForeignKey("llm_configurations.id"), nullable=True)
    strategy_llm_config_id = Column(Integer, ForeignKey("llm_configurations.id"), nullable=True)
    execution_llm_config_id = Column(Integer, ForeignKey("llm_configurations.id"), nullable=True)
    last_evolved_at = Column(Float, nullable=True)
    profile_snapshot_json = Column(Text, default='{}')
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp())
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp())

    account = relationship("Account", foreign_keys=[account_id])
    paper_account = relationship("Account", foreign_keys=[paper_account_id])
    arbitrage_paper_account = relationship("ArbitragePaperAccountDB", foreign_keys=[arbitrage_paper_account_id])


class ArbitragePaperAccountDB(Base):
    """套利专用 Paper 总账户 — 与 AI 策略 Paper 账户隔离。"""
    __tablename__ = "arbitrage_paper_accounts"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(128), nullable=False, index=True)
    owner_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True, index=True)
    total_equity = Column(Float, nullable=False, default=300.0)
    available_balance = Column(Float, nullable=False, default=300.0)
    frozen_balance = Column(Float, nullable=False, default=0.0)
    realized_pnl = Column(Float, nullable=False, default=0.0)
    estimated_points_value = Column(Float, nullable=False, default=0.0)
    risk_profile = Column(String(32), nullable=False, default="balanced")
    allocation_preset = Column(String(64), nullable=True)
    status = Column(String(24), nullable=False, default="active", index=True)
    metadata_json = Column(Text, default='{}')
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp())

    owner_account = relationship("Account", foreign_keys=[owner_account_id])
    exchange_balances = relationship("ArbitragePaperExchangeBalanceDB", back_populates="account")


class ArbitragePaperExchangeBalanceDB(Base):
    """套利 Paper 交易所分账户余额。"""
    __tablename__ = "arbitrage_paper_exchange_balances"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("arbitrage_paper_accounts.id"), nullable=False, index=True)
    exchange = Column(String(32), nullable=False, index=True)
    allocated_usd = Column(Float, nullable=False, default=0.0)
    available_usd = Column(Float, nullable=False, default=0.0)
    frozen_usd = Column(Float, nullable=False, default=0.0)
    asset_balances_json = Column(Text, default='{}')
    strategy_limits_json = Column(Text, default='{}')
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp())

    account = relationship("ArbitragePaperAccountDB", back_populates="exchange_balances")
    __table_args__ = (
        UniqueConstraint("account_id", "exchange", name="uq_arb_paper_account_exchange"),
    )


class ArbitragePaperAllocationPresetDB(Base):
    """套利 Paper 科学配额模板。"""
    __tablename__ = "arbitrage_paper_allocation_presets"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    preset_id = Column(String(64), nullable=False, unique=True, index=True)
    name = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    risk_profile = Column(String(32), nullable=False, default="balanced")
    total_equity_hint = Column(Float, nullable=True)
    exchange_ratios_json = Column(Text, default='{}')
    strategy_limits_json = Column(Text, default='{}')
    is_system = Column(Boolean, default=True, index=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp())


class ArbitragePaperLedgerDB(Base):
    """套利 Paper 资金流水。"""
    __tablename__ = "arbitrage_paper_ledgers"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("arbitrage_paper_accounts.id"), nullable=False, index=True)
    exchange = Column(String(32), nullable=True, index=True)
    action = Column(String(32), nullable=False, index=True)
    amount_usd = Column(Float, nullable=False, default=0.0)
    balance_after = Column(Float, nullable=True)
    strategy_type = Column(String(8), nullable=True, index=True)
    related_position_id = Column(String(64), nullable=True, index=True)
    note = Column(Text, nullable=True)
    metadata_json = Column(Text, default='{}')
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)


class WashTradeLogDB(Base):
    """刷交易/反洗交易安全日志。"""
    __tablename__ = "wash_trade_logs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    ts = Column(Float, nullable=False, index=True)
    exchange = Column(String(32), nullable=False, index=True)
    strategy_type = Column(String(8), nullable=True, index=True)
    size_usd = Column(Float, default=0.0)
    risk_score = Column(Float, default=0.0)
    is_safe = Column(Boolean, default=True, index=True)
    reason = Column(Text, nullable=True)
    metadata_json = Column(Text, default='{}')
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)


class RebateTradeOutcomeDB(Base):
    """Rebate 策略结果样本 — 供 evolver/backtest 学习。"""
    __tablename__ = "rebate_trade_outcomes"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    position_id = Column(String(64), nullable=False, index=True)
    strategy_type = Column(String(8), nullable=False, index=True)
    symbol = Column(String(32), nullable=True, index=True)
    mode = Column(String(16), default="paper")
    pnl = Column(Float, default=0.0)
    rebate = Column(Float, default=0.0)
    points = Column(Float, default=0.0)
    net_value = Column(Float, default=0.0)
    risk_score = Column(Float, default=0.0)
    hold_hours = Column(Float, default=0.0)
    outcome_json = Column(Text, default='{}')
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)


class RebateEvolutionProposalDB(Base):
    """策略进化/规则同步提案 — 统一进入人工确认队列。"""
    __tablename__ = "rebate_evolution_proposals"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    source = Column(String(32), default="evolution", index=True)  # evolution/rule_sync
    strategy_type = Column(String(8), nullable=True, index=True)
    severity = Column(String(16), default="low", index=True)
    title = Column(String(256), nullable=False)
    proposal_json = Column(Text, default='{}')
    status = Column(String(32), default="pending", index=True)  # pending/paper_validated/applied/dismissed
    requires_paper_validation = Column(Boolean, default=True)
    requires_manual_live_confirm = Column(Boolean, default=True)
    related_event_id = Column(Integer, nullable=True, index=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp())


class ExchangeRuleSnapshotDB(Base):
    """交易所/项目规则文本快照。"""
    __tablename__ = "exchange_rule_snapshots"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    source_id = Column(String(64), nullable=False, index=True)
    exchange = Column(String(32), nullable=False, index=True)
    rule_type = Column(String(32), nullable=False, index=True)
    title = Column(String(256), nullable=False)
    url = Column(Text, nullable=True)
    content_hash = Column(String(64), nullable=False, index=True)
    content_text = Column(Text, nullable=False)
    normalized_json = Column(Text, default='{}')
    fetched_at = Column(Float, nullable=False, index=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)


class RuleChangeEventDB(Base):
    """规则变更事件 — status: pending/analyzed/applied/dismissed。"""
    __tablename__ = "rule_change_events"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    source_id = Column(String(64), nullable=False, index=True)
    exchange = Column(String(32), nullable=False, index=True)
    rule_type = Column(String(32), nullable=False, index=True)
    previous_snapshot_id = Column(Integer, nullable=True, index=True)
    current_snapshot_id = Column(Integer, nullable=False, index=True)
    severity = Column(String(8), default="L1", index=True)
    affected_strategies_json = Column(Text, default='[]')
    diff_summary = Column(Text, nullable=True)
    ai_analysis_json = Column(Text, default='{}')
    status = Column(String(32), default="pending", index=True)
    auto_pause_applied = Column(Boolean, default=False)
    requires_code_change = Column(Boolean, default=False)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp())


class RuleAiAnalysisLogDB(Base):
    """规则变更 AI/启发式影响分析报告存档。"""
    __tablename__ = "rule_ai_analysis_logs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    event_id = Column(Integer, nullable=True, index=True)
    source_id = Column(String(64), nullable=False, index=True)
    exchange = Column(String(32), nullable=False, index=True)
    severity = Column(String(8), default="L1", index=True)
    analyzer = Column(String(64), default="heuristic")
    prompt_snapshot = Column(Text, nullable=True)
    analysis_json = Column(Text, default='{}')
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)


class RuleSyncGateStateDB(Base):
    """规则同步执行闸门 — 控制 Rebate/S1-S8 与可选 V3 暂停。"""
    __tablename__ = "rule_sync_gate_state"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    rebate_pause = Column(Boolean, default=False, index=True)
    v3_pause = Column(Boolean, default=False, index=True)
    paused_strategies_json = Column(Text, default='[]')
    pause_reason = Column(Text, nullable=True)
    allow_manual_override = Column(Boolean, default=False)
    requires_code_change = Column(Boolean, default=False)
    paused_at = Column(Float, nullable=True)
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp())


class RuleSyncAuditLogDB(Base):
    """规则同步/手动 override/Live 应用审计日志。"""
    __tablename__ = "rule_sync_audit_logs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    actor_user_id = Column(Integer, nullable=True, index=True)
    action = Column(String(64), nullable=False, index=True)
    target_type = Column(String(64), nullable=False, index=True)
    target_id = Column(String(128), nullable=True, index=True)
    before_json = Column(Text, default='{}')
    after_json = Column(Text, default='{}')
    reason = Column(Text, nullable=True)
    risk_acknowledged = Column(Boolean, default=False)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)


class OpenCodeInsightDB(Base):
    """OpenCode 分析结论 — 供 Context Pack open_issues 与面板展示。"""
    __tablename__ = "opencode_insights"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    window = Column(String(8), default="24h", index=True)
    domain = Column(String(16), default="ai", index=True)
    severity = Column(String(16), default="info", index=True)
    category = Column(String(64), default="general")
    title = Column(String(256), nullable=False)
    finding_json = Column(Text, default='{}')
    status = Column(String(32), default="open", index=True)  # open/resolved/dismissed
    source = Column(String(32), default="opencode")
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)
    resolved_at = Column(TIMESTAMP, nullable=True)


class OpenCodeEvolutionProposalDB(Base):
    """OpenCode / 系统进化提案 — Paper 自动 apply + 验证。"""
    __tablename__ = "opencode_evolution_proposals"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    source = Column(String(32), default="opencode", index=True)
    severity = Column(String(16), default="minor", index=True)
    title = Column(String(256), nullable=False)
    proposal_json = Column(Text, default='{}')
    patch_type = Column(String(32), default="tuning")  # tuning | policy_yaml | shadow_py
    status = Column(String(32), default="pending", index=True)
    baseline_json = Column(Text, default='{}')
    after_json = Column(Text, default='{}')
    requires_paper_validation = Column(Boolean, default=True)
    requires_manual_live_confirm = Column(Boolean, default=True)
    applied_at = Column(TIMESTAMP, nullable=True)
    validated_at = Column(TIMESTAMP, nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp())


class DashboardLayout(Base):
    """交易矩阵仪表盘 — 用户自定义网格布局持久化。

    widgets: [{id, type, x, y, w, h, config}]  react-grid-layout 网格项 + widget 类型/配置
    selected_accounts: [{account_id, exchange, trading_mode, label}]  多选对比的账户组合
    """
    __tablename__ = "dashboard_layouts"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    name = Column(String(100), nullable=False, default="默认布局")
    is_active = Column(Boolean, default=False, index=True)
    widgets = Column(JSON, nullable=True)  # List[dict]
    selected_accounts = Column(JSON, nullable=True)  # List[dict]
    created_at = Column(TIMESTAMP, server_default=func.current_timestamp(), index=True)
    updated_at = Column(TIMESTAMP, server_default=func.current_timestamp(), onupdate=func.current_timestamp())


# ═══════════════════════════════════════════════════════════════
# Strategic Analyst ORM 模型 — 注册到 AnalyticsBase
# 必须在此导入以确保 AnalyticsBase.metadata 包含这些表定义
# ═══════════════════════════════════════════════════════════════
try:
    from backend.services.strategic_analyst.db_models import (  # noqa: F401
        StrategicMacroSnapshot, StrategicReportRecord, NewCoinOpportunityRecord,
        StrategicMemoryRecord, CrossMarketCorrelationRecord, MacroRegimeStateRecord,
        TrendPredictionRecord,
    )
except ImportError:
    from services.strategic_analyst.db_models import (  # noqa: F401
        StrategicMacroSnapshot, StrategicReportRecord, NewCoinOpportunityRecord,
        StrategicMemoryRecord, CrossMarketCorrelationRecord, MacroRegimeStateRecord,
        TrendPredictionRecord,
    )
