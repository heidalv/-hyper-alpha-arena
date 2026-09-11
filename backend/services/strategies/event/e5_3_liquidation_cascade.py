# -*- coding: utf-8 -*-
"""E5-3 清算级联 → 反向均值回归（v3 方向 4，p2-event-strategies）。

方案原文：`30 分钟滚动清算额 > 历史 P99 且方向单边 → 反向 1–4h 均值回归`。

**数据源自适应**（本库两套清算数据深度差 100 倍，硬用其一都会出错）：
  liquidation_ticks   逐笔、30 天保留、可切任意桶宽，但当前只有几小时（WS 刚上线）
  liquidation_events  小时聚合、coinalyze 源已有 ~450 桶 / 19.5 天 / 14 个主流币

  `detect()` 自动选源：ticks 覆盖 ≥ `min_history_buckets × bucket` 就用 **ticks/30min**，
  否则回退 **events/60min**。关键点是 **P99 基线与触发值永远来自同一数据源、同一桶宽**，
  绝不用小时基线去卡 30 分钟的量（那会把阈值凭空放大一倍）。选中的源写进 `payload.source`
  与 `notes`，回测报告里一眼可查。

  两套都不足 `min_history_buckets`（默认 336）时返回空 + `insufficient_history`，
  不降级阈值硬凑信号。

规则（因果）：
  1. 全市场（跨币合计）与单币两级都算滚动桶合计；P99 用**事件之前**的历史桶（不含当前桶）；
  2. 触发：桶合计 ≥ P99 且 单边率 = max(L,S)/(L+S) ≥ `side_ratio`（默认 0.7）；
  3. 方向：`long_usd` 是多头被强平（SELL 单）→ 超跌 → **做多**(+1)；空头被强平 → 做空(−1)；
  4. horizon 2h（方案「1–4h」取中位）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from backend.services.strategies.event.base import (
    EventShadowStrategy,
    EventSignal,
    base_symbol,
    env_float,
    env_int,
    register_strategy,
)

logger = logging.getLogger(__name__)

STRATEGY_ID = "e5_3_liq_cascade"


def _market_db():
    from backend.database.connection import MarketSessionLocal

    return MarketSessionLocal()


def ticks_coverage_ms() -> Tuple[int, int]:
    """liquidation_ticks 的 (最早, 最新) ts_ms；无数据返回 (0, 0)。"""
    from sqlalchemy import text

    db = _market_db()
    try:
        r = db.execute(text("SELECT MIN(ts_ms) AS lo, MAX(ts_ms) AS hi FROM liquidation_ticks")).mappings().first()
        if not r or not r["lo"]:
            return 0, 0
        return int(r["lo"]), int(r["hi"])
    except Exception:
        return 0, 0
    finally:
        db.close()


def load_buckets_from_ticks(since_ms: int, until_ms: int, bucket_ms: int) -> Dict[str, Dict[int, Tuple[float, float]]]:
    """{symbol: {bucket_ts: (long_usd, short_usd)}}；SELL=多头被强平计入 long_usd。"""
    from sqlalchemy import text

    out: Dict[str, Dict[int, Tuple[float, float]]] = {}
    db = _market_db()
    try:
        rows = db.execute(text(
            "SELECT symbol, (ts_ms / :bw) * :bw AS b, "
            "COALESCE(SUM(CASE WHEN side = 'SELL' THEN notional_usd END), 0) AS lu, "
            "COALESCE(SUM(CASE WHEN side = 'BUY'  THEN notional_usd END), 0) AS su "
            "FROM liquidation_ticks WHERE ts_ms >= :lo AND ts_ms <= :hi "
            "GROUP BY symbol, (ts_ms / :bw) * :bw"
        ), {"bw": int(bucket_ms), "lo": int(since_ms), "hi": int(until_ms)}).mappings().all()
    except Exception as exc:
        logger.warning("[%s] liquidation_ticks 读取失败: %s", STRATEGY_ID, exc)
        return out
    finally:
        db.close()
    for r in rows:
        out.setdefault(base_symbol(r["symbol"]), {})[int(r["b"])] = (float(r["lu"] or 0), float(r["su"] or 0))
    return out


def load_buckets_from_events(since_ms: int, until_ms: int, bucket_ms: int,
                             source: str = "coinalyze") -> Dict[str, Dict[int, Tuple[float, float]]]:
    """小时聚合表 → 同结构。source 固定一个，避免 ws 与 coinalyze 双计。"""
    from sqlalchemy import text

    out: Dict[str, Dict[int, Tuple[float, float]]] = {}
    db = _market_db()
    try:
        rows = db.execute(text(
            "SELECT symbol, (ts_ms / :bw) * :bw AS b, SUM(long_usd) AS lu, SUM(short_usd) AS su "
            "FROM liquidation_events WHERE source = :src AND ts_ms >= :lo AND ts_ms <= :hi "
            "GROUP BY symbol, (ts_ms / :bw) * :bw"
        ), {"bw": int(bucket_ms), "src": source, "lo": int(since_ms), "hi": int(until_ms)}).mappings().all()
    except Exception as exc:
        logger.warning("[%s] liquidation_events 读取失败: %s", STRATEGY_ID, exc)
        return out
    finally:
        db.close()
    for r in rows:
        out.setdefault(base_symbol(r["symbol"]), {})[int(r["b"])] = (float(r["lu"] or 0), float(r["su"] or 0))
    return out


def _percentile(sorted_vals: List[float], q: float) -> float:
    """线性插值分位数（输入须已排序）。"""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


class LiquidationCascadeStrategy(EventShadowStrategy):
    strategy_id = STRATEGY_ID
    description = "滚动清算额破历史 P99 且方向单边 → 反向均值回归（2h）"
    default_horizon_h = 2.0

    def __init__(self):
        self.bucket_min = env_int("E5_LIQ_BUCKET_MIN", 30)
        self.min_history_buckets = env_int("E5_LIQ_MIN_HISTORY_BUCKETS", 336)  # 7 天 × 48 个 30min 桶
        self.pct = env_float("E5_LIQ_PERCENTILE", 0.99)
        self.side_ratio = env_float("E5_LIQ_SIDE_RATIO", 0.70)
        self.min_notional = env_float("E5_LIQ_MIN_NOTIONAL_USD", 2_000_000.0)
        self.horizon_h = env_float("E5_LIQ_HORIZON_H", 2.0)
        self.cooldown_buckets = env_int("E5_LIQ_COOLDOWN_BUCKETS", 4)  # 同币冷却，避免一次级联刷屏

    # ---------- 数据源选择 ----------
    def history_buckets_needed(self, bucket_ms: int) -> int:
        """按桶宽缩放历史门槛，保持同一墙钟窗口。

        [2026-09-04] `min_history_buckets=336` 是按 **30min × 7 天** 标定的。
        回退到小时源时若仍用 336，实际变成 336 小时 ≈ 14 天稠密桶；
        DOGE 等填充率约 82% 的币在默认 24h 扫描里经常不够数，
        连已经入账的事件都扫不出来（实测 lookback=24h → 0；=168h → 4）。
        这里按墙钟等比缩放：30min→336，60min→168。
        """
        ref_ms = max(60_000, self.bucket_min * 60 * 1000)
        if bucket_ms <= 0:
            return self.min_history_buckets
        return max(24, int(round(self.min_history_buckets * ref_ms / bucket_ms)))

    def choose_source(self, since_ms: int, until_ms: int, notes: List[str]) -> Tuple[str, int]:
        """→ (source, bucket_ms)。ticks 深度够就用 30min，否则回退小时聚合。"""
        bucket_ms = self.bucket_min * 60 * 1000
        need_ms = self.history_buckets_needed(bucket_ms) * bucket_ms
        lo, hi = ticks_coverage_ms()
        if hi > lo and (hi - lo) >= need_ms:
            return "ticks", bucket_ms
        have_h = (hi - lo) / 3600000 if hi > lo else 0.0
        notes.append(
            f"liquidation_ticks 仅 {have_h:.1f}h（需 {need_ms / 3600000:.0f}h），"
            f"回退 liquidation_events 小时桶"
        )
        return "events", 3600 * 1000

    def detect(self, *, since_ms: int, until_ms: int, limit: int = 5000,
               notes: Optional[List[str]] = None) -> List[EventSignal]:
        notes = notes if notes is not None else []
        source, bucket_ms = self.choose_source(since_ms, until_ms, notes)
        need = self.history_buckets_needed(bucket_ms)
        warmup_ms = need * bucket_ms
        load_lo = since_ms - warmup_ms

        if source == "ticks":
            data = load_buckets_from_ticks(load_lo, until_ms, bucket_ms)
        else:
            data = load_buckets_from_events(load_lo, until_ms, bucket_ms)
        if not data:
            notes.append(f"{source} 无清算样本")
            return []

        out: List[EventSignal] = []
        thin = 0
        for sym, book in data.items():
            buckets = sorted(book.keys())
            # 稀疏序列：按时间跨度判断历史够不够，而不是只看非空桶个数。
            # DOGE 填充率约 82% 时，纯 len(buckets) 会把真实级联误杀成 THIN。
            if not buckets or (buckets[-1] - buckets[0]) < warmup_ms:
                thin += 1
                continue
            totals = [book[b][0] + book[b][1] for b in buckets]
            last_hit = -10**9
            for i, b in enumerate(buckets):
                if b < since_ms or b > until_ms:
                    continue
                if b - buckets[0] < warmup_ms:
                    continue
                if b - last_hit < self.cooldown_buckets * bucket_ms:
                    continue
                hist = sorted(totals[max(0, i - 2000):i])   # 事件之前的历史桶，不含当前桶
                if len(hist) < max(24, need // 2):
                    continue
                p99 = _percentile(hist, self.pct)
                lu, su = book[b]
                tot = lu + su
                if tot < max(p99, self.min_notional) or tot <= 0:
                    continue
                ratio = max(lu, su) / tot
                if ratio < self.side_ratio:
                    continue
                # long_usd = 多头被强平 → 价格被砸穿 → 反向做多
                direction = 1 if lu >= su else -1
                mult = tot / p99 if p99 > 0 else 1.0
                out.append(EventSignal(
                    ts_ms=int(b + bucket_ms),   # 桶收盘时刻才可观测，避免用桶内未来信息
                    symbol=sym,
                    direction=direction,
                    horizon_h=self.horizon_h,
                    strength=round(min(10.0, 3.0 + 2.0 * (mult - 1.0)), 2),
                    confidence=round(min(0.75, 0.45 + 0.1 * (mult - 1.0) + 0.2 * (ratio - self.side_ratio)), 3),
                    reason=f"{self.bucket_min if source == 'ticks' else 60}min 清算 ${tot/1e6:.1f}M "
                           f"= P99 的 {mult:.1f}×，单边率 {ratio:.0%}"
                           f"（{'多头' if lu >= su else '空头'}被强平）",
                    payload={
                        "strategy": self.strategy_id, "source": source,
                        "bucket_min": bucket_ms // 60000,
                        "total_usd": round(tot, 2), "long_usd": round(lu, 2), "short_usd": round(su, 2),
                        "p99_usd": round(p99, 2), "multiple": round(mult, 2),
                        "side_ratio": round(ratio, 4), "history_buckets": len(hist),
                        "history_needed": need,
                    },
                ))
                last_hit = b
                if len(out) >= limit:
                    break
            if len(out) >= limit:
                notes.append(f"命中数达上限 {limit}，已截断")
                break

        if thin:
            notes.append(f"{thin} 个币历史跨度不足 {need} 桶（≈{warmup_ms/3600000:.0f}h），跳过（insufficient_history）")
        if not out and not data:
            notes.append("insufficient_history")
        out.sort(key=lambda s: s.ts_ms)
        return out


def build() -> LiquidationCascadeStrategy:
    return LiquidationCascadeStrategy()


register_strategy(STRATEGY_ID, build)
