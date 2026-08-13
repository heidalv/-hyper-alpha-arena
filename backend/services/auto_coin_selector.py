"""
全自动 AI 选币服务 (AutoCoinSelector)

五阶段流水线:
  1. Scan  - 获取交易所全量交易对，算法多维度打分排名
  2. Enrich - 获取市场数据、链上数据、新闻面
  3. AI Review - AI 深度审核，结合多维数据决策
  4. Inject - 将审核通过的 3-5 个币种注入运行中的交易会话
  5. Evaluate - 定期评估自动选中的币种，淘汰表现不佳者
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class CandidateCoin:
    """候选币种"""
    symbol: str
    score: float = 0.0
    scores_detail: Dict[str, float] = field(default_factory=dict)
    rank: int = 0

    market_cap: Optional[float] = None
    volume_24h: Optional[float] = None
    volume_change_24h: Optional[float] = None
    price: Optional[float] = None
    price_change_24h: Optional[float] = None
    funding_rate: Optional[float] = None
    open_interest: Optional[float] = None
    volatility_24h: Optional[float] = None

    onchain_data: Dict[str, Any] = field(default_factory=dict)
    news_sentiment: Optional[float] = None
    social_volume: Optional[int] = None

    # 阶段D:社交热度 + 新上市检测(前瞻信号,补齐滞后指标盲区)
    social_score: float = 0.0          # CoinGecko trending 0.0(不在榜)~1.0(第1名)
    is_new_listing: bool = False       # 本周期交易所新出现的交易对

    ai_approved: bool = False
    ai_reason: str = ""
    ai_confidence: float = 0.0
    # 阶段C：三层渐进式 AI 审核
    # ai_layer: A/B/C(全仓位通过) D/E(试仓) F/G(排除)
    ai_layer: str = ""
    # test_position=True → 下游用 AUTO_COIN_PROBE_SIZE_MULT (50%) 试仓
    test_position: bool = False

    injected_at: Optional[datetime] = None
    session_id: str = ""


@dataclass
class CoinPerformanceData:
    """币种在当前会话中的交易表现数据"""
    symbol: str
    total_trades: int = 0
    win_count: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    avg_pnl_per_trade: float = 0.0
    max_single_loss: float = 0.0
    sharpe_estimate: float = 0.0
    holding_duration_hours: float = 0.0
    is_new: bool = False


@dataclass
class RetentionScore:
    """币种留存评分（4 维度加权）"""
    symbol: str
    performance_score: float = 0.0
    market_fit_score: float = 0.0
    retention_bonus: float = 0.0
    diversity_score: float = 0.0
    composite_score: float = 0.0
    removal_risk_note: str = ""


# 冷却等级对应时长（秒）
COOLING_DURATIONS: Dict[str, int] = {
    "short": 2 * 3600,
    "long": 8 * 3600,
    "very_long": 24 * 3600,
}


@dataclass
class CandidatePool:
    """候选池管理：active / cooling / blacklist"""
    active: Dict[str, CandidateCoin] = field(default_factory=dict)
    cooling: Dict[str, Tuple[datetime, str]] = field(default_factory=dict)  # (start_time, tier)
    blacklist: Dict[str, datetime] = field(default_factory=dict)
    max_active: int = 5
    cooling_period: int = 3600

    def to_dict(self) -> dict:
        return {
            "active": {s: {"score": c.score, "ai_confidence": c.ai_confidence,
                           "injected_at": c.injected_at.isoformat() if c.injected_at else None}
                       for s, c in self.active.items()},
            "cooling_count": len(self.cooling),
            "blacklist_count": len(self.blacklist),
            "max_active": self.max_active,
            "cooling_period": self.cooling_period,
        }


# ═══════════════════════════════════════════════════════════════════════════
# 配置 — 统一从 settings 导入
# ═══════════════════════════════════════════════════════════════════════════

from backend.config.settings import (
    AUTO_COIN_MAX_COUNT,
    AUTO_COIN_MIN_SCORE as AUTO_COIN_MIN_SCORE_CFG,
    AUTO_COIN_MIN_AI_CONFIDENCE,
    AUTO_COIN_SCAN_INTERVAL as AUTO_COIN_SCAN_INTERVAL_CFG,
    AUTO_COIN_COOLING_HOURS,
    AUTO_COIN_BLACKLIST_DAYS,
    AUTO_COIN_REPLACEMENT_MARGIN,
    AUTO_COIN_MIN_HOLD_HOURS,
    AUTO_COIN_MAX_HOLD_HOURS_LONG as AUTO_COIN_MAX_HOLD_HOURS,
    AUTO_COIN_EXPIRY_KEEP_SCORE,
    AUTO_COIN_EXPIRY_REMOVE_SCORE,
    AUTO_COIN_GRACE_CYCLES,
    AUTO_COIN_COOLING_SHORT_HOURS,
    AUTO_COIN_COOLING_LONG_HOURS,
    AUTO_COIN_COOLING_VERY_LONG_HOURS,
    AUTO_COIN_PERF_WEIGHT,
    AUTO_COIN_MARKET_WEIGHT,
    AUTO_COIN_RETENTION_WEIGHT,
    AUTO_COIN_DIVERSITY_WEIGHT,
)

# 模块级别名（保持内部兼容）
AUTO_COIN_MAX_POOL_SIZE = AUTO_COIN_MAX_COUNT
AUTO_COIN_MIN_SCORE = AUTO_COIN_MIN_SCORE_CFG
AUTO_COIN_SCAN_INTERVAL = AUTO_COIN_SCAN_INTERVAL_CFG
AUTO_COIN_HOLD_MIN_CYCLES = AUTO_COIN_GRACE_CYCLES
AUTO_COIN_PERFORMANCE_THRESHOLD = float(os.getenv("AUTO_COIN_PERFORMANCE_THRESHOLD", "-0.05"))

# 冷却时长映射（秒）
COOLING_SHORT_S = AUTO_COIN_COOLING_SHORT_HOURS * 3600
COOLING_LONG_S = AUTO_COIN_COOLING_LONG_HOURS * 3600
COOLING_VERY_LONG_S = AUTO_COIN_COOLING_VERY_LONG_HOURS * 3600
BLACKLIST_S = AUTO_COIN_BLACKLIST_DAYS * 86400

_hl_snapshot_cache: Optional[Tuple[float, Dict[str, Dict[str, Any]]]] = None
_hl_candles_cache: Optional[Tuple[float, Dict[str, list]]] = None
_HL_CACHE_TTL = 60.0
AUTO_COIN_INJECTED_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "auto_coin_injected")

# ═══════════════════════════════════════════════════════════════════════════
# 阶段B 评分重构：方向性动量 + regime 自适应权重 + 缺数据默认 0.3
# ═══════════════════════════════════════════════════════════════════════════
# Regime 自适应权重 —— trend 重动量 / range 重流动性 / extreme 重安全
# 五维键与 _multi_dimension_score 输出的 scores dict 保持一致
_SCORING_WEIGHTS: Dict[str, Dict[str, float]] = {
    "trending": {"vol_score": 0.15, "mom_score": 0.30, "fund_score": 0.10, "vola_score": 0.15, "trend_score": 0.30},
    "ranging":  {"vol_score": 0.25, "mom_score": 0.15, "fund_score": 0.15, "vola_score": 0.15, "trend_score": 0.30},
    "extreme":  {"vol_score": 0.30, "mom_score": 0.10, "fund_score": 0.10, "vola_score": 0.20, "trend_score": 0.30},
}
# 未知 regime → 等权重（最保守）
_DEFAULT_WEIGHTS: Dict[str, float] = {
    "vol_score": 0.20, "mom_score": 0.20, "fund_score": 0.20, "vola_score": 0.20, "trend_score": 0.20,
}
# 数据缺失时维度默认值（低于 0.5 cutoff，避免无数据币种混入）
_MISSING_DEFAULT = 0.3


# ═══════════════════════════════════════════════════════════════════════════
# AutoCoinSelector
# ═══════════════════════════════════════════════════════════════════════════

class AutoCoinSelector:
    """
    全自动 AI 选币服务。

    每个实例绑定到一个交易会话 (session_id) 和交易员 (account_id)。
    通过 account_id 确定交易所，通过交易所获取全量交易对。
    """

    def __init__(self, session_id: str, account_id: int, db_session_factory=None):
        self.session_id = session_id
        self.account_id = account_id
        self._db_factory = db_session_factory
        self._exchange: Optional[str] = None
        self._pool = CandidatePool(
            max_active=AUTO_COIN_MAX_POOL_SIZE,
            cooling_period=AUTO_COIN_COOLING_HOURS * 3600,
        )
        self._evaluation_count: Dict[str, int] = {}
        self._auto_symbols: Set[str] = set()
        self._baseline_symbols: Optional[Set[str]] = None
        # 阶段D:新币检测 —— 记住上一轮扫描时见过的交易对集合,
        # 本轮 diff 出新增的即标 is_new_listing(首次运行不全部标记)
        self._known_symbols: Set[str] = set()
        self._injected_file = os.path.join(AUTO_COIN_INJECTED_DIR, f"{session_id}.json")
        self._load_injected()
        self._restore_pool_from_persisted_symbols()
        self._last_scan_time: Optional[datetime] = None
        self._last_inject_block_reason: Optional[str] = None
        self._cycle_count: int = 0
        self._cancel_requested = False
        self._last_degraded: Optional[str] = None
        self._last_replace_count: int = 0
        self._last_renewed_no_change: int = 0
        self._last_rank_source: str = "legacy"
        self._board_sourced: bool = False

    def _pool_limit(self, db=None) -> int:
        """本会话 AI 选币槽位：优先读 session.auto_coin_max_slots，限制 5~10，默认 5。"""
        close = False
        try:
            if db is None:
                if not self._db_factory:
                    return 5
                db = self._db_factory()
                close = True
            from backend.database.models import FullAutoSession

            s = (
                db.query(FullAutoSession)
                .filter(FullAutoSession.session_id == self.session_id)
                .first()
            )
            raw = getattr(s, "auto_coin_max_slots", None) if s else None
            if raw is None:
                return 5
            return max(5, min(10, int(raw)))
        except Exception as e:
            logger.debug(f"[AutoCoinSelector] pool_limit fallback: {e}")
            return 5
        finally:
            if close and db is not None:
                try:
                    db.close()
                except Exception:
                    pass

    def _injected_file_path(self) -> str:
        return self._injected_file

    def _load_injected(self):
        self._injected_times: Dict[str, datetime] = {}
        try:
            if os.path.exists(self._injected_file):
                with open(self._injected_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._auto_symbols = set(str(s).upper() for s in data.get("symbols", []))
                    for sym, ts in (data.get("symbol_times") or {}).items():
                        try:
                            self._injected_times[str(sym).upper()] = datetime.fromisoformat(str(ts))
                        except Exception:
                            pass
                    # 冷却 / 黑名单持久化（重启不丢）
                    for sym, meta in (data.get("cooling") or {}).items():
                        try:
                            if isinstance(meta, dict):
                                start = datetime.fromisoformat(str(meta.get("start")))
                                tier = str(meta.get("tier") or "short")
                            else:
                                start = datetime.fromisoformat(str(meta))
                                tier = "short"
                            self._pool.cooling[str(sym).upper()] = (start, tier)
                        except Exception:
                            pass
                    for sym, ts in (data.get("blacklist") or {}).items():
                        try:
                            self._pool.blacklist[str(sym).upper()] = datetime.fromisoformat(str(ts))
                        except Exception:
                            pass
                    logger.info(
                        f"[AutoCoinSelector] Loaded {len(self._auto_symbols)} persisted auto symbols, "
                        f"cooling={len(self._pool.cooling)}, blacklist={len(self._pool.blacklist)}"
                    )
        except Exception as e:
            logger.warning(f"[AutoCoinSelector] Failed to load injected file: {e}")
            self._auto_symbols = set()
            self._injected_times = {}

    def _save_injected(self):
        try:
            os.makedirs(os.path.dirname(self._injected_file), exist_ok=True)
            symbol_times = {}
            for sym, entry in self._pool.active.items():
                if entry.injected_at:
                    symbol_times[sym] = entry.injected_at.isoformat()
            cooling = {
                sym: {"start": start.isoformat(), "tier": tier}
                for sym, (start, tier) in self._pool.cooling.items()
            }
            blacklist = {
                sym: ts.isoformat() for sym, ts in self._pool.blacklist.items()
            }
            with open(self._injected_file, "w", encoding="utf-8") as f:
                json.dump({
                    "symbols": sorted(self._auto_symbols),
                    "symbol_times": symbol_times,
                    "cooling": cooling,
                    "blacklist": blacklist,
                    "updated_at": datetime.now().isoformat(),
                    "audit_only_file": True,  # 文件不作 DB 空池回灌源，仅审计/冷却/黑名单
                }, f)
        except Exception as e:
            logger.error(f"[AutoCoinSelector] Failed to save injected file: {e}")

    def _hydrate_injected_times(self, db: Session):
        """从审计表 / 持久化文件恢复上币时间，避免重启后过期计时被重置。"""
        try:
            from backend.database.models import AutoCoinSelection
        except Exception:
            AutoCoinSelection = None  # type: ignore

        for sym, entry in self._pool.active.items():
            if AutoCoinSelection is not None:
                row = (
                    db.query(AutoCoinSelection)
                    .filter(
                        AutoCoinSelection.session_id == self.session_id,
                        AutoCoinSelection.symbol == sym,
                        AutoCoinSelection.action == "injected",
                    )
                    .order_by(AutoCoinSelection.id.desc())
                    .first()
                )
                if row and row.created_at:
                    entry.injected_at = row.created_at
                    continue
            cached = getattr(self, "_injected_times", {}).get(sym)
            if cached:
                entry.injected_at = cached

    def _restore_pool_from_persisted_symbols(self):
        """把持久化自动选币恢复到 active 池，避免重启后 UI 显示 0/5 并继续注入。"""
        injected_map = getattr(self, "_injected_times", {}) or {}
        for sym in sorted(self._auto_symbols):
            self._pool.active.setdefault(sym, CandidateCoin(
                symbol=sym,
                score=0.5,
                ai_approved=True,
                ai_confidence=0.5,
                injected_at=injected_map.get(sym),
                session_id=self.session_id,
            ))

    def _sync_active_pool_from_db(self, db: Session):
        """以 DB 中的 session.symbols / auto_coin_symbols 为准恢复自动选币池。

        V5.3 修复: 崩溃重启后 file_auto 有持久化币种但 session.symbols 缺失时，
        先将其重新注入 session.symbols，防止 auto coin 被永久销毁。
        """
        session = db.query(self._get_session_model()).filter(
            self._get_session_model().session_id == self.session_id
        ).first()
        if not session:
            return None

        current_symbols = {str(s).upper() for s in (session.symbols or [])}
        db_auto = {str(s).upper() for s in (getattr(session, "auto_coin_symbols", None) or [])}
        file_auto = {str(s).upper() for s in self._auto_symbols}

        # ── V5.3: 恢复崩溃/重启后丢失的 auto coin ──
        # auto-coin 隔离在 auto_coin_symbols，孤儿判定应对 db_auto，而非 session.symbols
        orphaned = file_auto - db_auto
        if orphaned:
            logger.warning(
                f"[AutoCoinSelector] 检测到 {len(orphaned)} 个孤儿 auto coin "
                f"(文件有但auto_coin_symbols缺失): {orphaned}，正在恢复注入..."
            )
            try:
                from backend.services.full_auto_trading_service import FullAutoTradingService
                service = FullAutoTradingService.get_instance()
                result = service.add_symbols(db, self.session_id, list(orphaned), is_auto_coin=True)
                if result.get("success"):
                    logger.info(
                        f"[AutoCoinSelector] 孤儿 auto coin 恢复注入成功: "
                        f"{result.get('added', [])}"
                    )
                    db.refresh(session)
                    current_symbols = {str(s).upper() for s in (session.symbols or [])}
                    db_auto = {str(s).upper() for s in (getattr(session, "auto_coin_symbols", None) or [])}
                else:
                    logger.error(
                        f"[AutoCoinSelector] 孤儿 auto coin 恢复注入失败: "
                        f"{result.get('error')}"
                    )
            except Exception as _orphan_err:
                logger.error(
                    f"[AutoCoinSelector] 孤儿 auto coin 恢复注入异常: {_orphan_err}"
                )

        from backend.services.auto_coin_policy import filter_strict_auto_symbols
        # [隔离修复] auto-coin 只在 auto_coin_symbols；DB 为准，禁止空 DB 时用文件钉死池
        # 文件仅用于上文孤儿恢复注入 DB，恢复后仍以 db_auto 为准
        db_auto_refreshed = {str(s).upper() for s in (getattr(session, "auto_coin_symbols", None) or [])}
        # [2026-08-04 修复] 兜底过滤：当前会话交易所可交易目录之外的币不入池，
        # 防止「池里有币但交易所无行情」导致的永久数据缺失与永远无法交易
        # （实测 KPEPE@asterdex：hyperliquid 有行情、asterdex 无 → 各周期全部停更数天）。
        try:
            _ex = self.resolve_exchange(db)
            if _ex == "aster":
                _ex = "asterdex"
            from backend.services.kline_sync_meta import list_catalog_symbols
            _catalog = list_catalog_symbols(_ex) or []
            if _catalog:
                _dropped = {s for s in db_auto_refreshed if s not in set(_catalog)}
                if _dropped:
                    logger.warning(
                        f"[AutoCoinSelector] 交易所目录兜底过滤，剔除池内无效币: "
                        f"{sorted(_dropped)} @{_ex}"
                    )
                    db_auto_refreshed = {s for s in db_auto_refreshed if s in set(_catalog)}
        except Exception as _catalog_err:
            logger.debug(f"[AutoCoinSelector] catalog 兜底过滤跳过: {_catalog_err}")
        active_auto = set(filter_strict_auto_symbols(db_auto_refreshed))

        self._auto_symbols = set(active_auto)
        new_pool = {}
        for sym in sorted(active_auto):
            existing = self._pool.active.get(sym)
            if existing:
                new_pool[sym] = existing
            else:
                new_pool[sym] = CandidateCoin(
                    symbol=sym,
                    score=0.5,
                    ai_approved=True,
                    ai_confidence=0.5,
                    injected_at=getattr(self, "_injected_times", {}).get(sym),
                    session_id=self.session_id,
                )
        self._pool.active = new_pool

        self._hydrate_injected_times(db)
        session.auto_coin_symbols = sorted(active_auto)
        db.commit()
        self._save_injected()
        return session

    def _has_unfinished_auto_work(self, db: Session, symbols: Set[str]) -> Tuple[bool, str]:
        """已有自动选币还没结束时禁止继续添加/替换，防止无限叠仓。"""
        if not symbols:
            return False, ""
        try:
            from backend.database.models import AIStrategy, PaperPosition

            session = db.query(self._get_session_model()).filter(
                self._get_session_model().session_id == self.session_id
            ).first()
            trading_account_id = (
                getattr(session, "paper_account_id", None)
                or getattr(session, "trading_account_id", None)
                or getattr(session, "account_id", None)
            ) if session else self.account_id

            open_positions = (
                db.query(PaperPosition.symbol)
                .filter(
                    PaperPosition.account_id == trading_account_id,
                    PaperPosition.symbol.in_(list(symbols)),
                    PaperPosition.status == "open",
                )
                .distinct()
                .all()
            )
            if open_positions:
                syms = sorted({row[0] for row in open_positions})
                return True, f"仍有未平仓持仓: {', '.join(syms)}"

            active_strategies = (
                db.query(AIStrategy.primary_symbol)
                .filter(
                    AIStrategy.account_id == self.account_id,
                    AIStrategy.primary_symbol.in_(list(symbols)),
                    AIStrategy.status.in_(["active", "paused"]),
                )
                .distinct()
                .all()
            )
            if active_strategies:
                syms = sorted({row[0] for row in active_strategies if row[0]})
                return True, f"仍有未结束策略: {', '.join(syms)}"
        except Exception as e:
            logger.warning(f"[AutoCoinSelector] 未结束交易检查失败，保守阻止新增: {e}")
            return True, "未结束交易检查失败"
        return False, ""

    # ── 交易所解析 ──────────────────────────────────────────────────────

    def resolve_exchange(self, db: Session) -> str:
        """根据会话/账户解析当前使用的交易所（决策同源）。

        优先级：
          1. 缓存 self._exchange
          2. full_auto 会话 active_exchange（AI 交易员可切换）
          3. account.selected_exchange
          4. get_active_exchange() / DEFAULT_EXCHANGE（asterdex）
        """
        if self._exchange:
            return self._exchange

        # 会话级 active_exchange
        try:
            session = db.query(self._get_session_model()).filter(
                self._get_session_model().session_id == self.session_id
            ).first()
            sess_ex = getattr(session, "active_exchange", None) if session else None
            if sess_ex and str(sess_ex).strip():
                ex = str(sess_ex).strip().lower()
                if ex == "aster":
                    ex = "asterdex"
                self._exchange = ex
                logger.info(
                    f"[AutoCoinSelector] Resolved exchange via session.active_exchange: "
                    f"{self._exchange} for session {self.session_id}"
                )
                return self._exchange
        except Exception as e:
            logger.debug(f"[AutoCoinSelector] 读会话交易所失败: {e}")

        from backend.database.models import Account
        account = db.query(Account).filter(Account.id == self.account_id).first()
        if not account:
            from backend.services.exchange_config import get_active_exchange
            self._exchange = get_active_exchange() or "asterdex"
            logger.warning(
                f"[AutoCoinSelector] Account {self.account_id} not found, "
                f"fallback to {self._exchange}"
            )
            return self._exchange

        selected = getattr(account, "selected_exchange", None)
        if selected and selected.strip():
            ex = selected.strip().lower()
            if ex == "aster":
                ex = "asterdex"
            self._exchange = ex
            logger.info(
                f"[AutoCoinSelector] Resolved exchange via selected_exchange: "
                f"{self._exchange} for account {self.account_id}"
            )
            return self._exchange

        from backend.services.exchange_config import get_active_exchange
        self._exchange = get_active_exchange() or "asterdex"
        logger.warning(
            f"[AutoCoinSelector] No exchange explicitly set for account {self.account_id}, "
            f"fallback to {self._exchange}"
        )
        return self._exchange

    # ── 阶段 1: 全市场扫描 + 多维度打分 ──────────────────────────────

    def _detect_new_listings(self, all_symbols: List[str]) -> Set[str]:
        """检测本周期新增的交易对(阶段D 新数据维度)。

        与 ``self._known_symbols`` 做集合 diff,返回本轮新出现的 symbol(大写)。

        首次运行(``_known_symbols`` 为空)时只初始化基线,不把全量标记为新币 ——
        否则冷启动会把整个交易所都当成「新上市」,污染 AI prompt。

        副作用:更新 ``self._known_symbols`` 为本轮全量集合。
        """
        # 兼容跳过 __init__ 的单测构造:缺属性时按空集处理
        known = getattr(self, "_known_symbols", None)
        if known is None:
            known = set()
        current = set(str(s).upper() for s in all_symbols)
        if not known:
            # First run — initialize baseline without flagging everything as "new"
            self._known_symbols = current
            return set()
        new = current - known
        self._known_symbols = current
        if new:
            logger.info(f"[AutoCoinSelector] New listings detected: {sorted(new)}")
        return new

    def scan_candidates(
        self,
        db: Session,
        focus_symbols: Optional[List[str]] = None,
    ) -> List[CandidateCoin]:
        """
        阶段 1: 获取全量交易对，算法多维度打分。
        返回按评分降序排列的 TOP 候选币种列表（上限 100 个）。

        focus_symbols: M3 Fast/Normal 轻量模式——只评指定币 + 当前池，跳过全市场。
        优先走共用 CoinRankEngine（数据中心）；失败回退 MarketScanner 旧路径。
        """
        exchange = self.resolve_exchange(db)
        logger.info(f"[AutoCoinSelector] Phase 1: Scanning {exchange} market...")

        try:
            from backend.services.coin_rank.engine import engine_enabled

            if engine_enabled():
                return self._scan_candidates_via_rank(db, focus_symbols=focus_symbols, exchange=exchange)
        except Exception as e:
            logger.warning(f"[AutoCoinSelector] CoinRankEngine unavailable, legacy scan: {e}")

        return self._scan_candidates_legacy(db, focus_symbols=focus_symbols, exchange=exchange)

    def _scan_candidates_via_rank(
        self,
        db: Session,
        focus_symbols: Optional[List[str]] = None,
        exchange: Optional[str] = None,
    ) -> List[CandidateCoin]:
        """P1：数据中心 + CoinRankEngine 粗分，与平台看板对齐。"""
        from backend.services.auto_coin_policy import is_training_core_symbol
        from backend.services.coin_rank.engine import rank_symbols, rank_universe
        from backend.services.coin_rank.gates import is_strong_eligible

        exchange = exchange or self.resolve_exchange(db)
        now = datetime.now()

        if focus_symbols:
            focus_set = {s.upper() for s in focus_symbols if s}
            focus_set |= {s.upper() for s in self._pool.active.keys()}
            ranked = rank_symbols(list(focus_set), apply_factor=True, apply_gate=True, apply_decay=True)
            logger.info(
                f"[AutoCoinSelector] RankEngine focus scan: {len(ranked)} "
                f"(focus={len(focus_symbols)})"
            )
        else:
            ranked = rank_universe(limit=100, apply_factor=True, apply_gate=True, apply_decay=True)
            logger.info(f"[AutoCoinSelector] RankEngine universe: {len(ranked)} from DC")

        new_listings = self._detect_new_listings([r.symbol for r in ranked])
        try:
            from backend.services.autocoin_social_signal import fetch_trending_scores
            social_map = fetch_trending_scores()
        except Exception:
            social_map = {}

        candidates: List[CandidateCoin] = []
        for rr in ranked:
            symbol_upper = rr.symbol.upper()
            if is_training_core_symbol(symbol_upper):
                continue
            if self._is_blacklisted(symbol_upper, now):
                continue
            if self._is_cooling(symbol_upper, now):
                continue
            if rr.gate == "hard_reject":
                continue

            total_score = float(rr.composite)
            social = float(social_map.get(symbol_upper) or 0)
            if social > 0:
                total_score = min(1.0, total_score * (1.0 + min(0.15, social * 0.15)))

            c = CandidateCoin(
                symbol=symbol_upper,
                score=total_score,
                scores_detail={
                    "liquidity": rr.liquidity,
                    "cs_momentum": rr.cs_momentum,
                    "ts_momentum": rr.ts_momentum,
                    "trap_soft": rr.trap_soft,
                    "mtf_confluence": rr.mtf_confluence,
                    "factor_match": float(rr.factor_match or 0),
                    "gate": 1.0 if rr.gate == "pass" else 0.0,
                },
                rank=rr.rank,
                volume_24h=rr.volume_24h,
                price=rr.price,
                price_change_24h=rr.change_24h,
                is_new_listing=symbol_upper in new_listings,
            )
            # soft_reject：强制试仓层
            if rr.gate == "soft_reject" or not is_strong_eligible(rr):
                c.test_position = True
                c.scores_detail["force_test_position"] = 1.0
            candidates.append(c)

        candidates.sort(key=lambda x: x.score, reverse=True)
        for i, c in enumerate(candidates):
            c.rank = i + 1
        logger.info(
            f"[AutoCoinSelector] Phase 1 RankEngine done: {len(candidates)} candidates "
            f"top={[c.symbol for c in candidates[:5]]}"
        )
        return candidates[:100]

    def _scan_candidates_legacy(
        self,
        db: Session,
        focus_symbols: Optional[List[str]] = None,
        exchange: Optional[str] = None,
    ) -> List[CandidateCoin]:
        """旧路径：MarketScanner 全市场（灾难兜底）。"""
        exchange = exchange or self.resolve_exchange(db)
        from backend.services.market_scanner import MarketScanner
        all_symbols = MarketScanner.get_all_tradable_symbols(exchange)
        if not all_symbols:
            # 再试数据中心
            try:
                from backend.services.coin_rank.features import list_universe_symbols
                all_symbols = list_universe_symbols(200)
                logger.warning("[AutoCoinSelector] MarketScanner empty, using DC symbols")
            except Exception:
                logger.warning(f"[AutoCoinSelector] No symbols found for {exchange}")
                return []

        if focus_symbols:
            focus_set = {s.upper() for s in focus_symbols if s}
            focus_set |= {s.upper() for s in self._pool.active.keys()}
            all_symbols = [s for s in all_symbols if str(s).upper() in focus_set]
            logger.info(
                f"[AutoCoinSelector] Focus scan: {len(all_symbols)} symbols "
                f"(focus={len(focus_symbols)})"
            )
        else:
            logger.info(f"[AutoCoinSelector] Got {len(all_symbols)} symbols from {exchange}")

        global _hl_snapshot_cache
        if exchange == "hyperliquid":
            t0 = time.time()
            if not _hl_snapshot_cache or (t0 - _hl_snapshot_cache[0]) >= _HL_CACHE_TTL:
                try:
                    logger.info(f"[AutoCoinSelector] Pre-fetching HL snapshot for {len(all_symbols)} symbols...")
                    _hl_snapshot_cache = (t0, self._build_hl_snapshot_cache())
                    logger.info(f"[AutoCoinSelector] HL snapshot cached: {len(_hl_snapshot_cache[1])} entries in {time.time()-t0:.1f}s")
                except Exception as e:
                    logger.error(f"[AutoCoinSelector] HL snapshot cache build failed: {e}", exc_info=True)

        candidates: List[CandidateCoin] = []
        now = datetime.now()

        from backend.services.auto_coin_policy import is_training_core_symbol

        # [2026-07-18 新增 P1] Universe五步管线硬门（规划文档§5.3）：流动性/波动率
        # 适配/相关性去重。只会让候选池更严格，不新增交易权限——已持仓/手动选币/
        # 已激活策略走 _resolve_session_trade_symbols 另一条路径，不经过这里，
        # 不受影响。UNIVERSE_GATE_ENABLED=false 可秒回滚。冷启动(未rebuild过)时
        # is_qualified 对所有symbol放行，不会因为管理器还没跑就把选币全拦掉。
        _universe_gate_on = os.getenv("AUTO_COIN_UNIVERSE_GATE_ENABLED", "true").lower() in ("true", "1", "yes")
        _universe_mgr = None
        if _universe_gate_on:
            try:
                from backend.services.alpha.universe_manager import universe_manager as _universe_mgr
                # Paper 软门：Universe 过小（如 rebuild 后只剩 4 币）则本轮跳过硬拦，否则 0 候选
                try:
                    from backend.config.settings import (
                        PAPER_AUTO_COIN_UNIVERSE_MIN_SIZE,
                        PAPER_AUTO_COIN_UNIVERSE_SOFT,
                    )
                    st = _universe_mgr.get_state()
                    n_active = len(st.active_symbols()) if st and st.selected else 0
                    if (
                        PAPER_AUTO_COIN_UNIVERSE_SOFT
                        and self._is_paper_session(db)
                        and n_active > 0
                        and n_active < int(PAPER_AUTO_COIN_UNIVERSE_MIN_SIZE)
                    ):
                        logger.warning(
                            f"[AutoCoinSelector] Paper Universe 过小({n_active}<"
                            f"{PAPER_AUTO_COIN_UNIVERSE_MIN_SIZE})，本轮跳过 Universe 硬门"
                        )
                        _universe_mgr = None
                except Exception as _soft_err:
                    logger.debug(f"[AutoCoinSelector] universe soft-gate skip: {_soft_err}")
            except Exception as e:
                logger.debug(f"[AutoCoinSelector] UniverseManager 加载失败,跳过本轮门控: {e}")
                _universe_mgr = None

        # 阶段B Fix 2:取本轮 regime（SingleSourceCache 单例,失败默认 unknown→等权）
        # 只读一次,整个循环复用,避免每 symbol 都加锁
        _regime = self._fetch_current_regime()
        _weights = self._resolve_regime_weights(_regime)
        if _regime and _regime != "unknown":
            logger.info(f"[AutoCoinSelector] Phase 1 regime='{_regime}', weights={_weights}")
        else:
            logger.info(f"[AutoCoinSelector] Phase 1 regime 未知,使用等权重")

        # 阶段D:新币检测 —— diff 出本轮交易所新增交易对(首次运行只建基线)
        new_listings = self._detect_new_listings(all_symbols)
        # 阶段D:社交热度 —— 模块内置 5min 缓存,失败返回空 dict,流水线不受影响
        try:
            from backend.services.autocoin_social_signal import fetch_trending_scores
            social_map = fetch_trending_scores()
        except Exception as e:
            logger.debug(f"[AutoCoinSelector] social signal skip: {e}")
            social_map = {}

        for symbol in all_symbols:
            symbol_upper = symbol.upper()
            if is_training_core_symbol(symbol_upper):
                continue
            if self._is_blacklisted(symbol_upper, now):
                continue
            if self._is_cooling(symbol_upper, now):
                continue
            if _universe_mgr is not None and not _universe_mgr.is_qualified(symbol_upper):
                continue

            scores = self._multi_dimension_score(symbol_upper, exchange, regime=_regime)
            # Fix 2:加权汇总替代简单平均
            total_score = self._compute_weighted_total(scores, _weights)

            # M4 因子匹配：仅市场分过预筛的币才算因子暴露（避免 500 币 × 因子评估 ≈ 20s）
            # 注意：必须用模块级 `os`（本函数未 `import os as _os`，否则 NameError 整轮扫描崩溃）
            _factor_match = None
            _alpha = 0.0
            try:
                from backend.config.settings import (
                    AUTO_COIN_FACTOR_BLEND,
                    AUTO_COIN_FACTOR_MATCH_ENABLED,
                    AUTO_COIN_FACTOR_MIN_ABS_ALPHA,
                    AUTO_COIN_FACTOR_MIN_MARKET,
                )
                _min_market = float(AUTO_COIN_FACTOR_MIN_MARKET)
                _factor_on = bool(AUTO_COIN_FACTOR_MATCH_ENABLED)
                _min_abs_alpha = float(AUTO_COIN_FACTOR_MIN_ABS_ALPHA)
                _blend = float(AUTO_COIN_FACTOR_BLEND)
            except Exception:
                _min_market = float(os.getenv("AUTO_COIN_FACTOR_MIN_MARKET", "0.40"))
                _factor_on = os.getenv("AUTO_COIN_FACTOR_MATCH_ENABLED", "true").lower() in (
                    "1", "true", "yes", "on",
                )
                _min_abs_alpha = float(os.getenv("AUTO_COIN_FACTOR_MIN_ABS_ALPHA", "0.005"))
                _blend = float(os.getenv("AUTO_COIN_FACTOR_BLEND", "0.50"))
            if float(total_score or 0) >= _min_market:
                _exp = None
                try:
                    if _factor_on:
                        from backend.services.factor_engine.exposure_service import (
                            factor_exposure_service,
                        )
                        _exp = factor_exposure_service.exposure(symbol_upper, "15m", 200)
                        if _exp:
                            _alpha = sum(float(e.get("expected_alpha", 0) or 0) for e in _exp)
                            _factor_match = max(-1.0, min(1.0, _alpha * 8.0))
                except Exception:
                    _factor_match = None
                # 仅当因子预期 alpha 显著时才融合（种子因子太弱时不压制选币）
                if _factor_match is not None and abs(_alpha) >= _min_abs_alpha:
                    _fm = (_factor_match + 1.0) / 2.0
                    _b = max(0.0, min(1.0, _blend))
                    _before = float(total_score or 0)
                    total_score = _b * _fm + (1.0 - _b) * total_score
                    logger.info(
                        "[AutoCoin] M4 factor_match %s alpha=%.5f match=%.3f "
                        "score %.3f→%.3f (blend=%.2f n_exp=%d)",
                        symbol_upper, _alpha, _factor_match, _before, total_score, _b,
                        len(_exp) if _exp else 0,
                    )
            scores["factor_match"] = _factor_match
            scores["factor_alpha"] = _alpha if _factor_match is not None else None

            # 阶段D:社交热度 bonus(在榜才加,最高 +15%,封顶 1.0)
            _social = social_map.get(symbol_upper, 0.0)
            if _social > 0:
                total_score = min(1.0, total_score * (1 + _social * 0.15))
            # 阶段D:新上市标记(供 prompt 与下游使用,评分不加成避免冷启动噪音)
            _is_new = symbol_upper in new_listings

            if total_score >= AUTO_COIN_MIN_SCORE:
                candidates.append(CandidateCoin(
                    symbol=symbol_upper,
                    score=total_score,
                    scores_detail=scores,
                    social_score=_social,
                    is_new_listing=_is_new,
                ))

        candidates.sort(key=lambda c: c.score, reverse=True)

        seen = set()
        unique: List[CandidateCoin] = []
        for c in candidates:
            if c.symbol not in seen:
                seen.add(c.symbol)
                unique.append(c)
        candidates = unique[:100]

        for i, c in enumerate(candidates):
            c.rank = i + 1

        _social_n = sum(1 for c in candidates if c.social_score > 0)
        _new_n = sum(1 for c in candidates if c.is_new_listing)
        logger.info(
            f"[AutoCoinSelector] Phase 1 done: {len(candidates)} candidates scored "
            f"(cutoff >= {AUTO_COIN_MIN_SCORE}) | social-trending={_social_n} new-listing={_new_n}"
        )
        return candidates

    @staticmethod
    def _mom_score_directional(price_change_24h, price_change_4h=None) -> float:
        """方向性动量评分（阶段B Fix 1）。

        涨为正分，跌为低分 —— 不再用 abs() 抹平方向。

        - 若有 4h 数据:24h 60% + 4h 40% 加权
        - 标准化:涨幅 15% → 1.0,0% → 0.5,跌幅 15% → 0.0
        - 一个 -15% 暴跌的币会得到接近 0（不再是接近 1.0）
        """
        pc24 = price_change_24h if price_change_24h is not None else 0.0
        if price_change_4h is not None:
            blended = pc24 * 0.6 + (price_change_4h or 0.0) * 0.4
        else:
            blended = pc24
        # 0.5（中性）+ blended / 0.30：+15% 映射 +0.5 → 1.0；-15% → 0.0
        return max(0.0, min(1.0, 0.5 + blended / 0.30))

    @staticmethod
    def _resolve_regime_weights(regime: Optional[str]) -> Dict[str, float]:
        """把 regime 字符串映射到权重表（阶段B Fix 2）。

        兼容多种 regime 命名:
          trend / trending / trend_high_vol / trend_low_vol → trending
          range / ranging                                    → ranging
          extreme / crisis / crash / squeeze / liquidation   → extreme
          其余/未知                                          → _DEFAULT_WEIGHTS（等权）
        """
        reg = (regime or "").lower()
        if not reg:
            return dict(_DEFAULT_WEIGHTS)
        if "trend" in reg:
            return dict(_SCORING_WEIGHTS["trending"])
        if "rang" in reg or "range" in reg:
            return dict(_SCORING_WEIGHTS["ranging"])
        if any(k in reg for k in ("extreme", "crisis", "crash", "squeeze", "liquidation", "volatile", "high_vol")):
            return dict(_SCORING_WEIGHTS["extreme"])
        return dict(_DEFAULT_WEIGHTS)

    @staticmethod
    def _fetch_current_regime() -> str:
        """从 SingleSourceCache 读最新 regime（阶段B Fix 2）。

        Cache 由 RegimeAgent 异步广播写入;读失败/未启动时返回 "unknown",
        _resolve_regime_weights 会退化到等权重。
        """
        try:
            from backend.services.cache.single_source import get_default_cache
            rl = get_default_cache().get_regime()
            if rl is None:
                return "unknown"
            return (getattr(rl, "regime", "") or "").lower() or "unknown"
        except Exception as e:
            logger.debug(f"[AutoCoinSelector] regime 读取失败,退化等权重: {e}")
            return "unknown"

    @staticmethod
    def _compute_weighted_total(scores: Dict[str, float], weights: Dict[str, float]) -> float:
        """按权重表加权汇总五维分数（阶段B Fix 2）。

        对 scores 中缺失的维度按 weights 同样的键补 0；若 weights 缺失某维度，
        用剩余权重归一化。返回 0.0~1.0。
        """
        if not scores:
            return 0.0
        total_w = 0.0
        acc = 0.0
        for dim, w in weights.items():
            v = scores.get(dim, 0.0)
            acc += v * w
            total_w += w
        if total_w <= 0:
            # 退化到等权平均
            vals = [v for v in scores.values() if v is not None]
            return sum(vals) / len(vals) if vals else 0.0
        return max(0.0, min(1.0, acc / total_w))

    def _is_paper_session(self, db: Optional[Session] = None) -> bool:
        """当前会话是否 paper（用于灰度开关，不影响实盘）。"""
        cached = getattr(self, "_paper_mode_cache", None)
        if cached is not None:
            return bool(cached)
        try:
            factory_db = db
            close_after = False
            if factory_db is None:
                from backend.database.connection import SessionLocal
                factory_db = SessionLocal()
                close_after = True
            try:
                session = factory_db.query(self._get_session_model()).filter(
                    self._get_session_model().session_id == self.session_id
                ).first()
                mode = str(getattr(session, "trading_mode", "") or "").lower()
                self._paper_mode_cache = mode == "paper"
                return bool(self._paper_mode_cache)
            except Exception as inner:
                try:
                    factory_db.rollback()
                except Exception:
                    pass
                raise inner
            finally:
                if close_after:
                    factory_db.close()
        except Exception as e:
            logger.debug(f"[AutoCoinSelector] paper detect fail: {e}")
            # 探测失败时：若全局 Paper 轮换开着，宁可按 paper 放宽，避免选币假死
            try:
                from backend.config.settings import PAPER_AUTO_COIN_ROTATE
                return bool(PAPER_AUTO_COIN_ROTATE)
            except Exception:
                return False

    def _score_v3_enabled(self, db: Optional[Session] = None) -> bool:
        try:
            from backend.config.settings import (
                AUTO_COIN_SCORE_V3_ENABLED,
                PAPER_AUTO_COIN_SCORE_V3,
            )
            if bool(AUTO_COIN_SCORE_V3_ENABLED):
                return True
            # Paper 默认灰度开 V3
            if bool(PAPER_AUTO_COIN_SCORE_V3) and self._is_paper_session(db):
                return True
            return False
        except Exception:
            return os.getenv("AUTO_COIN_SCORE_V3_ENABLED", "false").lower() in (
                "1", "true", "yes", "on",
            )

    @staticmethod
    def _candidate_replace_score(c: "CandidateCoin") -> float:
        """替换比较分：禁止用 score*confidence（AI 降频直批时≈score²，几乎永远换不进）。"""
        s = float(c.score or 0.0)
        conf = float(c.ai_confidence or 0.0)
        if s <= 0 and conf <= 0:
            return 0.0
        if s <= 0:
            return conf
        if conf <= 0:
            return s
        # 取偏高侧，避免双低乘数压死候选
        return max(s, conf) * 0.5 + min(s, conf) * 0.5

    @staticmethod
    def _flow_score_from_onchain(onchain: Dict[str, Any], price_change_1h: Optional[float] = None) -> Optional[float]:
        """从 OI/CVD 缓存算 flow_score(0~1)；无数据返回 None（不参与加权）。"""
        if not onchain:
            return None
        oi_pct = onchain.get("oi_change_pct")
        cvd = onchain.get("cvd_direction")
        if oi_pct is None and cvd is None:
            return None
        try:
            from backend.config.settings import AUTO_COIN_FLOW_OI_CLIP_PCT
            clip = float(AUTO_COIN_FLOW_OI_CLIP_PCT) or 8.0
        except Exception:
            clip = 8.0
        oi_comp = 0.0
        if oi_pct is not None:
            oi_comp = max(-1.0, min(1.0, float(oi_pct) / clip))
        cvd_comp = 0.0
        if cvd is not None:
            try:
                cvd_comp = max(-1.0, min(1.0, float(cvd)))
            except (TypeError, ValueError):
                cvd_comp = 1.0 if str(cvd).lower() in ("buy", "positive", "long") else (
                    -1.0 if str(cvd).lower() in ("sell", "negative", "short") else 0.0
                )
        align = 0.0
        if price_change_1h is not None and oi_pct is not None:
            if (float(oi_pct) > 0 and float(price_change_1h) > 0) or (
                float(oi_pct) < 0 and float(price_change_1h) < 0
            ):
                align = 0.15
        flow_raw = 0.45 * oi_comp + 0.45 * cvd_comp + align
        return max(0.0, min(1.0, 0.5 + 0.5 * flow_raw))

    @staticmethod
    def _whale_score_from_onchain(onchain: Dict[str, Any]) -> Optional[float]:
        if not onchain or not onchain.get("whale_available"):
            return None
        direction = str(onchain.get("whale_net_direction") or "neutral").lower()
        conf = float(onchain.get("whale_confidence") or 0.5)
        if direction == "buy":
            d = 1.0
        elif direction == "sell":
            d = -1.0
        else:
            d = 0.0
        return max(0.0, min(1.0, 0.5 + 0.5 * d * max(0.0, min(1.0, conf))))

    @staticmethod
    def _news_score_from_payload(news: Optional[Dict[str, Any]]) -> Optional[float]:
        if not news or not news.get("available"):
            return None
        sentiment = float(news.get("sentiment") or 0.0)
        try:
            from backend.config.settings import AUTO_COIN_NEWS_HALF_LIFE_MIN
            half = float(AUTO_COIN_NEWS_HALF_LIFE_MIN) or 120.0
        except Exception:
            half = 120.0
        freshness = news.get("freshness_min")
        if freshness is None:
            decay = 1.0
        else:
            decay = 0.5 ** (float(freshness) / half)
        conf = 0.5
        tops = news.get("top_events") or []
        if tops:
            conf = float(tops[0].get("confidence") or 0.5)
        return max(0.0, min(1.0, 0.5 + 0.5 * sentiment * decay * max(conf, 0.3)))

    @staticmethod
    def _compose_v3_score(
        base_score: float,
        flow_score: Optional[float] = None,
        whale_score: Optional[float] = None,
        news_score: Optional[float] = None,
        sector_score: Optional[float] = None,
        weights: Optional[Dict[str, float]] = None,
    ) -> Tuple[float, Dict[str, Any]]:
        """V3 综合分：缺维权重重新归一化到可用维。

        S2-9：weights 参数可覆盖静态权重 —— IC 加权启用且样本充足时，
        ``_apply_v3_rescore`` 把因子 IC 归一化权重传进来（负 IC 因子已弃用）。
        """
        if weights is None:
            try:
                from backend.config.settings import (
                    AUTO_COIN_W_BASE,
                    AUTO_COIN_W_FLOW,
                    AUTO_COIN_W_NEWS,
                    AUTO_COIN_W_SECTOR,
                    AUTO_COIN_W_WHALE,
                )
                weights = {
                    "base": float(AUTO_COIN_W_BASE),
                    "flow": float(AUTO_COIN_W_FLOW),
                    "whale": float(AUTO_COIN_W_WHALE),
                    "news": float(AUTO_COIN_W_NEWS),
                    "sector": float(AUTO_COIN_W_SECTOR),
                }
            except Exception:
                weights = {"base": 0.55, "flow": 0.20, "whale": 0.10, "news": 0.10, "sector": 0.05}

        parts: Dict[str, Optional[float]] = {
            "base": max(0.0, min(1.0, float(base_score or 0.0))),
            "flow": flow_score,
            "whale": whale_score,
            "news": news_score,
            "sector": sector_score,
        }
        acc = 0.0
        tw = 0.0
        used: Dict[str, float] = {}
        for k, v in parts.items():
            if v is None:
                continue
            w = weights.get(k, 0.0)
            if w <= 0:
                continue
            acc += float(v) * w
            tw += w
            used[k] = float(v)
        composite = (acc / tw) if tw > 0 else float(base_score or 0.0)
        composite = max(0.0, min(1.0, composite))
        return composite, {"parts": used, "weights_sum": tw}

    def _apply_v3_rescore(self, candidates: List[CandidateCoin], db: Optional[Session] = None) -> List[CandidateCoin]:
        """Enrich 后按 V3 重算综合分；强利空硬门剔除。默认开关关闭时原样返回。

        S2-9：IC 加权 —— 样本充足时用因子 IC 归一化权重覆盖静态 AUTO_COIN_W_*，
        并把 ic_meta（各因子 IC / 样本数 / 诊断）写入 scores_detail，供决策链路视图展示。
        """
        if not self._score_v3_enabled() or not candidates:
            return candidates

        ic_weights: Optional[Dict[str, float]] = None
        ic_meta: Optional[Dict[str, Any]] = None
        try:
            if db is not None:
                from backend.services.coin_rank.ic_weights import get_ic_weights
                ic_res = get_ic_weights(db)
                if ic_res.enabled:
                    ic_weights = ic_res.weights
                    ic_meta = {
                        "ics": ic_res.ics,
                        "n_samples": ic_res.n_samples,
                        "note": ic_res.note,
                    }
        except Exception as e:
            logger.debug(f"[AutoCoinSelector] IC 权重读取失败,回退静态权重: {e}")

        kept: List[CandidateCoin] = []
        blocked = 0
        for c in candidates:
            base = float(c.score or 0.0)
            onchain = c.onchain_data or {}
            news_payload = None
            if onchain.get("news_available"):
                news_payload = {
                    "available": True,
                    "sentiment": c.news_sentiment if c.news_sentiment is not None else 0.0,
                    "freshness_min": onchain.get("news_freshness_min"),
                    "top_events": onchain.get("top_events") or [],
                }
            # 强利空硬门
            try:
                sent = float(c.news_sentiment) if c.news_sentiment is not None else 0.0
                tops = onchain.get("top_events") or []
                strength = max([int(t.get("strength") or 0) for t in tops], default=0)
                freshness = onchain.get("news_freshness_min")
                if (
                    onchain.get("news_available")
                    and sent <= -0.6
                    and strength >= 4
                    and (freshness is None or float(freshness) <= 180)
                ):
                    blocked += 1
                    continue
            except Exception:
                pass

            flow_s = self._flow_score_from_onchain(onchain, c.price_change_24h)
            whale_s = self._whale_score_from_onchain(onchain)
            news_s = self._news_score_from_payload(news_payload)
            sector_s = None
            try:
                from backend.config.settings import AUTO_COIN_SECTOR_SIGNAL_ENABLED
                if AUTO_COIN_SECTOR_SIGNAL_ENABLED:
                    from backend.services.auto_coin_sector_signal import sector_rs_score
                    sector_s = sector_rs_score(c.symbol)
            except Exception:
                sector_s = None

            composite, meta = self._compose_v3_score(base, flow_s, whale_s, news_s, sector_s, weights=ic_weights)
            c.scores_detail = dict(c.scores_detail or {})
            c.scores_detail["base_score"] = base
            c.scores_detail["flow_score"] = flow_s
            c.scores_detail["whale_score"] = whale_s
            c.scores_detail["news_score"] = news_s
            c.scores_detail["sector_rs_score"] = sector_s
            c.scores_detail["v3_meta"] = meta
            if ic_meta:
                c.scores_detail["ic_meta"] = ic_meta
            c.score = composite
            kept.append(c)

        kept.sort(key=lambda x: x.score, reverse=True)
        for i, c in enumerate(kept):
            c.rank = i + 1
        if blocked:
            logger.info(f"[AutoCoinSelector] V3 hard-gate blocked {blocked} symbols (strong negative news)")
        logger.info(f"[AutoCoinSelector] V3 rescore applied: {len(kept)} candidates kept")
        return kept

    def _compute_price_change_4h(self, symbol: str, exchange: str) -> Optional[float]:
        """从 4h K 线计算近 4h 价格变化率（用于方向性动量）。

        没有 4h 数据时返回 None —— 动量评分退化为只看 24h。
        """
        try:
            from backend.services.kline_data_service import kline_service
            raw = kline_service.get_klines_from_db(symbol, "4h", count=10)
            if not raw or len(raw) < 2:
                return None
            first = raw[-6] if len(raw) >= 6 else raw[0]
            last = raw[-1]
            o = float(first.get("open", 0) or 0)
            c = float(last.get("close", 0) or 0)
            if o <= 0 or c <= 0:
                return None
            return (c - o) / o
        except Exception:
            return None

    def _multi_dimension_score(
        self,
        symbol: str,
        exchange: str,
        regime: Optional[str] = None,
    ) -> Dict[str, float]:
        """
        多维度评分体系，每项 0.0-1.0，专注短线+中线。

        阶段B 重构（2026-07-23）:
          - Fix 1 方向性动量:涨为高分,跌为低分(原 abs 抹方向)
          - Fix 2 regime 自适应权重(可选):scan_candidates 在调用前
            从 SingleSourceCache 取 regime 传入；默认等权重
          - Fix 3 数据缺失默认 0.3(原 0.5 恰好过 cutoff)

        维度:
          - vol_score: 交易量评分（24h交易量，短线流动性）
          - trend_score: 趋势评分（短线1h MA7/MA25 + 中线4h MA12/MA25，不含长线）
          - mom_score: 动量评分（方向性:24h 60% + 4h 40%）
          - vola_score: 波动率评分（适中波动加分，过高/过低扣分）
          - fund_score: 资金费率评分（正费率扣分，负费率加分）
        """
        # Fix 3:数据缺失时各维度默认 0.3(低于 0.5 cutoff)
        scores = {
            "vol_score": _MISSING_DEFAULT,
            "trend_score": _MISSING_DEFAULT,
            "mom_score": _MISSING_DEFAULT,
            "vola_score": _MISSING_DEFAULT,
            "fund_score": _MISSING_DEFAULT,
        }

        try:
            market_data = self._fetch_market_snapshot(symbol, exchange)
            if market_data:
                if market_data.get("volume_24h"):
                    scores["vol_score"] = round(min(market_data["volume_24h"] / 5_000_000, 1.0), 3) if market_data["volume_24h"] > 0 else 0.1
                # Fix 1:方向性动量 —— 优先用快照里的 price_change_4h,
                # 否则即时从 4h K 线推算
                pc24 = market_data.get("price_change_24h")
                pc4 = market_data.get("price_change_4h")
                if pc4 is None:
                    pc4 = self._compute_price_change_4h(symbol, exchange)
                if pc24 is not None or pc4 is not None:
                    scores["mom_score"] = round(
                        self._mom_score_directional(pc24, pc4), 3
                    )
                if market_data.get("funding_rate") is not None:
                    fr = market_data["funding_rate"]
                    scores["fund_score"] = round(max(0.0, min(1.0, 0.5 - fr * 100)), 3) if fr < 0 else round(max(0.0, 0.5 - fr * 50), 3)
                if market_data.get("volatility_24h"):
                    vol = market_data["volatility_24h"]
                    scores["vola_score"] = round(1.0 - abs(vol - 0.04) / 0.06, 3)
                    scores["vola_score"] = max(0.1, min(1.0, scores["vola_score"]))
        except Exception as e:
            logger.debug(f"[AutoCoinSelector] Score error for {symbol}: {e}")

        try:
            trend = self._assess_trend(symbol, exchange)
            scores["trend_score"] = round(trend, 3)
        except Exception:
            pass

        return scores

    # ── 阶段 2: 数据丰富 ──────────────────────────────────────────────

    def enrich_candidates(self, db: Session, candidates: List[CandidateCoin]) -> List[CandidateCoin]:
        """阶段 2: 获取市场数据、链上数据、新闻面"""
        if not candidates:
            return candidates

        exchange = self.resolve_exchange(db)
        top_n = candidates[:30]
        logger.info(f"[AutoCoinSelector] Phase 2: Enriching {len(top_n)} top candidates...")
        news_hit = 0
        whale_hit = 0

        for candidate in top_n:
            try:
                market_data = self._fetch_market_snapshot(candidate.symbol, exchange)
                if market_data:
                    candidate.volume_24h = market_data.get("volume_24h")
                    candidate.volume_change_24h = market_data.get("volume_change_24h")
                    candidate.price = market_data.get("price")
                    candidate.price_change_24h = market_data.get("price_change_24h")
                    candidate.funding_rate = market_data.get("funding_rate")
                    candidate.open_interest = market_data.get("open_interest")
                    candidate.volatility_24h = market_data.get("volatility_24h")
            except Exception as e:
                logger.debug(f"[AutoCoinSelector] Enrich error for {candidate.symbol}: {e}")

            try:
                candidate.onchain_data = self._fetch_onchain_data(candidate.symbol) or {}
                if candidate.onchain_data.get("whale_available") or (
                    candidate.onchain_data.get("whale_net_direction")
                    and candidate.onchain_data.get("whale_net_direction") != "neutral"
                ):
                    whale_hit += 1
            except Exception:
                candidate.onchain_data = {"whale_available": False}

            try:
                news_data = self._fetch_news(candidate.symbol)
                if news_data:
                    # 主字段：仅在 available=True 时写入数值情绪；禁止涨跌伪情绪冒充新闻
                    if news_data.get("available"):
                        candidate.news_sentiment = news_data.get("sentiment")
                        candidate.social_volume = news_data.get("social_volume")
                        news_hit += 1
                    else:
                        candidate.news_sentiment = None
                        candidate.social_volume = news_data.get("social_volume") or 0
                    candidate.onchain_data = candidate.onchain_data or {}
                    candidate.onchain_data["news_available"] = bool(news_data.get("available"))
                    candidate.onchain_data["news_label"] = news_data.get("sentiment_label")
                    candidate.onchain_data["news_freshness_min"] = news_data.get("freshness_min")
                    candidate.onchain_data["top_events"] = news_data.get("top_events") or []
            except Exception:
                pass

        logger.info(
            f"[AutoCoinSelector] Phase 2 done: enriched {len(top_n)} candidates "
            f"| news_hit={news_hit} whale_hit={whale_hit}"
        )
        # M1：Enrich 后可选 V3 重算（全局开或 Paper 灰度开）
        if self._score_v3_enabled(db):
            rescored = self._apply_v3_rescore(top_n, db)
            # 用重算结果替换 candidates 前缀，保持其余顺序
            tail = candidates[len(top_n):]
            return rescored + tail
        return candidates

    # ── 阶段 3: AI 深度审核 ──────────────────────────────────────────

    async def ai_review(self, db: Session, candidates: List[CandidateCoin]) -> List[CandidateCoin]:
        """
        阶段 3: AI 深度审核候选币种。

        对 TOP 15 候选（已在阶段 1+2 评分和丰富）逐一进行 AI 审核，
        整合:
          - 算法评分
          - 市场数据（价格、量、资金费率、OI）
          - 链上数据（巨鲸动向、交易所流入/流出）
          - 新闻/社交媒体情绪
        最终决定是否批准加入交易。
        """
        top_candidates = candidates[:15]
        if not top_candidates:
            return candidates

        # V5 M4: AI 审核降频 — 默认 4 小时一次完整 LLM 审核，
        # 其余周期用算法评分直批（省出的 LLM 预算给决策核心）
        import os as _os
        import time as _time
        # 阶段C：3600→1800，与 AUTO_COIN_SCAN_INTERVAL 对齐，让三层框架更频繁生效
        _review_interval = float(_os.getenv("AUTO_COIN_AI_REVIEW_INTERVAL_SEC", "1800"))
        _last = getattr(AutoCoinSelector, "_last_ai_review_ts", 0.0)
        _now = _time.time()
        if _now - _last < _review_interval:
            logger.info(
                "[AutoCoinSelector] Phase 3: AI review throttled "
                f"({int(_now - _last)}s < {int(_review_interval)}s), score-only approval"
            )
            for c in top_candidates:
                if c.score >= AUTO_COIN_MIN_SCORE and c.score >= AUTO_COIN_MIN_AI_CONFIDENCE:
                    c.ai_approved = True
                    c.ai_reason = "Score auto-approve (AI throttled, V5 M4)"
                    c.ai_confidence = c.score
            return candidates
        AutoCoinSelector._last_ai_review_ts = _now

        exchange = self.resolve_exchange(db)
        logger.info(f"[AutoCoinSelector] Phase 3: AI reviewing {len(top_candidates)} candidates...")

        try:
            from backend.services.llm_config_service import get_llm_config_for_usage
            llm_cfg = get_llm_config_for_usage("coin_select", account_id=self.account_id, tier="deep")
            api_key = llm_cfg.api_key if llm_cfg else None
            if not api_key:
                _paper = self._is_paper_session(db)
                if not _paper:
                    logger.warning(
                        "[AutoCoinSelector] Live 无 API key，拒绝新注入（degraded 不可伪装 AI）"
                    )
                    for c in top_candidates:
                        c.ai_approved = False
                        c.ai_reason = "degraded:no_llm_live_block"
                        c.ai_confidence = 0.0
                    self._last_degraded = "no_llm_live_block"
                    return candidates
                logger.warning("[AutoCoinSelector] Paper 无 API key，score-only（degraded=score_only）")
                for c in top_candidates:
                    if c.score >= AUTO_COIN_MIN_SCORE:
                        c.ai_approved = True
                        c.ai_reason = "Score auto-approve (degraded=score_only)"
                        c.ai_confidence = c.score
                        if (c.scores_detail or {}).get("force_test_position"):
                            c.test_position = True
                self._last_degraded = "score_only"
                return candidates
        except Exception:
            api_key = None
            llm_cfg = None

        if not api_key:
            _paper = self._is_paper_session(db)
            if not _paper:
                logger.warning("[AutoCoinSelector] Live 无 API key，拒绝新注入")
                for c in top_candidates:
                    c.ai_approved = False
                    c.ai_reason = "degraded:no_llm_live_block"
                self._last_degraded = "no_llm_live_block"
                return candidates
            logger.warning("[AutoCoinSelector] No API key，Paper score-only")
            for c in top_candidates:
                if c.score >= AUTO_COIN_MIN_SCORE and c.score >= AUTO_COIN_MIN_AI_CONFIDENCE:
                    c.ai_approved = True
                    c.ai_reason = "Score auto-approve (degraded=score_only)"
                    c.ai_confidence = c.score
            self._last_degraded = "score_only"
            return candidates

        approved_count = 0
        _max_review = int(_os.getenv("AUTO_COIN_AI_REVIEW_MAX_CANDIDATES", "10"))  # 5→10
        # M4：因子匹配 — soft 模式只降权不直淘；hard 保留高分直放/低分直淘
        _soft_factor = _os.getenv("AUTO_COIN_SOFT_FACTOR", "false").lower() in (
            "1", "true", "yes", "on",
        )
        _llm_high = float(_os.getenv("AUTO_COIN_LLM_HIGH", "0.75"))
        _llm_low = float(_os.getenv("AUTO_COIN_LLM_LOW", "0.45"))
        _review_pool = []
        for _c in top_candidates:
            _fm = (_c.scores_detail or {}).get("factor_match")
            if _fm is None:
                _review_pool.append(_c)
                continue
            if _soft_factor:
                # 软门槛：极低分降权但仍送 AI；高分可略提置信度提示
                if _fm <= _llm_low:
                    _c.score = float(_c.score or 0) * 0.85
                    if isinstance(_c.scores_detail, dict):
                        _c.scores_detail["factor_soft_penalty"] = True
                _review_pool.append(_c)
                continue
            if _fm >= _llm_high:
                _c.ai_approved = True
                _c.ai_reason = "factor_match_high"
                _c.ai_confidence = max(float(_c.score or 0), 0.80)
                logger.info(f"[AutoCoinSelector] 因子高分直放: {_c.symbol} fm={_fm:.2f}")
            elif _fm <= _llm_low:
                _c.ai_approved = False
                _c.ai_reason = "factor_match_low"
                logger.info(f"[AutoCoinSelector] 因子低分直淘: {_c.symbol} fm={_fm:.2f}")
            else:
                _review_pool.append(_c)
        _paper = self._is_paper_session(db)
        _pool_cap = self._pool_limit(db)
        for candidate in _review_pool[:_max_review]:
            if approved_count >= _pool_cap + 3:
                break

            try:
                # 选币经验闭环：历史战绩回填，避免重复选中曾巨亏的币
                _track = self._get_symbol_track_record(db, candidate.symbol)
                prompt = self._build_ai_review_prompt(candidate, exchange, track_record=_track)
                response = await self._call_ai(llm_cfg, prompt)
                _reason = str(response.get("reason") or "")
                _ai_broken = (
                    _reason.startswith("Error:")
                    or _reason in ("Parse error", "Empty AI content")
                    or ("Empty AI" in _reason)
                )
                # LLM 空响应/报错：Paper 按分数直批，避免整轮 0 approve 假死
                if _ai_broken:
                    _use_soft_fb = bool(_paper)
                    _fb_thr = AUTO_COIN_MIN_SCORE if _use_soft_fb else max(0.80, AUTO_COIN_MIN_SCORE + 0.15)
                    if float(candidate.score or 0) >= _fb_thr:
                        candidate.ai_approved = True
                        candidate.ai_reason = f"Score fallback (AI error: {_reason[:40]})"
                        candidate.ai_confidence = max(
                            float(candidate.score or 0) * (0.9 if _use_soft_fb else 0.85),
                            AUTO_COIN_MIN_AI_CONFIDENCE,
                        )
                        candidate.test_position = bool(_use_soft_fb)
                        candidate.ai_layer = "D" if _use_soft_fb else ""
                        approved_count += 1
                        logger.info(
                            f"[AutoCoinSelector] Score fallback: {candidate.symbol} "
                            f"score={candidate.score:.3f} soft={_use_soft_fb}"
                        )
                    else:
                        candidate.ai_approved = False
                        candidate.ai_reason = f"AI error, score too low ({candidate.score:.3f})"
                    continue

                ai_conf = float(response.get("confidence", 0) or 0)
                if response.get("approved") and ai_conf >= AUTO_COIN_MIN_AI_CONFIDENCE:
                    candidate.ai_approved = True
                    candidate.ai_reason = response.get("reason", "")
                    candidate.ai_confidence = ai_conf
                    # 阶段C：回填三层框架字段
                    candidate.ai_layer = str(response.get("layer", "") or "")
                    candidate.test_position = bool(response.get("test_position", False))
                    approved_count += 1
                    _tag = " [试仓50%]" if candidate.test_position else ""
                    logger.info(f"[AutoCoinSelector] AI approved: {candidate.symbol} ({candidate.ai_confidence:.0%} LAYER {candidate.ai_layer or '-'}){_tag} - {candidate.ai_reason[:60]}")
                else:
                    candidate.ai_approved = False
                    if response.get("approved"):
                        candidate.ai_reason = (
                            f"置信度{ai_conf:.0%}低于严选门槛{AUTO_COIN_MIN_AI_CONFIDENCE:.0%}"
                        )
                    else:
                        candidate.ai_reason = response.get("reason", "AI declined")
                    logger.debug(f"[AutoCoinSelector] AI declined: {candidate.symbol} - {candidate.ai_reason[:60]}")
            except Exception as e:
                logger.warning(f"[AutoCoinSelector] AI review failed for {candidate.symbol}: {e}")
                _fb_thr = AUTO_COIN_MIN_SCORE if _paper else max(0.80, AUTO_COIN_MIN_SCORE + 0.15)
                if candidate.score >= _fb_thr:
                    candidate.ai_approved = True
                    candidate.ai_reason = "Score fallback (AI error)"
                    candidate.ai_confidence = max(
                        float(candidate.score or 0) * (0.9 if _paper else 0.85),
                        AUTO_COIN_MIN_AI_CONFIDENCE,
                    )
                    candidate.test_position = bool(_paper)
                    approved_count += 1

        approved = [
            c for c in candidates
            if c.ai_approved
            and float(c.ai_confidence or 0) >= AUTO_COIN_MIN_AI_CONFIDENCE
            and float(c.score or 0) >= AUTO_COIN_MIN_SCORE
        ]
        logger.info(f"[AutoCoinSelector] Phase 3 done: {len(approved)} approved by AI (strict)")
        return candidates

    def _get_symbol_track_record(self, db: Session, symbol: str) -> str:
        """选币经验闭环：汇总该币历史交易战绩 + 最近一次淘汰原因，
        回填给 AI Review prompt，避免重复选中曾巨亏的币（如 FET 单笔 -38707）。"""
        lines: List[str] = []
        sym = symbol.upper()
        try:
            from sqlalchemy import func as _f
            from backend.database.models import PaperOrder
            row = db.query(
                _f.count(PaperOrder.id),
                _f.coalesce(_f.sum(PaperOrder.pnl), 0.0),
                _f.coalesce(_f.min(PaperOrder.pnl), 0.0),
            ).filter(
                PaperOrder.symbol == sym,
                PaperOrder.pnl.isnot(None),
            ).first()
            n = int(row[0] or 0)
            if n > 0:
                total_pnl = float(row[1] or 0)
                worst = float(row[2] or 0)
                win_n = db.query(_f.count(PaperOrder.id)).filter(
                    PaperOrder.symbol == sym,
                    PaperOrder.pnl > 0,
                ).scalar() or 0
                lines.append(
                    f"  历史成交 {n} 笔, 累计盈亏 {total_pnl:+,.0f}$, "
                    f"胜率 {win_n / n * 100:.0f}%, 最大单笔亏损 {worst:,.0f}$"
                )
                if worst < -5000:
                    lines.append(f"  ⚠️ 该币曾出现单笔巨亏 {worst:,.0f}$，需重点评估波动风险")
        except Exception as e:
            logger.debug(f"[AutoCoinSelector] track record query skip: {e}")
        try:
            from backend.database.models import AutoCoinSelection
            last_removed = db.query(AutoCoinSelection).filter(
                AutoCoinSelection.symbol == sym,
                AutoCoinSelection.action == "removed",
            ).order_by(AutoCoinSelection.created_at.desc()).first()
            if last_removed is not None and last_removed.removal_reason:
                _ts = last_removed.created_at.strftime("%m-%d") if last_removed.created_at else "?"
                lines.append(f"  上次淘汰({_ts}): {str(last_removed.removal_reason)[:100]}")
        except Exception as e:
            logger.debug(f"[AutoCoinSelector] removal history query skip: {e}")
        return "\n".join(lines)

    def _build_ai_review_prompt(self, candidate: CandidateCoin, exchange: str,
                                track_record: str = "") -> str:
        """阶段C：三层渐进式 AI 审核 prompt。

        替代旧的「8 条平铺准则 + 全或无批准」结构。改为 LAYER 1/2/3
        渐进决策，让 LLM 有明确的优先级与「试仓」中间态，避免无差别保守拒绝。
        """
        scoring = candidate.scores_detail
        deltas = self._compute_onchain_deltas(candidate)

        prompt_parts = [
            "你是专业加密货币交易员，负责审核自动选币系统的候选币种。",
            "采用三层渐进式评估框架（LAYER 1 → LAYER 2 → LAYER 3），逐层判定：",
            "",
            "LAYER 1 — 高置信度通过（满足任一即可全仓位批准）:",
            "  A) 算法综合分≥0.70 且 趋势分明（上涨趋势） → 全仓位通过",
            "  B) 过去24h有重大利好（新闻情绪>0.5） 且 链上数据积极 → 事件驱动通过",
            "  C) 该币曾在系统中产生正收益且无当前风险信号 → 经验验证通过",
            "",
            "LAYER 2 — 有条件通过（小仓位测试）:",
            "  D) 综合分 0.55-0.69, 无排除因子 → 50%标准仓位测试",
            "  E) 属于当前热门概念(AI/Meme/RWA/DePIN)且 动量>0.6 → 50%仓位热点跟踪",
            "",
            "LAYER 3 — 排除条件（满足任一即拒绝）:",
            "  F) 24h暴跌>15% 或 曾被系统巨亏 → 直接拒绝",
            "  G) 资金费率极端(>0.1%且无利好) 且 流动性极低 → 暂缓",
            "",
            "判定原则：优先匹配 LAYER 1（全仓）→ 不满足再尝试 LAYER 2（试仓）",
            "→ 命中 LAYER 3 任一即拒绝。不要因为「不够完美」就拒绝，宁可试仓。",
            "",
            "【交易所】" + exchange,
            "【候选币种】" + candidate.symbol,
            f"【综合评分】{candidate.score:.3f} (排名 #{candidate.rank})",
            "",
            "【算法多维度评分】",
            f"  交易量评分: {scoring.get('vol_score', 0):.3f}",
            f"  趋势评分:   {scoring.get('trend_score', 0):.3f}",
            f"  动量评分:   {scoring.get('mom_score', 0):.3f}",
            f"  波动率评分: {scoring.get('vola_score', 0):.3f}",
            f"  资金费率评分: {scoring.get('fund_score', 0):.3f}",
            "",
            "【市场数据】",
        ]

        if candidate.volume_24h:
            prompt_parts.append(f"  24h交易量: ${candidate.volume_24h:,.0f}")
        if candidate.volume_change_24h is not None:
            prompt_parts.append(f"  交易量变化: {candidate.volume_change_24h:+.1%}")
        if candidate.price is not None:
            prompt_parts.append(f"  当前价格: ${candidate.price:.4f}")
        if candidate.price_change_24h is not None:
            prompt_parts.append(f"  24h价格变化: {candidate.price_change_24h:+.2%}")
        if candidate.funding_rate is not None:
            prompt_parts.append(f"  资金费率: {candidate.funding_rate:+.4%}")
        if candidate.open_interest:
            prompt_parts.append(f"  未平仓合约: ${candidate.open_interest:,.0f}")

        if candidate.onchain_data:
            prompt_parts.append("")
            prompt_parts.append("【链上数据】")
            for k, v in candidate.onchain_data.items():
                if isinstance(v, (int, float, str)):
                    prompt_parts.append(f"  {k}: {v}")
        if deltas:
            prompt_parts.append(f"  近期变化: " + ", ".join(f"{k}: {v:+.2%}" for k, v in deltas.items()))

        if candidate.news_sentiment is not None:
            prompt_parts.append("")
            prompt_parts.append(f"【新闻情绪】{candidate.news_sentiment:+.2f}")
        if candidate.social_volume is not None:
            prompt_parts.append(f"【社交提及】{candidate.social_volume} 次")

        # 阶段D:社交热度(CoinGecko Trending) + 新上市检测
        if candidate.social_score > 0 or candidate.is_new_listing:
            prompt_parts.append("")
            prompt_parts.append("【社交热度与新上市信号】")
            if candidate.social_score > 0:
                # social_score: 1.0=第1名, 0.95=第2名, ... 反推排名供 LLM 直观判断
                _rank = max(1, round((1.0 - candidate.social_score) / 0.05) + 1)
                prompt_parts.append(
                    f"  CoinGecko 热门榜:排名第{_rank}位 (热度分 {candidate.social_score:.2f}/1.0) "
                    f"—— 零售/社交 buzz 强,可能酝酿短线热点"
                )
            else:
                prompt_parts.append(f"  {candidate.symbol}: 不在 CoinGecko 热门榜")
            if candidate.is_new_listing:
                prompt_parts.append(
                    f"  {candidate.symbol}: 过去一个扫描周期内新上市交易对 "
                    f"—— 新币常有高初始波动/机会,但也伴随流动性/数据不足风险,优先试仓"
                )
            else:
                prompt_parts.append(f"  {candidate.symbol}: 已上市(非新币)")

        if track_record:
            prompt_parts.append("")
            prompt_parts.append("【本系统对该币的历史交易战绩】")
            prompt_parts.append(track_record)

        prompt_parts.extend([
            "",
            "【交易时间框架】本系统专注短线(数小时~2天)和中线(2~7天)，不做长线持仓。",
            "长期趋势(>7天)仅供参考背景，不作为开仓决策依据。",
            "注意：不要因为「长期趋势向好」而批准一个短线信号不佳的币种。",
            "",
            "请严格按三层框架判定该币种，并输出 JSON：",
            '{"approved": true/false, '
            '"layer": "A/B/C/D/E/F/G", '
            '"test_position": true/false, '
            '"reason": "≤50字中文", '
            '"confidence": 0.0-1.0, '
            '"suggested_tier": "scalp"}',
            "字段说明:",
            "  - layer: 命中的层级字母（LAYER1=A/B/C, LAYER2=D/E, LAYER3=F/G）",
            "  - test_position: 仅 LAYER 2(D/E) 为 true，其余 false",
            "  - suggested_tier: 固定 scalp（自动选币只进短线 scalp）",
        ])

        return "\n".join(prompt_parts)

    # ── 阶段 4: 注入交易会话 ──────────────────────────────────────────

    def _training_blocks_auto_coin_inject(self, db: Session) -> Tuple[bool, str]:
        """训练期默认禁止「独立扫描」自动注入。

        统一规则：
        - 跟投平台看板（管理员已审）→ 永不拦
        - Paper 模拟盘 → 不拦（便于实验）
        - 其余 Live + 训练期开启 → 拦
        """
        try:
            # 看板跟投：唯一真相源，不走训练期封锁
            if getattr(self, "_board_sourced", False):
                return False, ""

            from backend.config.settings import TRAINING_PHASE_BLOCK_AUTO_COIN
            from backend.services.training_phase_service import is_active

            if not TRAINING_PHASE_BLOCK_AUTO_COIN or not is_active():
                return False, ""

            # Paper：会话或账户任一标记即可
            session = (
                db.query(self._get_session_model())
                .filter(self._get_session_model().session_id == self.session_id)
                .first()
            )
            mode = str(getattr(session, "trading_mode", "") or "").lower() if session else ""
            if mode == "paper":
                return False, ""
            try:
                from backend.database.models import Account

                acc = db.query(Account).filter(Account.id == int(self.account_id)).first()
                if acc:
                    am = str(getattr(acc, "trading_mode", "") or "").lower()
                    at = str(getattr(acc, "account_type", "") or "").upper()
                    if am == "paper" or at == "PAPER":
                        return False, ""
            except Exception:
                pass

            return True, (
                "训练期保护中：仅允许 BTC/ETH/SOL/BNB/ASTER 等核心训练币，"
                "自动注入已暂停（可手动添加交易对；VIP 看板跟投不受此限）"
            )
        except Exception:
            return False, ""

    def _candidates_from_platform_board(self, db: Session) -> List[CandidateCoin]:
        """从管理员 VIP 共用短线看板取候选（已含 AI verdict），会话只负责注入/换币。

        统一规则：看板 approve = 可注入；不再用独立扫描的 score/黑名单否决管理员结论。
        """
        from backend.database.models import CoinSelectCandidate
        from backend.services.auto_coin_policy import is_training_core_symbol

        now = datetime.now()
        # 会话固定交易对不进 AI 池、不占槽位
        fixed_syms: Set[str] = set()
        try:
            sess = (
                db.query(self._get_session_model())
                .filter(self._get_session_model().session_id == self.session_id)
                .first()
            )
            if sess:
                fixed_syms = {
                    str(s).upper().strip()
                    for s in (sess.symbols or [])
                    if s
                }
        except Exception:
            fixed_syms = set()

        rows = (
            db.query(CoinSelectCandidate)
            .filter(
                CoinSelectCandidate.listed.is_(True),
                CoinSelectCandidate.horizon == "scalp",
                CoinSelectCandidate.ai_verdict.in_(["approve", "watch"]),
            )
            .order_by(CoinSelectCandidate.confidence.desc().nullslast())
            .limit(40)
            .all()
        )
        out: List[CandidateCoin] = []
        seen: Set[str] = set()
        for r in rows:
            sym = str(r.symbol or "").upper().strip()
            if not sym or sym in seen:
                continue
            seen.add(sym)
            # 核心大币 / 会话固定币 → 留给固定表，不进 auto 池
            if is_training_core_symbol(sym) or sym in fixed_syms:
                continue
            ms = r.market_scores if isinstance(r.market_scores, dict) else {}
            if ms.get("degraded"):
                continue
            verdict = str(r.ai_verdict or "").lower()
            conf = float(r.confidence or 0.5)
            # 注入排序用 AI 信心，不用粗分 market score（粗分常 < 0.5 会误杀 approve）
            inject_score = max(conf, float(r.score or 0) or 0.0)
            if verdict == "approve":
                approved = True
                test_pos = False
                # 管理员看板结论：清冷却/黑名单，允许重新进池
                try:
                    self._pool.cooling.pop(sym, None)
                    self._pool.blacklist.pop(sym, None)
                except Exception:
                    pass
            else:
                # watch：VIP 观察列表也跟投（试仓）；门槛与看板展示对齐
                if conf < 0.30:
                    continue
                # 看板上的 watch 同样清冷却，否则槽位加大也补不进（实测 ZEC/KAITO 被冷却挡掉）
                try:
                    self._pool.cooling.pop(sym, None)
                    self._pool.blacklist.pop(sym, None)
                except Exception:
                    pass
                approved = True
                test_pos = True
            out.append(
                CandidateCoin(
                    symbol=sym,
                    score=inject_score,
                    rank=len(out) + 1,
                    ai_approved=approved,
                    ai_reason=str(r.ai_reason or "VIP短线看板")[:2000],
                    ai_confidence=conf,
                    test_position=test_pos,
                    scores_detail={
                        "source": "platform_board",
                        "verdict": verdict,
                        "gate": ms.get("gate"),
                        "trap_soft": ms.get("trap_soft"),
                        "market_score": ms.get("score") or r.score,
                    },
                )
            )
        logger.info(
            "[AutoCoinSelector] VIP看板跟投候选 n=%d → %s",
            len(out),
            [f"{c.symbol}:{c.scores_detail.get('verdict')}@{c.ai_confidence:.2f}" for c in out[:12]],
        )
        return out

    def inject_approved_symbols(self, db: Session, candidates: List[CandidateCoin]) -> List[str]:
        """
        阶段 4: 将 AI 审核通过的币种注入运行中的交易会话。

        V2 逻辑:
          - 有空位：直接注入直到满 5 个
          - 已满：对比候选 vs 最差现有币种的留存评分，满足替换条件则替换
          - 严格上限：最终 auto symbols <= AUTO_COIN_MAX_POOL_SIZE
        """
        from backend.services.full_auto_trading_service import FullAutoTradingService
        from backend.services.auto_coin_sectors import get_sector

        if self._cancel_requested:
            logger.info("[AutoCoinSelector] Phase 4: Cancel requested, skip injection")
            self._last_inject_block_reason = "扫描已取消"
            return []

        blocked, block_reason = self._training_blocks_auto_coin_inject(db)
        if blocked:
            self._last_inject_block_reason = block_reason
            logger.info(f"[AutoCoinSelector] Phase 4: {block_reason}")
            return []
        self._last_inject_block_reason = None

        # VIP 看板跟投：以 AI 信心为准；watch 门槛 0.30，不再用独立扫描的 0.50 卡死
        if getattr(self, "_board_sourced", False):
            min_score = 0.0
            min_conf = 0.30
        else:
            min_score = AUTO_COIN_MIN_SCORE
            min_conf = AUTO_COIN_MIN_AI_CONFIDENCE
        approved = [
            c for c in candidates
            if c.ai_approved
            and float(c.ai_confidence or 0) >= min_conf
            and float(c.score or 0) >= min_score
        ]
        approved.sort(key=lambda c: (c.ai_confidence, c.score), reverse=True)

        if not approved:
            logger.info("[AutoCoinSelector] Phase 4: No approved candidates to inject")
            return []

        # ── 数据就绪检查 ──
        # VIP 看板跟投：管理员已审，只校验「当前交易所可交易」；K 线允许注入后补齐。
        # （否则短线新币常因 DC stale 永远进不了池，统一跟投形同虚设）
        exchange = self.resolve_exchange(db)
        board_src = bool(getattr(self, "_board_sourced", False))
        _data_ready = []
        _data_rejected = []
        for c in approved:
            if board_src:
                try:
                    from backend.services.kline_sync_meta import list_catalog_symbols

                    ex = (exchange or "asterdex").strip().lower()
                    if ex == "aster":
                        ex = "asterdex"
                    catalog = list_catalog_symbols(ex) or []
                    if catalog and c.symbol.upper() not in set(catalog):
                        _data_rejected.append(c.symbol)
                        logger.warning(
                            "[AutoCoinSelector] VIP跟投拒绝 %s：不在 %s 可交易目录",
                            c.symbol,
                            ex,
                        )
                        continue
                except Exception as e:
                    logger.debug("[AutoCoinSelector] board catalog check: %s", e)
                _data_ready.append(c)
                continue
            _ready = self._preflight_data_check(c.symbol, exchange)
            if _ready:
                _data_ready.append(c)
            else:
                _data_rejected.append(c.symbol)

        if _data_rejected:
            logger.info(
                f"[AutoCoinSelector] Phase 4: 数据预检淘汰 {_data_rejected} "
                f"（{'VIP看板仅拦不可交易对' if board_src else 'K线/衍生品不足，等待下次扫描补齐后再选'}）"
            )
        approved = _data_ready
        if not approved:
            logger.info("[AutoCoinSelector] Phase 4: 所有候选数据未就绪，跳过本轮注入")
            return []

        # ── S2-9：LLM 组合决策（board 跟投与 legacy 扫描共用；未启用/失败回退规则路径）──
        # 候选池已按 (ai_confidence, score) 降序，LLM 在此之上做组合级取舍：
        # 避免高分同质币扎堆，兼顾因子 IC 正维度。
        try:
            from backend.config.settings import (
                AUTO_COIN_LLM_COMPOSE_ENABLED,
                AUTO_COIN_LLM_COMPOSE_MAX,
            )
            if AUTO_COIN_LLM_COMPOSE_ENABLED and len(approved) > 1:
                from backend.services.coin_rank.ic_weights import llm_compose, factor_vector
                pool = [
                    {
                        "symbol": c.symbol,
                        "score": round(float(c.score or 0), 3),
                        "confidence": c.ai_confidence,
                        "reason": c.ai_reason or "",
                        "factors": factor_vector(c.scores_detail or {}),
                    }
                    for c in approved
                ]
                picked = llm_compose(
                    pool,
                    self._llm_compose_caller(),
                    max_select=int(AUTO_COIN_LLM_COMPOSE_MAX),
                )
                if picked:
                    picked_set = set(picked)
                    before = len(approved)
                    approved = [c for c in approved if c.symbol.upper() in picked_set]
                    logger.info(
                        f"[AutoCoinSelector] LLM 组合决策: {before} -> {len(approved)} "
                        f"(picked={picked})"
                    )
                    if not approved:
                        return []
        except Exception as e:
            logger.debug(f"[AutoCoinSelector] LLM 组合跳过(回退规则路径): {e}")

        service = FullAutoTradingService.get_instance()

        session = db.query(self._get_session_model()).filter(
            self._get_session_model().session_id == self.session_id
        ).first()
        if not session:
            logger.warning(f"[AutoCoinSelector] Session {self.session_id} not found")
            return []

        session = self._sync_active_pool_from_db(db) or session
        existing_auto = set(s.upper() for s in (getattr(session, "auto_coin_symbols", None) or []))
        existing_auto |= set(self._pool.active.keys())
        now = datetime.now()
        pool_limit = self._pool_limit(db)
        try:
            self._pool.max_active = pool_limit
        except Exception:
            pass

        # ── VIP 看板：补因子快照（IC 反馈闭环样本源，v6 4.3）──
        # 看板候选 scores_detail 只有 source/verdict/gate/trap_soft/market_score，
        # 不含 IC 因子维度 → load_ic_samples 的 _extract_factor_values 提取为空
        # → 样本被过滤 → IC 权重永不转正。此处尽力补：
        #   base_score=审核分 + sector_rs_score(板块RS) + 五维K线分(vol/trend/mom/vola/fund)
        try:
            from backend.services.coin_rank.ic_weights import FACTOR_KEYS as _IC_FK
        except Exception:
            _IC_FK = ()
        for c in approved:
            sd = dict(c.scores_detail or {})
            if not any(k in sd for k in _IC_FK):
                sd["base_score"] = float(c.score or 0.0)
                try:
                    from backend.services.auto_coin_sector_signal import sector_rs_score as _srs

                    rs = _srs(c.symbol)
                    if rs is not None:
                        sd["sector_rs_score"] = float(rs)
                except Exception:
                    pass
                try:
                    dim = self._multi_dimension_score(c.symbol, exchange)
                    for k in ("vol_score", "trend_score", "mom_score", "vola_score", "fund_score"):
                        v = dim.get(k)
                        if v is not None:
                            sd[k] = float(v)
                except Exception:
                    pass
                c.scores_detail = sd

        # ── S2-9 移植：board 跟投路径注入前 IC 加权重排 + 组合相关去重 ──
        # （legacy 扫描路径已有同能力；此处让统一跟投路径也消费 V3/IC/去重）
        if board_src:
            if self._score_v3_enabled(db):
                rescored = self._apply_v3_rescore(approved, db)
                if rescored:
                    approved = rescored
            try:
                from backend.config.settings import AUTO_COIN_CORR_DEDUP_THRESHOLD
                from backend.services.coin_rank.ic_weights import dedup_by_correlation, factor_vector
                threshold = float(AUTO_COIN_CORR_DEDUP_THRESHOLD)
                if threshold > 0 and len(approved) > 1:
                    kept_syms = dedup_by_correlation(
                        [(c.symbol, factor_vector(c.scores_detail or {})) for c in approved],
                        threshold=threshold,
                    )
                    if kept_syms:
                        kept_set = set(kept_syms)
                        before_n = len(approved)
                        approved = [c for c in approved if c.symbol in kept_set]
                        logger.info(
                            f"[AutoCoinSelector] S2-9 board 相关去重: {before_n} -> {len(approved)}"
                        )
            except Exception as e:
                logger.debug(f"[AutoCoinSelector] board 相关性去重跳过: {e}")

        # ── VIP 看板：补满槽位（加大槽位能立刻多跟投；不因看板变薄而误删已有 AI 币）──
        if board_src:
            fixed_now = {
                str(s).upper()
                for s in (getattr(session, "symbols", None) or [])
                if s
            }
            board_ordered = [
                c.symbol.upper()
                for c in approved
                if c.symbol.upper() not in fixed_now
            ]
            # 看板明确 reject 的才踢出；未上榜的旧 AI 币先保留，用多余槽位补新币
            reject_syms: Set[str] = set()
            try:
                from backend.database.models import CoinSelectCandidate
                rej_rows = (
                    db.query(CoinSelectCandidate.symbol)
                    .filter(
                        CoinSelectCandidate.listed.is_(True),
                        CoinSelectCandidate.horizon == "scalp",
                        CoinSelectCandidate.ai_verdict == "reject",
                    )
                    .all()
                )
                reject_syms = {str(r[0]).upper() for r in rej_rows if r and r[0]}
            except Exception as e:
                logger.debug("[AutoCoinSelector] reject lookup skip: %s", e)

            keep_existing = [
                s for s in sorted(existing_auto)
                if s not in fixed_now and s not in reject_syms
            ]
            # 看板币优先，再保留旧池，直到槽位上限
            ordered: List[str] = []
            for s in board_ordered:
                if s not in ordered:
                    ordered.append(s)
            for s in keep_existing:
                if s not in ordered:
                    ordered.append(s)
            target = ordered[: max(1, pool_limit)]
            # S2-9 移植：board 路径同板块上限（含已有池，与 legacy 一致）
            try:
                from backend.config.settings import AUTO_COIN_SECTOR_SIGNAL_ENABLED
                if AUTO_COIN_SECTOR_SIGNAL_ENABLED:
                    from backend.services.auto_coin_sector_signal import enforce_max_per_sector
                    pooled = list(existing_auto) + ordered
                    allowed = set(enforce_max_per_sector(pooled))
                    target = [s for s in target if s in allowed]
            except Exception as e:
                logger.debug(f"[AutoCoinSelector] board sector cap skip: {e}")
            target_set = set(target)
            to_remove = sorted(
                (existing_auto - target_set)
                | (existing_auto & fixed_now)
                | (existing_auto & reject_syms)
            )
            to_add = [s for s in target if s not in existing_auto]
            logger.info(
                "[AutoCoinSelector] VIP补满槽位 limit=%s remove=%s add=%s target=%s board=%s",
                pool_limit,
                to_remove,
                to_add,
                target,
                board_ordered,
            )
            if to_remove:
                try:
                    service.remove_symbols(db, self.session_id, to_remove)
                except Exception as e:
                    logger.warning("[AutoCoinSelector] VIP对齐 remove: %s", e)
                for sym in to_remove:
                    self._pool.active.pop(sym, None)
                    self._auto_symbols.discard(sym)
                    self._add_cooling(sym, "short")
                    self._write_audit_record(
                        db, sym, "removed", removal_reason="VIP看板对齐移出"
                    )
            added: List[str] = []
            if to_add:
                result = service.add_symbols(
                    db, self.session_id, to_add, is_auto_coin=True
                )
                if result.get("success"):
                    added = list(result.get("added") or to_add)
                    by_sym = {c.symbol.upper(): c for c in approved}
                    for sym in added:
                        c = by_sym.get(sym) or CandidateCoin(
                            symbol=sym, ai_approved=True, ai_confidence=0.5, score=0.5
                        )
                        c.injected_at = now
                        c.session_id = self.session_id
                        self._pool.active[sym] = c
                        self._auto_symbols.add(sym)
                        self._evaluation_count[sym] = 0
                        self._write_audit_record(
                            db,
                            sym,
                            "injected",
                            scanner_score=c.score,
                            ai_confidence=c.ai_confidence,
                            ai_reason=c.ai_reason or "VIP短线看板",
                            factor_snapshot=c.scores_detail,
                        )
                else:
                    logger.error(
                        "[AutoCoinSelector] VIP对齐注入失败: %s", result.get("error")
                    )
            # 已在池且仍在看板上的：刷新理由/信心
            for c in approved:
                sym = c.symbol.upper()
                if sym in self._pool.active and sym not in added:
                    old = self._pool.active[sym]
                    old.ai_confidence = c.ai_confidence
                    old.ai_reason = c.ai_reason
                    old.score = c.score
            self._save_injected()
            try:
                session.auto_coin_symbols = sorted(self._pool.active.keys())
                db.commit()
            except Exception:
                pass
            return added

        auto_count = len(self._pool.active)
        available = pool_limit - auto_count

        if available <= 0:
            logger.info(
                f"[AutoCoinSelector] Phase 4: 池满 ({auto_count}/{pool_limit})，尝试替换"
            )
        else:
            logger.info(
                f"[AutoCoinSelector] Phase 4: 有 {available} 个空位 ({auto_count}/{pool_limit})，开始选币"
            )

        # ===== 情况 A：有空位 =====
        selected: List[CandidateCoin] = []
        board_src = getattr(self, "_board_sourced", False)
        fixed_now = {
            str(s).upper()
            for s in (getattr(session, "symbols", None) or [])
            if s
        }
        # 第一遍：收集全部合格候选（approved 顺序 = 信心/分数降序）
        eligible: List[CandidateCoin] = []
        for c in approved:
            from backend.services.auto_coin_policy import is_training_core_symbol
            if is_training_core_symbol(c.symbol):
                continue
            if c.symbol.upper() in fixed_now:
                continue
            if c.symbol in existing_auto:
                continue
            # VIP 看板跟投：管理员 approve 已清黑名单/冷却，不再二次拦截
            if not board_src:
                if self._is_blacklisted(c.symbol, now):
                    continue
                if self._is_cooling(c.symbol, now):
                    continue
                # [2026-08-14 F2 整改] 24h 内被 removed 过的候选禁止重注入（防闪烁）
                if self._recently_removed(c.symbol):
                    logger.info("[AutoCoinSelector] %s 24h 内刚被移除，跳过本轮注入（F2 冷却）", c.symbol)
                    continue
            eligible.append(c)

        # S2-9：组合相关性去重 —— 贪心保留与已选低相关的候选（阈值 0 关闭）
        if eligible:
            try:
                from backend.config.settings import AUTO_COIN_CORR_DEDUP_THRESHOLD
                from backend.services.coin_rank.ic_weights import dedup_by_correlation, factor_vector
                threshold = float(AUTO_COIN_CORR_DEDUP_THRESHOLD)
                kept_syms = dedup_by_correlation(
                    [(c.symbol, factor_vector(c.scores_detail or {})) for c in eligible],
                    threshold=threshold,
                )
                if kept_syms:
                    kept_set = set(kept_syms)
                    eligible = [c for c in eligible if c.symbol in kept_set]
            except Exception as e:
                logger.debug(f"[AutoCoinSelector] 相关性去重跳过: {e}")

        for c in eligible[: available]:
            selected.append(c)

        # M2：同板块上限（含已有池）
        if selected:
            try:
                from backend.config.settings import AUTO_COIN_SECTOR_SIGNAL_ENABLED
                if AUTO_COIN_SECTOR_SIGNAL_ENABLED:
                    from backend.services.auto_coin_sector_signal import enforce_max_per_sector
                    pooled = list(self._pool.active.keys()) + [c.symbol for c in selected]
                    allowed = set(enforce_max_per_sector(pooled))
                    selected = [c for c in selected if c.symbol in allowed]
            except Exception as e:
                logger.debug(f"[AutoCoinSelector] sector cap skip: {e}")

        if selected:
            symbols_to_add = [c.symbol for c in selected]
            logger.info(f"[AutoCoinSelector] Phase 4: Injecting {len(symbols_to_add)} into {auto_count}-coin pool: {symbols_to_add}")

            result = service.add_symbols(db, self.session_id, symbols_to_add, is_auto_coin=True)
            if result.get("success"):
                for c in selected:
                    c.injected_at = now
                    c.session_id = self.session_id
                    self._pool.active[c.symbol] = c
                    self._auto_symbols.add(c.symbol)
                    self._evaluation_count[c.symbol] = 0
                    self._write_audit_record(db, c.symbol, "injected",
                        scanner_score=c.score, ai_confidence=c.ai_confidence,
                        ai_reason=c.ai_reason, factor_snapshot=c.scores_detail)
                    logger.info(f"[AutoCoinSelector] Injected: {c.symbol} (score={c.score:.3f}, conf={c.ai_confidence:.0%})")
                self._save_injected()
            else:
                logger.error(f"[AutoCoinSelector] Injection failed: {result.get('error')}")
                return result.get("added", [])

        # ===== 情况 B：池满，尝试替换 =====
        auto_count_after = len(self._pool.active)
        if auto_count_after >= pool_limit:
            # 对所有现有币种计算留存评分
            pool_symbols = list(self._pool.active.keys())
            existing_scores: Dict[str, RetentionScore] = {}
            for sym in pool_symbols:
                existing_scores[sym] = self._compute_retention_score(sym, db, pool_symbols)

            # 按评分排序，找最差币种
            sorted_existing = sorted(existing_scores.values(), key=lambda rs: rs.composite_score)
            worst = sorted_existing[0] if sorted_existing else None

            for c in approved:
                if c.symbol in existing_auto or c.symbol in self._pool.active:
                    continue
                if not board_src:
                    if self._is_blacklisted(c.symbol, now):
                        continue
                    if self._is_cooling(c.symbol, now):
                        continue
                if worst is None:
                    break

                # 候选评分（修复：不再用 score*confidence 把直批候选压成 score²）
                candidate_score = self._candidate_replace_score(c)

                # 替换条件检查
                eval_count = self._evaluation_count.get(worst.symbol, 0)
                perf = self._query_symbol_performance(db, worst.symbol)

                # Paper 轮换：更低替换差距
                replace_margin = AUTO_COIN_REPLACEMENT_MARGIN
                paper_rotate = False
                try:
                    from backend.config.settings import (
                        PAPER_AUTO_COIN_REPLACEMENT_MARGIN,
                        PAPER_AUTO_COIN_ROTATE,
                    )
                    if PAPER_AUTO_COIN_ROTATE and self._is_paper_session(db):
                        paper_rotate = True
                        replace_margin = float(PAPER_AUTO_COIN_REPLACEMENT_MARGIN)
                except Exception:
                    pass
                # VIP 看板跟投：更容易用管理员推荐替换旧池
                if board_src:
                    replace_margin = min(float(replace_margin), 0.05)

                can_replace = True
                reasons = []

                # 条件 1：候选评分必须比最差币高 REPLACEMENT_MARGIN
                if candidate_score <= worst.composite_score * (1 + replace_margin):
                    can_replace = False
                    reasons.append(f"差距不足({candidate_score:.3f} vs {worst.composite_score:.3f})")

                # 条件 2：最差币不在保护期
                # 修复：进程重启后 _evaluation_count 归零会永久卡保护期；
                # 若已持有 ≥ MIN_HOLD，视为保护期已过。
                hold_h = float(getattr(perf, "holding_duration_hours", 0) or 0)
                worst_entry = self._pool.active.get(worst.symbol)
                if worst_entry and worst_entry.injected_at:
                    hold_h = max(
                        hold_h,
                        (now - worst_entry.injected_at).total_seconds() / 3600.0,
                    )
                grace_ok = eval_count >= AUTO_COIN_GRACE_CYCLES or hold_h >= AUTO_COIN_MIN_HOLD_HOURS
                if not grace_ok:
                    can_replace = False
                    reasons.append(f"保护期中(cycle={eval_count},hold={hold_h:.1f}h)")

                # 条件 3：最差币已持有足够长时间
                if hold_h < AUTO_COIN_MIN_HOLD_HOURS and not (paper_rotate and hold_h >= 2.0):
                    can_replace = False
                    reasons.append(f"持有不足({hold_h:.0f}h<{AUTO_COIN_MIN_HOLD_HOURS}h)")

                # 条件 4：替换后不降低多样性（Paper 轮换时可放宽：仅记日志不拦截）
                candidate_sector = get_sector(c.symbol)
                worst_sector = get_sector(worst.symbol)
                if candidate_sector != worst_sector:
                    # 替换后 worst 的板块可能失去代表
                    others_in_worst_sector = sum(
                        1 for s in pool_symbols if s != worst.symbol and get_sector(s) == worst_sector
                    )
                    if others_in_worst_sector == 0:
                        if paper_rotate:
                            reasons.append(f"多样性放宽(丢失{worst_sector})")
                        else:
                            can_replace = False
                            reasons.append(f"会丢失板块({worst_sector})唯一代表")

                if not can_replace:
                    logger.info(
                        f"[AutoCoinSelector] replace_decision=no "
                        f"{c.symbol}↛{worst.symbol}: {'; '.join(reasons)}"
                    )
                    logger.info(
                        f"[AutoCoinSelector] Phase 4: 不替换 {worst.symbol}←{c.symbol}: "
                        + "; ".join(reasons)
                    )
                    continue

                if can_replace:
                    logger.info(f"[AutoCoinSelector] Phase 4: Replacing {worst.symbol}(C={worst.composite_score:.3f}) with {c.symbol}(score={candidate_score:.3f})")

                    # 轮出原子：先注入成功再移除旧币；注入失败则整次替换取消
                    add_result = service.add_symbols(db, self.session_id, [c.symbol], is_auto_coin=True)
                    if not add_result.get("success"):
                        logger.warning(
                            f"[AutoCoinSelector] Replace abort: inject {c.symbol} failed: "
                            f"{add_result.get('error')}"
                        )
                        continue

                    remove_result = service.remove_symbols(db, self.session_id, [worst.symbol])
                    if not remove_result.get("success"):
                        # 回滚新注入，避免池膨胀
                        service.remove_symbols(db, self.session_id, [c.symbol])
                        logger.warning(
                            f"[AutoCoinSelector] Replace rollback: remove {worst.symbol} failed, "
                            f"reverted {c.symbol}"
                        )
                        continue

                    if worst.symbol in self._pool.active:
                        self._pool.active.pop(worst.symbol)
                    self._add_cooling(worst.symbol, "short")
                    self._auto_symbols.discard(worst.symbol)
                    self._evaluation_count.pop(worst.symbol, None)
                    self._write_audit_record(db, worst.symbol, "removed",
                        scanner_score=worst.composite_score,
                        removal_reason=f"被 {c.symbol} 替换(候选分{candidate_score:.3f}>{worst.composite_score:.3f})")

                    c.injected_at = now
                    c.session_id = self.session_id
                    self._pool.active[c.symbol] = c
                    self._auto_symbols.add(c.symbol)
                    self._evaluation_count[c.symbol] = 0
                    existing_auto.add(c.symbol)
                    self._write_audit_record(db, c.symbol, "injected",
                        scanner_score=c.score, ai_confidence=c.ai_confidence,
                        ai_reason=c.ai_reason, factor_snapshot=c.scores_detail)
                    logger.info(
                        f"[AutoCoinSelector] replace_decision=yes "
                        f"{worst.symbol}→{c.symbol} "
                        f"cand={candidate_score:.3f} worst={worst.composite_score:.3f} "
                        f"margin={replace_margin}"
                    )
                    logger.info(f"[AutoCoinSelector] Replaced atomically: {worst.symbol} → {c.symbol}")
                    self._last_replace_count = getattr(self, "_last_replace_count", 0) + 1
                    self._save_injected()

                    # 重新计算 existing_scores
                    pool_symbols = list(self._pool.active.keys())
                    existing_scores = {}
                    for sym in pool_symbols:
                        existing_scores[sym] = self._compute_retention_score(sym, db, pool_symbols)
                    sorted_existing = sorted(existing_scores.values(), key=lambda rs: rs.composite_score)
                    worst = sorted_existing[0] if sorted_existing else None
                else:
                    logger.debug(f"[AutoCoinSelector] Cannot replace {worst.symbol}: {'; '.join(reasons)}")

        # ===== 硬性上限断言 =====
        final_count = len(self._pool.active)
        if final_count > pool_limit:
            logger.warning(f"[AutoCoinSelector] Pool overflow: {final_count} > {pool_limit}, force trimming")
            scored = []
            for sym in list(self._pool.active.keys()):
                entry = self._pool.active[sym]
                scored.append((sym, entry.score))
            scored.sort(key=lambda x: x[1], reverse=True)
            to_trim = [x[0] for x in scored[pool_limit:]]
            trim_result = service.remove_symbols(db, self.session_id, to_trim)
            if trim_result.get("success"):
                for sym in to_trim:
                    if sym in self._pool.active:
                        self._pool.active.pop(sym)
                    self._add_cooling(sym, "short")
                    self._auto_symbols.discard(sym)
                    self._evaluation_count.pop(sym, None)
                    self._write_audit_record(db, sym, "removed", removal_reason="超出上限强制裁剪")
                self._save_injected()

        # 更新 DB auto_coin_symbols
        try:
            session_obj = db.query(self._get_session_model()).filter(
                self._get_session_model().session_id == self.session_id
            ).first()
            if session_obj:
                session_obj.auto_coin_symbols = sorted(self._pool.active.keys())
                db.commit()
        except Exception:
            pass

        return [c.symbol for c in selected]

    def _preflight_data_check(self, symbol: str, exchange: str) -> bool:
        """注入硬门：会话交易所可交易 + 该所 K线新鲜且根数够。

        1) symbol_catalog 必须 status=trading（当前会话交易所）
        2) data_center purpose=trade 下 5m/15m/1h 必须新鲜可读
        3) 5m/15m/1h/4h/1d 根数达标（不够可尝试补齐，仍不够则拒注入）
        """
        try:
            sym_u = symbol.upper().strip()
            ex = (exchange or "").strip().lower()
            if ex == "aster":
                ex = "asterdex"

            # 硬门 1：可交易目录
            try:
                from backend.services.kline_sync_meta import list_catalog_symbols, refresh_catalog_from_scanner
                catalog = list_catalog_symbols(ex) or []
                if not catalog:
                    catalog = refresh_catalog_from_scanner(ex) or []
                if catalog and sym_u not in set(catalog):
                    logger.warning(
                        f"[AutoCoinSelector] {sym_u} 不在 {ex} 可交易目录，拒绝注入"
                    )
                    return False
            except Exception as e:
                logger.debug(f"[AutoCoinSelector] catalog gate skip: {e}")

            # 硬门 2：决策同源新鲜度（禁止过期/跨所）
            # Paper 放宽：5m 不新鲜时，只要 15m+1h 新鲜即可（短线新币常缺 5m 热缓存）
            paper_relax = False
            try:
                from backend.config.settings import PAPER_AUTO_COIN_PREFLIGHT_RELAX
                paper_relax = bool(PAPER_AUTO_COIN_PREFLIGHT_RELAX) and self._is_paper_session()
            except Exception:
                paper_relax = False
            try:
                from backend.services.data_center import data_center
                tfs = ("15m", "1h") if paper_relax else ("5m", "15m", "1h")
                for tf in tfs:
                    result = data_center.get_klines(
                        sym_u, tf, count=50, exchange=ex, purpose="trade",
                    )
                    if not result.rows or not result.is_fresh:
                        logger.warning(
                            f"[AutoCoinSelector] {sym_u}/{tf}@{ex} 不新鲜或空 "
                            f"(stale={getattr(result, 'stale_sec', None)})，拒绝注入"
                        )
                        return False
                if paper_relax:
                    # 尽力拉 5m，失败不拦
                    try:
                        data_center.get_klines(
                            sym_u, "5m", count=50, exchange=ex, purpose="trade",
                        )
                    except Exception:
                        pass
            except Exception as e:
                logger.warning(f"[AutoCoinSelector] freshness gate fail {sym_u}: {e}")
                return False

            from services.kline_data_service import kline_service

            if paper_relax:
                _TF_REQUIREMENTS = [
                    ("15m", 30),
                    ("1h", 30),
                    ("4h", 20),
                ]
            else:
                _TF_REQUIREMENTS = [
                    ("5m", 50),
                    ("15m", 50),
                    ("1h", 50),
                    ("4h", 30),
                    ("1d", 20),
                ]

            def _check_klines() -> tuple:
                missing = {}
                for tf, min_n in _TF_REQUIREMENTS:
                    raw = kline_service.get_klines_from_db(
                        sym_u, tf, count=100, exchange=ex,
                    )
                    n = len(raw) if raw else 0
                    if n < min_n:
                        missing[tf] = (n, min_n)
                return (len(missing) == 0, missing)

            ok, missing = _check_klines()
            if ok:
                return True

            logger.info(
                f"[AutoCoinSelector] {sym_u} 数据不足: "
                + ", ".join(f"{tf}={v[0]}/{v[1]}" for tf, v in missing.items())
                + f" @{ex}，尝试补齐..."
            )
            try:
                from backend.services.market_data import get_kline_data
                for _tf in ["5m", "15m", "1h", "4h", "1d"]:
                    try:
                        get_kline_data(sym_u, market=ex, period=_tf, count=200)
                    except Exception:
                        pass
                # 触发数据中心按所回填（不阻塞太久）
                try:
                    from backend.services.data_center import data_center
                    data_center.ensure_history(sym_u, "1h", min_years=0.1, exchange=ex)
                except Exception:
                    pass
            except Exception:
                pass

            ok2, missing2 = _check_klines()
            if ok2:
                logger.info(f"[AutoCoinSelector] {sym_u} 数据补齐成功，允许注入")
                return True

            logger.warning(
                f"[AutoCoinSelector] {sym_u} 数据不足，淘汰: "
                + ", ".join(f"{tf}={v[0]}/{v[1]}" for tf, v in missing2.items())
            )
            return False

        except Exception as e:
            logger.debug(f"[AutoCoinSelector] {symbol} 数据预检异常: {e}")
            return False

    # ── 阶段 5: 定期评估与淘汰 ──────────────────────────────────────────

    def _build_ai_expiry_review_prompt(
        self,
        symbol: str,
        exchange: str,
        hold_h: float,
        rs: RetentionScore,
        perf: CoinPerformanceData,
        market: Optional[Dict[str, Any]],
    ) -> str:
        lines = [
            "你是加密货币全自动交易系统的风控顾问。以下 AI 选币已到达上币期限，请判断是否应从交易列表中剔除。",
            "",
            f"【交易所】{exchange}",
            f"【币种】{symbol}",
            f"【已持有】{hold_h:.1f} 小时（期限 {AUTO_COIN_MAX_HOLD_HOURS}h）",
            "",
            "【留存评分（0-1，越高越好）】",
            f"  综合分: {rs.composite_score:.3f}",
            f"  表现分: {rs.performance_score:.3f}",
            f"  市场分: {rs.market_fit_score:.3f}",
            f"  稳定性: {rs.retention_bonus:.3f}",
            f"  多样性: {rs.diversity_score:.3f}",
            f"  备注: {rs.removal_risk_note}",
            "",
            "【实际交易表现】",
            f"  成交笔数: {perf.total_trades}",
            f"  胜率: {perf.win_rate:.1%}",
            f"  累计盈亏: {perf.total_pnl:+.4f}",
            f"  Sharpe估计: {perf.sharpe_estimate:.2f}",
        ]
        if market:
            lines.extend(["", "【当前市场】"])
            if market.get("price_change_24h") is not None:
                lines.append(f"  24h涨跌: {float(market['price_change_24h']):+.2%}")
            if market.get("volume_24h"):
                lines.append(f"  24h成交量: ${float(market['volume_24h']):,.0f}")
            if market.get("funding_rate") is not None:
                lines.append(f"  资金费率: {float(market['funding_rate']):.4%}")
        lines.extend([
            "",
            "请综合判断：该币是否仍值得保留在 AI 自动选币池中？",
            "考虑：近期盈亏、市场趋势、是否仍有盈利潜力、是否应让位给新机会。",
            "",
            '请以JSON回复: {"remove": true/false, "reason": "中文理由50字内", "confidence": 0.0-1.0}',
            "remove=true 表示建议剔除，false 表示建议续期保留。",
        ])
        return "\n".join(lines)

    async def _call_ai_expiry(self, llm_cfg, prompt: str) -> Dict[str, Any]:
        import json as _json

        try:
            from backend.services.llm_config_service import call_llm_api

            resp_data = await call_llm_api(
                config=llm_cfg,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=300,
            )
            if not resp_data:
                raise ValueError("Empty AI response")
            choices = resp_data.get("choices", [])
            response_text = (choices[0].get("message", {}) or {}).get("content", "") if choices else ""
            response_text = (response_text or "").strip()
            if response_text.startswith("```"):
                parts = response_text.split("\n")
                response_text = "\n".join(parts[1:-1] if len(parts) > 2 else parts)
            result = _json.loads(response_text)
            return {
                "remove": bool(result.get("remove", True)),
                "reason": str(result.get("reason", "")),
                "confidence": float(result.get("confidence", 0.5)),
            }
        except Exception as e:
            logger.warning(f"[AutoCoinSelector] AI expiry review failed: {e}")
            return {"remove": True, "reason": f"AI复核失败,保守剔除: {e}", "confidence": 0.0}

    async def _ai_expiry_removal_review(
        self, db: Session, symbol: str, hold_h: float, rs: RetentionScore
    ) -> Tuple[bool, str, float]:
        """到期 AI 复核。Returns (should_remove, reason, confidence)."""
        try:
            from backend.services.llm_config_service import get_llm_config_for_usage

            llm_cfg = get_llm_config_for_usage("coin_select", account_id=self.account_id, tier="quick")
            if not llm_cfg or not llm_cfg.api_key:
                return (
                    rs.composite_score < AUTO_COIN_EXPIRY_KEEP_SCORE,
                    "无AI密钥,按综合分判定",
                    rs.composite_score,
                )
        except Exception:
            return (
                rs.composite_score < AUTO_COIN_EXPIRY_KEEP_SCORE,
                "AI配置异常,按综合分判定",
                rs.composite_score,
            )

        exchange = self.resolve_exchange(db)
        perf = self._query_symbol_performance(db, symbol)
        market = self._fetch_market_snapshot(symbol, exchange)
        prompt = self._build_ai_expiry_review_prompt(symbol, exchange, hold_h, rs, perf, market)
        result = await self._call_ai_expiry(llm_cfg, prompt)
        return bool(result["remove"]), str(result["reason"]), float(result["confidence"])

    def _decide_expired_symbol_action(
        self, db: Session, symbol: str, hold_h: float
    ) -> Tuple[str, str, RetentionScore]:
        """到期复核：先算法评分，边界区间再 AI 分析。Returns (keep|remove, reason, rs)."""
        pool_syms = list(self._pool.active.keys())
        rs = self._compute_retention_score(symbol, db, pool_syms)

        keep_score = AUTO_COIN_EXPIRY_KEEP_SCORE
        try:
            from backend.config.settings import (
                PAPER_AUTO_COIN_EXPIRY_KEEP_SCORE,
                PAPER_AUTO_COIN_ROTATE,
            )
            if PAPER_AUTO_COIN_ROTATE and self._is_paper_session(db):
                keep_score = float(PAPER_AUTO_COIN_EXPIRY_KEEP_SCORE)
        except Exception:
            pass
        if rs.composite_score >= keep_score:
            # 相对排名：仍需在当前粗分宇宙前 50%，否则不续期（防僵尸）
            try:
                from backend.services.coin_rank.engine import engine_enabled, rank_universe

                if engine_enabled():
                    ranked = rank_universe(limit=40, apply_factor=False, apply_gate=False, apply_decay=True)
                    syms = [r.symbol for r in ranked]
                    if symbol.upper() in syms:
                        idx = syms.index(symbol.upper())
                        if idx > max(1, len(syms) // 2):
                            return (
                                "remove",
                                f"到期复核:综合分尚可但相对排名偏低(#{idx+1}/{len(syms)}),腾出空位",
                                rs,
                            )
                    elif ranked:
                        return (
                            "remove",
                            "到期复核:已不在 Rank Top 池,腾出空位",
                            rs,
                        )
            except Exception as e:
                logger.debug(f"[AutoCoinSelector] relative rank renew skip: {e}")
            self._last_renewed_no_change = getattr(self, "_last_renewed_no_change", 0) + 1
            return (
                "keep",
                f"到期复核:综合分{rs.composite_score:.2f}>={keep_score},续期保留",
                rs,
            )
        if rs.composite_score <= AUTO_COIN_EXPIRY_REMOVE_SCORE:
            return (
                "remove",
                f"到期复核:综合分{rs.composite_score:.2f}<={AUTO_COIN_EXPIRY_REMOVE_SCORE},建议剔除",
                rs,
            )

        import asyncio

        should_remove, ai_reason, confidence = asyncio.run(
            self._ai_expiry_removal_review(db, symbol, hold_h, rs)
        )
        if should_remove:
            return (
                "remove",
                f"到期AI复核:剔除(conf={confidence:.0%}) {ai_reason}",
                rs,
            )
        return (
            "keep",
            f"到期AI复核:续期(conf={confidence:.0%}) {ai_reason}",
            rs,
        )

    def _renew_expired_symbol(
        self, db: Session, symbol: str, reason: str, rs: RetentionScore
    ):
        now = datetime.now()
        entry = self._pool.active.get(symbol)
        if entry:
            entry.injected_at = now
        if not hasattr(self, "_injected_times"):
            self._injected_times = {}
        self._injected_times[symbol] = now
        self._write_audit_record(
            db,
            symbol,
            "renewed",
            scanner_score=rs.composite_score,
            ai_reason=reason[:500],
            risk_note=rs.removal_risk_note,
        )
        self._save_injected()
        logger.info(f"[AutoCoinSelector] Expiry renewed: {symbol} — {reason[:80]}")

    def prune_expired_auto_symbols(self, db: Session) -> Dict[str, Any]:
        """上币期限到期后：算法评分 + AI 复核，再决定续期或剔除。"""
        if AUTO_COIN_MAX_HOLD_HOURS <= 0:
            return {"success": True, "removed": [], "removed_count": 0}

        self._sync_active_pool_from_db(db)
        self._hydrate_injected_times(db)

        now = datetime.now()
        expired: List[Tuple[str, float]] = []
        for symbol, entry in list(self._pool.active.items()):
            injected = entry.injected_at
            if not injected:
                continue
            hold_h = (now - injected).total_seconds() / 3600
            if hold_h >= AUTO_COIN_MAX_HOLD_HOURS:
                expired.append((symbol, hold_h))

        if not expired:
            return {"success": True, "removed": [], "removed_count": 0, "renewed": []}

        to_remove: List[str] = []
        renewed: List[str] = []
        review_log: List[Dict[str, Any]] = []

        for symbol, hold_h in expired:
            action, reason, rs = self._decide_expired_symbol_action(db, symbol, hold_h)
            review_log.append({
                "symbol": symbol,
                "hold_hours": round(hold_h, 1),
                "action": action,
                "reason": reason,
                "composite_score": rs.composite_score,
            })
            if action == "keep":
                self._renew_expired_symbol(db, symbol, reason, rs)
                renewed.append(symbol)
            else:
                to_remove.append(symbol)

        if not to_remove:
            logger.info(
                f"[AutoCoinSelector] Expiry review: {len(renewed)} renewed, 0 removed"
            )
            return {
                "success": True,
                "removed": [],
                "removed_count": 0,
                "renewed": renewed,
                "review": review_log,
            }

        logger.info(
            f"[AutoCoinSelector] Expiry review removing {len(to_remove)}: {to_remove}; "
            f"renewed {len(renewed)}: {renewed}"
        )

        from backend.services.full_auto_trading_service import FullAutoTradingService

        service = FullAutoTradingService.get_instance()
        result = service.remove_symbols(db, self.session_id, to_remove)
        removed: List[str] = []
        if result.get("success"):
            for sym in result.get("removed", []):
                removed.append(sym)
                if sym in self._pool.active:
                    self._pool.active.pop(sym)
                self._add_cooling(sym, "short")
                self._auto_symbols.discard(sym)
                self._evaluation_count.pop(sym, None)
                hold_h = next((h for s, h in expired if s == sym), 0.0)
                log_item = next((x for x in review_log if x["symbol"] == sym), {})
                self._write_audit_record(
                    db,
                    sym,
                    "removed",
                    scanner_score=log_item.get("composite_score"),
                    removal_reason=log_item.get("reason") or (
                        f"上币期限到期(持有{hold_h:.1f}h>={AUTO_COIN_MAX_HOLD_HOURS}h)"
                    ),
                )
            self._save_injected()

        return {
            "success": bool(result.get("success")),
            "removed": removed,
            "removed_count": len(removed),
            "renewed": renewed,
            "review": review_log,
            "error": result.get("error"),
        }

    def evaluate_auto_symbols(self, db: Session) -> Dict[str, Any]:
        """
        阶段 5: 评估所有自动选中的币种表现，淘汰表现不佳者。

        V2 评分引擎：
          1. 查询实际交易表现（P&L、胜率、Sharpe）
          2. 4 维度综合评分：表现(40%) + 市场(30%) + 稳定性(15%) + 多样性(15%)
          3. 分层淘汰：Tier1(严重) / Tier2(标准) / Tier3(保护)
          4. 写审计记录到 AutoCoinSelection 表
        """
        from backend.services.full_auto_trading_service import FullAutoTradingService

        session = db.query(self._get_session_model()).filter(
            self._get_session_model().session_id == self.session_id
        ).first()
        if not session:
            return {"success": False, "error": "Session not found"}

        self.prune_expired_auto_symbols(db)
        session = db.query(self._get_session_model()).filter(
            self._get_session_model().session_id == self.session_id
        ).first()
        if not session:
            return {"success": False, "error": "Session not found"}

        # auto-coin 隔离在 auto_coin_symbols，不得用 session.symbols 判存活
        db_auto = set(s.upper() for s in (getattr(session, "auto_coin_symbols", None) or []))
        for symbol in list(self._pool.active.keys()):
            if symbol not in db_auto:
                del self._pool.active[symbol]
                self._evaluation_count.pop(symbol, None)
        auto_active = list(self._pool.active.keys())

        to_remove: List[Tuple[str, str, str]] = []  # (symbol, reason, cooling_tier)
        kept: List[str] = []
        report: List[Dict[str, Any]] = []
        retention_scores: Dict[str, RetentionScore] = {}

        # 第一步：对每个 auto-coin 计算留存评分
        for symbol in auto_active:
            self._evaluation_count[symbol] = self._evaluation_count.get(symbol, 0) + 1
            eval_count = self._evaluation_count[symbol]

            # 计算留存评分
            rs = self._compute_retention_score(symbol, db, auto_active)
            retention_scores[symbol] = rs

            should_remove = False
            reason = ""
            cooling_tier = "long"

            # ---- 分层淘汰规则（到期复核由 prune_expired_auto_symbols 前置处理）----

            # Tier 1: 严重不达标（无视保护期）
            if rs.performance_score < 0.15 and not rs.removal_risk_note.startswith("新币"):
                should_remove = True
                reason = f"表现严重不足(P={rs.performance_score:.2f})"
                cooling_tier = "very_long"

            elif not should_remove and rs.market_fit_score < 0.15:
                should_remove = True
                reason = f"市场条件恶化(M={rs.market_fit_score:.2f})"
                cooling_tier = "long"

            # 价格暴跌快速淘汰（保留原有逻辑）
            elif not should_remove and eval_count >= AUTO_COIN_GRACE_CYCLES:
                try:
                    exchange = self.resolve_exchange(db)
                    market_data = self._fetch_market_snapshot(symbol, exchange)
                    if market_data and market_data.get("price_change_24h") is not None:
                        if float(market_data["price_change_24h"]) < AUTO_COIN_PERFORMANCE_THRESHOLD:
                            should_remove = True
                            reason = f"价格暴跌({float(market_data['price_change_24h']):+.2%})"
                            cooling_tier = "very_long"
                except Exception:
                    pass

            # Tier 2: 标准淘汰（保护期后才生效）
            if not should_remove and eval_count >= AUTO_COIN_GRACE_CYCLES:
                if rs.composite_score < 0.25:
                    should_remove = True
                    reason = f"综合评分过低(C={rs.composite_score:.2f})"
                    cooling_tier = "long"
                elif rs.performance_score < 0.30:
                    perf = self._query_symbol_performance(db, symbol)
                    if perf.holding_duration_hours >= AUTO_COIN_MIN_HOLD_HOURS:
                        should_remove = True
                        reason = f"持续表现不佳(P={rs.performance_score:.2f}, held={perf.holding_duration_hours:.0f}h)"
                        cooling_tier = "long"

            # Tier 3: 不淘汰的保护条件
            if should_remove:
                # 板块唯一代表 → 保护
                if rs.diversity_score >= 1.0 and rs.composite_score >= 0.20:
                    should_remove = False
                    reason = f"保护:板块唯一(C={rs.composite_score:.2f})"
                    cooling_tier = "short"

            # 保护期内不淘汰
            if should_remove and eval_count < AUTO_COIN_GRACE_CYCLES:
                should_remove = False
                reason = f"保护期(cycle {eval_count}/{AUTO_COIN_GRACE_CYCLES})"

            cycle_report = {
                "symbol": symbol,
                "evaluation_cycle": eval_count,
                "composite_score": rs.composite_score,
                "performance_score": rs.performance_score,
                "market_fit_score": rs.market_fit_score,
                "retention_bonus": rs.retention_bonus,
                "diversity_score": rs.diversity_score,
                "risk_note": rs.removal_risk_note,
                "should_remove": should_remove,
                "reason": reason,
            }
            report.append(cycle_report)

            if should_remove:
                to_remove.append((symbol, reason, cooling_tier))
            else:
                kept.append(symbol)

        # 第二步：执行淘汰
        if to_remove:
            symbols_to_remove = [t[0] for t in to_remove]
            logger.info(f"[AutoCoinSelector] Phase 5: Removing {len(symbols_to_remove)} symbols: {[f'{t[0]}({t[2]})' for t in to_remove]}")
            service = FullAutoTradingService.get_instance()
            result = service.remove_symbols(db, self.session_id, symbols_to_remove)
            if result.get("success"):
                for sym in result.get("removed", []):
                    # 找到对应的 cooling tier
                    tier = "long"
                    rm_reason = ""
                    for t in to_remove:
                        if t[0] == sym:
                            tier = t[2]
                            rm_reason = t[1]
                            break
                    if sym in self._pool.active:
                        self._pool.active.pop(sym)
                    self._add_cooling(sym, tier)
                    # 严重移除(暴跌 / 表现严重不足)额外激活 7 天黑名单(阶段A 修复)
                    if self._is_severe_removal(rm_reason):
                        self._add_blacklist(sym)
                        logger.info(f"[AutoCoinSelector] Blacklisted: {sym} for {BLACKLIST_S}s (severe: {rm_reason})")
                    self._evaluation_count.pop(sym, None)
                    self._auto_symbols.discard(sym)
                    # 写审计
                    rs = retention_scores.get(sym)
                    self._write_audit_record(db, sym, "removed",
                        scanner_score=rs.composite_score if rs else None,
                        removal_reason=rm_reason,
                        risk_note=rs.removal_risk_note if rs else None,
                    )
                    logger.info(f"[AutoCoinSelector] Removed: {sym} (tier={tier}, reason={rm_reason})")
                self._save_injected()
            else:
                logger.error(f"[AutoCoinSelector] Remove failed: {result.get('error')}")

        # 第三步：强制上限（以 auto_coin 池为准，非 session.symbols）
        auto_in_session = list(self._pool.active.keys())
        pool_limit = self._pool_limit(db)
        if len(auto_in_session) > pool_limit:
            scored = [(s, retention_scores.get(s, RetentionScore(symbol=s)).composite_score) for s in auto_in_session]
            scored.sort(key=lambda x: x[1], reverse=True)
            keep_set = {x[0] for x in scored[:pool_limit]}
            excess = [x[0] for x in scored[pool_limit:]]
            logger.info(f"[AutoCoinSelector] Phase 5: Cap enforcement - trimming {len(excess)}: {excess}")
            service_for_cap = FullAutoTradingService.get_instance()
            result = service_for_cap.remove_symbols(db, self.session_id, excess)
            if result.get("success"):
                for sym in result.get("removed", []):
                    if sym in self._pool.active:
                        self._pool.active.pop(sym)
                    self._add_cooling(sym, "short")
                    self._auto_symbols.discard(sym)
                    self._evaluation_count.pop(sym, None)
                    to_remove.append((sym, "超出上限", "short"))
                    if sym in kept:
                        kept.remove(sym)
                    self._write_audit_record(db, sym, "removed",
                        removal_reason=f"超出上限({len(auto_in_session)}>{pool_limit})")
                    logger.info(f"[AutoCoinSelector] Capped: {sym}")
                self._save_injected()
            else:
                logger.error(f"[AutoCoinSelector] Cap removal failed: {result.get('error')}")

        # 更新 DB 中的 auto_coin_symbols
        try:
            session_obj = db.query(self._get_session_model()).filter(
                self._get_session_model().session_id == self.session_id
            ).first()
            if session_obj:
                session_obj.auto_coin_symbols = sorted(kept)
                db.commit()
        except Exception as e:
            logger.debug(f"[AutoCoinSelector] Failed to update auto_coin_symbols: {e}")

        summary = {
            "success": True,
            "active_count": len(kept),
            "removed_count": len(to_remove),
            "removed": [t[0] for t in to_remove],
            "kept": kept,
            "report": report,
            "cycle": self._cycle_count,
        }
        logger.info(f"[AutoCoinSelector] Phase 5 done: {len(kept)} kept, {len(to_remove)} removed")
        return summary

    # ── 主入口: 运行完整选币周期 ──────────────────────────────────────

    async def run_selection_cycle(
        self,
        db: Session,
        lane: str = "full",
        focus_symbols: Optional[List[str]] = None,
        force_ai: bool = False,
    ) -> Dict[str, Any]:
        """
        运行一个完整的自动选币周期（阶段 1-5）。

        lane: full|normal|fast（观测用）
        focus_symbols: 非空时 Phase1 只评这些币（M3 轻量车道）
        force_ai: True 时绕过 AI 降频（受每小时上限由调度器控制）
        """
        self._cycle_count += 1
        self._last_scan_time = datetime.now()
        self._current_lane = lane
        if force_ai:
            # 清零降频时间戳，让本轮走完整 LLM（或 score-only 若无 key）
            AutoCoinSelector._last_ai_review_ts = 0.0
        logger.info(
            f"[AutoCoinSelector] === Cycle {self._cycle_count} started "
            f"lane={lane} focus={len(focus_symbols or [])} ==="
        )
        self._sync_active_pool_from_db(db)

        from backend.config.settings import AUTO_COIN_SOURCE

        source_mode = (AUTO_COIN_SOURCE or "platform_board").strip().lower()
        self._board_sourced = source_mode in ("platform_board", "board", "vip", "platform")

        phase1_start = time.time()
        if self._board_sourced:
            # 统一路径：只跟投管理员 VIP 短线看板，不再另跑一套市场扫描+账户 LLM
            candidates = self._candidates_from_platform_board(db)
            self._last_rank_source = "platform_board"
            self._last_degraded = None
            if not candidates:
                logger.warning(
                    "[AutoCoinSelector] 平台短线看板为空，本轮跳过独立扫描"
                    "（请管理员在 VIP 页重扫；避免两套选币打架）"
                )
            phase1_ms = int((time.time() - phase1_start) * 1000)
            phase2_ms = 0
            phase3_ms = 0
        else:
            candidates = self.scan_candidates(db, focus_symbols=focus_symbols)
            phase1_ms = int((time.time() - phase1_start) * 1000)
            if self._cancel_requested:
                logger.info(f"[AutoCoinSelector] Cycle {self._cycle_count} cancelled after scan")
                return {"success": False, "cancelled": True, "phase": "scan"}

            phase2_start = time.time()
            candidates = self.enrich_candidates(db, candidates)
            phase2_ms = int((time.time() - phase2_start) * 1000)
            if self._cancel_requested:
                logger.info(f"[AutoCoinSelector] Cycle {self._cycle_count} cancelled after enrich")
                return {"success": False, "cancelled": True, "phase": "enrich"}

            phase3_start = time.time()
            candidates = await self.ai_review(db, candidates)
            phase3_ms = int((time.time() - phase3_start) * 1000)
            if self._cancel_requested:
                logger.info(f"[AutoCoinSelector] Cycle {self._cycle_count} cancelled before inject")
                return {"success": False, "cancelled": True, "phase": "ai_review"}

        if self._cancel_requested:
            logger.info(f"[AutoCoinSelector] Cycle {self._cycle_count} cancelled before inject")
            return {"success": False, "cancelled": True, "phase": "inject"}

        if self._baseline_symbols is None:
            session = db.query(self._get_session_model()).filter(
                self._get_session_model().session_id == self.session_id
            ).first()
            if session:
                # 只快照固定表。AI 池已由 _sync_active_pool_from_db 从 auto_coin_symbols 恢复，
                # 禁止再用 session.symbols ∩ 文件 覆盖 auto 池（否则固定币会误占/清空 AI 槽）。
                self._baseline_symbols = set(s.upper() for s in (session.symbols or []))
                logger.info(
                    f"[AutoCoinSelector] Baseline snapshot: 固定={sorted(self._baseline_symbols)} "
                    f"AI池={sorted(self._pool.active.keys())}"
                )

        self._last_degraded = None if self._board_sourced else getattr(self, "_last_degraded", None)
        self._last_replace_count = 0
        self._last_renewed_no_change = 0
        if not self._board_sourced:
            try:
                from backend.services.coin_rank.engine import engine_enabled
                self._last_rank_source = "coin_rank" if engine_enabled() else "legacy"
            except Exception:
                self._last_rank_source = "legacy"

        phase4_start = time.time()
        injected = self.inject_approved_symbols(db, candidates)
        # VIP 看板已改为「补满槽位」：未 reject 的旧 AI 币保留；此处不再强删非看板币
        if self._board_sourced and candidates:
            logger.info(
                "[AutoCoinSelector] VIP补满完成 injected=%s pool=%s",
                injected,
                sorted(self._pool.active.keys()),
            )
        phase4_ms = int((time.time() - phase4_start) * 1000)

        phase5_start = time.time()
        evaluation = self.evaluate_auto_symbols(db)
        phase5_ms = int((time.time() - phase5_start) * 1000)

        total_ms = phase1_ms + phase2_ms + phase3_ms + phase4_ms + phase5_ms

        report = {
            "success": True,
            "cycle": self._cycle_count,
            "session_id": self.session_id,
            "account_id": self.account_id,
            "exchange": self._exchange,
            "lane": lane,
            "degraded": self._last_degraded,
            "rank_source": self._last_rank_source,
            "phases": {
                "scan": {"candidates": len(candidates), "ms": phase1_ms},
                "enrich": {"enriched": min(len(candidates), 30), "ms": phase2_ms},
                "ai_review": {"approved": len([c for c in candidates if c.ai_approved]), "ms": phase3_ms},
                "inject": {
                    "injected": len(injected),
                    "replaced": self._last_replace_count,
                    "ms": phase4_ms,
                    "blocked_reason": self._last_inject_block_reason,
                },
                "evaluate": {
                    "active": evaluation.get("active_count", 0),
                    "removed": evaluation.get("removed_count", 0),
                    "renewed_no_change": self._last_renewed_no_change,
                    "ms": phase5_ms,
                },
            },
            "total_ms": total_ms,
            "timestamp": datetime.now().isoformat(),
        }

        try:
            from backend.services.coin_rank.metrics import CycleMetrics, record_cycle_metrics

            record_cycle_metrics(
                CycleMetrics(
                    track="session",
                    session_id=self.session_id,
                    scanned=len(candidates),
                    ai_reviewed=len([c for c in candidates if c.ai_approved or c.ai_reason]),
                    injected=len(injected),
                    replaced=self._last_replace_count,
                    renewed_no_change=self._last_renewed_no_change,
                    soft_reject=sum(
                        1 for c in candidates
                        if (c.scores_detail or {}).get("force_test_position")
                    ),
                    degraded=self._last_degraded,
                    rank_source=self._last_rank_source,
                    lane=lane,
                )
            )
        except Exception as e:
            logger.debug(f"[AutoCoinSelector] metrics: {e}")

        logger.info(
            f"[AutoCoinSelector] === Cycle {self._cycle_count} done ({total_ms}ms) "
            f"rank_source={self._last_rank_source} degraded={self._last_degraded} "
            f"replaced={self._last_replace_count} ==="
        )
        return report

    # ═══════════════════════════════════════════════════════════════════
    # 内部辅助方法
    # ═══════════════════════════════════════════════════════════════════

    def _is_blacklisted(self, symbol: str, now: datetime) -> bool:
        if symbol in self._pool.blacklist:
            if (now - self._pool.blacklist[symbol]).total_seconds() < BLACKLIST_S:
                return True
            del self._pool.blacklist[symbol]
        return False

    def _is_cooling(self, symbol: str, now: datetime) -> bool:
        if symbol in self._pool.cooling:
            start_time, tier = self._pool.cooling[symbol]
            duration = COOLING_DURATIONS.get(tier, COOLING_SHORT_S)
            if (now - start_time).total_seconds() < duration:
                return True
            del self._pool.cooling[symbol]
        return False

    # [2026-08-14 F2 整改] 移除后 24h 禁止重新注入（防 injected/removed 闪烁循环）
    _REINJECT_COOLDOWN_SEC = 86400

    def _recently_removed(self, symbol: str) -> bool:
        """该 symbol 24h 内被 removed 过则禁止本轮重新注入。

        历史事故：CL/CSCO/SPCX 等每 ~15 分钟 injected→removed 反复，
        反复改写 auto_coin_symbols、抖动中线候选集合。
        """
        try:
            from backend.database.connection import SessionLocal as _AL
            from backend.database.models import AutoCoinSelection

            db = _AL()
            try:
                try:
                    db.connection().exec_driver_sql("SET app.is_admin = 'on'")
                except Exception:
                    pass
                cutoff = datetime.utcnow() - timedelta(seconds=self._REINJECT_COOLDOWN_SEC)
                row = (
                    db.query(AutoCoinSelection)
                    .filter(
                        AutoCoinSelection.session_id == self.session_id,
                        AutoCoinSelection.symbol == str(symbol).upper(),
                        AutoCoinSelection.action == "removed",
                        AutoCoinSelection.created_at >= cutoff,
                    )
                    .order_by(AutoCoinSelection.id.desc())
                    .first()
                )
                return row is not None
            finally:
                db.close()
        except Exception as e:
            logger.debug("[AutoCoinSelector] recently_removed check fail %s: %s", symbol, e)
            return False

    def _add_cooling(self, symbol: str, tier: str = "short"):
        """将币种加入冷却池（分级冷却）并持久化。"""
        self._pool.cooling[symbol] = (datetime.now(), tier)
        try:
            self._save_injected()
        except Exception:
            pass

    def _add_blacklist(self, symbol: str):
        """将币种加入黑名单(BLACKLIST_S 秒,默认 7 天)并持久化。"""
        self._pool.blacklist[symbol] = datetime.now()
        try:
            self._save_injected()
        except Exception:
            pass

    # 触发黑名单的严重移除关键词(命中任一即拉黑)。
    _SEVERE_REMOVAL_KEYWORDS = ("价格暴跌", "表现严重不足")

    @classmethod
    def _is_severe_removal(cls, reason: str) -> bool:
        """判断一条 removal_reason 是否属于"严重"级别(应激活黑名单)。

        覆盖两个来源:
        - 价格暴跌(Tier fast-fail, very_long 冷却)
        - 表现严重不足(Tier 1, very_long 冷却)
        非 severe 的常规移除(综合评分过低 / 超出上限 / 市场条件恶化 / 到期)
        不拉黑,只走冷却。
        """
        if not reason:
            return False
        return any(kw in reason for kw in cls._SEVERE_REMOVAL_KEYWORDS)

    def _get_tenant_id(self, db: Session):
        """解析当前会话的 tenant_id(users.id)。

        阶段A 修复:``_write_audit_record`` 原本不设 tenant_id,而
        ``auto_coin_selections.tenant_id`` 在 0004 迁移里被改成 NOT NULL,
        导致每次 renewed/removed 写审计都触发 NotNullViolation。

        链路:session_id → full_auto_sessions.account_id → accounts.user_id。
        account_id 优先用会话里的 paper_account_id,回退到 trading account_id,
        最后回退到 self.account_id。
        """
        try:
            from backend.database.models import Account, FullAutoSession
            session = db.query(FullAutoSession).filter(
                FullAutoSession.session_id == self.session_id
            ).first()
            acc_id = (
                getattr(session, "paper_account_id", None)
                or getattr(session, "trading_account_id", None)
                or getattr(session, "account_id", None)
                or self.account_id
            ) if session else self.account_id
            account = db.query(Account).filter(Account.id == acc_id).first()
            if account is not None and account.user_id is not None:
                return account.user_id
        except Exception as e:
            logger.debug(f"[AutoCoinSelector] _get_tenant_id failed: {e}")
        # 兜底:会话/账户查不到时不让审计写直接炸;返回 None 让调用方决定。
        return None

    # ── 审计记录 ────────────────────────────────────────────────────────

    def _write_audit_record(self, db: Session, symbol: str, action: str, **kwargs):
        """写入 AutoCoinSelection 审计表。

        阶段A 增强:
        - action="injected" 时记录 price_at_selection(反馈闭环起点);
        - 始终 stamp tenant_id(修 0004 NOT NULL 违约)。
        """
        try:
            from backend.database.models import AutoCoinSelection

            # 价格:仅 injected 时取,避免对 removed/skipped 做无谓行情拉取。
            price_at_selection = None
            if action == "injected":
                price_at_selection = kwargs.get("price_at_selection")
                if price_at_selection is None:
                    try:
                        price_at_selection = self._resolve_inject_price(symbol)
                    except Exception:
                        price_at_selection = None
                if price_at_selection is None:
                    logger.warning(
                        "[AutoCoinSelector] injected %s 未取到进场价，绩效闭环将缺失",
                        symbol,
                    )

            record = AutoCoinSelection(
                session_id=self.session_id,
                symbol=symbol,
                exchange=self._exchange,
                action=action,
                scanner_score=kwargs.get("scanner_score"),
                scanner_rank=kwargs.get("scanner_rank"),
                ai_confidence=kwargs.get("ai_confidence"),
                ai_reason=kwargs.get("ai_reason", "")[:500] if kwargs.get("ai_reason") else None,
                removal_reason=kwargs.get("removal_reason", "")[:500] if kwargs.get("removal_reason") else None,
                risk_note=kwargs.get("risk_note", "")[:500] if kwargs.get("risk_note") else None,
                tenant_id=self._get_tenant_id(db),
                price_at_selection=price_at_selection,
                factor_snapshot_json=kwargs.get("factor_snapshot"),
            )
            db.add(record)
            db.commit()
        except Exception as e:
            logger.warning(f"[AutoCoinSelector] Audit write failed for {symbol}/{action}: {e}")
            try:
                db.rollback()
            except Exception:
                pass

    # ── 评分引擎：交易表现查询 ─────────────────────────────────────────

    def _query_symbol_performance(self, db: Session, symbol: str) -> CoinPerformanceData:
        """查询币种在当前会话中的实际交易表现"""
        try:
            from backend.database.models import PaperOrder, AIStrategy, StrategyMemory
            session = db.query(self._get_session_model()).filter(
                self._get_session_model().session_id == self.session_id
            ).first()
            if not session:
                return CoinPerformanceData(symbol=symbol, is_new=True)

            paper_account_id = getattr(session, 'paper_account_id', None) or getattr(session, 'trading_account_id', None)
            if not paper_account_id:
                return CoinPerformanceData(symbol=symbol, is_new=True)

            # 查已平仓订单
            closed_orders = db.query(PaperOrder).filter(
                PaperOrder.account_id == paper_account_id,
                PaperOrder.symbol == symbol,
                PaperOrder.status == 'filled',
                PaperOrder.close_reason.isnot(None),
            ).all()

            # 持有时长
            entry = self._pool.active.get(symbol)
            holding_hours = 0.0
            if entry and entry.injected_at:
                holding_hours = (datetime.now() - entry.injected_at).total_seconds() / 3600

            if len(closed_orders) < 3 and holding_hours < AUTO_COIN_MIN_HOLD_HOURS:
                return CoinPerformanceData(symbol=symbol, is_new=True, holding_duration_hours=holding_hours)

            # 计算表现指标
            pnls = [float(o.pnl or 0) for o in closed_orders if o.pnl is not None]
            total_trades = len(pnls)
            win_count = sum(1 for p in pnls if p > 0)
            total_pnl = sum(pnls)
            avg_pnl = total_pnl / total_trades if total_trades > 0 else 0
            max_loss = min(pnls) if pnls else 0

            # Sharpe 估计
            sharpe = 0.0
            if len(pnls) >= 3:
                import statistics
                std = statistics.stdev(pnls)
                sharpe = (avg_pnl / std) if std > 0 else 0

            # 查策略最佳 win_rate
            best_wr = 0.0
            try:
                strategies = db.query(AIStrategy).filter(
                    AIStrategy.primary_symbol == symbol,
                    AIStrategy.account_id == self.account_id,
                ).all()
                for st in strategies:
                    mem = db.query(StrategyMemory).filter(
                        StrategyMemory.strategy_id == st.strategy_id
                    ).first()
                    if mem and mem.win_rate > best_wr:
                        best_wr = mem.win_rate
            except Exception:
                pass

            return CoinPerformanceData(
                symbol=symbol,
                total_trades=total_trades,
                win_count=win_count,
                win_rate=win_count / total_trades if total_trades > 0 else 0,
                total_pnl=total_pnl,
                avg_pnl_per_trade=avg_pnl,
                max_single_loss=max_loss,
                sharpe_estimate=sharpe,
                holding_duration_hours=holding_hours,
            )
        except Exception as e:
            logger.debug(f"[AutoCoinSelector] Performance query failed for {symbol}: {e}")
            return CoinPerformanceData(symbol=symbol, is_new=True)

    # ── 评分引擎：4 维度评分 ────────────────────────────────────────────

    def _compute_performance_score(self, perf: CoinPerformanceData) -> float:
        """交易表现 → 0-1 评分"""
        if perf.is_new:
            return 0.5  # 中性

        # 胜率因子
        if perf.total_trades == 0:
            wr = 0.5
        elif perf.win_rate >= 0.60:
            wr = 1.0
        elif perf.win_rate >= 0.45:
            wr = 0.5 + (perf.win_rate - 0.45) / 0.15 * 0.3
        elif perf.win_rate >= 0.30:
            wr = 0.2 + (perf.win_rate - 0.30) / 0.15 * 0.3
        else:
            wr = max(0, perf.win_rate / 0.30 * 0.2)

        # PnL 因子（相对化：avg_pnl / abs(max_loss or 1)）
        if perf.total_trades == 0:
            pnl_f = 0.5
        else:
            pnl_ratio = perf.avg_pnl_per_trade / max(abs(perf.max_single_loss), 0.001)
            if pnl_ratio > 1.0:
                pnl_f = 1.0
            elif pnl_ratio > 0:
                pnl_f = 0.5 + pnl_ratio * 0.5
            elif pnl_ratio > -1.0:
                pnl_f = 0.2 + (pnl_ratio + 1.0) * 0.3
            else:
                pnl_f = max(0, 0.2 + (pnl_ratio + 1.0) * 0.1)

        # 回撤因子
        dd = abs(perf.max_single_loss)
        if dd < 0.03:
            dd_f = 1.0
        elif dd < 0.08:
            dd_f = 0.5 + (0.08 - dd) / 0.05 * 0.4
        elif dd < 0.15:
            dd_f = 0.2 + (0.15 - dd) / 0.07 * 0.3
        else:
            dd_f = max(0, 0.2 - (dd - 0.15) * 2)

        # Sharpe 因子
        if perf.total_trades < 3:
            sh_f = 0.5
        elif perf.sharpe_estimate > 2.0:
            sh_f = 1.0
        elif perf.sharpe_estimate > 1.0:
            sh_f = 0.6 + (perf.sharpe_estimate - 1.0) * 0.4
        elif perf.sharpe_estimate > 0:
            sh_f = 0.3 + perf.sharpe_estimate * 0.3
        else:
            sh_f = max(0, 0.3 + perf.sharpe_estimate * 0.3)

        return 0.30 * wr + 0.30 * pnl_f + 0.20 * dd_f + 0.20 * sh_f

    def _compute_market_fit_score(self, symbol: str, db: Session) -> float:
        """市场适配度 → 0-1 评分（复用多维度评分）"""
        try:
            exchange = self.resolve_exchange(db)
            snapshot = self._fetch_market_snapshot(symbol, exchange)
            if not snapshot:
                return 0.5

            vol = float(snapshot.get("volume_24h", 0) or 0)
            vol_score = min(vol / 5_000_000, 1.0)

            price_change = float(snapshot.get("price_change_24h", 0) or 0)
            trend_score = 0.5 + max(-0.3, min(0.3, price_change * 2))

            vola = float(snapshot.get("volatility_24h", 0) or 0.04)
            vola_score = max(0, 1.0 - abs(vola - 0.04) * 10)

            fr = float(snapshot.get("funding_rate", 0) or 0)
            fund_score = max(0, 0.5 - fr * 5)

            mom_score = min(abs(price_change) / 0.15, 1.0)

            return (vol_score + trend_score + mom_score + vola_score + fund_score) / 5.0
        except Exception:
            return 0.5

    def _compute_retention_bonus(self, symbol: str, perf: CoinPerformanceData) -> float:
        """持有稳定性奖励 → 0-1"""
        eval_count = self._evaluation_count.get(symbol, 0)

        # 保护期内：最大保护
        if eval_count < AUTO_COIN_GRACE_CYCLES:
            return 0.9

        # 持有时长奖励
        hours = perf.holding_duration_hours
        if hours > 12:
            hold_bonus = 0.7
        elif hours > 6:
            hold_bonus = 0.5
        elif hours > 3:
            hold_bonus = 0.3
        else:
            hold_bonus = 0.1

        return min(0.1 + hold_bonus, 1.0)

    def _compute_diversity_score(self, symbol: str, all_pool_symbols: List[str]) -> float:
        """多样性评分 → 0-1"""
        from backend.services.auto_coin_sectors import get_diversity_score
        return get_diversity_score(symbol, all_pool_symbols)

    def _compute_retention_score(self, symbol: str, db: Session, all_pool_symbols: List[str]) -> RetentionScore:
        """编排 4 维度，计算加权留存评分"""
        perf = self._query_symbol_performance(db, symbol)
        perf_score = self._compute_performance_score(perf)
        market_score = self._compute_market_fit_score(symbol, db)
        retention_score = self._compute_retention_bonus(symbol, perf)
        diversity_score = self._compute_diversity_score(symbol, all_pool_symbols)

        composite = (
            AUTO_COIN_PERF_WEIGHT * perf_score +
            AUTO_COIN_MARKET_WEIGHT * market_score +
            AUTO_COIN_RETENTION_WEIGHT * retention_score +
            AUTO_COIN_DIVERSITY_WEIGHT * diversity_score
        )

        # 生成风险备注
        notes = []
        if perf_score < 0.3:
            notes.append(f"表现差({perf_score:.2f})")
        if market_score < 0.3:
            notes.append(f"市场弱({market_score:.2f})")
        if diversity_score >= 1.0:
            notes.append("板块唯一代表")
        if perf.is_new:
            notes.append("新币保护期")

        return RetentionScore(
            symbol=symbol,
            performance_score=perf_score,
            market_fit_score=market_score,
            retention_bonus=retention_score,
            diversity_score=diversity_score,
            composite_score=composite,
            removal_risk_note="; ".join(notes) if notes else "正常",
        )

    # ── 评分引擎结束 ────────────────────────────────────────────────────

    def _get_session_model(self):
        from backend.database.models import FullAutoSession
        return FullAutoSession

    def _resolve_inject_price(self, symbol: str) -> Optional[float]:
        """注入时刻进场价：优先本机 ticker，必要时 ensure / 数据中心 / 快照。"""
        sym = str(symbol or "").upper().split("-")[0].split("/")[0]
        if not sym:
            return None
        # 1) Asterdex ticker（standalone 下 API 可能空 → ensure）
        try:
            from backend.services.asterdex_ticker_poller import asterdex_ticker_poller

            px = asterdex_ticker_poller.get_price(sym)
            if px and float(px) > 0:
                return float(px)
            st = asterdex_ticker_poller.get_stats(sym) or {}
            if st.get("price") and float(st["price"]) > 0:
                return float(st["price"])
            if len(asterdex_ticker_poller.get_all_prices()) < 20:
                asterdex_ticker_poller.ensure_snapshot(max_age_sec=60, fan_out=True)
                px = asterdex_ticker_poller.get_price(sym)
                if px and float(px) > 0:
                    return float(px)
                st = asterdex_ticker_poller.get_stats(sym) or {}
                if st.get("price") and float(st["price"]) > 0:
                    return float(st["price"])
        except Exception:
            pass
        # 2) 市场快照（volume 门槛放宽）
        try:
            snap = self._fetch_market_snapshot(sym, self._exchange or "asterdex")
            if snap and snap.get("price") and float(snap["price"]) > 0:
                return float(snap["price"])
        except Exception:
            pass
        # 3) data_center 最近收盘
        try:
            from backend.services.data_center import data_center

            kr = data_center.get_klines(sym, "1m", count=2, exchange="asterdex", purpose="research")
            if kr and kr.count > 0 and kr.rows:
                close = kr.rows[-1].get("close") or kr.rows[-1].get("close_price")
                if close and float(close) > 0:
                    return float(close)
        except Exception:
            pass
        return None

    def _fetch_market_snapshot(self, symbol: str, exchange: str) -> Optional[Dict[str, Any]]:
        """获取单个币种的市场快照数据"""
        try:
            # M1/M4 收口：优先走数据中心（ticker 内存快照 + K线门面），
            # 避免选币扫描逐个币直连交易所（500 币 ≈ 20s）
            dc_snap = self._fetch_dc_snapshot(symbol)
            if dc_snap and (dc_snap.get("price") or dc_snap.get("volume_24h")):
                return dc_snap
            # [2026-08-04 DC_ONLY] 数据中心唯一数据源：DC_ONLY 下禁止直连兜底，
            # 防止选币扫描绕过数据中心直连 HL/ccxt 交易所。
            from backend.services.market_data import _dc_only_enabled
            if _dc_only_enabled():
                return None
            if exchange == "hyperliquid":
                return self._fetch_hl_snapshot(symbol)
            else:
                return self._fetch_ccxt_snapshot(symbol, exchange)
        except Exception as e:
            logger.debug(f"[AutoCoinSelector] Market fetch error for {symbol}@{exchange}: {e}")
            return None

    def _fetch_dc_snapshot(self, symbol: str) -> Optional[Dict[str, Any]]:
        """数据中心快照：ticker 24h 统计（内存，2s/5s 新鲜）+ K线门面算 4h 动量。"""
        sym = str(symbol or "").upper().split("-")[0].split("/")[0]
        try:
            from backend.services.asterdex_ticker_poller import asterdex_ticker_poller

            st = asterdex_ticker_poller.get_stats(sym) or {}
            if not st or not (st.get("price") or st.get("quote_volume_24h")):
                try:
                    if len(asterdex_ticker_poller.get_all_stats()) < 20:
                        asterdex_ticker_poller.ensure_snapshot(max_age_sec=60, fan_out=False)
                    st = asterdex_ticker_poller.get_stats(sym) or {}
                except Exception:
                    pass
            px = float(st.get("price") or 0)
            if px <= 0:
                try:
                    raw = asterdex_ticker_poller.get_price(sym)
                    px = float(raw or 0)
                except Exception:
                    px = 0.0
            if px <= 0 and not st.get("quote_volume_24h"):
                return None
            snap: Dict[str, Any] = {
                "volume_24h": float(st.get("quote_volume_24h") or 0),
                "price_change_24h": float(st.get("change_pct") or 0),
                "price": px,
                "high_24h": float(st.get("high_24h") or 0),
                "low_24h": float(st.get("low_24h") or 0),
                "price_change_4h": None,
                "volatility_24h": None,
                "funding_rate": None,
            }
            # 4h 动量 + 波动率：经 K线统一门面（data_center）
            try:
                from backend.services.kline_data_service import kline_service
                kl = kline_service.query_klines(sym, "1h", limit=6, order="asc")
                if kl and len(kl) >= 2:
                    closes = [float(k["close"]) for k in kl]
                    base = closes[0] if closes[0] > 0 else closes[-1]
                    snap["price_change_4h"] = (closes[-1] / base - 1.0) * 100
                    if len(closes) >= 4:
                        import numpy as _np
                        rets = _np.diff(_np.log(_np.array(closes, dtype=float)))
                        snap["volatility_24h"] = min(0.3, float(_np.std(rets)))
            except Exception:
                pass
            return snap
        except Exception:
            return None

    @staticmethod
    def _build_hl_snapshot_cache() -> Dict[str, Dict[str, Any]]:
        # [2026-08-04 DC_ONLY] 数据中心唯一数据源：DC_ONLY 下禁止直连 HL
        # metaAndAssetCtxs 构建快照（选币行情一律走数据中心）。
        try:
            from backend.services.market_data import _dc_only_enabled
            if _dc_only_enabled():
                logger.info(
                    "[AutoCoinSelector] DC_ONLY: 跳过 HL 直连快照，改读数据中心"
                )
                return {}
        except Exception:
            pass
        import requests as _req
        result: Dict[str, Dict[str, Any]] = {}
        data = None
        for attempt in range(3):
            try:
                resp = _req.post(
                    "https://api.hyperliquid.xyz/info",
                    json={"type": "metaAndAssetCtxs"},
                    timeout=15,
                )
                if resp.status_code == 429:
                    wait = (attempt + 1) * 5
                    logger.warning(f"[AutoCoinSelector] HL API rate limited, waiting {wait}s (attempt {attempt+1}/3)")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                break
            except Exception as e:
                if attempt < 2:
                    logger.warning(f"[AutoCoinSelector] HL API error (attempt {attempt+1}/3): {e}, retrying in 3s")
                    time.sleep(3)
                else:
                    logger.error(f"[AutoCoinSelector] HL API failed after 3 attempts: {e}")
                    return result

        if data is None:
            return result

        try:
            universe = data[0].get("universe", []) if isinstance(data, list) and data else []
            ctxs = data[1] if isinstance(data, list) and len(data) > 1 else []

            for i, entry in enumerate(universe):
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("name", "")).upper()
                if not name:
                    continue
                ctx = ctxs[i] if i < len(ctxs) else {}
                snapshot: Dict[str, Any] = {}
                if isinstance(ctx, dict):
                    snapshot["price"] = float(ctx.get("markPx", 0) or ctx.get("oraclePx", 0))
                    snapshot["volume_24h"] = float(ctx.get("dayNtlVlm", 0))
                    snapshot["funding_rate"] = float(ctx.get("funding", 0))
                    snapshot["open_interest"] = float(ctx.get("openInterest", 0))
                    if "prevDayPx" in ctx:
                        prev = float(ctx["prevDayPx"])
                        if prev > 0 and snapshot.get("price"):
                            snapshot["price_change_24h"] = (snapshot["price"] - prev) / prev
                    if "dayBaseVlm" in ctx and "prevDayBaseVlm" in ctx:
                        cur = float(ctx.get("dayBaseVlm", 0))
                        prev_day = float(ctx.get("prevDayBaseVlm", 0))
                        if prev_day > 0:
                            snapshot["volume_change_24h"] = (cur - prev_day) / prev_day
                result[name] = snapshot
        except Exception as e:
            logger.warning(f"[AutoCoinSelector] Failed to build HL snapshot cache: {e}")
        return result

    @staticmethod
    def _fetch_hl_candles_single(self, symbol: str) -> Optional[list]:
        """按需拉取单个 symbol 的 1h K 线（DB 优先 → API 回退）。

        修复 _build_hl_candles_cache 从未被调用导致趋势评分恒 0.5 的问题。
        """
        # 优先从 DB 读（kline_realtime_collector 已在持续采集）
        try:
            from backend.services.kline_data_service import kline_service
            raw = kline_service.get_klines_from_db(symbol, "1h", 100)
            if raw and len(raw) >= 20:
                return [{"o": r.get("open", 0), "c": r.get("close", 0),
                         "h": r.get("high", 0), "l": r.get("low", 0),
                         "v": r.get("volume", 0), "t": r.get("time")}
                        for r in raw]
        except Exception:
            pass
        # DB 不足 → 直接 API 拉取
        try:
            import requests as _req
            for attempt in range(2):
                try:
                    resp = _req.post(
                        "https://api.hyperliquid.xyz/info",
                        json={
                            "type": "candleSnapshot",
                            "req": {"coin": symbol, "interval": "1h", "limit": 100},
                        },
                        timeout=10,
                    )
                    data = resp.json()
                    if isinstance(data, list) and len(data) >= 20:
                        return data
                except Exception:
                    pass
                if attempt < 1:
                    time.sleep(1.0)
        except Exception:
            pass
        return None

    def _build_hl_candles_cache(symbols: List[str]) -> Dict[str, list]:
        import requests as _req
        result: Dict[str, list] = {}
        if not symbols:
            return result

        def _fetch_one(symbol):
            for attempt in range(3):
                try:
                    resp = _req.post(
                        "https://api.hyperliquid.xyz/info",
                        json={
                            "type": "candleSnapshot",
                            "req": {"coin": symbol, "interval": "1h", "limit": 50},
                        },
                        timeout=15,
                    )
                    data = resp.json()
                    if isinstance(data, list) and len(data) >= 20:
                        return symbol, data
                except Exception:
                    pass
                if attempt < 2:
                    time.sleep(2.0 * (attempt + 1))
            return symbol, None

        for i, symbol in enumerate(symbols):
            sym, data = _fetch_one(symbol)
            if data is not None:
                result[sym] = data
            if i < len(symbols) - 1:
                time.sleep(1.5)
        return result

    def _fetch_hl_snapshot(self, symbol: str) -> Optional[Dict[str, Any]]:
        global _hl_snapshot_cache
        now = time.time()
        if _hl_snapshot_cache and (now - _hl_snapshot_cache[0]) < _HL_CACHE_TTL:
            return _hl_snapshot_cache[1].get(symbol)
        cache = self._build_hl_snapshot_cache()
        _hl_snapshot_cache = (now, cache)
        return cache.get(symbol)

    def _fetch_ccxt_snapshot(self, symbol: str, exchange: str) -> Optional[Dict[str, Any]]:
        """通过 CCXT 获取市场快照（binance/bybit/okx/gateio/asterdex）"""
        try:
            from backend.services.exchange.exchange_factory import ExchangeClientFactory
            client = ExchangeClientFactory.create(exchange, api_key="", secret="")
            ccxt_exchange = getattr(client, "_exchange", None)
            if ccxt_exchange is None:
                return None

            async def _fetch():
                pair = f"{symbol}/USDT"
                try:
                    ticker = await ccxt_exchange.fetch_ticker(pair)
                    return {
                        "price": ticker.get("last"),
                        "volume_24h": ticker.get("quoteVolume"),
                        "price_change_24h": ticker.get("percentage", 0) / 100 if ticker.get("percentage") else None,
                        "funding_rate": ticker.get("info", {}).get("fundingRate") if ticker.get("info") else None,
                    }
                except Exception:
                    return None

            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            return loop.run_until_complete(_fetch())
        except Exception:
            return None

    def _assess_trend(self, symbol: str, exchange: str) -> float:
        """评估趋势强度，返回 0.0-1.0"""
        try:
            if exchange == "hyperliquid":
                return self._assess_hl_trend(symbol)
            return 0.5
        except Exception:
            return 0.5

    def _assess_hl_trend(self, symbol: str) -> float:
        """评估短线+中线趋势强度(0~1)，不做长线。

        短线: 1h K线 MA7 vs MA25 (~7h vs ~1天)
        中线: 4h K线 MA12 vs MA25 (~2天 vs ~4天)
        权重: 短线 60% + 中线 40%
        长期(日线 MA50+)不纳入评分体系，仅保留在后续"市场背景"中可手动参考。
        """
        global _hl_candles_cache
        now = time.time()
        candles = None
        # 先查全局缓存
        if _hl_candles_cache and (now - _hl_candles_cache[0]) < _HL_CACHE_TTL:
            candles = _hl_candles_cache[1].get(symbol)

        # 缓存未命中 → 直接按需拉取当前 symbol 的 K 线（不再依赖全局缓存构建）
        if candles is None:
            candles = self._fetch_hl_candles_single(symbol)

        if not candles or len(candles) < 20:
            return 0.5

        closes = [float(c.get("c", 0)) for c in candles if isinstance(c, dict)]
        if len(closes) < 20:
            return 0.5

        # ── 短线评分 (1h MA7 vs MA25) ──
        ma7 = sum(closes[-7:]) / 7
        ma25_window = closes[-25:] if len(closes) >= 25 else closes
        ma25 = sum(ma25_window) / len(ma25_window) if ma25_window else closes[-1]

        short_score = 0.5
        if ma7 > ma25 * 1.02:
            ema = closes[-1]
            for c in closes[-10:]:
                ema = ema * 0.85 + c * 0.15
            if closes[-1] > ema:
                short_score = 0.85
            else:
                short_score = 0.70
        elif ma7 < ma25 * 0.98:
            short_score = 0.30

        # ── 中线评分 (4h: 取每 4 根 1h K线的收盘均价构造 4h 等效) ──
        mid_score = 0.5
        if len(closes) >= 100:  # 至少 100 根 1h = 25 根 4h
            h4_closes = []
            for i in range(4, len(closes) + 1, 4):
                chunk = closes[max(0, i - 4):i]
                if chunk:
                    h4_closes.append(sum(chunk) / len(chunk))
            if len(h4_closes) >= 25:
                h4_ma12 = sum(h4_closes[-12:]) / 12
                h4_ma25 = sum(h4_closes[-25:]) / 25
                if h4_ma12 > h4_ma25 * 1.02:
                    mid_score = 0.80
                elif h4_ma12 < h4_ma25 * 0.98:
                    mid_score = 0.30
                else:
                    mid_score = 0.50

        # ── 加权综合：短线 60% + 中线 40% ──
        return round(short_score * 0.6 + mid_score * 0.4, 3)

    def _fetch_onchain_data(self, symbol: str) -> Dict[str, Any]:
        """获取链上/衍生品数据，用于增强选币 AI 审核维度。

        数据来源（全部系统已有，非外部付费 API）：
        - OI 变化（Hyperliquid assetCtx → openInterest 变化率）
        - 资金费率偏离度（当前费率 vs 历史均值）
        - 鲸鱼大单方向（aggregate_whale_collector 近期大单净方向）
        """
        result: Dict[str, Any] = {}
        try:
            # 1. 从 HL 快照取 OI + funding（已有缓存）
            snap = self._fetch_hl_snapshot(symbol)
            if snap:
                oi = snap.get("openInterest") or snap.get("open_interest")
                if oi:
                    result["open_interest"] = float(oi)
                # funding rate
                ctx = snap.get("funding") or snap.get("funding_rate")
                if ctx is not None:
                    result["funding_rate"] = float(ctx)
                # price + volume 供 AI 参考
                result["mark_price"] = snap.get("markPrice") or snap.get("price")
                result["volume_24h"] = snap.get("volume_24h")
        except Exception:
            pass

        # 2. 鲸鱼大单方向（读聚合采集器缓存；无数据时显式 available=False）
        try:
            from backend.services.market_aggregation.aggregate_whale_collector import (
                aggregate_whale_collector,
            )
            whale_data = aggregate_whale_collector.get_recent_trades_summary(symbol, limit=20)
            if whale_data and whale_data.get("available"):
                result["whale_net_direction"] = whale_data.get("net_direction", "neutral")
                result["whale_buy_volume"] = whale_data.get("buy_volume", 0)
                result["whale_sell_volume"] = whale_data.get("sell_volume", 0)
                result["whale_confidence"] = whale_data.get("confidence", 0)
                result["whale_available"] = True
            else:
                result["whale_available"] = False
        except Exception:
            result["whale_available"] = False

        # 3. OI 变化率（从 DB 历史对比，如果有）
        try:
            from backend.database.connection import SessionLocal as _SL
            from sqlalchemy import text as _sql_text
            _db = _SL()
            try:
                _row = _db.execute(_sql_text(
                    "SELECT open_interest FROM oi_history "
                    "WHERE symbol = :sym ORDER BY ts DESC LIMIT 2"
                ), {"sym": symbol}).fetchall()
                if len(_row) >= 2:
                    oi_now = float(_row[0][0] or 0)
                    oi_prev = float(_row[1][0] or 0)
                    if oi_prev > 0:
                        result["oi_change_pct"] = round((oi_now - oi_prev) / oi_prev * 100, 2)
            finally:
                _db.close()
        except Exception:
            pass

        # 4. CVD / OI_DELTA（市场流指标；失败静默）
        try:
            from backend.services.market_flow_indicators import get_indicator_value
            cvd = get_indicator_value(None, symbol, "CVD", "15m")
            if cvd is not None:
                # 归一化到大约 -1~1（按量级裁剪）
                result["cvd_raw"] = float(cvd)
                result["cvd_direction"] = max(-1.0, min(1.0, float(cvd) / 1_000_000.0))
            oi_delta = get_indicator_value(None, symbol, "OI_DELTA", "1h")
            if oi_delta is not None and result.get("oi_change_pct") is None:
                result["oi_change_pct"] = float(oi_delta)
        except Exception:
            pass

        return result

    def _fetch_news(self, symbol: str) -> Optional[Dict[str, Any]]:
        """获取新闻和市场情绪（NewsIntelligenceService.get_symbol_sentiment）。

        M0：修正错误模块名；无新闻时 available=False，不再用涨跌伪情绪冒充新闻。
        """
        hours = int(os.getenv("AUTO_COIN_NEWS_HOURS", "24"))
        try:
            from backend.services.news_intelligence_service import NewsIntelligenceService
            svc = NewsIntelligenceService()
            data = svc.get_symbol_sentiment(symbol, hours=hours)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.debug(f"[AutoCoinSelector] news fetch skip {symbol}: {e}")
        return {
            "sentiment": 0.0,
            "sentiment_label": "neutral",
            "social_volume": 0,
            "top_events": [],
            "freshness_min": None,
            "available": False,
        }

    def _compute_onchain_deltas(self, candidate: CandidateCoin) -> Dict[str, float]:
        """计算链上/衍生品数据的变化率。"""
        deltas: Dict[str, float] = {}
        try:
            # M0 修复：字段是 onchain_data，不是不存在的 extra_data
            data = candidate.onchain_data or {}
            if isinstance(data, dict):
                if data.get("oi_change_pct") is not None:
                    deltas["oi_delta"] = float(data["oi_change_pct"])
                whale_dir = data.get("whale_net_direction", "")
                if whale_dir == "buy":
                    deltas["whale_signal"] = 1.0
                elif whale_dir == "sell":
                    deltas["whale_signal"] = -1.0
        except Exception:
            pass
        return deltas

    def _llm_compose_caller(self):
        """S2-9：LLM 组合决策的同步 caller（包装 async call_llm_api）。

        与 ``_call_ai`` 同款配置源（coin_select / deep），失败时抛异常由
        ``ic_weights.llm_compose`` 兜住回退规则路径。
        """
        import asyncio

        def _call(prompt: str) -> str:
            from backend.services.llm_config_service import get_llm_config_for_usage, call_llm_api
            llm_cfg = get_llm_config_for_usage("coin_select", account_id=self.account_id, tier="deep")
            if not llm_cfg or not getattr(llm_cfg, "api_key", None):
                raise RuntimeError("no llm api key")

            async def _invoke():
                return await call_llm_api(
                    config=llm_cfg,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                    max_tokens=300,
                )

            # S2-9 修复：Cycle 在 async 事件循环内同步调用本 caller 时，
            # asyncio.run() 会抛 "cannot be called from a running event loop"；
            # Python 3.12 亦禁止同线程嵌套运行另一事件循环（
            # "Cannot run the event loop while another loop is running"）。
            # 故检测到 running loop 时转到独立线程执行 —— 线程内无 running
            # loop，asyncio.run 合法；llm_config_service 的 httpx 客户端按
            # loop_id 索引缓存，线程内新建 loop 自动重建 client，安全。
            try:
                asyncio.get_running_loop()
                import concurrent.futures as _cf
                with _cf.ThreadPoolExecutor(max_workers=1, thread_name_prefix="llm-compose") as _ex:
                    resp_data = _ex.submit(lambda: asyncio.run(_invoke())).result()
            except RuntimeError:
                resp_data = asyncio.run(_invoke())
            if not resp_data:
                raise RuntimeError("empty llm response")
            choices = resp_data.get("choices", [])
            if not choices:
                raise RuntimeError("no llm choices")
            return (choices[0].get("message", {}).get("content") or "").strip()

        return _call

    async def _call_ai(self, llm_cfg, prompt: str) -> Dict[str, Any]:
        """调用 AI API 进行审核。

        阶段C：解析三层渐进框架的输出（layer + test_position）。
        - layer A/B/C → 全仓位通过（test_position=False）
        - layer D/E   → 试仓通过（test_position=True，50% 仓位）
        - layer F/G   → 拒绝（approved=False）
        若 LLM 漏掉 layer 字段，则退回 approved 布尔。
        """
        import json

        try:
            from backend.services.llm_config_service import call_llm_api
            resp_data = await call_llm_api(
                config=llm_cfg,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=300,
            )

            if not resp_data:
                raise ValueError("Empty AI response")

            choices = resp_data.get("choices", [])
            if not choices:
                raise ValueError("No choices in AI response")
            response_text = choices[0].get("message", {}).get("content", "")
            if not response_text:
                raise ValueError("Empty AI content")

            response_text = response_text.strip()
            if response_text.startswith("```"):
                lines = response_text.split("\n")
                response_text = "\n".join(lines[1:-1] if len(lines) > 2 else lines)

            result = json.loads(response_text)
            return self._normalize_ai_result(result)
        except json.JSONDecodeError:
            logger.warning(f"[AutoCoinSelector] AI response parse error, raw: {response_text[:100] if 'response_text' in dir() else 'N/A'}")
            return {"approved": False, "reason": "Parse error", "confidence": 0.0,
                    "layer": "", "test_position": False}
        except Exception as e:
            logger.warning(f"[AutoCoinSelector] AI call failed: {e}")
            return {"approved": False, "reason": f"Error: {e}", "confidence": 0.0,
                    "layer": "", "test_position": False}

    @staticmethod
    def _normalize_ai_result(result: Dict[str, Any]) -> Dict[str, Any]:
        """将 LLM 返回的三层框架 JSON 规整为内部统一字段。

        保证 approved/test_position 与 layer 语义一致：
          - LAYER 1 (A/B/C) → approved=True,  test_position=False
          - LAYER 2 (D/E)   → approved=True,  test_position=True
          - LAYER 3 (F/G)   → approved=False, test_position=False
        若 layer 缺失，则尊重 LLM 给的 approved/test_position 原值（向后兼容）。
        """
        layer = str(result.get("layer", "") or "").strip().upper()[:1]
        approved = bool(result.get("approved", False))
        test_position = bool(result.get("test_position", False))

        if layer in ("A", "B", "C"):
            approved = True
            test_position = False
        elif layer in ("D", "E"):
            approved = True
            test_position = True
        elif layer in ("F", "G"):
            approved = False
            test_position = False

        conf_raw = result.get("confidence", 0.5)
        try:
            confidence = float(conf_raw)
        except (TypeError, ValueError):
            confidence = 0.5

        return {
            "approved": approved,
            "reason": str(result.get("reason", "")),
            "confidence": confidence,
            "layer": layer,
            "test_position": test_position,
        }


# ═══════════════════════════════════════════════════════════════════════════
# 调度器: 管理所有活跃会话的选币实例
# ═══════════════════════════════════════════════════════════════════════════

class AutoCoinScheduler:
    """
    自动选币调度器。

    管理所有开启了自动选币的交易会话的 AutoCoinSelector 实例，
    按配置的扫描间隔定期触发选币周期。

    M3：AUTO_COIN_MULTI_LANE_ENABLED=true 时启用 Fast/Normal/Full 三档；
    默认 false 保持单一 Full 循环（旧行为）。
    """

    def __init__(self, db_session_factory=None):
        self._selectors: Dict[str, AutoCoinSelector] = {}
        self._db_factory = db_session_factory
        self._running = False
        self._interval = AUTO_COIN_SCAN_INTERVAL
        self._task: Optional[asyncio.Task] = None
        self._last_lane: str = "full"
        self._busy: bool = False
        self._last_full_ts: float = 0.0
        self._last_normal_ts: float = 0.0
        self._last_fast_ts: float = 0.0

    def register_session(self, session_id: str, account_id: int, db_session_factory=None):
        """注册一个需要自动选币的会话"""
        if session_id in self._selectors:
            selector = self._selectors[session_id]
            selector.account_id = account_id
            selector._exchange = None
            selector._cancel_requested = False
            logger.info(f"[AutoCoinScheduler] Updated session {session_id} (account {account_id})")
        else:
            self._selectors[session_id] = AutoCoinSelector(
                session_id=session_id,
                account_id=account_id,
                db_session_factory=db_session_factory or self._db_factory,
            )
            logger.info(f"[AutoCoinScheduler] Registered session {session_id} (account {account_id})")

        return self._selectors[session_id]

    def unregister_session(self, session_id: str):
        if session_id in self._selectors:
            self._selectors[session_id]._cancel_requested = True
            del self._selectors[session_id]
            logger.info(f"[AutoCoinScheduler] Unregistered session {session_id}")

    def get_selection_meta(self, session_id: str) -> Dict[str, Dict[str, Any]]:
        """供交易决策链路查询：当前注入币种的选币评分/AI置信度/理由。
        用于在 Master prompt 中标注 AI 自动选币，并执行更严的仓位与门禁。"""
        sel = self._selectors.get(session_id)
        if not sel:
            return {}
        out: Dict[str, Dict[str, Any]] = {}
        try:
            from backend.services.auto_coin_policy import is_training_core_symbol
            for sym, c in sel._pool.active.items():
                if is_training_core_symbol(sym):
                    continue
                out[str(sym).upper()] = {
                    "score": round(float(c.score or 0), 3),
                    "ai_confidence": round(float(c.ai_confidence or 0), 3),
                    "ai_reason": (c.ai_reason or "")[:120],
                    # 阶段C：LAYER 2 试仓标记，下游缩仓至 PROBE_SIZE_MULT
                    "test_position": bool(getattr(c, "test_position", False)),
                    "ai_layer": str(getattr(c, "ai_layer", "") or ""),
                    "injected_at": c.injected_at.isoformat() if c.injected_at else None,
                }
        except Exception as e:
            logger.debug(f"[AutoCoinScheduler] get_selection_meta skip: {e}")
        return out

    async def start(self):
        if self._running:
            logger.warning("[AutoCoinScheduler] 已经在运行中")
            return
        self._running = True
        self._task = asyncio.ensure_future(self._run_loop())
        logger.info(f"[AutoCoinScheduler] 启动，扫描间隔={self._interval}s")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("[AutoCoinScheduler] 已停止")

    def _multi_lane_enabled(self) -> bool:
        try:
            from backend.config.settings import (
                AUTO_COIN_MULTI_LANE_ENABLED,
                PAPER_AUTO_COIN_MULTI_LANE,
            )
            if bool(AUTO_COIN_MULTI_LANE_ENABLED):
                return True
            # Paper 默认开三车道（全面升级 P2）
            if bool(PAPER_AUTO_COIN_MULTI_LANE) and self._selectors:
                return True
            return False
        except Exception:
            return os.getenv("AUTO_COIN_MULTI_LANE_ENABLED", "false").lower() in (
                "1", "true", "yes", "on",
            )

    async def _execute_session_cycle(
        self,
        sid: str,
        executor,
        loop,
        lane: str = "full",
        focus_symbols: Optional[List[str]] = None,
        force_ai: bool = False,
    ):
        selector = self._selectors.get(sid)
        if not selector:
            return
        def _run_cycle_in_thread():
            from backend.core.tenant import set_system_identity
            from backend.database.connection import SessionLocal

            # [RLS] 后台线程不继承 ContextVar；不设身份则 RLS 把 accounts/
            # full_auto_sessions 全部过滤 → “Session not found” → 注入停摆 28 天
            # （2026-08-06 审计实锤）。选币调度线程必须穿透租户隔离。
            set_system_identity()
            db = SessionLocal()
            try:
                return asyncio.run(
                    selector.run_selection_cycle(
                        db,
                        lane=lane,
                        focus_symbols=focus_symbols,
                        force_ai=force_ai,
                    )
                )
            finally:
                db.close()
        await loop.run_in_executor(executor, _run_cycle_in_thread)

    async def _run_loop(self):
        import concurrent.futures
        # 0 = 不人为限流，给足线程（多账户注册后也能并行）
        try:
            from backend.config.settings import AUTO_COIN_PARALLEL_SESSIONS
            _mw = int(AUTO_COIN_PARALLEL_SESSIONS)
        except Exception:
            _mw = int(os.getenv("AUTO_COIN_PARALLEL_SESSIONS", "0"))
        _workers = 64 if _mw <= 0 else max(1, _mw)
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=_workers)
        loop = asyncio.get_running_loop()
        while self._running:
            try:
                if not self._multi_lane_enabled():
                    await self._run_legacy_full_round(executor, loop)
                    await asyncio.sleep(self._interval)
                    continue

                # ── M3 三档调度 ──
                from backend.config.settings import (
                    AUTO_COIN_FAST_FORCE_AI,
                    AUTO_COIN_FAST_INTERVAL_SEC,
                    AUTO_COIN_FAST_MIN_GAP_SEC,
                    AUTO_COIN_NORMAL_INTERVAL_SEC,
                )
                from backend.services.auto_coin_events import auto_coin_event_bus
                from backend.services.auto_coin_sector_signal import get_watchlist

                now = time.time()
                session_ids = list(self._selectors.keys())
                if not session_ids:
                    await asyncio.sleep(min(30, AUTO_COIN_FAST_INTERVAL_SEC))
                    continue

                if self._busy:
                    await asyncio.sleep(5)
                    continue

                # Full 优先 —— 多 session 并行（通道数见 AUTO_COIN_PARALLEL_SESSIONS）
                if now - self._last_full_ts >= self._interval:
                    self._busy = True
                    self._last_lane = "full"
                    try:
                        logger.info(f"[AutoCoinScheduler] lane=full sessions={len(session_ids)}")
                        await asyncio.gather(*[
                            self._execute_session_cycle(sid, executor, loop, lane="full")
                            for sid in session_ids
                        ], return_exceptions=True)
                        self._last_full_ts = time.time()
                    finally:
                        self._busy = False
                elif now - self._last_normal_ts >= AUTO_COIN_NORMAL_INTERVAL_SEC:
                    watch = get_watchlist()
                    focus = list({*watch, *[
                        s for sel in self._selectors.values() for s in sel._pool.active.keys()
                    ]})
                    self._busy = True
                    self._last_lane = "normal"
                    try:
                        logger.info(
                            f"[AutoCoinScheduler] lane=normal focus={len(focus)} "
                            f"sessions={len(session_ids)}"
                        )
                        await asyncio.gather(*[
                            self._execute_session_cycle(
                                sid, executor, loop, lane="normal", focus_symbols=focus or None,
                            )
                            for sid in session_ids
                        ], return_exceptions=True)
                        self._last_normal_ts = time.time()
                    finally:
                        self._busy = False
                elif (
                    now - self._last_fast_ts >= max(AUTO_COIN_FAST_INTERVAL_SEC, AUTO_COIN_FAST_MIN_GAP_SEC)
                    and auto_coin_event_bus.size() > 0
                ):
                    focus = auto_coin_event_bus.pending_symbols(limit=20)
                    if focus:
                        force = bool(AUTO_COIN_FAST_FORCE_AI) and auto_coin_event_bus.can_force_ai()
                        self._busy = True
                        self._last_lane = "fast"
                        try:
                            logger.info(
                                f"[AutoCoinScheduler] lane=fast focus={focus} force_ai={force}"
                            )
                            await asyncio.gather(*[
                                self._execute_session_cycle(
                                    sid, executor, loop,
                                    lane="fast",
                                    focus_symbols=focus,
                                    force_ai=force,
                                )
                                for sid in session_ids
                            ], return_exceptions=True)
                            if force:
                                auto_coin_event_bus.mark_force_ai()
                            self._last_fast_ts = time.time()
                        finally:
                            self._busy = False

                await asyncio.sleep(min(30, AUTO_COIN_FAST_INTERVAL_SEC))
            except Exception as e:
                logger.error(f"[AutoCoinScheduler] 循环异常: {e}")
                await asyncio.sleep(self._interval)

    async def _run_legacy_full_round(self, executor, loop):
        session_ids = list(self._selectors.keys())
        if not session_ids:
            logger.debug("[AutoCoinScheduler] 无活跃会话，等待下一轮")
            return
        logger.info(f"[AutoCoinScheduler] 开始扫描 {len(session_ids)} 个活跃会话")
        self._last_lane = "full"
        for sid in session_ids:
            try:
                await self._execute_session_cycle(sid, executor, loop, lane="full")
            except Exception as e:
                logger.error(f"[AutoCoinScheduler] 会话 {sid} 扫描失败: {e}")
        self._last_full_ts = time.time()

    async def trigger_scan_now(self, session_id: str):
        selector = self._selectors.get(session_id)
        if not selector:
            raise ValueError(f"会话 {session_id} 未注册自动选币")
        import concurrent.futures
        loop = asyncio.get_running_loop()
        def _run_in_thread():
            from backend.core.tenant import set_system_identity
            from backend.database.connection import SessionLocal

            # [RLS] 同 _run_cycle_in_thread：手动触发同样需要穿透租户隔离。
            set_system_identity()
            db = SessionLocal()
            try:
                return asyncio.run(selector.run_selection_cycle(db))
            finally:
                db.close()
        return await loop.run_in_executor(None, _run_in_thread)

    def is_auto_coin_symbol(self, symbol: str, session_id: str = None) -> bool:
        """检查某个 symbol 是否为 AI 自动选出的币种。
        
        Args:
            symbol: 币种名称（大小写不敏感）
            session_id: 可选，限定到特定会话；不传则检查所有会话
        """
        sym = str(symbol).upper()
        if session_id:
            sel = self._selectors.get(session_id)
            if sel:
                return sym in {s.upper() for s in sel._auto_symbols}
            return False
        for sel in self._selectors.values():
            if sym in {s.upper() for s in sel._auto_symbols}:
                return True
        return False

    def get_session_selector(self, session_id: str) -> Optional["AutoCoinSelector"]:
        return self._selectors.get(session_id)

    def get_status(self, session_id: str) -> Optional[dict]:
        selector = self._selectors.get(session_id)
        if not selector:
            return None
        pool = selector._pool
        auto_symbols = list(pool.active.keys()) if pool else []
        pool_dict = pool.to_dict() if pool else None
        # 槽位以上限会话配置为准（勿用 CandidatePool 构造默认）
        max_slots = 5
        try:
            from backend.database.connection import SessionLocal
            _db = SessionLocal()
            try:
                max_slots = int(selector._pool_limit(_db))
            finally:
                _db.close()
            if pool_dict is not None:
                pool_dict["max_active"] = max_slots
            if pool is not None:
                pool.max_active = max_slots
        except Exception as e:
            logger.debug("[AutoCoinScheduler] status slots: %s", e)
            max_slots = int(getattr(pool, "max_active", None) or 5)
        try:
            from backend.config.settings import (
                AUTO_COIN_MULTI_LANE_ENABLED,
                AUTO_COIN_SCORE_V3_ENABLED,
                AUTO_COIN_SECTOR_SIGNAL_ENABLED,
                AUTO_COIN_SOURCE,
            )
            from backend.services.auto_coin_sector_signal import get_watchlist
            watchlist_size = len(get_watchlist())
            score_v3 = bool(AUTO_COIN_SCORE_V3_ENABLED)
            multi_lane = bool(AUTO_COIN_MULTI_LANE_ENABLED)
            sector_on = bool(AUTO_COIN_SECTOR_SIGNAL_ENABLED)
            source = (AUTO_COIN_SOURCE or "platform_board").strip().lower()
        except Exception:
            watchlist_size = 0
            score_v3 = False
            multi_lane = False
            sector_on = False
            source = "platform_board"
        return {
            "session_id": session_id,
            "account_id": selector.account_id,
            "exchange": selector._exchange,
            "running": True,
            # 2026-07-20：前端读 auto_coin_enabled，与 running 同步
            "auto_coin_enabled": True,
            "segment": None,
            "last_scan_at": selector._last_scan_time.isoformat() if selector._last_scan_time else None,
            "last_injected_symbols": auto_symbols,
            "auto_symbols": auto_symbols,
            "auto_coin_max_slots": max_slots,
            "scan_interval": self._interval,
            "candidate_pool": pool_dict,
            "inject_blocked_reason": selector._last_inject_block_reason,
            "degraded": getattr(selector, "_last_degraded", None),
            "rank_source": getattr(selector, "_last_rank_source", "legacy"),
            "source": source,
            "source_label": (
                "平台看板跟投"
                if source in ("platform_board", "board", "vip", "platform")
                else "独立扫描(legacy)"
            ),
            "lane_enabled": multi_lane,
            "last_lane": self._last_lane,
            "watchlist_size": watchlist_size,
            "score_v3_enabled": score_v3,
            "sector_signal_enabled": sector_on,
        }


auto_coin_scheduler = AutoCoinScheduler()


def is_auto_coin_symbol(symbol: str, session_id: str = None) -> bool:
    """检测某个 symbol 是否为 AI 自动选币。可直接 import 使用。"""
    return auto_coin_scheduler.is_auto_coin_symbol(symbol, session_id=session_id)


_FIXED_TIERS = ("short", "mid", "long")


def _parse_symbol_list(raw) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip() else []
        except Exception:
            raw = [x.strip() for x in raw.split(",") if x.strip()]
    if not isinstance(raw, (list, tuple, set)):
        return []
    out: List[str] = []
    seen: set = set()
    for s in raw:
        u = str(s or "").strip().upper()
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _coerce_by_tier_raw(raw) -> Any:
    """把 DB 里可能的双重 JSON 字符串还原成 dict。

    历史 bug：`CAST(:by_tier AS json)` + `json.dumps(...)` 会把对象存成 JSON 字符串，
    ORM 读出来是 str；前端 typeof==='string' 后整组回退 symbols，表现为「保存失败/回退默认」。
    """
    cur = raw
    for _ in range(3):
        if cur is None:
            return None
        if isinstance(cur, dict):
            return cur
        if isinstance(cur, (bytes, bytearray)):
            try:
                cur = cur.decode("utf-8")
            except Exception:
                return None
        if isinstance(cur, str):
            s = cur.strip()
            if not s:
                return None
            try:
                cur = json.loads(s)
            except Exception:
                return None
            continue
        return None
    return cur if isinstance(cur, dict) else None


def _parse_by_tier_map(raw) -> Dict[str, List[str]]:
    """解析 fixed_symbols_by_tier。

    只要任一 short/mid/long 键存在，即视为已分周期配置；
    **空列表也保留**（表示该周期故意不配固定币），不得省略后回退 symbols，
    否则会出现「三周期联动 / 无法单独清空」的假象。
    """
    raw = _coerce_by_tier_raw(raw)
    if not isinstance(raw, dict):
        return {}
    if not any(k in raw for k in _FIXED_TIERS):
        return {}
    out: Dict[str, List[str]] = {}
    for k in _FIXED_TIERS:
        if k in raw:
            out[k] = _parse_symbol_list(raw.get(k))
    return out


def _union_preserve(*lists: List[str]) -> List[str]:
    out: List[str] = []
    seen: set = set()
    for lst in lists:
        for s in lst or []:
            u = str(s or "").strip().upper()
            if u and u not in seen:
                seen.add(u)
                out.append(u)
    return out


def validate_symbols_in_backup_pool(symbols: List[str]) -> Tuple[List[str], List[str]]:
    """返回 (ok, rejected)；备选池读失败时放行（不因配置服务宕机卡死）。"""
    try:
        from backend.services.trading_pairs_config import get_user_trading_pairs
        pool = {str(s).strip().upper() for s in (get_user_trading_pairs() or []) if s}
    except Exception:
        pool = set()
    if not pool:
        ok = _parse_symbol_list(symbols)
        return ok, []
    ok, bad = [], []
    for s in _parse_symbol_list(symbols):
        (ok if s in pool else bad).append(s)
    return ok, bad


def get_session_mid_ai_config(session_id: str, db: Optional[Session] = None) -> Dict[str, Any]:
    """读会话中线 AI 开关/槽位。"""
    enabled = False
    max_slots = 3
    try:
        from sqlalchemy import text as _sa_text
        from backend.config.settings import AUTO_COIN_MID_MAX_SLOTS
        max_slots = max(1, min(5, int(AUTO_COIN_MID_MAX_SLOTS or 3)))
    except Exception:
        max_slots = 3
    try:
        from sqlalchemy import text as _sa_text
        _owns_db = db is None
        if _owns_db:
            from backend.database.connection import SessionLocal
            db = SessionLocal()
            try:
                db.connection().exec_driver_sql("SET app.is_admin = 'on'")
            except Exception:
                pass
        try:
            row = db.execute(
                _sa_text(
                    "SELECT auto_coin_mid_enabled, auto_coin_mid_max_slots "
                    "FROM full_auto_sessions WHERE session_id = :sid"
                ),
                {"sid": session_id},
            ).first()
            if row:
                enabled = bool(row[0])
                if row[1] is not None:
                    try:
                        max_slots = max(1, min(5, int(row[1])))
                    except Exception:
                        pass
        finally:
            if _owns_db:
                db.close()
    except Exception as e:
        logger.debug("[AutoCoinSelector] get_session_mid_ai_config fail %s: %s", session_id, e)
    return {"enabled": enabled, "max_slots": max_slots}


def set_fixed_symbols_by_tier(
    session_id: str,
    by_tier: Dict[str, List[str]],
    db: Optional[Session] = None,
    *,
    enforce_backup_pool: bool = True,
) -> Dict[str, Any]:
    """写入分周期固定币，并同步 symbols=三周期并集。"""
    cleaned: Dict[str, List[str]] = {}
    rejected: Dict[str, List[str]] = {}
    for k in _FIXED_TIERS:
        vals = _parse_symbol_list((by_tier or {}).get(k))
        if enforce_backup_pool:
            ok, bad = validate_symbols_in_backup_pool(vals)
            cleaned[k] = ok
            if bad:
                rejected[k] = bad
        else:
            cleaned[k] = vals
    if any(rejected.values()):
        return {
            "success": False,
            "error": "以下币不在固定币备选池(交易对配置)中",
            "rejected": rejected,
            "fixed_symbols_by_tier": cleaned,
        }
    union = _union_preserve(cleaned.get("short", []), cleaned.get("mid", []), cleaned.get("long", []))
    try:
        from backend.database.models import FullAutoSession
        from sqlalchemy.orm.attributes import flag_modified

        _owns_db = db is None
        if _owns_db:
            from backend.database.connection import SessionLocal
            db = SessionLocal()
            try:
                db.connection().exec_driver_sql("SET app.is_admin = 'on'")
            except Exception:
                pass
        try:
            row = (
                db.query(FullAutoSession)
                .filter(FullAutoSession.session_id == session_id)
                .first()
            )
            if not row:
                return {"success": False, "error": "会话不存在"}
            # 直接赋 dict，避免 CAST(json.dumps(...)) 双重编码成字符串
            row.fixed_symbols_by_tier = cleaned
            row.symbols = union
            flag_modified(row, "fixed_symbols_by_tier")
            flag_modified(row, "symbols")
            db.commit()
        finally:
            if _owns_db:
                db.close()
        return {
            "success": True,
            "fixed_symbols_by_tier": cleaned,
            "symbols": union,
        }
    except Exception as e:
        logger.warning("[AutoCoinSelector] set_fixed_symbols_by_tier fail %s: %s", session_id, e)
        try:
            if db is not None:
                db.rollback()
        except Exception:
            pass
        return {"success": False, "error": str(e)}


def get_fixed_symbols_for_session(
    session_id: str,
    db: Optional[Session] = None,
    tier: Optional[str] = None,
) -> Set[str]:
    """固定币正向白名单。

    tier:
      - "short"|"mid"|"long"：该周期固定币（by_tier 非空用 by_tier，否则回退 symbols）
      - None：三周期并集（运维/进化）；无 by_tier 时回退 symbols
    始终减去 AI 污染（短线 auto + 历史 AI；中线 sticky 在 mid 时减去）。
    """
    try:
        from sqlalchemy import text as _sa_text
        _owns_db = db is None
        if _owns_db:
            from backend.database.connection import SessionLocal
            db = SessionLocal()
            try:
                db.connection().exec_driver_sql("SET app.is_admin = 'on'")
            except Exception:
                pass
        try:
            row = db.execute(
                _sa_text(
                    "SELECT s.symbols, s.auto_coin_symbols, s.fixed_symbols_by_tier "
                    "FROM full_auto_sessions s "
                    "WHERE s.session_id = :sid"
                ),
                {"sid": session_id},
            ).first()
        finally:
            if _owns_db:
                db.close()
        if not row:
            return set()
        legacy = _parse_symbol_list(row[0])
        auto_set = set(_parse_symbol_list(row[1]))
        by_tier = _parse_by_tier_map(row[2] if len(row) > 2 else None)

        t = str(tier or "").strip().lower() or None
        if t in _FIXED_TIERS:
            # 已分周期配置时：缺键/空列表都不再回退 symbols（避免三周期被并集「焊死」）
            if by_tier:
                base = list(by_tier.get(t, []))
            else:
                base = legacy
        elif by_tier:
            base = _union_preserve(
                by_tier.get("short", []),
                by_tier.get("mid", []),
                by_tier.get("long", []),
            )
        else:
            base = legacy

        fixed = set(base)
        # [根因修复] 禁止把 MIDLONG_CORE_BASKET(BTC/ETH/SOL) 强行并进长线白名单。
        # 否则会话里「长线只勾 BTC/ETH」仍会分析/展示 SOL，配置与实盘脱节。
        # 长线唯一权威 = fixed_symbols_by_tier.long（无 by_tier 时才回退 symbols）。

        # 只剔除「当前」AI 池，不剔除历史扫描记录。
        # 否则 VIRTUAL/XPL 等曾进过 auto_coin_selections 的币，即使用户明确勾进固定币，
        # 也会被永久抹掉，运维台「会话当前启用」永远对不上备选池。
        fixed = fixed - auto_set
        if t in (None, "mid"):
            try:
                sticky = _load_ai_mid_sticky(session_id)
                mid_ai = {
                    str(s).strip().upper()
                    for s in (sticky.get("symbols") or [])
                    if s
                }
                fixed = fixed - mid_ai
            except Exception:
                pass
        return fixed
    except Exception as e:
        logger.warning(f"[AutoCoinSelector] get_fixed_symbols_for_session 查询失败 {session_id}: {e}")
        return set()


def count_open_ai_mid_positions(db: Optional[Session] = None, account_id=None) -> int:
    """open 的 tier=mid 持仓数（AI 中线单分通道计数）。

    [2026-08-10 问题三] 修复前 mid lane 全禁、中长线一律归一成 long，不存在 mid
    持仓；修复后 mid 持仓只可能来自 AI 中线候选，timeframe_tier='mid' 即通道标记，
    无需再区分来源。槽位 ≤3 硬上限依赖本计数（候选查询截断 + 开仓前二次校验）。
    """
    try:
        from sqlalchemy import text as _sa_text
        _owns_db = db is None
        if _owns_db:
            from backend.database.connection import SessionLocal as _CoreLocal
            db = _CoreLocal()
            try:
                db.connection().exec_driver_sql("SET app.is_admin = 'on'")
            except Exception:
                pass
        try:
            _sql = (
                "SELECT COUNT(*) FROM paper_positions "
                "WHERE status = 'open' AND timeframe_tier = 'mid'"
            )
            _params: Dict[str, Any] = {}
            if account_id is not None:
                _sql += " AND account_id = :acc"
                _params["acc"] = int(account_id)
            return int(db.execute(_sa_text(_sql), _params).scalar() or 0)
        finally:
            if _owns_db:
                db.close()
    except Exception as e:
        logger.warning(f"[AutoCoinSelector] count_open_ai_mid_positions 失败: {e}")
        return 0


def _ai_mid_sticky_path(session_id: str) -> str:
    base = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "data", "ai_mid_sticky"
    )
    os.makedirs(base, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(session_id))
    return os.path.join(base, f"{safe}.json")


def _load_ai_mid_sticky(session_id: str) -> Dict[str, Any]:
    path = _ai_mid_sticky_path(session_id)
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            if isinstance(data, dict):
                return data
    except Exception as e:
        logger.debug("[AutoCoinSelector] load ai_mid sticky fail %s: %s", session_id, e)
    return {}


def _save_ai_mid_sticky(session_id: str, symbols: List[str], *, reason: str) -> None:
    path = _ai_mid_sticky_path(session_id)
    payload = {
        "session_id": session_id,
        "symbols": [str(s).upper() for s in symbols if s],
        "updated_at": time.time(),
        "updated_iso": datetime.utcnow().isoformat() + "Z",
        "reason": reason,
    }
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("[AutoCoinSelector] save ai_mid sticky fail %s: %s", session_id, e)


def _midlong_board_approve_candidates(
    db: Session,
    *,
    fixed: Set[str],
    min_conf: float,
    limit: int = 40,
) -> List[tuple]:
    """平台看板 midlong approve 候选：(symbol, confidence)，已排除固定长线白名单。"""
    from sqlalchemy import text as _sa_text

    rows = db.execute(
        _sa_text(
            "SELECT symbol, confidence FROM coin_select_candidates "
            "WHERE listed IS TRUE "
            "AND horizon = 'midlong' "
            "AND lower(ai_verdict) = 'approve' "
            "AND COALESCE(confidence, 0) >= :min_conf "
            "ORDER BY confidence DESC NULLS LAST "
            "LIMIT :lim"
        ),
        {"min_conf": float(min_conf), "lim": int(limit)},
    ).all()
    out: List[tuple] = []
    seen: Set[str] = set()
    for r in rows:
        sym = str(r[0] or "").strip().upper()
        if not sym or sym in seen or sym in fixed:
            continue
        seen.add(sym)
        try:
            conf = float(r[1]) if r[1] is not None else 0.0
        except (TypeError, ValueError):
            conf = 0.0
        out.append((sym, conf))
    return out


def _auto_coin_pool_mid_fallback_candidates(
    db: Session,
    session_id: str,
    *,
    fixed: Set[str],
) -> List[tuple]:
    """看板无合格 midlong approve 时的兜底：短线 auto_coin_symbols + 最近注入审计。"""
    from sqlalchemy import text as _sa_text

    _row = db.execute(
        _sa_text(
            "SELECT auto_coin_symbols FROM full_auto_sessions "
            "WHERE session_id = :sid"
        ),
        {"sid": session_id},
    ).first()
    _auto_syms = [
        str(s).strip().upper()
        for s in (_row[0] if _row else []) or []
        if s and str(s).strip().upper() not in fixed
    ]
    if not _auto_syms:
        return []

    _rows = db.execute(
        _sa_text(
            "SELECT DISTINCT ON (symbol) symbol, action, ai_confidence "
            "FROM auto_coin_selections "
            "WHERE session_id = :sid AND upper(symbol) = ANY(:syms) "
            "ORDER BY symbol, id DESC"
        ),
        {"sid": session_id, "syms": _auto_syms},
    ).all()
    _cands: List[tuple] = []
    for r in _rows:
        _sym = str(r[0]).strip().upper()
        _act = str(r[1] or "")
        if _act not in ("injected", "renewed"):
            continue
        _cands.append((_sym, r[2]))
    _found = {s for s, _ in _cands}
    for _s in _auto_syms:
        if _s not in _found:
            _cands.append((_s, None))
    _cands.sort(key=lambda x: (x[1] is None, -(x[1] or 0.0)))
    return _cands


def force_adopt_ai_mid_symbol(session_id: str, symbol: str, *, max_slots: int = 3) -> Dict[str, Any]:
    """VIP 人工采纳 midlong：写入 AI 中线 sticky，不进固定长线表、不进短线 auto 池。"""
    sym = str(symbol or "").strip().upper()
    if not sym:
        return {"success": False, "error": "symbol required"}
    mid_cfg = get_session_mid_ai_config(session_id)
    if not mid_cfg.get("enabled"):
        return {
            "success": False,
            "error": "会话未开启中线AI选币，请先在会话管理打开开关",
        }
    try:
        from backend.config.settings import AUTO_COIN_MID_MAX_SLOTS
        _max = max(1, int(max_slots or mid_cfg.get("max_slots") or AUTO_COIN_MID_MAX_SLOTS or 3))
    except Exception:
        _max = max(1, int(max_slots or mid_cfg.get("max_slots") or 3))
    _max = max(1, min(5, _max))

    fixed = get_fixed_symbols_for_session(session_id, db=None, tier="mid")
    if sym in fixed:
        return {
            "success": True,
            "symbol": sym,
            "skipped": "already_fixed_mid",
            "note": "已在中线固定币表，无需占 AI 中线槽",
        }

    sticky = _load_ai_mid_sticky(session_id)
    cur = [
        str(s).strip().upper()
        for s in (sticky.get("symbols") or [])
        if s and str(s).strip().upper() not in fixed
    ]
    merged = [sym] + [s for s in cur if s != sym]
    merged = merged[:_max]
    _save_ai_mid_sticky(
        session_id, merged,
        reason="manual_adopt midlong_board",
    )
    return {"success": True, "symbol": sym, "ai_mid_watch": merged}


def get_ai_mid_candidates_for_session(
    session_id: str,
    db: Optional[Session] = None,
    max_slots: Optional[int] = None,
) -> List[str]:
    """AI 中线(tier=mid)候选权威来源——平台看板 midlong approve + 粘性慢刷新。

    主源：coin_select_candidates（horizon=midlong, ai_verdict=approve,
    confidence≥MIDLONG_AI_MIN_CONF），与固定长线白名单正交。
    粘性：AUTO_COIN_MID_RESAMPLE_SEC（默认 3h）内沿用 sticky；
    仅 reason 含 midlong_board / manual_adopt 的 sticky 才算有效主源缓存。
    兜底：看板无合格项时，才回退短线 auto_coin_symbols（并打日志）。

    槽位优先：显式 max_slots → 会话 auto_coin_mid_max_slots → env。
    会话 auto_coin_mid_enabled=false 时返回空（已有 mid 仓由调用方续管）。
    """
    mid_cfg = get_session_mid_ai_config(session_id, db=db)
    if not mid_cfg.get("enabled"):
        return []
    try:
        from sqlalchemy import text as _sa_text
        try:
            from backend.config.settings import (
                AUTO_COIN_MID_MAX_SLOTS,
                AUTO_COIN_MID_RESAMPLE_SEC,
                MIDLONG_AI_MIN_CONF,
            )
            _raw_slots = (
                max_slots
                if max_slots is not None
                else (mid_cfg.get("max_slots") or AUTO_COIN_MID_MAX_SLOTS or 3)
            )
            _max_slots = max(1, min(5, int(_raw_slots)))
            _resample = max(3600, int(AUTO_COIN_MID_RESAMPLE_SEC or 10800))  # 至少 1h
            _min_conf = float(MIDLONG_AI_MIN_CONF or 0.60)
        except Exception:
            _raw_slots = (
                max_slots
                if max_slots is not None
                else (mid_cfg.get("max_slots") or 3)
            )
            _max_slots = max(1, min(5, int(_raw_slots)))
            _resample = 10800
            _min_conf = 0.60

        _owns_db = db is None
        if _owns_db:
            from backend.database.connection import SessionLocal as _CoreLocal
            db = _CoreLocal()
            try:
                db.connection().exec_driver_sql("SET app.is_admin = 'on'")
            except Exception:
                pass
        try:
            _acc_id = None
            _acc_row = db.execute(
                _sa_text(
                    "SELECT paper_account_id FROM full_auto_sessions "
                    "WHERE session_id = :sid"
                ),
                {"sid": session_id},
            ).first()
            if _acc_row and _acc_row[0] is not None:
                _acc_id = int(_acc_row[0])
            _open_mid_n = count_open_ai_mid_positions(db=db, account_id=_acc_id)
            _free = max(0, _max_slots - _open_mid_n)
            if _free <= 0:
                logger.info(
                    "[AutoCoinSelector] AI 中线槽位已满 open=%d max=%d "
                    "(ai_mid_slot_full) session=%s",
                    _open_mid_n, _max_slots, session_id,
                )
                return []

            _fixed = get_fixed_symbols_for_session(session_id, db=None, tier="mid")
            _open_mid_rows = db.execute(
                _sa_text(
                    "SELECT DISTINCT upper(symbol) FROM paper_positions "
                    "WHERE status = 'open' AND timeframe_tier = 'mid'"
                    + (" AND account_id = :acc" if _acc_id is not None else "")
                ),
                {"acc": _acc_id} if _acc_id is not None else {},
            ).all()
            _open_mid = {str(r[0]).upper() for r in _open_mid_rows if r[0]}

            sticky = _load_ai_mid_sticky(session_id)
            sticky_reason = str(sticky.get("reason") or "")
            sticky_from_board = (
                "midlong_board" in sticky_reason or "manual_adopt" in sticky_reason
            )
            sticky_syms = [
                str(s).strip().upper()
                for s in (sticky.get("symbols") or [])
                if s and str(s).strip().upper() not in _fixed
            ]
            sticky_ts = float(sticky.get("updated_at") or 0)
            age = time.time() - sticky_ts if sticky_ts > 0 else 1e18
            # 旧版「from auto_coin」sticky 立即失效，强制改读看板
            if sticky_syms and age < _resample and sticky_from_board:
                picked = [s for s in sticky_syms if s not in _open_mid][:_free]
                logger.info(
                    "[AutoCoinSelector] AI 中线候选 sticky(board) session=%s picked=%s "
                    "age=%.0fs<%ds (open_mid=%d free=%d reason=%s)",
                    session_id, picked, age, _resample, _open_mid_n, _free,
                    sticky_reason[:80],
                )
                return picked

            # 到期重算：主源 = 平台看板 midlong approve
            _cands = _midlong_board_approve_candidates(
                db, fixed=_fixed, min_conf=_min_conf,
            )
            _source = "midlong_board"
            if not _cands:
                _cands = _auto_coin_pool_mid_fallback_candidates(
                    db, session_id, fixed=_fixed,
                )
                _source = "auto_coin_fallback"
                if _cands:
                    logger.warning(
                        "[AutoCoinSelector] AI 中线看板无合格 approve"
                        "(min_conf=%.2f)，兜底短线池 session=%s n=%d",
                        _min_conf, session_id, len(_cands),
                    )

            if not _cands:
                if sticky_syms:
                    picked = [s for s in sticky_syms if s not in _open_mid][:_free]
                    logger.info(
                        "[AutoCoinSelector] AI 中线主源空，宽限沿用 sticky=%s session=%s",
                        picked, session_id,
                    )
                    return picked
                logger.info(
                    "[AutoCoinSelector] AI 中线候选为空 "
                    "(board+auto_coin 均无合格项) session=%s min_conf=%.2f",
                    session_id, _min_conf,
                )
                return []
        finally:
            if _owns_db:
                db.close()

        full_watch: List[str] = []
        for _s, _c in _cands:
            if _s in full_watch:
                continue
            full_watch.append(_s)
            if len(full_watch) >= _max_slots:
                break
        _save_ai_mid_sticky(
            session_id, full_watch,
            reason=f"resample age>={_resample}s from {_source} min_conf={_min_conf}",
        )
        picked = [s for s in full_watch if s not in _open_mid][:_free]
        logger.info(
            "[AutoCoinSelector] AI 中线候选 resample session=%s source=%s "
            "watch=%s picked=%s (open_mid=%d free=%d min_conf=%.2f)",
            session_id, _source, full_watch, picked,
            _open_mid_n, _free, _min_conf,
        )
        return picked
    except Exception as e:
        logger.warning(
            f"[AutoCoinSelector] get_ai_mid_candidates_for_session 查询失败 {session_id}: {e}"
        )
        return []


def sanitize_fixed_symbols_column(
    session_id: str, db: Optional[Session] = None
) -> Dict[str, Any]:
    """清出「当前仍占 AI 池、却躺在固定列」的重叠币。

    只对照当前 auto_coin_symbols + 中线 sticky；不按历史扫描表清洗。
    清洗 symbols + fixed_symbols_by_tier 各组；不碰 auto_coin_symbols。
    返回 {"removed": [...], "kept": [...], "changed": bool, "by_tier": {...}}。
    """
    result: Dict[str, Any] = {"removed": [], "kept": [], "changed": False, "by_tier": {}}
    try:
        from sqlalchemy import text as _sa_text
        _owns_db = db is None
        if _owns_db:
            from backend.database.connection import SessionLocal
            db = SessionLocal()
            try:
                db.connection().exec_driver_sql("SET app.is_admin = 'on'")
            except Exception:
                pass
        try:
            row = db.execute(
                _sa_text(
                    "SELECT s.symbols, s.fixed_symbols_by_tier, s.auto_coin_symbols "
                    "FROM full_auto_sessions s "
                    "WHERE s.session_id = :sid"
                ),
                {"sid": session_id},
            ).first()
            if not row:
                return result
            _symbols = _parse_symbol_list(row[0])
            by_tier = _parse_by_tier_map(row[1] if len(row) > 1 else None)
            # 仅剔除当前短线 AI 池 + 当前中线 sticky，绝不按历史扫描表清洗。
            # 历史 auto_coin_selections 几乎含全市场，会把用户勾选的 VIRTUAL/XPL 等洗掉。
            pollution = set(_parse_symbol_list(row[2] if len(row) > 2 else None))
            try:
                sticky = _load_ai_mid_sticky(session_id)
                pollution |= {
                    str(s).strip().upper()
                    for s in (sticky.get("symbols") or [])
                    if s
                }
            except Exception:
                pass

            def _clean(lst: List[str]) -> Tuple[List[str], List[str]]:
                kept, removed = [], []
                for s in lst:
                    u = str(s or "").strip().upper()
                    if not u:
                        continue
                    if u in pollution:
                        removed.append(u)
                    elif u not in kept:
                        kept.append(u)
                return kept, removed

            kept, removed = _clean(_symbols)
            cleaned_tier: Dict[str, List[str]] = {}
            tier_removed: List[str] = []
            for k in _FIXED_TIERS:
                tk, tr = _clean(by_tier.get(k, []))
                cleaned_tier[k] = tk
                tier_removed.extend(tr)
            all_removed = list(dict.fromkeys(removed + tier_removed))
            if not all_removed and by_tier == cleaned_tier and kept == _symbols:
                result["kept"] = kept
                result["by_tier"] = cleaned_tier
                return result

            union = _union_preserve(
                cleaned_tier.get("short", []),
                cleaned_tier.get("mid", []),
                cleaned_tier.get("long", []),
            ) or kept
            # 若 by_tier 原本为空，只更新 symbols
            from backend.database.models import FullAutoSession
            from sqlalchemy.orm.attributes import flag_modified

            row = (
                db.query(FullAutoSession)
                .filter(FullAutoSession.session_id == session_id)
                .first()
            )
            if not row:
                return result
            if by_tier:
                row.symbols = union
                row.fixed_symbols_by_tier = cleaned_tier
                flag_modified(row, "fixed_symbols_by_tier")
                flag_modified(row, "symbols")
            else:
                row.symbols = kept
                flag_modified(row, "symbols")
            db.commit()
            result.update({
                "removed": all_removed,
                "kept": union if by_tier else kept,
                "changed": True,
                "by_tier": cleaned_tier if by_tier else {},
            })
            logger.warning(
                "[AutoCoinSelector] sanitize_fixed_symbols session=%s "
                "removed=%s kept=%s",
                session_id, all_removed, result["kept"],
            )
            return result
        finally:
            if _owns_db:
                db.close()
    except Exception as e:
        logger.warning(
            "[AutoCoinSelector] sanitize_fixed_symbols 失败 %s: %s", session_id, e
        )
        return result


def is_long_allowed(symbol: str, session_id: str, db: Optional[Session] = None) -> bool:
    """长线 tier 唯一权威判定：是否允许该 symbol 进入长线分析/thesis/前端展示。

    [2026-07-23 统一守卫]
    替代散落在 orchestrator.py / mlto_routes.py / tier_fanout.py / mlto_cycle.py
    各自维护的反向排除法（读 session.auto_coin_symbols 快照）。当
    AUTO_COIN_FORBID_LONG=true 时，一律使用 get_fixed_symbols_for_session
    正向白名单（每次现查 DB 最新行）。任何不在白名单的 symbol——含当前 AI 选币、
    已退役 AI 选币、未注册币种——都不允许进入长线。

    注意：full_auto_sessions 表在 core DB (alpha_arena)，但调用方（如
    mlto_routes.py）可能传入 analytics DB (alpha_analytics) 的 session。
    所以这里始终传 db=None 让 get_fixed_symbols_for_session 自己创建 core
    DB 连接，绝不用调用方的 analytics db 去查 core DB 表（否则查不到行，
    所有 symbol 都被判 False，导致 thesis 全被过滤掉）。

    回滚：.env 设 AUTO_COIN_FORBID_LONG=false 即退回旧行为（反向排除法兜底）。
    """
    try:
        from backend.config.settings import AUTO_COIN_FORBID_LONG
    except Exception:
        AUTO_COIN_FORBID_LONG = True
    if not AUTO_COIN_FORBID_LONG:
        return True  # 开关关闭：退回旧行为
    try:
        # 始终用 core DB (SessionLocal)，不用调用方可能传入的 analytics DB
        fixed = get_fixed_symbols_for_session(session_id, db=None, tier="long")
        return str(symbol).strip().upper() in fixed
    except Exception:
        return False  # 查询失败保守拒绝


def prune_expired_auto_symbols_for_session(
    db: Session, session_id: str, account_id: int
) -> Dict[str, Any]:
    """健康检查等场景：即使未注册选币调度器，也能剔除过期 AI 币。"""
    from backend.database.models import FullAutoSession

    session = db.query(FullAutoSession).filter(
        FullAutoSession.session_id == session_id
    ).first()
    if not session:
        return {"success": False, "error": "Session not found"}

    auto_syms = getattr(session, "auto_coin_symbols", None) or []
    if not auto_syms:
        return {"success": True, "removed": [], "removed_count": 0}

    selector = auto_coin_scheduler.get_session_selector(session_id)
    if not selector:
        selector = AutoCoinSelector(session_id=session_id, account_id=account_id)
    return selector.prune_expired_auto_symbols(db)
