# -*- coding: utf-8 -*-
"""事件影子策略基类（v3 方向 4/6，p2-event-strategies，2026-09-03）。

方案第六节要求：**每个策略服务必须提供 `backtest()` / `shadow()` / `live()` 三种模式与同一 KPI 输出**。
本基类把这三种模式压到**同一份规则代码** `detect()` 上，杜绝"回测一套、实盘另一套"：

    detect(since, until)   纯规则：扫原始表 → 候选 EventSignal 列表（无副作用、无前视、可重放）
      ├─ backtest(days)    历史全量 detect → 用 event_study 的价格路径算 KPI（不入账）
      ├─ shadow(lookback)  近窗 detect → 未入账的写 signal_ledger（source=strategy_id）
      │                    → 到期由 analysis_ledger_scoring 自动评分（命中/超额/Brier）
      └─ live()            Phase 3 才开；未过影子门时直接抛错

幂等：signal_ledger 主键 = sha1(strategy_id | symbol | event_ts)，重复 detect 不会重复入账
（`record_signal` 走 `ON CONFLICT (id) DO NOTHING`）。

晋升门（方案第七节「影子」一档）：
  N ≥ 30 个事件、平均超额 bp 的 95% 下界 > 往返成本（14bp）→ 才允许进研究桶小资金。
  `kpi()` 直接给出这三项与是否过门，`promotion_ready` 是唯一判据。

不造数原则：任一策略的输入数据历史不足（清算只有几小时、OI 只覆盖部分币）时，
`detect()` 返回空并在 `notes` 写明原因，**绝不**用降级阈值硬凑信号。
"""
from __future__ import annotations

import hashlib
import logging
import math
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# 往返成本（与 event_study 同口径：14bp）；影子净期望下界必须高于它
COST_BP = 14.0
PROMOTION_MIN_N = 30


def now_ms() -> int:
    return int(time.time() * 1000)


def env_true(name: str, default: bool = True) -> bool:
    import os

    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def env_float(name: str, default: float) -> float:
    import os

    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def env_int(name: str, default: int) -> int:
    import os

    try:
        return int(float(os.getenv(name, str(default))))
    except Exception:
        return default


_UNIVERSE_CACHE: Dict[str, Any] = {"ts": 0.0, "key": None, "syms": set()}
_UNIVERSE_TTL_SEC = 3600.0


def tradable_universe(*, days: int = 30, min_coverage: float = 0.8,
                      exchange: str = "binance") -> set:
    """近 `days` 天 1h K 线覆盖率 ≥ `min_coverage` 的 base 符号集合。

    **为什么必须过滤**：`perp_funding` 有 847 个币，`crypto_klines` 只有 788 个且历史仅 8 个月，
    近 30 天覆盖达标的仅 112 个。在没有 K 线的币上产信号，`score_due()` 取不到价格
    → 48h 后一律判 void，既污染账本又让 KPI 分母失真。检出阶段就挡掉，
    账本里剩下的每一条都是**可评分**的。代价是策略只能在主流币池上跑，这一点在
    回测报告里如实标注（`universe_size`）。
    """
    import time as _t

    key = f"{exchange}|{days}|{min_coverage}"
    if _UNIVERSE_CACHE["key"] == key and _t.time() - _UNIVERSE_CACHE["ts"] < _UNIVERSE_TTL_SEC:
        return _UNIVERSE_CACHE["syms"]

    from sqlalchemy import text

    from backend.database.connection import MarketSessionLocal

    need = int(days * 24 * float(min_coverage))
    lo = int(_t.time()) - int(days) * 86400
    db = MarketSessionLocal()
    try:
        rows = db.execute(text(
            "SELECT symbol FROM crypto_klines WHERE exchange = :ex AND period = '1h' "
            "AND timestamp >= :lo GROUP BY symbol HAVING COUNT(*) >= :need"
        ), {"ex": exchange, "lo": lo, "need": need}).fetchall()
        syms = {base_symbol(r[0]) for r in rows}
        syms.discard("")
    except Exception as exc:
        logger.warning("[event.base] 可交易币池查询失败（不过滤）: %s", exc)
        return set()
    finally:
        db.close()
    _UNIVERSE_CACHE.update({"ts": _t.time(), "key": key, "syms": syms})
    return syms


def existing_signal_ids(ids: Sequence[str]) -> set:
    """signal_ledger 里已存在的 id 子集（影子重扫时用来区分新旧）。"""
    if not ids:
        return set()
    from sqlalchemy import text

    from backend.services.analysis.ledgers import _db, ensure_schema

    ensure_schema()
    db = _db()
    try:
        rows = db.execute(
            text("SELECT id FROM signal_ledger WHERE id = ANY(:ids)"), {"ids": list(ids)}
        ).fetchall()
        return {r[0] for r in rows}
    except Exception as exc:
        logger.warning("[event.base] 查已存在信号失败（按全新处理）: %s", exc)
        return set()
    finally:
        db.close()


def base_symbol(sym: Any) -> str:
    """'BTCUSDT' / 'ETH/USDT:USDT' / 'SOL-PERP' → 'BTC' / 'ETH' / 'SOL'。"""
    s = str(sym or "").upper().replace("-", "").replace("/", "").replace(":", "").strip()
    changed = True
    while changed:
        changed = False
        for suf in ("USDT", "USDC", "USD", "PERP"):
            if s.endswith(suf) and len(s) > len(suf):
                s = s[: -len(suf)]
                changed = True
    return s


@dataclass
class EventSignal:
    """一条候选事件信号（回测与影子共用）。"""

    ts_ms: int
    symbol: str                 # base 符号（BTC / ETH …）
    direction: int              # +1 做多 / -1 做空 / 0 中性
    horizon_h: float
    strength: float = 0.0       # 0–10
    confidence: float = 0.5     # 0–1
    reason: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)

    def signal_id(self, strategy_id: str) -> str:
        raw = f"{strategy_id}|{self.symbol}|{int(self.ts_ms)}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:32]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class EventShadowStrategy:
    """事件影子策略基类。子类只需实现 `detect()`。"""

    strategy_id: str = "e5_base"
    description: str = ""
    default_horizon_h: float = 4.0
    # 影子入账开关的环境变量名（缺省 E5_<ID>_SHADOW_ENABLED）
    enabled_env: Optional[str] = None

    # 是否只保留"可评分"标的（近窗 1h K 线覆盖达标的币）；见 tradable_universe 注释
    universe_filter: bool = True

    # ---------------- 子类实现 ----------------
    def detect(self, *, since_ms: int, until_ms: int, limit: int = 5000,
               notes: Optional[List[str]] = None) -> List[EventSignal]:
        raise NotImplementedError

    # ---------------- 车道层：可评分性过滤 ----------------
    def detect_scorable(self, *, since_ms: int, until_ms: int, limit: int = 5000,
                        notes: Optional[List[str]] = None,
                        universe_days: int = 30) -> List[EventSignal]:
        """`detect()` + 剔除无法评分的标的。shadow / backtest 都走这条，口径一致。

        `detect()` 本身保持纯规则（便于单测规则逻辑），"能不能评分"是车道层的事。
        """
        notes = notes if notes is not None else []
        sigs = self.detect(since_ms=since_ms, until_ms=until_ms, limit=limit, notes=notes)
        if not sigs or not self.universe_filter:
            return sigs
        uni = tradable_universe(days=max(7, int(universe_days)))
        if not uni:
            notes.append("可评分币池查询为空，本轮不过滤")
            return sigs
        kept = [s for s in sigs if s.symbol in uni]
        dropped = len(sigs) - len(kept)
        if dropped:
            notes.append(
                f"{dropped}/{len(sigs)} 条信号的标的不在可评分币池"
                f"（近 {universe_days} 天 1h K 线覆盖 ≥80%，共 {len(uni)} 币）被剔除"
            )
        return kept

    # ---------------- 开关 ----------------
    def shadow_enabled(self) -> bool:
        key = self.enabled_env or f"E5_{self.strategy_id.upper()}_SHADOW_ENABLED"
        return env_true(key, True) and env_true("E5_SHADOW_ENABLED", True)

    # ---------------- 影子 ----------------
    def shadow(self, *, lookback_h: Optional[float] = None, dry_run: bool = False,
               limit: int = 500) -> Dict[str, Any]:
        """近窗检测 → 新信号入 signal_ledger（幂等）。到期评分由 score_due 负责。"""
        t0 = time.time()
        notes: List[str] = []
        lookback = float(lookback_h if lookback_h is not None else env_float(
            f"E5_{self.strategy_id.upper()}_LOOKBACK_H", 24.0))
        until = now_ms()
        since = until - int(lookback * 3600 * 1000)
        enabled = self.shadow_enabled()
        out: Dict[str, Any] = {
            "strategy": self.strategy_id, "mode": "shadow", "enabled": enabled,
            "lookback_h": lookback, "dry_run": dry_run,
            "detected": 0, "recorded": 0, "signals": [], "notes": notes,
        }
        if not enabled and not dry_run:
            notes.append("影子开关关闭（E5_SHADOW_ENABLED / 单策略开关）")
            return out
        try:
            signals = self.detect_scorable(since_ms=since, until_ms=until, limit=limit, notes=notes)
        except Exception as exc:
            logger.exception("[%s] detect 失败", self.strategy_id)
            notes.append(f"detect 异常: {exc}")
            out["ok"] = False
            return out
        out["detected"] = len(signals)
        if dry_run:
            out["signals"] = [s.to_dict() for s in signals[:50]]
            out["elapsed_sec"] = round(time.time() - t0, 3)
            out["ok"] = True
            return out

        from backend.services.analysis import ledgers

        # `record_signal` 走 ON CONFLICT DO NOTHING，冲突时**照样返回 id**；影子每 15 分钟
        # 重扫同一个 24h 窗口，若按返回值计数会把老事件反复算成"新入账"。先查一次已存在的 id。
        ids = {s.signal_id(self.strategy_id): s for s in signals}
        already = existing_signal_ids(list(ids.keys()))
        recorded: List[str] = []
        for sid, s in ids.items():
            if sid in already:
                continue
            got = ledgers.record_signal(
                source=self.strategy_id,
                symbol=s.symbol,
                direction=int(s.direction),
                horizon_ms=int(s.horizon_h * 3600 * 1000),
                strength=float(s.strength),
                confidence=float(s.confidence),
                regime=None,
                context_hash=None,
                payload={"reason": s.reason, "lane": "shadow", **(s.payload or {})},
                created_ms=int(s.ts_ms),
                signal_id=sid,
            )
            if got:
                recorded.append(got)
        out["recorded"] = len(recorded)
        out["already_recorded"] = len(already)
        out["signals"] = [s.to_dict() for s in signals[:50]]
        out["elapsed_sec"] = round(time.time() - t0, 3)
        out["ok"] = True
        return out

    # ---------------- 回测 ----------------
    def backtest(self, *, days: int = 180, horizons_h: Sequence[int] = (1, 2, 4, 8, 24),
                 limit: int = 20000, exchange: str = "binance") -> Dict[str, Any]:
        """历史全量 detect → 相对 BTC 的**方向化超额** KPI。不入账、不改任何状态。

        与 event_study 同口径：超额 = sign × (资产收益 − BTC 收益)；标的本身是 BTC 时不减基准。
        """
        from backend.research.event_study import (
            HOUR,
            load_hourly_closes,
            mean_se_interval,
            wilson_interval,
        )

        t0 = time.time()
        notes: List[str] = []
        until = now_ms()
        since = until - int(days) * 86400 * 1000
        signals = self.detect_scorable(since_ms=since, until_ms=until, limit=limit, notes=notes,
                                       universe_days=days)
        out: Dict[str, Any] = {
            "strategy": self.strategy_id, "mode": "backtest", "days": days,
            "horizons_h": list(horizons_h), "n_events": len(signals), "notes": notes,
            "cost_bp": COST_BP, "by_horizon": [], "elapsed_sec": 0.0,
            "universe_size": len(tradable_universe(days=max(7, int(days)))) if self.universe_filter else None,
        }
        if not signals:
            out["elapsed_sec"] = round(time.time() - t0, 3)
            return out

        syms = sorted({s.symbol for s in signals} | {"BTC"})
        max_h = max(int(h) for h in horizons_h)
        closes = load_hourly_closes(syms, since // 1000 - 2 * HOUR, until // 1000 + max_h * HOUR, exchange=exchange)

        def price(sym: str, ts_s: int) -> Optional[float]:
            book = closes.get(sym) or {}
            return book.get((int(ts_s) // HOUR) * HOUR)

        per_h: Dict[int, List[float]] = {int(h): [] for h in horizons_h}
        used = skipped = 0
        for s in signals:
            t_s = int(s.ts_ms // 1000)
            p0 = price(s.symbol, t_s)
            b0 = price("BTC", t_s)
            if not p0 or not b0:
                skipped += 1
                continue
            used += 1
            for h in horizons_h:
                p1 = price(s.symbol, t_s + int(h) * HOUR)
                b1 = price("BTC", t_s + int(h) * HOUR)
                if not p1:
                    continue
                asset_ret = p1 / p0 - 1.0
                if s.symbol == "BTC" or not b1:
                    excess = s.direction * asset_ret
                else:
                    excess = s.direction * (asset_ret - (b1 / b0 - 1.0))
                per_h[int(h)].append(excess * 1e4)

        for h in sorted(per_h):
            xs = per_h[h]
            if not xs:
                out["by_horizon"].append({"horizon_h": h, "n": 0})
                continue
            mean, se, lo, hi = mean_se_interval([x / 1e4 for x in xs])
            wins = sum(1 for x in xs if x > 0)
            w_lo, w_hi = wilson_interval(wins, len(xs))
            out["by_horizon"].append({
                "horizon_h": h,
                "n": len(xs),
                "mean_excess_bp": round(mean * 1e4, 2),
                "excess_ci_bp": [round(lo * 1e4, 2), round(hi * 1e4, 2)],
                "hit_rate": round(wins / len(xs), 4),
                "hit_ci": [round(w_lo, 4), round(w_hi, 4)],
                "passes_cost": bool(lo * 1e4 > COST_BP),
            })
        out["n_used"] = used
        out["n_skipped_no_price"] = skipped
        best = [r for r in out["by_horizon"] if r.get("n", 0) >= PROMOTION_MIN_N and r.get("passes_cost")]
        out["significant_horizons"] = [r["horizon_h"] for r in best]
        out["significant"] = bool(best)
        out["elapsed_sec"] = round(time.time() - t0, 3)
        return out

    # ---------------- 影子 KPI / 晋升门 ----------------
    def kpi(self, days: int = 90) -> Dict[str, Any]:
        """从 signal_ledger 出影子 KPI（N / 命中率 CI / 平均超额 CI / 是否过晋升门）。"""
        from backend.research.event_study import mean_se_interval, wilson_interval
        from backend.services.analysis import ledgers

        rows = ledgers.list_signals(source=self.strategy_id, since_ms=now_ms() - days * 86400 * 1000, limit=2000)
        scored = [r for r in rows if str(r.get("status")) == "scored"]
        excess = [float(r["excess_bp"]) for r in scored if r.get("excess_bp") is not None]
        hits = [int(r["hit"]) for r in scored if r.get("hit") is not None]
        out: Dict[str, Any] = {
            "strategy": self.strategy_id, "days": days,
            "n_total": len(rows), "n_scored": len(scored),
            "n_open": sum(1 for r in rows if str(r.get("status")) == "open"),
            "n_void": sum(1 for r in rows if str(r.get("status")) == "void"),
            "cost_bp": COST_BP, "min_n": PROMOTION_MIN_N,
        }
        if excess:
            mean, se, lo, hi = mean_se_interval([x / 1e4 for x in excess])
            out["mean_excess_bp"] = round(mean * 1e4, 2)
            out["excess_ci_bp"] = [round(lo * 1e4, 2), round(hi * 1e4, 2)]
            out["net_lower_bp"] = round(lo * 1e4 - COST_BP, 2)
        if hits:
            w_lo, w_hi = wilson_interval(sum(hits), len(hits))
            out["hit_rate"] = round(sum(hits) / len(hits), 4)
            out["hit_ci"] = [round(w_lo, 4), round(w_hi, 4)]
        briers = [float(r["brier"]) for r in scored if r.get("brier") is not None]
        if briers:
            out["avg_brier"] = round(sum(briers) / len(briers), 4)
        ready = bool(len(scored) >= PROMOTION_MIN_N and out.get("excess_ci_bp")
                     and out["excess_ci_bp"][0] > COST_BP)
        out["promotion_ready"] = ready
        out["promotion_reason"] = (
            "N 与净期望 95% 下界均达标，可进研究桶小资金" if ready
            else f"未过门：N={len(scored)}/{PROMOTION_MIN_N}，"
                 f"净期望下界={out.get('net_lower_bp', 'NA')}bp（需 > 0）"
        )
        return out

    # ---------------- 实盘 ----------------
    def live(self, **kwargs: Any) -> Dict[str, Any]:
        k = self.kpi()
        raise NotImplementedError(
            f"{self.strategy_id} 未开放 live：{k.get('promotion_reason')}（方案上线协议要求先过影子门）"
        )

    # ---------------- 汇总 ----------------
    def status(self) -> Dict[str, Any]:
        return {
            "strategy": self.strategy_id,
            "description": self.description,
            "shadow_enabled": self.shadow_enabled(),
            "default_horizon_h": self.default_horizon_h,
            "kpi": self.kpi(),
        }


# ─────────────────────────── 注册表 ───────────────────────────
_REGISTRY: Dict[str, Any] = {}


def register_strategy(strategy_id: str, factory) -> None:
    _REGISTRY[strategy_id] = factory


def get_strategy(strategy_id: str) -> Optional[EventShadowStrategy]:
    f = _REGISTRY.get(strategy_id)
    return f() if f else None


def registered_strategies() -> List[str]:
    return sorted(_REGISTRY.keys())
