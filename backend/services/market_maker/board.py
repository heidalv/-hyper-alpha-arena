# -*- coding: utf-8 -*-
"""[F247] L1 做市 · 实时深度看板数据服务

设计依据：`docs/MM_实时深度看板设计_F246补全.md`（F246 补全版）。

## 为什么单独一个模块

F246 原设计把 B1（`/board` 聚合端点）直接写进 `lane_routes.py`，但该端点**从未实现**
（实测 `lane_routes.py` 对 `board|depth|raw_levels|recent_fills` 的 grep 命中数为 0）。
数据层与车道状态层职责不同，且深度涉及**跨库**（`alpha_market`）与**命名不一致**
（深度表带 `USDT` 后缀、`lane_registry.meta.symbols` 是裸标的），独立成模块才好测。

## 数据来源与实测粒度

  · `asterdex_depth_snapshots` —— **20 档真实价量**（`bids=[[价,量],...]`）、
    **p50 105ms**、**10 个币**（BTC/ETH/SOL/XRP/ASTER/HYPE/ZEC/ARB/ONDO/SEI）、94 小时；
    索引 `ix_adx_depth_sym_ts (symbol, event_ts_ms)`，单币最新快照查询 **<1ms**（实测）。
  · `asterdex_book_ticker` —— top-of-book 兜底（32 币），用于没有深度的币。
  · 引擎运行态 —— 我方挂单/持仓，来自 `runner.status()`（不重复查库，避免两处口径分叉）。
  · `lane_ledger` —— 最近成交。

## 命名坑（本仓库已踩过两次，此处显式处理）

  `asterdex_depth_snapshots.symbol` = **`ASTERUSDT`**（带后缀）
  `market_orderbook_snapshots.symbol` = **`ZEC`**（裸标的）
  `lane_registry.meta_json.symbols` = **`["ZEC","ASTER",...]`**（裸标的）
  ⇒ **对外 API 一律用裸标的**，进深度查询前拼 `USDT`。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# 有 20 档深度采集的币。
#
# ⚠️ 这里**不要硬编码**：采集名单由 `research_l1/services/aster_ws_ingest.py
# --depth-symbols` 决定，是可随时扩容的外部事实。曾硬编码成 10 币，扩容
# DOGE/UNI 后前端仍报「该币无深度采集」——把"能采到"误报成"采不到"。
#
# 也**不要在请求里查全表**：`SELECT DISTINCT symbol ... WHERE event_ts_ms >= floor`
# 实测 **5.3s**（2700 万行）。改为**由实际返回的深度推导 + 进程内累积**：
#   · 某币本次返回了新鲜 20 档 ⇒ 它必然在采集名单里（不需要额外查询）
#   · 累积集合跨请求保留 ⇒ 采集短暂中断时不会误报"从不采集"
# 代价：进程刚启动时集合为空，此时 `depth_expected` 一律 False（前端文案退化为
# 「无深度数据」而非「该币无采集」）——这是可接受的降级，不是错误。
_KNOWN_DEPTH_SYMS: set = set()
_DEPTH_DISCOVERY_TTL_SEC = 1800.0
_DEPTH_DISCOVERY_TS: float = 0.0
_DEPTH_FRESH_MS = 3600_000      # 1 小时内有落库才算"在采"


def _discover_depth_symbols_once() -> None:
    """冷启动时查一次全表（5.3s），之后 30 分钟内不再查。失败静默。"""
    global _DEPTH_DISCOVERY_TS
    import time as _t
    now = _t.time()
    if _KNOWN_DEPTH_SYMS and (now - _DEPTH_DISCOVERY_TS) < _DEPTH_DISCOVERY_TTL_SEC:
        return
    try:
        from sqlalchemy import text as sa_text

        from backend.database.connection import MarketSessionLocal
        floor = int(datetime.now(timezone.utc).timestamp() * 1000) - _DEPTH_FRESH_MS
        with MarketSessionLocal() as db:
            rows = db.execute(
                sa_text("SELECT DISTINCT symbol FROM asterdex_depth_snapshots "
                        "WHERE event_ts_ms >= :floor"),
                {"floor": floor},
            ).fetchall()
        got = {to_bare_symbol(r[0]) for r in rows if r and r[0]}
        if got:
            _KNOWN_DEPTH_SYMS.update(got)
            _DEPTH_DISCOVERY_TS = now
    except Exception as e:                       # noqa: BLE001
        logger.warning("[board] 深度采集名单发现失败（保留旧值）: %s", e)


def depth_symbols(force: bool = False) -> frozenset:
    """当前已知在采 20 档深度的裸标的集合（**默认不阻塞请求**）。

    集合来自两处并集：① 冷启动的一次全表发现（缓存 30 分钟）；
    ② 每次 `load_depth` 实际取到深度的币（持续累积）。

    ⚠️ `force=True` 才会做全表 `DISTINCT`（实测 **5.3s**，2700 万行）。
    **HTTP 请求路径不要传 force** —— 那里应使用 `known_depth_symbols()`；
    只有后台任务（如深度看门狗、选币调度）才应触发发现。
    """
    if force:
        _discover_depth_symbols_once()
    return frozenset(_KNOWN_DEPTH_SYMS)


def known_depth_symbols() -> frozenset:
    """**只读**当前已知集合，绝不触发查询（供 HTTP 请求路径使用）。

    代价：进程刚重启且尚未有任何 `load_depth` 调用时返回空集 ⇒ 调用方应把空集
    理解为「暂无深度信息」而不是「深度全没了」（前端文案已如此处理）。
    空集会在第一次真正取深度的请求后立刻被填充（`load_depth` 会累加）。
    """
    return frozenset(_KNOWN_DEPTH_SYMS)


def to_book_symbol(symbol: str) -> str:
    """裸标的 -> 深度表的 symbol（带 USDT 后缀，已带则不重复加）。"""
    s = (symbol or "").strip().upper()
    if not s:
        return ""
    return s if s.endswith("USDT") else f"{s}USDT"


def to_bare_symbol(symbol: str) -> str:
    """深度表的 symbol -> 裸标的。"""
    s = (symbol or "").strip().upper()
    return s[:-4] if s.endswith("USDT") else s


def _f(x, default=None):
    try:
        v = float(x)
        return v if v == v else default      # NaN -> default
    except (TypeError, ValueError):
        return default


def _parse_levels(raw: Any) -> List[List[float]]:
    """jsonb -> [[价, 量], ...]。psycopg2 通常已解析为 list；字符串则再解析一次。"""
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return []
    out: List[List[float]] = []
    if isinstance(raw, (list, tuple)):
        for lv in raw:
            if isinstance(lv, (list, tuple)) and len(lv) >= 2:
                p, q = _f(lv[0]), _f(lv[1])
                if p and p > 0 and q is not None and q > 0:
                    out.append([p, q])
    return out


def _cumulate(levels: List[List[float]]) -> List[List[float]]:
    """[[价,量],...] -> [[价,量,累计量],...]（前端画柱用，避免前端重复算）。"""
    out: List[List[float]] = []
    c = 0.0
    for p, q in levels:
        c += q
        out.append([p, q, c])
    return out


class BoardService:
    """看板数据聚合。所有方法都不抛异常——取不到就如实留白（`has_depth=False`）。"""

    def __init__(self, depth_levels: int = 20):
        self.depth_levels = max(1, min(int(depth_levels or 20), 20))

    # ------------------------------------------------------------------ 深度
    def load_depth(self, symbols: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        """批量取每个币的**最新**深度快照。

        返回 {裸标的: {"bids":[[p,q,cum],...], "asks":[...], "ts_ms":int, "age_ms":int}}
        无数据/异常的币不出现在返回里（调用方据此判断 has_depth）。
        """
        out: Dict[str, Dict[str, Any]] = {}
        syms = [to_book_symbol(s) for s in symbols if s]
        if not syms:
            return out
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        try:
            from sqlalchemy import text as sa_text

            from backend.database.connection import MarketSessionLocal
        except Exception as e:                       # pragma: no cover
            logger.warning("[board] 无法导入 MarketSessionLocal: %s", e)
            return out

        try:
            with MarketSessionLocal() as db:
                # 逐币 `LIMIT 1` 的 LATERAL 形式，而不是 `DISTINCT ON (symbol)`。
                #
                # 为什么不能用 DISTINCT ON：索引 `ix_adx_depth_sym_ts` 是
                # `(symbol, event_ts_ms)` **升序**，而 `DISTINCT ON (symbol) ...
                # ORDER BY symbol, event_ts_ms DESC` 需要每个分组做一次反向扫描，
                # 规划器退化成「先取全部 symbol 的行再排序」——实测直接撞
                # `statement_timeout`（QueryCanceled）。LATERAL + LIMIT 1 每次都是
                # 走索引的 seek，实测单币 <1ms。
                #
                # `floor`：只认 10 分钟内的快照。既避免停采币触发长反向扫描，
                # 也保证看板不会把几小时前的深度当实时深度展示（age_ms 会显性告警）。
                rows = db.execute(
                    sa_text(
                        "SELECT s.symbol, d.event_ts_ms, d.bids, d.asks "
                        "FROM unnest(CAST(:syms AS text[])) AS s(symbol) "
                        "LEFT JOIN LATERAL ("
                        "  SELECT event_ts_ms, bids, asks "
                        "  FROM asterdex_depth_snapshots d2 "
                        "  WHERE d2.symbol = s.symbol AND d2.event_ts_ms >= :floor "
                        "  ORDER BY d2.event_ts_ms DESC LIMIT 1"
                        ") d ON TRUE"
                    ),
                    {"syms": syms, "floor": now_ms - 600_000},
                ).fetchall()
        except Exception as e:
            logger.warning("[board] 深度查询失败: %s", e)
            return out

        for r in rows:
            sym = to_bare_symbol(r[0])
            if r[1] is None:                       # LEFT JOIN 未命中 ⇒ 该币无深度
                continue
            bids = _parse_levels(r[2])[: self.depth_levels]
            asks = _parse_levels(r[3])[: self.depth_levels]
            if not bids and not asks:
                continue
            # bids 在库里是**降序**（最优价在前）；asks 升序。前端按"上卖下买"渲染，
            # 所以 bids 需要翻成升序，让"最好的买价"贴近中价那一侧。
            bids_asc = list(reversed(bids))
            ts = int(r[1] or 0)
            # 取到新鲜深度 ⇒ 该币必然在采集名单里（无需额外查全表）
            if ts and (now_ms - ts) <= _DEPTH_FRESH_MS:
                _KNOWN_DEPTH_SYMS.add(sym)
            out[sym] = {
                "bids": _cumulate(bids_asc),
                "asks": _cumulate(asks),
                "ts_ms": ts,
                "age_ms": (now_ms - ts) if ts else None,
            }
        return out

    # ------------------------------------------------------------- top-of-book
    def load_top(self, symbols: Sequence[str]) -> Dict[str, Dict[str, Any]]:
        """无深度时的 top-of-book 兜底（`asterdex_book_ticker`，带 USDT 后缀）。"""
        out: Dict[str, Dict[str, Any]] = {}
        syms = [to_book_symbol(s) for s in symbols if s]
        if not syms:
            return out
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        try:
            from sqlalchemy import text as sa_text

            from backend.database.connection import MarketSessionLocal
        except Exception as e:                       # pragma: no cover
            logger.warning("[board] 无法导入 MarketSessionLocal: %s", e)
            return out
        try:
            with MarketSessionLocal() as db:
                rows = db.execute(
                    sa_text(
                        "SELECT s.symbol, d.event_ts_ms, "
                        "d.bid_px, d.bid_qty, d.ask_px, d.ask_qty "
                        "FROM unnest(CAST(:syms AS text[])) AS s(symbol) "
                        "LEFT JOIN LATERAL ("
                        "  SELECT event_ts_ms, bid_px, bid_qty, ask_px, ask_qty "
                        "  FROM asterdex_book_ticker d2 "
                        "  WHERE d2.symbol = s.symbol AND d2.event_ts_ms >= :floor "
                        "  ORDER BY d2.event_ts_ms DESC LIMIT 1"
                        ") d ON TRUE"
                    ),
                    # `floor`：只认 10 分钟内的新鲜盘口。既避免停采币触发长反向扫描，
                    # 也保证看板不会把几小时前的价格当现价展示。
                    {"syms": syms, "floor": now_ms - 600_000},
                ).fetchall()
        except Exception as e:
            logger.warning("[board] top-of-book 查询失败: %s", e)
            return out
        for r in rows:
            sym = to_bare_symbol(r[0])
            if r[1] is None:
                continue
            ts = int(r[1] or 0)
            out[sym] = {
                "bid": _f(r[2]), "bid_qty": _f(r[3]),
                "ask": _f(r[4]), "ask_qty": _f(r[5]),
                "ts_ms": ts, "age_ms": (now_ms - ts) if ts else None,
            }
        return out

    # ---------------------------------------------------------------- 队列前方
    @staticmethod
    def queue_ahead_usd(ladder_side: List[List[float]], price: Optional[float],
                        side: str) -> Optional[float]:
        """我方挂单价**前方**（价格更优）的累计名义额。

        Args:
            ladder_side: [[价,量,累计量],...]，**按价格由优到劣**排列
                （`side="bid"` 时降序、`"ask"` 时升序）。
            price: 我方挂单价。
            side: `"bid"` / `"ask"`。

        Returns:
            严格优于我方价的档位累计名义（USD）。我方价优于全部档位 ⇒ 0；
            劣于全部档位 ⇒ 全量。`price` 为空或无档位 ⇒ None（前端显示「无数据」）。
        """
        if price is None or not price or not ladder_side:
            return None
        is_bid = str(side).lower() == "bid"
        total = 0.0
        for lv in ladder_side:
            p, q = _f(lv[0]), _f(lv[1])
            if not p or q is None:
                continue
            better = (p > price) if is_bid else (p < price)
            if better:
                total += p * q
        return total

    # ------------------------------------------------------------------ 主入口
    def board(self, lane_id: str, symbols: Sequence[str], *,
              runner_status: Optional[Dict[str, Any]] = None,
              recent_fills: Optional[Dict[str, List[Dict[str, Any]]]] = None,
              depth_levels: Optional[int] = None) -> Dict[str, Any]:
        """组装看板。`runner_status` / `recent_fills` 由调用方注入（便于测试与避免重复查库）。"""
        lv = int(depth_levels or self.depth_levels)
        # ⚠️ 只在这里取一次：`depth_symbols()` 是全表 DISTINCT（约 6s），
        # 放进下面逐卡片的循环会变成 N 倍。
        known = depth_symbols()
        depth = self.load_depth(symbols)
        # top-of-book 只在**需要兜底**时才查：有深度的币用梯子最优价即可。
        # 实测 `load_top` 是第二个慢查询（32 币表），全部有深度时属纯浪费。
        need_top = [s for s in symbols if to_bare_symbol(s) not in depth]
        top = self.load_top(need_top) if need_top else {}
        states = ((runner_status or {}).get("states") or {})
        fills = recent_fills or {}

        cards: List[Dict[str, Any]] = []
        for raw in symbols:
            sym = to_bare_symbol(raw)
            if not sym:
                continue
            d = depth.get(sym)
            t = top.get(sym) or {}
            st = states.get(sym) or states.get(to_book_symbol(sym)) or {}

            has_depth = bool(d and (d.get("bids") or d.get("asks")))
            # 我方挂单/持仓（引擎运行态；字段名与 runner.status() 对齐）
            my_bid = _f(st.get("quote_bid"))
            my_ask = _f(st.get("quote_ask"))
            ref_mid = _f(st.get("quote_mid"))

            # ── mid 必须来自**当前盘**，不能来自引擎报价缓存 ──────────────────
            #
            # [F253 2026-09-20] 这里此前是 `mid = _f(st.get("quote_mid"))`：直接拿
            # `lane_runtime_state.states[sym].quote_mid` 当 mid。那是**引擎上一次
            # 报价时记录的中价**，只在 tick（15s）且该币真的重新报价时才写。
            #
            # 后果（用户实测反馈：「持仓（实时）数据没有任何变化」+「成交记录和持仓
            # 对不上，两边不同步」）：XRP 的 mid 连续三次 5s 轮询都是 1.375700 一字不动，
            # 而真实盘口在 1.37930/1.37950（已偏离 26bp）。因为
            #   · 深度梯（ladder/ts_ms）来自 `asterdex_depth_snapshots`，**2s 真刷新** ✓
            #   · mid（以及由它派生的「浮盈」「距中价 bp」）来自报价缓存，**冻结** ✗
            # 于是同一张卡片里出现"深度在动、浮盈不动"的自相矛盾，用户自然会认为数据坏了。
            #
            # 正确口径：**mid = 当前盘口中间价**（优先 20 档梯子最优价，回落到 top 表）；
            # `quote_mid` 只在完全没有盘口数据时才兜底。报价基准另行以 `ref_mid`
            # 字段下发，供"报价是否陈旧"这类判断使用，不再冒充行情价。
            mid = None
            if has_depth:
                _bb = d["bids"][-1][0] if d["bids"] else None      # bids 升序 ⇒ 末位最优
                _ba = d["asks"][0][0] if d["asks"] else None
                if _bb and _ba:
                    mid = (_bb + _ba) / 2.0
            if mid is None and t.get("bid") and t.get("ask"):
                mid = (t["bid"] + t["ask"]) / 2.0
            if mid is None:
                mid = ref_mid      # 兜底：无任何盘口数据时退回报价基准（并置 stale 标记）

            # 行情是否新鲜：有盘口数据时 mid 就是实时价；否则只能是陈旧兜底值
            mid_is_live = bool(
                (has_depth and d.get("bids") and d.get("asks"))
                or (t.get("bid") and t.get("ask"))
            )

            def _w(px: Optional[float]) -> Optional[float]:
                return ((px - mid) / mid * 1e4) if (px and mid) else None

            card = {
                "symbol": sym,
                "has_depth": has_depth,
                "depth_expected": sym in known,
                "depth_age_ms": d.get("age_ms") if has_depth else None,
                "mid": mid,
                # mid 的来源是否实时（False ⇒ 退回了报价缓存，前端应显著标注）
                "mid_is_live": mid_is_live,
                # 引擎报价基准中价（与 mid 不同：它是上一次报价时刻的价）。
                # 单独给出，避免把"报价陈旧度"和"行情价"混成一个数。
                "ref_mid": ref_mid,
                "top": ({"bid": d["bids"][-1][0], "bid_qty": d["bids"][-1][1],
                         "ask": d["asks"][0][0], "ask_qty": d["asks"][0][1]}
                        if has_depth else
                        ({"bid": t.get("bid"), "bid_qty": t.get("bid_qty"),
                          "ask": t.get("ask"), "ask_qty": t.get("ask_qty")} if t else None)),
                "top_age_ms": t.get("age_ms"),
                "spread_bp": None,
                "ladder": ({"bids": d["bids"], "asks": d["asks"],
                            "levels": lv, "ts_ms": d.get("ts_ms")} if has_depth else None),
                "mine": {
                    "bid": my_bid, "ask": my_ask,
                    "bid_width_bp": _w(my_bid), "ask_width_bp": _w(my_ask),
                    # 队列前方量：买单看 bid 梯（库里降序），卖单看 ask 梯（升序）
                    "bid_queue_ahead_usd": (self.queue_ahead_usd(
                        list(reversed(d["bids"])), my_bid, "bid")
                        if (has_depth and my_bid) else None),
                    "ask_queue_ahead_usd": (self.queue_ahead_usd(
                        d["asks"], my_ask, "ask")
                        if (has_depth and my_ask) else None),
                    "quoted_age_ms": None,
                },
                "position": {
                    "qty": _f(st.get("qty"), 0.0),
                    "avg_px": _f(st.get("avg_px")),
                    "avg_mid": _f(st.get("avg_mid")),
                    "opened_ts": _f(st.get("opened_ts")),
                    "last_ts": _f(st.get("last_ts")),
                    "unrealized_usd": None,
                },
                "recent_fills": fills.get(sym, []),
            }
            # 点差（用梯子最优价，比 top 表更准）
            if has_depth and d["bids"] and d["asks"]:
                bb, ba = d["bids"][-1][0], d["asks"][0][0]
                if bb and ba and bb > 0:
                    card["spread_bp"] = (ba - bb) / ((ba + bb) / 2.0) * 1e4
            elif t.get("bid") and t.get("ask") and t["bid"] > 0:
                card["spread_bp"] = ((t["ask"] - t["bid"]) /
                                     ((t["ask"] + t["bid"]) / 2.0) * 1e4)
            # 浮盈（mid 对 avg_mid 口径，与引擎一致：用中间价而非成交价）
            pos = card["position"]
            if pos["qty"] and pos["avg_mid"] and mid:
                pos["unrealized_usd"] = pos["qty"] * (mid - pos["avg_mid"])
            if pos["opened_ts"]:
                pos["hold_ms"] = max(0, int(
                    (datetime.now(timezone.utc).timestamp() - pos["opened_ts"]) * 1000))
            if st.get("quote_ts"):
                card["mine"]["quoted_age_ms"] = max(0, int(
                    (datetime.now(timezone.utc).timestamp() - float(st["quote_ts"])) * 1000))
            cards.append(card)

        return {
            "lane_id": lane_id,
            "as_of": datetime.now(timezone.utc).isoformat(),
            "depth_symbols": sorted(known),
            "cards": cards,
        }
