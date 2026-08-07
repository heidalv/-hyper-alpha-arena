"""
数据中台启动门禁 / 覆盖率验收（阶段 4）。

硬约束对齐：
  H1 默认所 asterdex（可切换）
  H2 purpose=trade 强制 active_exchange，禁止静默跨所
  H3 业务读走 data_center
  H4 四所 catalog/heartbeat 可观测
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 交易热路径：不允许再出现 exchange="hyperliquid" 硬编码取 K 线
_HOT_PATH_FILES = [
    "backend/services/full_auto/loops/scalp_loop.py",
    "backend/services/scalp/mtf_resonance_engine.py",
    "backend/services/full_auto/v3_factor_pipeline.py",
    "backend/services/full_auto/ai_decisions.py",
    "backend/services/paper_trading_engine.py",
]

_HARDCODE_PATTERNS = [
    re.compile(r'get_klines_from_db\([^)]*exchange\s*=\s*["\']hyperliquid["\']', re.I),
    re.compile(r'get_klines\([^)]*exchange\s*=\s*["\']hyperliquid["\'][^)]*purpose\s*=\s*["\']trade["\']', re.I),
]


def _repo_root() -> Path:
    # .../Hyper-Alpha-Arena/backend/services/this.py → repo root
    return Path(__file__).resolve().parents[2]


def scan_hot_path_hardcodes() -> List[Dict[str, Any]]:
    """扫描热路径是否仍硬编码 hyperliquid 作为 K 线源。"""
    root = _repo_root()
    hits: List[Dict[str, Any]] = []
    for rel in _HOT_PATH_FILES:
        path = root / rel
        if not path.exists():
            hits.append({"file": rel, "ok": False, "error": "missing"})
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            hits.append({"file": rel, "ok": False, "error": str(e)})
            continue
        bad = []
        for pat in _HARDCODE_PATTERNS:
            for m in pat.finditer(text):
                line = text[: m.start()].count("\n") + 1
                bad.append({"line": line, "snippet": m.group(0)[:120]})
        hits.append({"file": rel, "ok": len(bad) == 0, "hits": bad})
    return hits


def check_trade_source_isolation() -> Dict[str, Any]:
    """运行时：purpose=trade 不得静默改读他所。"""
    from backend.services.exchange_config import get_active_exchange
    from backend.services.data_center import data_center

    active = (get_active_exchange() or "").strip().lower()
    if active == "aster":
        active = "asterdex"

    result = data_center.get_klines("BTC", "5m", count=5, exchange="hyperliquid", purpose="trade")
    actual = (result.exchange or "").strip().lower()
    ok = (not actual) or (actual == active)
    return {
        "ok": ok,
        "active_exchange": active,
        "requested": "hyperliquid",
        "actual_exchange": actual,
        "count": result.count,
        "stale_sec": result.stale_sec,
        "fresh": bool(result.is_fresh) if result.count else None,
        "detail": "trade 强制 active_exchange" if ok else "跨所静默回退仍存在",
    }


def check_default_exchange() -> Dict[str, Any]:
    try:
        from config import settings
        default = (getattr(settings, "DEFAULT_EXCHANGE", None) or "asterdex").strip().lower()
    except Exception:
        default = (os.getenv("DEFAULT_EXCHANGE") or "asterdex").strip().lower()
    if default == "aster":
        default = "asterdex"
    return {
        "ok": default == "asterdex",
        "DEFAULT_EXCHANGE": default,
        "detail": "默认所应为 asterdex（可被会话覆盖）",
    }


def check_coverage_gate(
    *,
    exchanges: Optional[List[str]] = None,
    min_catalog: int = 50,
) -> Dict[str, Any]:
    """四所 catalog 覆盖门禁（弱门：告警，不阻断启动）。"""
    if exchanges is None:
        try:
            from config import settings
            exchanges = list(getattr(settings, "KLINE_SYNC_EXCHANGES", None) or [])
        except Exception:
            exchanges = []
    if not exchanges:
        exchanges = ["asterdex", "binance", "okx", "hyperliquid"]

    from backend.services.kline_sync_meta import get_catalog_coverage, get_heartbeats, list_catalog_symbols

    coverage = get_catalog_coverage()
    by_ex = {c.get("exchange"): c for c in coverage}
    heartbeats = get_heartbeats()
    items = []
    all_ok = True
    for ex in exchanges:
        ex_n = ex.strip().lower()
        if ex_n == "aster":
            ex_n = "asterdex"
        cat_n = int((by_ex.get(ex_n) or {}).get("catalog_trading") or 0)
        if cat_n <= 0:
            # 内存/即时再拉一次计数（不强制写入）
            try:
                cat_n = len(list_catalog_symbols(ex_n) or [])
            except Exception:
                cat_n = 0
        ok = cat_n >= min_catalog
        if not ok:
            all_ok = False
        hb = [h for h in heartbeats if h.get("exchange") == ex_n]
        items.append({
            "exchange": ex_n,
            "ok": ok,
            "catalog_trading": cat_n,
            "min_required": min_catalog,
            "heartbeats": hb,
        })
    return {"ok": all_ok, "exchanges": items, "raw_coverage": coverage}


def run_startup_gate(*, block_on_hard_fail: bool = False) -> Dict[str, Any]:
    """启动自检。硬失败（错源）可配置是否阻断；覆盖率不足仅告警。"""
    report: Dict[str, Any] = {"ok": True, "checks": {}}

    try:
        report["checks"]["default_exchange"] = check_default_exchange()
    except Exception as e:
        report["checks"]["default_exchange"] = {"ok": False, "error": str(e)}

    try:
        report["checks"]["trade_isolation"] = check_trade_source_isolation()
    except Exception as e:
        report["checks"]["trade_isolation"] = {"ok": False, "error": str(e)}

    try:
        hardcodes = scan_hot_path_hardcodes()
        report["checks"]["hot_path_hardcodes"] = {
            "ok": all(x.get("ok") for x in hardcodes),
            "files": hardcodes,
        }
    except Exception as e:
        report["checks"]["hot_path_hardcodes"] = {"ok": False, "error": str(e)}

    try:
        report["checks"]["coverage"] = check_coverage_gate()
    except Exception as e:
        report["checks"]["coverage"] = {"ok": False, "error": str(e)}

    hard_keys = ("default_exchange", "trade_isolation", "hot_path_hardcodes")
    hard_ok = all(report["checks"].get(k, {}).get("ok") for k in hard_keys)
    soft_ok = bool(report["checks"].get("coverage", {}).get("ok"))
    report["hard_ok"] = hard_ok
    report["soft_ok"] = soft_ok
    report["ok"] = hard_ok  # 启动硬门只看硬检查

    if hard_ok:
        logger.info("[DataCenterGate] 启动硬门通过 hard_ok=True soft_coverage=%s", soft_ok)
    else:
        logger.error("[DataCenterGate] 启动硬门失败: %s", report["checks"])

    if soft_ok:
        logger.info("[DataCenterGate] 四所 catalog 覆盖达标")
    else:
        logger.warning(
            "[DataCenterGate] 四所 catalog 覆盖不足（不阻断启动，继续 P1 填充）: %s",
            report["checks"].get("coverage"),
        )

    if block_on_hard_fail and not hard_ok:
        raise RuntimeError(f"DataCenterGate hard fail: {report}")

    return report
