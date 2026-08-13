"""中长线方向一致性 + 开/拒仓漏斗审计（v6 M6 + P0）。

每笔决策追加一行 JSONL：
  - outcome=opened：成交开仓（方向一致性字段）
  - outcome=skip / open_attempt：拒仓或尝试开仓（漏斗 KPI）

文件默认：data/midlong_direction_audit.jsonl
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_DEFAULT_PATH = os.path.normpath(
    os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "data", "midlong_direction_audit.jsonl"
    )
)


def _path() -> str:
    return os.getenv("MIDLONG_DIRECTION_AUDIT_PATH", _DEFAULT_PATH)


def _write_row(row: Dict[str, Any]) -> Dict[str, Any]:
    try:
        path = _path()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with _LOCK:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.debug("[MidLongAudit] write skip: %s", exc)
    return row


def record_decision_audit(
    *,
    outcome: str,
    stage: str,
    symbol: str,
    reason: str = "",
    session_id: str = "",
    tier: str = "",
    source: str = "",
    authority: str = "",
    action: str = "",
    hub_action: str = "",
    direction: str = "",
    score: Any = None,
    regime: str = "",
    mode: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """记录中长线开/拒仓漏斗一行（compact JSONL）。

    outcome: skip | open_attempt | opened
    stage:   trend | hub | gate | writer | exec
    """
    out = str(outcome or "skip").strip().lower()
    if out not in ("skip", "open_attempt", "opened"):
        out = "skip"
    stg = str(stage or "exec").strip().lower() or "exec"
    sym = str(symbol or "").upper()
    row: Dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "epoch": time.time(),
        "outcome": out,
        "stage": stg,
        "symbol": sym,
        "session_id": str(session_id or "")[:32],
        "tier": str(tier or "").strip().lower(),
        "source": str(source or "").strip().lower(),
        "authority": str(authority or "").strip().lower(),
        "action": str(action or "").strip().lower(),
        "hub_action": str(hub_action or "").strip().upper(),
        "dir": str(direction or "").strip().lower(),
        "reason": str(reason or "")[:160],
        "regime": str(regime or "").strip().lower(),
        "mode": str(mode or ""),
    }
    if score is not None:
        try:
            # 保留小数：adj=0.54 被 int() 会变成 0，审计失真
            fv = float(score)
            row["score"] = round(fv, 4) if abs(fv - int(fv)) > 1e-9 else int(fv)
        except (TypeError, ValueError):
            pass
    if isinstance(extra, dict) and extra:
        row["extra"] = extra

    _write_row(row)
    if out == "skip":
        logger.info(
            "[MidLongAudit] skip stage=%s symbol=%s reason=%s hub=%s score=%s",
            stg, sym, row["reason"] or "-", row.get("hub_action") or "-",
            row.get("score", "-"),
        )
    elif out == "open_attempt":
        logger.info(
            "[MidLongAudit] open_attempt stage=%s symbol=%s action=%s hub=%s",
            stg, sym, row.get("action") or "-", row.get("hub_action") or "-",
        )
    return row


def record_open_audit(
    *,
    symbol: str,
    fill_dir: str,
    thesis_dir: str = "",
    hub_dir: str = "",
    sl_source: str = "",
    mode: str = "",
    dir_src: str = "",
    authority: str = "",
    session_id: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """记录一笔中长线开仓的方向链路审计（兼容旧字段 + outcome=opened）。"""
    fill = str(fill_dir or "").strip().lower()
    if fill in ("buy", "long"):
        fill_n = "long"
    elif fill in ("sell", "short"):
        fill_n = "short"
    else:
        fill_n = fill or "unknown"

    thesis_n = str(thesis_dir or "").strip().lower() or ""
    hub_n = str(hub_dir or "").strip().lower() or ""
    # 优先比 thesis；无 thesis 时比 hub
    ref = thesis_n if thesis_n in ("long", "short") else hub_n
    consistent = None
    if ref in ("long", "short") and fill_n in ("long", "short"):
        consistent = ref == fill_n

    row: Dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "symbol": str(symbol or "").upper(),
        "thesis_dir": thesis_n,
        "hub_dir": hub_n,
        "fill_dir": fill_n,
        "sl_source": str(sl_source or ""),
        "mode": str(mode or ""),
        "dir_src": str(dir_src or ""),
        "authority": str(authority or ""),
        "session_id": str(session_id or "")[:32],
        "consistent": consistent,
        "epoch": time.time(),
        # P0：统一漏斗字段
        "outcome": "opened",
        "stage": "exec",
        "dir": fill_n,
        "action": "buy" if fill_n == "long" else ("sell" if fill_n == "short" else ""),
        "reason": "filled",
    }
    if isinstance(extra, dict) and extra:
        row["extra"] = extra

    _write_row(row)
    logger.info(
        "[MidLongAudit] open symbol=%s thesis=%s hub=%s fill=%s "
        "sl_source=%s mode=%s dir_src=%s consistent=%s",
        row["symbol"], thesis_n or "-", hub_n or "-", fill_n,
        row["sl_source"] or "-", row["mode"] or "-", row["dir_src"] or "-",
        consistent,
    )
    return row


def summarize_consistency(lookback_hours: float = 48.0) -> Dict[str, Any]:
    """统计近 N 小时方向一致率（验收用）。"""
    path = _path()
    out: Dict[str, Any] = {
        "path": path,
        "n": 0,
        "comparable": 0,
        "consistent": 0,
        "rate": None,
        "flips": 0,
    }
    if not os.path.isfile(path):
        return out
    cutoff = time.time() - float(lookback_hours) * 3600.0
    flips = 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if float(row.get("epoch") or 0) < cutoff:
                    continue
                # 只统计有方向一致性字段的开仓行
                if row.get("consistent") is None and row.get("outcome") not in (None, "opened"):
                    continue
                if "consistent" not in row and row.get("outcome") != "opened":
                    continue
                out["n"] += 1
                c = row.get("consistent")
                if c is None:
                    continue
                out["comparable"] += 1
                if c:
                    out["consistent"] += 1
                else:
                    flips += 1
        out["flips"] = flips
        if out["comparable"] > 0:
            out["rate"] = round(out["consistent"] / out["comparable"], 4)
    except Exception as exc:
        out["error"] = str(exc)
    return out


def summarize_decision_funnel(lookback_hours: float = 48.0) -> Dict[str, Any]:
    """统计近 N 小时开/拒仓漏斗（按 outcome / stage / reason 前缀）。"""
    path = _path()
    out: Dict[str, Any] = {
        "path": path,
        "lookback_hours": float(lookback_hours),
        "n": 0,
        "by_outcome": {},
        "by_stage_skip": {},
        "top_skip_reasons": [],
        "opened": 0,
        "open_attempts": 0,
        "skips": 0,
    }
    if not os.path.isfile(path):
        return out
    cutoff = time.time() - float(lookback_hours) * 3600.0
    by_outcome: Counter = Counter()
    by_stage_skip: Counter = Counter()
    reason_counter: Counter = Counter()
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if float(row.get("epoch") or 0) < cutoff:
                    continue
                # 兼容旧 opened 行（无 outcome 字段）
                outcome = str(row.get("outcome") or "").strip().lower()
                if not outcome:
                    if "fill_dir" in row or "consistent" in row:
                        outcome = "opened"
                    else:
                        continue
                out["n"] += 1
                by_outcome[outcome] += 1
                if outcome == "skip":
                    stg = str(row.get("stage") or "unknown")
                    by_stage_skip[stg] += 1
                    reason = str(row.get("reason") or "unknown")
                    # 前缀聚合：score_low(30<32) → score_low
                    prefix = reason.split("(", 1)[0].split(":", 1)[0].strip() or reason
                    reason_counter[prefix[:80]] += 1
        out["by_outcome"] = dict(by_outcome)
        out["by_stage_skip"] = dict(by_stage_skip)
        out["opened"] = int(by_outcome.get("opened", 0))
        out["open_attempts"] = int(by_outcome.get("open_attempt", 0))
        out["skips"] = int(by_outcome.get("skip", 0))
        out["top_skip_reasons"] = [
            {"reason": k, "count": v}
            for k, v in reason_counter.most_common(12)
        ]
    except Exception as exc:
        out["error"] = str(exc)
    return out


def count_nibble_probes_today(session_id: str = "") -> int:
    """统计今日已发出的 NIBBLE 探针（dir_src/reason 含 nibble_probe）。"""
    path = _path()
    if not os.path.isfile(path):
        return 0
    # UTC 日界
    now = datetime.now(timezone.utc)
    day0 = datetime(now.year, now.month, now.day, tzinfo=timezone.utc).timestamp()
    sid = str(session_id or "")[:32]
    n = 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if float(row.get("epoch") or 0) < day0:
                    continue
                if sid and str(row.get("session_id") or "")[:32] != sid:
                    continue
                blob = " ".join(
                    [
                        str(row.get("reason") or ""),
                        str(row.get("dir_src") or ""),
                        str((row.get("extra") or {}).get("dir_src") or "")
                        if isinstance(row.get("extra"), dict)
                        else "",
                    ]
                ).lower()
                if "nibble_probe" not in blob:
                    continue
                if str(row.get("outcome") or "").lower() in (
                    "open_attempt", "opened", "skip"
                ):
                    # 只计真正尝试/成交；skip 里标注 probe 的也计配额防刷
                    if str(row.get("outcome") or "").lower() in ("open_attempt", "opened"):
                        n += 1
                    elif "nibble_probe_applied" in blob:
                        n += 1
    except Exception:
        return n
    return n
