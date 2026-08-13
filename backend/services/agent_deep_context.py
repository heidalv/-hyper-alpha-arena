"""Agent 深度上下文注入器 — 为 SwingAgent/TrendAgent 构建完整市场数据。

之前这两个 agent 只拿到 compact_report_text（~730 tokens），数据极度贫瘠。
本模块把系统里已有的丰富数据源注入到 agent 的 prompt 中，让 AI 做深度分析。

所有函数 try/except 优雅降级，数据缺失返回空字符串，绝不阻塞 prompt 构建。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_PERIOD_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "8h": 28800,
    "12h": 43200, "1d": 86400, "1w": 604800, "1M": 2592000,
}


def _safe_round(v, n=2):
    try:
        return round(float(v), n)
    except Exception:
        return v


def _fetch_klines_for_prompt(symbol: str, tf: str, count: int) -> list:
    """AI prompt 侧 K 线取数（unified_data_pool 全量整合 · 灰度切片 2026-07-06）。

    开启 `COORDINATOR_CONSUME_SNAPSHOT_KLINES` 时，AI prompt 的 K 线优先复用主链
    已采集的统一快照（与门禁 / coordinator **同一时点**，消除 AI"看到的 K 线"与
    "被门禁校验的 K 线"不一致的隐患）；快照缺失 / 该周期不足 count 根 / 开关未开时，
    回退 `get_klines_from_db`——行为与整改前逐字节一致（向后兼容）。

    返回 list[dict]，与 `get_klines_from_db` 形态一致，并 `tail(count)` 到相同根数，
    保证下游指标（RSI/EMA/ATR/量比）计算窗口不变，切换数据源不改变指标口径。
    回滚 = 关掉开关。

    [2026-07-31] 统一补 datetime：data_center 行通常只有 timestamp，否则提示词时间轴为空。
    """
    import os
    from backend.services.kline_data_service import kline_service as ks
    rows: list = []
    try:
        if os.getenv("COORDINATOR_CONSUME_SNAPSHOT_KLINES", "false").lower() in ("1", "true", "yes", "on"):
            from backend.services.unified_data_pool import unified_data_pool
            snap = unified_data_pool.get_snapshot(
                max_age=float(os.getenv("COORDINATOR_SNAPSHOT_MAX_AGE_SEC", "180") or 180)
            )
            if snap is not None:
                df = snap.klines.get((str(symbol).upper(), tf))
                if df is not None and len(df) >= count:
                    rows = df.tail(count).to_dict("records")
    except Exception as e:
        logger.debug("[AgentDeepContext] 快照 K 线复用失败 %s/%s，回退 DB: %s", symbol, tf, e)
    if not rows:
        rows = ks.get_aggregated_klines(symbol, tf, count=count) or []
    # 补时间轴，保证 deep_context OHLCV 表有可读时间
    for r in rows:
        if not isinstance(r, dict):
            continue
        if r.get("datetime"):
            continue
        ts = r.get("timestamp")
        if ts is None:
            continue
        try:
            from datetime import datetime, timezone
            ts_i = int(ts)
            if ts_i > 10_000_000_000:  # ms → s
                ts_i //= 1000
            r["datetime"] = datetime.fromtimestamp(ts_i, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass
    return rows


def build_kline_block(symbol: str, periods: list, count: int = 30) -> str:
    """构建 K线深度数据块（带时间轴的 OHLCV 表格 + 技术指标）。

    Args:
        symbol: 交易对
        periods: ['1h','4h'] 或 ['4h','1d']
        count: 每周期 K线条数
    """
    parts = []
    try:
        import pandas as pd

        for tf in periods:
            kl = _fetch_klines_for_prompt(symbol, tf, count=count)
            if not kl or len(kl) < 10:
                continue
            kdf = pd.DataFrame(kl)
            close = kdf["close"]

            # 技术指标
            delta = close.diff()
            gain = delta.where(delta > 0, 0.0)
            loss_ = (-delta).where(delta < 0, 0.0)
            avg_g = gain.ewm(alpha=1 / 14, adjust=False).mean()
            avg_l = loss_.ewm(alpha=1 / 14, adjust=False).mean()
            rs = avg_g / avg_l.replace(0, 1e-10)
            rsi = round(float((100 - 100 / (1 + rs)).iloc[-1]), 1)

            ema9 = float(close.ewm(span=9, adjust=False).mean().iloc[-1])
            ema21 = float(close.ewm(span=21, adjust=False).mean().iloc[-1])
            ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1]) if len(kdf) >= 50 else ema21
            ema_trend = "bullish" if ema9 > ema21 > ema50 else "bearish" if ema9 < ema21 < ema50 else "mixed"

            macd_f = close.ewm(span=12, adjust=False).mean()
            macd_s = close.ewm(span=26, adjust=False).mean()
            macd_hist = float((macd_f - macd_s - (macd_f - macd_s).ewm(span=9, adjust=False).mean()).iloc[-1])

            _vol = kdf["volume"]
            _period_sec = _PERIOD_SECONDS.get(tf, 3600)
            _last_ts = (
                int(kdf["timestamp"].iloc[-1])
                if "timestamp" in kdf.columns and kdf["timestamp"].iloc[-1] is not None
                else 0
            )
            _partial = bool(
                _last_ts > 0 and int(time.time()) < _last_ts + _period_sec
            )
            if _partial and len(kdf) >= 2:
                _cur_vol = float(_vol.iloc[-2])
                _vol_ma = _vol.iloc[-21:-1].mean()
            else:
                _cur_vol = float(_vol.iloc[-1])
                _vol_ma = _vol.iloc[-20:].mean()
            vol_ratio = (
                round(_cur_vol / float(_vol_ma), 2)
                if float(_vol_ma) > 0 and _cur_vol > 0
                else None
            )

            # ATR
            if len(kdf) >= 14:
                tr = (kdf["high"] - kdf["low"]).rolling(14).mean()
                atr = float(tr.iloc[-1]) if not tr.isna().iloc[-1] else 0
                atr_pct = round(atr / float(close.iloc[-1]) * 100, 2) if close.iloc[-1] > 0 else 0
            else:
                atr_pct = 0

            # 最近 count 根 K线（带时间轴）
            dt_col = "datetime" if "datetime" in kdf.columns else None
            recent = kdf.tail(count)
            klines_text = ""
            for _, row in recent.iterrows():
                dt = row.get(dt_col, "") if dt_col else ""
                dt_str = str(dt)[:16] if dt else ""
                klines_text += f"  {dt_str} O={_safe_round(row['open'])} H={_safe_round(row['high'])} L={_safe_round(row['low'])} C={_safe_round(row['close'])} V={_safe_round(row['volume'])}\n"

            _header = (
                f"### {tf} K线（最近{count}根，ATR={atr_pct}%）\n"
                f"RSI(14)={rsi} | EMA9={_safe_round(ema9)} EMA21={_safe_round(ema21)} EMA50={_safe_round(ema50)} | "
                f"趋势={ema_trend} | MACD柱={_safe_round(macd_hist, 4)}"
            )
            if vol_ratio is not None and vol_ratio > 0:
                _header += f" | 量比={vol_ratio}"
            parts.append(_header + f"\n```\n{klines_text}```\n")
    except Exception as e:
        logger.debug(f"[AgentDeepContext] K线块构建失败 {symbol}: {e}")

    return "\n".join(parts) if parts else ""


def build_regime_block(symbol: str) -> str:
    """市场状态分类块。"""
    try:
        import pandas as pd
        from backend.services.market_regime import MarketRegimeClassifier

        for tf in ["1h", "4h"]:
            kl = _fetch_klines_for_prompt(symbol, tf, count=100)
            if kl and len(kl) >= 50:
                clf = MarketRegimeClassifier()
                cls = clf.classify(pd.DataFrame(kl))
                regime = cls.regime.value if hasattr(cls.regime, "value") else str(cls.regime)
                return f"### 市场状态\n{regime} (置信度={cls.confidence:.0%})\n"
    except Exception as e:
        logger.debug(f"[AgentDeepContext] regime 块构建失败 {symbol}: {e}")
    return ""


def build_intel_block(symbol: str) -> str:
    """情报信号块（鲸鱼/情绪/衍生品/新闻）。"""
    try:
        from backend.services.intelligence_signal_engine import intelligence_signal_engine
        sig = intelligence_signal_engine.compute_trading_signal(symbol)
        if sig:
            text = sig.to_prompt_text()
            if text:
                return f"### 情报信号\n{text}\n"
    except Exception as e:
        logger.debug(f"[AgentDeepContext] intel 块构建失败 {symbol}: {e}")
    return ""


def build_memory_block(
    db, symbol: str, account_id: int = None, *, agent_focus: str = None
) -> str:
    """交易记忆 + 亏损教训 + 历史类比 + Hermes 调参智慧（只读注入）。"""
    parts = []
    if not db:
        return ""

    # 逐笔战绩
    try:
        from backend.services.trade_memory_context import (
            build_recent_trades_section,
            build_loss_lessons_section,
        )
        recent = build_recent_trades_section(
            db,
            limit=10,
            nature={"swing": "swing", "trend": "trend"}.get(agent_focus or ""),
        )
        if recent:
            parts.append(recent)

        lessons = build_loss_lessons_section(db, symbols=[symbol.upper()])
        if lessons:
            parts.append(lessons)
    except Exception as e:
        logger.debug(f"[AgentDeepContext] memory 块构建失败: {e}")

    # RAG 历史类比
    try:
        from backend.services.experience_retriever import experience_retriever
        analogy = experience_retriever.format_for_prompt(db, [symbol.upper()])
        if analogy:
            parts.append(analogy)
    except Exception as e:
        logger.debug(f"[AgentDeepContext] RAG 块构建失败: {e}")

    # Hermes 历史调参智慧 + Agent 平仓决策智慧（只读，冷启动 graceful skip）
    try:
        from backend.services.hermes_proposal_wisdom_engine import proposal_wisdom
        from backend.services.hermes_agent_wisdom_engine import build_agent_wisdom_context

        # L1：不按 agent_focus 过滤（focus 字段存 master_close/frequency/global，非 swing/trend）
        hermes_params = proposal_wisdom.build_wisdom_context(limit=8)
        if hermes_params:
            parts.append(f"### Hermes 历史调参智慧\n{hermes_params}")

        agent_type = "swing" if (agent_focus or "") == "swing" else "trend"
        hermes_agent = build_agent_wisdom_context(agent_type, limit=5)
        if hermes_agent and "暂无" not in hermes_agent[:20]:
            parts.append(f"### Hermes Agent 决策智慧\n{hermes_agent}")
    except Exception as e:
        logger.debug(f"[AgentDeepContext] Hermes 智慧块构建失败: {e}")

    return "\n".join(parts) if parts else ""


def build_crypto_alpha_block(symbol: str) -> str:
    """币圈衍生品 alpha（清算磁吸/CVD/OBI/funding-OI 背离）。"""
    try:
        from backend.services.crypto_alpha_signals import crypto_alpha
        block = crypto_alpha.get_bundle(symbol).to_prompt_block()
        return block if block else ""
    except Exception as e:
        logger.debug(f"[AgentDeepContext] crypto_alpha 块构建失败 {symbol}: {e}")
    return ""


def build_stop_hunt_block(symbol: str, periods: list = None) -> str:
    """猎杀止损检测（Stop Hunt / Liquidity Grab）。

    加密市场最经典的操纵手法：主力故意击穿关键支撑/阻力位来触发散户止损，
    然后反向拉升/砸盘。检测方法：
    1. 找到近期 swing high/low（散户止损集中区）
    2. 检查价格是否短暂击穿这些位置（长影线 = 扫止损特征）
    3. 击穿后快速回收 = 猎杀确认，后续大概率反向

    同时输出"止损密集区"（散户止损最可能被触发的价位）和"流动性池"（大单挂单区）。
    """
    if periods is None:
        periods = ["4h", "1d"]

    try:
        import pandas as pd
        import numpy as np

        parts = []
        for tf in periods:
            kl = _fetch_klines_for_prompt(symbol, tf, count=50)
            if not kl or len(kl) < 20:
                continue
            kdf = pd.DataFrame(kl)

            # 1. 找 swing high/low（前N根的局部极值）
            window = 5
            highs = kdf["high"].rolling(window, center=True).max()
            lows = kdf["low"].rolling(window, center=True).min()
            swing_highs = kdf[kdf["high"] == highs]["high"].dropna().tail(3).tolist()
            swing_lows = kdf[kdf["low"] == lows]["low"].dropna().tail(3).tolist()

            # 2. 最近5根K线的猎杀特征检测
            recent = kdf.tail(5)
            current_price = float(recent.iloc[-1]["close"])
            body = abs(recent.iloc[-1]["close"] - recent.iloc[-1]["open"])
            range_ = float(recent.iloc[-1]["high"] - recent.iloc[-1]["low"])
            # 上影线 / 下影线比例
            upper_wick = float(recent.iloc[-1]["high"] - max(recent.iloc[-1]["close"], recent.iloc[-1]["open"]))
            lower_wick = float(min(recent.iloc[-1]["close"], recent.iloc[-1]["open"]) - recent.iloc[-1]["low"])
            upper_wick_pct = upper_wick / range_ * 100 if range_ > 0 else 0
            lower_wick_pct = lower_wick / range_ * 100 if range_ > 0 else 0

            # 3. 猎杀检测
            hunts = []
            for sh in swing_highs:
                # 价格是否短暂突破 swing high 然后收回
                if float(recent.iloc[-1]["high"]) >= sh * 0.998 and current_price < sh:
                    hunts.append(f"上方猎杀@{sh:.0f}(影线扫{upper_wick_pct:.0f}%，收回收)")
            for sl in swing_lows:
                if float(recent.iloc[-1]["low"]) <= sl * 1.002 and current_price > sl:
                    hunts.append(f"下方猎杀@{sl:.0f}(影线扫{lower_wick_pct:.0f}%，收回收)")

            # 4. 止损密集区（散户止损最可能的位置）
            # 做多止损在 swing low 下方 1-2%，做空止损在 swing high 上方 1-2%
            stop_clusters = []
            for sl in swing_lows:
                stop_price = sl * 0.99  # 止损通常在支撑下方 1%
                dist_pct = abs(current_price - stop_price) / current_price * 100
                if dist_pct < 5:
                    stop_clusters.append(f"多止损区@{stop_price:.0f}({dist_pct:.1f}%)")
            for sh in swing_highs:
                stop_price = sh * 1.01
                dist_pct = abs(current_price - stop_price) / current_price * 100
                if dist_pct < 5:
                    stop_clusters.append(f"空止损区@{stop_price:.0f}({dist_pct:.1f}%)")

            # 5. VPVR 流动性池（成交量集中区 = 大单挂单区）
            poc = vah = val = None
            try:
                from backend.services.unified_data_pool import unified_data_pool
                vp = unified_data_pool.compute_volume_profile_v2(symbol, days=7, bucket_count=50, va_pct=0.70)
                if vp and not vp.get("error"):
                    poc = float(vp.get("poc", 0))
                    vah = float(vp.get("vah", 0))
                    val = float(vp.get("val", 0))
            except Exception:
                pass

            # 构建输出
            tf_lines = [f"### {tf} 猎杀止损分析（当前价={current_price:.0f}）"]
            tf_lines.append(f"近期 swing high: {[round(x) for x in swing_highs]}")
            tf_lines.append(f"近期 swing low: {[round(x) for x in swing_lows]}")

            if hunts:
                tf_lines.append(f"⚠️ 猎杀信号: {'; '.join(hunts)}")
                tf_lines.append("  → 主力在扫散户止损，后续大概率反向。如果是下方猎杀→看多；上方猎杀→看空")
            else:
                # 检查是否接近止损密集区（即将被猎杀）
                near_stops = [s for s in stop_clusters if float(s.split("(")[1].replace("%)","")) < 2]
                if near_stops:
                    tf_lines.append(f"⚠️ 接近止损密集区: {'; '.join(near_stops)}")
                    tf_lines.append("  → 价格接近散户止损集中区，可能即将被猎杀，注意假突破")

            if stop_clusters:
                tf_lines.append(f"止损密集区: {'; '.join(stop_clusters)}")

            if poc:
                poc_dist = abs(current_price - poc) / current_price * 100
                tf_lines.append(f"流动性池: POC={poc:.0f}({poc_dist:.1f}%) VAH={vah:.0f} VAL={val:.0f}")
                if current_price < val * 1.01:
                    tf_lines.append("  → 价格在 VAL 下方，可能已扫完下方流动性池，倾向反弹")
                elif current_price > vah * 0.99:
                    tf_lines.append("  → 价格在 VAH 上方，可能已扫完上方流动性池，倾向回落")

            # K线影线特征
            if upper_wick_pct > 50:
                tf_lines.append(f"长上影线({upper_wick_pct:.0f}%) → 上方有抛压/假突破特征")
            if lower_wick_pct > 50:
                tf_lines.append(f"长下影线({lower_wick_pct:.0f}%) → 下方有买盘/扫止损回收")

            parts.append("\n".join(tf_lines) + "\n")

        return "\n".join(parts) if parts else ""
    except Exception as e:
        logger.debug(f"[AgentDeepContext] 猎杀止损块构建失败 {symbol}: {e}")
    return ""


def build_full_deep_context(
    symbol: str,
    *,
    db=None,
    account_id: int = None,
    kline_periods: list = None,
    kline_count: int = 30,
) -> str:
    """一键构建完整深度上下文（所有模块拼装）。

    Args:
        symbol: 交易对
        db: 数据库 session（用于交易记忆/教训/RAG）
        account_id: 账户 ID
        kline_periods: K线周期列表，如 ['1h','4h'] 或 ['4h','1d']
        kline_count: 每周期 K线条数
    """
    if kline_periods is None:
        kline_periods = ["1h", "4h"]

    parts = []

    # K线深度数据（最重要）
    kl_block = build_kline_block(symbol, kline_periods, kline_count)
    if kl_block:
        parts.append(kl_block)

    # 市场状态
    regime_block = build_regime_block(symbol)
    if regime_block:
        parts.append(regime_block)

    # 币圈衍生品 alpha
    crypto_block = build_crypto_alpha_block(symbol)
    if crypto_block:
        parts.append(crypto_block)

    # 猎杀止损分析（中线层：1h+4h 短周期）
    hunt_block = build_stop_hunt_block(symbol, periods=["1h", "4h"])
    if hunt_block:
        parts.append(hunt_block)
    intel_block = build_intel_block(symbol)
    if intel_block:
        parts.append(intel_block)

    # 链上/巨鲸摘要（best-effort；缺失时明确标注不可用）
    try:
        if db is not None:
            try:
                db.rollback()
            except Exception:
                pass
        _onchain_block = build_onchain_summary(symbol)
        if _onchain_block:
            parts.append(_onchain_block)
    except Exception:
        pass

    # 交易记忆 + 教训 + RAG
    if db:
        mem_block = build_memory_block(db, symbol, account_id, agent_focus="swing")
        if mem_block:
            parts.append(mem_block)

    result = "\n".join(parts)
    if result:
        logger.info(
            f"[AgentDeepContext] {symbol} 深度上下文构建完成: {len(result)} 字符 (~{len(result)//3} tokens), "
            f"模块数={len(parts)}"
        )
    return result


def build_onchain_summary(symbol: str) -> str:
    """best-effort 链上/巨鲸摘要；无数据时明确标注不可用，绝不虚构数字。"""
    lines: list = []
    try:
        from backend.services.onchain_data_collector import onchain_collector as _oc
        _data = _oc.collect_all([symbol]).get(symbol, {})
        if isinstance(_data, dict):
            for _k in (
                "fear_greed", "btc_dominance", "tvl", "exchange_net_flow",
                "active_addresses", "whale_tx_count", "whale_tx_volume",
            ):
                _v = _data.get(_k)
                try:
                    if _v is not None and float(_v) != 0:
                        lines.append(f"  {_k}: {float(_v):.4f}")
                except (TypeError, ValueError):
                    pass
            _smb = _data.get("stablecoin_mint_burn")
            try:
                if _smb is not None and float(_smb) != 0:
                    lines.append(f"  stablecoin_mint_burn: {float(_smb):.4f}")
            except (TypeError, ValueError):
                pass
    except Exception:
        pass
    try:
        from backend.services.whale_tracker_service import WhaleTrackerService
        _ws = WhaleTrackerService().get_whale_signal(str(symbol).upper())
        _summary = getattr(_ws, "summary", "") or ""
        if _summary and "暂无" not in _summary:
            lines.append(f"  巨鲸: {_summary}")
    except Exception:
        pass
    if not lines:
        return "### 链上/宏观数据\n（链上数据不可用）\n"
    return "### 链上/宏观数据（best-effort）\n" + "\n".join(lines) + "\n"


def build_trend_deep_context(
    symbol: str,
    *,
    db=None,
) -> str:
    """长线趋势专属深度上下文 — 比中线更深、更宏观。

    长线是整体交易的基石，需要：
    - 更大周期结构（4h+1d+周线级别）
    - 宏观资金流（BTC dominance、链上大额转账）
    - 关键价位地图（多周期支撑阻力、斐波那契）
    - 趋势生命周期判断（启动/加速/衰竭/反转信号）
    - 历史经验 + 预测推理
    """
    parts = []

    # 1. 多周期 K线（4h + 1d + 1w 真周线 + 1M 真月线）
    # [2026-08-10 v3.1.0] 纳入 1M：长线锚定月线级别大周期（asterdex 主所已全量回填）
    kl_block = build_kline_block(symbol, ["4h", "1d", "1w", "1M"], count=52)
    if kl_block:
        parts.append(kl_block)

    # 1b. 周线结构摘要（真 1w K 线，非日线推算）
    try:
        import pandas as pd
        kl_1w = _fetch_klines_for_prompt(symbol, "1w", count=52)
        if kl_1w and len(kl_1w) >= 8:
            wdf = pd.DataFrame(kl_1w)
            w_high = float(wdf["high"].max())
            w_low = float(wdf["low"].min())
            w_close = float(wdf["close"].iloc[-1])
            w_diff = w_high - w_low
            w_pos = (w_close - w_low) / w_diff * 100 if w_diff > 0 else 50
            w_ema9 = float(wdf["close"].ewm(span=9, adjust=False).mean().iloc[-1])
            w_ema21 = float(wdf["close"].ewm(span=21, adjust=False).mean().iloc[-1])
            w_trend = (
                "bullish" if w_ema9 > w_ema21 else
                "bearish" if w_ema9 < w_ema21 else "mixed"
            )
            parts.append(
                f"### 周线结构（真 1w K 线，{len(wdf)} 周）\n"
                f"52周高={w_high:.0f} 低={w_low:.0f} 当前={w_close:.0f} (位置={w_pos:.0f}%)\n"
                f"周线EMA趋势={w_trend} (EMA9={w_ema9:.0f} EMA21={w_ema21:.0f})\n"
            )
    except Exception as e:
        logger.debug(f"[AgentDeepContext] 周线结构块构建失败 {symbol}: {e}")

    # 1c. 月线结构摘要（真 1M K 线，长线大周期锚；asterdex 主所已回填 60 根）
    try:
        import pandas as pd
        kl_1M = _fetch_klines_for_prompt(symbol, "1M", count=52)
        if kl_1M and len(kl_1M) >= 12:
            mdf = pd.DataFrame(kl_1M)
            m_high = float(mdf["high"].max())
            m_low = float(mdf["low"].min())
            m_close = float(mdf["close"].iloc[-1])
            m_diff = m_high - m_low
            m_pos = (m_close - m_low) / m_diff * 100 if m_diff > 0 else 50
            m_ema9 = float(mdf["close"].ewm(span=9, adjust=False).mean().iloc[-1])
            m_ema21 = float(mdf["close"].ewm(span=21, adjust=False).mean().iloc[-1])
            m_trend = (
                "bullish" if m_ema9 > m_ema21 else
                "bearish" if m_ema9 < m_ema21 else "mixed"
            )
            parts.append(
                f"### 月线结构（真 1M K 线，{len(mdf)} 个月）\n"
                f"历史高={m_high:.0f} 低={m_low:.0f} 当前={m_close:.0f} (位置={m_pos:.0f}%)\n"
                f"月线EMA趋势={m_trend} (EMA9={m_ema9:.0f} EMA21={m_ema21:.0f})\n"
            )
        else:
            parts.append("### 月线结构\n（1M 月线数据不足 <12 根，暂缺月线锚）\n")
    except Exception as e:
        logger.debug(f"[AgentDeepContext] 月线结构块构建失败 {symbol}: {e}")

    # 2. 日线级别关键价位（高低点、支撑阻力、斐波那契 — 补充 1w 以下的中观地图）
    try:
        import pandas as pd
        kl_1d = _fetch_klines_for_prompt(symbol, "1d", count=90)
        if kl_1d and len(kl_1d) >= 30:
            kdf = pd.DataFrame(kl_1d)
            high_90 = float(kdf["high"].max())
            low_90 = float(kdf["low"].min())
            close_now = float(kdf["close"].iloc[-1])
            # 斐波那契回撤位
            diff = high_90 - low_90
            fib_382 = high_90 - diff * 0.382
            fib_500 = high_90 - diff * 0.500
            fib_618 = high_90 - diff * 0.618
            # 近期高低点（30天）
            high_30 = float(kdf["high"].iloc[-30:].max())
            low_30 = float(kdf["low"].iloc[-30:].min())
            # 价格位置
            pos_pct = (close_now - low_90) / (high_90 - low_90) * 100 if diff > 0 else 50

            parts.append(
                f"### 宏观关键价位地图（90天范围）\n"
                f"90天高={high_90:.0f} 低={low_90:.0f} 当前={close_now:.0f} (位置={pos_pct:.0f}%)\n"
                f"30天高={high_30:.0f} 低={low_30:.0f}\n"
                f"斐波那契回撤: 38.2%={fib_382:.0f} | 50%={fib_500:.0f} | 61.8%={fib_618:.0f}\n"
                f"价格相对90天范围: {'高位' if pos_pct>70 else '中位' if pos_pct>30 else '低位'}\n"
            )

            # 趋势生命周期判断
            ema9 = float(kdf["close"].ewm(span=9, adjust=False).mean().iloc[-1])
            ema21 = float(kdf["close"].ewm(span=21, adjust=False).mean().iloc[-1])
            ema50 = float(kdf["close"].ewm(span=50, adjust=False).mean().iloc[-1]) if len(kdf) >= 50 else ema21
            ema_gap_9_21 = abs(ema9 - ema21) / ema21 * 100
            ema_gap_21_50 = abs(ema21 - ema50) / ema50 * 100

            if ema9 > ema21 > ema50:
                if ema_gap_9_21 > ema_gap_21_50:
                    lifecycle = "加速上涨（EMA间距扩大）"
                else:
                    lifecycle = "减速上涨（EMA间距缩小，可能衰竭）"
            elif ema9 < ema21 < ema50:
                if ema_gap_9_21 > ema_gap_21_50:
                    lifecycle = "加速下跌（EMA间距扩大）"
                else:
                    lifecycle = "减速下跌（EMA间距缩小，可能见底）"
            else:
                lifecycle = "趋势混乱（EMA交叉缠绕）"

            parts.append(f"### 趋势生命周期\n{lifecycle} (EMA间距 9-21={ema_gap_9_21:.2f}% 21-50={ema_gap_21_50:.2f}%)\n")
    except Exception as e:
        logger.debug(f"[AgentDeepContext] 宏观价位块构建失败 {symbol}: {e}")

    # 3. 市场状态（4h+1d 双周期）
    regime_block = build_regime_block(symbol)
    if regime_block:
        parts.append(regime_block)

    # 4. 币圈衍生品 alpha（长线最关注 funding-OI 背离 + OI 趋势）
    crypto_block = build_crypto_alpha_block(symbol)
    if crypto_block:
        parts.append(crypto_block)

    # 4.5 猎杀止损分析（长线层：4h+1d+1w 大周期）
    hunt_block = build_stop_hunt_block(symbol, periods=["4h", "1d", "1w"])
    if hunt_block:
        parts.append(hunt_block)

    # 5. 情报信号（长线最关注恐贪指数 + 鲸鱼 + 新闻宏观影响）
    intel_block = build_intel_block(symbol)
    if intel_block:
        parts.append(intel_block)

    # 6. 链上/宏观数据（长线专属）
    try:
        # [2026-08-11 修复] 外部网络调用前释放调用方只读事务，
        # 防止 onchain 请求阻塞期间 DB 连接 idle-in-transaction。
        if db is not None:
            try:
                db.rollback()
            except Exception:
                pass
        _onchain_block = build_onchain_summary(symbol)
        if _onchain_block:
            parts.append(_onchain_block)
    except Exception:
        pass

    # 7. 交易记忆 + 教训 + RAG（长线经验更重要）
    if db:
        mem_block = build_memory_block(db, symbol, agent_focus="trend")
        if mem_block:
            parts.append(mem_block)

    result = "\n".join(parts)
    if result:
        logger.info(
            f"[AgentDeepContext] {symbol} 长线深度上下文: {len(result)} 字符 (~{len(result)//3} tokens), "
            f"模块数={len(parts)}"
        )
    return result
