"""source_attribution — 来源归因 / 信用分 / 出场通道熔断持久化 / 窄 LLM 仲裁（阶段2）。

设计依据：02_详细设计 v2 §2.2/§2.3 + 03 可行性验证。所有入口 try/except 包裹，
热路径只做内存字典操作；JSON 持久化节流（默认 60s），重启后恢复。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_STATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "fusion_attribution.json",
)

_MIN_SAMPLES = 20        # 归因最低样本
_EXPECT_THRESHOLD = 0.0  # 期望值 < 0 → shadow


def normalize_reason(reason: str) -> str:
    """把带叙述的 close_reason 归并到通道键：trend_broken: xxx → trend_broken；[no_progress] ... → [no_progress]。"""
    r = (reason or "").strip()
    if not r:
        return "?"
    if r.startswith("["):
        end = r.find("]")
        return r[: end + 1] if end > 0 else r[:24]
    if ":" in r:
        return r.split(":", 1)[0].strip()
    return r


def _record_attribution_row(*, position_id: int, src: str, nature: str, symbol: str,
                            pnl: float, fee: float, net: float, win: bool,
                            close_reason: str, tier: str) -> None:
    """[U2-2] 归因事实行写入 brain_attribution（幂等表；异常静默）。"""
    from datetime import datetime as _dt, timezone as _tz
    from backend.database.connection import SessionLocal
    db = SessionLocal()
    try:
        from sqlalchemy import text
        db.execute(text(
            "INSERT INTO brain_attribution "
            "(position_id, src, nature, symbol, pnl, fee, net, win, close_reason, tier, created_at) "
            "VALUES (:pid, :src, :nat, :sym, :pnl, :fee, :net, :win, :reason, :tier, :ts)"
        ), {
            "pid": position_id, "src": (src or "unknown")[:32], "nat": (nature or "")[:32],
            "sym": (symbol or "")[:32], "pnl": pnl, "fee": fee, "net": net,
            "win": bool(win), "reason": close_reason, "tier": tier,
            "ts": _dt.now(_tz.utc),
        })
        db.commit()
    except Exception as e:
        logger.debug("[SourceAttr] DB 写穿跳过: %s", e)
    finally:
        db.close()


class SourceAttribution:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tags: Dict[str, Dict[str, Any]] = {}          # position_id -> {source, nature, symbol}
        self._stats: Dict[str, Dict[str, Any]] = {}         # src|nature|symbol -> {n, wins, gross, fee}
        self._shadow: Dict[str, bool] = {}                  # src|nature|symbol -> shadow
        self._breaker: Dict[str, Dict[str, int]] = {}       # tier|reason -> {n, wins}
        self._breaker_shadow: Dict[str, bool] = {}
        self._last_save = 0.0
        self._loaded = False

    # ── 持久化 ──
    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            if os.path.exists(_STATE_PATH):
                with open(_STATE_PATH, "r", encoding="utf-8") as f:
                    d = json.load(f)
                with self._lock:
                    self._tags = d.get("tags", {})
                    self._stats = d.get("stats", {})
                    self._shadow = d.get("shadow", {})
                    self._breaker = d.get("breaker", {})
                    self._breaker_shadow = d.get("breaker_shadow", {})
                logger.info("[SourceAttr] 恢复状态: tags=%d stats=%d breaker=%d",
                            len(self._tags), len(self._stats), len(self._breaker))
        except Exception as e:
            logger.warning("[SourceAttr] 状态加载失败(以空态启动): %s", e)

    def _maybe_save(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_save < 60:
            return
        self._last_save = now
        try:
            os.makedirs(os.path.dirname(_STATE_PATH), exist_ok=True)
            tmp = _STATE_PATH + f".{os.getpid()}.tmp"
            with self._lock:
                payload = {
                    "ts": now,
                    "tags": dict(self._tags),
                    "stats": dict(self._stats),
                    "shadow": dict(self._shadow),
                    "breaker": dict(self._breaker),
                    "breaker_shadow": dict(self._breaker_shadow),
                }
                empty = not (self._tags or self._stats or self._breaker
                             or self._shadow or self._breaker_shadow)
            # 空覆写防护（M4 custom_factor_store 同款教训）：内存空且磁盘已有非空数据
            # → 拒绝覆盖，并把磁盘内容合并回内存（防多进程/重启窗口清空历史归因）。
            if empty and os.path.exists(_STATE_PATH):
                try:
                    with open(_STATE_PATH, "r", encoding="utf-8") as f:
                        disk = json.load(f)
                    if disk.get("tags") or disk.get("stats") or disk.get("breaker"):
                        logger.warning("[SourceAttr] 拒绝空态覆盖磁盘非空状态（多进程防护）→ 合并磁盘内容")
                        with self._lock:
                            self._tags = dict(disk.get("tags", {}) or {})
                            self._stats = dict(disk.get("stats", {}) or {})
                            self._shadow = dict(disk.get("shadow", {}) or {})
                            self._breaker = dict(disk.get("breaker", {}) or {})
                            self._breaker_shadow = dict(disk.get("breaker_shadow", {}) or {})
                        return
                except Exception:
                    pass
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(tmp, _STATE_PATH)
        except Exception as e:
            logger.debug("[SourceAttr] 状态保存失败: %s", e)

    # ── 仓位标签（入场后调用）──
    def tag_position(self, position_id: int, source: str, nature: str = "",
                     symbol: str = "", meta: Optional[Dict[str, Any]] = None) -> None:
        if not position_id:
            return
        self._ensure_loaded()
        with self._lock:
            self._tags[str(int(position_id))] = {
                "source": source or "unknown", "nature": nature or "",
                "symbol": (symbol or "").upper(), "meta": meta or {}, "ts": time.time(),
            }

    # ── 平仓归因 + 通道熔断（close 钩子）──
    def tag_meta(self, position_id: int) -> Optional[Dict[str, Any]]:
        """[U3-2a] 回查仓位标签 meta（含 thesis_id），供学习桥兜底。"""
        self._ensure_loaded()
        with self._lock:
            t = self._tags.get(str(int(position_id)))
            if not t:
                return None
            return dict(t.get("meta") or {})

    def record_close(self, position_id: int, *, pnl: float, fee: float = 0.0,
                     close_reason: str = "", tier: str = "", symbol: str = "",
                     nature: str = "", source: Optional[str] = None) -> Dict[str, Any]:
        self._ensure_loaded()
        net = float(pnl or 0) - float(fee or 0)
        win = net > 0
        with self._lock:
            tag = self._tags.get(str(int(position_id)))
            src = (source or (tag or {}).get("source") or "unknown")
            nat = nature or (tag or {}).get("nature") or ""
            sym = (symbol or (tag or {}).get("symbol") or "").upper()
            key = f"{src}|{nat}|{sym}"
            st = self._stats.setdefault(key, {"n": 0, "wins": 0, "gross": 0.0, "fee": 0.0})
            st["n"] += 1
            st["wins"] += int(win)
            st["gross"] += float(pnl or 0)
            st["fee"] += float(fee or 0)
            # 来源信用：n>=20 且净期望 <0 → shadow
            if st["n"] >= _MIN_SAMPLES and (st["gross"] - st["fee"]) / st["n"] < _EXPECT_THRESHOLD:
                self._shadow[key] = True
            elif self._shadow.get(key) and st["n"] >= _MIN_SAMPLES * 2 and (st["gross"] - st["fee"]) / st["n"] >= 0:
                self._shadow[key] = False
            # 出场通道熔断（close_reason×tier 滚动 30 笔 wr<40% → shadow）
            bkey = f"{tier or '?'}|{normalize_reason(close_reason)}"
            bst = self._breaker.setdefault(bkey, {"n": 0, "wins": 0})
            bst["n"] += 1
            bst["wins"] += int(win)
            if bst["n"] >= 30:
                wr = bst["wins"] / bst["n"]
                self._breaker_shadow[bkey] = wr < 0.40
            result = {"key": key, "n": st["n"], "net": round(st["gross"] - st["fee"], 4),
                      "shadow": bool(self._shadow.get(key)), "bkey": bkey,
                      "breaker_shadow": bool(self._breaker_shadow.get(bkey))}
        self._maybe_save()
        # [U2-2 2026-08-25] M0 写穿：归因事实落 DB（brain_attribution；失败不影响 JSON 主链路）
        try:
            _record_attribution_row(
                position_id=int(position_id), src=src, nature=nat, symbol=sym,
                pnl=float(pnl or 0), fee=float(fee or 0), net=net, win=win,
                close_reason=(close_reason or "")[:120], tier=(tier or "")[:16],
            )
        except Exception:
            pass
        return result

    # ── 查询接口 ──
    def credit(self, source: str, nature: str = "", symbol: str = "") -> float:
        self._ensure_loaded()
        with self._lock:
            if self._shadow.get(f"{source}|{nature}|{(symbol or '').upper()}"):
                return 0.0
        return 1.0

    def exit_channel_shadow(self, close_reason: str, tier: str) -> bool:
        self._ensure_loaded()
        with self._lock:
            return bool(self._breaker_shadow.get(f"{tier or '?'}|{normalize_reason(close_reason)}"))

    def snapshot(self) -> Dict[str, Any]:
        self._ensure_loaded()
        with self._lock:
            return {
                "stats": {k: dict(v) for k, v in self._stats.items()},
                "shadow": dict(self._shadow),
                "breaker": {k: dict(v) for k, v in self._breaker.items()},
                "breaker_shadow": dict(self._breaker_shadow),
            }


attribution = SourceAttribution()


# ── 窄 JSON LLM 仲裁（thesis 冲突时 1 次调用；失败返回 None → 调用方保守 hold）──
def llm_arbitrate_conflict(*, symbol: str, direction: str, thesis_dir: str,
                           thesis_conf: float, pwin: float, factor_score: float,
                           account_id: int = 0, trading_mode: str = "paper") -> Optional[bool]:
    """返回 True=放行(0.25x) / False=否决 / None=调用失败（保守 hold）。"""
    if os.getenv("FUSION_ARBITRATE_LLM", "true").strip().lower() in ("0", "false", "off"):
        return None
    try:
        from backend.services.llm_config_service import (
            get_llm_config_for_account, call_llm_api_sync,
        )
        cfg = get_llm_config_for_account(account_id, tier="quick") if account_id else None
        if not cfg:
            return None
        prompt = (
            "你是交易仲裁员。因子引擎给出一个短线信号，但长周期 LLM thesis 方向相反。"
            "请用一行 JSON 裁决是否放行（只输出 JSON）。\n"
            f"symbol={symbol} 因子方向={direction} thesis方向={thesis_dir} "
            f"thesis置信={thesis_conf:.2f} 元模型胜率={pwin:.3f} 因子分={factor_score:.0f}\n"
            'JSON 格式：{"allow": true/false, "reason": "<=30字"}。'
            "规则：证据不足默认 allow=false（宁可错过）。"
        )
        resp = call_llm_api_sync(
            cfg, messages=[{"role": "user", "content": prompt}],
            temperature=0, max_tokens=120, response_format={"type": "json_object"},
            timeout_s=8, caller="fusion_arbitrate",
        )
        content = (resp or {}).get("content") or ""
        import re as _re
        m = _re.search(r'\{\s*"allow"\s*:\s*(true|false)', content, _re.IGNORECASE)
        if not m:
            return None
        return m.group(1).lower() == "true"
    except Exception as e:
        logger.debug("[FusionArb] LLM 仲裁调用失败: %s", e)
        return None


# ── 每周 pwin 分桶验证（衰减监控；scripts/_fusion_weekly_validation.py 调用）──
def weekly_pwin_validation(days: int = 7) -> Dict[str, Any]:
    """统计最近 N 天已结算信号 pwin>=0.55 桶的胜率与净收益（OOS 衰减监控）。"""
    import psycopg
    try:
        con = psycopg.connect("host=localhost port=5432 dbname=alpha_arena user=laobao password=alpha_pass")
        cur = con.cursor()
        cur.execute("""
            SELECT COUNT(*),
                   SUM(CASE WHEN COALESCE(win,false) THEN 1 ELSE 0 END),
                   ROUND(COALESCE(SUM(net_ret),0)::numeric,6)
            FROM scalp_signal_log
            WHERE settled AND created_at >= now() - make_interval(days => %s)
              AND (features_json::json->>'meta_p_win')::float >= 0.55
        """, (days,))
        n, w, s = cur.fetchone()
        con.close()
        wr = (w or 0) / n if n else 0.0
        return {"days": days, "n": int(n or 0), "wr": round(wr, 4),
                "net": float(s or 0), "ok": n >= 100 and wr >= 0.55}
    except Exception as e:
        logger.warning("[FusionArb] 每周 pwin 验证失败: %s", e)
        return {"days": days, "n": 0, "wr": 0.0, "net": 0.0, "ok": False, "error": str(e)[:120]}
