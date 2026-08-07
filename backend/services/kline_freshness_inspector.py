"""K线数据新鲜度巡检 — 检测交易币种的 K线数据是否缺失/陈旧并告警。

背景：
  data_quality_monitor.check_kline_freshness() 检测能力完备（stale>5min warning、
  >10min critical、无数据 critical），但历史上从未被任何调度/采集器调用，
  导致 JTO 等币种 K线停滞数小时也无告警，交易却在静默运行。

本巡检器：
  1. 从 .env 读交易币种(MARKET_DATA_V2_SYMBOLS)和交易所(MARKET_DATA_V2_EXCHANGES)
  2. 直查 alpha_market.crypto_klines 取每个 symbol×period 的最新时间戳
  3. 调 check_kline_freshness 判定告警
  4. critical 告警推飞书(若配置了 webhook)，所有告警存内存供 API 查
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 三周期策略关心的最小周期集（长/中/短），缺一不可交易
# 2026-07-06 整改（审查 3 #21）：加入 4h 和 1w——TIER_TIMEFRAME_MAP 里 mid 的
# confirm 周期包含 4h、long 的 primary/confirm 分别是 4h/1d/1w，这两个周期此前
# 完全不在巡检范围内，意味着即使 4h/1w K线停更数小时/数天，也不会有任何告警，
# 而中长线策略却在拿着陈旧的 4h/1w 数据做决策。
_DEFAULT_PERIODS = ["1d", "1w", "4h", "1h", "5m", "15m"]
# 单 symbol 单周期允许的最大滞后（秒），超过即 warning/critical
# 由 data_quality_monitor.STALE_THRESHOLD_SEC 推导：warning=300s, critical=600s
# 但不同周期本就有不同的合理刷新间隔，这里按周期放宽：分钟级5min、小时级2h、日线级36h
_PERIOD_STALE_SEC: Dict[str, float] = {
    "1m": 300, "3m": 600, "5m": 600, "15m": 1200, "30m": 2400,
    "1h": 7200, "4h": 28800, "1d": 129600, "1w": 604800,
}


def _stale_threshold(period: str) -> float:
    return _PERIOD_STALE_SEC.get(period, 600)


class KlineFreshnessInspector:
    """K线新鲜度巡检器（单例）。"""

    def __init__(self) -> None:
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._last_check_at: Optional[float] = None
        self._last_alerts: List[Dict[str, Any]] = []
        self._last_snapshot: Dict[str, Any] = {}

    # ── 配置解析 ──────────────────────────────────
    def _symbols(self) -> List[str]:
        raw = os.getenv("MARKET_DATA_V2_SYMBOLS", "") or os.getenv("KLINE_FRESHNESS_SYMBOLS", "")
        if not raw or raw == "account_selected":
            # 兜底：用全交易系统最常见的活跃币种
            raw = "BTC,ETH,SOL,BNB,ASTER,JTO"
        return [s.strip().upper() for s in raw.split(",") if s.strip()]

    def _exchanges(self) -> List[str]:
        raw = os.getenv("MARKET_DATA_V2_EXCHANGES", "hyperliquid,binance,asterdex")
        return [e.strip().lower() for e in raw.split(",") if e.strip()]

    def _periods(self) -> List[str]:
        raw = os.getenv("KLINE_FRESHNESS_PERIODS", ",".join(_DEFAULT_PERIODS))
        return [p.strip() for p in raw.split(",") if p.strip()]

    def _interval(self) -> int:
        return max(60, int(os.getenv("KLINE_FRESHNESS_INTERVAL_S", "300")))

    # ── 核心检测 ──────────────────────────────────
    def check_once(self) -> Dict[str, Any]:
        """执行一次新鲜度检测，返回 {alerts, snapshot, summary}。"""
        symbols = self._symbols()
        exchanges = self._exchanges()
        periods = self._periods()
        now = time.time()
        alerts: List[Dict[str, Any]] = []
        snapshot: Dict[str, Any] = {}

        try:
            from sqlalchemy import create_engine, text as sa_text
            market_url = os.getenv("MARKET_DATABASE_URL") or os.getenv("DATABASE_URL", "").rsplit("/", 1)[0] + "/alpha_market"
            if not market_url:
                logger.warning("[KlineFreshness] 无 MARKET_DATABASE_URL，跳过")
                return {"alerts": [], "snapshot": {}, "error": "no market db url"}
            # 有交易活动的交易所（主交易所 + 活跃自动交易账户的 selected_exchange）
            # 都要全周期告警；无交易的所仅监控 1m（避免对未使用的所长周期误报）。
            trading_exchanges: set = set()
            try:
                from backend.services.kline_realtime_collector import get_trading_exchanges
                trading_exchanges = set(get_trading_exchanges())
            except Exception:
                pass
            if not trading_exchanges:
                try:
                    from backend.services.exchange_config import get_active_exchange
                    ae = (get_active_exchange() or "").lower()
                    if ae:
                        trading_exchanges = {ae}
                except Exception:
                    pass
            engine = create_engine(market_url)
            try:
                with engine.connect() as conn:
                    for ex in exchanges:
                        ex_snap = snapshot.setdefault(ex, {})
                        is_trading = (ex in trading_exchanges) or not trading_exchanges
                        # 有交易的交易所：全周期检测；无交易的所：仅 1m
                        periods_for_ex = periods if is_trading else ["1m"]
                        for sym in symbols:
                            sym_alerts = []
                            sym_snap = {"periods": {}, "worst_status": "ok"}
                            for p in periods_for_ex:
                                row = conn.execute(sa_text(
                                    "SELECT COUNT(*) AS n, COALESCE(MAX(timestamp), 0) AS latest "
                                    "FROM crypto_klines WHERE exchange=:ex AND symbol=:sym AND period=:p"
                                ), {"ex": ex, "sym": sym, "p": p}).fetchone()
                                n = int(row[0] or 0)
                                latest = float(row[1] or 0)
                                age = now - latest if latest else None
                                thresh = _stale_threshold(p)

                                status = "ok"
                                if n == 0 or latest == 0:
                                    status = "missing"
                                    sym_alerts.append({"level": "critical", "exchange": ex,
                                        "symbol": sym, "period": p, "issue": "missing",
                                        "message": f"{ex}/{sym} {p} K线缺失(无数据)"})
                                elif age is not None and age > thresh * 2:
                                    status = "critical"
                                    sym_alerts.append({"level": "critical", "exchange": ex,
                                        "symbol": sym, "period": p, "issue": "stale",
                                        "age_min": round(age / 60, 1),
                                        "message": f"{ex}/{sym} {p} K线停滞 {age/60:.0f}分钟(>{thresh*2/60:.0f}分)"})
                                elif age is not None and age > thresh:
                                    status = "warning"
                                    sym_alerts.append({"level": "warning", "exchange": ex,
                                        "symbol": sym, "period": p, "issue": "stale",
                                        "age_min": round(age / 60, 1),
                                        "message": f"{ex}/{sym} {p} K线滞后 {age/60:.0f}分钟(>{thresh/60:.0f}分)"})

                                sym_snap["periods"][p] = {"count": n, "age_min": round(age / 60, 1) if age else None, "status": status}
                                # 取最差状态
                                if status == "critical" or sym_snap["worst_status"] != "critical":
                                    rank = {"ok": 0, "warning": 1, "critical": 2, "missing": 2}
                                    if rank[status] > rank.get(sym_snap["worst_status"], 0):
                                        sym_snap["worst_status"] = status

                            alerts.extend(sym_alerts)
                            ex_snap[sym] = sym_snap
            finally:
                engine.dispose()
        except Exception as e:
            logger.error(f"[KlineFreshness] 检测失败: {e}", exc_info=True)
            return {"alerts": [], "snapshot": {}, "error": str(e)}

        self._last_check_at = now
        self._last_alerts = alerts[-200:]
        self._last_snapshot = snapshot

        summary = {
            "checked": len(symbols) * len(periods) * len(exchanges),
            "alerts": len(alerts),
            "critical": sum(1 for a in alerts if a["level"] == "critical"),
            "warning": sum(1 for a in alerts if a["level"] == "warning"),
            "ok_symbols": sum(1 for ex in snapshot.values() for s in ex.values() if s.get("worst_status") == "ok"),
        }

        # critical 告警推飞书（异步，失败不影响巡检）
        critical = [a for a in alerts if a["level"] == "critical"]
        if critical:
            try:
                asyncio.get_event_loop().create_task(self._notify(critical))
            except RuntimeError:
                pass

        logger.info(
            "[KlineFreshness] 巡检完成: %d 项, 告警 %d(critical=%d warning=%d)",
            summary["checked"], summary["alerts"], summary["critical"], summary["warning"],
        )
        return {"alerts": alerts, "snapshot": snapshot, "summary": summary}

    async def _notify(self, critical: List[Dict[str, Any]]) -> None:
        """critical 告警推飞书。"""
        try:
            from backend.services.openclaw_notify import get_notifier, NotifyLevel
            notifier = get_notifier()
            lines = ["🚨 K线数据缺失告警\n"] + [f"• {a['message']}" for a in critical[:15]]
            await notifier.send(
                text="\n".join(lines),
                title="K线数据告警",
                level=NotifyLevel.CRITICAL,
                event_type="system",
            )
        except Exception as e:
            logger.debug(f"[KlineFreshness] 飞书通知未发送(可能未配置): {e}")

    # ── 调度 ──────────────────────────────────
    def start(self) -> Dict[str, Any]:
        if self._running:
            return {"started": False, "reason": "already_running"}
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        self._running = True
        self._task = loop.create_task(self._loop())
        return {"started": True, "interval_s": self._interval(),
                "symbols": self._symbols(), "exchanges": self._exchanges(), "periods": self._periods()}

    async def _loop(self) -> None:
        # 启动后 30s 先跑一次，再按间隔
        await asyncio.sleep(30)
        while self._running:
            try:
                self.check_once()
            except Exception as exc:
                logger.error(f"[KlineFreshness] loop error: {exc}")
            await asyncio.sleep(self._interval())

    def status(self) -> Dict[str, Any]:
        return {
            "running": self._running,
            "last_check_at": datetime.fromtimestamp(self._last_check_at, tz=timezone.utc).isoformat() if self._last_check_at else None,
            "interval_s": self._interval(),
            "symbols": self._symbols(),
            "exchanges": self._exchanges(),
            "periods": self._periods(),
            "summary": {
                "alerts": len(self._last_alerts),
                "critical": sum(1 for a in self._last_alerts if a["level"] == "critical"),
                "warning": sum(1 for a in self._last_alerts if a["level"] == "warning"),
            },
            "last_alerts": self._last_alerts[-20:],
            "last_snapshot": self._last_snapshot,
        }


kline_freshness_inspector = KlineFreshnessInspector()
