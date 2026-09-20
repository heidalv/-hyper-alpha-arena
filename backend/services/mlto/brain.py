"""中长线 LLM 主脑（L0–L5）。

唯一开仓理由：包月双模型（Minimax + GLM）对这笔交易给出 accepted 论题，
且 recommend_open=true、方向明确、未过期。因子 / V2 / E1 / 图审只当证据；
图审有新鲜共识时才做否决，缺图不挡分析、也不挡开仓。

没有新鲜论题 = 不开。不回退因子。
"""
from __future__ import annotations

import json
import copy
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from backend.services.analysis import schemas
from backend.services.analysis.model_gateway import get_model_gateway
from backend.services.mlto.types import ThesisDTO

logger = logging.getLogger(__name__)

# [2026-09-07] 异步脑批次全局信号量：同一时刻只跑一个后台批次（日内/中/长串行），
# 避免三档并行把 CPU/GIL 打满、饿死 API（实测 tier-status 25s 超时）。
# 循环不阻塞（派单即返回）+ 后台不堆 CPU（串行）两全。
_BRAIN_BG_SEM = threading.Semaphore(1)

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "analysis"
CONSENSUS_THRESHOLD = 0.7
# [2026-09-07] 主脑上下文预算：GLM-5.3/MiniMax-M3 均为 1M 上下文窗口，
# 18k 是旧 200K 时代的保守值。100k 可喂完整多周期 K 线 + 多币面板 + 证据。
# env 可调低回滚。
_BRAIN_CONTEXT_TOKEN_BUDGET = int(os.getenv("MIDLONG_BRAIN_CONTEXT_TOKENS", "100000") or 100000)
_BRAIN_EXTRAS_CHAR_BUDGET = int(os.getenv("MIDLONG_BRAIN_EXTRAS_CHARS", "60000") or 60000)


def brain_prompt_version() -> str:
    """[2026-09-07] 主脑 system prompt 版本号（OPRO 地基）。

    取 system prompt 的短哈希：prompt 一改版本即变，论题/成交都带上它，
    事后可按版本统计胜率/净盈亏，为 Adaptive-OPRO 滚动优化提供评分依据。
    """
    import hashlib
    try:
        return "bp_" + hashlib.sha256(_system_prompt().encode("utf-8")).hexdigest()[:10]
    except Exception:
        return "bp_unknown"
_MISSING_PRICE_KEYS = ("现价", "价格", "last", "mark", "k线", "kline", "ohlc")
_REFRESH_LOCKS: Dict[str, threading.Lock] = {}
_REFRESH_LOCKS_GUARD = threading.Lock()
_BATCH_LOCKS: Dict[str, threading.Lock] = {}
_BATCH_LOCKS_GUARD = threading.Lock()


def _named_lock(table: Dict[str, threading.Lock], guard: threading.Lock, key: str) -> threading.Lock:
    with guard:
        lock = table.get(key)
        if lock is None:
            lock = threading.Lock()
            table[key] = lock
        return lock


def midlong_brain_enabled() -> bool:
    try:
        from backend.config.settings import midlong_brain_enabled as _fn
        return bool(_fn())
    except Exception:
        return (os.getenv("MIDLONG_BRAIN_MODE", "llm") or "llm").strip().lower() == "llm"


def _tier3(tier) -> str:
    """三档归一：long / short / mid（其余一律落 mid）。

    [2026-09-07] short = LLM 日内波段车道（4-12h 持仓）；此前非 long 全塌缩成 mid，
    日内波段会被错用中线参数。
    """
    t = str(tier or "").strip().lower()
    if t == "long":
        return "long"
    if t == "short":
        return "short"
    return "mid"


def _short_gate_hint(symbol: str, tier: str) -> str:
    """[2026-09-18 根因修复·方向语义] 空头会被 regime 闸拦时，应注入的**硬约束**文本。

    为什么抽成函数：原先是内联在 prompt 拼装处的一大段，**只能做源码文本断言**；
    而"文本存在"≠"条件在真实分支下成立"（我这一轮就因为漏了 `regime 未知` 这一档
    而写出过错误的条件）。抽出来后可以**直接对条件与文案做单测**。

    口径与 `full_auto/midlong_circuit_gate` 的准入**同源**（不另写一套）：
      · `short` 档豁免（日内波段自己的路径）⇒ 不注入；
      · `MIDLONG_SHORT_MODE=regime_gated` 下，**日线 regime=空（不可判）也会 fail-closed
        拦截**（`midlong_short_regime_unknown`）⇒ 必须同样注入（这是我第一版的漏洞）；
      · `down` 才放行 ⇒ 不注入；
      · 其它模式/异常 ⇒ 返回 ""（fail-safe：不注入也不改变既有行为）。
    """
    try:
        if _tier3(tier) == "short":
            return ""
        from backend.services.full_auto.midlong_circuit_gate import (
            _daily_regime,
            _short_mode,
        )
        if str(_short_mode()) != "regime_gated":
            return ""
        reg = str(_daily_regime(str(symbol or "").upper()) or "").strip().lower()
        if reg == "down":
            return ""
        if not reg:
            _why = ("日线 regime **不可判**（数据不足），闸门对此 fail-closed —— "
                    "空头同样会被全部拦截")
            _reg_txt = "不可判"
        else:
            _why = f"日线 regime={reg}（非下行），空头开仓会被 regime 闸**全部拦截**"
            _reg_txt = reg
        return (
            f"【风控硬约束】当前 {symbol} {_why}。"
            "因此：**direction 必须写 neutral**（bearish 观点请写进 reasoning 字段，"
            "不要写进 direction）——direction 会被下游当作可执行意图，"
            "写 short 只会产生必然被拒的提案，并压低本币多头提案的评分。"
            f"（本币日线 regime={_reg_txt}）要做请评估多头，否则观望。"
        )
    except Exception:  # noqa: BLE001 — 注入失败不得影响主链路
        return ""


def thesis_ttl_s(tier: str) -> int:
    try:
        from backend.config.settings import (
            MIDLONG_THESIS_TTL_LONG_S,
            MIDLONG_THESIS_TTL_MID_S,
            MIDLONG_THESIS_TTL_SHORT_S,
        )
        t = _tier3(tier)
        if t == "long":
            return int(MIDLONG_THESIS_TTL_LONG_S)
        if t == "short":
            return int(MIDLONG_THESIS_TTL_SHORT_S)
        return int(MIDLONG_THESIS_TTL_MID_S)
    except Exception:
        t = _tier3(tier)
        return 28800 if t == "long" else (3600 if t == "short" else 14400)


def thesis_fail_backoff_s(tier: str) -> int:
    """失败票短退避：不得占满成功票 TTL。"""
    try:
        from backend.config.settings import (
            MIDLONG_THESIS_FAIL_BACKOFF_LONG_S,
            MIDLONG_THESIS_FAIL_BACKOFF_MID_S,
            MIDLONG_THESIS_FAIL_BACKOFF_SHORT_S,
        )
        t = _tier3(tier)
        if t == "long":
            return int(MIDLONG_THESIS_FAIL_BACKOFF_LONG_S)
        if t == "short":
            return int(MIDLONG_THESIS_FAIL_BACKOFF_SHORT_S)
        return int(MIDLONG_THESIS_FAIL_BACKOFF_MID_S)
    except Exception:
        t = _tier3(tier)
        return 2400 if t == "long" else (600 if t == "short" else 1200)


def thesis_expiry_s(tier: str, *, accepted: bool) -> int:
    return thesis_ttl_s(tier) if accepted else thesis_fail_backoff_s(tier)


_CST = timezone(timedelta(hours=8))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    """读回的 expires_at 常是无时区墙上时间。

    analytics 库 TIMESTAMP 无时区；连接 +08 时，aware UTC 写入会被转成北京时间。
    若再按 UTC 解释，4 小时 TTL 会假活 12 小时，过期论题一直不刷新。
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_CST).astimezone(timezone.utc)
    return dt.astimezone(timezone.utc)


_TF_MS = {"1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000, "1w": 604_800_000}


def _short_active(symbol: str, market_summary: Optional[Dict[str, Any]]) -> bool:
    """日内波段活跃档判定（2026-09-07 自适应频率，用户拍板）。

    平时档：1h 决策时钟 + watch 10min + 冲击 0.7%；
    活跃档（波动放大）：15m 决策时钟 + watch 5min + 冲击 0.5%。
    触发（纯规则无前视）：|1h 涨跌| ≥ 1.5% 或 volatility_value ≥ 2%。
    """
    try:
        from backend.config.settings import INTRADAY_ADAPTIVE_FREQ_ENABLED
        if not INTRADAY_ADAPTIVE_FREQ_ENABLED:
            return False
    except Exception:
        return False
    try:
        from backend.config import settings as _s
        vol_th = float(getattr(_s, "INTRADAY_ACTIVE_VOL_PCT", 0.02) or 0.02)
        chg_th = float(getattr(_s, "INTRADAY_ACTIVE_1H_CHANGE_PCT", 0.015) or 0.015)
    except Exception:
        vol_th, chg_th = 0.02, 0.015
    ms = (market_summary or {}).get(str(symbol or "").upper()) or {}
    if not isinstance(ms, dict):
        return False
    vol = 0.0
    for k in ("volatility_value", "volatility_pct"):
        try:
            v = ms.get(k)
            if v is not None:
                vol = float(v)
                break
        except (TypeError, ValueError):
            continue
    if vol > 1:  # 百分数归一到小数（2.0 → 0.02）
        vol = vol / 100.0
    chg = 0.0
    for k in ("price_change_1h", "price_change_1h_pct", "change_1h"):
        try:
            v = ms.get(k)
            if v is not None:
                chg = float(v)
                break
        except (TypeError, ValueError):
            continue
    if abs(chg) > 1:
        chg = chg / 100.0
    return vol >= vol_th or abs(chg) >= chg_th


def decision_bar_tf(tier: str, active: bool = False) -> str:
    """日内波段跟 1h 收盘（活跃档 15m），中线跟 4h 收盘，长线跟日线收盘。这是行情时钟，不是我们的闹钟。"""
    t = _tier3(tier)
    if t == "long":
        return "1d"
    if t == "short":
        return "15m" if active else "1h"
    return "4h"


def current_bar_open_ms(tf: str, now_ms: Optional[int] = None) -> int:
    step = int(_TF_MS.get(str(tf), _TF_MS["4h"]))
    now = int(now_ms if now_ms is not None else _utcnow().timestamp() * 1000)
    return (now // step) * step


def _watch_of(dto: Optional[ThesisDTO]) -> Dict[str, Any]:
    inv = getattr(dto, "invalidation", None) if dto is not None else None
    if not isinstance(inv, dict):
        return {}
    watch = inv.get("_watch")
    return watch if isinstance(watch, dict) else {}


def _attach_watch(inv: Any, snap: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(inv, dict):
        out = dict(inv)
    elif isinstance(inv, str) and inv.strip():
        out = {"condition": inv.strip()}
    else:
        out = {}
    out["_watch"] = snap
    return out


def _inv_price(inv: Any) -> Optional[float]:
    """从 invalidation 取机读价。支持嵌套 JSON 字符串和常见字段名。"""
    obj = inv
    if isinstance(obj, str) and obj.strip():
        s = obj.strip()
        if s.startswith("{"):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, dict):
                    obj = parsed
            except Exception:
                return None
        else:
            return None
    if not isinstance(obj, dict):
        return None
    for key in ("price", "invalidation_price", "price_level", "level"):
        raw = obj.get(key)
        if raw is None or raw == "":
            continue
        try:
            px = float(raw)
        except (TypeError, ValueError):
            continue
        if px > 0:
            return px
    # condition 里塞了 {"price": ...} 的旧票
    cond = obj.get("condition")
    if isinstance(cond, str) and "{" in cond:
        try:
            nested = json.loads(cond)
            if isinstance(nested, dict):
                return _inv_price(nested)
        except Exception:
            pass
    return None


def normalize_invalidation(raw: Any) -> Dict[str, Any]:
    """统一成 {price, condition, ...}；无数字价则 price 缺失。"""
    if isinstance(raw, str) and raw.strip():
        s = raw.strip()
        if s.startswith("{"):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, dict):
                    return normalize_invalidation(parsed)
            except Exception:
                pass
        return {"condition": s}
    if not isinstance(raw, dict):
        return {}
    out = {k: v for k, v in raw.items() if k != "_watch"}
    px = _inv_price(out)
    if px is not None:
        out["price"] = px
    return out


_SOFT_MISS_MARKERS = (
    "图审", "chart", "taker", "trend_e1", "e1", "历史胜率", "n_scored",
    "source=none", "accepted=false", "dual:trend_chart", "trend_chart_review",
    "无分时", "订单簿", "精确档位", "regime confidence",
    # [2026-09-06] 下列是「想要但没有也不该绑架不开」的软证据；硬缺仍只认现价/K线。
    "correlation", "corr_to_btc", "相关性", "oi", "多空比", "funding", "资金费",
    "成交量", "量能", "量z", "缩量", "whale", "order_book", "fair_value",
    "daily_brief", "stale", "v2", "l1=", "factor_route", "因子路由",
    "历史亏", "pnl", "high_impact_news", "news_density", "支撑", "阻力", "档位",
)


def is_soft_missing_item(item: Any) -> bool:
    s = str(item or "").strip().lower()
    if not s:
        return True
    if missing_blocks_open([item]):
        return False
    return any(m in s for m in _SOFT_MISS_MARKERS)


def strip_soft_missing(missing: Sequence[Any]) -> List[str]:
    """硬缺项（现价/K线）保留；图审/taker/E1 等软项剥掉，不得绑架开仓。"""
    out: List[str] = []
    for raw in missing or []:
        s = str(raw or "").strip()
        if not s:
            continue
        if is_soft_missing_item(s):
            continue
        if s not in out:
            out.append(s)
    return out[:10]


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


def _shock_pct(tier: str, active: bool = False) -> float:
    t = _tier3(tier)
    if t == "long":
        return max(0.005, _env_float("MIDLONG_WATCH_SHOCK_PCT_LONG", 0.025))
    # [2026-09-07] 日内波段专用：平时 0.7%（对 SL 2% 匹配），活跃档 0.5%；此前落中线 1.2% 太粗
    if t == "short":
        if active:
            return max(0.003, _env_float("MIDLONG_WATCH_SHOCK_PCT_SHORT_ACTIVE", 0.005))
        return max(0.003, _env_float("MIDLONG_WATCH_SHOCK_PCT_SHORT", 0.007))
    return max(0.004, _env_float("MIDLONG_WATCH_SHOCK_PCT_MID", 0.012))


def _chase_pct(tier: str, active: bool = False) -> float:
    t = _tier3(tier)
    if t == "long":
        return max(0.004, _env_float("MIDLONG_WATCH_CHASE_PCT_LONG", 0.015))
    if t == "short":
        if active:
            return max(0.002, _env_float("MIDLONG_WATCH_CHASE_PCT_SHORT_ACTIVE", 0.0035))
        return max(0.002, _env_float("MIDLONG_WATCH_CHASE_PCT_SHORT", 0.005))
    return max(0.003, _env_float("MIDLONG_WATCH_CHASE_PCT_MID", 0.008))


def _near_inv_pct() -> float:
    return max(0.001, _env_float("MIDLONG_WATCH_NEAR_INV_PCT", 0.004))


def _min_refresh_s(tier: str = "", active: bool = False) -> int:
    # [2026-09-07] 日内波段专用：平时 10min、活跃档 5min；此前落中线 30min 太慢
    if _tier3(tier) == "short":
        env_key = "MIDLONG_WATCH_MIN_REFRESH_SHORT_ACTIVE_S" if active else "MIDLONG_WATCH_MIN_REFRESH_SHORT_S"
        default = 300 if active else 600
        try:
            return max(60, int(float(os.getenv(env_key, str(default)) or default)))
        except (TypeError, ValueError):
            return default
    try:
        return max(120, int(float(os.getenv("MIDLONG_WATCH_MIN_REFRESH_S", "1800") or 1800)))
    except (TypeError, ValueError):
        return 1800


def _spot_from_summary(market_summary: Optional[Dict[str, Any]], symbol: str) -> Optional[float]:
    if not isinstance(market_summary, dict) or not symbol:
        return None
    want = {_norm_base_symbol(symbol), str(symbol).upper()}
    for key, val in market_summary.items():
        ku = str(key or "").upper()
        if _norm_base_symbol(ku) not in want and ku not in want:
            continue
        if isinstance(val, dict):
            for fk in ("last", "mark", "price", "close", "last_price", "mark_price"):
                try:
                    px = float(val.get(fk) or 0)
                except (TypeError, ValueError):
                    continue
                if px > 0:
                    return px
        else:
            try:
                px = float(val)
            except (TypeError, ValueError):
                continue
            if px > 0:
                return px
    return None


def _spot_price(symbol: str, market_summary: Optional[Dict[str, Any]] = None) -> Optional[float]:
    px = _spot_from_summary(market_summary, symbol)
    if px:
        return px
    try:
        from backend.services.analysis.context_pack import _klines
        kl = _klines(symbol, "1h", 2)
        if kl:
            last = float(kl[-1].get("close") or 0)
            return last if last > 0 else None
    except Exception:
        return None
    return None


def capture_watch_snapshot(
    symbol: str,
    tier: str,
    market_summary: Optional[Dict[str, Any]] = None,
    now_ms: Optional[int] = None,
) -> Dict[str, Any]:
    # [2026-09-07] 快照的 bar_tf 必须与自适应决策时钟一致（否则 bar_close 触发器错拍）
    _active = _short_active(symbol, market_summary) if _tier3(tier) == "short" else False
    tf = decision_bar_tf(tier, _active)
    ms = int(now_ms if now_ms is not None else _utcnow().timestamp() * 1000)
    return {
        "price": _spot_price(symbol, market_summary),
        "bar_tf": tf,
        "bar_open_ms": current_bar_open_ms(tf, ms),
        "captured_ms": ms,
    }


def thesis_watch_reason(
    dto: Optional[ThesisDTO],
    symbol: str,
    tier: Optional[str] = None,
    market_summary: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
    ignore_cooldown: bool = False,
) -> Optional[str]:
    """行情变了就重问。TTL 没到也算。查询失败不当成变了。

    ignore_cooldown=True 给开仓闸用：价格已经跑飞必须立刻停手，
    但重问模型仍受 30 分钟冲击冷却，避免阴跌把配额打光。
    """
    now_dt = now or _utcnow()
    if dto is None or not thesis_is_fresh(dto, now=now_dt):
        return None
    now_ms = int(now_dt.timestamp() * 1000)
    tier_l = _tier3(tier or getattr(dto, "tier", "mid"))
    # [2026-09-07] 日内波段自适应：活跃档判定一次，贯穿决策时钟/watch 参数
    _active = _short_active(symbol, market_summary) if tier_l == "short" else False
    tf = decision_bar_tf(tier_l, _active)
    bar_open = current_bar_open_ms(tf, now_ms)
    updated = _aware(getattr(dto, "updated_at", None))
    updated_ms = int(updated.timestamp() * 1000) if updated is not None else 0
    watch = _watch_of(dto)
    th_dir = str(getattr(dto, "direction", "") or "").lower()

    snap_bar = watch.get("bar_open_ms")
    try:
        snap_bar_i = int(snap_bar) if snap_bar not in (None, "") else 0
    except (TypeError, ValueError):
        snap_bar_i = 0
    if snap_bar_i > 0 and snap_bar_i < bar_open:
        return f"bar_close:{tf}"
    if snap_bar_i <= 0 and updated_ms > 0 and updated_ms < bar_open:
        return f"bar_close:{tf}"

    inv = getattr(dto, "invalidation", None)
    ipx = _inv_price(inv)
    spot = _spot_price(symbol, market_summary)
    if ipx and spot:
        if th_dir == "long" and spot < ipx:
            return "invalidation_hit"
        if th_dir == "short" and spot > ipx:
            return "invalidation_hit"
        if abs(spot - ipx) / ipx <= _near_inv_pct():
            return "invalidation_near"

    try:
        from backend.services.full_auto.midlong_chart_gate import _latest_chart_signal
        sig = _latest_chart_signal(symbol)
        if sig and int(sig.get("created_ms") or 0) > updated_ms:
            payload = sig.get("payload") if isinstance(sig.get("payload"), dict) else {}
            advice = str((payload or {}).get("position_advice") or "").lower()
            chart_dir = str(sig.get("direction") or (payload or {}).get("direction") or "").lower()
            if advice in ("invalidate", "close", "flat"):
                return "chart_invalidate"
            if th_dir == "long" and ("long" in advice or chart_dir in ("bearish", "short")):
                return "chart_conflict"
            if th_dir == "short" and ("short" in advice or chart_dir in ("bullish", "long")):
                return "chart_conflict"
    except Exception:
        pass

    try:
        from backend.services.analysis import ledgers
        rows = ledgers.list_signals(source="dual:event_impact", symbol=symbol, limit=8) or []
        for r in rows:
            if int(r.get("created_ms") or 0) <= updated_ms:
                continue
            if abs(float(r.get("strength") or 0)) >= 6:
                return "event_shock"
    except Exception:
        pass

    if (
        (not ignore_cooldown)
        and updated is not None
        and (now_dt - updated).total_seconds() < _min_refresh_s(tier_l, _active)
    ):
        return None

    try:
        ref = float(watch["price"]) if watch.get("price") not in (None, "") else 0.0
    except (TypeError, ValueError):
        ref = 0.0
    if ref > 0 and spot and spot > 0:
        signed = (spot - ref) / ref
        chase = _chase_pct(tier_l, _active)
        shock = _shock_pct(tier_l, _active)
        if th_dir == "long" and signed >= chase:
            return f"chase:{signed:.3f}"
        if th_dir == "short" and signed <= -chase:
            return f"chase:{signed:.3f}"
        if abs(signed) >= shock:
            return f"price_shock:{signed:.3f}"
    return None


def thesis_is_fresh(dto: Optional[ThesisDTO], now: Optional[datetime] = None) -> bool:
    """主脑新鲜论题：必须带 analysis_run_id，且未过期。

    成功票 expires_at = 满 TTL；失败票 = 短退避。本函数只看 expires_at，
    由 refresh_thesis 写入时区分。旧影子行无 run_id / expires_at 不算鲜。
    """
    if dto is None:
        return False
    if not str(getattr(dto, "analysis_run_id", "") or "").strip():
        return False
    exp = _aware(getattr(dto, "expires_at", None))
    if exp is None:
        return False
    return exp > _aware(now or _utcnow())


def thesis_is_tradeable_fresh(dto: Optional[ThesisDTO], now: Optional[datetime] = None) -> bool:
    """可交易的新鲜论题：accepted 且未过期。"""
    return bool(dto and getattr(dto, "accepted", False) and thesis_is_fresh(dto, now=now))


def order_refresh_symbols(
    session_id: str,
    symbols: Sequence[str],
    tier: str,
    priority: Optional[Sequence[str]] = None,
    market_summary: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """有仓、行情已变、缺 run_id、过期的排前面，避免按字母永远先刷 ASTER/BNB。"""
    pri = {str(s).upper() for s in (priority or []) if s}
    tier_l = _tier3(tier)
    ranked: List[tuple] = []
    seen = set()
    from backend.services.mlto import thesis_store
    for raw in symbols:
        sym = str(raw or "").upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        dto = None
        try:
            dto = thesis_store.get(session_id, sym, tier_l)
        except Exception:
            dto = None
        no_run = not bool(getattr(dto, "analysis_run_id", "") or "") if dto else True
        stale = not thesis_is_fresh(dto)
        changed = bool(thesis_watch_reason(dto, sym, tier_l, market_summary)) if not stale else True
        upd = 0.0
        try:
            ua = _aware(getattr(dto, "updated_at", None)) if dto else None
            upd = float(ua.timestamp()) if ua is not None else 0.0
        except Exception:
            upd = 0.0
        ranked.append((
            0 if sym in pri else 1,
            0 if no_run else 1,
            0 if changed else 1,
            upd,
            sym,
        ))
    ranked.sort()
    return [str(r[-1]) for r in ranked]


def _open_symbols_for_tier(session, tier: str) -> List[str]:
    out: List[str] = []
    try:
        from backend.database.connection import SessionLocal
        from backend.services.full_auto.midlong_position_manager import _open_midlong_positions
        acct = getattr(session, "paper_account_id", None) or getattr(session, "account_id", None)
        if not acct:
            return out
        db = SessionLocal()
        try:
            want = _tier3(tier)
            for p in _open_midlong_positions(db, acct) or []:
                tier_p = str(p.get("timeframe_tier") or "").lower()
                nature = str(p.get("trade_nature") or "").lower()
                is_long = tier_p == "long" or nature in ("trend_follow", "position")
                is_mid = tier_p == "mid" or nature == "swing"
                is_short = tier_p == "short" or nature == "intraday"
                if (want == "long" and is_long) or (want == "mid" and is_mid) or (want == "short" and is_short):
                    sym = str(p.get("symbol") or "").upper()
                    if sym and sym not in out:
                        out.append(sym)
        finally:
            db.close()
    except Exception as exc:
        logger.debug("[MidLongBrain] 持仓优先队列跳过: %s", exc)
    return out


def missing_blocks_open(missing: Sequence[Any]) -> bool:
    """只认预检结构化缺项（现价 / K线:4h），不认模型叙事里的「对应K线」「pack 现价」。"""
    for raw in missing or []:
        s = str(raw or "").strip()
        if not s:
            continue
        sl = s.lower()
        if sl in ("现价", "价格", "last", "mark", "k线", "kline", "ohlc"):
            return True
        if sl.startswith(("k线:", "kline:", "ohlc:")):
            return True
    return False


def _map_direction(raw: Any) -> str:
    return {1: "long", -1: "short"}.get(schemas.direction_to_int(raw), "neutral")


def _norm_base_symbol(sym: str) -> str:
    s = str(sym or "").upper().replace("/", "").replace("-", "")
    if ":" in s:
        s = s.split(":", 1)[0]
    for suf in ("USDT", "USDC", "USD"):
        if s.endswith(suf) and len(s) > len(suf):
            s = s[: -len(suf)]
            break
    return s


def _position_from_pack(
    pack: Any,
    symbol: str,
    account_id: Optional[int] = None,
    tier: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """context_pack 持仓行用 `sym`，不是 `symbol`。读错字段会让模型以为永远空仓。"""
    layers = getattr(pack, "layers", None)
    if layers is None and isinstance(pack, dict):
        layers = pack.get("layers") or pack
    rows = ((layers or {}).get("positions") or {}).get("open") or []
    want = _norm_base_symbol(symbol)
    hits: List[Dict[str, Any]] = []
    for p in rows:
        if not isinstance(p, dict):
            continue
        raw = p.get("sym") or p.get("symbol") or ""
        if _norm_base_symbol(raw) != want:
            continue
        hits.append(p)
    if account_id is not None:
        hits = [p for p in hits if str(p.get("acct") or "") == str(account_id)]
    if tier:
        want_tier = _tier3(tier)
        tiered = [p for p in hits if str(p.get("tier") or "").lower() == want_tier]
        if tiered:
            hits = tiered
    return hits[0] if hits else None


def _account_id_for_session(session_id: str) -> Optional[int]:
    sid = str(session_id or "").strip()
    if not sid:
        return None
    try:
        from backend.services.full_auto.full_auto_trading_service import get_full_auto_trading_service
        svc = get_full_auto_trading_service()
        sess = None
        getter = getattr(svc, "get_session", None)
        if callable(getter):
            sess = getter(sid)
        if sess is None:
            bag = getattr(svc, "sessions", None) or getattr(svc, "_sessions", None) or {}
            if isinstance(bag, dict):
                sess = bag.get(sid)
        if sess is None:
            return None
        acct = getattr(sess, "paper_account_id", None) or getattr(sess, "account_id", None)
        return int(acct) if acct is not None else None
    except Exception:
        return None


def consensus_is_tradeable(cres: Any, final: Optional[Dict[str, Any]], hard_miss: bool) -> bool:
    """只有「共识通过 + 方向多/空 + 关键数据齐 + 数字失效价」才能 accepted。"""
    if hard_miss or not final or not bool(getattr(cres, "accepted", False)):
        return False
    if _map_direction(final.get("direction")) not in ("long", "short"):
        return False
    inv = normalize_invalidation(final.get("invalidation"))
    if _inv_price(inv) is None:
        return False
    return True


def _force_hold_if_missing(final: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(final or {})
    miss = out.get("missing_evidence") if isinstance(out.get("missing_evidence"), list) else []
    hard = [m for m in miss if missing_blocks_open([m])]
    soft_stripped = strip_soft_missing(miss)
    # 硬缺项才拦开仓；软项从列表剥掉，不得把 recommend_open 改 false。
    out["missing_evidence"] = soft_stripped if not hard else (hard + soft_stripped)[:10]
    if hard:
        out["recommend_open"] = False
        try:
            if float(out.get("confidence") or 0) >= 0.6:
                out["confidence"] = 0.59
        except (TypeError, ValueError):
            out["confidence"] = 0.0
    return out


def _entry_zone_bounds(raw: Any) -> Optional[Tuple[float, float]]:
    if not isinstance(raw, dict):
        return None
    try:
        lo = float(raw.get("low") or 0)
        hi = float(raw.get("high") or 0)
    except (TypeError, ValueError):
        return None
    if lo <= 0 or hi <= 0 or lo > hi:
        return None
    return lo, hi


def promote_open_if_in_zone(
    final: Dict[str, Any],
    *,
    last_price: Optional[float],
    has_position: bool = False,
    atr_pct: Optional[float] = None,
) -> Dict[str, Any]:
    """无仓 + 方向明确 + 现价已在 entry_zone → 强制 recommend_open=true。

    模型常写「等回踩」却把 recommend_open 留 false；入场区是它自己给的数字契约，
    现价已落入则视为条件达成，避免永远观望。
    """
    out = dict(final or {})
    if has_position:
        return out
    if bool(out.get("recommend_open")):
        return out
    direction = _map_direction(out.get("direction"))
    if direction not in ("long", "short"):
        return out
    if _inv_price(out.get("invalidation")) is None:
        return out
    bounds = _entry_zone_bounds(out.get("entry_zone"))
    if bounds is None or last_price is None:
        return out
    try:
        px = float(last_price)
    except (TypeError, ValueError):
        return out
    if px <= 0:
        return out
    lo, hi = bounds
    # [2026-09-07 追价容忍] 主脑常把入场区设在现价下方等回踩、永不开仓（用户实证）。
    # 除「现价在区内」外，放宽：现价超出区沿 ≤ 1×ATR（日线 ATR%，随品种波动自适应）也视为达成。
    if atr_pct is not None and atr_pct > 0:
        _tol = float(atr_pct) / 100.0  # atr_pct 是百分数（如 2.3=2.3%）
    else:
        try:
            _tol = float(os.getenv("MIDLONG_CHASE_TOL_PCT", "1.0") or 1.0) / 100.0
        except (TypeError, ValueError):
            _tol = 0.01
    in_zone = lo <= px <= hi
    # 多头：现价略高于上沿（追高容忍）；空头：现价略低于下沿（追空容忍）
    chase_long = direction == "long" and hi < px <= hi * (1 + _tol)
    chase_short = direction == "short" and lo * (1 - _tol) <= px < lo
    if in_zone or chase_long or chase_short:
        out["recommend_open"] = True
        out["_promoted_open"] = "entry_zone_touched" if in_zone else "entry_zone_chase_tol"
        summary = str(out.get("thesis_summary") or "")
        if "入场区" not in summary:
            _note = "；现价已落入入场区，允许开仓" if in_zone else "；现价贴近入场区沿（容忍内），允许开仓"
            out["thesis_summary"] = (summary + _note).strip("；")[:500]
        return out
    # [2026-09-08 进取模式] 用户拍板（模拟盘快速找平衡）：模型把入场区设在远离现价处
    # 等回踩、连 chase 容忍都够不着时，若方向明确+置信达标+有失效价，按市价直接开仓——
    # 模型的入场区保守是择时谨慎，不该否决一个清晰的方向判断。仅模拟盘默认开。
    try:
        _agg = os.getenv("MIDLONG_AGGRESSIVE_ENTRY", "true").strip().lower() in ("1", "true", "yes", "on")
    except Exception:
        _agg = True
    if _agg and not out.get("recommend_open"):
        try:
            _conf = float(out.get("confidence") or 0)
        except (TypeError, ValueError):
            _conf = 0.0
        _conf_th = float(os.getenv("MIDLONG_AGGRESSIVE_MIN_CONF", "0.5") or 0.5)
        if _conf >= _conf_th:
            out["recommend_open"] = True
            out["_promoted_open"] = "aggressive_market_entry"
            summary = str(out.get("thesis_summary") or "")
            out["thesis_summary"] = (summary + f"；方向明确(conf={_conf:.2f})，按市价进取开仓").strip("；")[:500]
    return out


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        if path.exists():
            obj = json.loads(path.read_text(encoding="utf-8"))
            return obj if isinstance(obj, dict) else {}
    except Exception:
        pass
    return {}


def _chart_feed(symbol: str) -> Dict[str, Any]:
    latest = _load_json(DATA_DIR / f"latest_trend_chart_{symbol}.json")
    if latest:
        return {
            "source": "latest_trend_chart",
            "accepted": latest.get("accepted"),
            "consensus_score": latest.get("consensus_score"),
            "final": latest.get("final"),
            "age_note": "文件快照；开仓闸另查 signal_ledger 新鲜度",
        }
    try:
        from backend.services.full_auto.midlong_chart_gate import _latest_chart_signal
        sig = _latest_chart_signal(symbol)
        return {"source": "signal_ledger", "signal": sig} if sig else {"source": "none"}
    except Exception as exc:
        return {"source": "error", "error": str(exc)[:120]}


def _brief_view(symbol: str) -> Dict[str, Any]:
    brief = _load_json(DATA_DIR / "latest_daily_brief.json")
    final = brief.get("final") if isinstance(brief.get("final"), dict) else {}
    views = final.get("symbol_views") if isinstance(final.get("symbol_views"), list) else []
    hit = None
    for v in views:
        if not isinstance(v, dict):
            continue
        if str(v.get("symbol") or "").upper().startswith(symbol):
            hit = v
            break
    as_of_ms = int(brief.get("ts_ms") or brief.get("created_ms") or 0)
    age_h = None
    if as_of_ms > 0:
        age_h = round((time.time() * 1000 - as_of_ms) / 3_600_000, 2)
    stale = bool(age_h is not None and age_h > 18.0)
    return {
        "role": "背景非指令。超过 18h 标 stale，不得当开仓理由。",
        "as_of_ms": as_of_ms or None,
        "age_h": age_h,
        "stale": stale,
        "accepted": brief.get("accepted"),
        "regime": final.get("regime"),
        "direction": final.get("direction"),
        "symbol_view": hit,
        "summary": (final.get("summary") or "")[:400],
    }


def _recent_same_dir_pnl(symbol: str, side: str, days: int = 14) -> Dict[str, Any]:
    db = None
    try:
        from sqlalchemy import text
        from backend.database.connection import SessionLocal
        db = SessionLocal()
        rows = db.execute(
            text("""
                SELECT side, size, entry_price, close_price, partial_realized_pnl, closed_at
                FROM paper_positions
                WHERE symbol = :sym AND status = 'closed'
                  AND closed_at >= now() - make_interval(days => :d)
                ORDER BY closed_at DESC
                LIMIT 20
            """),
            {"sym": symbol, "d": int(days)},
        ).mappings().all()
        same = []
        for r in rows:
            if str(r["side"] or "").lower() != str(side or "").lower():
                continue
            entry = float(r["entry_price"] or 0)
            close = float(r["close_price"] or 0)
            size = float(r["size"] or 0)
            if entry <= 0 or close <= 0 or size <= 0:
                continue
            direction = 1 if str(r["side"]).lower() == "long" else -1
            net = (close - entry) * direction * size + float(r["partial_realized_pnl"] or 0)
            same.append({"net_pnl": round(net, 4), "closed_at": str(r["closed_at"])[:19]})
        return {"n": len(same), "trades": same[:8], "sum_pnl": round(sum(t["net_pnl"] for t in same), 4)}
    except Exception as exc:
        return {"error": str(exc)[:120]}
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass


def _recent_pnl_bundle(symbol: str, side_hint: Optional[str], days: int = 14) -> Dict[str, Any]:
    long_s = _recent_same_dir_pnl(symbol, "long", days)
    short_s = _recent_same_dir_pnl(symbol, "short", days)
    hint = str(side_hint or "").lower() if side_hint else ""
    same = long_s if hint == "long" else (short_s if hint == "short" else None)
    return {
        "long": long_s,
        "short": short_s,
        "same_dir_side": hint if hint in ("long", "short") else None,
        "same_dir": same,
        "note": "已平仓纸盘盈亏，不是开仓指令。无仓且无论题方向时没有「同向」，不要把 long 侧亏损当成当前方向。",
    }


def _reflexion_memory(symbol: str, days: int = 14) -> Dict[str, Any]:
    """[2026-09-07] 复盘记忆闭环（对标 WebCryptoAgent Reflexion + TechIfat LLM-Wiki）。

    把本币近期已平仓交易按「结果 + 当时方向」整理成简短教训，写回主脑上下文，
    让模型看到自己历史的决策后果，而不是每轮从零开始。只读 paper_positions，
    失败静默返回空，不影响主链路。
    """
    db = None
    try:
        from sqlalchemy import text
        from backend.database.connection import SessionLocal
        db = SessionLocal()
        rows = db.execute(
            text("""
                SELECT side, entry_price, close_price, size, partial_realized_pnl,
                       closed_at, close_reason
                FROM paper_positions
                WHERE symbol = :sym AND status = 'closed'
                  AND closed_at >= now() - make_interval(days => :d)
                ORDER BY closed_at DESC
                LIMIT 12
            """),
            {"sym": symbol, "d": int(days)},
        ).mappings().all()
        wins, losses, lessons = 0, 0, []
        for r in rows:
            entry = float(r["entry_price"] or 0)
            close = float(r["close_price"] or 0)
            size = float(r["size"] or 0)
            if entry <= 0 or close <= 0 or size <= 0:
                continue
            side = str(r["side"] or "").lower()
            direction = 1 if side == "long" else -1
            net = (close - entry) * direction * size + float(r["partial_realized_pnl"] or 0)
            pct = (close - entry) / entry * direction * 100
            if net > 0:
                wins += 1
            else:
                losses += 1
            lessons.append({
                "side": side,
                "pnl_pct": round(pct, 2),
                "result": "win" if net > 0 else "loss",
                "exit": str(r.get("close_reason") or "")[:40] if isinstance(r, dict) else "",
                "at": str(r["closed_at"])[:16],
            })
        total = wins + losses
        return {
            "window_days": days,
            "n": total,
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / total, 3) if total else None,
            "recent_trades": lessons[:8],
            "note": "这是你近期在本币的真实决策后果。连胜别飘、连败别犟；"
                    "若同方向连续亏损，降低 confidence 或要求更强证据。",
        }
    except Exception as exc:
        return {"error": str(exc)[:120], "n": 0}
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass


def _similar_episodes_feed(
    symbol: str,
    tier: str,
    session_id: str,
    market_summary: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """[2026-09-07] 相似情景检索（海马体模式完成）：当前市场指纹 → top-k 相似历史情景+结局。

    失败静默返回空，不影响主链路。
    """
    try:
        from backend.services.mlto.episodic_memory import retrieve_similar, format_similar_for_feed
        eps = retrieve_similar(
            session_id=session_id, symbol=symbol, tier=tier,
            market_summary=market_summary, k=5, days=14,
        )
        return {
            "n": len(eps),
            "text": format_similar_for_feed(eps),
            "episodes": eps[:5],
        }
    except Exception as exc:
        return {"n": 0, "text": "", "error": str(exc)[:120]}


def _wisdom_feed(session_id: str, tier: str) -> Dict[str, Any]:
    """[2026-09-07] 回测智慧接通（断线修复）：此前智慧编译后只进 qual_layer，
    而 LLM 主脑链路不经过 qual_layer → 智慧蒸出来没端上桌。这里直接注入 feed。

    取活跃策略模板的回测智慧文本（insight_compiler），失败静默返回空。
    """
    try:
        from backend.database.connection import SessionLocal
        from backend.database.models import StrategyTemplate
        from backend.services.backtest_insight_compiler import insight_compiler
        db = SessionLocal()
        try:
            templates = (
                db.query(StrategyTemplate)
                .filter(StrategyTemplate.is_active == True)  # noqa: E712
                .order_by(StrategyTemplate.rating.desc())
                .limit(3)
                .all()
            )
            parts = []
            for tpl in templates:
                w = insight_compiler.get_active_wisdom(db, tpl.template_id)
                if w:
                    parts.append(str(w)[:400])
            return {"n": len(parts), "text": "\n".join(parts)[:800]}
        finally:
            db.close()
    except Exception as exc:
        return {"n": 0, "text": "", "error": str(exc)[:120]}


def _consolidated_lessons_feed() -> str:
    """[2026-09-07] 读每日睡眠巩固蒸馏的规则（空则返回空串）。"""
    try:
        from backend.services.mlto.episodic_memory import read_consolidated_lessons
        return read_consolidated_lessons()
    except Exception:
        return ""


def _factor_system_lessons_feed() -> str:
    """[F345 2026-09-18 学习→策略读回路] 让**实际下单**的中长线脑读到另一套学习。

    实测（报告 §15）：MLTO 只读它自己那套（reflexion/episodes/wisdom/consolidated），
    对 **v7 硬指标教训池 / 因子运行时权重 / 衰减退役** 的读取命中为 0 ⇒ 两套学习各自闭环，
    "学习只写不读"。本函数把 v7 分层教训 + 因子治理现状注入 `extras`。

    开关：`LEARNING_READBACK_ENABLED`（默认 `auto` = 本车道**关**，与今日逐字节一致；
    置 1 启用）。`extras` 会被 `json.dumps(...)[:_BRAIN_EXTRAS_CHAR_BUDGET]` 截断，
    因此该键**放在 extras 前部**，避免长尾键被截掉后"算了但没人看见"。
    """
    try:
        from backend.services.learning_readback import decision_block
        return decision_block(lane="mlto", limit=6)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[MidLongBrain] 学习读回路跳过: %s", str(exc)[:160])
        return ""


def _high_severity_events(symbol: str) -> List[Dict[str, Any]]:
    try:
        from backend.services.analysis import ledgers
        rows = ledgers.list_signals(source="dual:event_impact", symbol=symbol, limit=8) or []
        cutoff = __import__("time").time() * 1000 - 72 * 3600 * 1000
        out = []
        for r in rows:
            if int(r.get("created_ms") or 0) < cutoff:
                continue
            if abs(float(r.get("strength") or 0)) < 6:
                continue
            out.append({
                "direction": r.get("direction"),
                "strength": r.get("strength"),
                "summary": str((r.get("payload") or {}).get("summary") or "")[:160],
            })
        return out[:4]
    except Exception:
        return []


def _e1_evidence() -> Dict[str, Any]:
    return {
        "role": "证据，非指令",
        "enabled": os.getenv("TREND_E1_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on"),
        "exclusive": os.getenv("TREND_E1_LONG_LANE_EXCLUSIVE", "false").strip().lower() in ("1", "true", "yes", "on"),
    }


def _kline_ok(symbol: str, tier: str) -> tuple[bool, List[str], Optional[float]]:
    """检查现价与周期 K 线。缺了就写入 missing_evidence。"""
    missing: List[str] = []
    last = None
    try:
        from backend.services.analysis.context_pack import _klines
        # [2026-09-07] 日内波段(short)：15m/1h/4h；中线：1h/4h/1d；长线：4h/1d/1w
        _t3 = _tier3(tier)
        need = (
            ("15m", "1h", "4h") if _t3 == "short"
            else ("1h", "4h", "1d") if _t3 == "mid"
            else ("4h", "1d", "1w")
        )
        for tf in need:
            kl = _klines(symbol, tf, 20 if tf != "1w" else 40)
            if len(kl) < 8:
                missing.append(f"K线:{tf}")
            elif last is None:
                try:
                    last = float(kl[-1].get("close") or 0) or None
                except Exception:
                    last = None
        if last is None or last <= 0:
            missing.append("现价")
    except Exception as exc:
        missing.extend(["现价", f"K线:{exc}"[:40]])
    return (not missing_blocks_open(missing), missing, last)


def build_feed(
    *,
    symbol: str,
    tier: str,
    session_id: str,
    market_summary: Optional[Dict[str, Any]] = None,
    thesis: Optional[ThesisDTO] = None,
) -> Dict[str, Any]:
    """组装主脑饲料。缺现价/K 线会写进 missing_evidence。"""
    from backend.services.analysis.context_pack import build

    pack = build("midlong_thesis", symbols=[symbol, "BTC"] if symbol != "BTC" else [symbol])
    ok, miss, last = _kline_ok(symbol, tier)
    ms = (market_summary or {}).get(symbol) or {}
    if last is None:
        try:
            last = float(ms.get("price") or ms.get("last") or 0) or None
            if last:
                miss = [m for m in miss if m != "现价"]
        except Exception:
            pass

    factor_ev: Dict[str, Any] = {"role": "证据，非指令"}
    try:
        from backend.services.factor_engine.midlong_factor_route import factor_route_decide
        factor_ev.update(factor_route_decide(symbol, market_summary=market_summary) or {})
        factor_ev["role"] = "证据，非指令"
    except Exception as exc:
        factor_ev["error"] = str(exc)[:120]

    v2_ev: Dict[str, Any] = {"role": "证据，非指令"}
    try:
        from backend.services.long_trend_v2 import evidence_snapshot
        v2_ev = evidence_snapshot(symbol)
    except Exception as exc:
        v2_ev["error"] = str(exc)[:120]

    acct = _account_id_for_session(session_id)
    pos = _position_from_pack(pack, symbol, account_id=acct, tier=tier)

    side_hint = None
    if pos and str(pos.get("side") or "").lower() in ("long", "short"):
        side_hint = str(pos.get("side")).lower()
    elif thesis and thesis.direction in ("long", "short"):
        side_hint = thesis.direction

    pnl_bundle = _recent_pnl_bundle(symbol, side_hint)
    extras = {
        "symbol": symbol,
        "tier": tier,
        "role_reminder": "因子/V2/E1/图审都是证据，不是开仓指令。你才是主脑。",
        "spot_price": last,
        "chart_review": _chart_feed(symbol),
        "chart_role": "附加证据。缺失或 source=none 不阻止分析、也不等于必须观望。",
        "watch_rule": "循环每 45 秒看现价、决策 K 收盘、失效价。行情变了立刻重问，不是等到 TTL。",
        "daily_brief": _brief_view(symbol),
        # [F345 2026-09-18] 学习→策略读回路：v7 硬指标教训 + 因子治理现状。
        # 默认关（LEARNING_READBACK_ENABLED=auto 时本车道不启用）⇒ 关时返回 ""，
        # 与历史 prompt 逐字节一致；置 1 即可生效。
        "factor_system_lessons": _factor_system_lessons_feed(),
        "factor_route": factor_ev,
        "long_trend_v2": v2_ev,
        "trend_e1": _e1_evidence(),
        "current_position": pos,
        "recent_pnl_14d": pnl_bundle,
        "reflexion_memory": _reflexion_memory(symbol),
        # [2026-09-07] 海马体机制接通：相似情景检索（模式完成）+ 回测智慧（巩固后的规则）
        "similar_episodes": _similar_episodes_feed(symbol, tier, session_id, market_summary),
        "similar_episodes_note": "相似市场指纹的历史情景及其结局。做对了参考，做错了别再摔。",
        "backtest_wisdom": _wisdom_feed(session_id, tier),
        # [2026-09-07] 睡眠巩固蒸馏的长期规则（海马体→新皮层转移）
        "consolidated_lessons": _consolidated_lessons_feed(),
        "recent_same_dir_pnl_14d": pnl_bundle["same_dir"] if pnl_bundle.get("same_dir") is not None else {
            "n": 0,
            "trades": [],
            "sum_pnl": 0.0,
            "note": "无持仓且无论题方向，不提供「同向」盈亏；请看 recent_pnl_14d 的 long/short 两侧",
        },
        "funding_unit": "pack 里 funding_8h_pct 是 8 小时费率的百分数：0.01 = 0.01%/8h，年化约 0.01×3×365≈11%，不是 1%，更不是 100%。",
        "high_severity_events": _high_severity_events(symbol),
        "existing_thesis": {
            "direction": getattr(thesis, "direction", None),
            "recommend_open": getattr(thesis, "recommend_open", None),
            "should_close": getattr(thesis, "should_close", False),
            "summary": (getattr(thesis, "thesis_summary", "") or "")[:240],
        } if thesis else None,
        "missing_evidence_precheck": miss,
        "data_complete": ok and not missing_blocks_open(miss),
        "prompt_version": brain_prompt_version(),
    }
    return {"pack": pack, "extras": extras, "missing_precheck": miss, "last": last}


def _system_prompt(tier: str = "") -> str:
    # [2026-09-07] 日内波段(short)车道：给主脑明确的周期框架，防止用日线趋势思考 4-12h 的单
    _tier_line = ""
    if _tier3(tier) == "short":
        _tier_line = (
            "本论题是【日内波段】：预期持仓 4-12 小时，按 15m/1h/4h 结构思考，"
            "止损幅度约 2%、止盈约 3-4%。禁止用日线/周线级别趋势当开仓理由，"
            "entry_zone 和 invalidation.price 必须按 1h 级别结构给出。"
        )
    return (
        "你是中长线交易主脑。因子分、V2 的 L1、E1 日任务、图审都是证据，不是指令。"
        + _tier_line +
        "没有现价或 K 线时必须写入 missing_evidence，且 recommend_open 必须为 false，"
        "confidence 必须 < 0.6。不要复读因子分当确信。"
        "已有仓时用 should_close 表达是否离场；没有仓时 should_close=false。"
        "亏完再开必须非常谨慎：看 recent_pnl_14d。无仓时不要把 long 侧亏损当成「同向」。"
        "direction 是你对这段行情的方向判断，不是「开不开仓」。"
        "recommend_open 才是开不开。决定观望时仍必须给出 bullish 或 bearish。"
        "正文若在说超买、因子偏空、多头拥挤、回吐，direction 必须写 bearish，即使不开空。"
        "正文若在说趋势完好，direction 必须写 bullish。"
        "你必须给出 entry_zone={low,high}（数字价，low<=high），且 entry_zone 必须紧贴现价可执行："
        "多头入场区上沿不得低于现价 0.5×ATR 以上、空头入场区下沿不得高于现价 0.5×ATR 以上。"
        "禁止把入场区设在远离现价处等回踩——方向明确时，入场区必须覆盖或紧贴现价。"
        "无仓且方向明确、现价落在 entry_zone 内（含边界）或贴近其沿 1×ATR 内、且有数字 invalidation.price 时："
        "recommend_open 必须为 true——禁止再用「等回踩」「追高」「追空」当不开仓借口。"
        "只有现价已大幅远离入场区（超过 1×ATR）且结构不支持追入时，才允许 recommend_open=false，"
        "并在 thesis_summary 写清「现价相对入场区」。"
        "缺 OI/资金费/相关性/图审/日简报/量能等软证据时：写入 missing_evidence 可以，"
        "但不得因此把 recommend_open 改成 false。"
        "只有多空证据真正打平、看不出倾斜时才写 neutral。neutral 不能当作观望的代名词。"
        "两个模型都写 neutral 不会被当成交易共识。"
        "图审只是附加证据。没有图、图审 source=none，照样必须给出方向和是否开仓，"
        "不许因为缺图就写成中性或 recommend_open=false。"
        "invalidation 必须写成 {\"price\": 数字, \"condition\": \"...\"}，price>0 强制。"
        "没有数字失效价的论题不能成交，循环无法在行情打穿时立刻平仓。"
        "\n\n" + schemas.output_contract("midlong_thesis")
    )


def _framework_agree_adjust(llm_dir: str, fw_mean: float, require_agree: bool = True) -> str:
    """[M8 2026-09-14] 框架同意闸纯函数：LLM 方向与框架均值不一致时回落框架兜底。

    语义与 decision_hub._derive_direction 的 ai_governed 分支一致（n=192：
    LLM 方向 24h 胜率 0.434/0.343 比抛硬币差）：long 需 fw≥0.55、short 需 fw≤0.45；
    不一致时框架决定性（≥0.55/≤0.45）取框架方向，否则 neutral。
    """
    if not require_agree:
        return llm_dir
    d = (llm_dir or "neutral").lower()
    if d == "long" and fw_mean < 0.55:
        return "short" if fw_mean <= 0.45 else "neutral"
    if d == "short" and fw_mean > 0.45:
        return "long" if fw_mean >= 0.55 else "neutral"
    return d


def _apply_owm_to_conviction(conviction: int, owm_w: float) -> int:
    """[M8 2026-09-14] OWM llm 乘子（clamp [0.5,1.5]）作用到 conviction。"""
    w = max(0.5, min(1.5, float(owm_w or 1.0)))
    return int(max(0, min(100, round(conviction * w))))


def refresh_thesis(
    *,
    session_id: str,
    symbol: str,
    tier: str,
    market_summary: Optional[Dict[str, Any]] = None,
    thesis: Optional[ThesisDTO] = None,
    force: bool = False,
) -> ThesisDTO:
    """跑 dual_call 并落库。共识不够也写一张 accepted=false 的论题，避免 45s 再打。"""
    from backend.services.mlto import thesis_store

    lock = _named_lock(
        _REFRESH_LOCKS, _REFRESH_LOCKS_GUARD, f"{session_id}:{symbol}:{tier}",
    )
    if not lock.acquire(blocking=False):
        logger.info("[MidLongBrain] skip concurrent refresh %s %s", symbol, tier)
        return thesis or thesis_store.get(session_id, symbol, tier) or thesis_store.get_or_create(
            session_id, symbol, tier,
        )
    try:
        latest = thesis_store.get(session_id, symbol, tier)
        if latest is not None and thesis_is_fresh(latest) and not force:
            logger.info("[MidLongBrain] already fresh %s %s, skip dual_call", symbol, tier)
            return latest

        feed = build_feed(
            symbol=symbol, tier=tier, session_id=session_id,
            market_summary=market_summary, thesis=thesis or latest,
        )
        pack = feed["pack"]
        extras = feed["extras"]
        # [F345 2026-09-18 禁止静默退化] extras 明文按字符数硬截断，此前**截断无任何日志**
        # ⇒ 尾部键（如后加的读回路块）可能被悄悄切掉，而调用方以为"已经喂给主脑了"。
        # 这里只加告警、不改输出字节；实测当前 extras 约 8-12k 字符 < 60000 预算。
        _extras_json = json.dumps(extras, ensure_ascii=False, default=str)
        if len(_extras_json) > _BRAIN_EXTRAS_CHAR_BUDGET:
            logger.warning(
                "[MidLongBrain] extras 超预算被截断: %d > %d 字符 ⇒ 尾部键对主脑不可见: %s %s",
                len(_extras_json), _BRAIN_EXTRAS_CHAR_BUDGET, symbol, tier,
            )
            _extras_json = _extras_json[:_BRAIN_EXTRAS_CHAR_BUDGET]
        user = (
            f"【标的】{symbol}  【周期】{tier}\n\n"
            + pack.to_prompt_text(_BRAIN_CONTEXT_TOKEN_BUDGET)
            + "\n\n【本币附加证据】\n"
            + _extras_json
        )
        # [2026-09-11] 风控提示注入：mid/long 空头被 regime 闸拦截时提前告知主脑，
        # 避免 LLM 反复提案「必然被拒」的空头。
        #
        # [2026-09-18 根因修复·方向语义] 旧文案写「direction 可写 bearish，但
        # recommend_open=true 的空头论题不会成交」——**这句许可是失效的根源**：
        #   ① 下游把 thesis.direction 当**可执行意图**用，即便 recommend_open=false
        #      也会走闸并被 midlong_short_regime_block 拒；
        #   ② 实测 LLM 仍提 `recommend_open=true` 的空头（快照 id=461133/461134）；
        #   ③ 更隐蔽的连带伤害：持久 short 让 quant_layer 把 llm_qual 夹到 ≤0.45
        #      ⇒ 在 ai_governed 下压低 composite ⇒ 连该币合规多头也被拖成 WAIT。
        # 实测（近 3h、133 条）：short 100 / long 25 / neutral 8，dir_src 全 llm_brain，
        # 其中单一标的 VIRTUAL 占 80 条 ⇒ 绝大多数是"必被拒的空头"。
        # 文案逻辑抽到 `_short_gate_hint()` 以便**单测直接断言**（原来是内联的，
        # 只能做源码文本断言 —— 而文本断言正是我这一轮栽过的坑，见 §11.4）。
        _hint_txt = _short_gate_hint(symbol, tier)
        if _hint_txt:
            user += "\n\n" + _hint_txt
        gw = get_model_gateway()
        cres = gw.dual_call(
            "midlong_thesis",
            _system_prompt(tier),
            user,
            context_hash=pack.hash,
            data_cutoff_ms=pack.data_cutoff_ms,
            context_pack=pack.to_dict(),
            # [2026-09-07] 超时放宽：长上下文思维链模型单次 dual_call 实测 90-145s，
            # 180s 偏紧易超时出空壳；跟随 LLM_CALL_TIMEOUT_SECONDS 放宽到 300s。
            timeout_s=float(os.getenv("MIDLONG_BRAIN_TIMEOUT_S", "300") or 300),
            # [2026-09-07 根因修复] 主脑此前请求 4096 > 配额上限 ANALYSIS_MAX_OUTPUT_TOKENS(4000)，
            # dual_call 被配额整体 reject → status=skipped → 论题恒空（conv=0/摘要空/不分析）。
            # 钳到配额上限，杜绝"参数超上限被拒"。
            max_output_tokens=min(
                int(os.getenv("MIDLONG_BRAIN_MAX_OUTPUT_TOKENS", "4096") or 4096),
                int(os.getenv("ANALYSIS_MAX_OUTPUT_TOKENS", "4000") or 4000),
            ),
        )
        final = _force_hold_if_missing(dict(cres.final or {}))
        pre_miss = list(feed.get("missing_precheck") or [])
        miss = list(final.get("missing_evidence") or [])
        for m in pre_miss:
            if m not in miss:
                miss.append(m)
        hard_miss = missing_blocks_open(pre_miss)
        if hard_miss:
            final["recommend_open"] = False
        # 软证据（图审/taker/E1）剥掉；不得因软项改 recommend_open=false。
        miss = strip_soft_missing(miss) if not hard_miss else (
            [m for m in miss if missing_blocks_open([m])] + strip_soft_missing(miss)
        )[:10]
        final["missing_evidence"] = miss
        inv_norm = normalize_invalidation(final.get("invalidation"))
        final["invalidation"] = inv_norm
        has_pos = bool(feed.get("extras", {}).get("current_position"))
        if not hard_miss:
            # [2026-09-07] 提取日线 ATR% 供追价容忍（1×ATR 自适应，替代固定 1%）
            _atr_pct = None
            try:
                _pk = feed.get("pack")
                _mkt = (_pk.to_dict().get("layers", {}) or {}).get("market", {}) if _pk else {}
                _sym_d = (_mkt.get("symbols", {}) or {}).get(symbol) or {}
                _av = _sym_d.get("atr14_1d_pct")
                _atr_pct = float(_av) if _av is not None else None
            except Exception:
                _atr_pct = None
            final = promote_open_if_in_zone(
                final, last_price=feed.get("last"), has_position=has_pos, atr_pct=_atr_pct,
            )

        dto = latest or thesis or thesis_store.get_or_create(session_id, symbol, tier)
        new_accepted = consensus_is_tradeable(cres, final, hard_miss)
        live = thesis_store.get(session_id, symbol, tier) or dto
        # 并行旧 tick 的 degraded 票不许盖掉刚达成的共识；图审提前刷新(force)可以覆盖。
        if (not force) and thesis_is_fresh(live) and live.accepted and not new_accepted:
            logger.info(
                "[MidLongBrain] keep accepted %s %s, ignore degraded refresh score=%s",
                symbol, tier, getattr(cres, "consensus_score", None),
            )
            return live
        # 可交易成功票：弱刷新不得把 recommend_open 打回 false（除非 force）
        if (
            (not force)
            and thesis_is_fresh(live)
            and live.accepted
            and bool(live.recommend_open)
            and new_accepted
            and not bool(final.get("recommend_open"))
        ):
            logger.info(
                "[MidLongBrain] keep recommend_open %s %s, ignore hold refresh score=%s",
                symbol, tier, getattr(cres, "consensus_score", None),
            )
            return live
        dto.direction = _map_direction(final.get("direction"))
        # [M8 2026-09-14] 框架同意闸搬回现网：decision_hub._derive_direction（含
        # 「LLM 定方向需框架同意」闸，audit_llm_direction_edge.py n=192：LLM 方向
        # 24h 胜率 0.434/0.343 比抛硬币更差）随旧 orchestrator 9/5 下线后成为死路径，
        # 现网 LLM 方向不经任何框架校验直接采用。此处按同源口径（orch bias +
        # quant_alignment，与 quant_layer.compute 一致）复刻该闸：
        # LLM 想定 long 需 fw_mean≥0.55、short 需 fw_mean≤0.45，否则方向回落框架
        # 兜底（框架决定性时取框架方向，否则 neutral）。回滚：MLTO_LLM_DIRECTION_
        # REQUIRE_FW_AGREE=false（.env 已有，原为死开关，现在真正生效）。
        try:
            if (os.getenv("MLTO_LLM_DIRECTION_REQUIRE_FW_AGREE", "true") or "true"
                    ).strip().lower() in ("1", "true", "yes", "on"):
                _fw_mean = 0.5
                _fw_has_evidence = False
                _pack = feed.get("pack")
                _qb = getattr(_pack, "quant_brief", None) or {}
                _orch = getattr(_pack, "orchestrator", None) or {}
                if isinstance(_qb, dict) and isinstance(_orch, dict):
                    _fw_vals = []
                    _bias_k = "mid_bias" if _tier3(tier) == "mid" else "long_bias"
                    _bias = str(_orch.get(_bias_k) or "").strip().lower()
                    if _bias in ("bullish", "long", "buy"):
                        _fw_vals.append(1.0)
                        _fw_has_evidence = True
                    elif _bias in ("bearish", "short", "sell"):
                        _fw_vals.append(0.0)
                        _fw_has_evidence = True
                    elif _bias:
                        _fw_vals.append(0.5)
                        _fw_has_evidence = True
                    if "alignment_score" in _qb:
                        try:
                            _align = float(_qb.get("alignment_score") or 0)
                            _fw_vals.append(min(1.0, max(0.0, _align / 15.0)))
                            _fw_has_evidence = True
                        except (TypeError, ValueError):
                            pass
                    if _fw_vals:
                        _fw_mean = sum(_fw_vals) / len(_fw_vals)
                _llm_dir = dto.direction
                _new_dir = _framework_agree_adjust(_llm_dir, _fw_mean, require_agree=True)
                if _new_dir != _llm_dir and _fw_has_evidence:
                    logger.info(
                        "[MidLongBrain] 框架同意闸: LLM %s 但 fw_mean=%.2f → 回落 %s",
                        _llm_dir, _fw_mean, _new_dir,
                    )
                    dto.direction = _new_dir
                elif _new_dir != _llm_dir:
                    # 框架证据缺失：不回退（fail-open，避免缺数据把 long 误翻 short）
                    logger.debug(
                        "[MidLongBrain] 框架同意闸跳过: 无框架证据（%s %s）",
                        symbol, tier,
                    )
        except Exception as _fw_err:
            logger.debug("[MidLongBrain] 框架同意闸跳过(fail-open): %s", _fw_err)
        # [2026-09-09 归因落库] 主脑是 mid/long 车道的方向决定者（旧 MLTO orchestrator
        # 已于 2026-09-05 下线，`run_mlto_tick` 不再调用），因此 hub 归因必须在此写。
        # 记录：direction / llm_qual(conviction) / fw_mean(量化对齐) / regime / 是否建议开仓。
        # 目的：让 LLM 方向命中率可滚动校准（`brain_agent_calibration` 此前 0 行）。
        try:
            from backend.services.mlto.hub_decision_log import persist_brain_decision
            _reg_now = ""
            try:
                from backend.services.full_auto.midlong_executor import get_cached_regime
                _reg_now = get_cached_regime(symbol) or ""
            except Exception:
                _reg_now = ""
            persist_brain_decision(
                account_id=_account_id_for_session(session_id),
                symbol=symbol,
                tier=tier,
                direction=dto.direction,
                conviction=int(dto.llm_conviction or 0),
                recommend_open=bool(final.get("recommend_open")),
                accepted=bool(new_accepted),
                consensus_score=float(getattr(cres, "consensus_score", 0) or 0),
                alignment_score=int((feed.get("pack").quant_brief or {}).get("alignment_score") or 0)
                if getattr(feed.get("pack"), "quant_brief", None) else 0,
                regime=_reg_now,
                session_id=session_id,
                thesis_id=str(getattr(dto, "thesis_id", "") or ""),
                market_summary=market_summary,
                reasoning=str(final.get("thesis_summary") or final.get("summary") or ""),
            )
        except Exception as _hd_err:
            logger.debug("[MidLongBrain] 归因落库跳过: %s", _hd_err)
        dto.thesis_summary = str(final.get("thesis_summary") or final.get("summary") or "")[:500]
        # [2026-09-07 修复] 推理文字落库：08-14 以来 reasoning_snapshot 恒空——
        # 主脑分析照做但思维链没持久化，页面上论题看起来像"没分析"。
        # 这里把双模型的原始输出（含思维链）+ 共识摘要写回 dto.reasoning_content。
        try:
            _rparts = []
            for _p in (getattr(cres, "primaries", None) or []):
                _pt = str(getattr(_p, "text", "") or "").strip()
                if _pt:
                    _rparts.append(f"[{getattr(_p, 'transport', '?')}/{getattr(_p, 'model', '?')}]\n{_pt}")
            _arb = getattr(cres, "arbiter", None)
            if _arb is not None and str(getattr(_arb, "text", "") or "").strip():
                _rparts.append(f"[仲裁/{getattr(_arb, 'transport', '?')}]\n{str(getattr(_arb, 'text', '')).strip()}")
            _reasoning = "\n\n".join(_rparts).strip()
            if not _reasoning:
                _reasoning = str(final.get("thesis_summary") or final.get("summary") or "")
            dto.reasoning_content = _reasoning[:6000]
        except Exception:
            pass
        try:
            dto.llm_conviction = int(max(0, min(100, round(float(final.get("confidence") or 0) * 100))))
        except (TypeError, ValueError):
            dto.llm_conviction = 0
        # [M8 2026-09-14] OWM 读端接线：_bump_owm 每笔平仓仍在写 mlto_signal_weights
        # （赢 +5% 基础权重 / 输 −5%），但唯一读端随 orchestrator 下线 → write-only。
        # 现在把 llm 源的 OWM 乘子（clamp [0.5,1.5]，同 decision_hub.fuse_signals 口径）
        # 作用到主脑 conviction：该 tier 的 LLM 近期连胜 → 加码，连败 → 打折。
        # 回滚：MLTO_OWM_INTO_BRAIN=false。
        try:
            if (os.getenv("MLTO_OWM_INTO_BRAIN", "true") or "true"
                    ).strip().lower() in ("1", "true", "yes", "on"):
                from backend.services.mlto.learning_bridge import (
                    _normalize_owm_tier as _owm_tier_of,
                    load_owm_weights as _load_owm,
                )
                from backend.database.connection import AnalyticsSessionLocal as _ASL
                _owm_w = 1.0
                with _ASL() as _adb:
                    _owm_map = _load_owm(session_id, _owm_tier_of(tier), _adb)
                _owm_w = float(_owm_map.get("llm", 1.0) or 1.0)
                _owm_w = max(0.5, min(1.5, _owm_w))
                if abs(_owm_w - 1.0) > 1e-6:
                    _old_conv = dto.llm_conviction
                    dto.llm_conviction = _apply_owm_to_conviction(_old_conv, _owm_w)
                    logger.info(
                        "[MidLongBrain] OWM llm×%.3f: conviction %d→%d (%s %s)",
                        _owm_w, _old_conv, dto.llm_conviction, symbol, tier,
                    )
        except Exception as _owm_err:
            logger.debug("[MidLongBrain] OWM 接线跳过(fail-open): %s", _owm_err)
        # [轮130 2026-09-20 用户指令] 牛熊研究员对抗辩论 —— 接线到**活主脑**。
        # 架构：五分析师+六域信号 → 牛熊对抗辩论 → 风控官（否决权）→ 交易员。
        # 实测：`mlto/debate_layer.py` 此前唯一调用者是生产 0 调用点的 orchestrator（09-05 下线），
        # 故 `mlto_debate_log` 0 行。本轮把它接到这里（OUW 调整后、accepted/recommend_open 定型前），
        # 证据取自 context_pack 的市场/资金流/因子/**六分析师信号**层；灰区（conv 40~70）才跑，
        # 且受冷却 + 小时上限约束（LLM 是花钱的）。生效幅度有界：reject×0.6 / reduce×0.85，
        # **否决权不在这一层**（属风控官）。回滚：MIDLONG_DEBATE_ENABLED=false。
        _debate: Optional[Dict[str, Any]] = None
        try:
            from backend.services.mlto.brain_debate import (
                apply_conviction_effect,
                run_debate_for_thesis,
            )
            from backend.services.full_auto.midlong_executor import get_cached_regime as _reg_of
            _debate = run_debate_for_thesis(
                symbol=symbol, tier=tier, conviction=dto.llm_conviction, direction=str(dto.direction or ""),
                pack=feed.get("pack"), extras=feed.get("extras"),
                regime=str(_reg_of(symbol) or ""), thesis_id=str(dto.thesis_id or ""),
                session_id=session_id,
            )
            if _debate:
                _new_conv, _note = apply_conviction_effect(dto.llm_conviction, _debate)
                if abs(_new_conv - float(dto.llm_conviction)) > 1e-6:
                    logger.info("[MidLongBrain] 辩论调整 conviction %d→%d（%s %s %s）",
                                dto.llm_conviction, int(_new_conv), _note, symbol, tier)
                    dto.llm_conviction = int(_new_conv)
        except Exception as _deb_err:
            logger.debug("[MidLongBrain] 辩论接线跳过(fail-open): %s", _deb_err)
        dto.missing_evidence = miss[:10]
        dto.invalidation = inv_norm
        try:
            dto.sl_pct = float(final.get("sl_pct") or 0) or dto.sl_pct
            dto.tp_pct = float(final.get("tp_pct") or 0) or dto.tp_pct
        except (TypeError, ValueError):
            pass
        dto.accepted = new_accepted
        dto.recommend_open = bool(dto.accepted and final.get("recommend_open"))
        # 开平同权：有仓且方向明确时，accepted 票可落 should_close（不要求 recommend_open）。
        has_pos = bool(feed.get("extras", {}).get("current_position"))
        if dto.accepted and dto.direction in ("long", "short") and (
            has_pos or bool(final.get("should_close"))
        ):
            dto.should_close = bool(final.get("should_close")) and has_pos
        else:
            dto.should_close = False
        dto.expires_at = _utcnow() + timedelta(seconds=thesis_expiry_s(tier, accepted=dto.accepted))
        dto.analysis_run_id = str(cres.run_id or "")
        dto.prompt_version = brain_prompt_version()
        dto.updated_at = _utcnow()
        dto.invalidation = _attach_watch(
            dto.invalidation,
            capture_watch_snapshot(symbol, tier, market_summary),
        )
        if dto.direction in ("long", "short"):
            dto.review_count += 1
        thesis_store._persist(None, dto)  # noqa: SLF001 — 独立 analytics 短连接
        thesis_store.append_event(
            dto.thesis_id,
            "midlong_thesis",
            {
                "accepted": dto.accepted,
                "recommend_open": dto.recommend_open,
                "should_close": dto.should_close,
                "direction": dto.direction,
                "consensus": cres.consensus_score,
                "run_id": dto.analysis_run_id,
                "missing": dto.missing_evidence,
                "expiry_s": thesis_expiry_s(tier, accepted=dto.accepted),
                "inv_price": _inv_price(dto.invalidation),
                # [轮130] 辩论裁决随论题事件落库（含证据）——画布与复盘据此可查"辩论说了什么"
                **({"debate": {"verdict": _debate.get("verdict"),
                               "primary_horizon": _debate.get("primary_horizon"),
                               "primary_verdict": _debate.get("primary_verdict"),
                               "horizon_verdicts": _debate.get("horizon_verdicts"),
                               "horizon_conflict": _debate.get("horizon_conflict"),
                               "net": _debate.get("net_sentiment"),
                               "risk_min": _debate.get("risk_min"),
                               "llm": _debate.get("used_llm"),
                               "evidence": (_debate.get("evidence") or [])[:4]}} if _debate else {}),
            },
        )
        # [2026-09-07] 情景记忆：论题刷新后快照市场指纹（海马体情景记忆写入）。
        # 失败静默，不影响主链路。
        try:
            from backend.services.mlto.episodic_memory import snapshot_episode
            snapshot_episode(
                session_id=session_id, symbol=symbol, tier=tier,
                thesis_id=str(dto.thesis_id or ""), direction=str(dto.direction or "neutral"),
                accepted=bool(dto.accepted), recommend_open=bool(dto.recommend_open),
                market_summary=market_summary,
            )
        except Exception:
            pass
        logger.info(
            "[MidLongBrain] refresh %s %s accepted=%s rec_open=%s dir=%s conv=%d miss=%s score=%.3f expiry=%ss",
            symbol, tier, dto.accepted, dto.recommend_open, dto.direction,
            dto.llm_conviction, dto.missing_evidence, float(cres.consensus_score or 0),
            thesis_expiry_s(tier, accepted=dto.accepted),
        )
        return dto
    finally:
        lock.release()


def midlong_new_open_halted() -> bool:
    try:
        from backend.config.settings import midlong_new_open_halted as _fn
        return bool(_fn())
    except Exception:
        if (os.getenv("MIDLONG_BRAIN_MODE", "llm") or "llm").strip().lower() != "llm":
            return True
        return (os.getenv("MIDLONG_NO_THESIS_NO_OPEN", "true") or "true").strip().lower() not in (
            "1", "true", "yes", "on",
        )


def thesis_needs_early_refresh(
    dto: Optional[ThesisDTO],
    symbol: str,
    market_summary: Optional[Dict[str, Any]] = None,
    tier: Optional[str] = None,
) -> bool:
    """行情变了、图审打架或高严重度事件：TTL 未到也提前刷新。"""
    return thesis_watch_reason(dto, symbol, tier, market_summary) is not None


# open_blocked 事件节流：(thesis_id, reason) → 上次落盘单调秒
_OPEN_BLOCK_EMIT_TS: Dict[str, float] = {}
_OPEN_BLOCK_EMIT_COOLDOWN_S = 1800.0  # 同因 30min 内不重复落盘
# overlapping batch 合并：锁占用时把 symbols 记入 pending，持锁方收尾再扫一轮
_PENDING_BATCH_SYMS: Dict[str, set] = {}
_PENDING_BATCH_GUARD = threading.Lock()


def _emit_open_blocked(thesis_id: str, payload: Dict[str, Any]) -> None:
    """结构化落盘 open_blocked；同 thesis+reason 30min 节流，防每 tick 刷爆事件表。"""
    if not thesis_id:
        return
    reason = str(payload.get("reason") or "")
    key = f"{thesis_id}|{reason}"
    now = time.time()
    last = float(_OPEN_BLOCK_EMIT_TS.get(key) or 0.0)
    if now - last < _OPEN_BLOCK_EMIT_COOLDOWN_S:
        return
    _OPEN_BLOCK_EMIT_TS[key] = now
    # 粗清：表过大时丢掉最旧一半键，避免进程长跑泄漏
    if len(_OPEN_BLOCK_EMIT_TS) > 400:
        for k, _ in sorted(_OPEN_BLOCK_EMIT_TS.items(), key=lambda kv: kv[1])[:200]:
            _OPEN_BLOCK_EMIT_TS.pop(k, None)
    try:
        from backend.services.mlto import thesis_store as _ts
        _ts.append_event(thesis_id, "open_blocked", payload)
    except Exception:
        pass


def _emit_open_execute_false(thesis_id: str, payload: Dict[str, Any]) -> None:
    """fuse 层否决后的 open_execute_false；同 thesis+action 30min 节流。"""
    if not thesis_id:
        return
    action = str(payload.get("action") or "")
    key = f"execfalse|{thesis_id}|{action}"
    now = time.time()
    last = float(_OPEN_BLOCK_EMIT_TS.get(key) or 0.0)
    if now - last < _OPEN_BLOCK_EMIT_COOLDOWN_S:
        return
    _OPEN_BLOCK_EMIT_TS[key] = now
    try:
        from backend.services.mlto import thesis_store as _ts
        _ts.append_event(thesis_id, "open_execute_false", payload)
    except Exception:
        pass


def can_open_block_reason(
    dto: Optional[ThesisDTO],
    market_summary: Optional[Dict[str, Any]] = None,
    account_id: Optional[int] = None,
) -> Optional[str]:
    """返回拦截开仓的原因码；可开则 None。供 maybe_open / 观测对拍。"""
    if midlong_new_open_halted():
        return "new_open_halted"
    if not thesis_is_tradeable_fresh(dto):
        return "not_tradeable_fresh"
    assert dto is not None
    if dto.recommend_open is not True:
        # ── [轮121 2026-09-19 修我自己两个机制互相矛盾] ────────────────────
        # 轮120 的小仓试探为了让分档系数走 **NIBBLE（0.15，小仓）** 档，
        # 刻意把 `recommend_open` 置为 False；而这里又要求 `recommend_open is True`
        # ⇒ 试探刚进 `maybe_open` 就被自己这条闸拒掉（实测 23:49:09：
        # `小仓试探 BTC mid 原因=waiting_pullback` 紧跟 `skip open ... reason=rec_open_false`，
        # 三个试探全军覆没，成交恒 0）。
        # 修法：带 `_probe_entry` 标记的论题**允许** `recommend_open=False` 通过这一条
        # （其余所有闸——方向/失效价/缺失证据/周期联动/宪法——一条都不豁免），
        # 尺寸仍由 NIBBLE 档保证是"小仓"。
        if not getattr(dto, "_probe_entry", ""):
            return "rec_open_false"
    if dto.direction not in ("long", "short"):
        return "dir_unclear"
    if missing_blocks_open(dto.missing_evidence):
        return "missing_evidence"
    if _inv_price(getattr(dto, "invalidation", None)) is None:
        return "no_invalidation_price"
    # [2026-09-07] 周期联动协调器（严格一致）：长线强多/强空时，中线/日内
    # 逆向开仓在此拦截（此前编排器 slots 算出但主脑零引用，左右互搏无闸门）。
    try:
        from backend.services.full_auto.cycle_coordinator import lane_permission
        _cc_ok, _cc_reason, _cc_mult = lane_permission(
            str(dto.symbol or "").upper(), str(dto.tier or "mid"),
            str(dto.direction or ""), market_summary,
        )
        if not _cc_ok:
            return _cc_reason
    except Exception:
        pass  # 协调器异常不阻塞开仓（fail-open）
    reason = thesis_watch_reason(
        dto, dto.symbol, dto.tier, market_summary, ignore_cooldown=True,
    )
    if reason:
        return f"watch:{reason}"
    # [2026-09-07] 熔断/空头下行证据提前到 can_open：否则 fuse 拦下后每 tick
    # 落 open_execute_false（ASTER mid short 实测 ~1–2min/条刷 39 次）。
    try:
        from backend.services.full_auto.midlong_circuit_gate import check_midlong_entry
        side = "short" if str(dto.direction).lower() == "short" else "long"
        ok, why = check_midlong_entry(
            account_id,
            str(dto.symbol or "").upper(),
            side=side,
            tier=str(dto.tier or "mid"),
            market_summary=market_summary,
        )
        if not ok:
            code = str(why or "midlong_circuit").split(":", 1)[0].strip()
            return code or "midlong_circuit"
    except Exception:
        pass
    # [2026-09-07] 宪法级风控（最后红线，env 不可覆盖）：止损幅度/单笔保证金/
    # 日亏损硬停/敞口上限。任何上层判断都不得越过。
    # [轮131 2026-09-20 订正] 这里**只传了 sl_pct**：单笔保证金/日亏损/敞口三项依赖
    # `equity_usd`/`margin_usd`，而本函数（can_open_block_reason）拿不到账户权益与计划名义 ——
    # 所以过去那三项**从未生效**（空转）。完整判定已挪到**下单前唯一同时拿得到
    # 权益/名义/杠杆**的位置：`full_auto/midlong_helpers.try_execute_independent_agent_open`
    # 里的风控官（`services/risk_officer.py`）。此处保留 sl_pct 早筛，避免白跑后面的链路。
    try:
        from backend.services.risk_constitution import constitutional_veto
        side = "short" if str(dto.direction).lower() == "short" else "long"
        veto = constitutional_veto(
            account_id=account_id,
            symbol=str(dto.symbol or "").upper(),
            side=side,
            sl_pct=float(getattr(dto, "sl_pct", 0) or 0),
        )
        if veto:
            logger.warning("[MidLongBrain] 宪法否决 %s %s: %s", dto.symbol, dto.tier, veto)
            return f"constitution:{veto}"
    except Exception:
        pass
    return None


def can_open(dto: Optional[ThesisDTO], market_summary: Optional[Dict[str, Any]] = None) -> bool:
    return can_open_block_reason(dto, market_summary) is None


def maybe_open(
    *,
    host,
    session,
    symbol: str,
    tier: str,
    thesis: ThesisDTO,
    market_summary: Optional[Dict[str, Any]],
    trading_mode: str = "paper",
) -> bool:
    acct = getattr(session, "paper_account_id", None) or getattr(session, "account_id", None)
    try:
        acct_i = int(acct) if acct is not None else None
    except (TypeError, ValueError):
        acct_i = None
    block = can_open_block_reason(thesis, market_summary, account_id=acct_i)
    if block:
        logger.info(
            "[MidLongBrain] skip open %s %s reason=%s dir=%s rec=%s thesis=%s",
            symbol, tier, block, thesis.direction, thesis.recommend_open, thesis.thesis_id,
        )
        _emit_open_blocked(
            thesis.thesis_id,
            {
                "symbol": str(symbol).upper(),
                "tier": tier,
                "reason": block,
                "direction": thesis.direction,
                "recommend_open": bool(thesis.recommend_open),
            },
        )
        return False
    action = "buy" if thesis.direction == "long" else "sell"
    # [2026-09-07] 日内波段(short→intraday)：SL 默认 2% / TP 默认 3.5%（费用占比 <3%）
    _t3 = _tier3(tier)
    nature = {"mid": "swing", "short": "intraday"}.get(_t3, "trend_follow")
    sl = float(thesis.sl_pct or 0) or (0.02 if nature == "intraday" else (0.05 if nature == "swing" else 0.08))
    tp = float(thesis.tp_pct or 0) or (sl * 2.2 if nature == "intraday" else sl * 2.0)
    # [2026-09-08] 日内档 RR 下限对齐 V5 门（.env V5_SCALP_MIN_RR_PAPER=2.0）：
    # 论题给的 TP/SL 若 RR<2.0（如 SL2%/TP3.5%=1.75），放宽 TP 到 SL×2.2 达标，
    # 否则日内仓 100% 被盈亏比门卡死（UNI 实证）。SL 不动（风险口径不变）。
    if nature == "intraday" and sl > 0 and tp / sl < 2.0:
        tp = round(sl * 2.2, 6)
    # ── [轮117 2026-09-19 修「中线被冻结」：量纲用错] ──────────────────────
    # 这里此前传的是 `MIDLONG_BRAIN_OPEN_MARGIN_PCT`（**占权益的保证金比例**，0.12），
    # 但下游两处消费方都把它当**分档系数**（fraction of the full intended position）用：
    #   * `proposal_execution`：`dec["size_multiplier"] *= tranche`；
    #   * `midlong_helpers`：`estimate_open_notional_aligned(tranche_mult=tranche)`。
    # 设计值在 `tranche_gate.compute_margin_pct()`：BUILD 0.30/0.30/0.20/0.10、NIBBLE 0.15/0.10。
    #
    # 实测后果（`reports/_probe117b.txt`）：
    #   0.12（brain margin）× 0.25（V5Gate 缩仓）= **0.030 < MIDLONG_MIN_SIZE_MULT(0.05)**
    #   ⇒ 每一次中线开仓都撞 `[SizeFloor] BLOCK`（线上 13:12–14:03 连续 6 次），
    #   **中线因此冻结一整天**；而 09-18/09-19 那些 $690 名义的中线仓，是这套量纲还没咬合时开的。
    #
    # 保证金「占净值上限」由 `risk_constitution.MAX_SINGLE_TRADE_MARGIN_PCT`（硬顶 20%）负责，
    # 不该占用分档系数这个槽位。分档系数由 `tranche_gate` 单一来源给出。
    # 回滚：`MIDLONG_TRANCHE_FROM_GATE=false` → 退回旧的 margin-% 行为（仅对照用）。
    try:
        from backend.config.settings import MIDLONG_TRANCHE_FROM_GATE as _tfg
    except Exception:
        _tfg = True
    if _tfg:
        try:
            from backend.services.mlto.tranche_gate import compute_margin_pct as _cmp

            class _HubStub:  # compute_margin_pct 只读 hub.action
                # 主脑路径是"决定开仓"（没有 decision_hub 的 WAIT/NIBBLE 分档）⇒ 按 BUILD 取档；
                # 论题自己标了 recommend_open=0 时退到 NIBBLE（试探档），保持"证据不足先小仓"的语义。
                action = "BUILD" if bool(getattr(thesis, "recommend_open", True)) else "NIBBLE"

            margin = float(_cmp(thesis, _HubStub(), False) or 0.0)
            if margin <= 0:
                # stage≥3 = 该论题的分档已用尽 ⇒ 交给下游按"tranche 耗尽"诚实拒绝（不静默放大）
                logger.info(
                    "[MidLongBrain] %s %s 分档已用尽(tranche_stage=%s) ⇒ 本轮不开",
                    symbol, tier, getattr(thesis, "tranche_stage", "?"),
                )
        except Exception as _t_err:
            logger.warning("[MidLongBrain] 分档系数取值失败，按设计首档 0.30 兜底: %s", _t_err)
            margin = 0.30
    else:
        try:
            from backend.config.settings import MIDLONG_BRAIN_OPEN_MARGIN_PCT
            margin = float(MIDLONG_BRAIN_OPEN_MARGIN_PCT or 0.12)
        except Exception:
            margin = 0.12
    # [2026-09-07] 周期联动：中性市日内波段保证金 ×0.5（严格一致矩阵的降仓档）
    try:
        from backend.services.full_auto.cycle_coordinator import cycle_size_mult
        margin *= cycle_size_mult(
            str(symbol or "").upper(), str(tier or "mid"),
            str(thesis.direction or ""), market_summary,
        )
    except Exception:
        pass
    # 夹到可执行区间：太小没意义，太大 ranging 探针后名义仍可能顶满组合闸
    margin = max(0.04, min(0.20, margin))
    from backend.database.connection import SessionLocal
    from backend.services.full_auto.midlong_executor import execute_midlong_open

    db = SessionLocal()
    try:
        # [2026-09-18 解冻·选项E] 先在**本次尝试之前**清空跨层否决原因登记，避免取到陈旧值；
        # execute 内部各层用 open_block_reason.mark_open_block() 写入本次的具体原因（后写覆盖先写）。
        try:
            from backend.services.mlto.open_block_reason import (
                clear_last_open_block as _clr_last,
                clear_open_block as _clr_blk,
            )
            _clr_blk()
            _clr_last(str(symbol or ""), str(tier or ""))
        except Exception:
            pass
        opened = bool(execute_midlong_open(
            host=host,
            db=db,
            session=session,
            source="mlto",
            symbol=symbol,
            action=action,
            confidence=int(thesis.llm_conviction or 50),
            sl_pct=sl,
            tp_pct=tp,
            market_summary=market_summary or {},
            session_mode=getattr(session, "status", "running"),
            tier=tier,
            trade_nature=nature,
            tranche_margin_pct=margin,
            invalidation_condition=str((thesis.invalidation or {}).get("condition") or ""),
            reason=(thesis.thesis_summary or "midlong_thesis")[:80],
            trading_mode=trading_mode or "paper",
            thesis_dir=thesis.direction,
            hub_dir=thesis.direction,
            hub_mode="llm_brain",
            dir_src="midlong_thesis",
        ))
        if opened:
            logger.info(
                "[MidLongBrain] opened %s %s dir=%s thesis=%s run=%s",
                symbol, tier, thesis.direction, thesis.thesis_id, thesis.analysis_run_id,
            )
        else:
            # execute_midlong_open 内部已打 stage=fuse reason；这里补 thesis 事件便于对拍
            logger.info(
                "[MidLongBrain] execute_false %s %s dir=%s thesis=%s (见 stage=fuse 日志)",
                symbol, tier, thesis.direction, thesis.thesis_id,
            )
            # [选项E] 取本次尝试登记的否决原因：
            #  ① 先读按 (symbol,tier) 留存的副本（执行链**内部**的闸只能从这里取到，
            #     因为 midlong_helpers.record_exec_false_audit() 会 take 掉 ContextVar 版）；
            #  ② 再回退到 ContextVar 版（fuse 层/更外层用）；两者都空 ⇒ 显式写 `<未登记>`。
            _obr_blk: Dict[str, Any] = {}
            try:
                from backend.services.mlto.open_block_reason import (
                    last_open_block,
                    peek_open_block,
                )
                _obr_blk = (last_open_block(str(symbol or ""), str(tier or ""))
                            or peek_open_block() or {})
            except Exception:
                _obr_blk = {}
            _emit_open_execute_false(
                thesis.thesis_id,
                {
                    "symbol": str(symbol).upper(),
                    "tier": tier,
                    "direction": thesis.direction,
                    "action": action,
                    # [2026-09-18 解冻·选项E] 补 `reason`：此前该事件**没有任何原因字段**
                    # （实测近 48h 321 条 open_execute_false 全部无因，其中 160 条是 mid 做多）
                    # ⇒ "多头为什么被否"在台账里是空的，只能靠翻日志。
                    # 原因由执行链各层经 open_block_reason.mark_open_block() 登记；
                    # **未登记时写显式的 `<未登记>`，绝不猜测**（与原模块"不得猜"的纪律一致）。
                    "reason": str(_obr_blk.get("code") or "") or "<未登记>",
                    "reason_detail": str(_obr_blk.get("detail") or "")[:200],
                    "reason_layer": str(_obr_blk.get("layer") or ""),
                },
            )
        return opened
    except Exception as exc:
        logger.warning("[MidLongBrain] 开仓失败 %s %s: %s", symbol, tier, exc, exc_info=True)
        try:
            from backend.services.mlto import thesis_store as _ts
            _ts.append_event(
                thesis.thesis_id,
                "open_execute_error",
                {"symbol": str(symbol).upper(), "tier": tier, "error": str(exc)[:160]},
            )
        except Exception:
            pass
        return False
    finally:
        try:
            db.close()
        except Exception:
            pass


def run_midlong_brain(
    *,
    host,
    session,
    symbol: str,
    tier: str,
    market_summary: Optional[Dict[str, Any]] = None,
    trading_mode: str = "paper",
    force_refresh: bool = False,
    analysis_only: bool = False,
) -> Dict[str, Any]:
    """单币单周期：有仓不新开；行情变了或过期才 dual_call；accepted 才尝试开仓。

    analysis_only=True（出进程子进程用）：只做 refresh_thesis（重CPU+LLM+写论题），
    跳过 maybe_open——开仓留在 API 进程用真实 host 跑（内存冷却/持续态不丢）。
    """
    from backend.services.mlto import thesis_store

    session_id = str(getattr(session, "session_id", "") or "")
    sym = str(symbol or "").upper()
    tier_l = _tier3(tier)
    out = {"symbol": sym, "tier": tier_l, "action": "hold", "refreshed": False, "opened": False}

    if not midlong_brain_enabled():
        out["reason"] = "brain_disabled"
        return out

    dto = thesis_store.get(session_id, sym, tier_l)
    has_pos = False
    try:
        from backend.database.connection import SessionLocal
        from backend.services.full_auto.midlong_position_manager import has_open_position_of_nature
        acct = getattr(session, "paper_account_id", None) or getattr(session, "account_id", None)
        db = SessionLocal()
        try:
            has_pos = bool(has_open_position_of_nature(db, acct, sym, tier_l))
        finally:
            db.close()
    except Exception as exc:
        logger.debug("[MidLongBrain] 持仓探测跳过: %s", exc)

    need = force_refresh or (not thesis_is_fresh(dto))
    if need:
        try:
            dto = refresh_thesis(
                session_id=session_id, symbol=sym, tier=tier_l,
                market_summary=market_summary, thesis=dto,
                force=force_refresh,
            )
            out["refreshed"] = True
        except Exception as exc:
            logger.warning("[MidLongBrain] dual_call 失败 %s %s: %s", sym, tier_l, exc)
            out["reason"] = f"dual_call_error:{exc}"[:160]
            return out
    else:
        out["reason"] = "thesis_fresh_hold"

    out["accepted"] = bool(dto and dto.accepted)
    out["recommend_open"] = bool(dto and dto.recommend_open)
    out["direction"] = getattr(dto, "direction", "neutral") if dto else "neutral"
    out["thesis_id"] = getattr(dto, "thesis_id", "") if dto else ""
    out["analysis_run_id"] = getattr(dto, "analysis_run_id", "") if dto else ""

    if analysis_only:
        # 出进程分析：论题已刷新落库即返回，开仓交回 API 进程的 open sweep。
        if not out.get("reason"):
            out["reason"] = "analysis_only"
        return out

    if has_pos:
        out["reason"] = "has_position"
        return out
    # rec_open=true 才走 maybe_open：内部落盘 open_blocked / open_execute_false，
    # 避免原先「外层 can_open 挡掉 → maybe_open 永不记因」的对拍黑洞。
    if dto and dto.recommend_open is True:
        opened = maybe_open(
            host=host, session=session, symbol=sym, tier=tier_l,
            thesis=dto, market_summary=market_summary, trading_mode=trading_mode,
        )
        out["opened"] = opened
        out["action"] = ("buy" if dto.direction == "long" else "sell") if opened else "hold"
        if opened:
            out["reason"] = "opened"
        else:
            acct = getattr(session, "paper_account_id", None) or getattr(session, "account_id", None)
            try:
                acct_i = int(acct) if acct is not None else None
            except (TypeError, ValueError):
                acct_i = None
            block = can_open_block_reason(dto, market_summary, account_id=acct_i)
            out["reason"] = f"blocked:{block}" if block else "writer_blocked"
        return out
    if not out.get("reason"):
        out["reason"] = "no_accepted_thesis"
    return out


def _cheap_evidence_score(symbol: str, market_summary: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """证据引擎前置廉筛（对标 HydraQuant Evidence Engine，<50ms 无 LLM）。

    只在「闲扫」路径用：有仓、watch 触发、失败退避的一律放行给 LLM。
    用 market_summary 里已有的硬数据（24h 涨跌/量能/因子分）打个 0-10 的
    粗糙分，低于阈值说明此刻没有值得主脑重新思考的变化，跳过本次 dual_call。
    """
    ms = (market_summary or {}).get(symbol) or {}
    score = 5.0
    reasons: List[str] = []
    try:
        # [2026-09-09 第十八轮] 补上 price_change_24h_pct：midlong 链路注入的
        # 字段名是 price_change_24h_pct（midlong_helpers.py:1376 / unified_data_pool），
        # 而此处只读 change_24h/chg_24h/pct_24h（那是选币链路字段）→ 恒为 0，
        # 「24h波动」加分永不触发、「死水」扣分恒触发（廉筛系统性偏保守）。
        chg = abs(float(
            ms.get("price_change_24h_pct") or ms.get("change_24h")
            or ms.get("chg_24h") or ms.get("pct_24h") or 0
        ))
        if chg >= 5:
            score += 2.5; reasons.append(f"24h波动{chg:.1f}%")
        elif chg >= 2:
            score += 1.0; reasons.append(f"24h波动{chg:.1f}%")
        elif chg < 0.5:
            score -= 1.5; reasons.append("死水")
    except Exception:
        pass
    try:
        vol_r = float(ms.get("volume_ratio") or ms.get("vol_ratio") or 1.0)
        if vol_r >= 1.8:
            score += 1.5; reasons.append(f"放量{vol_r:.1f}x")
        elif vol_r < 0.6:
            score -= 1.0; reasons.append("缩量")
    except Exception:
        pass
    try:
        fs = ms.get("factor_score") or ms.get("score")
        if fs is not None:
            fs = abs(float(fs))
            if fs >= 0.6:
                score += 1.5; reasons.append(f"因子分{fs:.2f}")
            elif fs < 0.2:
                score -= 1.0; reasons.append("因子弱")
    except Exception:
        pass
    return {"score": round(score, 2), "reasons": reasons}


def _prefilter_skip_idle(symbol: str, market_summary: Optional[Dict[str, Any]]) -> Optional[str]:
    """闲扫前置门：分数过低返回跳过原因，否则 None（放行）。

    默认开启；MIDLONG_BRAIN_IDLE_PREFILTER=0 回滚。阈值 env 可调。
    """
    if (os.getenv("MIDLONG_BRAIN_IDLE_PREFILTER", "1") or "1").strip().lower() in ("0", "false", "off"):
        return None
    try:
        thr = float(os.getenv("MIDLONG_BRAIN_IDLE_PREFILTER_MIN", "3.5") or 3.5)
    except (TypeError, ValueError):
        thr = 3.5
    ev = _cheap_evidence_score(symbol, market_summary)
    if ev["score"] < thr:
        return f"idle_prefilter score={ev['score']}<{thr} ({','.join(ev['reasons']) or 'flat'})"
    return None


def run_midlong_brain_batch(
    *,
    host,
    session,
    symbols: Sequence[str],
    tier: str,
    market_summary: Optional[Dict[str, Any]] = None,
    trading_mode: str = "paper",
    reserve_key=None,
    analysis_only: bool = False,
) -> List[Dict[str, Any]]:
    """变盘池与闲扫拆开：watch/失败退避优先，闲扫才吃 MAX_REFRESH。

    overlapping：非阻塞抢锁失败时把 symbols 合并进 pending，由持锁方在
    finally 前排空，避免整轮 tick 被丢弃（重注册叠跑时尤其关键）。
    """
    try:
        from backend.config.settings import (
            MIDLONG_THESIS_MAX_REFRESH_PER_CYCLE,
            MIDLONG_THESIS_WATCH_REFRESH_CAP,
        )
        idle_cap = max(0, int(MIDLONG_THESIS_MAX_REFRESH_PER_CYCLE or 3))
        watch_cap = max(idle_cap, int(MIDLONG_THESIS_WATCH_REFRESH_CAP or 5))
    except Exception:
        idle_cap, watch_cap = 3, 5
    sid = str(getattr(session, "session_id", "") or "")
    lock_key = f"{sid}:{tier}"
    batch_lock = _named_lock(_BATCH_LOCKS, _BATCH_LOCKS_GUARD, lock_key)
    sym_set = {str(s).upper() for s in (symbols or []) if s}
    if not batch_lock.acquire(blocking=False):
        with _PENDING_BATCH_GUARD:
            pend = _PENDING_BATCH_SYMS.setdefault(lock_key, set())
            before = len(pend)
            pend.update(sym_set)
            after = len(pend)
        logger.info(
            "[MidLongBrain] coalesce overlapping batch tier=%s session=%s "
            "added=%d pending=%d",
            tier, sid, after - before, after,
        )
        return []
    results: List[Dict[str, Any]] = []
    try:
        work = set(sym_set)
        # 持锁后把此前 coalesce 的一并吃掉
        with _PENDING_BATCH_GUARD:
            work |= _PENDING_BATCH_SYMS.pop(lock_key, set())
        passes = 0
        while work and passes < 3:
            passes += 1
            batch_syms = sorted(work)
            work.clear()
            held = _open_symbols_for_tier(session, tier)
            ordered = order_refresh_symbols(
                sid, list(batch_syms) + held, tier, priority=held, market_summary=market_summary,
            )
            watch_n = 0
            idle_n = 0
            tier_l = _tier3(tier)
            for sym in ordered:
                if reserve_key is not None:
                    try:
                        if not reserve_key(f"{sym}:{tier}"):
                            continue
                    except Exception:
                        pass
                force = False
                from backend.services.mlto import thesis_store
                existing = thesis_store.get(sid, sym, tier_l)
                stale = not thesis_is_fresh(existing)
                watch = None if stale else thesis_watch_reason(
                    existing, sym, tier_l, market_summary,
                )
                is_watch = bool(watch) or (
                    stale and existing is not None and not bool(getattr(existing, "accepted", False))
                )
                if is_watch and watch_n < watch_cap:
                    force = True
                    watch_n += 1
                    logger.info(
                        "[MidLongBrain] watch-refresh %s %s reason=%s",
                        sym, tier, watch or "fail_backoff",
                    )
                elif stale and not watch and idle_n < idle_cap and (watch_n + idle_n) < watch_cap:
                    # [2026-09-07] 证据引擎前置廉筛：闲扫（非 watch/非持仓）先过
                    # 廉价规则门，无变化不烧 LLM。watch/持仓路径不受影响。
                    if sym not in held:
                        skip = _prefilter_skip_idle(sym, market_summary)
                        if skip:
                            logger.info("[MidLongBrain] idle-skip %s %s %s", sym, tier, skip)
                            results.append({"symbol": sym, "tier": tier_l, "action": "hold",
                                            "reason": skip, "refreshed": False, "opened": False})
                            continue
                    force = True
                    idle_n += 1
                    logger.info("[MidLongBrain] idle-refresh %s %s reason=ttl", sym, tier)
                try:
                    results.append(run_midlong_brain(
                        host=host, session=session, symbol=sym, tier=tier,
                        market_summary=market_summary, trading_mode=trading_mode,
                        force_refresh=force, analysis_only=analysis_only,
                    ))
                except Exception as exc:
                    logger.warning("[MidLongBrain] batch %s %s: %s", sym, tier, exc)
                    results.append({"symbol": sym, "tier": tier, "action": "hold", "reason": str(exc)[:120]})
            opened_n = sum(1 for r in results if r.get("opened"))
            logger.info(
                "[MidLongBrain] batch tier=%s n=%d watch=%d idle=%d opened=%d priority=%s pass=%d",
                tier, len(results), watch_n, idle_n, opened_n, held, passes,
            )
            with _PENDING_BATCH_GUARD:
                work |= _PENDING_BATCH_SYMS.pop(lock_key, set())
        return results
    finally:
        batch_lock.release()


# ═══ [2026-09-08] 出进程主脑：重分析(context_pack+LLM)拆独立子进程，API进程只做轻开仓 ═══
# 背景：refresh_thesis 的 context_pack 构建是 CPU 大头（13-21s/批），此前在 API 进程后台
# 线程跑——CPython GIL 下同进程任意时刻只有一线程执行 Python，与 uvicorn 事件循环抢同一个
# 核，36 核机器只用 1 核，网页 API 被拖卡。拆子进程后：子进程有独立 GIL/DB 会话，重计算吃
# 别的核；开仓留在 API 进程用真实 host（内存冷却/持续态/账户解析不丢，零交易路径改动）。
_BRAIN_SUBPROCS: Dict[str, Any] = {}  # tier -> subprocess.Popen
_BRAIN_SUBPROCS_GUARD = threading.Lock()


def _brain_subprocess_enabled() -> bool:
    return (os.getenv("MIDLONG_BRAIN_SUBPROCESS", "1") or "1").strip().lower() not in ("0", "false", "off")


def _spawn_brain_subprocess(*, symbols, tier: str, trigger: str) -> bool:
    """spawn 出进程分析子进程（fire-and-forget，子进程直接写库）。同 tier 已在跑则合并跳过。"""
    import subprocess as _sp
    import sys as _sys
    tier_l = _tier3(tier)
    with _BRAIN_SUBPROCS_GUARD:
        cur = _BRAIN_SUBPROCS.get(tier_l)
        if cur is not None and cur.poll() is None:
            logger.info("[MidLongBrain] 子进程在跑 tier=%s pid=%d，本批合并跳过", tier_l, cur.pid)
            return False
        cmd = [
            _sys.executable, "-m", "backend.services.mlto.brain_subprocess",
            "--tier", tier_l,
            "--symbols", ",".join(str(s).upper() for s in (symbols or []) if s),
            "--trigger", trigger,
        ]
        try:
            repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
            proc = _sp.Popen(
                cmd, cwd=repo,
                # 子进程日志走自己的 FileHandler（logs/brain_subprocess.log），不握 backend.log
                # 句柄——避免主进程死后孤儿子进程握句柄导致后端重启重定向失败（09-05 事故教训）。
                stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
            )
            _BRAIN_SUBPROCS[tier_l] = proc
            logger.info("[MidLongBrain] 出进程分析 tier=%s pid=%d n=%d", tier_l, proc.pid, len(symbols or []))
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("[MidLongBrain] 子进程启动失败 tier=%s: %s（本批仅靠进程内开仓扫描）", tier_l, e)
            return False


def run_midlong_open_sweep(
    *,
    host,
    session,
    symbols: Sequence[str],
    tier: str,
    market_summary: Optional[Dict[str, Any]] = None,
    trading_mode: str = "paper",
    reserve_key=None,
) -> List[Dict[str, Any]]:
    """[2026-09-08] 进程内轻量开仓扫描：读子进程写的新鲜论题，recommend_open 才 maybe_open。

    用真实 host（内存冷却/持续态/账户解析都在），无 context_pack/LLM，CPU 开销极小。
    吃的是子进程上一批写的论题（分析→开仓延迟 ≤1 个 45s 周期，模拟盘可接受）。
    """
    from backend.services.mlto import thesis_store
    from backend.services.full_auto.midlong_position_manager import has_open_position_of_nature
    from backend.database.connection import SessionLocal

    sid = str(getattr(session, "session_id", "") or "")
    tier_l = _tier3(tier)
    acct = getattr(session, "paper_account_id", None) or getattr(session, "account_id", None)
    results: List[Dict[str, Any]] = []
    # [轮118 2026-09-19] 逐币记"为什么没进候选/没成交"——此前只有 `候选=N 成交=0`
    # 一行，用户看到的是"整条车道冻住却查不出原因"（实测 22:10–23:21 连续 24 轮
    # 候选=1 成交=0，而唯一候选 ZEC 根本没有 mid 策略、每次都 strategy_detached）。
    _skip_stat: Dict[str, int] = {}
    _no_strategy_logged: Dict[str, float] = {}

    def _bump(_k: str) -> None:
        _skip_stat[_k] = int(_skip_stat.get(_k, 0)) + 1

    for sym_raw in (symbols or []):
        sym = str(sym_raw).upper()
        if not sym:
            continue
        try:
            if reserve_key is not None and not reserve_key(f"{sym}:{tier_l}"):
                _bump("reserved")
                continue
        except Exception:
            pass
        dto = thesis_store.get(sid, sym, tier_l)
        if dto is None:
            _bump("no_thesis")
            continue
        _dir_raw = str(getattr(dto, "direction", "") or "").strip().lower()
        # ── [轮120 2026-09-19 用户拍板「允许小仓位」] 两条小仓试探路径 ──────────
        # 现场（`reports/_probe119.txt`）：9 个固定币全部"等回踩/中性"⇒ 候选=0
        # ⇒ 中线整天一单不开。用户口径：中线应当能开，除非极端反转；允许小仓位。
        #   (a) 方向明确但 `recommend_open=false`（=模型在"等回踩"）⇒ NIBBLE 档市价试探；
        #   (b) `direction=neutral` ⇒ **用 regime 定方向**（up→long / down→short），
        #       震荡/未知**不猜方向**（没有方向优势就不下注）。
        # 尺寸由分档系数保证是"小仓"（recommend_open=False ⇒ tranche_gate 走 NIBBLE 0.15 档），
        # 且照旧过全部风控闸（V5/预算/组合/宪法）；每一步都写显式审计 `probe_entry:*`。
        _probe_why = ""
        if not bool(getattr(dto, "accepted", False)) or not bool(
            getattr(dto, "recommend_open", False)
        ):
            try:
                from backend.config.settings import MIDLONG_NEUTRAL_PROBE_ENABLED as _pn
            except Exception:
                _pn = True
            _probe_dir = ""
            if _dir_raw in ("long", "short"):
                _probe_why = "waiting_pullback"
                _probe_dir = _dir_raw
            elif _pn:
                try:
                    _ms = (market_summary or {}).get(sym) or {}
                    _reg = str((_ms or {}).get("regime") or "").strip().lower()
                except Exception:
                    _reg = ""
                if _reg in ("up", "bull", "bullish"):
                    _probe_dir, _probe_why = "long", f"neutral_regime_{_reg}"
                elif _reg in ("down", "bear", "bearish"):
                    _probe_dir, _probe_why = "short", f"neutral_regime_{_reg}"
            if not _probe_dir:
                _bump("no_thesis" if dto is None else
                      ("no_direction" if not _pn else "probe_no_regime"))
                if not bool(getattr(dto, "recommend_open", False)):
                    _bump("not_recommended")
                continue
            # 试探：只改本次调用用的副本（不动落库论题）
            try:
                dto = copy.copy(dto)
                dto.direction = _probe_dir
                dto.recommend_open = False   # ⇒ NIBBLE 档（小仓）
                # [轮121] 显式标记：`can_open_block_reason` 据此放行 rec_open_false
                # （否则上面这行会把自己刚放进来的试探拒掉 —— 实测三个试探全灭）
                dto._probe_entry = _probe_why
            except Exception:
                pass
            logger.info(
                "[MidLongBrain] 小仓试探 %s %s dir=%s 原因=%s（原 accepted=%s rec_open=%s）",
                sym, tier_l, _probe_dir, _probe_why,
                getattr(dto, "accepted", None), getattr(dto, "recommend_open", None),
            )
            try:
                from backend.services.mlto.midlong_direction_audit import (
                    record_decision_audit as _rda_p,
                )
                _rda_p(outcome="probe", stage="sweep", symbol=sym,
                       reason=f"probe_entry:{_probe_why}", session_id=sid,
                       tier=tier_l, action=("buy" if _probe_dir == "long" else "sell"),
                       authority="mlto")
            except Exception:
                pass
            _bump("probe")
        else:
            if not bool(getattr(dto, "recommend_open", False)):
                _bump("not_recommended")
                continue
        if not thesis_is_fresh(dto):
            _bump("stale")
            continue
        try:  # 有仓不新开
            db = SessionLocal()
            try:
                if has_open_position_of_nature(db, acct, sym, tier_l):
                    _bump("has_position")
                    continue
            finally:
                db.close()
        except Exception:
            pass
        # ── [轮118] 无独立策略的候选**不得**进入执行层 ────────────────────
        # 实测：ZEC 每 3 分钟被扫一次，执行层解析不到策略 → `strategy_detached`
        # →（轮115 的棘轮修复后）5 次同因即装配 30 分钟冷却 → 再试 → 再冻。
        # 它既不在会话标的内、也没有 mid 策略，属于"僵尸候选"：唯一的候选位被它占满，
        # 审计里堆的也全是它的噪音，看起来就是"中线全冻结"。
        try:
            _resolve = getattr(host, "resolve_independent_strategy", None)
            if callable(_resolve):
                _db_s = SessionLocal()
                try:
                    _strat = _resolve(_db_s, session, sym, tier_l)
                    # [轮122 2026-09-19 ZEC 根除] 解析到的策略必须
                    #   ① status=active（`resolve_independent_strategy` 三个分支都要求 active，
                    #      但 ZEC 的 `tpl_mid_reversion_a9e8e8` 是 **paused**）；
                    #   ② **能绑定到本会话/本账户** —— 该函数在 paper 下有"跨账户兜底"
                    #      （取任何账户的同币 active 策略），拿到的对象执行层
                    #      `ensure_bound_strategy` 绑不上 ⇒ `strategy_detached`
                    #      ⇒ 5 次同因 ⇒ 30 分钟冷却 ⇒ 无限循环（ZEC 实测）。
                    # 两道都过才放行；否则记 `no_strategy` / `strategy_inactive` /
                    # `strategy_unbound` 并跳过（不再进执行层制造冷却）。
                    _st_status = str(getattr(_strat, "status", "") or "").lower()
                    _ok_bind = False
                    if _strat is not None and _st_status in ("", "active"):
                        try:
                            _sess_ids = {str(x) for x in (
                                getattr(session, "active_strategy_ids", None) or [])}
                            _acct_t = (getattr(session, "paper_account_id", None)
                                       or getattr(session, "account_id", None))
                            _sid_s = str(getattr(_strat, "strategy_id", "") or "")
                            _acct_s = int(getattr(_strat, "account_id", 0) or 0)
                            _ok_bind = (_sid_s and _sid_s in _sess_ids) or (
                                _acct_t is not None and _acct_s == int(_acct_t))
                        except Exception:
                            _ok_bind = False
                    if _strat is None or (not _ok_bind):
                        # ── [轮123 2026-09-19] 无策略时**先补建**（与执行层同模式）──────
                        # 旧行为：直接跳过 ⇒ AI 选币的标的大多不在会话白名单里、也没有
                        # 现成策略 ⇒ **整条 AI 中线车道被清零**（实测 00:18–00:21
                        # `picked=['SYN','WLFI','ZEC'] → []`、`候选=0`）。
                        # 执行层本来就有"策略缺位即补建"（2026-09-18 修复），这里补齐入口侧：
                        # 补建成功且可绑定 ⇒ 放行；仍不可用 ⇒ 记因跳过（ZEC 的 paused/
                        # 跨账户不可绑定情形由上面的 `_ok_bind` 继续挡住，不会回到无限循环）。
                        _created_ok = False
                        if _strat is None:
                            try:
                                _ac_fn = getattr(host, "auto_create_strategy", None)
                                _mkt_i = (market_summary or {}).get(sym)
                                if callable(_ac_fn) and isinstance(_mkt_i, dict):
                                    _nw = _ac_fn(_db_s, session, sym, dict(_mkt_i))
                                    if _nw is not None:
                                        _strat2 = _resolve(_db_s, session, sym, tier_l)
                                        _st2 = str(getattr(_strat2, "status", "") or "").lower()
                                        _sid2 = str(getattr(_strat2, "strategy_id", "") or "")
                                        _acct2 = int(getattr(_strat2, "account_id", 0) or 0)
                                        _acct_t2 = (getattr(session, "paper_account_id", None)
                                                    or getattr(session, "account_id", None))
                                        _sess_ids2 = {str(x) for x in (
                                            getattr(session, "active_strategy_ids", None) or [])}
                                        _created_ok = bool(
                                            _strat2 is not None and _st2 in ("", "active")
                                            and ((_sid2 and _sid2 in _sess_ids2)
                                                 or (_acct_t2 is not None
                                                     and _acct2 == int(_acct_t2))))
                                    if _created_ok:
                                        logger.info(
                                            "[MidLongBrain] 开仓扫描 %s %s：策略缺位已补建并绑定",
                                            sym, tier_l,
                                        )
                            except Exception as _ac_err:
                                logger.debug("[MidLongBrain] 扫描侧补建策略跳过 %s: %s",
                                             sym, _ac_err)
                        # 可用性判定：原有可绑定策略，或**本轮刚补建并绑定成功**
                        _usable = bool(_ok_bind or _created_ok)
                        if not _usable:
                            _bump("no_strategy" if _strat is None else (
                                "strategy_inactive" if _st_status not in ("", "active")
                                else "strategy_unbound"))
                            _now = time.time()
                            if _now - float(_no_strategy_logged.get(sym) or 0) > 1800:
                                _no_strategy_logged[sym] = _now
                                logger.info(
                                    "[MidLongBrain] 开仓扫描跳过 %s %s：策略不可用"
                                    "（无/非 active/绑不上；不再进入执行层制造 "
                                    "strategy_detached 循环）", sym, tier_l,
                                )
                                try:
                                    from backend.services.mlto.midlong_direction_audit import (
                                        record_decision_audit as _rda,
                                    )
                                    _rda(outcome="skip", stage="sweep", symbol=sym,
                                         reason="sweep_skip:no_strategy",
                                         session_id=sid, tier=tier_l, action="hold",
                                         authority="mlto")
                                except Exception:
                                    pass
                            continue
                finally:
                    _db_s.close()
        except Exception:
            pass
        try:
            opened = maybe_open(
                host=host, session=session, symbol=sym, tier=tier_l,
                thesis=dto, market_summary=market_summary, trading_mode=trading_mode,
            )
        except Exception as exc:
            logger.warning("[MidLongBrain] 开仓扫描 %s %s: %s", sym, tier_l, exc)
            opened = False
        results.append({"symbol": sym, "tier": tier_l, "opened": bool(opened),
                        "action": ("buy" if dto.direction == "long" else "sell") if opened else "hold"})
    if results or _skip_stat:
        logger.info("[MidLongBrain] 开仓扫描 tier=%s 候选=%d 成交=%d%s",
                    tier_l, len(results), sum(1 for r in results if r["opened"]),
                    ("｜未进候选: " + ", ".join(f"{k}={v}" for k, v in sorted(_skip_stat.items())))
                    if _skip_stat else "")
    return results


def run_midlong_brain_batch_async(
    *,
    host,
    session,
    symbols: Sequence[str],
    tier: str,
    market_summary: Optional[Dict[str, Any]] = None,
    trading_mode: str = "paper",
    reserve_key=None,
) -> bool:
    """[2026-09-07 解耦] 主脑 LLM 批次异步执行（用户拍板：因子为主、LLM 要久）。

    45s 因子/哨兵循环派单即返回，LLM 慢分析（实测 90-145s/币）在后台线程跑完
    再写论题——循环不再被 LLM 阻塞（此前实测循环被卡 780s）。
    - 批次锁 + coalesce 在同步版内部：重入自动合并，不堆叠。
    - 跨线程不共享 SQLAlchemy session：后台线程按 session_id 重取（expunge 后
      纯当数据载体，attribute 读取安全）。
    返回是否成功派出。"""
    import threading
    _sid = str(getattr(session, "session_id", "") or "")
    if not _sid:
        return False

    # [2026-09-08 出进程] 重分析拆独立子进程（独立GIL不抢API的核），API进程只做轻开仓扫描。
    if _brain_subprocess_enabled():
        spawned = _spawn_brain_subprocess(symbols=symbols, tier=tier, trigger="scheduler")
        try:
            run_midlong_open_sweep(
                host=host, session=session, symbols=symbols, tier=tier,
                market_summary=market_summary, trading_mode=trading_mode,
                reserve_key=reserve_key,
            )
        except Exception as exc:
            logger.warning("[MidLongBrain] 开仓扫描异常 %s: %s", tier, exc)
        return spawned or True  # 开仓扫描已跑即算派出（子进程在跑时 spawn 返回 False 属正常合并）

    def _bg() -> None:
        try:
            from backend.database.connection import SessionLocal
            from backend.database.models import FullAutoSession
            _db = SessionLocal()
            try:
                _sess = (
                    _db.query(FullAutoSession)
                    .filter(FullAutoSession.session_id == _sid)
                    .first()
                )
                if _sess is not None:
                    _db.expunge(_sess)  # 脱离 session，纯数据载体
            finally:
                _db.close()
            if _sess is None:
                return
            # 全局串行：拿到信号量才跑，拿不到就等（daemon 线程等待无害，
            # 循环早已返回）。三档 LLM/CPU 工作不并行，API 不被饿死。
            with _BRAIN_BG_SEM:
                run_midlong_brain_batch(
                    host=host, session=_sess, symbols=symbols, tier=tier,
                    market_summary=market_summary, trading_mode=trading_mode,
                    reserve_key=reserve_key,
                )
        except Exception as exc:
            logger.warning("[MidLongBrain] 异步批次异常 %s: %s", tier, exc)

    threading.Thread(target=_bg, name=f"brain-batch-{tier}", daemon=True).start()
    return True
