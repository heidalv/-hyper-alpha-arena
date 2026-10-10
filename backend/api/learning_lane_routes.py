# -*- coding: utf-8 -*-
"""[2026-10-10 学习进化前端升级] 高频入口 / 车道顾问 / 知识卡片 的只读视图接口。

三块面板各自需要"看得见 + 看得懂"的数据，而它们分散在几个 JSON/JSONL 与两次只读 SQL 里。
本路由把它们收敛成三个只读端点（**不新增任何写入权限面**；写动作复用既有
`POST /api/learning/loop/trigger/{job}` 与 `POST /api/rag/reindex`）：

  GET /api/learning/lane/inlet     高频入口进度（era 起往返 / 已进主脑 / 游标 / 待补）
  GET /api/learning/lane/advisor   车道顾问（决策时间轴 + 白名单现值 + 待判/已判）
  GET /api/learning/lane/kb/cards  知识卡片文件 + 索引状态 + 按车道可见数

设计原则（与本仓 learning-evidence / ChainOverviewPanel 同款，逐条照做）：
  · **文件缺失 ⇒ `present:false`，不是 500**（断链要如实展示，不能白屏）；
  · **比率一律带样本数**；样本不足时**不显示比率**，只显示门槛与进度；
  · 每块都给出**判据**（阈值/门槛/来源表），让人能验算；
  · `include_raw=0` 默认不返回模型的原始输出（`raw_llm` 可达数 KB，只在展开时取）。
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/learning/lane", tags=["Learning Lane"])

# 与 learning_health_service / pp_brain 同款的仓库根相对路径
JOURNAL_PATH = os.path.join("data", "lane_advisor_journal.jsonl")
CURSOR_PATH = os.path.join("data", "pp_brain_cursor.json")
FLOW_PARAMS_PATH = os.path.join("data", "flow_learn_params.json")
PENDING_PATH = os.path.join("logs", "self_tuner_pending.json")
HISTORY_PATH = os.path.join("logs", "self_tuner_history.jsonl")
RUNTIME_TUNING_PATH = os.path.join("data", "runtime_tuning.json")

LANES = ("hft", "mid", "long")


def _iso(ts: Any) -> Optional[str]:
    """epoch 秒 → ISO（本地时区无关：统一 UTC，前端 `fmtTime` 自己转本地）。"""
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def _read_json(path: str) -> Optional[Any]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return None


def _read_jsonl(path: str, limit: int = 0) -> List[Dict[str, Any]]:
    """读 JSONL；坏行跳过。`limit>0` 时只返回**末尾** limit 行（大文件时不解析全量）。"""
    out: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except Exception:  # noqa: BLE001
        return out
    if limit > 0:
        lines = lines[-limit:]
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


# ══════════════════════════════════════════════════════════════════════
#  ① 高频入口
# ══════════════════════════════════════════════════════════════════════

def _ledger_counts() -> Dict[str, Any]:
    """只读 SQL：era 起的 PP 平仓腿条数 + 最新一条时间 + 最近 10 分钟腿数。

    口径与 `pp_brain` 完全一致：`meta_json->>'rt_bp' IS NOT NULL`（只在平仓腿写）。
    """
    out: Dict[str, Any] = {"roundtrips_since_era": None, "last_close_ts": None,
                           "legs_last_10min": None, "error": None}
    try:
        from sqlalchemy import text

        from backend.core.tenant import system_identity
        from backend.database.connection import SessionLocal

        from backend.services.market_maker import pp_brain

        era = pp_brain.era_start_ts()
        out["era_start_ts"] = era
        out["era_start_iso"] = _iso(era)
        with system_identity():
            with SessionLocal() as db:
                row = db.execute(text(
                    "SELECT count(*), max(ts) FROM lane_ledger"
                    " WHERE lane_id = :lane AND event = 'fill'"
                    "   AND meta_json->>'rt_bp' IS NOT NULL"
                    "   AND ts >= to_timestamp(:era)"),
                    {"lane": pp_brain.LANE_ID, "era": era}).fetchone()
                out["roundtrips_since_era"] = int(row[0] or 0) if row else 0
                if row and row[1] is not None:
                    out["last_close_ts"] = row[1].isoformat()
                n10 = db.execute(text(
                    "SELECT count(*) FROM lane_ledger"
                    " WHERE lane_id = :lane AND ts > now() - make_interval(mins => 10)"),
                    {"lane": pp_brain.LANE_ID}).scalar()
                out["legs_last_10min"] = int(n10 or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[learning/lane] ledger counts failed: %s", exc)
        out["error"] = str(exc)[:200]
    return out


@router.get("/inlet")
async def lane_inlet() -> Dict[str, Any]:
    """高频入口（PP 往返 → 主脑）的进度与断链判据。

    判据（前端按 `health` 字段直接上色，不在前端重算）：
      · `backend_has_tick=false`            ⇒ **bad**（后端未重启，入口尚未注册）
      · `enabled=false`（MM_PP_TO_BRAIN=0） ⇒ off（按开关关闭，不是故障）
      · `cursor.present=false`              ⇒ warn（从未成功跑过）
      · `cursor.present && pending==0`      ⇒ ok
      · `pending>0`                         ⇒ warn（还有历史没补完）
    """
    from backend.services.market_maker import pp_brain

    out: Dict[str, Any] = {
        "lane_id": pp_brain.LANE_ID,
        "strategy_id": pp_brain.STRATEGY_ID,
        "tier": pp_brain.TIER,
        "nature": pp_brain.NATURE,
        "enabled": bool(pp_brain.enabled()),
        "to_brain_env": "MM_PP_TO_BRAIN",
        "isolation_on": None,
        "cursor": {"present": False},
        "ledger": {},
        "budget": {},
        "tick": {},
        "health": "unknown",
        "health_reason": "",
        "as_of": datetime.now(timezone.utc).isoformat(),
    }

    try:
        from backend.services.unified_learning_service import hft_isolation_enabled
        out["isolation_on"] = bool(hft_isolation_enabled())
    except Exception:  # noqa: BLE001
        pass

    out["ledger"] = _ledger_counts()

    # 游标（入口唯一的"我推进到哪了"证据）
    try:
        cur = pp_brain.load_cursor()
        # 注意用本模块常量（而不是 pp_brain 的内部路径）判断存在性：
        # 前端要显示的是"这个文件在不在"，且测试需要能替换该路径。
        present = os.path.exists(CURSOR_PATH)
        out["cursor"] = {
            "present": present,
            "path": CURSOR_PATH,
            "ts": cur.get("cursor_ts"),
            "ts_iso": _iso(cur.get("cursor_ts")),
            "id": cur.get("cursor_id"),
            "ingested_total": cur.get("ingested"),
            "skipped_total": cur.get("skipped"),
            "failed_total": cur.get("failed"),
            "applied_at": cur.get("applied_at"),
            "applied_at_iso": _iso(cur.get("applied_at")) if cur.get("applied_at") else None,
        }
    except Exception as exc:  # noqa: BLE001
        out["cursor"] = {"present": False, "error": str(exc)[:200]}

    out["budget"] = {
        "limit_per_tick": int(_env_int("MM_PP_BRAIN_LIMIT", 200)),
        "max_attempts": int(_env_int("MM_PP_BRAIN_MAX_ATTEMPTS", 3)),
        "since_env": os.getenv("MM_PP_BRAIN_SINCE") or "",
        "note": ("首次开启按 MM_PP_BRAIN_LIMIT/次补历史（有界）；"
                 "入参口径见 data/pp_era_start_ts.txt 定义的当前 era"),
    }

    # 学习循环：是否注册了入口 tick + 最近一次的摘要
    try:
        from backend.services.learning_loop_service import JOB_HFT_BRAIN, learning_loop
        status = learning_loop.status() or {}
        metrics = learning_loop.metrics() or {}
        last_map = status.get("last_tick_at") or {}
        out["tick"] = {
            "job_id": JOB_HFT_BRAIN,
            "registered_in_loop": JOB_HFT_BRAIN in (status.get("intervals") or {}),
            "has_heartbeat": last_map.get(JOB_HFT_BRAIN) is not None,
            "last_tick_at": last_map.get(JOB_HFT_BRAIN),
            "next_tick_at": (status.get("next_tick_at") or {}).get(JOB_HFT_BRAIN),
            "interval_sec": (status.get("intervals") or {}).get(JOB_HFT_BRAIN),
            "loop_enabled": status.get("enabled"),
            "loop_paused": status.get("paused"),
            "last_extra": (metrics.get(JOB_HFT_BRAIN) or {}).get("last_extra") or {},
            "p50_ms": (metrics.get(JOB_HFT_BRAIN) or {}).get("p50_ms"),
            "p95_ms": (metrics.get(JOB_HFT_BRAIN) or {}).get("p95_ms"),
        }
        out["backend_has_tick"] = bool(out["tick"]["registered_in_loop"])
    except Exception as exc:  # noqa: BLE001
        out["tick"] = {"error": str(exc)[:200]}
        out["backend_has_tick"] = False

    # 待补笔数（era 起往返 − 已进主脑）
    total = out["ledger"].get("roundtrips_since_era")
    ingested = (out["cursor"] or {}).get("ingested_total")
    out["pending_from_cursor"] = (
        max(0, int(total) - int(ingested)) if (total is not None and ingested is not None)
        else None)

    # 结论（判据写在后端，前端只上色）
    if not out["enabled"]:
        out["health"] = "off"
        out["health_reason"] = "MM_PP_TO_BRAIN=0：入口按开关关闭（交易链路不受影响）"
    elif not out["backend_has_tick"]:
        out["health"] = "bad"
        out["health_reason"] = ("后端进程里没有这个 tick ⇒ 未重启加载新代码，"
                               "入口尚未注册（重启后自动出现）")
    elif not (out["cursor"] or {}).get("present"):
        out["health"] = "warn"
        out["health_reason"] = ("游标文件不存在 ⇒ 入口从未成功跑过一轮"
                               "（可点『手动补扫』立即验证）")
    elif (out["pending_from_cursor"] or 0) > 0:
        out["health"] = "warn"
        out["health_reason"] = f"还有 {out['pending_from_cursor']} 笔历史往返待补"
    else:
        out["health"] = "ok"
        out["health_reason"] = "入口在跑且已追平 era 起的全部往返"
    return out


def _env_int(name: str, default: int) -> int:
    try:
        raw = os.getenv(name)
        return int(float(raw)) if raw not in (None, "") else int(default)
    except (TypeError, ValueError):
        return int(default)


# ══════════════════════════════════════════════════════════════════════
#  ② 车道顾问
# ══════════════════════════════════════════════════════════════════════

def _whitelists() -> Dict[str, Dict[str, List[float]]]:
    try:
        from backend.services.market_maker import brain_advisor as BA
        return {lane: {k: [float(v[0]), float(v[1])]
                       for k, v in (BA.allowed_keys(lane) or {}).items()}
                for lane in LANES}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[learning/lane] whitelist failed: %s", exc)
        return {lane: {} for lane in LANES}


def _current_values() -> Dict[str, Any]:
    """各车道白名单键的**现值**（hft 读 flow_learn_params；mid/long 读 runtime_tuning）。"""
    out: Dict[str, Any] = {lane: {} for lane in LANES}
    try:
        from backend.services.market_maker import brain_advisor as BA
        hft_keys = set(BA.allowed_keys("hft"))
    except Exception:  # noqa: BLE001
        hft_keys = set()
    try:
        from backend.services.market_maker.flow_rules import load_learn_params
        cur = dict(load_learn_params(_root()))
        out["hft"] = {k: cur.get(k) for k in sorted(hft_keys)}
    except Exception as exc:  # noqa: BLE001
        out["hft"] = {"error": str(exc)[:200]}
    try:
        from backend.services import runtime_tuning_store as rts
        tuning = rts.get_all_tuning() or {}
        for lane in ("mid", "long"):
            for key in _whitelists().get(lane, {}):
                if "." in key:
                    head, _, tail = key.partition(".")
                    node = tuning.get(head) or {}
                    if head == "by_nature":
                        nat, _, field = tail.partition(".")
                        node = node.get(nat) or {}
                        out[lane][key] = node.get(field)
                    else:
                        out[lane][key] = node.get(tail) if isinstance(node, dict) else None
                else:
                    node = tuning.get(key)
                    out[lane][key] = (node.get("value")
                                      if isinstance(node, dict) else node)
    except Exception as exc:  # noqa: BLE001
        for lane in ("mid", "long"):
            out[lane] = {"error": str(exc)[:200]}
    return out


def _root():
    from pathlib import Path
    return Path(__file__).resolve().parents[2]


@router.get("/advisor")
async def lane_advisor(
    lane: str = Query("", description="过滤车道：hft/mid/long；留空=全部"),
    limit: int = Query(50, ge=1, le=500),
    include_raw: int = Query(0, ge=0, le=1, description="1=返回模型原始输出（可数 KB）"),
) -> Dict[str, Any]:
    """车道顾问：决策时间轴 + 白名单现值 + 待判/已判（全部只读）。

    决策取值与含义（`decision` 字段，前端按此上色）：
      applied                   已落地（成功写入参数文件）
      proposal_only             只出提案（MM_BRAIN_APPLY=0）
      hold                      模型自己说不动（正确结论之一）
      rejected                  越界/无引用/现值不一致 ⇒ 作废
      blocked_by_gate           该车道统计关未过 ⇒ 不改
      blocked_by_counterfactual 桶门阈值反事实未过 ⇒ 不改
      kb_unavailable            知识库不可用/无卡片 ⇒ 本轮放弃
      llm_invalid               模型不可用或非合法 JSON ⇒ 本轮放弃
      not_due                   样本不足且未到间隔
      disabled / no_whitelist / error
    """
    lane_key = str(lane or "").strip().lower()
    rows = _read_jsonl(JOURNAL_PATH, limit=int(limit) * 3 if lane_key else int(limit))
    if lane_key:
        rows = [r for r in rows if str(r.get("lane") or "") == lane_key]
    rows = rows[-int(limit):]

    journal: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {}
    for r in rows:
        dec = str(r.get("decision") or "unknown")
        counts[dec] = counts.get(dec, 0) + 1
        item: Dict[str, Any] = {
            "ts": r.get("ts"),
            "ts_iso": _iso(r.get("ts")),
            "lane": r.get("lane"),
            "decision": dec,
            "why": str(r.get("why") or "")[:400],
            "param": r.get("param"),
            "old": r.get("old"),
            "new": r.get("new"),
            "cited": r.get("cited") or [],
            "kb": r.get("kb") or {},
            "gate": r.get("gate"),
            "evidence_digest": r.get("evidence_digest") or {},
        }
        if include_raw:
            item["raw_llm"] = str(r.get("raw_llm") or "")[:1200]
        journal.append(item)
    journal.reverse()  # 最新在前

    pending = _read_json(PENDING_PATH)
    history = _read_jsonl(HISTORY_PATH, limit=20)
    history.reverse()

    return {
        "lane": lane_key or "all",
        "journal": journal,
        "decision_counts": counts,
        "journal_present": os.path.exists(JOURNAL_PATH),
        "journal_rows_total": None if lane_key else len(rows),
        "whitelists": _whitelists(),
        "current": _current_values(),
        "pending_verdicts": pending if isinstance(pending, list) else [],
        "pending_present": os.path.exists(PENDING_PATH),
        "verdict_history": history,
        "sources": {
            "journal": JOURNAL_PATH,
            "flow_params": FLOW_PARAMS_PATH,
            "runtime_tuning": RUNTIME_TUNING_PATH,
            "pending": PENDING_PATH,
            "history": HISTORY_PATH,
        },
        "rules": {
            "whitelist": "每次只许改一个旋钮，且必须引用本轮检索到的卡片编号（否则作废）",
            "hft_apply": "写 data/flow_learn_params.json，下一拍生效",
            "midlong_apply": ("写 runtime_tuning（60s 内生效）；其它 MIDLONG_*/TREND_*/"
                              "EXIT_POLICY_* 是 import 期读 env，要重启 ⇒ 不在白名单"),
            "verdict": ("改后 30 分钟内 mean_y < 改前 × 0.8 就写回旧值"
                        "（self_tuner_verdict 每 15 分钟跑）"),
            "bucket_min_n": "pp_bucket_min_n 另走事前反事实（evolution.pp_evolution_round）",
        },
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


# ══════════════════════════════════════════════════════════════════════
#  ③ 知识卡片
# ══════════════════════════════════════════════════════════════════════

@router.get("/kb/cards")
async def lane_kb_cards(
    warm: int = Query(0, ge=0, le=1,
                      description="1=允许触发 RAG 懒加载（会阻塞数十秒：embedding 模型 ~1.3GB）"),
) -> Dict[str, Any]:
    """知识卡片：文件内容（只给检索需要的字段）+ 索引状态 + 按车道可见数。

    [2026-10-10 性能修正] 默认 **不触发** RAG 懒加载：`_ensure_ready()` 要加载
    bge-large（~1.3GB，实测 30~60s），放在这个接口里会让请求直接超时
    （实测 `Invoke-RestMethod` 30s 超时 ⇒ 面板显示"请求失败"，而数据其实没问题）。
    冷启动的加载交给第一次真实检索（`brain_advisor` 那一侧）或显式 `warm=1`。
    """
    out: Dict[str, Any] = {
        "file": {"present": False, "count": 0, "unverified": [], "meta": {}},
        "collection": {"ready": None, "degraded": None, "doc_count": None,
                       "last_indexed": None},
        "cards": [],
        "by_lane": {},
        "warm": bool(warm),
        "as_of": datetime.now(timezone.utc).isoformat(),
    }
    try:
        from backend.services.rag_knowledge_service import (
            COLL_KNOWLEDGE_CARDS,
            RAGKnowledgeService,
            rag_knowledge_service,
        )
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)[:200]
        return out

    try:
        raw = _read_json(str(_root() / "backend" / "data" / "knowledge_cards.json")) or {}
        meta = raw.get("meta") if isinstance(raw, dict) else {}
        cards = raw.get("cards") if isinstance(raw, dict) else []
        cards = cards if isinstance(cards, list) else []
        out["file"] = {
            "present": bool(cards),
            "count": len(cards),
            "meta": meta if isinstance(meta, dict) else {},
            "unverified": (meta or {}).get("unverified") or [],
        }
        out["cards"] = [{
            "id": c.get("id"),
            "title": c.get("title"),
            "lanes": c.get("lanes") or ["all"],
            "topics": c.get("topics") or [],
            "source": c.get("source"),
            "source_url": c.get("source_url"),
            "claim": str(c.get("claim") or "")[:400],
            "not_when": str(c.get("not_when") or "")[:300],
            "verified_in_repo": str(c.get("verified_in_repo") or "")[:300],
            "text": RAGKnowledgeService.card_document(c),
        } for c in cards if isinstance(c, dict) and c.get("id")]
        for ln in ("hft", "mid", "long", "all"):
            out["by_lane"][ln] = 0
        for c in out["cards"]:
            for ln in (c.get("lanes") or ["all"]):
                key = str(ln).strip().lower()
                out["by_lane"][key] = out["by_lane"].get(key, 0) + 1
    except Exception as exc:  # noqa: BLE001
        out["file"]["error"] = str(exc)[:200]

    try:
        ready = bool(getattr(rag_knowledge_service, "is_ready", False))
        # 只在显式 warm=1 时才触发懒加载（见 docstring：加载模型要 30~60s）
        if not ready and warm and hasattr(rag_knowledge_service, "_ensure_ready"):
            try:
                rag_knowledge_service._ensure_ready()
            except Exception:  # noqa: BLE001
                pass
        stats = rag_knowledge_service.get_stats() or {}
        coll = (stats.get("collections") or {}).get(COLL_KNOWLEDGE_CARDS) or {}
        last_indexed = coll.get("last_indexed")
        last_src = "memory"
        # `_last_index_time` 是**进程内**状态：若索引是在别的进程里跑过
        # （计划任务 DSH_KB_CARDS）或后端刚重启，这里会是 None ⇒ 用卡片文件 mtime 兜底。
        # 不兜底的话前端会显示"从未索引"，而实际上索引是好的（只差一个时间戳）。
        if not last_indexed:
            try:
                mt = os.path.getmtime(str(_root() / "backend" / "data"
                                          / "knowledge_cards.json"))
                last_indexed = datetime.fromtimestamp(mt, tz=timezone.utc).isoformat()
                last_src = "file_mtime"
            except Exception:  # noqa: BLE001
                last_src = "unknown"
        out["collection"] = {
            "name": COLL_KNOWLEDGE_CARDS,
            "ready": bool(stats.get("ready")),
            "degraded": bool(stats.get("degraded")),
            "doc_count": coll.get("doc_count"),
            "last_indexed": last_indexed,
            "last_indexed_source": last_src,
            # 冷启动时 collection 计数不可读（服务未就绪）—— 如实标注，前端据此提示
            "note": ("" if ready else "RAG 未加载（冷启动）：文档会在第一次检索时载入；"
                                     "需要立即核对可带 warm=1"),
        }
        out["all_collections"] = {
            k: {"doc_count": v.get("doc_count"), "last_indexed": v.get("last_indexed")}
            for k, v in (stats.get("collections") or {}).items()
        }
    except Exception as exc:  # noqa: BLE001
        out["collection"]["error"] = str(exc)[:200]
    return out
