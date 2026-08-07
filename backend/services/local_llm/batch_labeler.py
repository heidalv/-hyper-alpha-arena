"""
batch_labeler — 观察池批量打标器（v6 10.2.3 本地 LLM）

## 定位
用本地 LLM（ollama，GTX 1070 8GB 跑 7B 量化模型）给「选币观察池」批量打标：
观察池 = auto_coin_selections 中 action='injected' 且带因子快照的样本（45 天窗口）。

## 触发条件
未打标样本 >= _MIN_SAMPLES(3) 才触发一轮批量打标（观察池 min_samples=3 门槛）。

## 打标内容（LLM 输出严格 JSON）
{"regime_label": "trend|range|breakout|riskoff|unknown",
 "sentiment_bias": "bullish|bearish|neutral",
 "quality": "usable|marginal|reject",
 "confidence": 0.0-1.0,
 "reasoning": "一句话理由"}

## 落库
observation_pool_labels 表（selection_id 唯一 → 幂等，同一样本只打标一次）。

## 用法
    python -m backend.services.local_llm.batch_labeler --dry-run    # 只统计不调用
    python -m backend.services.local_llm.batch_labeler --limit 10   # 最多打标 10 个
    python -m backend.services.local_llm.batch_labeler              # 全量（默认 20/轮）

## 配置
LLM 走系统统一网关（llm_config_service.call_llm_api_sync）：
  - 环境变量 LOCAL_LLM_LABELER_CONFIG_ID 指定 LLMConfiguration id（首选）
  - 否则自动找 provider='ollama' 且 is_active 的配置
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

# 观察池窗口（天）：与选币反馈闭环 load_ic_samples 的 lookback 对齐。
_OBSERVATION_LOOKBACK_DAYS = 45

# 观察池门槛：至少 min_samples 个未打标样本才触发一轮批量打标。
_MIN_SAMPLES = 3

# 每轮最多打标数（控制单轮耗时与 ollama 负载）。
_MAX_LABEL_PER_RUN = 20

# 单次调用超时（秒）。7B 量化模型在 GTX 1070 上首 token 可能较慢，给足余量。
_CALL_TIMEOUT = 180.0

_LABELER_CONFIG_ID = int(os.environ.get("LOCAL_LLM_LABELER_CONFIG_ID", "0") or 0)

_SYSTEM_PROMPT = (
    "你是加密资产观察池标注器。根据给定币种的因子快照，输出该样本的市场状态标签、"
    "情绪倾向与质量判定。只能输出 JSON，不要输出任何其它内容。"
    "regime_label 取值：trend(趋势)/range(震荡)/breakout(突破)/riskoff(避险)/unknown(不明)；"
    "sentiment_bias 取值：bullish/bearish/neutral；"
    "quality 取值：usable(因子完整可用)/marginal(部分缺失)/reject(因子严重缺失或矛盾)。"
)

_LABEL_SCHEMA_HINT = (
    '{"regime_label": "trend|range|breakout|riskoff|unknown", '
    '"sentiment_bias": "bullish|bearish|neutral", '
    '"quality": "usable|marginal|reject", '
    '"confidence": <0-1你对判定的把握>, '
    '"reasoning": "<一句话理由>"}'
)


# ---------------------------------------------------------------------------
# 观察池采样
# ---------------------------------------------------------------------------

def _fetch_unlabeled_rows(db, limit: Optional[int] = None) -> List[Any]:
    """观察池：45 天内 injected + 因子快照非空 + 尚未打标的样本。"""
    from backend.database.models import AutoCoinSelection, ObservationPoolLabel

    cutoff = datetime.now() - timedelta(days=_OBSERVATION_LOOKBACK_DAYS)
    labeled_ids = (
        db.query(ObservationPoolLabel.selection_id)
        .filter(ObservationPoolLabel.selection_id.isnot(None))
        .all()
    )
    labeled = {r[0] for r in labeled_ids}

    q = (
        db.query(AutoCoinSelection)
        .filter(AutoCoinSelection.action == "injected")
        .filter(AutoCoinSelection.factor_snapshot_json.isnot(None))
        .filter(AutoCoinSelection.created_at >= cutoff)
        .order_by(AutoCoinSelection.created_at.desc())
    )
    rows = q.all()
    unlabeled = [r for r in rows if r.id not in labeled]
    if limit is not None:
        unlabeled = unlabeled[:limit]
    return unlabeled


# ---------------------------------------------------------------------------
# Prompt 构造与响应解析
# ---------------------------------------------------------------------------

def _build_prompt(row) -> List[Dict[str, str]]:
    snapshot = row.factor_snapshot_json or {}
    if not isinstance(snapshot, dict):
        try:
            snapshot = json.loads(snapshot) if isinstance(snapshot, str) else {}
        except Exception:
            snapshot = {}
    user = (
        f"币种：{row.symbol}\n"
        f"因子快照：{json.dumps(snapshot, ensure_ascii=False)}\n\n"
        f"请输出标签，严格 JSON 格式：\n{_LABEL_SCHEMA_HINT}"
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def _parse_label(raw_content: str) -> Optional[Dict[str, Any]]:
    """解析模型输出为标签 dict；失败返回 None。"""
    content = (raw_content or "").strip()
    if not content:
        return None
    if content.startswith("```"):
        content = content.strip("`")
        if content.lower().startswith("json"):
            content = content[4:].strip()
    if not content.startswith("{"):
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1 and end > start:
            content = content[start : end + 1]
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as err:
        logger.warning("[Labeler] JSON 解析失败: %s; 片段=%s", err, content[:200])
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _normalize_label(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """校验并归一化标签取值（非法值回退 unknown/neutral/reject 并降置信）。"""
    regime = str(parsed.get("regime_label") or "unknown").strip().lower()
    if regime not in ("trend", "range", "breakout", "riskoff", "unknown"):
        regime = "unknown"
    sentiment = str(parsed.get("sentiment_bias") or "neutral").strip().lower()
    if sentiment not in ("bullish", "bearish", "neutral"):
        sentiment = "neutral"
    quality = str(parsed.get("quality") or "marginal").strip().lower()
    if quality not in ("usable", "marginal", "reject"):
        quality = "marginal"
    try:
        confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0.5))))
    except (TypeError, ValueError):
        confidence = 0.5
    reasoning = str(parsed.get("reasoning") or "")[:500]
    return {
        "regime_label": regime,
        "sentiment_bias": sentiment,
        "quality": quality,
        "confidence": confidence,
        "reasoning": reasoning,
    }


# ---------------------------------------------------------------------------
# LLM 配置解析
# ---------------------------------------------------------------------------

def _resolve_config():
    """返回 (config, model_name)；无可用配置返回 (None, None)。

    优先环境变量 LOCAL_LLM_LABELER_CONFIG_ID；否则找 provider='ollama' 的活跃配置。
    系统身份穿透 RLS（后台线程无租户上下文）。
    """
    from backend.core.tenant import set_system_identity

    set_system_identity()

    from backend.database.connection import SessionLocal
    from backend.database.models import LLMConfiguration
    from backend.services.llm_config_service import get_llm_config

    db = SessionLocal()
    try:
        cfg_id = _LABELER_CONFIG_ID
        if cfg_id:
            cfg = get_llm_config(cfg_id, tenant_id=None, allow_shared=True)
            if cfg is None:
                logger.warning("[Labeler] LOCAL_LLM_LABELER_CONFIG_ID=%s 读取失败", cfg_id)
            else:
                return cfg, cfg.model
        rows = (
            db.query(LLMConfiguration)
            .filter(LLMConfiguration.provider == "ollama")
            .filter(LLMConfiguration.is_active == "true")
            .all()
        )
        if not rows:
            logger.warning(
                "[Labeler] 无 provider=ollama 的 LLMConfiguration。"
                "请先注册本地模型配置（见 5.2 接线）。"
            )
            return None, None
        row = rows[0]
        cfg = get_llm_config(row.id, tenant_id=None, allow_shared=True)
        return cfg, row.model
    finally:
        db.close()


def _call_labeler(messages: List[Dict[str, str]], config, model: str) -> Optional[str]:
    """调用本地 LLM，返回 assistant 文本。失败返回 None（不抛出）。"""
    try:
        from backend.services.llm_config_service import call_llm_api_sync
    except Exception as err:  # noqa: BLE001
        logger.warning("[Labeler] llm_config_service 导入失败: %s", err)
        return None
    try:
        resp = call_llm_api_sync(
            config=config,
            messages=messages,
            temperature=0.2,
            max_tokens=400,
            response_format={"type": "json_object"},
            timeout=_CALL_TIMEOUT,
            caller="observation_pool_labeler",
        )
    except Exception as err:  # noqa: BLE001
        logger.warning("[Labeler] 本地模型调用失败（ollama 可能未启动）: %s", err)
        return None
    if not resp:
        return None
    try:
        return resp["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        logger.warning("[Labeler] 响应结构异常: %s", str(resp)[:200])
        return None


# ---------------------------------------------------------------------------
# 核心：批量打标
# ---------------------------------------------------------------------------

def run_batch_labeling(*, dry_run: bool = False, limit: Optional[int] = None) -> Dict[str, Any]:
    """单轮观察池批量打标。

    Args:
        dry_run: 只统计观察池规模与待打标数，不调用 LLM、不落库。
        limit: 本轮最多打标数（默认 _MAX_LABEL_PER_RUN）。

    Returns:
        汇总（ok/skipped/skip_reason/labeled/failed/…）。
    """
    t0 = time.time()
    result: Dict[str, Any] = {
        "ok": False,
        "skipped": False,
        "skip_reason": "",
        "observation_pool": 0,
        "unlabeled": 0,
        "labeled": 0,
        "failed": 0,
        "dry_run": dry_run,
        "elapsed_sec": 0.0,
    }

    from backend.core.tenant import set_system_identity

    set_system_identity()

    from backend.database.connection import SessionLocal
    from backend.database.models import AutoCoinSelection, ObservationPoolLabel

    db = SessionLocal()
    try:
        # 1. 观察池采样
        pool = _fetch_unlabeled_rows(db, limit=None)
        unlabeled = pool
        result["observation_pool"] = len(pool)
        result["unlabeled"] = len(unlabeled)

        if len(unlabeled) < _MIN_SAMPLES:
            result["skipped"] = True
            result["skip_reason"] = (
                f"观察池未标样本 {len(unlabeled)} < min_samples={_MIN_SAMPLES}"
            )
            logger.info("[Labeler] %s", result["skip_reason"])
            return result

        if dry_run:
            result["ok"] = True
            result["elapsed_sec"] = round(time.time() - t0, 2)
            logger.info("[Labeler][dry_run] 观察池=%d 待打标=%d（触发门槛 %d）",
                        len(pool), len(unlabeled), _MIN_SAMPLES)
            return result

        # 2. 解析 LLM 配置
        config, model = _resolve_config()
        if config is None:
            result["skip_reason"] = "无可用 ollama LLMConfiguration"
            result["elapsed_sec"] = round(time.time() - t0, 2)
            return result

        # 3. 批量打标（每轮限额）
        target = unlabeled[:_MAX_LABEL_PER_RUN if limit is None else limit]
        labeled = 0
        failed = 0
        for row in target:
            try:
                messages = _build_prompt(row)
                raw = _call_labeler(messages, config, model)
                if raw is None:
                    failed += 1
                    continue
                parsed = _parse_label(raw)
                if parsed is None:
                    failed += 1
                    continue
                norm = _normalize_label(parsed)
                db.add(ObservationPoolLabel(
                    selection_id=row.id,
                    symbol=row.symbol,
                    session_id=row.session_id,
                    llm_config_id=getattr(config, "id", None),
                    model=model,
                    regime_label=norm["regime_label"],
                    sentiment_bias=norm["sentiment_bias"],
                    quality=norm["quality"],
                    confidence=norm["confidence"],
                    reasoning=norm["reasoning"],
                    raw_json=parsed,
                    tenant_id=row.tenant_id,
                ))
                db.commit()
                labeled += 1
                logger.info(
                    "[Labeler] #%s %s → regime=%s sentiment=%s quality=%s conf=%.2f",
                    row.id, row.symbol,
                    norm["regime_label"], norm["sentiment_bias"],
                    norm["quality"], norm["confidence"],
                )
            except Exception as err:  # noqa: BLE001
                try:
                    db.rollback()
                except Exception:
                    pass
                failed += 1
                logger.warning("[Labeler] #%s %s 打标失败: %s", row.id, row.symbol, err)

        result["labeled"] = labeled
        result["failed"] = failed
        result["ok"] = labeled > 0 or failed > 0
        result["elapsed_sec"] = round(time.time() - t0, 2)
        logger.info("[Labeler] 完成：打标 %d 成功 / %d 失败，耗时 %.1fs",
                    labeled, failed, result["elapsed_sec"])
        return result
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 调度注册（低频，fail-safe：ollama 未启动时静默跳过）
# ---------------------------------------------------------------------------

def register_batch_labeler_job(scheduler, *, interval_hours: int = 6) -> bool:
    """把批量打标器注册为低频定时任务（默认每 6 小时）。

    未注册到 ollama 配置时不注册（同 gate_optimizer 的约定）。
    """
    from backend.core.tenant import set_system_identity

    set_system_identity()
    from backend.database.connection import SessionLocal
    from backend.database.models import LLMConfiguration

    db = SessionLocal()
    try:
        has_ollama = (
            db.query(LLMConfiguration)
            .filter(LLMConfiguration.provider == "ollama")
            .filter(LLMConfiguration.is_active == "true")
            .first()
        ) is not None
    except Exception:
        has_ollama = False
    finally:
        db.close()

    if not has_ollama and not _LABELER_CONFIG_ID:
        logger.info("[Labeler] 无 ollama LLM 配置，跳过定时任务注册。")
        return False

    job_id = "observation_pool_batch_labeler"
    try:
        if hasattr(scheduler, "add_interval_task"):
            scheduler.add_interval_task(
                run_batch_labeling,
                interval_seconds=interval_hours * 3600,
                task_id=job_id,
                max_instances=1,
            )
        else:
            from apscheduler.triggers.interval import IntervalTrigger
            scheduler.add_job(
                run_batch_labeling,
                trigger=IntervalTrigger(hours=interval_hours),
                id=job_id,
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=600,
            )
        logger.info("[Labeler] 定时任务已注册：%s，每 %d 小时", job_id, interval_hours)
        return True
    except Exception as err:  # noqa: BLE001
        logger.warning("[Labeler] 定时任务注册失败: %s", err)
        return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(description="观察池批量打标器（本地 LLM）")
    p.add_argument("--dry-run", action="store_true", help="只统计观察池，不调用 LLM")
    p.add_argument("--limit", type=int, default=None, help="本轮最多打标数")
    args = p.parse_args()

    print("=" * 60)
    print(f"观察池批量打标（dry_run={args.dry_run}, limit={args.limit}）")
    print("=" * 60)
    res = run_batch_labeling(dry_run=args.dry_run, limit=args.limit)
    print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
    if res.get("skipped"):
        print(f"\n[SKIP] {res.get('skip_reason', '')}")
        return 0
    if res.get("dry_run"):
        print("\n[dry_run] 观察池就绪，正式打标需去除 --dry-run")
        return 0
    if res.get("labeled", 0) > 0:
        print(f"\n[OK] 打标 {res['labeled']} 条，失败 {res.get('failed', 0)} 条")
        return 0
    print(f"\n[FAIL] 无打标产出：{res.get('skip_reason', '未知')}")
    return 1


if __name__ == "__main__":
    sys.exit(_cli())
