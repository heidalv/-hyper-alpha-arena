# -*- coding: utf-8 -*-
"""聪明钱（大佬操作）监控采集器 — [2026-09-08 新增]

监控"具体的人"的合约操作，补齐 position_structure（只有币安大户聚合多空比）缺失的维度：

数据源（全部公开免费、无需 Key）：
  1. OKX 带单交易员（/api/v5/copytrading/public-lead-traders + public-current-subpositions）
     —— 排行榜自动发现 Top-N 交易员，抓取其真实持仓（币种/方向/杠杆/保证金）。
  2. Hyperliquid 鲸鱼（官方 info clearinghouseState）
     —— 链上全透明，给地址即可查全部持仓。地址名单来自 smart_money_traders 注册表
        （环境变量 HL_WHALE_ADDRESSES 播种，格式 "0xabc:名字,0xdef:名字2"，或人工入库）。

表（Market DB / alpha_market）：
  smart_money_traders   交易员注册表（OKX 自动登记 + HL 种子地址）
  smart_money_snapshots 最新持仓快照（每持仓一行，upsert，非追加——防表膨胀）
  smart_money_moves     动作事件流（开/平/加/减/翻转，追加，学习系统的原料）
  smart_money_scorecard 大佬成绩单（动作 vs 4h/24h 后价格 → 命中率/均收益）

事件：单笔名义 ≥ SMART_MONEY_EVENT_USD（默认 $25万）的动作 → market_events(smart_money.move)，
severity≥3 的大动作由 LLM（glm_opencode 通道）写一句解读。

学习闭环：scorecard_update() 每小时结算到期的动作（4h/24h 价格对照），
命中率写入 smart_money_scorecard，供信号融合/选币/策略加权查询。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from backend.services.events import market_events_store as mes
from backend.services.events._http import get_json, proxy_for

logger = logging.getLogger(__name__)

OKX_BASE = "https://www.okx.com"
HL_INFO = "https://api.hyperliquid.xyz/info"

_schema_ready = False
_schema_lock = threading.Lock()
_LAST: Dict[str, Any] = {}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


def _f(v: Any) -> Optional[float]:
    try:
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def _now_ms() -> int:
    return int(time.time() * 1000)


def last_summary() -> Dict[str, Any]:
    return dict(_LAST)


# ─────────────────────────────────────────────────────────────────────────────
# Schema（与 position_structure 同款：CREATE IF NOT EXISTS，免迁移）
# ─────────────────────────────────────────────────────────────────────────────
def _db():
    from backend.database.connection import MarketSessionLocal
    return MarketSessionLocal()


def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        from sqlalchemy import text
        db = _db()
        try:
            db.execute(text("""
                CREATE TABLE IF NOT EXISTS smart_money_traders (
                    id BIGSERIAL PRIMARY KEY,
                    source VARCHAR(20) NOT NULL,           -- okx_lead / hyperliquid
                    trader_key VARCHAR(80) NOT NULL,       -- OKX uniqueCode / HL 地址
                    name VARCHAR(120),
                    meta JSONB,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    first_seen_ts BIGINT NOT NULL,
                    last_seen_ts BIGINT NOT NULL,
                    CONSTRAINT uq_smart_money_trader UNIQUE (source, trader_key)
                )"""))
            db.execute(text("""
                CREATE TABLE IF NOT EXISTS smart_money_snapshots (
                    id BIGSERIAL PRIMARY KEY,
                    source VARCHAR(20) NOT NULL,
                    trader_key VARCHAR(80) NOT NULL,
                    symbol VARCHAR(32) NOT NULL,
                    direction VARCHAR(8) NOT NULL,         -- long / short
                    size NUMERIC(28, 8),
                    notional_usd NUMERIC(24, 2),
                    leverage NUMERIC(12, 4),
                    entry_price NUMERIC(24, 8),
                    unrealized_pnl NUMERIC(24, 2),
                    extra JSONB,
                    ts_ms BIGINT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    CONSTRAINT uq_smart_money_snap UNIQUE (source, trader_key, symbol)
                )"""))
            db.execute(text("""
                CREATE TABLE IF NOT EXISTS smart_money_moves (
                    id BIGSERIAL PRIMARY KEY,
                    source VARCHAR(20) NOT NULL,
                    trader_key VARCHAR(80) NOT NULL,
                    trader_name VARCHAR(120),
                    symbol VARCHAR(32) NOT NULL,
                    action VARCHAR(12) NOT NULL,           -- open/close/increase/decrease/flip
                    direction VARCHAR(8),                  -- 动作后方向（close 时为原方向）
                    notional_usd NUMERIC(24, 2),
                    prev_notional_usd NUMERIC(24, 2),
                    leverage NUMERIC(12, 4),
                    px_at_move NUMERIC(24, 8),             -- 动作发生时标记价（成绩单结算基准）
                    ret_4h NUMERIC(12, 6),                 -- 4h 后方向收益（结算后回填）
                    ret_24h NUMERIC(12, 6),
                    settled_4h BOOLEAN NOT NULL DEFAULT FALSE,
                    settled_24h BOOLEAN NOT NULL DEFAULT FALSE,
                    ai_comment TEXT,
                    ts_ms BIGINT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )"""))
            db.execute(text("""
                CREATE TABLE IF NOT EXISTS smart_money_scorecard (
                    id BIGSERIAL PRIMARY KEY,
                    source VARCHAR(20) NOT NULL,
                    trader_key VARCHAR(80) NOT NULL,
                    trader_name VARCHAR(120),
                    sample_count INTEGER NOT NULL DEFAULT 0,
                    hits_4h INTEGER NOT NULL DEFAULT 0,
                    avg_ret_4h NUMERIC(14, 8),
                    hits_24h INTEGER NOT NULL DEFAULT 0,
                    avg_ret_24h NUMERIC(14, 8),
                    score NUMERIC(10, 4),                  -- 综合分 0~100
                    last_updated TIMESTAMPTZ NOT NULL DEFAULT now(),
                    CONSTRAINT uq_smart_money_score UNIQUE (source, trader_key)
                )"""))
            db.execute(text("CREATE INDEX IF NOT EXISTS ix_smm_moves_sym_ts ON smart_money_moves (symbol, ts_ms DESC)"))
            db.execute(text("CREATE INDEX IF NOT EXISTS ix_smm_moves_ts ON smart_money_moves (ts_ms DESC)"))
            db.execute(text("CREATE INDEX IF NOT EXISTS ix_smm_snap_trader ON smart_money_snapshots (source, trader_key)"))
            db.commit()
            _schema_ready = True
            logger.info("[SmartMoney] schema 就绪")
        except Exception as exc:
            db.rollback()
            logger.warning("[SmartMoney] schema 创建失败: %s", exc)
            raise


# ─────────────────────────────────────────────────────────────────────────────
# 数据源 1：OKX 带单交易员
# ─────────────────────────────────────────────────────────────────────────────
def fetch_okx_lead_traders(limit: int = 20) -> List[Dict[str, Any]]:
    """OKX 公开带单排行榜。返回 [{unique_code, name, win_ratio, aum, pnl, pnl_ratio}]。"""
    data = get_json(
        f"{OKX_BASE}/api/v5/copytrading/public-lead-traders",
        params={"instType": "SWAP", "sortType": "overview", "state": "0",
                "pageType": "0", "limit": str(limit)},
        headers={"User-Agent": "Mozilla/5.0"},
    )
    if not isinstance(data, dict) or str(data.get("code")) != "0":
        return []
    out: List[Dict[str, Any]] = []
    for group in data.get("data") or []:
        for t in (group or {}).get("ranks") or []:
            uc = str(t.get("uniqueCode") or "")
            if not uc:
                continue
            out.append({
                "unique_code": uc,
                "name": str(t.get("nickName") or "")[:120],
                "win_ratio": _f(t.get("winRatio")),
                "aum": _f(t.get("aum")),
                "pnl": _f(t.get("pnl")),
                "pnl_ratio": _f(t.get("pnlRatio")),
                "copy_trader_num": _f(t.get("copyTraderNum")),
            })
    return out


def fetch_okx_trader_positions(unique_code: str) -> List[Dict[str, Any]]:
    """某带单员当前公开持仓。返回 [{symbol, direction, leverage, margin, notional_usd}]。

    OKX 不给标记价，名义价值 ≈ 保证金 × 杠杆（逐仓），用于动作检测的相对比较足够。
    """
    data = get_json(
        f"{OKX_BASE}/api/v5/copytrading/public-current-subpositions",
        params={"uniqueCode": unique_code, "limit": "50"},
        headers={"User-Agent": "Mozilla/5.0"},
    )
    if not isinstance(data, dict) or str(data.get("code")) != "0":
        return []
    out: List[Dict[str, Any]] = []
    for p in data.get("data") or []:
        inst = str(p.get("instId") or "")
        if not inst:
            continue  # 汇总占位行（instId 为空）跳过
        symbol = inst.split("-")[0].upper()
        margin = _f(p.get("margin")) or 0.0
        lever = _f(p.get("lever")) or 1.0
        out.append({
            "symbol": symbol,
            "direction": "long" if str(p.get("posSide")).lower() == "long" else "short",
            "size": _f(p.get("subPos")),
            "leverage": lever,
            "entry_price": _f(p.get("openAvgPx")),
            "notional_usd": margin * lever if margin > 0 else None,
            "extra": {"margin": margin, "mgn_mode": p.get("mgnMode"), "sub_pos_id": p.get("subPosId")},
        })
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 数据源 2：Hyperliquid 鲸鱼（官方 info API，POST）
# ─────────────────────────────────────────────────────────────────────────────
def _post_hl(payload: Dict[str, Any], timeout: float = 15.0) -> Optional[Any]:
    """Hyperliquid info POST（_http.get_json 只支持 GET，这里补一个同款带代理的 POST）。"""
    import httpx
    try:
        with httpx.Client(proxy=proxy_for(HL_INFO), timeout=timeout,
                          headers={"Content-Type": "application/json"}) as client:
            r = client.post(HL_INFO, json=payload)
        if r.status_code != 200:
            logger.debug("[SmartMoney] HL info 响应 %s", r.status_code)
            return None
        return r.json()
    except Exception as exc:
        logger.debug("[SmartMoney] HL info 失败: %s", exc)
        return None


def fetch_hl_positions(address: str) -> Tuple[List[Dict[str, Any]], Optional[float]]:
    """查地址全部永续持仓。返回 (positions, account_value)。"""
    data = _post_hl({"type": "clearinghouseState", "user": address})
    if not isinstance(data, dict):
        return [], None
    account_value = _f((data.get("marginSummary") or {}).get("accountValue"))
    out: List[Dict[str, Any]] = []
    for ap in data.get("assetPositions") or []:
        pos = (ap or {}).get("position") or {}
        szi = _f(pos.get("szi"))
        if szi is None or szi == 0:
            continue
        lev = pos.get("leverage") or {}
        out.append({
            "symbol": str(pos.get("coin") or "").upper(),
            "direction": "long" if szi > 0 else "short",
            "size": abs(szi),
            "leverage": _f(lev.get("value")),
            "entry_price": _f(pos.get("entryPx")),
            "notional_usd": _f(pos.get("positionValue")),
            "unrealized_pnl": _f(pos.get("unrealizedPnl")),
            "extra": {"liquidation_px": pos.get("liquidationPx")},
        })
    return out, account_value


# ─────────────────────────────────────────────────────────────────────────────
# 交易员注册表
# ─────────────────────────────────────────────────────────────────────────────
def _upsert_trader(db, source: str, key: str, name: str, meta: Dict[str, Any]) -> None:
    from sqlalchemy import text
    now = _now_ms()
    db.execute(text("""
        INSERT INTO smart_money_traders (source, trader_key, name, meta, is_active, first_seen_ts, last_seen_ts)
        VALUES (:s, :k, :n, CAST(:m AS JSONB), TRUE, :now, :now)
        ON CONFLICT (source, trader_key) DO UPDATE
        SET name = EXCLUDED.name, meta = CAST(:m AS JSONB), last_seen_ts = :now, is_active = TRUE
    """), {"s": source, "k": key, "n": name, "m": json.dumps(meta, ensure_ascii=False), "now": now})


def _seed_hl_whales(db) -> None:
    """环境变量播种 HL 鲸鱼地址：HL_WHALE_ADDRESSES="0xabc:名字,0xdef:名字2" """
    raw = os.getenv("HL_WHALE_ADDRESSES", "") or ""
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        addr, _, nm = part.partition(":")
        addr = addr.strip()
        if len(addr) < 20:
            continue
        _upsert_trader(db, "hyperliquid", addr, nm.strip() or f"whale_{addr[:8]}",
                       {"seeded": True})


def _active_traders(db, source: str) -> List[Tuple[str, str]]:
    from sqlalchemy import text
    rows = db.execute(text(
        "SELECT trader_key, COALESCE(name, '') FROM smart_money_traders "
        "WHERE source = :s AND is_active = TRUE"
    ), {"s": source}).fetchall()
    return [(r[0], r[1]) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# 快照 upsert + 动作检测
# ─────────────────────────────────────────────────────────────────────────────
def _load_prev_snapshots(db, source: str, trader_key: str) -> Dict[str, Dict[str, Any]]:
    from sqlalchemy import text
    rows = db.execute(text(
        "SELECT symbol, direction, notional_usd, leverage FROM smart_money_snapshots "
        "WHERE source = :s AND trader_key = :k"
    ), {"s": source, "k": trader_key}).fetchall()
    return {r[0]: {"direction": r[1], "notional": float(r[2] or 0), "leverage": float(r[3] or 0)}
            for r in rows}


def _save_snapshot(db, source: str, trader_key: str, pos: Dict[str, Any], ts: int) -> None:
    from sqlalchemy import text
    db.execute(text("""
        INSERT INTO smart_money_snapshots
            (source, trader_key, symbol, direction, size, notional_usd, leverage,
             entry_price, unrealized_pnl, extra, ts_ms, updated_at)
        VALUES (:s, :k, :sym, :dir, :size, :not, :lev, :ep, :pnl, CAST(:ex AS JSONB), :ts, now())
        ON CONFLICT (source, trader_key, symbol) DO UPDATE SET
            direction = EXCLUDED.direction, size = EXCLUDED.size, notional_usd = EXCLUDED.notional_usd,
            leverage = EXCLUDED.leverage, entry_price = EXCLUDED.entry_price,
            unrealized_pnl = EXCLUDED.unrealized_pnl, extra = EXCLUDED.extra,
            ts_ms = EXCLUDED.ts_ms, updated_at = now()
    """), {"s": source, "k": trader_key, "sym": pos["symbol"], "dir": pos["direction"],
           "size": pos.get("size"), "not": pos.get("notional_usd"), "lev": pos.get("leverage"),
           "ep": pos.get("entry_price"), "pnl": pos.get("unrealized_pnl"),
           "ex": json.dumps(pos.get("extra") or {}, ensure_ascii=False), "ts": ts})


def _delete_snapshot(db, source: str, trader_key: str, symbol: str) -> None:
    from sqlalchemy import text
    db.execute(text(
        "DELETE FROM smart_money_snapshots WHERE source=:s AND trader_key=:k AND symbol=:sym"
    ), {"s": source, "k": trader_key, "sym": symbol})


def _mark_price(symbol: str) -> Optional[float]:
    """当前标记价（market_asset_metrics 最新一行，任一交易所）。"""
    from sqlalchemy import text
    db = _db()
    try:
        row = db.execute(text(
            "SELECT mark_price FROM market_asset_metrics "
            "WHERE symbol = :sym AND mark_price IS NOT NULL "
            "ORDER BY timestamp DESC LIMIT 1"
        ), {"sym": symbol}).fetchone()
        return float(row[0]) if row and row[0] is not None else None
    except Exception:
        return None
    finally:
        db.close()


def detect_moves(prev: Dict[str, Dict[str, Any]], curr: List[Dict[str, Any]],
                 *, min_notional: float, change_ratio: float) -> Tuple[List[Dict[str, Any]], List[str]]:
    """对比上一快照与当前持仓 → (moves, 当前仍持有的 symbol 列表)。

    判定：
      新出现 → open；消失 → close；方向反 → flip；
      名义变动 ≥ change_ratio 且两侧都 ≥ min_notional → increase / decrease。
    """
    moves: List[Dict[str, Any]] = []
    curr_map = {p["symbol"]: p for p in curr if (p.get("notional_usd") or 0) >= min_notional}
    held: List[str] = []

    for sym, p in curr_map.items():
        old = prev.get(sym)
        if old is None:
            moves.append({"symbol": sym, "action": "open", "direction": p["direction"],
                          "notional_usd": p.get("notional_usd"), "prev_notional_usd": None,
                          "leverage": p.get("leverage")})
            held.append(sym)
            continue
        if old["direction"] != p["direction"]:
            moves.append({"symbol": sym, "action": "flip", "direction": p["direction"],
                          "notional_usd": p.get("notional_usd"),
                          "prev_notional_usd": old["notional"], "leverage": p.get("leverage")})
            held.append(sym)
            continue
        old_n = old["notional"]
        new_n = p.get("notional_usd") or 0.0
        if old_n >= min_notional and new_n >= min_notional:
            chg = (new_n - old_n) / old_n
            if abs(chg) >= change_ratio:
                moves.append({"symbol": sym,
                              "action": "increase" if chg > 0 else "decrease",
                              "direction": p["direction"], "notional_usd": new_n,
                              "prev_notional_usd": old_n, "leverage": p.get("leverage")})
        held.append(sym)

    for sym, old in prev.items():
        if sym not in curr_map and old["notional"] >= min_notional:
            moves.append({"symbol": sym, "action": "close", "direction": old["direction"],
                          "notional_usd": None, "prev_notional_usd": old["notional"],
                          "leverage": old.get("leverage")})
    return moves, held


# ─────────────────────────────────────────────────────────────────────────────
# LLM 解读（大动作才调，走 glm_opencode 通道，复用 news_annotate 契约）
# ─────────────────────────────────────────────────────────────────────────────
_ACTION_CN = {"open": "新开", "close": "平掉", "increase": "加仓",
              "decrease": "减仓", "flip": "反手"}


def _interpret_move(move: Dict[str, Any], trader_name: str, source: str) -> Optional[str]:
    try:
        from backend.services.analysis.model_gateway import get_model_gateway
        gw = get_model_gateway()
        src_cn = "OKX带单交易员" if source == "okx_lead" else "Hyperliquid鲸鱼"
        system = (
            "你是加密货币聪明钱分析师。解读一位实盘大户的仓位动作对市场的含义。\n"
            "direction: -1.0(极度利空)~+1.0(极度利多)；strength: 1~5；"
            "duration: short(<24h)/medium(1-7d)/long(>7d)；category 固定填 whale；"
            "symbols: 受影响币种；summary: 一句中文解读（提到是谁、做了什么、值得注意的点）"
        )
        user = (
            f"{src_cn}「{trader_name}」{_ACTION_CN.get(move['action'], move['action'])} "
            f"{move['symbol']} {move.get('direction') or ''} 仓，"
            f"名义价值 ${(move.get('notional_usd') or 0):,.0f}"
            + (f"（之前 ${(move.get('prev_notional_usd') or 0):,.0f}）" if move.get("prev_notional_usd") else "")
            + f"，杠杆 {move.get('leverage') or '?'}x"
        )
        for tr in ("minimax", "glm_opencode", "ollama"):
            res = gw.call("news_annotate", system, user, transport=tr,
                          schema_task="news_annotate", max_output_tokens=400, timeout_s=60)
            if res.ok and res.json:
                return str(res.json.get("summary") or "")[:300] or None
    except Exception as exc:
        logger.debug("[SmartMoney] LLM 解读失败: %s", str(exc)[:120])
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────────────────
def collect_once() -> Dict[str, Any]:
    """采集一轮：OKX Top-N 带单员 + 注册的 HL 鲸鱼 → 快照 upsert + 动作检测 + 事件/解读。"""
    if os.getenv("SMART_MONEY_ENABLED", "true").strip().lower() not in ("1", "true", "yes", "on"):
        return {"ok": False, "skipped": "SMART_MONEY_ENABLED=false"}
    ensure_schema()

    okx_top_n = _env_int("SMART_MONEY_OKX_TOP_N", 10)
    min_notional = _env_float("SMART_MONEY_MIN_NOTIONAL", 50_000)     # 低于此名义不追踪
    change_ratio = _env_float("SMART_MONEY_CHANGE_RATIO", 0.30)       # 加减仓判定阈值
    event_usd = _env_float("SMART_MONEY_EVENT_USD", 250_000)          # 进 market_events 阈值
    llm_max = _env_int("SMART_MONEY_LLM_PER_CYCLE", 2)

    ts = _now_ms()
    stats = {"ok": True, "okx_traders": 0, "hl_whales": 0, "positions": 0,
             "moves": 0, "events": 0, "llm": 0, "errors": []}
    all_moves: List[Dict[str, Any]] = []

    db = _db()
    try:
        _seed_hl_whales(db)
        db.commit()

        # ── OKX 带单员 ──
        traders = fetch_okx_lead_traders(limit=max(20, okx_top_n))
        for t in traders[:okx_top_n]:
            try:
                _upsert_trader(db, "okx_lead", t["unique_code"], t["name"],
                               {"win_ratio": t["win_ratio"], "aum": t["aum"],
                                "pnl": t["pnl"], "pnl_ratio": t["pnl_ratio"],
                                "copy_trader_num": t["copy_trader_num"]})
                db.commit()
                positions = fetch_okx_trader_positions(t["unique_code"])
                stats["okx_traders"] += 1
            except Exception as exc:
                db.rollback()
                stats["errors"].append(f"okx {t['unique_code']}: {str(exc)[:80]}")
                continue
            _process_positions(db, "okx_lead", t["unique_code"], t["name"], positions,
                               ts, min_notional, change_ratio, all_moves, stats)

        # ── Hyperliquid 鲸鱼 ──
        for addr, name in _active_traders(db, "hyperliquid"):
            try:
                positions, acct_val = fetch_hl_positions(addr)
                _upsert_trader(db, "hyperliquid", addr, name,
                               {"account_value": acct_val})
                db.commit()
                stats["hl_whales"] += 1
            except Exception as exc:
                db.rollback()
                stats["errors"].append(f"hl {addr[:10]}: {str(exc)[:80]}")
                continue
            _process_positions(db, "hyperliquid", addr, name, positions,
                               ts, min_notional, change_ratio, all_moves, stats)

        # ── 动作落库 + 事件 + LLM ──
        from sqlalchemy import text
        llm_used = 0
        for mv in all_moves:
            px = _mark_price(mv["symbol"])
            notional = mv.get("notional_usd") or mv.get("prev_notional_usd") or 0.0
            big = notional >= event_usd
            comment = None
            if big and llm_used < llm_max:
                comment = _interpret_move(mv, mv["trader_name"], mv["source"])
                llm_used += 1 if comment else 0
            db.execute(text("""
                INSERT INTO smart_money_moves
                    (source, trader_key, trader_name, symbol, action, direction,
                     notional_usd, prev_notional_usd, leverage, px_at_move, ai_comment, ts_ms)
                VALUES (:s, :k, :n, :sym, :a, :d, :not, :prev, :lev, :px, :ai, :ts)
            """), {"s": mv["source"], "k": mv["trader_key"], "n": mv["trader_name"],
                   "sym": mv["symbol"], "a": mv["action"], "d": mv.get("direction"),
                   "not": mv.get("notional_usd"), "prev": mv.get("prev_notional_usd"),
                   "lev": mv.get("leverage"), "px": px, "ai": comment, "ts": ts})
            stats["moves"] += 1

            if big:
                sev = 2 if notional < 1_000_000 else (3 if notional < 5_000_000 else 4)
                direction_map = {"long": 0.4, "short": -0.4}
                d = direction_map.get(mv.get("direction") or "", 0.0)
                if mv["action"] in ("close", "decrease"):
                    d = -d  # 平多/减多 = 偏多信号减弱 → 方向取反
                n = mes.publish([mes.MarketEvent(
                    event_type=mes.SMART_MONEY_MOVE, ts_ms=ts, source="smart_money",
                    symbol=mv["symbol"], severity=sev, direction=d,
                    title=(f"[{mv['trader_name']}] {_ACTION_CN.get(mv['action'], mv['action'])} "
                           f"{mv['symbol']} {mv.get('direction') or ''} ${notional:,.0f}"),
                    payload={"source": mv["source"], "trader_key": mv["trader_key"],
                             "action": mv["action"], "notional_usd": notional,
                             "prev_notional_usd": mv.get("prev_notional_usd"),
                             "leverage": mv.get("leverage"), "ai_comment": comment},
                    dedupe_hash=mes.make_dedupe_hash("smart_money", mv["source"], mv["trader_key"],
                                                     mv["symbol"], mv["action"], ts),
                )])
                stats["events"] += n
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.exception("[SmartMoney] collect_once 失败: %s", exc)
        stats["ok"] = False
        stats["errors"].append(str(exc)[:160])
    finally:
        db.close()

    stats["llm"] = llm_used
    _LAST.clear()
    _LAST.update(stats)
    _LAST["ts"] = ts
    logger.info("[SmartMoney] 一轮完成: OKX=%d HL=%d 持仓=%d 动作=%d 事件=%d",
                stats["okx_traders"], stats["hl_whales"], stats["positions"],
                stats["moves"], stats["events"])
    return stats


def _aggregate_positions(positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """同一交易员的多个子仓按币种净额聚合（实测 OKX 带单员同币种可有多笔子仓，
    不聚合会在快照唯一键 (source, trader_key, symbol) 上互相覆盖）。

    净额口径：多仓名义 - 空仓名义 → 净方向；杠杆按名义加权平均。
    """
    agg: Dict[str, Dict[str, Any]] = {}
    for p in positions:
        sym = p["symbol"]
        notional = p.get("notional_usd") or 0.0
        sign = 1.0 if p["direction"] == "long" else -1.0
        a = agg.setdefault(sym, {"symbol": sym, "net": 0.0, "lev_num": 0.0, "lev_den": 0.0,
                                 "size": 0.0, "upnl": 0.0, "entries": []})
        a["net"] += sign * notional
        a["lev_num"] += (p.get("leverage") or 0.0) * notional
        a["lev_den"] += notional
        a["size"] += p.get("size") or 0.0
        a["upnl"] += p.get("unrealized_pnl") or 0.0
        if p.get("entry_price"):
            a["entries"].append((notional, p["entry_price"]))
    out: List[Dict[str, Any]] = []
    for a in agg.values():
        if abs(a["net"]) <= 0:
            continue
        entry = None
        if a["entries"]:
            tot = sum(n for n, _ in a["entries"])
            entry = sum(n * e for n, e in a["entries"]) / tot if tot else None
        out.append({
            "symbol": a["symbol"],
            "direction": "long" if a["net"] > 0 else "short",
            "size": a["size"],
            "leverage": round(a["lev_num"] / a["lev_den"], 4) if a["lev_den"] else None,
            "entry_price": entry,
            "notional_usd": abs(a["net"]),
            "unrealized_pnl": a["upnl"],
            "extra": {"sub_position_count": len(a["entries"])},
        })
    return out


def _process_positions(db, source: str, trader_key: str, trader_name: str,
                       positions: List[Dict[str, Any]], ts: int,
                       min_notional: float, change_ratio: float,
                       all_moves: List[Dict[str, Any]], stats: Dict[str, Any]) -> None:
    """单个交易员：快照对比 → 动作收集 → 快照 upsert。首轮（无历史快照）只建档不报动作，
    避免新交易员上线时把全部存量持仓误报成"新开仓"。"""
    positions = _aggregate_positions(positions)
    prev = _load_prev_snapshots(db, source, trader_key)
    first_seen = not prev and not _has_history(db, source, trader_key)

    if first_seen:
        for p in positions:
            if (p.get("notional_usd") or 0) >= min_notional:
                _save_snapshot(db, source, trader_key, p, ts)
                stats["positions"] += 1
        db.commit()
        return

    moves, _held = detect_moves(prev, positions, min_notional=min_notional,
                                change_ratio=change_ratio)
    for mv in moves:
        mv.update({"source": source, "trader_key": trader_key, "trader_name": trader_name})
    all_moves.extend(moves)

    curr_symbols = set()
    for p in positions:
        if (p.get("notional_usd") or 0) >= min_notional:
            _save_snapshot(db, source, trader_key, p, ts)
            curr_symbols.add(p["symbol"])
            stats["positions"] += 1
    # 已平仓的从快照删除
    for sym in set(prev.keys()) - curr_symbols:
        _delete_snapshot(db, source, trader_key, sym)
    db.commit()


def _has_history(db, source: str, trader_key: str) -> bool:
    from sqlalchemy import text
    row = db.execute(text(
        "SELECT 1 FROM smart_money_moves WHERE source=:s AND trader_key=:k LIMIT 1"
    ), {"s": source, "k": trader_key}).fetchone()
    return row is not None


# ─────────────────────────────────────────────────────────────────────────────
# 学习闭环：大佬成绩单（每小时结算到期动作）
# ─────────────────────────────────────────────────────────────────────────────
def _price_near(symbol: str, target_ts_ms: int, tolerance_ms: int = 1_800_000) -> Optional[float]:
    """market_asset_metrics 中离目标时刻最近的标记价（±30分钟内）。"""
    from sqlalchemy import text
    db = _db()
    try:
        row = db.execute(text(
            "SELECT mark_price FROM market_asset_metrics "
            "WHERE symbol = :sym AND mark_price IS NOT NULL "
            "  AND timestamp BETWEEN :lo AND :hi "
            "ORDER BY ABS(timestamp - :t) ASC LIMIT 1"
        ), {"sym": symbol, "lo": target_ts_ms - tolerance_ms,
            "hi": target_ts_ms + tolerance_ms, "t": target_ts_ms}).fetchone()
        return float(row[0]) if row and row[0] is not None else None
    except Exception:
        return None
    finally:
        db.close()


def scorecard_update() -> Dict[str, Any]:
    """结算到期动作的方向收益并更新成绩单。

    收益口径：动作方向的多空符号 × 价格变动率。open/increase/flip 按动作后方向；
    close/decrease 是"撤离信号"，按原方向取反（他平多→看空）。
    """
    ensure_schema()
    from sqlalchemy import text
    now = _now_ms()
    stats = {"ok": True, "settled_4h": 0, "settled_24h": 0}

    db = _db()
    try:
        rows = db.execute(text(
            "SELECT id, symbol, action, direction, px_at_move, ts_ms, settled_4h, settled_24h "
            "FROM smart_money_moves "
            "WHERE px_at_move IS NOT NULL AND (settled_4h = FALSE OR settled_24h = FALSE) "
            "  AND ts_ms <= :cutoff ORDER BY ts_ms LIMIT 200"
        ), {"cutoff": now - 4 * 3600_000}).fetchall()

        for r in rows:
            mid, sym, action, direction, px0, ts0, s4, s24 = r
            if not px0 or px0 <= 0:
                continue
            sign = 1.0 if direction == "long" else -1.0
            if action in ("close", "decrease"):
                sign = -sign
            upd: Dict[str, Any] = {"id": mid}
            if not s4 and now - ts0 >= 4 * 3600_000:
                px4 = _price_near(sym, ts0 + 4 * 3600_000)
                if px4:
                    upd["r4"] = sign * (px4 / float(px0) - 1.0)
                    stats["settled_4h"] += 1
            if not s24 and now - ts0 >= 24 * 3600_000:
                px24 = _price_near(sym, ts0 + 24 * 3600_000)
                if px24:
                    upd["r24"] = sign * (px24 / float(px0) - 1.0)
                    stats["settled_24h"] += 1
            if "r4" in upd or "r24" in upd:
                db.execute(text(
                    "UPDATE smart_money_moves SET "
                    "ret_4h = COALESCE(:r4, ret_4h), settled_4h = settled_4h OR (:r4 IS NOT NULL), "
                    "ret_24h = COALESCE(:r24, ret_24h), settled_24h = settled_24h OR (:r24 IS NOT NULL) "
                    "WHERE id = :id"
                ), {"r4": upd.get("r4"), "r24": upd.get("r24"), "id": mid})
        db.commit()

        # 重建成绩单（全量重算，数据量小，简单可靠）
        db.execute(text("""
            INSERT INTO smart_money_scorecard
                (source, trader_key, trader_name, sample_count, hits_4h, avg_ret_4h,
                 hits_24h, avg_ret_24h, score, last_updated)
            SELECT source, trader_key, MAX(trader_name), COUNT(*),
                   COUNT(*) FILTER (WHERE ret_4h > 0), AVG(ret_4h),
                   COUNT(*) FILTER (WHERE ret_24h > 0), AVG(ret_24h),
                   LEAST(100, GREATEST(0,
                       50 + COALESCE(AVG(ret_24h), AVG(ret_4h), 0) * 5000
                       + (COUNT(*) FILTER (WHERE ret_24h > 0)::float
                          / GREATEST(1, COUNT(*) FILTER (WHERE ret_24h IS NOT NULL)) - 0.5) * 40
                   )),
                   now()
            FROM smart_money_moves
            WHERE settled_4h = TRUE OR settled_24h = TRUE
            GROUP BY source, trader_key
            ON CONFLICT (source, trader_key) DO UPDATE SET
                trader_name = EXCLUDED.trader_name, sample_count = EXCLUDED.sample_count,
                hits_4h = EXCLUDED.hits_4h, avg_ret_4h = EXCLUDED.avg_ret_4h,
                hits_24h = EXCLUDED.hits_24h, avg_ret_24h = EXCLUDED.avg_ret_24h,
                score = EXCLUDED.score, last_updated = now()
        """))
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning("[SmartMoney] scorecard_update 失败: %s", exc)
        stats["ok"] = False
        stats["error"] = str(exc)[:160]
    finally:
        db.close()
    return stats


def get_symbol_smart_money_sentiment(symbol: str, hours: int = 24) -> Dict[str, Any]:
    """给策略/选币用的单币聪明钱情绪：近 N 小时动作按成绩单分数加权的方向汇总。

    返回 direction(-1~+1) / move_count / top_traders / available。
    无数据时 available=False（不伪造中性）。
    """
    ensure_schema()
    from sqlalchemy import text
    cutoff = _now_ms() - int(hours * 3600_000)
    db = _db()
    try:
        rows = db.execute(text("""
            SELECT m.action, m.direction, m.notional_usd, m.prev_notional_usd,
                   m.trader_name, COALESCE(s.score, 50) AS score
            FROM smart_money_moves m
            LEFT JOIN smart_money_scorecard s
              ON s.source = m.source AND s.trader_key = m.trader_key
            WHERE m.symbol = :sym AND m.ts_ms >= :cutoff
            ORDER BY m.ts_ms DESC LIMIT 100
        """), {"sym": symbol.upper(), "cutoff": cutoff}).fetchall()
    finally:
        db.close()
    if not rows:
        return {"direction": 0.0, "move_count": 0, "top_traders": [], "available": False}

    wsum = 0.0
    wtot = 0.0
    traders: Dict[str, float] = {}
    for action, direction, notional, prev_not, name, score in rows:
        sign = 1.0 if direction == "long" else -1.0
        if action in ("close", "decrease"):
            sign = -sign
        weight = (float(score) / 50.0) * min(3.0, float(notional or prev_not or 0) / 250_000)
        wsum += sign * weight
        wtot += abs(weight)
        traders[name or "?"] = traders.get(name or "?", 0.0) + sign * weight
    direction = max(-1.0, min(1.0, wsum / wtot)) if wtot else 0.0
    top = sorted(traders.items(), key=lambda kv: -abs(kv[1]))[:3]
    return {"direction": round(direction, 4), "move_count": len(rows),
            "top_traders": [{"name": n, "bias": round(b, 2)} for n, b in top],
            "available": True}
