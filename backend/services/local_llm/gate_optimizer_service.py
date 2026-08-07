"""
gate_optimizer_service — 本地 LLM 门控参数优化器（交易机侧接入）

## 定位
调用内网 GPU 算力机上的本地模型（Qwen3-30B-A3B），让它根据近期交易统计
建议门控参数（min_risk_reward / scalp_min_confidence / max_daily_trades），
经 RuntimeGovernor 仲裁后写入 runtime_tuning.json。

本服务是**唯一触碰运行时**的模块，但完全不触碰实时决策链：
  - 只读交易统计（复用 decision_feedback 的归因）
  - 调 GPU 机拿参数建议
  - 经 Governor 仲裁提交（source 优先级 55，低于规则 60，出错被自动覆盖）
  - 失败时静默跳过，不影响交易

## 触发
低频运行（每日收盘后 / 每周），由调度器调用 run_gate_optimization()。

## 配置
GPU 机推理服务地址通过 LLMConfiguration 表配置（base_url 指向内网 IP）。
本服务用 LOCAL_LLM_CONFIG_ID 找到该配置；也可用环境变量覆盖。

详见 docs/LOCAL_LLM_TRADING_HOST_GUIDE.md。
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

# 本地 GPU 模型的 LLMConfiguration id（交易机数据库 llm_configurations.id）。
# 通过环境变量 LOCAL_LLM_CONFIG_ID 覆盖；实施时填入实际 id。
# 可用 python -m backend.services.local_llm.connectivity_check 先验证连通性。
_LOCAL_LLM_CONFIG_ID = int(os.environ.get("LOCAL_LLM_CONFIG_ID", "0"))

# 调用超时（秒）。MoE 模型推理快，但首次加载/批量时可能慢，给足余量。
_CALL_TIMEOUT = 90.0

# Governor 受管的、本模型可建议的数值型参数（与 runtime_governor.GOVERNED_KEYS 对齐，
# 排除 disabled_natures——那是列表型，由规则更合适处理）。
_OPTIMIZABLE_KEYS = ("min_risk_reward", "scalp_min_confidence", "max_daily_trades")

# 参数合理边界（unified_gate 也会二次截断，这里先挡一道明显的离谱值）。
_PARAM_BOUNDS: Dict[str, Tuple[float, float]] = {
    "min_risk_reward": (1.5, 3.0),
    "scalp_min_confidence": (55.0, 85.0),
    "max_daily_trades": (3.0, 10.0),
}

# 归因统计的回看天数（与 decision_feedback 的 build_net_attribution 默认 7 天一致）。
_LOOKBACK_DAYS = 7

# 最少样本数：近期交易太少时不出建议（避免噪声）。
_MIN_TRADES_TO_ADVISE = 10

_SYSTEM_PROMPT = (
    "你是加密永续合约交易平台的门控参数优化器。根据给定的近期交易统计和当前参数，"
    "输出建议的门控参数调整。决策原则：手续费占比高则降频收紧，平均亏损>平均盈利则"
    "提高盈亏比门槛，胜率高且赚多亏少则可适度放松。只能输出 JSON，不要输出任何其它内容。"
)


# ---------------------------------------------------------------------------
# 统计采集（复用 decision_feedback 的净扣费归因）
# ---------------------------------------------------------------------------

def _gather_stats(days: int = _LOOKBACK_DAYS) -> Optional[Dict[str, Any]]:
    """采集近期交易统计，复用 DecisionFeedbackService.build_net_attribution。

    返回形如 {
        "summary": {trades, win_rate, avg_win, avg_loss, fee_gross_ratio, net_pnl, ...},
        "by_nature": {trend: {...}, scalp: {...}, ...},
    }；采集失败返回 None。
    """
    try:
        from backend.services.decision_feedback_service import DecisionFeedbackService
        from backend.database.connection import SessionLocal
    except Exception as err:  # noqa: BLE001
        logger.warning("[GateOpt] 依赖导入失败: %s", err)
        return None

    own_session = False
    db = None
    try:
        db = SessionLocal()
        own_session = True
    except Exception as err:  # noqa: BLE001
        logger.debug("[GateOpt] 无法打开 DB session: %s", err)
        return None

    try:
        svc = DecisionFeedbackService()
        attribution = svc.build_net_attribution(db, days=days)
        summary = attribution.get("summary") or {}
        trades = summary.get("trades", 0) or 0
        if trades < _MIN_TRADES_TO_ADVISE:
            logger.info(
                "[GateOpt] 近 %d 天仅 %d 笔交易（< %d），跳过本轮建议",
                days, trades, _MIN_TRADES_TO_ADVISE,
            )
            return None
        return attribution
    except Exception as err:  # noqa: BLE001
        logger.warning("[GateOpt] 统计采集失败: %s", err)
        return None
    finally:
        if own_session and db is not None:
            try:
                db.close()
            except Exception:
                pass


def _current_gates() -> Dict[str, Any]:
    """读取当前生效的门控参数（runtime_tuning_store，自动解包 {"value":...}）。"""
    try:
        from backend.services.runtime_tuning_store import get_tuning
        return {k: get_tuning(k) for k in _OPTIMIZABLE_KEYS}
    except Exception:
        return {}


def _detect_regime() -> str:
    """粗判当前市场环境。

    classify_regime 需要 market_data 参数（含 price_change/volatility），
    本优化器在离线/低频上下文不一定有实时行情，无数据时安全降级为 unknown。
    有数据来源时注入即可启用更精细的 regime。
    """
    try:
        from backend.services.decision_core.regime_agent import classify_regime
        # 无实时行情时不强调用（classify_regime 对空 dict 会返回 unknown，这里复用其保守逻辑）
        result = classify_regime({})
        return result.regime
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Prompt 构造
# ---------------------------------------------------------------------------

def _build_prompt(
    attribution: Dict[str, Any],
    current_gates: Dict[str, Any],
    regime: str,
) -> List[Dict[str, str]]:
    """构造喂给本地模型的 messages。"""
    summary = attribution.get("summary") or {}

    def _fmt(v: Any, default: str = "N/A") -> str:
        if v is None:
            return default
        if isinstance(v, float):
            return f"{v:.4f}".rstrip("0").rstrip(".")
        return str(v)

    wr = summary.get("win_rate")
    wr_str = f"{wr * 100:.1f}%" if isinstance(wr, (int, float)) else "N/A"

    # 按交易类型汇总关键信息（取 top 3 nature）
    by_nature = attribution.get("by_nature") or {}
    nature_lines: List[str] = []
    for nature, b in sorted(by_nature.items(), key=lambda kv: kv[1].get("trades", 0), reverse=True)[:3]:
        if nature in ("all", "unknown", None):
            continue
        nature_lines.append(
            f"  - {nature}: {b.get('trades', 0)}笔, "
            f"净盈亏={_fmt(b.get('net_pnl'))}, "
            f"胜率={_fmt(b.get('win_rate'))}"
        )
    nature_block = "\n".join(nature_lines) if nature_lines else "  （无分类数据）"

    user = (
        f"当前市场环境：{regime}\n"
        f"近 {_LOOKBACK_DAYS} 天整体统计：\n"
        f"- 交易笔数：{summary.get('trades', 0)}\n"
        f"- 胜率：{wr_str}\n"
        f"- 平均盈利：{_fmt(summary.get('avg_win'))}\n"
        f"- 平均亏损：{_fmt(summary.get('avg_loss'))}\n"
        f"- 净盈亏：{_fmt(summary.get('net_pnl'))}\n"
        f"- 手续费占毛利比：{_fmt(summary.get('fee_gross_ratio'))}\n"
        f"按交易类型：\n{nature_block}\n\n"
        f"当前门控参数：{json.dumps(current_gates, ensure_ascii=False)}\n\n"
        f"请输出建议参数，严格 JSON 格式：\n"
        f'{{"min_risk_reward": <1.5-3.0>, '
        f'"scalp_min_confidence": <55-85整数>, '
        f'"max_daily_trades": <3-10整数>, '
        f'"confidence": <0-1你对建议的把握>, '
        f'"reasoning": "<一句话理由>"}}'
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


# ---------------------------------------------------------------------------
# 响应解析与校验
# ---------------------------------------------------------------------------

def _parse_response(raw_content: str) -> Optional[Dict[str, Any]]:
    """解析模型输出为参数字典；失败返回 None。"""
    content = (raw_content or "").strip()
    if not content:
        return None
    # 剥 markdown 代码块
    if content.startswith("```"):
        content = content.strip("`")
        if content.lower().startswith("json"):
            content = content[4:].strip()
    # 模型可能把 JSON 嵌在文字里，尝试提取第一个 {...}
    if not content.startswith("{"):
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1 and end > start:
            content = content[start : end + 1]
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as err:
        logger.warning("[GateOpt] JSON 解析失败: %s; 内容片段=%s", err, content[:200])
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _validate_and_clamp(parsed: Dict[str, Any]) -> Tuple[Dict[str, float], str]:
    """提取并校验建议参数，超界值夹紧到边界内。返回 (建议, 跳过原因)。"""
    suggestions: Dict[str, float] = {}
    for key in _OPTIMIZABLE_KEYS:
        if key not in parsed:
            continue
        try:
            val = float(parsed[key])
        except (TypeError, ValueError):
            continue
        lo, hi = _PARAM_BOUNDS[key]
        if val < lo:
            val = lo
        elif val > hi:
            val = hi
        # 整数型参数取整
        if key in ("scalp_min_confidence", "max_daily_trades"):
            val = float(int(round(val)))
        suggestions[key] = val

    if not suggestions:
        return {}, "模型输出无可识别的参数键"
    return suggestions, ""


# ---------------------------------------------------------------------------
# 核心：调用本地模型 + 经 Governor 仲裁
# ---------------------------------------------------------------------------

def _call_local_llm(messages: List[Dict[str, str]]) -> Optional[str]:
    """通过现有统一网关调用本地 GPU 模型，返回 assistant 文本内容。"""
    if not _LOCAL_LLM_CONFIG_ID:
        logger.warning(
            "[GateOpt] 未配置 LOCAL_LLM_CONFIG_ID（环境变量），跳过。"
            "请在 llm_configurations 表添加本地 GPU 模型配置后设此变量。"
        )
        return None
    try:
        from backend.services.llm_config_service import get_llm_config, call_llm_api_sync
    except Exception as err:  # noqa: BLE001
        logger.warning("[GateOpt] llm_config_service 导入失败: %s", err)
        return None

    try:
        config = get_llm_config(_LOCAL_LLM_CONFIG_ID)
        if config is None:
            logger.warning("[GateOpt] LLMConfiguration id=%s 不存在", _LOCAL_LLM_CONFIG_ID)
            return None
    except Exception as err:  # noqa: BLE001
        logger.warning("[GateOpt] 读取 LLM 配置失败: %s", err)
        return None

    try:
        # [2026-07-11 修复] llm_config_service.should_use_llm_streaming 里的
        # long_task_markers 含 "optimizer" 子串，caller="gate_optimizer" 会被
        # 误判成"长文/深思任务"强制走流式；一旦走流式，response_format=json_object
        # 会被静默丢弃（该函数内 `if response_format and not use_streaming` 判断），
        # 模型不再被强制"只出JSON"，实测因此偶发输出自然语言推理前缀导致
        # 500 token 内被截断、JSON 解析失败（复核："gate_optimizer" 3次调用里
        # 2次因此失败）。这里改用不含长任务关键词的调用标识，走非流式+严格
        # JSON 模式，与本调用"短输出、强结构化"的定位一致。
        resp = call_llm_api_sync(
            config=config,
            messages=messages,
            temperature=0.2,
            max_tokens=500,
            response_format={"type": "json_object"},
            timeout=_CALL_TIMEOUT,
            caller="gate_param_advisor",
        )
    except Exception as err:  # noqa: BLE001
        logger.warning("[GateOpt] 调用本地模型失败（GPU 机可能离线）: %s", err)
        return None

    if not resp:
        return None
    try:
        return resp["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        logger.warning("[GateOpt] 响应结构异常: %s", str(resp)[:200])
        return None


def _submit_to_governor(
    suggestions: Dict[str, float],
    confidence: float,
    reason: str,
) -> Dict[str, Any]:
    """把每个建议参数经 Governor 仲裁提交（source=local_llm_optimizer, 优先级55）。"""
    from backend.services.runtime_governor import runtime_governor as gov

    applied: Dict[str, Any] = {}
    for key, value in suggestions.items():
        result = gov.submit_intent(
            key=key,
            value=value,
            source="local_llm_optimizer",
            confidence=confidence,
            reason=reason or "本地模型门控参数建议",
            ttl_sec=None,  # 用 DEFAULT_TTL_SEC["local_llm_optimizer"] = 36h
        )
        applied[key] = {
            "suggested": value,
            "ok": result.get("ok", False),
            "applied": result.get("applied", False),
            "winner_source": result.get("winner_source"),
        }
        logger.info(
            "[GateOpt] submit_intent %s=%s conf=%.2f → applied=%s winner=%s",
            key, value, confidence,
            result.get("applied"), result.get("winner_source"),
        )
    return applied


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------

def run_gate_optimization(*, dry_run: bool = False) -> Dict[str, Any]:
    """单次门控参数优化。低频调用（每日/每周）。

    流程：采集统计 → 构造 prompt → 调本地模型 → 解析校验 → Governor 仲裁。

    Args:
        dry_run: True 则只采集和调用模型、打印建议，不提交 Governor（用于联调/评估）。

    Returns:
        汇总字典（含 stats/model_output/suggestions/applied），供日志和评估。
    """
    t0 = time.time()
    result: Dict[str, Any] = {
        "ok": False,
        "skipped": False,
        "skip_reason": "",
        "regime": "",
        "stats_trades": 0,
        "suggestions": {},
        "applied": {},
        "model_reasoning": "",
        "elapsed_sec": 0.0,
    }

    # 1. 采集统计
    attribution = _gather_stats(days=_LOOKBACK_DAYS)
    if attribution is None:
        result["skipped"] = True
        result["skip_reason"] = "统计样本不足或采集失败"
        result["elapsed_sec"] = round(time.time() - t0, 2)
        return result
    result["stats_trades"] = (attribution.get("summary") or {}).get("trades", 0)

    # 2. 当前参数 + regime
    current = _current_gates()
    regime = _detect_regime()
    result["regime"] = regime

    # 3. 构造 prompt 并调用本地模型
    messages = _build_prompt(attribution, current, regime)
    raw = _call_local_llm(messages)
    if raw is None:
        result["skip_reason"] = "本地模型调用失败"
        result["elapsed_sec"] = round(time.time() - t0, 2)
        return result

    # 4. 解析校验
    parsed = _parse_response(raw)
    if parsed is None:
        result["skip_reason"] = "模型输出无法解析为 JSON"
        result["elapsed_sec"] = round(time.time() - t0, 2)
        return result

    suggestions, skip_reason = _validate_and_clamp(parsed)
    result["suggestions"] = suggestions
    result["model_reasoning"] = parsed.get("reasoning") or parsed.get("reason") or ""
    conf_raw = parsed.get("confidence", 0.6)
    try:
        confidence = max(0.0, min(1.0, float(conf_raw)))
    except (TypeError, ValueError):
        confidence = 0.6

    if not suggestions:
        result["skip_reason"] = skip_reason or "无有效建议"
        result["elapsed_sec"] = round(time.time() - t0, 2)
        return result

    # 5. 经 Governor 仲裁提交（或 dry_run 只记录）
    if dry_run:
        result["applied"] = {k: {"suggested": v, "dry_run": True} for k, v in suggestions.items()}
        logger.info("[GateOpt][dry_run] 建议参数: %s (未提交)", suggestions)
    else:
        result["applied"] = _submit_to_governor(suggestions, confidence, result["model_reasoning"])

    result["ok"] = True
    result["elapsed_sec"] = round(time.time() - t0, 2)
    logger.info(
        "[GateOpt] 完成 regime=%s 建议=%s applied=%s 耗时%.1fs",
        regime, suggestions, {k: v.get("applied") for k, v in result["applied"].items()},
        result["elapsed_sec"],
    )
    return result


# ---------------------------------------------------------------------------
# 定时任务注册（供 startup / API 按需调用，不在导入时自动触发）
# ---------------------------------------------------------------------------

def register_daily_job(scheduler, *, interval_hours: int = 24) -> bool:
    """把门控优化器注册为定时任务。

    复用现有 TaskScheduler 的 add_interval_task 机制（间隔触发，避免 cron 的复杂度）。
    默认每 24 小时跑一次。失败静默（GPU 机离线时 run_gate_optimization 内部已兜底）。

    Args:
        scheduler: TaskScheduler 实例（services/scheduler.py）或裸 APScheduler。
        interval_hours: 触发间隔（小时），默认 24。

    Returns:
        True 注册成功 / False 未注册（未配置 LOCAL_LLM_CONFIG_ID 或 scheduler 不可用）。

    用法（在 startup 注册区或 API 里）：
        from backend.services.local_llm.gate_optimizer_service import register_daily_job
        from services.scheduler import task_scheduler
        register_daily_job(task_scheduler, interval_hours=24)
    """
    if not _LOCAL_LLM_CONFIG_ID:
        logger.info(
            "[GateOpt] 未配置 LOCAL_LLM_CONFIG_ID，跳过定时任务注册。"
            "配置后重启或手动调用 register_daily_job 生效。"
        )
        return False

    job_id = "local_llm_gate_optimizer"
    try:
        # 优先用 TaskScheduler 的 add_interval_task（处理了 shutdown 兜底）
        if hasattr(scheduler, "add_interval_task"):
            scheduler.add_interval_task(
                run_gate_optimization,
                interval_seconds=interval_hours * 3600,
                task_id=job_id,
                max_instances=1,
            )
        else:
            # 兜底：裸 APScheduler
            from apscheduler.triggers.interval import IntervalTrigger
            scheduler.add_job(
                run_gate_optimization,
                trigger=IntervalTrigger(hours=interval_hours),
                id=job_id,
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=600,
            )
        logger.info("[GateOpt] 定时任务已注册：%s，每 %d 小时", job_id, interval_hours)
        return True
    except Exception as err:  # noqa: BLE001
        logger.warning("[GateOpt] 定时任务注册失败: %s", err)
        return False


# ---------------------------------------------------------------------------
# CLI：手动触发（联调/验证用）
# ---------------------------------------------------------------------------

def _cli() -> int:
    import argparse
    p = argparse.ArgumentParser(description="本地 LLM 门控参数优化器（手动触发）")
    p.add_argument("--dry-run", action="store_true", help="只采集+调用模型，不提交 Governor")
    p.add_argument("--config-id", type=int, default=None, help="覆盖 LOCAL_LLM_CONFIG_ID")
    args = p.parse_args()

    global _LOCAL_LLM_CONFIG_ID
    if args.config_id is not None:
        _LOCAL_LLM_CONFIG_ID = args.config_id

    if not _LOCAL_LLM_CONFIG_ID:
        print("错误：未设置 LOCAL_LLM_CONFIG_ID。")
        print("用法：python -m backend.services.local_llm.gate_optimizer_service --config-id 99")
        print("或：set LOCAL_LLM_CONFIG_ID=99")
        return 2

    print("=" * 60)
    print(f"门控参数优化（dry_run={args.dry_run}, config_id={_LOCAL_LLM_CONFIG_ID}）")
    print("=" * 60)
    res = run_gate_optimization(dry_run=args.dry_run)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
