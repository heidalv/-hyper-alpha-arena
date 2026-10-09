# -*- coding: utf-8 -*-
"""影子编排 —— 周期运行/节流/落盘/命中评估/状态。

模式（config.mode()）：
    off    ：maybe_shadow_hook 直接返回；run_cycle 拒绝执行
    shadow ：默认。全链计算与落盘，不影响任何注入决策
    fusion ：晋级档。额外提供 hybrid 分数给选币消费（消费位在 auto_coin_selector，
             权重封顶 config.fusion_weight_cap()）
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from backend.services.hybrid_scoring import channel_a, channel_b, config, evidence, fusion, kpanel, ltr

logger = logging.getLogger(__name__)

_hook_lock = threading.Lock()
_last_run_ts: float = 0.0
_running = threading.Event()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def maybe_shadow_hook(symbols: Optional[List[str]] = None) -> bool:
    """选币循环钩子：节流 + 后台线程执行，任何异常静默（零影响承诺）。"""
    if config.mode() == "off":
        return False
    global _last_run_ts
    with _hook_lock:
        if time.time() - _last_run_ts < config.hook_interval_sec():
            return False
        if _running.is_set():
            return False
        _last_run_ts = time.time()
    t = threading.Thread(target=_safe_cycle, args=(symbols,), daemon=True, name="hybrid-score-shadow")
    t.start()
    return True


def _safe_cycle(symbols: Optional[List[str]]):
    try:
        run_cycle(symbols=symbols)
    except Exception as e:  # noqa: BLE001
        logger.warning("[HybridScore] 影子轮失败（不影响选币）: %s", str(e)[:200])


def run_cycle(symbols: Optional[List[str]] = None, *, force: bool = False,
              llm_enabled: Optional[bool] = None) -> Dict[str, object]:
    """一轮完整影子计算。返回 latest 结构（同时落盘）。"""
    if config.mode() == "off" and not force:
        return {"ok": False, "error": "mode=off（force=True 可强制）"}
    if _running.is_set() and not force:
        return {"ok": False, "error": "busy"}
    _running.set()
    try:
        t0 = time.time()
        syms = [kpanel.norm_sym(s) for s in (symbols or []) if s]
        if not syms:
            syms = kpanel.top_liquid_symbols(config.max_symbols())
        syms = syms[: config.max_symbols()]

        a_rows = channel_a.run(syms, period=config.PANEL_PERIOD)
        if not a_rows:
            return {"ok": False, "error": "channel_a_empty"}

        packs = evidence.build_packs(a_rows)
        use_llm = config.channel_b_enabled() if llm_enabled is None else llm_enabled
        b_rows = None
        if use_llm:
            b_rows = channel_b.run(packs)

        ic_stats = _load_ic_stats()
        thesis_map = evidence._thesis_map()
        # [流B 2026-09-17] thesis 数值化：分析师观点作为第三通道（TradingAgents 范式）
        # tier→分数：long=0.9, mid=0.8, short=0.6（观点强度的先验排序）
        _TIER_SCORE = {"long": 0.9, "mid": 0.8, "short": 0.6}
        thesis_scores = {sym: _TIER_SCORE.get(str(t.get("tier") or ""), 0.6)
                         for sym, t in thesis_map.items()} or None
        fused = fusion.fuse(a_rows, b_rows, ic_stats, thesis_scores=thesis_scores)

        thesis_map = evidence._thesis_map()
        latest = {
            "ok": True, "ts": time.time(), "iso": _now_iso(),
            "mode": config.mode(), "period": config.PANEL_PERIOD,
            "n_symbols": len(fused),
            "model_meta": _model_summary(),
            "fusion": {"w_a": fused and list(fused.values())[0].get("w_a"),
                       "w_b": fused and list(fused.values())[0].get("w_b"),
                       "lambda": fused and list(fused.values())[0].get("lambda"),
                       "regime": fused and list(fused.values())[0].get("regime"),
                       "channel_b_present": b_rows is not None},
            "channel_a": {s: {"score": r["score"], "rank": r["rank"], "backend": r["backend"]}
                          for s, r in a_rows.items()},
            "channel_b": dict(b_rows or {}),
            "fused": dict(sorted(fused.items(), key=lambda kv: kv[1].get("rank", 999))),
            "elapsed_sec": round(time.time() - t0, 2),
        }
        latest["channel_b"] = dict(b_rows or {})
        config.latest_path().write_text(json.dumps(latest, ensure_ascii=False, indent=2), encoding="utf-8")
        _append_score_log(fused, a_rows, thesis_map)
        logger.info("[HybridScore] 影子轮完成 n=%d arm=%s regime=%s %.1fs",
                    len(fused), list(fused.values())[0]["arm"] if fused else "-",
                    latest["fusion"]["regime"], latest["elapsed_sec"])
        return latest
    finally:
        _running.clear()


def _append_score_log(fused: Dict, a_rows: Dict, thesis_map: Dict):
    """逐币追加 jsonl（消融三臂同轮在册：A0=channel_a.score, A2=llm.score, A3=hybrid）。"""
    try:
        with config.score_log_path().open("a", encoding="utf-8") as fh:
            for sym, r in fused.items():
                th = thesis_map.get(sym) or {}
                entry = {
                    "ts": time.time(), "iso": _now_iso(), "symbol": sym,
                    "arm_fields": {
                        "A0": float((a_rows.get(sym) or {}).get("score") or 0.0),
                        "A1": r.get("a1"),
                        "A2": (r.get("llm") or {}).get("score"),
                        "A3": float(r.get("hybrid") or 0.0),
                    },
                    "thesis": {"present": bool(th), "tier": th.get("tier")},
                    "regime": r.get("regime"), "backend": r.get("backend"),
                    "outcome": None,  # evaluate_hits 回填
                }
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001
        logger.warning("[HybridScore] score_log 追加失败: %s", str(e)[:120])


def _load_ic_stats() -> Dict:
    try:
        return json.loads(config.ic_stats_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def evaluate_hits(max_entries: int = 400) -> Dict[str, object]:
    """对 ≥24h 未回填的日志条目：用 1h K线计算 24h 前瞻收益 → 回填 + 更新 ic_stats。"""
    path = config.score_log_path()
    if not path.exists():
        return {"ok": False, "error": "no_log"}
    lines = path.read_text(encoding="utf-8").splitlines()
    now = time.time()
    out_lines, updated, evaluated = [], 0, 0
    per_date: Dict[str, Dict[str, list]] = {}
    pending_syms: Dict[str, float] = {}
    for line in lines:
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("outcome") is None and now - float(e.get("ts") or 0) >= 24 * 3600:
            pending_syms[e.get("symbol")] = float(e.get("ts") or 0)
            updated += 1
        out_lines.append(line)
    # 逐币取 1h K线（限流：只查 pending 的）
    ret_cache: Dict[str, Optional[float]] = {}
    for sym, ts in pending_syms.items():
        try:
            df = kpanel.load_klines(sym, period="1h", bars=24 * 14)  # 覆盖评估窗
            if df is None or len(df) < 2:
                ret_cache[sym] = None
                continue
            df = df[df.index.astype("int64") // 10**9 >= int(ts)]
            if len(df) < 2:
                ret_cache[sym] = None
                continue
            t0_close = float(df["close"].iloc[0])
            tgt = df.index[0] + timedelta(hours=24)
            window = df[df.index <= tgt]
            t1_close = float(window["close"].iloc[-1]) if len(window) >= 2 else None
            ret_cache[sym] = (t1_close / t0_close - 1.0) if (t1_close and t0_close) else None
        except Exception:
            ret_cache[sym] = None
    # 回填
    for i, line in enumerate(out_lines):
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("outcome") is None and now - float(e.get("ts") or 0) >= 24 * 3600:
            r = ret_cache.get(e.get("symbol"))
            if r is not None:
                e["outcome"] = {"ret_24h": round(r, 6), "evaluated_at": now}
                evaluated += 1
                date_key = e["iso"][:10]
                d = per_date.setdefault(date_key, {"A0": [], "A1": [], "A2": [], "A3": [], "y": []})
                # [F326 2026-09-18 复查修复] 原为 ("A0","A2","A3") —— **漏掉 thesis 臂 A1**，
                # 而上面 per_date 已初始化 "A1": []。后果：`ic_thesis_mixed` 恒 None、
                # `per_arm_days.A1` 恒 0 ⇒ "thesis 第三通道"永远没有 IC 证据（通道形同虚设）。
                for arm in ("A0", "A1", "A2", "A3"):
                    v = (e.get("arm_fields") or {}).get(arm)
                    if v is not None:
                        d[arm].append(float(v))
                d["y"].append(r)
            out_lines[i] = json.dumps(e, ensure_ascii=False)
    path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    _update_ic_stats(per_date)
    return {"ok": True, "pending": updated, "evaluated": evaluated}


def _update_ic_stats(per_date: Dict[str, Dict[str, list]]):
    """滚动 IC：对已回填全量日志重算三臂逐日 RankIC 均值 + 通道差 std。"""
    try:
        path = config.score_log_path()
        days: Dict[str, Dict[str, list]] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except Exception:
                continue
            o = e.get("outcome") or {}
            if o.get("ret_24h") is None:
                continue
            date_key = e["iso"][:10]
            d = days.setdefault(date_key, {"A0": [], "A1": [], "A2": [], "A3": [], "y": []})
            # [F326 2026-09-18 复查修复] 同上：加入 thesis 臂 A1（否则周报读到的
            # ic_thesis_mixed 永远为 null，即使 A1 已有真实打分）。
            for arm in ("A0", "A1", "A2", "A3"):
                v = (e.get("arm_fields") or {}).get(arm)
                if v is not None:
                    d[arm].append(float(v))
            d["y"].append(float(o["ret_24h"]))
        import numpy as np

        def _ric(xs, ys):
            if len(xs) < 5:
                return None
            rx = np.argsort(np.argsort(xs)) + 1.0
            ry = np.argsort(np.argsort(ys)) + 1.0
            rx = (rx - rx.mean()) / (rx.std() + 1e-12)
            ry = (ry - ry.mean()) / (ry.std() + 1e-12)
            return float((rx * ry).mean())

        ics = {"A0": [], "A1": [], "A2": [], "A3": []}
        for d in days.values():
            ys = d["y"]
            for arm in ics:
                ic = _ric(d[arm], ys)
                if ic is not None:
                    ics[arm].append(ic)
        stats = {
            "updated_at": _now_iso(),
            "days": len(days),
            "ic_a": float(np.mean(ics["A0"])) if ics["A0"] else None,
            "ic_b": float(np.mean(ics["A2"])) if ics["A2"] else None,
            "ic_fused": float(np.mean(ics["A3"])) if ics["A3"] else None,
            "ic_thesis_mixed": float(np.mean(ics["A1"])) if ics["A1"] else None,
            "std_diff": float(np.std([a - b for a, b in zip(ics["A0"], ics["A2"])
                                      if a is not None and b is not None])) if ics["A0"] and ics["A2"] else 0.25,
            "per_arm_days": {k: len(v) for k, v in ics.items()},
        }
        config.ic_stats_path().write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.warning("[HybridScore] ic_stats 更新失败: %s", str(e)[:120])


def _model_summary() -> Optional[Dict]:
    meta = ltr.load_meta()
    if not meta:
        return None
    m = meta.get("metrics") or {}
    return {"trained_at": m.get("trained_at"), "test_rank_ic": m.get("test_rank_ic"),
            "valid_rank_ic": m.get("valid_rank_ic"), "n_rows": m.get("n_rows"),
            "n_symbols": m.get("n_symbols"), "fresh": ltr._model_fresh()}


def status() -> Dict[str, object]:
    latest = {}
    try:
        latest = json.loads(config.latest_path().read_text(encoding="utf-8"))
    except Exception:
        pass
    return {
        "mode": config.mode(),
        "channel_b_enabled": config.channel_b_enabled(),
        "model": _model_summary(),
        "ic_stats": _load_ic_stats(),
        "latest_ts": latest.get("ts"),
        "latest_top10": list((latest.get("fused") or {}).items())[:10],
        "ic_entry_threshold": config.ic_entry_threshold(),
    }


def fusion_blend(symbol: str, base_score: float) -> float:
    """fusion 模式消费位：composite 与 hybrid 百分位混合（w 封顶；shadow/off 原样返回）。

    晋级前置：mode=fusion 且 latest 新鲜（<2h）且融合臂滚动 IC ≥ 阈值；任一不满足原样返回。
    """
    try:
        if config.mode() != "fusion":
            return float(base_score)
        latest = json.loads(config.latest_path().read_text(encoding="utf-8"))
        if time.time() - float(latest.get("ts") or 0) > 2 * 3600:
            return float(base_score)
        fused = latest.get("fused") or {}
        row = (fused.get(str(symbol).upper()) or {})
        if not row:
            return float(base_score)
        ic = _load_ic_stats()
        if float(ic.get("ic_fused") or 0.0) < config.ic_entry_threshold():
            return float(base_score)
        w = config.fusion_weight_cap()
        return float(np_clip((1.0 - w) * float(base_score) + w * float(row.get("hybrid") or 0.5), 0.0, 1.0))
    except Exception:
        return float(base_score)


def np_clip(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else (hi if x > hi else x)
