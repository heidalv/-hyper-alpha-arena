"""
宏观数据采集器 — FRED 日频序列 + 官方日历解析 + 发布后 LLM 影响标注。

[2026-08-15 D6]
    此前系统完全没有宏观经济数据（CPI/FOMC/NFP 等事件零采集），长线趋势
    因子与「重点事件记录」均无输入。本模块全部使用免费官方源：

    1. FRED（api.stlouisfed.org，免费 API key，需在 .env 配 FRED_API_KEY）：
       日频序列 DFF(联邦基金利率)/DGS10(10Y)/DTWEXBGS(DXY)/VIXCLS(VIX)/
       CPIAUCSL(CPI)/UNRATE(失业率)/GOLDAMGBD228NLBM(黄金) → macro_series。
    2. 日历：Fed FOMC 日历 / BLS 发布日程（官方免费 HTML）由正则+LLM 抽取，
       未来事件写入 macro_events（无 forecast 时置空，绝不编造共识值）。
    3. 发布后标注：检测近 24h 应发布的事件 → 抓官方新闻发布页 → LLM 提取
       actual/previous 与影响标注（方向/强度/置信度），复用 NewsImpact 结构。

诚实原则：无 FRED key / 网络不通时优雅空转（offline=True）；官方页面解析
失败不写入；LLM 失败时只写事件事实（actual/previous），不写影响标注。
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

# FRED 免费日频序列（机构级宏观基本面）
FRED_SERIES: List[Tuple[str, str]] = [
    ("DFF", "联邦基金有效利率"),
    ("DGS10", "美国10年期国债收益率"),
    ("DTWEXBGS", "美元指数(广义)"),
    ("VIXCLS", "VIX波动率指数"),
    ("CPIAUCSL", "美国CPI(季调)"),
    ("UNRATE", "美国失业率"),
    ("GOLDAMGBD228NLBM", "伦敦金定盘价"),
]

_last_summary: Dict[str, Any] = {}


def get_last_summary() -> Dict[str, Any]:
    return dict(_last_summary)


def _load_fred_key() -> str:
    key = os.environ.get("FRED_API_KEY", "")
    if key:
        return key
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import SystemConfig
        db = SessionLocal()
        try:
            cfg = db.query(SystemConfig).filter(SystemConfig.key == "FRED_API_KEY").first()
            if cfg and cfg.value:
                os.environ["FRED_API_KEY"] = cfg.value
                return cfg.value
        finally:
            db.close()
    except Exception:
        pass
    return ""


def _get_json(url: str, timeout: float = 15.0) -> Optional[Any]:
    try:
        import urllib.request
        from backend.services.market_aggregation.aggregate_collector_base import _get_proxy
        proxy = None
        try:
            proxy = _get_proxy()
        except Exception:
            pass
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
            if proxy else urllib.request.ProxyHandler({})
        )
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with opener.open(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        logger.debug("[MacroCollector] GET %s 失败: %s", url[:80], exc)
        return None


def _get_html(url: str, timeout: float = 15.0) -> Optional[str]:
    try:
        import urllib.request
        from backend.services.market_aggregation.aggregate_collector_base import _get_proxy
        proxy = None
        try:
            proxy = _get_proxy()
        except Exception:
            pass
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
            if proxy else urllib.request.ProxyHandler({})
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with opener.open(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        logger.debug("[MacroCollector] HTML %s 失败: %s", url[:80], exc)
        return None


def _persist_series(rows: List[Dict[str, Any]]) -> int:
    """幂等写入 macro_series（唯一约束 series_id+ts）。"""
    if not rows:
        return 0
    try:
        from sqlalchemy import text as _sa_text

        from backend.database.connection import MarketSessionLocal
        with MarketSessionLocal() as db:
            db.execute(
                _sa_text(
                    "INSERT INTO macro_series (series_id, name, ts, value) "
                    "VALUES (:series_id, :name, :ts, :value) "
                    "ON CONFLICT (series_id, ts) DO UPDATE SET "
                    "value = EXCLUDED.value, updated_at = now()"
                ),
                rows,
            )
            db.commit()
        return len(rows)
    except Exception as exc:
        logger.warning("[MacroCollector] macro_series 落库失败: %s", exc)
        return 0


def collect_fred_series(days: int = 365) -> Dict[str, Any]:
    """FRED 日频序列回填/续更（免费 key）。"""
    key = _load_fred_key()
    if not key:
        summary = {"ok": False, "offline": True, "written": 0, "reason": "no FRED_API_KEY"}
        _last_summary.clear()
        _last_summary.update(summary)
        return summary

    start = (date.today() - timedelta(days=days)).isoformat()
    written = 0
    fetched_series = 0
    for series_id, name in FRED_SERIES:
        try:
            data = _get_json(
                f"{FRED_BASE}?series_id={series_id}&api_key={key}&file_type=json"
                f"&observation_start={start}"
            )
            obs = (data or {}).get("observations") or []
            rows: List[Dict[str, Any]] = []
            for o in obs:
                v = o.get("value")
                if v in (None, "", "."):
                    continue
                try:
                    d = date.fromisoformat(o.get("date", ""))
                except ValueError:
                    continue
                rows.append({"series_id": series_id, "name": name, "ts": d, "value": float(v)})
            if rows:
                written += _persist_series(rows)
                fetched_series += 1
                logger.info("[MacroCollector] FRED %s(%s) +%d 行", series_id, name, len(rows))
        except Exception as exc:
            logger.debug("[MacroCollector] FRED %s 失败: %s", series_id, exc)

    summary = {
        "ok": fetched_series > 0,
        "offline": fetched_series == 0,
        "written": written,
        "series": fetched_series,
    }
    _last_summary.clear()
    _last_summary.update(summary)
    return summary


# ── 日历（官方免费页面 + LLM 抽取） ─────────────────────────────

_FOMC_CAL_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
_BLS_SCHEDULE_URL = "https://www.bls.gov/schedule/news_release/cpi.htm"
_ECB_CAL_URL = "https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html"


def _parse_dates_from_html(html: str, year: Optional[int] = None) -> List[datetime]:
    """从官方页面抽取日期（美式格式 Month Day[, Year]），返回未来日期。"""
    year = year or datetime.now(timezone.utc).year
    found: List[datetime] = []
    # "January 28-29", "March 18-19", "June 17-18, 2026" 等
    pattern = re.compile(
        r"(January|February|March|April|May|June|July|August|September|October|November|December)"
        r"\s+(\d{1,2})(?:-(\d{1,2}))?(?:,\s*(\d{4}))?",
        re.IGNORECASE,
    )
    for m in pattern.finditer(html):
        month = {
            "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
            "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
            "december": 12,
        }[m.group(1).lower()]
        day = int(m.group(2))
        y = int(m.group(4)) if m.group(4) else year
        try:
            dt = datetime(y, month, day, 18, 0, tzinfo=timezone.utc)  # 官方发布于美东白天
            if dt.date() >= date.today() - timedelta(days=2):
                found.append(dt)
        except ValueError:
            continue
    return found


def _llm_extract_calendar(html: str, source: str) -> List[Dict[str, Any]]:
    """LLM 从官方页面抽取结构化事件（未来 60 天）。失败返回 []。"""
    try:
        from backend.services.llm_config_service import call_llm_api_sync, get_llm_config
        config = get_llm_config()
        if not config:
            return []
        text = re.sub(r"<[^>]+>", " ", html or "")
        text = re.sub(r"\s+", " ", text)[:12000]
        messages = [
            {"role": "system", "content": (
                "你是宏观经济日历解析器。从页面文本中提取未来 60 天内的经济事件。\n"
                '严格返回 JSON 数组：[{"event":"FOMC","scheduled":"2026-08-18T18:00:00Z",'
                '"importance":5,"country":"US"}, ...]\n'
                "event 用标准名：FOMC/CPI/NFP/PPI/GDP/ECB；只返回能确定日期的，不要编造。"
            )},
            {"role": "user", "content": f"来源: {source}\n文本:\n{text}"},
        ]
        resp = call_llm_api_sync(config, messages=messages)
        content = resp["choices"][0]["message"]["content"]
        m = re.search(r"\[.*\]", content, re.DOTALL)
        if not m:
            return []
        items = json.loads(m.group())
        out = []
        for it in items:
            if not isinstance(it, dict) or not it.get("scheduled"):
                continue
            try:
                scheduled = datetime.fromisoformat(str(it["scheduled"]).replace("Z", "+00:00"))
                if scheduled.tzinfo is None:
                    scheduled = scheduled.replace(tzinfo=timezone.utc)
                out.append({
                    "event": str(it.get("event", "macro")).upper()[:60],
                    "scheduled_at": scheduled,
                    "importance": max(1, min(5, int(it.get("importance", 3)))),
                    "country": str(it.get("country", "US")).upper()[:10],
                    "source": source,
                })
            except (ValueError, TypeError):
                continue
        return out
    except Exception as exc:
        logger.debug("[MacroCollector] LLM 日历解析失败: %s", exc)
        return []


def _persist_calendar(items: List[Dict[str, Any]]) -> int:
    """幂等写入未来宏观事件（按 source+event+scheduled_at 去重）。

    [2026-08-15] 只保留「未来 120 天内 + 过去 2 天内」的事件：
    官方页面（如 Fed FOMC 日历）会列出数年后日程，全量入库会让事件轴
    出现遥远日期噪声；窗口外的事件由每日调度自然滚动进入。
    """
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=120)
    floor = now - timedelta(days=2)
    items = [it for it in items if floor <= it["scheduled_at"] <= horizon]
    if not items:
        return 0
    try:
        from sqlalchemy import text as _sa_text

        from backend.database.connection import MarketSessionLocal
        written = 0
        with MarketSessionLocal() as db:
            for it in items:
                existing = db.execute(
                    _sa_text(
                        "SELECT 1 FROM macro_events WHERE source=:s AND event=:e AND scheduled_at=:t"
                    ),
                    {"s": it.get("source"), "e": it.get("event"), "t": it.get("scheduled_at")},
                ).first()
                if existing:
                    continue
                db.execute(
                    _sa_text(
                        "INSERT INTO macro_events "
                        "(source, event, country, importance, scheduled_at) "
                        "VALUES (:source, :event, :country, :importance, :scheduled_at)"
                    ),
                    it,
                )
                written += 1
            db.commit()
        return written
    except Exception as exc:
        logger.warning("[MacroCollector] 日历落库失败: %s", exc)
        return 0


def collect_macro_calendar() -> Dict[str, Any]:
    """抓官方日历页面 → 抽取未来事件 → 落库。"""
    items: List[Dict[str, Any]] = []
    for url, source in ((_FOMC_CAL_URL, "federalreserve"), (_BLS_SCHEDULE_URL, "bls")):
        html = _get_html(url)
        if not html:
            continue
        for dt in _parse_dates_from_html(html):
            items.append({
                "event": "FOMC" if source == "federalreserve" else "CPI",
                "scheduled_at": dt,
                "importance": 5 if source == "federalreserve" else 4,
                "country": "US",
                "source": source,
            })
        items.extend(_llm_extract_calendar(html, source))
    written = _persist_calendar(items)
    summary = {"ok": written > 0, "offline": not items, "written": written}
    _last_summary.clear()
    _last_summary.update(summary)
    logger.info("[MacroCollector] 日历采集: 写入 %d 个未来事件", written)
    return summary


# ── 发布后标注（LLM 提取 actual/previous + 影响） ───────────────

_BLS_CPI_RELEASE = "https://www.bls.gov/news.release/cpi.toc.htm"
_BLS_EMP_RELEASE = "https://www.bls.gov/news.release/empsit.toc.htm"


def annotate_recent_releases() -> Dict[str, Any]:
    """检测近 24h 应发布的宏观事件 → 抓官方发布页 → LLM 提取结果与影响标注。

    事件来源：macro_events 中 scheduled_at 在过去 24h 内且 actual 为空的事件。
    每次至多处理 3 条（控制 LLM 成本）；失败只记日志，不阻塞。
    """
    try:
        from sqlalchemy import text as _sa_text

        from backend.database.connection import MarketSessionLocal
        from backend.services.llm_config_service import call_llm_api_sync, get_llm_config

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=24)
        with MarketSessionLocal() as db:
            rows = db.execute(
                _sa_text(
                    "SELECT id, event, scheduled_at, country FROM macro_events "
                    "WHERE actual IS NULL AND scheduled_at <= :now AND scheduled_at >= :cutoff "
                    "ORDER BY scheduled_at LIMIT 3"
                ),
                {"now": now, "cutoff": cutoff},
            ).fetchall()
        if not rows:
            return {"ok": True, "processed": 0}

        config = get_llm_config()
        processed = 0
        for row in rows:
            eid, event, _sched, country = row
            url = _BLS_CPI_RELEASE if str(event).upper() == "CPI" else _BLS_EMP_RELEASE
            if str(event).upper() == "FOMC":
                url = "https://www.federalreserve.gov/newsevents/pressreleases.htm"
            html = _get_html(url)
            if not html or config is None:
                continue
            text = re.sub(r"<[^>]+>", " ", html)
            text = re.sub(r"\s+", " ", text)[:12000]
            try:
                messages = [
                    {"role": "system", "content": (
                        "你是宏观数据发布解析器。从官方发布页文本中提取该事件的关键数字"
                        "与对加密货币市场的影响。\n"
                        '严格返回 JSON：{"actual": 3.1, "previous": 3.0, "forecast": 2.9, '
                        '"unit": "%", "summary": "一句话摘要", "direction": -0.4, '
                        '"strength": 4, "duration": "medium", "symbols": ["BTC","ETH"], '
                        '"confidence": 0.8}\n'
                        "数字找不到就填 null；direction: -1(极空)~+1(极多)；"
                        "strength: 1~5；duration: short/medium/long。"
                    )},
                    {"role": "user", "content": f"事件: {event}({country})\n来源文本:\n{text}"},
                ]
                resp = call_llm_api_sync(config, messages=messages)
                content = resp["choices"][0]["message"]["content"]
                m = re.search(r"\{.*\}", content, re.DOTALL)
                if not m:
                    continue
                parsed = json.loads(m.group())
            except Exception as exc:
                logger.debug("[MacroCollector] 发布标注 LLM 失败: %s", exc)
                continue
            try:
                with MarketSessionLocal() as db:
                    db.execute(
                        _sa_text(
                            "UPDATE macro_events SET actual=:a, previous=:p, forecast=:f, "
                            "unit=:u, ai_summary=:s, impact_direction=:d, impact_strength=:st, "
                            "impact_duration=:du, affected_symbols=:sym, confidence=:c "
                            "WHERE id=:id"
                        ),
                        {
                            "a": parsed.get("actual"), "p": parsed.get("previous"),
                            "f": parsed.get("forecast"), "u": parsed.get("unit"),
                            "s": str(parsed.get("summary", ""))[:500],
                            "d": parsed.get("direction"), "st": parsed.get("strength"),
                            "du": parsed.get("duration"), "sym": json.dumps(parsed.get("symbols") or []),
                            "c": parsed.get("confidence"), "id": eid,
                        },
                    )
                    db.commit()
                processed += 1
                logger.info("[MacroCollector] 已标注宏观事件 %s(id=%s)", event, eid)
            except Exception as exc:
                logger.warning("[MacroCollector] 宏观事件标注落库失败: %s", exc)
        return {"ok": True, "processed": processed}
    except Exception as exc:
        logger.warning("[MacroCollector] annotate_recent_releases 失败: %s", exc)
        return {"ok": False, "processed": 0, "error": str(exc)[:200]}


def start_macro_collector() -> None:
    """数据中心/主服务后台线程：每日日历 + 每日 FRED 续更 + 每小时发布标注检查。"""
    if os.getenv("MACRO_COLLECTOR_ENABLED", "true").strip().lower() not in (
        "1", "true", "yes", "on",
    ):
        logger.info("[MacroCollector] 已禁用（MACRO_COLLECTOR_ENABLED=false）")
        return

    def _run() -> None:
        time.sleep(90)
        last_daily = 0.0
        while True:
            try:
                now = time.time()
                if now - last_daily >= 86400:
                    collect_fred_series(days=365)
                    collect_macro_calendar()
                    last_daily = now
                annotate_recent_releases()
            except Exception as exc:
                logger.warning("[MacroCollector] 循环异常: %s", exc)
            time.sleep(3600)

    t = threading.Thread(target=_run, name="macro-collector", daemon=True)
    t.start()
    logger.info("[MacroCollector] 已启动（每日日历/FRED + 每小时发布标注）")
