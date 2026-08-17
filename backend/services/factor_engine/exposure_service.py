"""
M3 因子暴露矩阵服务

对应《短期因子策略全链路详细技术设计.md》§3。
数据源：factor_active_set(ACTIVE) + data_center（经 kline_data_service 门面）。
开关 FEATURE_FACTOR_EXPOSURE_ENABLED=false 时 exposure() 返回空（fail-safe）。
"""

from __future__ import annotations

import os
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

FEATURE_FACTOR_EXPOSURE_ENABLED = os.getenv(
    "FEATURE_FACTOR_EXPOSURE_ENABLED", "false"
).lower() in ("1", "true", "yes", "on")

logger = logging.getLogger(__name__)
_GOVERNANCE_COLUMNS_CHECKED = False


def _exposure_enabled() -> bool:
    """运行时读开关（避免 import 早于 load_dotenv 被冻成 false）。"""
    return os.getenv("FEATURE_FACTOR_EXPOSURE_ENABLED", "false").lower() in (
        "1", "true", "yes", "on",
    )


def summarize_exposure(
    symbol: str,
    period: str = "15m",
    count: int = 200,
) -> tuple[Optional[float], Dict[str, Any]]:
    """统一因子匹配汇总：与 auto_coin M4 对齐。

    Returns:
        (match_score in [-1, 1] or None, detail)
        detail 含 top / alpha / n / reason，前端可展示「为何为空」。
    """
    detail: Dict[str, Any] = {"top": [], "n": 0, "alpha": 0.0, "reason": None}
    if not _exposure_enabled():
        detail["reason"] = "disabled"
        return None, detail
    try:
        rows = factor_exposure_service.exposure(symbol, period, count) or []
    except Exception as e:
        detail["reason"] = "error"
        detail["error"] = str(e)[:200]
        return None, detail
    if not rows:
        # 区分：缺 K 线 vs 无 ACTIVE / 计算失败
        try:
            from backend.services.kline_data_service import kline_service

            ks = kline_service.query_klines(
                str(symbol or "").upper().split("-")[0].split("/")[0],
                period,
                limit=30,
                order="desc",
                purpose="research",
            )
            detail["reason"] = "no_klines" if not ks else "no_active_or_eval_fail"
        except Exception:
            detail["reason"] = "empty"
        return None, detail
    alpha = sum(float(e.get("expected_alpha") or 0) for e in rows)
    # 与 auto_coin_selector M4 同一归一化
    match = max(-1.0, min(1.0, alpha * 8.0))
    top = sorted(
        rows,
        key=lambda e: abs(float(e.get("expected_alpha") or 0)),
        reverse=True,
    )[:8]
    detail.update({
        "top": top,
        "n": len(rows),
        "alpha": round(alpha, 8),
        "reason": "ok",
    })
    return float(match), detail


@dataclass
class FactorExposure:
    """单因子暴露（设计文档 §3.1）。"""
    factor_id: str
    expr_id: str = ""
    z_score: float = 0.0
    net_ic: float = 0.0
    weight: float = 0.0

    @property
    def expected_alpha(self) -> float:
        """期望 alpha = z_score × net_ic × weight。"""
        return self.z_score * self.net_ic * self.weight

    def to_dict(self) -> Dict[str, Any]:
        return {
            "factor_id": self.factor_id,
            "expr_id": self.expr_id,
            "z_score": round(self.z_score, 4),
            "net_ic": round(self.net_ic, 6),
            "weight": round(self.weight, 6),
            "expected_alpha": round(self.expected_alpha, 8),
        }


class FactorExposureService:
    """单例；内存缓存 30s；DB 快照（10min）待启用后接入。"""

    _instance: Optional["FactorExposureService"] = None
    _inst_lock = threading.Lock()

    def __init__(self):
        self._cache: Dict[tuple, tuple] = {}   # (symbol, period) -> (ts, list[FactorExposure])
        self._cache_ttl = 30.0
        self._lock = threading.Lock()
        self._fail_factors = 0
        self._last_snapshot_ts = 0.0

    @classmethod
    def get_instance(cls) -> "FactorExposureService":
        if cls._instance is None:
            with cls._inst_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def exposure(
        self,
        symbol: str,
        period: str = "5m",
        count: int = 200,
    ) -> List[Dict[str, Any]]:
        """返回该 symbol 在活跃因子上的暴露列表。

        未启用（或数据源未接入）时返回 []；
        启用后流程：加载 factor_active_set(ACTIVE) → data_center.get_klines
        → expr.evaluate → rolling z-score → expected_alpha。
        """
        if not _exposure_enabled():
            return []
        key = (str(symbol).upper(), str(period).lower())
        now = time.time()
        with self._lock:
            cached = self._cache.get(key)
            if cached and now - cached[0] < self._cache_ttl:
                return [e.to_dict() for e in cached[1]]
        try:
            rows = self._compute(symbol, period, count)
        except Exception:
            rows = []
        with self._lock:
            self._cache[key] = (time.time(), rows)
        return [e.to_dict() for e in rows]

    def matrix(self, symbols: List[str], period: str = "5m"):
        """选币/PRL 用：行=symbol、列=factor_id、值=expected_alpha。"""
        if not _exposure_enabled():
            import pandas as pd
            return pd.DataFrame()
        import pandas as pd
        data: Dict[str, Dict[str, float]] = {}
        for s in symbols or []:
            data[s.upper()] = {
                e["factor_id"]: e["expected_alpha"]
                for e in self.exposure(s, period)
            }
        return pd.DataFrame.from_dict(data, orient="index")

    def snapshot(self, symbols: List[str], periods: List[str]) -> None:
        """后台快照任务（10min）：写 factor_exposure_snapshots。

        [2026-08-16 泄漏修复] 原实现先开会话再逐币 _compute（分钟级因子计算），
        事务挂在 idle-in-transaction >120s 被 LeakGuard 强杀 → 本批全部回滚
        （日志实锤 INSERT INTO factor_exposure_snapshots 被反复 kill）。
        改为「先算后写」：全部计算完收集 payload，再一次性短事务插入提交。
        """
        if not _exposure_enabled():
            return
        try:
            self._ensure_snapshot_table()
            payload = []
            for sym in (symbols or [])[:60]:
                for period in (periods or ["5m"])[:4]:
                    try:
                        rows = self._compute(sym, period, 200)
                    except Exception:
                        continue
                    for e in rows:
                        payload.append({
                            "s": str(sym).upper(), "p": period,
                            "f": e.factor_id, "z": e.z_score,
                            "a": e.expected_alpha, "w": e.weight,
                        })
            if payload:
                from backend.database.connection import AnalyticsSessionLocal
                from sqlalchemy import text as _sa_text
                with AnalyticsSessionLocal() as db:
                    db.execute(_sa_text("SET LOCAL app.is_admin='on'"))
                    for p in payload:
                        db.execute(_sa_text(
                            "INSERT INTO factor_exposure_snapshots "
                            "(ts, symbol, period, factor_id, z_score, expected_alpha, weight) "
                            "VALUES (now(), :s, :p, :f, :z, :a, :w)"
                        ), p)
                    db.commit()
            self._last_snapshot_ts = time.time()
        except Exception as exc:
            logger.debug("[FactorExposure] snapshot failed: %s", exc)

    # ------------------------------------------------------------------

    def _load_active(self) -> List[Dict[str, Any]]:
        """加载可参与匹配的因子（factor_active_set）。

        [2026-08-08 P1-3] 新晋升几乎总是 PAPER；若只读 ACTIVE，M4 factor_match
        在影子期长期空转。纳入 ACTIVE/PAPER/SMALL_LIVE；影子态无权重时给地板
        权重（PAPER=0.5 / SMALL_LIVE=0.75），ACTIVE 缺权重给 1.0。
        """
        global _GOVERNANCE_COLUMNS_CHECKED
        if not _GOVERNANCE_COLUMNS_CHECKED:
            self._ensure_governance_columns()
            _GOVERNANCE_COLUMNS_CHECKED = True
        try:
            from backend.services.factor_engine.active_set_policy import (
                ActiveSetRole,
                load_factor_active_rows,
            )
            rows = load_factor_active_rows(ActiveSetRole.TRADABLE, parse_expr=True)
            _floor = {"ACTIVE": 1.0, "SMALL_LIVE": 0.75, "PAPER": 0.5}
            out = []
            for r in rows:
                try:
                    expr = r.get("expr")
                    if not expr:
                        continue
                    w = 0.0
                    cw = r.get("current_weight") or {}
                    if cw:
                        vals = list(cw.values()) if isinstance(cw, dict) else []
                        w = float(vals[0]) if vals else 0.0
                    if w <= 0:
                        w = float(_floor.get(str(r.get("state")), 0.5))
                    out.append({
                        "factor_id": r.get("factor_id"),
                        "expr": expr,
                        "net_ic": float(r.get("last_net_ic") or r.get("icir") or 0),
                        "weight": w,
                        "state": str(r.get("state")),
                    })
                except Exception:
                    continue
            if out:
                logger.debug(
                    "[FactorExposure] 载入 %d 因子 (TRADABLE)", len(out),
                )
            return out
        except Exception as e:
            logger.warning("[FactorExposure] _load_active 失败: %s", e)
            return []

    def _compute(self, symbol: str, period: str, count: int) -> List[FactorExposure]:
        import numpy as np
        import pandas as pd

        from backend.services.alpha.factor_compute import kline_df_to_fields

        sym = str(symbol or "").upper().split("-")[0].split("/")[0]
        # 选币/暴露是研究打分，不是下单：必须用 research，
        # 否则 trade 门控把「略过期但仍有数据」的山寨币 K 线清空 → 因子永远 —。
        klines: List[Dict[str, Any]] = []
        try:
            from backend.services.kline_data_service import kline_service

            klines = kline_service.query_klines(
                sym,
                period,
                limit=max(int(count), 60),
                order="asc",
                purpose="research",
            ) or []
        except Exception as e:
            logger.debug("[FactorExposure] research klines %s: %s", sym, e)
            klines = []
        if not klines:
            return []
        # 统一按时间升序算 z-score
        klines = sorted(
            klines,
            key=lambda k: int(k.get("timestamp") or 0),
        )
        df = pd.DataFrame([{
            "open": float(k.get("open") or 0), "high": float(k.get("high") or 0),
            "low": float(k.get("low") or 0), "close": float(k.get("close") or 0),
            "volume": float(k.get("volume") or 0),
        } for k in klines])
        if len(df) < 30:
            return []
        fields = kline_df_to_fields(df)
        active = self._load_active()
        if not active:
            return []
        weight_sum = sum(float(a["weight"] or 0) for a in active) or 1.0
        out: List[FactorExposure] = []
        for a in active:
            try:
                vals = a["expr"].evaluate(fields)
                s = pd.Series(vals)
                window = max(30, min(len(s) // 3, 120))
                roll = s.rolling(window)
                mean = roll.mean()
                std = roll.std(ddof=0)
                z = (s - mean) / std.replace(0, np.nan)
                z_last = float(z.iloc[-1]) if len(z) and np.isfinite(z.iloc[-1]) else 0.0
                w = float(a["weight"] or 0) / weight_sum
                out.append(FactorExposure(
                    factor_id=a["factor_id"],
                    expr_id=a["factor_id"],
                    z_score=round(z_last, 4),
                    net_ic=float(a["net_ic"] or 0),
                    weight=round(w, 6),
                ))
            except Exception:
                self._fail_factors += 1
                continue
        return out

    @staticmethod
    def _ensure_governance_columns() -> None:
        """???? factor_active_set ????M2??"""
        try:
            from backend.database.connection import AnalyticsSessionLocal
            from sqlalchemy import text as _sa_text
            with AnalyticsSessionLocal() as db:
                for col, typ in (
                    ("last_net_ic", "DOUBLE PRECISION"),
                    ("turnover", "DOUBLE PRECISION"),
                    ("evaluated_cycles", "INTEGER"),
                ):
                    db.execute(_sa_text(
                        f"ALTER TABLE factor_active_set ADD COLUMN IF NOT EXISTS {col} {typ}"
                    ))
                db.commit()
        except Exception:
            pass

    @staticmethod
    def _ensure_snapshot_table() -> None:
        """??? factor_exposure_snapshots ??"""
        try:
            from backend.database.connection import AnalyticsSessionLocal
            from sqlalchemy import text as _sa_text
            with AnalyticsSessionLocal() as db:
                db.execute(_sa_text(
                    "CREATE TABLE IF NOT EXISTS factor_exposure_snapshots ("
                    " id BIGSERIAL PRIMARY KEY,"
                    " ts TIMESTAMPTZ NOT NULL DEFAULT now(),"
                    " symbol VARCHAR(32) NOT NULL,"
                    " period VARCHAR(8) NOT NULL,"
                    " factor_id VARCHAR(64) NOT NULL,"
                    " z_score DOUBLE PRECISION,"
                    " expected_alpha DOUBLE PRECISION,"
                    " weight DOUBLE PRECISION)"
                ))
                db.execute(_sa_text(
                    "CREATE INDEX IF NOT EXISTS idx_fexp_sym_time "
                    "ON factor_exposure_snapshots(symbol, ts DESC)"
                ))
                db.commit()
        except Exception:
            pass

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": _exposure_enabled(),
            "cache_entries": len(self._cache),
            "fail_factors": self._fail_factors,
            "last_snapshot_ts": self._last_snapshot_ts,
        }


factor_exposure_service = FactorExposureService.get_instance()
