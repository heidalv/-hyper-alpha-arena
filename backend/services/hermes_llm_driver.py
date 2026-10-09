# -*- coding: utf-8 -*-
"""Hermes LLM 驱动选择层（[2026-10-03 用户指令] 「必须用 opencode 做驱动么？直接使用 deepseek flash 不行么？

项目内就配置了啊」）。

## 为什么加这一层
L2（提示词优化）/ L3（架构进化）此前**唯一**的 LLM 通路是 OpenCode sidecar
（`opencode_bridge.collect_http_agent_stream_text`），而这条通路：
  · 需要额外一把 zai key（`.env` 里那行曾被注释掉 ⇒ 实测 `ZAI key present=False len=0`，
    任务是"跑完了但 0 产物"）；
  · 会因 `MaxListenersExceededWarning` 崩溃（实测 sidecar 日志 FATAL 后被看门狗重启）；
  · 自 2026-08-16 起停摆 48 天。
用户要求直接用**项目内已配置**的 LLM 通道（DeepSeek Flash / 各档模型）。

## 设计
`collect_hermes_text()` 与 `opencode_bridge.collect_http_agent_stream_text()` **同签名同返回**
（`(text, err)`），引擎侧只换 import：
  · `HERMES_LLM_DRIVER=direct`（**默认**）：走 `llm_config_service`（tier 可配
    `HERMES_LLM_TIER`，默认 `deep`）→ `call_llm_api_sync`；
  · `HERMES_LLM_DRIVER=opencode`：沿用旧的 sidecar 通路（原样保留，便于回滚/对照）。
不做静默降级：direct 失败就把错误原样返回，避免"看起来跑了其实没产出"。
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def driver_name() -> str:
    return str(os.getenv("HERMES_LLM_DRIVER", "direct")).strip().lower() or "direct"


def _tier() -> str:
    """[2026-10-03 用户指令] 默认 `quick` ⇒ 取租户默认配置的 **flash** 模型
    （id=17「DeepSeek V4 (Flash)」的 model 与 model_deep 都是 `deepseek-flash`）。
    需要更重的档位时设 `HERMES_LLM_TIER=deep`。"""
    return str(os.getenv("HERMES_LLM_TIER", "quick")).strip().lower() or "quick"


def _extract_text(result: Optional[Dict[str, Any]]) -> str:
    """从 call_llm_api_sync 的返回里取正文（优先复用仓库既有的稳健提取器）。"""
    if not result:
        return ""
    try:
        from backend.services.llm_reasoning_helper import extract_text_from_message

        choices = (result or {}).get("choices") or []
        if choices:
            content = (choices[0].get("message") or {}).get("content")
            txt = extract_text_from_message(content)
            if txt:
                return str(txt)
    except Exception as e:  # 提取器不可用时退回朴素路径
        logger.debug("[HermesDriver] 文本提取器不可用: %s", e)
    try:
        choices = (result or {}).get("choices") or []
        if choices:
            return str((choices[0].get("message") or {}).get("content") or "")
        return str(result.get("content") or result.get("text") or "")
    except Exception:
        return ""


def _resolve_llm_config():
    """按**项目既有的「用途分配」机制**取 LLM 配置（多租户安全，禁止公用默认）。

    [2026-10-03] 实测裸调 `get_llm_config()` 会被 ``FORBID_SHARED_PLATFORM_LLM`` 拒绝
    （「拒绝公用默认配置：请为账户配置自有 LLM」）。仓库本身就有按用途路由的入口
    `get_llm_config_for_usage(usage=...)`，用途表含 `evolution`（学习进化：策略进化/遗传优化）
    与 `assistant`（对话助手 / Hermes / OpenCode 侧车）—— Hermes L2/L3 正属这两类。
    解析顺序：
      1. `HERMES_LLM_TENANT_ID` 显式指定租户（可选）；
      2. 用**本机有 LLM 配置的租户**（账户 user_id）依次尝试 usage=`evolution` → `assistant`。
    """
    from backend.services.llm_config_service import get_llm_config_for_usage
    from backend.database.connection import SessionLocal

    tier = _tier()
    usages = [u.strip() for u in (os.getenv("HERMES_LLM_USAGE", "evolution,assistant")).split(",") if u.strip()]

    # 0) 显式钉住某条配置（最优先，便于"就用某一条"）
    pin = os.getenv("HERMES_LLM_CONFIG_ID")
    if pin and str(pin).strip().isdigit():
        try:
            from backend.services.llm_config_service import get_llm_config

            cfg = get_llm_config(config_id=int(str(pin).strip()), tier=tier, allow_shared=True)
            if cfg:
                logger.info("[HermesDriver] 使用 HERMES_LLM_CONFIG_ID=%s（model=%s）", pin, getattr(cfg, "model", "?"))
                return cfg
        except Exception as e:
            logger.warning("[HermesDriver] 指定配置解析失败: %s", e)

    explicit = os.getenv("HERMES_LLM_TENANT_ID")
    tenants: list[int] = []
    if explicit and str(explicit).strip().isdigit():
        tenants.append(int(str(explicit).strip()))
    else:
        db = SessionLocal()
        try:
            from sqlalchemy import text

            tenants = [int(r[0]) for r in db.execute(text(
                "SELECT DISTINCT user_id FROM accounts WHERE user_id IS NOT NULL ORDER BY user_id"
            )).fetchall()]
            # 有 LLM 配置的租户优先（避免先试空租户）
            cfgs = [int(r[0]) for r in db.execute(text(
                "SELECT DISTINCT tenant_id FROM llm_configurations WHERE tenant_id IS NOT NULL "
                "AND coalesce(api_key,'') <> ''"
            )).fetchall()]
            tenants = [t for t in cfgs if t in tenants] + [t for t in tenants if t not in cfgs]
        except Exception:
            tenants = []
        finally:
            db.close()

    for tid in tenants:
        # 1) 租户**默认**配置（本机= id 17「DeepSeek V4 (Flash)」is_default=true）
        try:
            from backend.services.llm_config_service import get_llm_config

            cfg = get_llm_config(tier=tier, tenant_id=tid)
            if cfg:
                logger.info("[HermesDriver] 使用租户 %s 默认 LLM（model=%s）", tid, getattr(cfg, "model", "?"))
                return cfg
        except Exception as e:
            logger.debug("[HermesDriver] 租户默认解析失败 tenant=%s: %s", tid, e)
        # 2) 按用途绑定（evolution / assistant）
        for usage in usages:
            cfg = get_llm_config_for_usage(usage, tier=tier, tenant_id=tid)
            if cfg:
                logger.info("[HermesDriver] LLM 配置解析成功 tenant=%s usage=%s model=%s",
                            tid, usage, getattr(cfg, "model", "?"))
                return cfg
    return None


def collect_hermes_text(
    *,
    system_prompt: str,
    user_text: str,
    agent: str = "plan",
    model_slug: Optional[str] = None,
    session_title: str = "Hermes",
    log_prefix: str = "Hermes",
    idle_timeout_s: float = 900.0,
    max_duration_s: float = 7200.0,
) -> Tuple[str, Optional[str]]:
    """返回 (正文, 错误)。签名与 opencode_bridge 版本一致，便于引擎无感切换。"""
    if driver_name() == "opencode":
        from backend.services.opencode_bridge import collect_http_agent_stream_text

        return collect_http_agent_stream_text(
            system_prompt=system_prompt,
            user_text=user_text,
            agent=agent,
            model_slug=model_slug,
            session_title=session_title,
            log_prefix=log_prefix,
            idle_timeout_s=idle_timeout_s,
            max_duration_s=max_duration_s,
        )

    # ── 直连项目内 LLM 配置 ──
    try:
        from backend.services.llm_config_service import call_llm_api_sync

        tier = _tier()
        cfg = _resolve_llm_config()
        if not cfg:
            return "", (f"未取到 LLM 配置（tier={tier}）；请在「设置 → LLM 配置」里为账户配置模型，"
                        f"或设 HERMES_LLM_TENANT_ID 指定租户")
        timeout = float(os.getenv("HERMES_LLM_TIMEOUT_S", "600"))
        res = call_llm_api_sync(
            cfg,
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
            temperature=float(os.getenv("HERMES_LLM_TEMPERATURE", "0.2")),
            max_tokens=int(os.getenv("HERMES_LLM_MAX_TOKENS", "8000")),
            timeout=timeout,
            caller=f"hermes:{log_prefix}",
            bypass_cache=True,  # L2/L3 每次应基于最新证据，命中缓存会得出旧结论
        )
        txt = _extract_text(res)
        if not txt:
            return "", f"直连 LLM 返回空正文（model={getattr(cfg, 'model', '?')}）"
        logger.info(
            "[HermesDriver] direct 成功 caller=%s tier=%s model=%s len=%d",
            log_prefix, tier, getattr(cfg, "model", "?"), len(txt),
        )
        return txt, None
    except Exception as e:
        logger.warning("[HermesDriver] direct 调用失败: %s", e)
        return "", f"直连 LLM 失败: {str(e)[:200]}"


def driver_status() -> Dict[str, Any]:
    """供界面/诊断：当前驱动与目标模型。"""
    out: Dict[str, Any] = {"driver": driver_name(), "tier": _tier()}
    if driver_name() == "direct":
        try:
            cfg = _resolve_llm_config()

            out.update({
                "provider": getattr(cfg, "provider", None),
                "model": getattr(cfg, "model", None),
                "base_url": getattr(cfg, "base_url", None),
                "configured": bool(cfg),
                "usage": os.getenv("HERMES_LLM_USAGE", "evolution,assistant"),
            })
        except Exception as e:
            out["error"] = str(e)[:160]
    else:
        try:
            from backend.services.opencode_bridge import _model, _server_url

            out.update({"model": _model(), "server_url": _server_url()})
        except Exception as e:
            out["error"] = str(e)[:160]
    return out
