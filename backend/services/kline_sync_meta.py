"""
K线同步元数据：symbol_catalog / kline_sync_heartbeat。

阶段1（2026-07-31）：为 P0/P1/P2 采集隔离提供目录与心跳落库。
表在首次写入时 CREATE IF NOT EXISTS，不依赖迁移跑通即可用。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import text as sa_text

logger = logging.getLogger(__name__)

_tables_ready = False
_tables_lock = threading.Lock()

# ── [2026-09-18 数据中心优化·D-4] 非加密 ticker 告警**聚合**（降噪） ──
# 问题：`_filter_catalog_symbols` 对每个被拒标的每次都 `logger.warning`。
# 实测（logs/data-center.log）：`拒绝写入非加密 ticker` **2,219 条 = 全文件 1.27%**，
# 而实际只有 **10 个标的**（bybit 的代币化股票/ETF：AMZN/EWY/IWM/MSTR/QQQ/SPY/BTR/
# FLOCK/DIA/USO），在 4 个交易所 × 多轮采集里被反复打印。
# 处置：逐条降为 `debug`，并按交易所**每小时聚合一条** `warning`（保留可观测性 + 不掉级别）。
# 回滚：`KLINE_NONCRYPTO_LOG_INTERVAL_S=0` ⇒ 每条即时 warning（旧行为）。
# 注意：**已下架交易对**的告警不动（那是防"目录自读自写复活"的安全信息）。
_NONCRYPTO_COUNT: Dict[str, Dict[str, int]] = {}
_NONCRYPTO_LAST_LOG: Dict[str, float] = {}
_NONCRYPTO_LOCK = threading.Lock()


def _noncrypto_interval_s() -> float:
    try:
        return float(os.getenv("KLINE_NONCRYPTO_LOG_INTERVAL_S", "3600") or 0)
    except (TypeError, ValueError):
        return 3600.0


def _log_non_crypto_ticker(ex: str, sym: str) -> None:
    """记录被拒的非加密 ticker：逐条 debug + **窗口到期时**按交易所聚合一条 warning。

    [修] 第一版在"首次调用"就报警（那时窗口内只有 1 条）⇒ 之后一小时静默、
    聚合永远统计不到有意义的量（被测试 `test_aggregate_warning_fires_and_counts` 抓出）。
    正确语义：首次只登记起点不报警；此后调用只累加（debug）；**窗口到期后的第一次调用**
    汇报该窗口累积的总量。
    """
    now = time.time()
    interval = _noncrypto_interval_s()
    with _NONCRYPTO_LOCK:
        cnt = _NONCRYPTO_COUNT.setdefault(ex, {})
        cnt[sym] = cnt.get(sym, 0) + 1
        if interval <= 0:
            due = True                      # 回滚：每条即时汇报
        elif ex not in _NONCRYPTO_LAST_LOG:
            _NONCRYPTO_LAST_LOG[ex] = now   # 首次仅登记起点，不报警
            due = False
        else:
            due = (now - _NONCRYPTO_LAST_LOG[ex]) >= interval
        if due:
            _NONCRYPTO_LAST_LOG[ex] = now
            sample = sorted(cnt.items(), key=lambda kv: -kv[1])[:8]
            total = sum(cnt.values())
            cnt.clear()
    logger.debug("[KlineSyncMeta] 拒绝写入非加密 ticker: %s (%s)", sym, ex)
    if due:
        detail = ", ".join(f"{k}×{v}" for k, v in sample)
        logger.warning(
            "[KlineSyncMeta] %s 本窗口累计拒绝非加密 ticker %d 次（%d 个标的）：%s —— "
            "过滤器工作正常，逐条已降 debug（KLINE_NONCRYPTO_LOG_INTERVAL_S 可调）",
            ex, total, len(sample), detail,
        )

# [2026-08-14 F1 整改] 非加密货币 ticker 黑名单（防再污染 symbol_catalog）。
# 事故溯源：CL/CSCO/CYS/SPCX/XAU/1000NEX 曾被写入 catalog 并标 trading，
# 最终经选币链路进入中线分析宇宙（进程内实测 _mid_allowed 含 CSCO/CYS）。
# 黑名单可扩展：发现新污染 ticker 时在此追加并同步清理存量行。
# 注意：只收「明确非加密」的 ticker，避免误伤真实币种（如 1000PEPE 是真实合约）。
NON_CRYPTO_TICKERS: frozenset[str] = frozenset({
    # 本次事故实测污染源
    "CL", "CSCO", "CYS", "SPCX", "1000NEX",
    # 贵金属现货/期货
    "XAU", "XAG", "XPT", "XPD", "GC", "SI", "HG", "PL", "PA",
    # 能源期货
    "NG",
    # 知名美股 ticker（加密市场无同名主流币）
    "C", "MSFT", "AAPL", "NVDA",
    # [2026-09-01 选币质量审计] 实测进入 auto_coin 池的非加密 ticker：
    # EWY=韩国ETF；MSTR/SPY/QQQ/AMZN=美股；BTR/FLOCK 为无行情垃圾对
    # （审计快照池=["APT","BTR","EWY","FLOCK","IP"]，3 个不可交易）。
    "EWY", "MSTR", "SPY", "QQQ", "AMZN", "BTR", "FLOCK",
    # 常见 ETF/指数补充
    "DIA", "IWM", "VTI", "TLT", "GLD", "SLV", "USO", "EEM", "EFA",
})

# [2026-09-12 F38x] 交易所已下架交易对（venue, symbol）黑名单。
# 事故溯源：Binance 于 2024-02 下架 XMR（现货+USD-M 永续），Bybit 同期下架；
# 但 symbol_catalog 里残留的 binance/bybit XMR 行在 DC_ONLY 模式下被
# MarketScanner 从目录自读再写回（refresh_catalog_from_scanner → upsert），
# updated_at 每天刷新成"今天"→ 选币 catalog 闸放行 XMR → 永久 stale 告警 +
# 浪费候选槽（实测 binance XMR 4h 陈旧 64.6h 仍标 trading）。
# 只收「交易所已公告下架」的确定对，避免误伤真实币种。
DELISTED_BY_VENUE: frozenset[tuple] = frozenset({
    ("binance", "XMR"),
    ("bybit", "XMR"),
})


def _ensure_tables() -> None:
    global _tables_ready
    if _tables_ready:
        return
    with _tables_lock:
        if _tables_ready:
            return
        try:
            from backend.database.connection import MarketSessionLocal
            with MarketSessionLocal() as db:
                db.execute(sa_text("""
                    CREATE TABLE IF NOT EXISTS symbol_catalog (
                        id SERIAL PRIMARY KEY,
                        exchange VARCHAR(20) NOT NULL,
                        symbol VARCHAR(32) NOT NULL,
                        status VARCHAR(20) NOT NULL DEFAULT 'trading',
                        contract_type VARCHAR(20) NOT NULL DEFAULT 'perp',
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        CONSTRAINT uq_symbol_catalog_ex_sym UNIQUE (exchange, symbol)
                    )
                """))
                db.execute(sa_text("""
                    CREATE INDEX IF NOT EXISTS ix_symbol_catalog_exchange
                    ON symbol_catalog (exchange)
                """))
                db.execute(sa_text("""
                    CREATE TABLE IF NOT EXISTS kline_sync_heartbeat (
                        id SERIAL PRIMARY KEY,
                        exchange VARCHAR(20) NOT NULL,
                        period VARCHAR(10) NOT NULL DEFAULT '*',
                        pool VARCHAR(8) NOT NULL DEFAULT 'p0',
                        last_success_at TIMESTAMP NULL,
                        symbols_ok INTEGER NOT NULL DEFAULT 0,
                        symbols_fail INTEGER NOT NULL DEFAULT 0,
                        meta_json TEXT NULL,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        CONSTRAINT uq_kline_sync_hb_ex_period_pool
                            UNIQUE (exchange, period, pool)
                    )
                """))
                # [2026-09-18 数据中心优化·D-2 心跳语义] 原表只有 `last_success_at`，
                # 而写入方**每轮无条件**把它刷成 now()（含 0 ok 的失败轮）⇒ 名字说"成功"、
                # 实际是"最后一次尝试" ⇒ 消费方（`data_center_gate._eval_p0_stale`、
                # `market_intelligence_routes` 的"5 分钟内成功=数据中心在线"）全都**看不见失败连胜**。
                # 实测代价：P0 有 1383 轮里 656 轮（47.4%）整轮全零，而这两处判据始终"正常"。
                # 现拆成三个语义明确的列（幂等 ALTER，老库自动升级）：
                #   last_attempt_at        —— 每轮都写（=旧的 last_success_at 实际含义）
                #   last_success_at        —— **只在 symbols_ok > 0 时前移**
                #   consecutive_fail_rounds—— 连续整轮失败计数（成功清零）
                for _ddl in (
                    "ALTER TABLE kline_sync_heartbeat "
                    "ADD COLUMN IF NOT EXISTS last_attempt_at TIMESTAMP NULL",
                    "ALTER TABLE kline_sync_heartbeat "
                    "ADD COLUMN IF NOT EXISTS consecutive_fail_rounds INTEGER NOT NULL DEFAULT 0",
                ):
                    db.execute(sa_text(_ddl))
                db.commit()
            _tables_ready = True
        except Exception as e:
            logger.warning("[KlineSyncMeta] ensure_tables 失败: %s", e)


def _filter_catalog_symbols(exchange: str, symbols: Sequence[str]) -> List[str]:
    """纯函数：归一化 + 去重 + 黑名单过滤（NON_CRYPTO_TICKERS + DELISTED_BY_VENUE）。"""
    ex = (exchange or "").strip().lower()
    if ex == "aster":
        ex = "asterdex"
    from backend.services.symbol_normalizer import is_valid_base_symbol, normalize_symbol

    cleaned: List[str] = []
    seen = set()
    for s in symbols or []:
        su = normalize_symbol(s)
        if not su:
            continue
        if su in NON_CRYPTO_TICKERS:
            # [D-4] 逐条降 debug + 按交易所聚合 warning（原为每条 warning，实测占日志 1.27%）
            _log_non_crypto_ticker(ex, su)
            continue
        if (ex, su) in DELISTED_BY_VENUE:
            logger.warning(
                "[KlineSyncMeta] 拒绝写入已下架交易对: %s@%s（交易所已公告下架，防目录自读自写复活）",
                su, ex,
            )
            continue
        if is_valid_base_symbol(su) and su not in seen:
            seen.add(su)
            cleaned.append(su)
    return cleaned


def upsert_symbol_catalog(
    exchange: str,
    symbols: Sequence[str],
    *,
    status: str = "trading",
    contract_type: str = "perp",
) -> int:
    """批量 upsert 可交易目录。返回写入/更新条数。"""
    _ensure_tables()
    ex = (exchange or "").strip().lower()
    if ex == "aster":
        ex = "asterdex"
    if not ex:
        return 0
    cleaned = _filter_catalog_symbols(ex, symbols)
    if not cleaned:
        return 0
    n = 0
    try:
        from backend.database.connection import MarketSessionLocal
        with MarketSessionLocal() as db:
            for su in cleaned:
                db.execute(sa_text("""
                    INSERT INTO symbol_catalog (exchange, symbol, status, contract_type, updated_at)
                    VALUES (:ex, :sym, :st, :ct, CURRENT_TIMESTAMP)
                    ON CONFLICT (exchange, symbol) DO UPDATE SET
                        status = EXCLUDED.status,
                        contract_type = EXCLUDED.contract_type,
                        updated_at = CURRENT_TIMESTAMP
                """), {"ex": ex, "sym": su, "st": status, "ct": contract_type})
                n += 1
            db.commit()
    except Exception as e:
        logger.warning("[KlineSyncMeta] upsert_symbol_catalog(%s) 失败: %s", ex, e)
        return 0
    return n


def list_catalog_symbols(exchange: str, status: str = "trading") -> List[str]:
    """读 symbol_catalog；空则返回 []。"""
    _ensure_tables()
    ex = (exchange or "").strip().lower()
    if ex == "aster":
        ex = "asterdex"
    try:
        from backend.database.connection import MarketSessionLocal
        with MarketSessionLocal() as db:
            rows = db.execute(sa_text("""
                SELECT symbol FROM symbol_catalog
                WHERE exchange = :ex AND status = :st
                ORDER BY symbol
            """), {"ex": ex, "st": status}).fetchall()
        return [str(r[0]).upper() for r in rows if r and r[0]]
    except Exception as e:
        logger.debug("[KlineSyncMeta] list_catalog_symbols 失败: %s", e)
        return []


def refresh_catalog_from_scanner(exchange: str) -> List[str]:
    """从 MarketScanner 拉全市场可交易对并写入 catalog。"""
    ex = (exchange or "").strip().lower()
    if ex == "aster":
        ex = "asterdex"
    symbols: List[str] = []
    try:
        from backend.services.market_scanner import MarketScanner
        symbols = MarketScanner.get_all_tradable_symbols(ex) or []
    except Exception as e:
        logger.warning("[KlineSyncMeta] scanner 拉目录失败 %s: %s", ex, e)
    if symbols:
        upsert_symbol_catalog(ex, symbols)
        logger.info("[KlineSyncMeta] catalog 刷新 %s: %d symbols", ex, len(symbols))
    return [str(s).upper() for s in symbols]


def record_heartbeat(
    exchange: str,
    *,
    pool: str,
    period: str = "*",
    symbols_ok: int = 0,
    symbols_fail: int = 0,
    meta: Optional[dict[str, Any]] = None,
) -> None:
    """写入/更新采集心跳。

    [2026-09-18 D-2] 语义修正（原来 `last_success_at` 每轮无条件刷新，名不副实）：
      · `last_attempt_at` 每轮都写；
      · `last_success_at` **只在 symbols_ok > 0 时前移**（0 ok 的失败轮保持旧值）；
      · `consecutive_fail_rounds` 连续整轮失败计数（成功清零）。
    这样 `data_center_gate._eval_p0_stale`（"最新**成功**时间 >300s"）与
    `market_intelligence_routes`（"5 分钟内**成功**=数据中心在线"）才真正生效 ——
    此前它们在 P0 47.4% 整轮全零的情况下也一直报"正常"。
    """
    _ensure_tables()
    ex = (exchange or "").strip().lower()
    if ex == "aster":
        ex = "asterdex"
    if not ex:
        return
    pool_l = (pool or "p0").strip().lower()
    period_l = (period or "*").strip()
    meta_json = None
    if meta is not None:
        try:
            meta_json = json.dumps(meta, ensure_ascii=False, default=str)[:4000]
        except Exception:
            meta_json = None
    ok_i = int(symbols_ok or 0)
    try:
        from backend.database.connection import MarketSessionLocal
        with MarketSessionLocal() as db:
            db.execute(sa_text("""
                INSERT INTO kline_sync_heartbeat
                    (exchange, period, pool, last_attempt_at, last_success_at,
                     consecutive_fail_rounds, symbols_ok, symbols_fail, meta_json, updated_at)
                VALUES
                    (:ex, :period, :pool, CURRENT_TIMESTAMP,
                     CASE WHEN :ok > 0 THEN CURRENT_TIMESTAMP ELSE NULL END,
                     CASE WHEN :ok > 0 THEN 0 ELSE 1 END,
                     :ok, :fail, :meta, CURRENT_TIMESTAMP)
                ON CONFLICT (exchange, period, pool) DO UPDATE SET
                    last_attempt_at = CURRENT_TIMESTAMP,
                    last_success_at = CASE
                        WHEN :ok > 0 THEN CURRENT_TIMESTAMP
                        ELSE kline_sync_heartbeat.last_success_at END,
                    consecutive_fail_rounds = CASE
                        WHEN :ok > 0 THEN 0
                        ELSE kline_sync_heartbeat.consecutive_fail_rounds + 1 END,
                    symbols_ok = EXCLUDED.symbols_ok,
                    symbols_fail = EXCLUDED.symbols_fail,
                    meta_json = EXCLUDED.meta_json,
                    updated_at = CURRENT_TIMESTAMP
            """), {
                "ex": ex,
                "period": period_l,
                "pool": pool_l,
                "ok": ok_i,
                "fail": int(symbols_fail or 0),
                "meta": meta_json,
            })
            db.commit()
    except Exception as e:
        logger.debug("[KlineSyncMeta] record_heartbeat 失败: %s", e)


def get_heartbeats(exchange: Optional[str] = None) -> List[dict[str, Any]]:
    """读心跳列表（运维/验收）。"""
    _ensure_tables()
    try:
        from backend.database.connection import MarketSessionLocal
        with MarketSessionLocal() as db:
            if exchange:
                ex = exchange.strip().lower()
                if ex == "aster":
                    ex = "asterdex"
                rows = db.execute(sa_text("""
                    SELECT exchange, period, pool, last_success_at, symbols_ok, symbols_fail, meta_json,
                           last_attempt_at, consecutive_fail_rounds, updated_at
                    FROM kline_sync_heartbeat WHERE exchange = :ex
                    ORDER BY pool, period
                """), {"ex": ex}).fetchall()
            else:
                rows = db.execute(sa_text("""
                    SELECT exchange, period, pool, last_success_at, symbols_ok, symbols_fail, meta_json,
                           last_attempt_at, consecutive_fail_rounds, updated_at
                    FROM kline_sync_heartbeat
                    ORDER BY exchange, pool, period
                """)).fetchall()
        out = []
        for r in rows:
            out.append({
                "exchange": r[0],
                "period": r[1],
                "pool": r[2],
                # [D-2] `last_success_at` 现在名副其实（仅成功轮前移）；
                # 需要"最后一次尝试/采集器是否在跑"请用 `last_attempt_at`。
                "last_success_at": r[3].isoformat() if r[3] else None,
                "symbols_ok": r[4],
                "symbols_fail": r[5],
                "meta_json": r[6],
                "last_attempt_at": r[7].isoformat() if r[7] else None,
                "consecutive_fail_rounds": int(r[8] or 0),
                "updated_at": r[9].isoformat() if r[9] else None,
            })
        return out
    except Exception as e:
        logger.debug("[KlineSyncMeta] get_heartbeats 失败: %s", e)
        return []


def get_catalog_coverage() -> List[dict[str, Any]]:
    """各所 catalog 规模 + crypto_klines 粗覆盖（运维/磁盘规划）。

    禁止对整表 crypto_klines（约 6800 万行 / 38GB）做 COUNT(*) 全表聚合：
    Snapshot/监控若周期性调用会把磁盘 IO 打满，表现为后端“假死”。
    这里只做 catalog 精确统计 + 表级近似行数/体积。
    """
    _ensure_tables()
    out: List[dict[str, Any]] = []
    try:
        from backend.database.connection import MarketSessionLocal
        with MarketSessionLocal() as db:
            try:
                db.execute(sa_text("SET LOCAL statement_timeout = '2500ms'"))
            except Exception:
                pass
            cat_rows = db.execute(sa_text("""
                SELECT exchange, COUNT(*) FILTER (WHERE status = 'trading') AS trading_n,
                       COUNT(*) AS total_n, MAX(updated_at) AS updated_at
                FROM symbol_catalog GROUP BY exchange ORDER BY exchange
            """)).fetchall()
            approx = db.execute(sa_text("""
                SELECT COALESCE(c.reltuples, 0)::bigint AS approx_rows,
                       pg_size_pretty(pg_total_relation_size(c.oid)) AS table_size
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relname = 'crypto_klines'
            """)).first()
        approx_rows = int(approx[0]) if approx else 0
        table_size = approx[1] if approx else None
        n_ex = max(len(cat_rows), 1)
        for r in cat_rows:
            out.append({
                "exchange": r[0],
                "catalog_trading": r[1],
                "catalog_total": r[2],
                "catalog_updated_at": r[3].isoformat() if r[3] else None,
                "symbols_with_klines": None,  # 全表 DISTINCT 过贵，不再实时算
                "kline_rows": max(approx_rows // n_ex, 0),
                "kline_rows_approx_total": approx_rows,
                "table_size": table_size,
                "approximate": True,
            })
    except Exception as e:
        logger.warning("[KlineSyncMeta] get_catalog_coverage 失败: %s", e)
    return out
