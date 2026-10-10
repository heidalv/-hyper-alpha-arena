# -*- coding: utf-8 -*-
"""[2026-10-10 主脑接入高频学习] ping-pong 往返 → 统一学习入口（一个入口）。

病根（用户 2026-10-10）：整个项目的学习是几套并排转的 —— 中线/长线平仓会进
`unified_learning.process_outcome`，而高频（`mm_asterdex` 车道的 ping-pong 来回）
自己记 `flow_roundtrip_log.jsonl`、自己算 `pp_symbol_stats.json`，**从不进主脑**。
于是"三条车道共用一颗脑"在数据上不成立。

本模块是那**唯一缺失的入口**：把已经落库的平仓腿读出来，按往返汇成一笔
`TradeOutcome` 交给主脑，不新开第二条学习链。

## 为什么读 `lane_ledger` 而不是 `flow_roundtrip_log.jsonl`
本仓纪律：`lane_ledger` 是**车道级唯一事实源**（`lane_ledger.py` 模块头、
`runner.py:5073/6287` 都写着这一条）。所有学习产物（选币表、桶表、出口归因、
每腿净）都是从它派生的。若高频单独从 JSONL 学，就会出现"两本账"——正是要消灭的
那种分裂。因此这里只读账本。

平仓腿在账本里天然可识别：`meta_json->>'rt_bp' IS NOT NULL`
（`runner.py:6435` 只在平仓腿写 `rt_bp`）⇒ 没有"按仓位编号把十小时旧账配成一笔"
那套启发式，也不需要去重表。

## 幂等
游标 = `(ts, id)` 双键（`data/pp_brain_cursor.json`）。`id` 是账本 BIGSERIAL，
同秒多笔不会互相吃掉。单笔失败只重试该行，最多 `MAX_ATTEMPTS` 次后跳过并告警，
**绝不因为一行毒数据卡死整条入口**。

## 车道身份（与中/长线隔离的唯一手段）
`strategy_id="mm_asterdex_pp"`、`tier="hft"`、`trade_nature="scalp"`、
`persist_trade=False`：
  · `tier="hft"` ⇒ 主脑的 `thesis_store.find_latest(sym,"hft")` 恒空 ⇒ **不挂中线/
    长线论题**（用户硬要求：同一个币的短来回不能改写中线记忆）；
  · `tier="hft"` 也不在短/中线熔断器的白名单里 ⇒ 不污染那两道交易侧硬闸
    （由 `unified_learning._is_hft_lane` 的隔离闸显式保证，见那里注释）；
  · `persist_trade=False` ⇒ 不写 `strategy_trades`，不给中线统计加笔数。

## 开关与回滚
`MM_PP_TO_BRAIN=0` ⇒ 本模块整体空转（默认 1；非法值 fail-closed = 空转）。
`MM_PP_BRAIN_SINCE`（epoch 秒或 ISO）可把入口起点钉到任意时刻；缺省用
`data/pp_era_start_ts.txt`（当前 PP 时代的起点，与选币表同源）。
`MM_PP_BRAIN_LIMIT`（默认 200）每次 tick 最多补多少笔 —— 首次开启补历史时**有界**。
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

LANE_ID = "mm_asterdex"
STRATEGY_ID = "mm_asterdex_pp"
TIER = "hft"
NATURE = "scalp"

CURSOR_FILE = "data/pp_brain_cursor.json"
ERA_FILE = "data/pp_era_start_ts.txt"

DEFAULT_LIMIT = 200
DEFAULT_MAX_ATTEMPTS = 3
# [2026-10-10 现场修复] 单次 tick 的**墙钟预算**（秒）。
#
# 实测事故：`learning_loop_hft_brain` 注册为 60s 间隔，但一次 tick 跑了 **~15 分钟**
# （200 笔 × `process_outcome` 的慢下游：MLTO/复盘官/learning_registry 多后端），
# 于是 APScheduler 按 `max_instances=1` 把它后面的每一次触发全部
# `skipped: maximum number of running instances reached` —— 入口看起来"注册了但不动"，
# 而且期间一直持着 DB 连接（`[DB LeakGuard] origin: _tick_hft_brain`）。
#
# 现在：一个 tick 最多跑 `MM_PP_BRAIN_BUDGET_SEC`（默认 45s），到点就收工并保存游标
# （**每笔成功即推进**，所以收工不丢进度），下一次 tick 接着补。
DEFAULT_BUDGET_SEC = 45.0
# 首次启动、还没有游标时的兜底回溯窗口（`pp_era_start_ts.txt` 缺失时用）
FALLBACK_LOOKBACK_SEC = 24 * 3600.0


def _root() -> Path:
    """仓库根：backend/services/market_maker/pp_brain.py → parents[3]。"""
    return Path(__file__).resolve().parents[3]


def _flag_on(name: str, default: str = "1") -> bool:
    """开关：缺失用 default，非法值 fail-closed（与仓内开关约定一致）。"""
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        raw = default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def enabled() -> bool:
    return _flag_on("MM_PP_TO_BRAIN", "1")


def _limit() -> int:
    try:
        return max(1, int(float(os.getenv("MM_PP_BRAIN_LIMIT", "") or DEFAULT_LIMIT)))
    except (TypeError, ValueError):
        return DEFAULT_LIMIT


def _max_attempts() -> int:
    try:
        return max(1, int(float(os.getenv("MM_PP_BRAIN_MAX_ATTEMPTS", "")
                               or DEFAULT_MAX_ATTEMPTS)))
    except (TypeError, ValueError):
        return DEFAULT_MAX_ATTEMPTS


def _budget_sec() -> float:
    """单个 tick 的墙钟预算（秒）。0 → 不限（仅调试用，生产别这么设）。"""
    try:
        raw = os.getenv("MM_PP_BRAIN_BUDGET_SEC")
        v = float(raw) if raw not in (None, "") else DEFAULT_BUDGET_SEC
    except (TypeError, ValueError):
        v = DEFAULT_BUDGET_SEC
    return max(0.0, v)


def era_start_ts(root: Optional[Path] = None) -> float:
    """当前 PP 时代的起点（epoch 秒）。与 `scripts/pp_learn_tables.py` 同源。

    `MM_PP_BRAIN_SINCE` 优先（epoch 秒或 ISO 时间串）；否则读 era 文件；
    都没有则回落到"现在往前 24h"。
    """
    raw = (os.getenv("MM_PP_BRAIN_SINCE") or "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
        try:
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=None)
            return dt.timestamp()
        except Exception:  # noqa: BLE001
            logger.warning("[pp-brain] MM_PP_BRAIN_SINCE 无法解析(%s)，回落 era 文件", raw)
    try:
        p = (root or _root()) / ERA_FILE
        return float(p.read_text(encoding="utf-8").strip() or 0.0)
    except Exception:  # noqa: BLE001
        return time.time() - FALLBACK_LOOKBACK_SEC


# ══════════════════════════════════════════════════════════════════════
#  游标
# ══════════════════════════════════════════════════════════════════════

def _empty_cursor(root: Path) -> Dict[str, Any]:
    return {
        "cursor_ts": float(era_start_ts(root)),
        "cursor_id": 0,
        "ingested": 0,
        "failed": 0,
        "skipped": 0,
        # 重试记账：游标停在失败行上，用 (attempts_id, attempts) 记住试了几次
        "attempts_id": -1,
        "attempts": 0,
        "applied_at": 0.0,
    }


def load_cursor(root: Optional[Path] = None) -> Dict[str, Any]:
    """读游标；文件缺失/损坏 ⇒ era 起点的空游标（fail-open 到"从头补"）。"""
    r = root or _root()
    base = _empty_cursor(r)
    try:
        doc = json.loads((r / CURSOR_FILE).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return base
    if not isinstance(doc, dict):
        return base
    for key in ("cursor_ts", "ingested", "failed", "skipped", "applied_at"):
        if key in doc:
            try:
                base[key] = float(doc[key])
            except (TypeError, ValueError):
                pass
    for key in ("cursor_id", "attempts_id", "attempts"):
        if key in doc:
            try:
                base[key] = int(doc[key])
            except (TypeError, ValueError):
                pass
    return base


def save_cursor(cur: Dict[str, Any], root: Optional[Path] = None) -> bool:
    r = root or _root()
    try:
        p = r / CURSOR_FILE
        p.parent.mkdir(parents=True, exist_ok=True)
        doc = dict(cur)
        doc["applied_at"] = time.time()
        p.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("[pp-brain] 游标写入失败: %s", e)
        return False


# ══════════════════════════════════════════════════════════════════════
#  账本 → TradeOutcome
# ══════════════════════════════════════════════════════════════════════

def _bare(symbol: str) -> str:
    s = str(symbol or "").upper()
    return s[:-4] if s.endswith("USDT") else s


def _f(val: Any, default: float = 0.0) -> float:
    try:
        out = float(val)
    except (TypeError, ValueError):
        return default
    if out != out or out in (float("inf"), float("-inf")):  # NaN / inf
        return default
    return out


def to_outcome(row: Dict[str, Any]):
    """账本平仓腿 → `TradeOutcome`（纯函数，可单测；无 IO）。

    只接受平仓腿（`meta.rt_bp` 非空）；开仓腿返回 None。
    """
    from backend.services.unified_learning_service import TradeOutcome

    meta = row.get("meta")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:  # noqa: BLE001
            meta = {}
    if not isinstance(meta, dict):
        meta = {}
    rt_bp = meta.get("rt_bp")
    if rt_bp is None:
        return None
    rt_bp = _f(rt_bp, float("nan"))
    if rt_bp != rt_bp:
        return None

    symbol = _bare(row.get("symbol"))
    exit_side = str(meta.get("side") or "").lower()
    # 平仓腿的对手方向 = 我们的持仓方向
    side = "long" if exit_side == "sell" else ("short" if exit_side == "buy" else "")
    qty = abs(_f(meta.get("qty")))
    fill_px = _f(meta.get("fill_px"))
    entry_px = _f(meta.get("rt_entry_px")) or fill_px
    notional = _f(row.get("notional")) or abs(qty * fill_px)
    fee_usd = _f(meta.get("fee_usd"))
    # 账本净口径 = 价格盈亏 − 吃单费；`rt_bp` 只含价格项（pingpong._close_roundtrip
    # 在有 fee_bp 时已扣一趟费），这里统一再用 fee_usd 校准一次净额。
    pnl = notional * rt_bp / 1e4 - fee_usd
    pnl_pct = (pnl / notional) if notional > 0 else 0.0
    hold_sec = _f(meta.get("hold_sec_true"))
    exit_path = str(meta.get("exit_path") or meta.get("source") or "pp_exit")
    pos_id = str(row.get("position_id") or "")

    metadata: Dict[str, Any] = {
        "close_reason": exit_path,
        "close_fee": fee_usd,
        "leverage": _f(meta.get("leverage"), 1.0) or 1.0,
        "market_type": "perp",
        "exchange": "asterdex",
        "data_source": "lane_ledger_pp",
        "pp_roundtrip_id": f"{LANE_ID}:{row.get('id')}",
        "pp_rt_bp": round(rt_bp, 4),
        "pp_position_id": pos_id,
        "pp_exit_path": exit_path,
        "pp_qpos": str(meta.get("qpos") or ""),
        "pp_hold_sec": round(hold_sec, 3),
    }
    for src_key, dst_key in (("regime", "pp_entry_regime"),
                             ("pp_arm_rel_bp", "pp_arm_rel_bp"),
                             ("book_capture_bp", "pp_book_capture_bp")):
        if meta.get(src_key) is not None:
            metadata[dst_key] = meta.get(src_key)

    return TradeOutcome(
        source="paper",
        strategy_id=STRATEGY_ID,
        template_id="",
        symbol=symbol,
        side=side,
        tier=TIER,
        trade_nature=NATURE,
        entry_price=float(entry_px or 0.0),
        exit_price=float(fill_px or 0.0),
        pnl=float(pnl),
        pnl_pct=float(pnl_pct),
        duration_seconds=int(max(0.0, hold_sec)),
        regime_at_entry=str(meta.get("regime") or "unknown"),
        regime_at_exit="unknown",
        confidence=0.6,
        metadata=metadata,
        position_size=float(abs(qty)),
        opened_at=None,
        persist_trade=False,
    )


# ══════════════════════════════════════════════════════════════════════
#  默认 driver（真库）
# ══════════════════════════════════════════════════════════════════════

FETCH_SQL = """
SELECT id, symbol, ts, position_id, notional, meta_json AS meta
  FROM lane_ledger
 WHERE lane_id = :lane
   AND event = 'fill'
   AND meta_json->>'rt_bp' IS NOT NULL
   AND (ts, id) > (:cursor_at, :cursor_id)
 ORDER BY ts, id
 LIMIT :limit
"""


class SqlDriver:
    """默认驱动：只读账本 + 走主脑 `process_outcome`。

    [2026-10-10 现场修复] **一整批共用一个 DB session**（此前每笔开一个新 session）。
    实测后果：一批 200 笔会开 200 个 session，且 `process_outcome` 的慢下游
    （MLTO / 复盘官 / learning_registry）会把连接长期占住 ⇒ `[DB LeakGuard]`
    把 origin 指向 `_tick_hft_brain`，同时 tick 跑到 15 分钟导致后续触发被
    `max_instances=1` 全部跳过。共用 session 后：连接数 1，单笔异常只 `rollback`
    本事务（`process_outcome` 自己会 commit，失败会 re-raise，这里吞掉继续下一笔）。
    """

    def __init__(self, lane_id: str = LANE_ID,
                 session_factory: Optional[Callable[[], Any]] = None,
                 processor: Optional[Callable[[Any, Any], Any]] = None) -> None:
        self.lane_id = lane_id
        self._session_factory = session_factory
        self._processor = processor
        self._db = None

    def fetch(self, cursor_ts: float, cursor_id: int, limit: int) -> List[Dict[str, Any]]:
        from datetime import timezone as _tz

        from sqlalchemy import text

        from backend.core.tenant import system_identity
        from backend.database.connection import SessionLocal

        factory = self._session_factory or SessionLocal
        # `ts` 是 TIMESTAMPTZ：必须传带时区的 datetime，直接比 epoch 秒会
        # UndefinedFunction（实测：timestamp with time zone > double precision）。
        cursor_at = datetime.fromtimestamp(float(cursor_ts), tz=_tz.utc)
        with system_identity():
            with factory() as db:
                rows = db.execute(text(FETCH_SQL), {
                    "lane": self.lane_id,
                    "cursor_at": cursor_at,
                    "cursor_id": int(cursor_id),
                    "limit": int(limit),
                }).mappings().all()
        out: List[Dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["meta"] = _as_dict(d.get("meta"))
            out.append(d)
        return out

    # ── 批级 session 生命周期（由 `_apply_rows` 调用；开不出来就退化为每笔一开）──
    def open(self) -> None:
        if self._db is not None or self._processor is not None:
            return
        try:
            from backend.database.connection import SessionLocal
            factory = self._session_factory or SessionLocal
            self._db = factory()
        except Exception as e:  # noqa: BLE001
            logger.warning("[pp-brain] 批级 session 打开失败（退化为每笔一开）: %s", e)
            self._db = None

    def close(self) -> None:
        db, self._db = self._db, None
        if db is not None:
            try:
                db.close()
            except Exception:  # noqa: BLE001
                pass

    def process(self, outcome) -> None:
        if self._processor is not None:
            self._processor(outcome)
            return
        from backend.services.unified_learning_service import unified_learning

        if self._db is not None:
            unified_learning.process_outcome(self._db, outcome)
            return
        from backend.database.connection import SessionLocal
        db = SessionLocal()
        try:
            unified_learning.process_outcome(db, outcome)
        finally:
            db.close()


def _as_dict(val: Any) -> Dict[str, Any]:
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        try:
            out = json.loads(val)
            return out if isinstance(out, dict) else {}
        except Exception:  # noqa: BLE001
            return {}
    return {}


# ══════════════════════════════════════════════════════════════════════
#  主流程
# ══════════════════════════════════════════════════════════════════════

def run_once(driver: Optional[Any] = None, *, limit: Optional[int] = None,
             root: Optional[Path] = None,
             cursor: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """把账本里尚未进主脑的 PP 平仓腿补进去。绝不抛异常。

    `driver`：任何实现 `fetch(cursor_ts, cursor_id, limit) -> list[dict]` 与
    `process(outcome) -> None` 的对象（测试/干跑注入用）；缺省走 `SqlDriver`。
    `cursor`：显式传游标（干跑用；不读写游标文件）。

    Returns: `{"ok","scanned","ingested","failed","skipped_rows","cursor"}`。
    """
    r = root or _root()
    dry = cursor is not None
    cur = cursor if dry else load_cursor(r)
    if not enabled() and not dry:
        return {"ok": True, "skipped": "MM_PP_TO_BRAIN=0",
                "scanned": 0, "ingested": 0, "failed": 0, "skipped_rows": 0}
    drv = driver or SqlDriver()
    try:
        rows = drv.fetch(cur["cursor_ts"], cur["cursor_id"], int(limit or _limit()))
    except Exception as e:  # noqa: BLE001
        logger.warning("[pp-brain] 账本读取失败（不改游标）: %s", e)
        return {"ok": False, "reason": f"fetch_failed:{e}", "scanned": 0,
                "ingested": 0, "failed": 0, "skipped_rows": 0, "cursor": cur}
    return _apply_rows(rows, cur, r, drv, persist=not dry)


def _apply_rows(rows: List[Dict[str, Any]], cur: Dict[str, Any], r: Path,
                drv: Any, *, persist: bool = True) -> Dict[str, Any]:
    scanned = len(rows)
    ingested = failed = skipped = 0
    t0 = time.time()
    budget = _budget_sec()
    exhausted = False
    if not rows:
        return {"ok": True, "scanned": 0, "ingested": 0, "failed": 0,
                "skipped_rows": 0, "cursor": cur, "elapsed_ms": 0}

    # 批级 session（可用则共用；开不出来就退化为每笔一开）
    if hasattr(drv, "open"):
        try:
            drv.open()
        except Exception:  # noqa: BLE001
            pass
    try:
        for row in rows:
            # 墙钟预算：到点就收工（游标已按每笔成功推进 ⇒ 不丢进度）
            if budget > 0 and (time.time() - t0) >= budget:
                exhausted = True
                logger.info(
                    "[pp-brain] 本次 tick 到 %ss 预算收工（已进主脑 %d 笔，剩余下次继续）",
                    int(budget), ingested)
                break
            rid = row.get("id")
            try:
                outcome = to_outcome(row)
            except Exception as e:  # noqa: BLE001
                logger.warning("[pp-brain] 往返→outcome 失败 id=%s: %s", rid, e)
                outcome = None
            if outcome is None:
                # 开仓腿/坏行：直接推进游标（不是待重试的错误）
                cur["cursor_ts"] = _row_ts(row)
                cur["cursor_id"] = int(rid or 0)
                skipped += 1
                continue
            # 同一行的重试计数：游标停在失败行上，下次 tick 还会取到它
            attempts = int(cur.get("attempts") or 0)
            if int(cur.get("attempts_id") or -1) != int(rid or -1):
                attempts = 0
            try:
                drv.process(outcome)
            except Exception as e:  # noqa: BLE001
                failed += 1
                # 单笔失败必须回滚本事务，否则 session 进入 PendingRollback
                # 状态、后续全部连锁失败（本仓多次踩过：一笔毒一笔整轮）
                db = getattr(drv, "_db", None)
                if db is not None:
                    try:
                        db.rollback()
                    except Exception:  # noqa: BLE001
                        pass
                attempts += 1
                cur["attempts_id"] = int(rid or -1)
                cur["attempts"] = attempts
                if attempts >= _max_attempts():
                    logger.error(
                        "[pp-brain] 单行连续 %d 次失败，跳过并推进游标（不阻塞入口）id=%s: %s",
                        attempts, rid, e)
                    cur["cursor_ts"] = _row_ts(row)
                    cur["cursor_id"] = int(rid or 0)
                    cur["attempts_id"] = -1
                    cur["attempts"] = 0
                    skipped += 1
                    continue
                logger.warning("[pp-brain] 单笔进主脑失败（保留游标，下次重试）id=%s: %s", rid, e)
                break
            ingested += 1
            cur["attempts_id"] = -1
            cur["attempts"] = 0
            cur["cursor_ts"] = _row_ts(row)
            cur["cursor_id"] = int(rid or 0)
    finally:
        if hasattr(drv, "close"):
            try:
                drv.close()
            except Exception:  # noqa: BLE001
                pass

    cur["ingested"] = int(cur.get("ingested") or 0) + ingested
    cur["failed"] = int(cur.get("failed") or 0) + failed
    cur["skipped"] = int(cur.get("skipped") or 0) + skipped
    if persist:
        save_cursor(cur, r)
    extra = {"ok": True, "scanned": scanned, "ingested": ingested, "failed": failed,
             "skipped_rows": skipped, "budget_exhausted": exhausted,
             "elapsed_ms": int((time.time() - t0) * 1000),
             "cursor": {"ts": cur["cursor_ts"], "id": cur["cursor_id"]},
             "ingested_total": cur["ingested"], "skipped_total": cur["skipped"]}
    if ingested or skipped:
        logger.info(
            "[pp-brain] 入口推进: 扫 %d / 进主脑 %d / 跳过 %d / 失败 %d / 耗时 %dms%s"
            "（累计 %d）",
            scanned, ingested, skipped, failed, extra["elapsed_ms"],
            "（预算到点收工）" if exhausted else "", cur["ingested"])
    return extra


def _row_ts(row: Dict[str, Any]) -> float:
    ts = row.get("ts")
    if isinstance(ts, datetime):
        return ts.timestamp()
    return _f(ts)


if __name__ == "__main__":  # pragma: no cover - 运维手动补扫用
    import io
    import sys

    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                      errors="replace", line_buffering=True)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    print(json.dumps(run_once(), ensure_ascii=False, indent=2))
