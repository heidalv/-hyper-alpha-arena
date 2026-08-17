"""VIP 共用 AI 选币 — 平台级扫描（管理员 LLM）+ 短线/长线看板。

与会话内 AutoCoin 分离：本模块只写共用看板，不自动注入各 VIP 会话。
交易决策 LLM 仍走各账户自备 Key；此处仅 coin_select 用途使用管理员租户。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_last_scan_ts: float = 0.0
_running = False


def resolve_admin_tenant_id() -> Optional[int]:
    try:
        from backend.config.settings import COIN_SELECT_ADMIN_TENANT_ID
        tid = int(COIN_SELECT_ADMIN_TENANT_ID or 0)
        if tid > 0:
            return tid
    except Exception:
        pass
    # 回退：role=admin 的第一个用户（如 heida）
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import User
        db = SessionLocal()
        try:
            u = (
                db.query(User)
                .filter(User.role == "admin", User.is_active == "true")
                .order_by(User.id.asc())
                .first()
            )
            return int(u.id) if u else None
        finally:
            db.close()
    except Exception as e:
        logger.warning("[CoinSelectPlatform] resolve admin tenant: %s", e)
        return None


def get_admin_coin_select_llm():
    """管理员租户的 coin_select LLM；失败返回 None。

    使用管理员已配置的默认/coin_select 模型（当前为 deepseek-v4-flash）。
    不再强切 deepseek-chat（官方已停用）。

    注意：调度器线程没有 HTTP 请求态，必须显式 set_request_identity(admin)，
    否则 RLS 会把管理员租户的 llm_configurations 滤成空 → 误报 no_llm。
    """
    from backend.services.llm_config_service import get_llm_config, get_llm_config_for_usage

    tid = resolve_admin_tenant_id()
    if not tid:
        return None

    # 后台扫描 / 无 JWT 场景：以管理员身份穿透 RLS 读取其 LLM
    try:
        from backend.core.tenant import set_request_identity

        set_request_identity(int(tid), "admin")
    except Exception as e:
        logger.warning("[CoinSelectPlatform] set_request_identity(%s): %s", tid, e)

    cfg = get_llm_config_for_usage("coin_select", tenant_id=tid, tier="fast")
    if not (cfg and getattr(cfg, "api_key", None)):
        cfg = get_llm_config_for_usage("coin_select", tenant_id=tid, tier="deep")
    if not (cfg and getattr(cfg, "api_key", None)):
        cfg = get_llm_config(tier="fast", tenant_id=tid)
    if not (cfg and getattr(cfg, "api_key", None)):
        cfg = get_llm_config(tier="deep", tenant_id=tid)
    if not (cfg and getattr(cfg, "api_key", None)):
        return None

    # 若配置仍指向已停用的 deepseek-chat，自动改用 v4-flash（同 Key）
    model = (getattr(cfg, "model", None) or "").strip().lower()
    if model in ("deepseek-chat", "deepseek-coder"):
        try:
            cfg.model = "deepseek-v4-flash"
            logger.info(
                "[CoinSelectPlatform] 模型 %s 已停用，改用 deepseek-v4-flash (cfg id=%s)",
                model,
                getattr(cfg, "id", None),
            )
        except Exception:
            pass
    return cfg


def _factor_soft(symbol: str) -> Tuple[Optional[float], Dict[str, Any]]:
    """软因子匹配：失败不阻断，返回 (score, detail)。与 CoinRank / auto_coin 对齐。"""
    try:
        from backend.services.factor_engine.exposure_service import summarize_exposure

        return summarize_exposure(symbol, "15m", 200)
    except Exception as e:
        return None, {"reason": "error", "error": str(e)[:200], "top": [], "n": 0}


def _norm_sym(sym: str) -> str:
    s = str(sym or "").upper().replace("-USD", "").replace("USDT", "").strip()
    if not s or len(s) > 20 or "/" in s:
        return ""
    return s


def _scan_market_candidates(limit: int = 40) -> List[Dict[str, Any]]:
    """只消费数据中心已采集的行情，禁止再单独打交易所拉目录。

    优先走共用 CoinRankEngine（与会话猎手对齐）；失败则回退下方旧公式。
    """
    try:
        from backend.services.coin_rank.engine import (
            engine_enabled,
            rank_results_to_platform_candidates,
            rank_universe,
        )

        if engine_enabled():
            ranked = rank_universe(limit=limit, apply_factor=True, apply_gate=True, apply_decay=True)
            out = rank_results_to_platform_candidates(ranked)
            # [2026-08-14 F2 整改] fail-closed：候选必须属于数据中心 catalog 可交易集。
            out = _fail_closed_filter(out)
            logger.info(
                "[CoinSelectPlatform] CoinRankEngine ranked=%d top=%s",
                len(out),
                [x.get("symbol") for x in out[:5]],
            )
            return out
    except Exception as e:
        logger.warning("[CoinSelectPlatform] CoinRankEngine fallback to legacy: %s", e)

    out: List[Dict[str, Any]] = []
    by_sym: Dict[str, Dict[str, Any]] = {}

    def _upsert(sym: str, *, volume: float = 0.0, change: float = 0.0, price: float = 0.0, source: str = "") -> None:
        u = _norm_sym(sym)
        if not u:
            return
        row = by_sym.get(u) or {"symbol": u, "volume_24h": 0.0, "change_24h": 0.0, "price": 0.0, "sources": []}
        if volume and volume > float(row.get("volume_24h") or 0):
            row["volume_24h"] = float(volume)
        if change:
            row["change_24h"] = float(change)
        if price:
            row["price"] = float(price)
        if source and source not in row["sources"]:
            row["sources"].append(source)
        by_sym[u] = row

    # ── 1) 数据中心 ticker 缓存（已由 poller/采集器写入，不发起新交易所目录请求）──
    try:
        from backend.services.asterdex_ticker_poller import asterdex_ticker_poller

        stats = asterdex_ticker_poller.get_all_stats() or {}
        for sym, st in stats.items():
            if not isinstance(st, dict):
                continue
            _upsert(
                sym,
                volume=float(st.get("quote_volume_24h") or st.get("volume_24h") or 0),
                change=float(st.get("change_24h") or st.get("percentage") or 0),
                price=float(st.get("price") or st.get("last") or 0),
                source="dc_ticker",
            )
        for sym, px in (asterdex_ticker_poller.get_all_prices() or {}).items():
            _upsert(sym, price=float(px or 0), source="dc_price")
        logger.info("[CoinSelectPlatform] data_center ticker stats: %d", len(stats))
    except Exception as e:
        logger.warning("[CoinSelectPlatform] ticker poller unavailable: %s", e)

    # ── 2) 数据中心目录表（只读 catalog，禁止 refresh_catalog_from_scanner）──
    # 用于校验可交易；成交额仍以 ticker 为准，避免字母序垃圾币顶栏
    catalog_set: set = set()
    try:
        from backend.services.kline_sync_meta import list_catalog_symbols

        for ex in ("asterdex", "binance", "hyperliquid"):
            cats = list_catalog_symbols(ex, status="trading") or []
            if not cats:
                continue
            for sym in cats:
                u = _norm_sym(sym)
                if u:
                    catalog_set.add(u)
                    # 仅当尚无 ticker 时登记进池（不赋假成交额）
                    if u not in by_sym:
                        _upsert(u, source=f"catalog:{ex}")
            logger.info("[CoinSelectPlatform] catalog %s: %d", ex, len(cats))
            break
    except Exception as e:
        logger.debug("[CoinSelectPlatform] catalog read: %s", e)

    # ── 3) 已构建的交易宇宙（内存态，不重建、不打交易所）──
    try:
        from backend.services.alpha.universe_manager import universe_manager

        state = universe_manager.get_state()
        selected = getattr(state, "selected", None) or []
        for r in selected:
            sym = getattr(r, "symbol", None) or (r.get("symbol") if isinstance(r, dict) else None)
            adv = float(getattr(r, "adv_usd", 0) or (r.get("adv_usd") if isinstance(r, dict) else 0) or 0)
            score = float(
                getattr(r, "composite_score", 0)
                or (r.get("composite_score") if isinstance(r, dict) else 0)
                or 0
            )
            u = _norm_sym(sym or "")
            if not u:
                continue
            _upsert(u, volume=adv, source="universe")
            if score:
                by_sym[u]["universe_score"] = score
        if selected:
            logger.info("[CoinSelectPlatform] universe selected: %d", len(selected))
    except Exception as e:
        logger.debug("[CoinSelectPlatform] universe: %s", e)

    _LIQUID_PREF = (
        "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "AVAX", "LINK", "DOT", "ATOM",
        "NEAR", "APT", "SUI", "ARB", "OP", "INJ", "TIA", "SEI", "AAVE", "UNI",
        "LTC", "FIL", "RENDER", "FET", "ONDO", "HYPE", "WIF", "TON", "ADA", "TRX",
    )  # noqa: F841 —— 保留为模块级 _LIQUID_PREF 的别名引用，避免大范围改动

    if not by_sym:
        # [2026-08-15 消费端验收] 兜底不再伪造 volume=1.0；用 0 成交额 +
        # emergency_fallback 来源标记，诚实反映「数据中心无行情」。
        logger.error(
            "[CoinSelectPlatform] 数据中心无可用行情/目录，临时使用主流币兜底。"
            "请检查 ticker poller / catalog 是否在跑。"
        )
        for s in _LIQUID_PREF[:12]:
            _upsert(s, volume=0.0, source="emergency_fallback")

    has_volume = any(float(r.get("volume_24h") or 0) > 0 for r in by_sym.values())
    pref_idx = {s: i for i, s in enumerate(_LIQUID_PREF)}

    if has_volume:
        ranked = sorted(by_sym.values(), key=lambda x: float(x.get("volume_24h") or 0), reverse=True)
    else:
        # ticker 未就绪：只取 catalog∩流动性偏好，禁止按字母把 0G/1000xxx 顶上来
        pool = catalog_set or set(by_sym.keys())
        preferred = [s for s in _LIQUID_PREF if s in pool]
        if not preferred:
            preferred = [
                s for s in sorted(pool)
                if s.isalpha() and 2 <= len(s) <= 10 and not s.startswith("1000")
            ][:limit]
        ranked = []
        for s in preferred:
            ranked.append(
                by_sym.get(s)
                or {"symbol": s, "volume_24h": 0.0, "change_24h": 0.0, "price": 0.0, "sources": ["pref"]}
            )
        logger.warning(
            "[CoinSelectPlatform] ticker 无成交额，改用数据中心目录∩流动性偏好 %d 个",
            len(ranked),
        )

    max_vol = max((float(r.get("volume_24h") or 0) for r in ranked), default=0.0) or 1.0

    for i, r in enumerate(ranked[: max(limit, 20)]):
        vol = float(r.get("volume_24h") or 0)
        chg = abs(float(r.get("change_24h") or 0))
        uni = float(r.get("universe_score") or 0)
        if has_volume:
            liq = min(1.0, vol / max_vol)
            mom = min(1.0, chg / 15.0) if chg else 0.0
            score = round(0.7 * liq + 0.15 * mom + 0.15 * min(1.0, uni), 4)
        else:
            score = round(max(0.25, 1.0 - pref_idx.get(r["symbol"], 80) / 80.0), 4)
        if score < 0.2:
            score = max(0.2, 1.0 - i / 280.0)
        out.append(
            {
                "symbol": r["symbol"],
                "rank": i + 1,
                "score": score,
                "volume_24h": vol,
                "change_24h": r.get("change_24h"),
                "price": r.get("price"),
                "market_source": ",".join(r.get("sources") or []),
            }
        )
        if len(out) >= limit:
            break

    # 因子软分（本地 exposure，不拉交易所）
    for row in out:
        fm, fd = _factor_soft(row["symbol"])
        row["factor_match"] = fm
        row["factor_detail"] = fd
        if fm is not None:
            row["score"] = round(0.65 * float(row["score"]) + 0.35 * float(fm), 4)
    out.sort(key=lambda x: float(x.get("score") or 0), reverse=True)
    # [2026-08-14 F2 整改] fail-closed：剔除不在数据中心 catalog 可交易集的 symbol
    #（历史事故：CL/CSCO/CYS 等股票代码经此进入选币候选 → 看板 approve → 中线宇宙）。
    out = _fail_closed_filter(out)
    return out


_LIQUID_PREF = (
    "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "AVAX", "LINK", "DOT", "ATOM",
    "NEAR", "APT", "SUI", "ARB", "OP", "INJ", "TIA", "SEI", "AAVE", "UNI",
    "LTC", "FIL", "RENDER", "FET", "ONDO", "HYPE", "WIF", "TON", "ADA", "TRX",
)


def _fail_closed_filter(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """候选白名单过滤：只保留数据中心 symbol_catalog(status=trading) 中的 symbol。

    catalog 读取失败时降级为内置流动性偏好白名单（仍 fail-closed，不放任陌生 symbol）。
    """
    trading_set: set = set()
    try:
        from backend.services.kline_sync_meta import list_catalog_symbols

        for ex in ("asterdex", "binance", "hyperliquid", "bybit", "okx"):
            try:
                trading_set.update(str(s).strip().upper() for s in (list_catalog_symbols(ex, status="trading") or []))
            except Exception:
                continue
    except Exception as e:
        logger.warning("[CoinSelectPlatform] catalog 读取失败，降级流动性白名单: %s", e)
    if not trading_set:
        trading_set = {s for s in _LIQUID_PREF}
    dropped = []
    kept: List[Dict[str, Any]] = []
    for c in candidates:
        sym = str(c.get("symbol") or "").strip().upper()
        if sym and sym in trading_set:
            kept.append(c)
        else:
            dropped.append(sym)
    if dropped:
        logger.warning("[CoinSelectPlatform] fail-closed 剔除 %d 个非 catalog symbol: %s", len(dropped), dropped[:12])
    return kept


def _build_dual_horizon_prompt(batch: List[Dict[str, Any]]) -> str:
    lines = []
    for c in batch:
        lines.append(
            f"- {c['symbol']}: market_score={c.get('score')}, "
            f"volume_24h={c.get('volume_24h')}, change_24h={c.get('change_24h')}, "
            f"factor_match={c.get('factor_match')}, "
            f"factor_top={json.dumps(c.get('factor_detail', {}).get('top', [])[:3], ensure_ascii=False)}, "
            f"source={c.get('market_source')}"
        )
    body = "\n".join(lines)
    return f"""你是加密货币选币研究官。下面候选全部来自平台数据中心已采集行情（非临时拉交易所）。请同时给出「短线 scalp」与「中长线 midlong」两套判断。

要求：
1. 不要被单一分数束缚；综合叙事、流动性、因子与风险。
2. 必须只输出一个 JSON 对象，格式严格为：
   {{"items":[{{"symbol":"ETH","horizon":"scalp","verdict":"approve","confidence":0.7,"direction":"long","reason":"...","risk_notes":"...","invalidation":"...","tier":"strong"}}, ...]}}
3. 每个元素字段：symbol, horizon(scalp|midlong), verdict(approve|watch|reject),
   confidence(0-1), direction(long|short|neutral), reason(中文详细理由),
   risk_notes, invalidation(失效条件), tier(strong|watch|reject)
4. 同一币可同时出现在 scalp 与 midlong（若适合）。
5. 至少给出若干 approve/watch；reject 也要写清原因。
6. 禁止 markdown、禁止 JSON 外任何文字。
7. **通过率纪律（校准要求）**：approve 每批次控制在 20%-40%——宁可错过不可错杀，
   只放行证据链最强的候选；多数候选给 watch/reject。
8. approve 必须有明确证据链：叙事/流动性/因子/风险四要素中至少三项明确支持，
   且 confidence >= 0.6；证据不足一律降为 watch。

候选：
{body}
"""


async def _call_admin_ai(llm_cfg, prompt: str) -> List[Dict[str, Any]]:
    from backend.services.llm_config_service import call_llm_api, is_reasoning_model

    messages = [
        {
            "role": "system",
            "content": (
                "你是专业的加密货币研究员。"
                '只输出一个 JSON 对象：{"items":[...]}。'
                "items 内每项含 symbol,horizon,verdict,confidence,direction,reason,risk_notes,invalidation,tier。"
                "禁止 markdown，禁止解释文字。"
            ),
        },
        {"role": "user", "content": prompt},
    ]

    def _try_parse(text: str) -> List[Dict[str, Any]]:
        text = (text or "").strip()
        if not text:
            return []
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                for k in ("items", "results", "candidates", "data"):
                    if isinstance(data.get(k), list):
                        return [x for x in data[k] if isinstance(x, dict)]
                if data.get("symbol"):
                    return [data]
                return []
            if isinstance(data, list):
                return [x for x in data if isinstance(x, dict)]
        except Exception:
            return []
        return []

    def _extract(content: str) -> List[Dict[str, Any]]:
        content = (content or "").strip()
        if content.startswith("```"):
            content = content.strip("`")
            if content.lower().startswith("json"):
                content = content[4:].strip()
        parsed = _try_parse(content)
        if parsed:
            return parsed
        for opener, closer in (("{", "}"), ("[", "]")):
            start_i = content.find(opener)
            if start_i < 0:
                continue
            depth = 0
            in_str = False
            esc = False
            end_i = -1
            for i in range(start_i, len(content)):
                ch = content[i]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == "\\":
                        esc = True
                    elif ch == '"':
                        in_str = False
                    continue
                if ch == '"':
                    in_str = True
                    continue
                if ch == opener:
                    depth += 1
                elif ch == closer:
                    depth -= 1
                    if depth == 0:
                        end_i = i + 1
                        break
            if end_i > start_i:
                parsed = _try_parse(content[start_i:end_i])
                if parsed:
                    return parsed
        return []

    async def _once(*, use_json_fmt: bool) -> List[Dict[str, Any]]:
        kwargs: Dict[str, Any] = {}
        # deepseek-v4-flash 支持 json_object；强制对象格式避免数组解析失败
        if use_json_fmt and not is_reasoning_model(getattr(llm_cfg, "model", "") or ""):
            kwargs["response_format"] = {"type": "json_object"}
        resp = await call_llm_api(
            llm_cfg,
            messages,
            temperature=0.3,
            max_tokens=8192,
            caller="coin_select_platform",
            **kwargs,
        )
        if not resp:
            return []
        try:
            msg = (resp.get("choices") or [{}])[0].get("message", {}) or {}
            content = msg.get("content") or ""
            if not str(content).strip() and msg.get("reasoning_content"):
                content = str(msg.get("reasoning_content") or "")
        except Exception:
            content = str(resp)
        return _extract(content)

    # 先试 json_object，失败再裸调一次
    parsed = await _once(use_json_fmt=True)
    if parsed:
        return parsed
    logger.warning("[CoinSelectPlatform] json_object 解析失败，改裸调重试")
    parsed = await _once(use_json_fmt=False)
    if parsed:
        logger.info("[CoinSelectPlatform] 裸调解析成功 n=%d", len(parsed))
        return parsed
    logger.warning("[CoinSelectPlatform] AI JSON 两次均失败")
    return []



def _persist_board(
    db,
    scan_id: str,
    market_rows: List[Dict[str, Any]],
    ai_rows: List[Dict[str, Any]],
    ttl_hours: int,
) -> Tuple[int, int]:
    from backend.database.models import CoinSelectCandidate

    by_sym = {r["symbol"]: r for r in market_rows}
    valid_until = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=ttl_hours)
    n_s = n_m = 0
    seen = set()

    def _add(item: Dict[str, Any], default_horizon: str) -> None:
        nonlocal n_s, n_m
        sym = str(item.get("symbol") or "").upper().strip()
        if not sym:
            return
        horizon = str(item.get("horizon") or default_horizon).lower()
        if horizon not in ("scalp", "midlong"):
            horizon = default_horizon
        key = (sym, horizon)
        if key in seen:
            return
        seen.add(key)
        m = by_sym.get(sym) or {}
        verdict = str(item.get("verdict") or item.get("ai_verdict") or "watch").lower()
        # 门控：hard/soft reject 不得进强烈推荐
        gate = str(m.get("gate") or item.get("gate") or "pass")
        if gate == "hard_reject":
            verdict = "reject"
        elif gate == "soft_reject" and verdict == "approve":
            verdict = "watch"
        tier = str(item.get("tier") or ("strong" if verdict == "approve" else "watch" if verdict == "watch" else "reject"))
        if gate == "hard_reject":
            tier = "reject"
        elif gate == "soft_reject" and tier == "strong":
            tier = "watch"
        conf = item.get("confidence")
        try:
            conf = float(conf) if conf is not None else float(m.get("score") or 0.5)
        except Exception:
            conf = 0.5
        row = CoinSelectCandidate(
            scan_id=scan_id,
            symbol=sym,
            horizon=horizon,
            tier_label=tier,
            score=float(m.get("score") or conf),
            factor_match=m.get("factor_match"),
            factor_detail=m.get("factor_detail"),
            market_scores={
                "rank": m.get("rank"),
                "score": m.get("score"),
                "volume_24h": m.get("volume_24h"),
                "change_24h": m.get("change_24h"),
                "source": m.get("market_source"),
                "trap_soft": m.get("trap_soft"),
                "mtf_confluence": m.get("mtf_confluence"),
                "gate": m.get("gate"),
                "liquidity": m.get("liquidity"),
                "cs_momentum": m.get("cs_momentum"),
                "ts_momentum": m.get("ts_momentum"),
                "explain": m.get("explain"),
                "hist_hit_rate": m.get("hist_hit_rate"),
                "hist_avg_pnl_24h": m.get("hist_avg_pnl_24h"),
                "hist_samples": m.get("hist_samples") or 0,
                "degraded": item.get("degraded") or m.get("degraded"),
            },
            ai_verdict=verdict,
            ai_reason=str(item.get("reason") or item.get("ai_reason") or "")[:4000],
            confidence=conf,
            direction_bias=str(item.get("direction") or item.get("direction_bias") or "neutral")[:16],
            risk_notes=str(item.get("risk_notes") or "")[:2000],
            invalidation=str(item.get("invalidation") or "")[:2000],
            valid_until=valid_until,
            listed=True,  # 管理员可下架；VIP 看板再按 verdict 过滤
            raw_json=item,
        )
        db.add(row)
        if horizon == "scalp":
            n_s += 1
        else:
            n_m += 1

    if not ai_rows:
        # AI 失败：仅当已有「非降质」在架看板时保留；垃圾板可被覆盖
        prev_listed = (
            db.query(CoinSelectCandidate)
            .filter(CoinSelectCandidate.listed.is_(True))
            .all()
        )
        prev_good = 0
        for r in prev_listed:
            ms = r.market_scores if isinstance(r.market_scores, dict) else {}
            if not ms.get("degraded"):
                prev_good += 1
        if prev_good > 0:
            logger.warning(
                "[CoinSelectPlatform] AI 空结果，保留既有优质看板 good=%d（本轮仅记 scan 降质）",
                prev_good,
            )
            return 0, 0

        # 无优质板：下架旧 listed，写规则分降级卡片
        if prev_listed:
            db.query(CoinSelectCandidate).filter(CoinSelectCandidate.listed.is_(True)).update(
                {"listed": False}, synchronize_session=False
            )
        reason = (
            "规则分·非 AI：本轮管理员 LLM 有 Key 但未返回可用 JSON（no_llm_response）。"
            "请管理员点「立即重扫」；若反复失败，检查管理员 LLM Key 与模型（应用 deepseek-v4-flash，勿再用已停用的 deepseek-chat）。"
            if any(m.get("degraded") == "no_llm_response" for m in market_rows)
            else "规则分·非 AI：管理员选币 LLM 未就绪。请到「设置 → LLM」为管理员配置 Key（用途 coin_select）。"
        )
        deg = "no_llm_response" if "no_llm_response" in reason else "no_llm"
        for m in market_rows[:12]:
            _add(
                {
                    "symbol": m["symbol"],
                    "horizon": "scalp",
                    "verdict": "watch",
                    "confidence": m.get("score"),
                    "reason": reason,
                    "tier": "watch",
                    "direction": "neutral",
                    "degraded": deg,
                },
                "scalp",
            )
            _add(
                {
                    "symbol": m["symbol"],
                    "horizon": "midlong",
                    "verdict": "watch",
                    "confidence": m.get("score"),
                    "reason": reason,
                    "tier": "watch",
                    "direction": "neutral",
                    "degraded": deg,
                },
                "midlong",
            )
        db.commit()
        return n_s, n_m

    # 有 AI 结果：下架旧 listed，写入新看板
    db.query(CoinSelectCandidate).filter(CoinSelectCandidate.listed.is_(True)).update(
        {"listed": False}, synchronize_session=False
    )
    for it in ai_rows:
        _add(it, "scalp")
    db.commit()
    return n_s, n_m


async def run_platform_scan(*, force: bool = False) -> Dict[str, Any]:
    """执行一轮平台选币扫描。"""
    global _last_scan_ts, _running
    from backend.config.settings import (
        COIN_SELECT_AI_MAX_CANDIDATES,
        COIN_SELECT_BOARD_TTL_HOURS,
        COIN_SELECT_PLATFORM_ENABLED,
        COIN_SELECT_SCAN_INTERVAL_SEC,
    )
    from backend.database.connection import SessionLocal
    from backend.database.models import CoinSelectScan

    if not COIN_SELECT_PLATFORM_ENABLED and not force:
        return {"ok": False, "reason": "platform disabled"}

    with _lock:
        if _running:
            return {"ok": False, "reason": "already running"}
        if not force and (time.time() - _last_scan_ts) < max(60, int(COIN_SELECT_SCAN_INTERVAL_SEC) * 0.5):
            return {"ok": False, "reason": "throttled", "last_ts": _last_scan_ts}
        _running = True

    scan_id = uuid.uuid4().hex[:16]
    admin_tid = resolve_admin_tenant_id()
    t0 = time.time()

    # 调度线程无 JWT：整轮扫描以管理员身份跑，避免 RLS 滤掉 LLM/看板写入
    try:
        from backend.core.tenant import set_request_identity

        if admin_tid:
            set_request_identity(int(admin_tid), "admin")
    except Exception as e:
        logger.warning("[CoinSelectPlatform] identity bootstrap: %s", e)

    db = SessionLocal()
    try:
        scan = CoinSelectScan(
            scan_id=scan_id,
            status="running",
            admin_tenant_id=admin_tid,
            started_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        db.add(scan)
        db.commit()

        market_rows = _scan_market_candidates(limit=max(20, int(COIN_SELECT_AI_MAX_CANDIDATES) + 10))
        scan.candidates_scanned = len(market_rows)
        db.commit()

        batch = market_rows[: int(COIN_SELECT_AI_MAX_CANDIDATES)]
        llm = get_admin_coin_select_llm()
        ai_rows: List[Dict[str, Any]] = []
        degraded = None
        board_kept = False
        if llm and getattr(llm, "api_key", None):
            # 分批审核：单次 15 币易超时/烂 JSON；每批 ≤8
            chunk_size = 8
            for i in range(0, len(batch), chunk_size):
                chunk = batch[i : i + chunk_size]
                prompt = _build_dual_horizon_prompt(chunk)
                part = await _call_admin_ai(llm, prompt)
                if part:
                    ai_rows.extend(part)
                else:
                    logger.warning(
                        "[CoinSelectPlatform] LLM 分批空响应 offset=%d size=%d",
                        i,
                        len(chunk),
                    )
            if not ai_rows:
                degraded = "no_llm_response"
                logger.warning("[CoinSelectPlatform] LLM 全部分批失败，尝试保留既有看板")
        else:
            degraded = "no_llm"
            logger.warning("[CoinSelectPlatform] 管理员 LLM 未配置，降级为规则分·非 AI")

        if degraded:
            for m in market_rows:
                m["degraded"] = degraded

        scan.candidates_ai = len(ai_rows)
        n_s, n_m = _persist_board(db, scan_id, market_rows, ai_rows, int(COIN_SELECT_BOARD_TTL_HOURS))
        if not ai_rows and n_s == 0 and n_m == 0:
            board_kept = True
        scan.board_scalp = n_s
        scan.board_midlong = n_m
        scan.status = "done"
        scan.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
        scan.duration_sec = round(time.time() - t0, 2)
        try:
            scan.meta_json = {
                "degraded": degraded,
                "board_kept": board_kept,
                "rank_source": "coin_rank",
                "llm_ready": bool(llm and getattr(llm, "api_key", None)),
            }
        except Exception:
            pass
        db.commit()
        _last_scan_ts = time.time()
        # 自动跟投短线（仅 approve；长线永不自动；本轮无新板则跳过）
        followed = 0
        if ai_rows and not board_kept:
            try:
                followed = _auto_follow_scalp(db, scan_id)
            except Exception as e:
                logger.warning("[CoinSelectPlatform] auto_follow: %s", e)
                followed = 0
        try:
            from backend.services.coin_rank.metrics import CycleMetrics, record_cycle_metrics

            record_cycle_metrics(
                CycleMetrics(
                    track="platform",
                    scanned=len(market_rows),
                    ai_reviewed=len(ai_rows),
                    injected=followed,
                    soft_reject=sum(1 for m in market_rows if m.get("gate") == "soft_reject"),
                    hard_reject=sum(1 for m in market_rows if m.get("gate") == "hard_reject"),
                    degraded=degraded,
                    rank_source="coin_rank",
                )
            )
        except Exception as e:
            logger.debug("[CoinSelectPlatform] metrics: %s", e)
        return {
            "ok": True,
            "scan_id": scan_id,
            "scanned": len(market_rows),
            "ai": len(ai_rows),
            "board_scalp": n_s,
            "board_midlong": n_m,
            "duration_sec": scan.duration_sec,
            "admin_tenant_id": admin_tid,
            "llm_ready": bool(llm and getattr(llm, "api_key", None)),
            "degraded": degraded,
            "board_kept": board_kept,
            "auto_followed": followed,
        }
    except Exception as e:
        logger.exception("[CoinSelectPlatform] scan failed")
        try:
            scan = db.query(CoinSelectScan).filter(CoinSelectScan.scan_id == scan_id).first()
            if scan:
                scan.status = "failed"
                scan.error_message = str(e)[:1000]
                scan.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
                db.commit()
        except Exception:
            pass
        return {"ok": False, "reason": str(e), "scan_id": scan_id}
    finally:
        db.close()
        with _lock:
            _running = False


def _auto_follow_scalp(db, scan_id: str) -> int:
    """对开启 auto_follow 的 VIP：把本轮强烈推荐短线同步到默认会话。"""
    from backend.database.models import (
        Account,
        CoinSelectAdoption,
        CoinSelectCandidate,
        FullAutoSession,
        User,
    )
    from backend.services.full_auto_trading_service import full_auto_service

    strong = (
        db.query(CoinSelectCandidate)
        .filter(
            CoinSelectCandidate.scan_id == scan_id,
            CoinSelectCandidate.horizon == "scalp",
            CoinSelectCandidate.listed.is_(True),
            CoinSelectCandidate.ai_verdict == "approve",
        )
        .all()
    )
    if not strong:
        return 0
    users = (
        db.query(User)
        .filter(
            User.coin_select_enabled == "true",
            User.coin_select_auto_follow == "true",
            User.coin_select_default_session.isnot(None),
        )
        .all()
    )
    n = 0
    for u in users:
        sid = (u.coin_select_default_session or "").strip()
        if not sid:
            continue
        # 会话归属：必须是本用户账户下的会话
        sess = db.query(FullAutoSession).filter(FullAutoSession.session_id == sid).first()
        if not sess or not sess.account_id:
            logger.debug("[CoinSelectPlatform] auto_follow skip bad session %s", sid)
            continue
        acc = db.query(Account).filter(Account.id == sess.account_id).first()
        if not acc or int(acc.user_id) != int(u.id):
            logger.warning(
                "[CoinSelectPlatform] auto_follow 拒绝：session %s 不属于 user %s",
                sid,
                u.id,
            )
            continue
        # 账户级开关（若显式关闭则跳过）
        acc_flag = (getattr(acc, "ai_coin_select_enabled", None) or "true").lower()
        if acc_flag in ("false", "0", "off", "no"):
            continue
        for c in strong:
            try:
                result = full_auto_service.add_symbols(
                    db, sid, [c.symbol], is_auto_coin=True
                )
                if result.get("success"):
                    c.adopt_count = int(c.adopt_count or 0) + 1
                    db.add(
                        CoinSelectAdoption(
                            user_id=u.id,
                            session_id=sid,
                            symbol=c.symbol,
                            horizon="scalp",
                            candidate_id=c.id,
                        )
                    )
                    n += 1
            except Exception as e:
                logger.debug("[CoinSelectPlatform] auto_follow %s→%s: %s", c.symbol, sid, e)
    if n:
        db.commit()
    return n


def list_board(
    *,
    horizon: Optional[str] = None,
    include_rejected: bool = False,
    admin: bool = False,
    min_score: Optional[float] = None,
    max_trap: Optional[float] = None,
    verdict: Optional[str] = None,
    min_liquidity: Optional[float] = None,
    sort_by: str = "confidence",
) -> Dict[str, Any]:
    from backend.database.connection import SessionLocal
    from backend.database.models import CoinSelectCandidate, CoinSelectScan

    db = SessionLocal()
    try:
        last = (
            db.query(CoinSelectScan)
            .filter(CoinSelectScan.status == "done")
            .order_by(CoinSelectScan.id.desc())
            .first()
        )
        q = db.query(CoinSelectCandidate).filter(CoinSelectCandidate.listed.is_(True))
        if not admin and not include_rejected:
            q = q.filter(CoinSelectCandidate.ai_verdict.in_(["approve", "watch"]))
        elif include_rejected and not admin:
            pass
        if horizon in ("scalp", "midlong"):
            q = q.filter(CoinSelectCandidate.horizon == horizon)
        if verdict in ("approve", "watch", "reject"):
            q = q.filter(CoinSelectCandidate.ai_verdict == verdict)
        rows = q.order_by(CoinSelectCandidate.confidence.desc().nullslast()).limit(200).all()

        items = []
        try:
            from backend.services.coin_rank.feedback import get_hist_map
            live_hist = get_hist_map() or {}
        except Exception:
            live_hist = {}
        for r in rows:
            ms = r.market_scores if isinstance(r.market_scores, dict) else {}
            score_v = float(r.score or 0)
            trap = float(ms.get("trap_soft") or 0)
            liq = float(ms.get("liquidity") or 0)
            if min_score is not None and score_v < float(min_score):
                continue
            if max_trap is not None and trap > float(max_trap):
                continue
            if min_liquidity is not None and liq < float(min_liquidity):
                continue
            # 绩效用实时反馈表覆盖扫描快照，避免「已回写仍显示样本不足」
            live = live_hist.get((r.symbol or "").upper()) or {}
            hist_samples = int(live.get("samples") or ms.get("hist_samples") or 0)
            hist_hit = live.get("hit_rate") if live else ms.get("hist_hit_rate")
            hist_pnl = live.get("avg_pnl_24h") if live else ms.get("hist_avg_pnl_24h")
            items.append(
                {
                    "id": r.id,
                    "scan_id": r.scan_id,
                    "symbol": r.symbol,
                    "horizon": r.horizon,
                    "tier_label": r.tier_label,
                    "score": r.score,
                    "factor_match": r.factor_match,
                    "factor_detail": r.factor_detail,
                    "market_scores": ms,
                    "trap_soft": ms.get("trap_soft"),
                    "mtf_confluence": ms.get("mtf_confluence"),
                    "gate": ms.get("gate"),
                    "liquidity": ms.get("liquidity"),
                    "hist_hit_rate": hist_hit,
                    "hist_avg_pnl_24h": hist_pnl,
                    "hist_samples": hist_samples,
                    "degraded": ms.get("degraded"),
                    "ai_verdict": r.ai_verdict,
                    "ai_reason": r.ai_reason,
                    "confidence": r.confidence,
                    "direction_bias": r.direction_bias,
                    "risk_notes": r.risk_notes,
                    "invalidation": r.invalidation,
                    "valid_until": str(r.valid_until) if r.valid_until else None,
                    "adopt_count": r.adopt_count,
                }
            )

        # Screener 排序
        key = (sort_by or "confidence").lower()
        reverse = True
        if key == "trap":
            items.sort(key=lambda x: float(x.get("trap_soft") or 0), reverse=False)
        elif key == "score":
            items.sort(key=lambda x: float(x.get("score") or 0), reverse=reverse)
        elif key == "liquidity":
            items.sort(key=lambda x: float(x.get("liquidity") or 0), reverse=reverse)
        elif key == "hist_hit":
            items.sort(key=lambda x: float(x.get("hist_hit_rate") or -1), reverse=reverse)
        else:
            items.sort(key=lambda x: float(x.get("confidence") or 0), reverse=reverse)

        # 黄条只看「当前在架卡片」是否降质；扫描 meta 失败但保留了优质板时不吓人
        board_deg = next((i.get("degraded") for i in items if i.get("degraded")), None)
        meta = {}
        try:
            meta = getattr(last, "meta_json", None) or {}
            if not isinstance(meta, dict):
                meta = {}
        except Exception:
            meta = {}

        try:
            _llm = get_admin_coin_select_llm()
            llm_now = bool(_llm and getattr(_llm, "api_key", None))
        except Exception:
            llm_now = False

        degraded = board_deg
        if not degraded and not items and meta.get("degraded"):
            degraded = meta.get("degraded")
        # 实时再探：避免「上次调度因无身份误报 no_llm」一直挂黄条
        if degraded == "no_llm" and llm_now:
            degraded = "stale_board_need_rescan"
        # LLM 已就绪、当前板却是 no_llm_response → 引导重扫，不要装作「没 Key」
        if degraded == "no_llm_response" and llm_now:
            degraded = "no_llm_response"

        return {
            "items": items,
            "degraded": degraded,
            "llm_ready": bool(llm_now),
            "board_kept": bool(meta.get("board_kept")),
            "last_scan": {
                "scan_id": last.scan_id if last else None,
                "finished_at": str(last.finished_at) if last and last.finished_at else None,
                "duration_sec": last.duration_sec if last else None,
                "board_scalp": last.board_scalp if last else 0,
                "board_midlong": last.board_midlong if last else 0,
                "meta": getattr(last, "meta_json", None) if last else None,
            },
        }
    finally:
        db.close()


def admin_scan_detail(limit: int = 20) -> Dict[str, Any]:
    from backend.database.connection import SessionLocal
    from backend.database.models import CoinSelectAdoption, CoinSelectCandidate, CoinSelectScan
    from sqlalchemy import func

    db = SessionLocal()
    try:
        scans = db.query(CoinSelectScan).order_by(CoinSelectScan.id.desc()).limit(limit).all()
        llm = get_admin_coin_select_llm()
        adopt_rows = (
            db.query(
                CoinSelectAdoption.symbol,
                CoinSelectAdoption.horizon,
                func.count(CoinSelectAdoption.id).label("cnt"),
            )
            .group_by(CoinSelectAdoption.symbol, CoinSelectAdoption.horizon)
            .order_by(func.count(CoinSelectAdoption.id).desc())
            .limit(30)
            .all()
        )
        return {
            "admin_tenant_id": resolve_admin_tenant_id(),
            "llm_ready": bool(llm and getattr(llm, "api_key", None)),
            "llm_provider": getattr(llm, "provider", None) if llm else None,
            "llm_model": getattr(llm, "model", None) if llm else None,
            "scans": [
                {
                    "scan_id": s.scan_id,
                    "status": s.status,
                    "started_at": str(s.started_at) if s.started_at else None,
                    "finished_at": str(s.finished_at) if s.finished_at else None,
                    "duration_sec": s.duration_sec,
                    "candidates_scanned": s.candidates_scanned,
                    "candidates_ai": s.candidates_ai,
                    "board_scalp": s.board_scalp,
                    "board_midlong": s.board_midlong,
                    "error_message": s.error_message,
                }
                for s in scans
            ],
            "adopt_stats": [
                {"symbol": r.symbol, "horizon": r.horizon, "count": int(r.cnt)} for r in adopt_rows
            ],
            "factor_exposure_hint": os.getenv("FEATURE_FACTOR_EXPOSURE_ENABLED", "unset"),
        }
    finally:
        db.close()


class CoinSelectPlatformScheduler:
    def __init__(self):
        self._task = None
        self._running = False

    async def start(self):
        if self._running:
            return
        self._running = True
        import asyncio
        self._task = asyncio.ensure_future(self._loop())
        logger.info("[CoinSelectPlatform] scheduler started")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    async def _loop(self):
        import asyncio
        from backend.config.settings import COIN_SELECT_SCAN_INTERVAL_SEC
        # 启动稍后跑一轮
        await asyncio.sleep(45)
        while self._running:
            try:
                await run_platform_scan(force=False)
            except Exception as e:
                logger.warning("[CoinSelectPlatform] loop: %s", e)
            await asyncio.sleep(max(300, int(COIN_SELECT_SCAN_INTERVAL_SEC)))


coin_select_platform_scheduler = CoinSelectPlatformScheduler()
