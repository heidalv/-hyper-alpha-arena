# -*- coding: utf-8 -*-
"""[2026-09-24 R1-宏观] 美国宏观事件日历 + 事件窗口入场闸（BTC/ETH 主导）。

## 来源（官方日历，2026 美东时间；发布时刻换算为 CST）
  - FOMC 决议：美联储官方日历（federalreserve.gov/monetarypolicy/fomccalendars.htm）
      2026: Jan27-28 · Mar17-18 · Apr28-29 · Jun16-17 · Jul28-29 · **Sep15-16** · Oct27-28 · Dec8-9
      决议公布 14:00 ET = 次日 **02:00 CST**
  - Employment Report（非农）：2026 Jan9 Feb6 Mar6 Apr3 May8 Jun5 Jul2 Aug7 **Sep4** Oct2 Nov6 Dec4
  - CPI:2026 Jan13 Feb11 Mar11 Apr10 May12 Jun10 Jul14 Aug12 **Sep11** Oct14 Nov10 Dec10
  - PPI:2026 Sep10 Oct15 Nov13 Dec15
      非农/CPI/PPI 公布 08:30 ET = **20:30 CST**

## 用户口径
「美国宣布非农经济、宣布加息降息的时间，都是变盘时间，尤其是 btc 和 eth，主导全部经济」

## 实测（`scripts/audit_macro_event_impact_20260924.py`，2026-01-15~2026-09-23 的 1h K 线，
## 12 个币；这是**市场结构**统计，不是我们的业绩记录）
  1) 事件窗口波动比（事件前后 12h 已实现波动 / 滚动 24h 基线中位 1.503%）差异极大：
     2 月 6 日非农 3.80~3.96 倍、6 月 5 日非农 3.21~3.82 倍（都是"事件前已经走了一大段"），
     而 4 月 3 日非农 0.53、8 月 7 日非农 0.46、8 月 12 日 CPI 0.51（比平常还安静）。
     ⇒ 「事件日 = 变盘日」**不是无条件的**，只有"事件前已有大幅单边移动"时才成立。
  2) 「变盘/反转」检验（事件前 24h 与后 24h 收益符号相反的比例，基线 52%）：
       FOMC 36%（低于基线，倾向**延续**）· NFP 39%（低于基线）· **CPI 64%（高于基线）**；
       其中 **CPI 且事件前 24h 已移动 >3%：18/20 = 90% 反转** ← 本闸的唯一依据。
  3) 领先性：BTC 与各山寨 1h 收益**同小时**相关 0.57~0.88，但滞后相关 k=±1..3 全部 ≈0.00~0.03。
     ⇒ BTC/ETH 在小时级**不领先**，是同步共振；因此本模块不做"BTC 领先"择时，
     只把 BTC/ETH 当作**同一笔 beta**（组合 10 个币 ≈ 1 个仓位）。

## 闸的行为（默认关闭，需显式开启）
  仅对 `MACRO_EVENT_KINDS`（默认 cpi）生效：在事件前 `MACRO_EVENT_PRE_H` 小时内，
  若该币 24h 已朝**拟开仓方向**移动超过 `MACRO_EVENT_REVERSAL_PRE_MOVE_PCT`（默认 3%），
  则缩仓 ×`MACRO_EVENT_SHRINK_MULT`（默认 0.25；`MACRO_EVENT_VETO=true` 时改为硬拦）。
  FOMC / 非农的翻转率低于基线（延续性），**不拦**（避免无依据地压制开仓）。
"""
from __future__ import annotations

import datetime as dt
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

CST = dt.timezone(dt.timedelta(hours=8))

# ── 2026 日历：(kind, "YYYY-MM-DD", 发布小时(CST，可含小数)) ──
# FOMC 决议 14:00 ET = 次日 02:00 CST → 用 hour=26 表达"次日 02:00"
# 非农/CPI/PPI 08:30 ET = 12:30 UTC = **20:30 CST** → hour=20.5
CALENDAR_2026: List[Tuple[str, str, float]] = [
    ("fomc", "2026-01-28", 26), ("fomc", "2026-03-18", 26), ("fomc", "2026-04-29", 26),
    ("fomc", "2026-06-17", 26), ("fomc", "2026-07-29", 26), ("fomc", "2026-09-16", 26),
    ("fomc", "2026-10-28", 26), ("fomc", "2026-12-09", 26),
    ("nfp", "2026-01-09", 20.5), ("nfp", "2026-02-06", 20.5), ("nfp", "2026-03-06", 20.5),
    ("nfp", "2026-04-03", 20.5), ("nfp", "2026-05-08", 20.5), ("nfp", "2026-06-05", 20.5),
    ("nfp", "2026-07-02", 20.5), ("nfp", "2026-08-07", 20.5), ("nfp", "2026-09-04", 20.5),
    ("nfp", "2026-10-02", 20.5), ("nfp", "2026-11-06", 20.5), ("nfp", "2026-12-04", 20.5),
    ("cpi", "2026-01-13", 20.5), ("cpi", "2026-02-11", 20.5), ("cpi", "2026-03-11", 20.5),
    ("cpi", "2026-04-10", 20.5), ("cpi", "2026-05-12", 20.5), ("cpi", "2026-06-10", 20.5),
    ("cpi", "2026-07-14", 20.5), ("cpi", "2026-08-12", 20.5), ("cpi", "2026-09-11", 20.5),
    ("cpi", "2026-10-14", 20.5), ("cpi", "2026-11-10", 20.5), ("cpi", "2026-12-10", 20.5),
    ("ppi", "2026-09-10", 20.5), ("ppi", "2026-10-15", 20.5), ("ppi", "2026-11-13", 20.5),
    ("ppi", "2026-12-15", 20.5),
]

_LABEL = {"fomc": "美联储利率决议", "nfp": "非农就业", "cpi": "CPI", "ppi": "PPI"}


def _env_b(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _env_f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


def _ts(kind: str, datestr: str, hour: float) -> int:
    y, m, d = (int(x) for x in datestr.split("-"))
    base = dt.datetime(y, m, d, 0, 0, tzinfo=CST) + dt.timedelta(hours=float(hour))
    return int(base.timestamp())


def all_events() -> List[Dict[str, Any]]:
    out = []
    for kind, d, h in CALENDAR_2026:
        out.append({"kind": kind, "date": d, "label": _LABEL.get(kind, kind), "ts": _ts(kind, d, h)})
    out.sort(key=lambda x: x["ts"])
    return out


def guard_enabled() -> bool:
    """总开关（默认关闭）。开启后仅对 `MACRO_EVENT_KINDS` 生效。"""
    return _env_b("MACRO_EVENT_GUARD_ENABLED", False)


def _kinds() -> set:
    raw = (os.getenv("MACRO_EVENT_KINDS", "cpi") or "cpi").strip().lower()
    return {x.strip() for x in raw.split(",") if x.strip()}


def pre_hours() -> float:
    return max(0.0, _env_f("MACRO_EVENT_PRE_H", 12.0))


def reversal_pre_move_pct() -> float:
    return abs(_env_f("MACRO_EVENT_REVERSAL_PRE_MOVE_PCT", 3.0))


def shrink_mult() -> float:
    return max(0.05, min(1.0, _env_f("MACRO_EVENT_SHRINK_MULT", 0.25)))


def veto_mode() -> bool:
    return _env_b("MACRO_EVENT_VETO", False)


def next_event(now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    now = float(now if now is not None else dt.datetime.now(CST).timestamp())
    for ev in all_events():
        if ev["ts"] >= now:
            e = dict(ev)
            e["hours_until"] = (ev["ts"] - now) / 3600.0
            return e
    return None


def last_event(now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    now = float(now if now is not None else dt.datetime.now(CST).timestamp())
    done = [ev for ev in all_events() if ev["ts"] <= now]
    if not done:
        return None
    e = dict(done[-1])
    e["hours_since"] = (now - e["ts"]) / 3600.0
    return e


def describe(now: Optional[float] = None) -> str:
    """一行摘要，供报表/状态展示。"""
    now = float(now if now is not None else dt.datetime.now(CST).timestamp())
    nxt = next_event(now)
    lst = last_event(now)
    parts = []
    if lst:
        parts.append("上一事件 %s(%s) 已过 %.1fh" % (lst["label"], lst["date"], lst["hours_since"]))
    if nxt:
        parts.append("下一事件 %s(%s) 还有 %.1fh" % (nxt["label"], nxt["date"], nxt["hours_until"]))
    return "；".join(parts) if parts else "无日历数据"


def entry_guard(
    symbol: str,
    side: str,
    market_summary: Optional[Dict[str, Any]] = None,
    now: Optional[float] = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """事件窗口入场闸。

    返回 (是否放行, 原因, detail)。detail 可含 `paper_shrink_mult`（与位置闸同一约定）。
    数据缺失/异常一律 fail-open（不因缺数据拦开仓）。
    """
    if not guard_enabled():
        return True, "macro_guard_off", {}
    kinds = _kinds()
    if not kinds:
        return True, "macro_guard_nokinds", {}
    now = float(now if now is not None else dt.datetime.now(CST).timestamp())
    ev = next_event(now)
    if not ev or ev["kind"] not in kinds:
        return True, "macro_guard_clear", {}
    if ev["hours_until"] > pre_hours():
        return True, "macro_guard_far(%.1fh)" % ev["hours_until"], {}
    # 事件前窗口：取该币 24h 涨跌幅，判断是否"顺着已走完的方向开仓"
    sym = str(symbol or "").upper()
    chg24 = None
    if isinstance(market_summary, dict):
        blk = market_summary.get(sym) or market_summary.get(symbol) or {}
        if isinstance(blk, dict):
            for k in ("price_change_24h_pct", "change_24h_pct", "pct_change_24h"):
                v = blk.get(k)
                if v is None:
                    continue
                try:
                    chg24 = float(v)
                    break
                except (TypeError, ValueError):
                    continue
    if chg24 is None:
        return True, "macro_guard_nodata(fail-open)", {"event": ev["label"]}
    thr = reversal_pre_move_pct()
    act = str(side or "").strip().lower()
    chasing_long = act in ("buy", "long") and chg24 >= thr
    chasing_short = act in ("sell", "short") and chg24 <= -thr
    if not (chasing_long or chasing_short):
        return True, "macro_guard_ok(chg24=%+.1f,thr=%.1f)" % (chg24, thr), {"event": ev["label"]}
    reason = "%s前%.1fh 且24h已%s%.1f%%(≥%.1f%%) 事件反转风险" % (
        ev["label"], ev["hours_until"], "涨" if chasing_long else "跌", abs(chg24), thr)
    if veto_mode():
        return False, "macro_event_veto: " + reason, {"event": ev["label"], "chg24": chg24}
    return True, "macro_event_shrink: " + reason, {
        "event": ev["label"], "chg24": chg24, "paper_shrink_mult": shrink_mult(),
    }
