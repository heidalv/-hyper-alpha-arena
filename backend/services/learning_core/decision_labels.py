# -*- coding: utf-8 -*-
"""[工作流①-b] 决策持久化标注（摆脱快照保留期约束，2026-10-04）。

## 为什么必须做（实测阻断）
`decision_snapshots` 只保留 ~8 天，而前向标注需要 7 天窗口 ⇒ **可标注样本永远无法累积**：
```
实测：labeled 从 335（10-04 凌晨）→ 30（同日稍后）—— 旧决策在被标注前就被清理
```
两条路：(a) 延长快照保留期（治标，历史已删找不回）；(b) **独立标注表，只增不删**（本模块）。
选 (b)：它同时修掉本会话反复吃亏的根因 —— "数据被删就无法回看"（快照回填 7.4%、误删 prompt_versions 等）。

## 设计
· 表：`decision_labels`（**独立库 `alpha_analytics`，只增不删**）
    decision_id(PK) / trace_id / symbol / lane / tier / action / direction / confidence /
    decided_at / entry_price / price_1d / price_3d / price_7d / ret_1d / ret_3d / ret_7d /
    label_1d / label_3d / label_7d / filled_at / source
· 写入时机：**决策时**固化 entry_price（不等 7 天）；之后由 `backfill_labels()` 补齐各周期价格。
  ⇒ 即使快照被删，标注表里的 entry_price 仍在，标注永远不会丢。
· 幂等：`INSERT ... ON CONFLICT (decision_id) DO NOTHING`；补价只更新 NULL 列。
· 开关：`FWD_LABEL_TABLE=off|shadow|on`
    · `off`（默认）—— 完全不写（行为与今日一致）
    · `shadow` —— 只写表，不影响任何判定
    · `on` —— 未来供 p_win 校准读取
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DDL = """
CREATE TABLE IF NOT EXISTS decision_labels (
    decision_id   BIGINT PRIMARY KEY,
    trace_id      TEXT,
    symbol        TEXT,
    lane          TEXT,
    tier          TEXT,
    action        TEXT,
    direction     TEXT,
    confidence    DOUBLE PRECISION,
    decided_at    TIMESTAMP,
    entry_price   DOUBLE PRECISION,
    price_1d      DOUBLE PRECISION,
    price_3d      DOUBLE PRECISION,
    price_7d      DOUBLE PRECISION,
    ret_1d        DOUBLE PRECISION,
    ret_3d        DOUBLE PRECISION,
    ret_7d        DOUBLE PRECISION,
    label_1d      SMALLINT,
    label_3d      SMALLINT,
    label_7d      SMALLINT,
    filled_at     TIMESTAMP,
    source        TEXT DEFAULT 'live_decision'
);
CREATE INDEX IF NOT EXISTS idx_decision_labels_lane_tier
    ON decision_labels (lane, tier, confidence);
"""


_ENV_FALLBACK: Dict[str, str] = {}


def _env_with_dotenv_fallback(name: str, default: str) -> str:
    """先 `os.getenv`；**取不到时直读 .env**。

    [2026-10-04 决定性修复] 实测：`.env` 里的键**进不了后端进程的 os.environ**
    （同一问题导致过 `ANALYSIS_*` 配额键失效、看门狗误判）。而"设用户级环境变量"
    对**已存在的 shell 派生出的进程**也不生效（实测新进程 `os.getenv` 仍为 None）。
    ⇒ 唯一可靠做法是自己读 `.env`。任何开关类读取都应走这里。
    """
    v = os.getenv(name)
    if v is not None and str(v).strip() != "":
        return str(v)
    if name in _ENV_FALLBACK:
        return _ENV_FALLBACK[name]
    val = default
    try:
        import io as _io
        from pathlib import Path as _P

        root = _P(__file__).resolve().parents[3]
        for cand in (root / ".env", _P.cwd() / ".env"):
            if not cand.exists():
                continue
            with _io.open(cand, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    s = line.strip()
                    if s.startswith("#") or "=" not in s:
                        continue
                    if s.startswith(name + "="):
                        val = s.split("=", 1)[1].strip().strip('"').strip("'")
            if val != default:
                break
    except Exception:
        pass
    _ENV_FALLBACK[name] = val
    return val


def _mode() -> str:
    return str(_env_with_dotenv_fallback("FWD_LABEL_TABLE", "off") or "off").strip().lower()


def _engine():
    from sqlalchemy import create_engine

    return create_engine(
        os.getenv("ANALYTICS_DB_URL",
                  "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_analytics")
    )


def ensure_table() -> bool:
    try:
        from sqlalchemy import text

        with _engine().begin() as c:
            for stmt in DDL.strip().split(";"):
                if stmt.strip():
                    c.execute(text(stmt))
        return True
    except Exception as exc:
        logger.debug("[DecisionLabels] 建表失败: %s", exc)
        return False


def record_decision_label(*, decision_id: int, symbol: str, action: str, direction: str = "",
                          confidence: float = 0.0, lane: str = "", tier: str = "",
                          decided_at=None, entry_price: Optional[float] = None,
                          trace_id: str = "", source: str = "live_decision") -> bool:
    """决策时固化一条标注行（幂等）。entry_price 缺席则留 NULL，后续补价时一并补上。"""
    if _mode() == "off":
        return False
    try:
        from sqlalchemy import text

        with _engine().begin() as c:
            c.execute(text("""
                INSERT INTO decision_labels
                    (decision_id, trace_id, symbol, lane, tier, action, direction, confidence,
                     decided_at, entry_price, source)
                VALUES (:did, :tid, :sym, :lane, :tier, :act, :dir, :conf, :ts, :px, :src)
                ON CONFLICT (decision_id) DO NOTHING
            """), {"did": int(decision_id), "tid": trace_id, "sym": str(symbol).upper(),
                   "lane": lane, "tier": tier, "act": action, "dir": direction,
                   "conf": float(confidence or 0.0),
                   "ts": decided_at or datetime.now(timezone.utc).replace(tzinfo=None),
                   "px": float(entry_price) if entry_price else None, "src": source})
        return True
    except Exception as exc:  # fail-open
        logger.debug("[DecisionLabels] 写入跳过: %s", exc)
        return False


def backfill_labels(horizons: tuple = (1, 3, 7), limit: int = 5000) -> Dict[str, Any]:
    """补齐各周期价格与标签（只更新仍为 NULL 的列）。"""
    if _mode() == "off":
        return {"status": "disabled", "reason": "FWD_LABEL_TABLE=off"}
    from sqlalchemy import text

    stats = {"scanned": 0, "filled": 0, "no_price": 0}
    try:
        eng = _engine()
        with eng.begin() as c:
            c.execute(text(DDL.strip().split(";")[0]))
            rows = c.execute(text("""
                SELECT decision_id, symbol, decided_at, entry_price, action, direction,
                       ret_1d IS NULL AS need1, ret_3d IS NULL AS need3, ret_7d IS NULL AS need7
                FROM decision_labels
                WHERE ret_7d IS NULL OR entry_price IS NULL
                ORDER BY decided_at DESC LIMIT :lim
            """), {"lim": int(limit)}).fetchall()
        stats["scanned"] = len(rows)
        mk = _mk_engine()
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        for r in rows:
            did, sym, decided, entry, act, direction, n1, n3, n7 = tuple(r)
            if decided is None:
                continue
            if isinstance(decided, str):
                try:
                    decided = datetime.fromisoformat(decided)
                except Exception:
                    continue
            with mk.connect() as mc:
                kr = mc.execute(text("""
                    SELECT timestamp, close_price FROM crypto_klines
                    WHERE symbol = :s AND period = '1h'
                      AND timestamp BETWEEN :lo AND :hi
                    ORDER BY timestamp ASC
                """), {"s": str(sym).upper(),
                       # [2026-10-04 修正] 窗口必须**回看 1 天**：entry_price 应取"决策时刻或之前最近
                       # 一根"的收盘价；原先从 decided_at 起，导致新行永远取不到 entry（实测 no_price=1）、
                       # 而 entry 是所有收益率的锚 ⇒ 标注全链路失效。
                       "lo": int((decided - timedelta(days=1)).replace(tzinfo=timezone.utc).timestamp()),
                       "hi": int((decided + timedelta(days=8)).replace(tzinfo=timezone.utc).timestamp())}).fetchall()
            series = [(int(t), float(p)) for t, p in kr if p is not None]
            if not series:
                stats["no_price"] += 1
                continue
            def _at(ts):
                best = None
                for t, p in series:
                    if t <= ts:
                        best = p
                    else:
                        break
                return best
            sign = 1 if str(act).lower() in ("buy", "long") else (-1 if str(act).lower() in ("sell", "short") else 0)
            base = float(entry) if entry else _at(int(decided.replace(tzinfo=timezone.utc).timestamp()))
            if not base or base <= 0:
                stats["no_price"] += 1
                continue
            upd: Dict[str, Any] = {"did": int(did), "px": base}
            for h in horizons:
                target = int((decided + timedelta(days=h)).replace(tzinfo=timezone.utc).timestamp())
                if target > int(now_utc.replace(tzinfo=timezone.utc).timestamp()):
                    continue
                px = _at(target)
                if px is None:
                    continue
                ret = (px - base) / base
                upd[f"px{h}"] = px
                upd[f"ret{h}"] = ret
                upd[f"lab{h}"] = 1 if (ret * (sign or 1)) > 0 else 0
            sets = ["entry_price = COALESCE(entry_price, :px)"]
            for h in horizons:
                if f"ret{h}" in upd:
                    sets.append(f"price_{h}d = :px{h}, ret_{h}d = :ret{h}, label_{h}d = :lab{h}")
            sets.append("filled_at = now()")
            with eng.begin() as c:
                c.execute(text(f"UPDATE decision_labels SET {', '.join(sets)} WHERE decision_id = :did"), upd)
            stats["filled"] += 1
    except Exception as exc:
        logger.debug("[DecisionLabels] 补价失败: %s", exc)
        stats["error"] = str(exc)[:200]
    return stats


def _mk_engine():
    from sqlalchemy import create_engine

    return create_engine(
        os.getenv("MARKET_DB_URL",
                  "postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
    )


def table_stats() -> Dict[str, Any]:
    try:
        from sqlalchemy import text

        with _engine().connect() as c:
            row = c.execute(text("""
                SELECT count(*), count(entry_price), count(ret_1d), count(ret_3d), count(ret_7d)
                FROM decision_labels
            """)).fetchone()
        return {"rows": int(row[0]), "with_entry": int(row[1]), "ret_1d": int(row[2]),
                "ret_3d": int(row[3]), "ret_7d": int(row[4]), "mode": _mode()}
    except Exception:
        return {"rows": 0, "mode": _mode(), "note": "table not created yet"}


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)
    print(json.dumps({"mode": _mode(), "ensure": ensure_table(), "stats": table_stats()},
                     ensure_ascii=False))
