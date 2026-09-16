# -*- coding: utf-8 -*-
"""ModelGateway：双模型深度分析的统一入口（v3 方向 2）。

三条传输（transport）：
  minimax         MiniMax Token Plan 订阅 Key，Anthropic 兼容 API 直连（MINIMAX_BASE_URL，默认 https://api.minimax.io/anthropic）
  glm_opencode    GLM Coding Plan 只允许在 OpenCode / Claude Code 等受支持工具内使用 → 经本地 OpenCode sidecar
                 （opencode.json 的 zai-coding-plan provider，模型 GLM_OPENCODE_MODEL，默认 zai-coding-plan/glm-5.3-flash）
  glm_opencode_alt 同 sidecar 通道的 GLM 第三票（模型 GLM_ARBITER_MODEL，默认 zai-coding-plan/glm-5.3）。
                 [2026-09-05] deepseek 全面退役后由它承担仲裁与主票顶补
  deepseek        已退役（2026-09-05）：传输保留仅作应急后备，不再进入仲裁/降级默认链路

统一协议：
  call(task, system, user, transport)      单模型：QuotaGuard 预检 → 调用 → 抽 JSON → schema 校验 → analysis_runs 落库
  dual_call(task, system, user)             盲评（两主模型互不见对方，相同 context）→ 结构化比对 → 分歧走第三票仲裁
                                            → consensus_score；≥ 0.7 才算可入 signal_ledger 的结论
  degrade 规则：任一主传输被 QuotaGuard 判 degrade / 未配置 / 调用失败 → 单模型结论（status=degraded，consensus 仍按
  单票折算，永不超过 0.6，即不能成为信号）；两条都不可用 → status=skipped 并发 P2 告警。

不造数：任何失败都记 error 并如实返回；不用占位输出补位。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from backend.services.analysis import ledgers, schemas
from backend.services.analysis.quota_guard import (
    Decision,
    QuotaGuard,
    estimate_tokens,
    get_quota_guard,
    is_local_transport,
)

logger = logging.getLogger(__name__)

TRANSPORTS = ("minimax", "glm_opencode", "glm_opencode_alt", "deepseek", "ollama", "ollama2")
CONSENSUS_THRESHOLD = 0.7
DEFAULT_TEMPERATURE = 0.2
DEFAULT_TIMEOUT_S = 240.0
# 本地推理 30–120s，比云端慢一个量级；给 ollama 单独兜底超时，避免沿用云端值被掐断
OLLAMA_MIN_TIMEOUT_S = 300.0


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def sha256_text(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


def extract_json(text: str) -> Optional[Dict[str, Any]]:
    """复用 opencode_bridge 的平衡括号扫描；无可解析对象 → None（不回退占位）。"""
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    try:
        obj = json.loads(t)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    try:
        from backend.services.opencode_bridge import _extract_first_json_object

        return _extract_first_json_object(t)
    except Exception:
        return None


# --------------------------------------------------------------------------- results
@dataclass
class RawCompletion:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    tokens_estimated: bool = False


@dataclass
class ModelResult:
    transport: str
    model: str
    task: str
    ok: bool
    text: str = ""
    json: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    schema_errors: List[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    run_id: Optional[str] = None
    quota: Optional[str] = None  # allow / degrade / reject（未调用时的原因）

    @property
    def direction(self) -> int:
        return schemas.direction_to_int((self.json or {}).get("direction")) if self.json else 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "transport": self.transport, "model": self.model, "task": self.task, "ok": self.ok,
            "json": self.json, "error": self.error, "schema_errors": self.schema_errors,
            "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
            "latency_ms": self.latency_ms, "run_id": self.run_id, "quota": self.quota,
            "text_excerpt": (self.text or "")[:800],
        }


@dataclass
class ConsensusResult:
    task: str
    status: str  # ok / degraded / skipped
    consensus_group: str
    consensus_score: float
    accepted: bool  # consensus_score ≥ 阈值 且双票有效
    final: Optional[Dict[str, Any]]
    primaries: List[ModelResult]
    arbiter: Optional[ModelResult] = None
    comparison: Dict[str, Any] = field(default_factory=dict)
    run_id: Optional[str] = None
    context_hash: Optional[str] = None
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task": self.task, "status": self.status, "consensus_group": self.consensus_group,
            "consensus_score": self.consensus_score, "accepted": self.accepted, "final": self.final,
            "primaries": [p.to_dict() for p in self.primaries],
            "arbiter": self.arbiter.to_dict() if self.arbiter else None,
            "comparison": self.comparison, "run_id": self.run_id, "context_hash": self.context_hash,
            "notes": self.notes,
        }


# --------------------------------------------------------------------------- transports
class Transport:
    name: str = "base"

    def configured(self) -> Tuple[bool, str]:
        raise NotImplementedError

    def model_name(self) -> str:
        raise NotImplementedError

    def complete(self, system: str, user: str, *, max_tokens: int, temperature: float, timeout_s: float, task: str,
                 images: Optional[List[Dict[str, str]]] = None) -> RawCompletion:
        raise NotImplementedError

    def status(self) -> Dict[str, Any]:
        ok, why = self.configured()
        return {"transport": self.name, "configured": ok, "detail": why, "model": self.model_name() if ok else None}


class MiniMaxTransport(Transport):
    """MiniMax Anthropic 兼容 API（订阅 Key 直连）。只取 text 块，忽略 thinking 块。"""

    name = "minimax"

    def configured(self) -> Tuple[bool, str]:
        if not _env("MINIMAX_API_KEY"):
            return False, "MINIMAX_API_KEY 未配置"
        try:
            import anthropic  # noqa: F401
        except Exception:
            return False, "anthropic SDK 未安装"
        return True, f"base_url={self.base_url()}"

    def base_url(self) -> str:
        return _env("MINIMAX_BASE_URL", "https://api.minimax.io/anthropic")

    def model_name(self) -> str:
        return _env("MINIMAX_MODEL", "MiniMax-M3")

    def _client(self, timeout_s: float):
        import anthropic

        kwargs: Dict[str, Any] = {
            "api_key": _env("MINIMAX_API_KEY"),
            "base_url": self.base_url(),
            "timeout": timeout_s,
            "max_retries": 1,
        }
        proxy = _env("MINIMAX_HTTPS_PROXY") or _env("EVENTS_HTTPS_PROXY") or _env("HTTPS_PROXY") or _env("https_proxy")
        if proxy:
            import httpx

            kwargs["http_client"] = httpx.Client(proxy=proxy, timeout=timeout_s, trust_env=False)
        return anthropic.Anthropic(**kwargs)

    def complete(self, system: str, user: str, *, max_tokens: int, temperature: float, timeout_s: float, task: str,
                 images: Optional[List[Dict[str, str]]] = None) -> RawCompletion:
        client = self._client(timeout_s)
        try:
            # [2026-09-05 P2] 多模态：Anthropic 图像块（base64 PNG），图在文前。
            # MiniMax-M3 实测可精确读出 K 线结构/放量位置/价格区间。
            if images:
                content: List[Dict[str, Any]] = []
                for img in images:
                    b64 = str((img or {}).get("b64") or "").strip()
                    if not b64:
                        continue
                    content.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": str(img.get("media_type") or "image/png"),
                            "data": b64,
                        },
                    })
                content.append({"type": "text", "text": user})
                messages: List[Dict[str, Any]] = [{"role": "user", "content": content}]
            else:
                messages = [{"role": "user", "content": user}]
            resp = client.messages.create(
                model=self.model_name(),
                max_tokens=int(max_tokens),
                system=system,
                messages=messages,
                temperature=float(temperature),
            )
        finally:
            try:
                client.close()
            except Exception:
                pass
        parts: List[str] = []
        for block in getattr(resp, "content", []) or []:
            if getattr(block, "type", "") == "text":
                parts.append(getattr(block, "text", "") or "")
        usage = getattr(resp, "usage", None)
        return RawCompletion(
            text="".join(parts),
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            model=str(getattr(resp, "model", "") or self.model_name()),
        )


class GlmOpenCodeTransport(Transport):
    """GLM 经 OpenCode sidecar（合规路径）。token 无法从 sidecar 精确拿到 → 估算并标记。"""

    name = "glm_opencode"

    def model_name(self) -> str:
        return _env("GLM_OPENCODE_MODEL", "zai-coding-plan/glm-5.3")

    def agent(self) -> str:
        return _env("GLM_OPENCODE_AGENT", "analysis")

    def configured(self) -> Tuple[bool, str]:
        if not _env("ZAI_CODING_PLAN_API_KEY"):
            return False, "ZAI_CODING_PLAN_API_KEY 未配置（sidecar 的 zai-coding-plan provider 需要）"
        try:
            from backend.services import opencode_bridge

            if not opencode_bridge.health_check():
                return False, f"OpenCode sidecar 不可达: {opencode_bridge._last_error or 'unknown'}"
        except Exception as exc:
            return False, f"opencode_bridge 不可用: {exc}"
        return True, f"sidecar ok, model={self.model_name()}, agent={self.agent()}"

    def complete(self, system: str, user: str, *, max_tokens: int, temperature: float, timeout_s: float, task: str,
                 images: Optional[List[Dict[str, str]]] = None) -> RawCompletion:
        from backend.services import opencode_bridge

        text, err = opencode_bridge.run_http_agent_message(
            system_prompt=system,
            user_text=user,
            agent=self.agent(),
            model_slug=self.model_name(),
            session_title=f"v3 deep analysis {task}",
            timeout_s=timeout_s,
            # [2026-09-05] 非流式：opencode 的 /event SSE 订阅每会话累积监听器，
            # 高频网关任务（15min 事件扫描 + 图审）跑几十个会话后触发
            # MaxListenersExceededWarning 致命崩溃（03:34/03:55 两次实锤，与
            # 8/16 崩溃同族）。同步 POST 无此问题；演化深任务（低频）保留流式。
            allow_stream=False,
            images=images,
        )
        if err or not text:
            raise RuntimeError(err or "empty response from sidecar")
        return RawCompletion(
            text=text,
            input_tokens=estimate_tokens(system) + estimate_tokens(user),
            output_tokens=estimate_tokens(text),
            model=self.model_name(),
            tokens_estimated=True,
        )


class GlmOpenCodeAltTransport(GlmOpenCodeTransport):
    """GLM 第三票（仲裁/主票顶补）：同 sidecar 通道、不同模型（glm-5.3 非 flash）。

    [2026-09-05] deepseek 全面退役，仲裁与顶补改由 GLM Coding Plan 承担。
    必须是**独立传输名 + 不同模型名**：若直接把 arbiter 设为 glm_opencode，
    `arb_name in names` 会让每轮分歧都退化成规则仲裁；模型名相同也会被
    「同模型一致不构成交叉验证」防线折成单票。glm-5.3 与 flash 同源不同型号，
    保留真·第三票语义。峰时限制由 quota_guard 按 glm 前缀统一约束。
    """

    name = "glm_opencode_alt"

    def model_name(self) -> str:
        return _env("GLM_ARBITER_MODEL", "zai-coding-plan/glm-5.3")


class DeepSeekTransport(Transport):
    """DeepSeek 直连：管理员租户 deep_analysis 用途 → 任一 deepseek 配置 → env DEEPSEEK_API_KEY。"""

    name = "deepseek"

    def __init__(self):
        self._cfg = None
        self._cfg_ts = 0.0

    def _resolve(self):
        if self._cfg is not None and time.time() - self._cfg_ts < 300:
            return self._cfg
        cfg = None
        try:
            from backend.services.coin_select_platform_service import resolve_admin_tenant_id
            from backend.services.llm_config_service import get_llm_config, get_llm_config_for_usage

            tid = resolve_admin_tenant_id()
            if tid:
                try:
                    from backend.core.tenant import set_request_identity

                    set_request_identity(int(tid), "admin")
                except Exception:
                    pass
                cfg = get_llm_config_for_usage("deep_analysis", tenant_id=tid, tier="deep", provider="deepseek")
                if not (cfg and getattr(cfg, "api_key", None)):
                    cfg = get_llm_config_for_usage("deep_analysis", tenant_id=tid, tier="deep")
                if not (cfg and getattr(cfg, "api_key", None) and "deepseek" in (cfg.provider or "").lower()):
                    c2 = get_llm_config(tier="deep", tenant_id=tid)
                    if c2 and getattr(c2, "api_key", None) and "deepseek" in (c2.provider or "").lower():
                        cfg = c2
        except Exception as exc:
            logger.debug("[ModelGateway] deepseek 租户配置解析失败: %s", exc)
        if not (cfg and getattr(cfg, "api_key", None) and "deepseek" in (getattr(cfg, "provider", "") or "").lower()):
            key = _env("DEEPSEEK_API_KEY")
            if key:
                from backend.services.llm_config_service import LLMConfig

                cfg = LLMConfig(
                    id=0,
                    name="deepseek-env",
                    provider="deepseek",
                    model=_env("DEEPSEEK_MODEL", "deepseek-v4-flash"),
                    base_url=_env("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                    api_key=key,
                )
            else:
                cfg = None
        self._cfg, self._cfg_ts = cfg, time.time()
        return cfg

    def configured(self) -> Tuple[bool, str]:
        cfg = self._resolve()
        if not cfg:
            return False, "无 deepseek 配置（租户 deep_analysis 用途或 DEEPSEEK_API_KEY）"
        return True, f"model={cfg.model} via {'db' if getattr(cfg, 'id', 0) else 'env'}"

    def model_name(self) -> str:
        cfg = self._resolve()
        return getattr(cfg, "model", "") or _env("DEEPSEEK_MODEL", "deepseek-v4-flash")

    def complete(self, system: str, user: str, *, max_tokens: int, temperature: float, timeout_s: float, task: str,
                 images: Optional[List[Dict[str, str]]] = None) -> RawCompletion:
        from backend.services.llm_config_service import call_llm_api_sync

        cfg = self._resolve()
        if not cfg:
            raise RuntimeError("deepseek 未配置")
        resp = call_llm_api_sync(
            cfg,
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=float(temperature),
            max_tokens=int(max_tokens),
            response_format={"type": "json_object"},
            timeout=timeout_s,
            caller="analysis.model_gateway",
            bypass_cache=True,
        )
        if not resp:
            raise RuntimeError("deepseek 空响应")
        try:
            content = resp["choices"][0]["message"]["content"]
        except Exception:
            raise RuntimeError(f"deepseek 响应格式异常: {str(resp)[:300]}")
        usage = resp.get("usage") or {}
        return RawCompletion(
            text=content or "",
            input_tokens=int(usage.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage.get("completion_tokens", 0) or 0),
            model=str(resp.get("model") or cfg.model),
            tokens_estimated=not usage,
        )


class OllamaTransport(Transport):
    """本地 Ollama（免费、不限量、数据不出本机）。

    [2026-09-04] 加这条传输是因为两条主传输长期都不可用（MINIMAX_API_KEY 从未配置、
    ZAI_CODING_PLAN_API_KEY 实测 401 已失效），dual_call 一直退化成单票 —— 单票的
    consensus_score 被硬性压在 ≤0.6，永远达不到 0.7 阈值，即整套深度分析从上线起就
    产不出任何可入 signal_ledger 的结论。本地模型让交叉验证在没有云端配额时也能真跑。

    与云端的差异（都已在 llm_config_service 里处理，此处只需放宽参数）：
      - qwen3 系列强制思维链，reasoning 会吃掉大半 token 额度 → max_tokens 抬到 ≥1024
      - 本地推理 30–120s → 超时不低于 OLLAMA_MIN_TIMEOUT_S
    """

    name = "ollama"

    def __init__(self, name: str = "ollama", model_env: str = "ANALYSIS_OLLAMA_MODEL",
                 default_model: str = "qwen3:14b", prefer_db: bool = True):
        """可参数化，以便注册第二条本地传输凑齐两票（见 ModelGateway.transports）。

        prefer_db=False 时跳过 DB 绑定解析，模型名只认 env —— 第二条本地传输必须这样，
        否则它会查到与第一条相同的 `provider=ollama` 绑定，两票落到同一个模型，
        交叉验证退化成「同一个模型跑两次」，还会产出虚假的高共识分。
        """
        self.name = name
        self._model_env = model_env
        self._default_model = default_model
        self._prefer_db = prefer_db
        self._cfg = None
        self._cfg_ts = 0.0

    def _base_host(self) -> str:
        """Ollama 原生 API 根地址（OLLAMA_BASE_URL 常带 /v1 的 OpenAI 兼容后缀）。"""
        base = _env("OLLAMA_BASE_URL", "http://localhost:11434/v1").rstrip("/")
        return base[: -len("/v1")] if base.endswith("/v1") else base

    def _resolve(self):
        # [调研轮11 2026-09-16 用户澄清] 本地 Ollama 已停用（一律走线上 MiniMax/GLM）。
        # 必须**同时**拦住 DB 解析与 env 兜底构造：否则即使 DB 配置停用，下面仍会按
        # `OLLAMA_BASE_URL` 构造 cfg 去打本地端口 —— 实测每次白等 26~54s
        # （近 6h `qwen3:14b/ollama` 失败 13 次、均 49.6s）再降级云端。
        # 回滚：`LLM_LOCAL_FIRST_DISABLED=false`。
        if str(os.getenv("LLM_LOCAL_FIRST_DISABLED", "true")).strip().lower() in (
            "1", "true", "yes", "on",
        ):
            return None
        if self._cfg is not None and time.time() - self._cfg_ts < 300:
            return self._cfg
        cfg = None
        try:
            if not self._prefer_db:
                raise RuntimeError("prefer_db=False：模型名只认 env")
            from backend.services.coin_select_platform_service import resolve_admin_tenant_id
            from backend.services.llm_config_service import get_llm_config_for_usage

            tid = resolve_admin_tenant_id()
            if tid:
                try:
                    from backend.core.tenant import set_request_identity

                    set_request_identity(int(tid), "admin")
                except Exception:
                    pass
                # deep_analysis 未绑本地模型时，退而取任一 ollama 绑定（如 factor_mining→qwen3）。
                # 注意：provider 只作用于「usage_scope 绑定」这一级查询，查不到时该函数会
                # 继续回落到租户默认配置（本项目是云端 DeepSeek）——必须校验 provider，
                # 否则本地票会被悄悄换成云端模型（实测解析出 deepseek-v4-flash）。
                for _usage in ("deep_analysis", "factor_mining", "kline_analysis"):
                    _c = get_llm_config_for_usage(_usage, tenant_id=tid, tier="deep", provider="ollama")
                    if _c and str(getattr(_c, "provider", "") or "").lower() == "ollama":
                        cfg = _c
                        break
        except Exception as exc:
            logger.debug("[ModelGateway] ollama 配置解析失败: %s", exc)
        if cfg is None:
            # 本地服务无需真实密钥，DB 未绑定时直接按 env 构造
            from backend.services.llm_config_service import LLMConfig

            cfg = LLMConfig(
                id=0,
                name=f"{self.name}-env",
                provider="ollama",
                model=_env(self._model_env, self._default_model),
                base_url=_env("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
                api_key="ollama",
            )
        self._cfg, self._cfg_ts = cfg, time.time()
        return cfg

    def model_name(self) -> str:
        cfg = self._resolve()
        return getattr(cfg, "model", "") or _env(self._model_env, self._default_model)

    def configured(self) -> Tuple[bool, str]:
        """探活 + 校验模型已拉取。

        本地服务没跑时若只看配置就判「可用」，每次调用都要干等到超时才失败，
        dual_call 会被拖成分钟级 —— 故这里主动短探一次。
        """
        import urllib.request

        # [调研轮11] 本地 Ollama 已停用 → 直接判"不可用"，不探活、不发起调用
        if str(os.getenv("LLM_LOCAL_FIRST_DISABLED", "true")).strip().lower() in (
            "1", "true", "yes", "on",
        ):
            return False, "本地 Ollama 已停用（LLM_LOCAL_FIRST_DISABLED=true），一律走线上 LLM"
        want = self.model_name()
        try:
            with urllib.request.urlopen(self._base_host() + "/api/tags", timeout=3) as resp:
                installed = {
                    str(m.get("name") or "")
                    for m in (json.loads(resp.read().decode("utf-8")).get("models") or [])
                }
        except Exception as exc:
            return False, f"Ollama 服务不可达({self._base_host()}): {str(exc)[:70]}"
        if want not in installed:
            return False, f"模型 {want} 未安装（已装 {sorted(installed)[:3]}）"
        return True, f"本地 ollama, model={want}"

    def complete(self, system: str, user: str, *, max_tokens: int, temperature: float, timeout_s: float, task: str,
                 images: Optional[List[Dict[str, str]]] = None) -> RawCompletion:
        from backend.services.llm_config_service import call_llm_api_sync

        cfg = self._resolve()
        resp = call_llm_api_sync(
            cfg,
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=float(temperature),
            max_tokens=max(1024, int(max_tokens)),
            response_format={"type": "json_object"},
            timeout=max(float(timeout_s), OLLAMA_MIN_TIMEOUT_S),
            caller="analysis.model_gateway",
            bypass_cache=True,
        )
        if not resp:
            raise RuntimeError("ollama 空响应")
        try:
            content = resp["choices"][0]["message"]["content"]
        except Exception:
            raise RuntimeError(f"ollama 响应格式异常: {str(resp)[:300]}")
        usage = resp.get("usage") or {}
        return RawCompletion(
            text=content or "",
            input_tokens=int(usage.get("prompt_tokens", 0) or 0) or estimate_tokens(system) + estimate_tokens(user),
            output_tokens=int(usage.get("completion_tokens", 0) or 0) or estimate_tokens(content or ""),
            model=str(resp.get("model") or cfg.model),
            tokens_estimated=not usage,
        )


# --------------------------------------------------------------------------- comparison
def _norm_factor(s: str) -> set:
    toks = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]{2,}", (s or "").lower())
    return {t for t in toks if len(t) >= 2}


def factor_overlap(a: List[str], b: List[str]) -> float:
    """key_factors 的依据重叠度：两组要点按 token 集合的 Jaccard（0–1）。"""
    sa = set().union(*[_norm_factor(x) for x in (a or [])]) if a else set()
    sb = set().union(*[_norm_factor(x) for x in (b or [])]) if b else set()
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / float(len(sa | sb))


def compare_outputs(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """结构化比对：方向一致（0.5）+ 强度差 ≤ 2 折算（0.3）+ 依据重叠（0.2）→ raw_score ∈ [0,1]。

    方向分只给「两边都有多/空、且同向」。两边都写 neutral 是「都没看法」，
    不是交易共识，不能拿 0.5；否则套话一重叠就会假通过 0.7。
    """
    da, db_ = schemas.direction_to_int(a.get("direction")), schemas.direction_to_int(b.get("direction"))
    both_neutral = da == 0 and db_ == 0
    dir_agree = (da == db_) and da != 0
    try:
        sa, sb = float(a.get("strength", 0) or 0), float(b.get("strength", 0) or 0)
    except Exception:
        sa, sb = 0.0, 0.0
    strength_diff = abs(sa - sb)
    strength_component = max(0.0, 1.0 - max(0.0, strength_diff - 2.0) / 4.0) if dir_agree else max(0.0, 1.0 - strength_diff / 4.0) * 0.5
    overlap = factor_overlap(a.get("key_factors") or [], b.get("key_factors") or [])
    raw = 0.5 * (1.0 if dir_agree else 0.0) + 0.3 * strength_component + 0.2 * overlap
    return {
        "direction_a": da, "direction_b": db_, "direction_agree": dir_agree,
        "both_neutral": both_neutral,
        "strength_a": sa, "strength_b": sb, "strength_diff": strength_diff,
        "factor_overlap": round(overlap, 3), "raw_score": round(raw, 3),
    }


def merge_outputs(a: Dict[str, Any], b: Dict[str, Any], weight_a: float = 0.5) -> Dict[str, Any]:
    """一致时的合成：数值字段加权平均，列表字段去重合并，其余以 A 为底、B 补缺。

    [2026-09-06] bool 不再静默吞掉 B：
      - recommend_open：双方都 true 才 true（开仓要双票同意）
      - should_close：任一方 true 即 true（平仓偏保守离场）
      - 其他 bool：默认 AND
    """
    out: Dict[str, Any] = dict(a)
    for k, vb in b.items():
        va = out.get(k)
        if va is None:
            out[k] = vb
        elif isinstance(va, bool) and isinstance(vb, bool):
            if k == "should_close":
                out[k] = bool(va or vb)
            else:
                # recommend_open 及其他 bool：AND
                out[k] = bool(va and vb)
        elif isinstance(va, (int, float)) and isinstance(vb, (int, float)) and not isinstance(va, bool):
            out[k] = round(weight_a * float(va) + (1 - weight_a) * float(vb), 4)
        elif isinstance(va, list) and isinstance(vb, list):
            seen, merged = set(), []
            for item in list(va) + list(vb):
                key = json.dumps(item, sort_keys=True, ensure_ascii=False) if isinstance(item, (dict, list)) else str(item)
                if key not in seen:
                    seen.add(key)
                    merged.append(item)
            out[k] = merged[:16]
        elif isinstance(va, dict) and isinstance(vb, dict):
            out[k] = merge_outputs(va, vb, weight_a)
    out["direction"] = a.get("direction")  # 方向一致时二者相同
    return out


# --------------------------------------------------------------------------- gateway
AUTH_COOLDOWN_SEC = 1800.0  # 鉴权失败后 30 分钟内不再重试该传输（省配额、省时间）
_AUTH_ERR = re.compile(r"authentication|unauthorized|invalid[_ ]api[_ ]key|401|forbidden|403", re.I)


class ModelGateway:
    def __init__(self, quota: Optional[QuotaGuard] = None, transports: Optional[Dict[str, Transport]] = None):
        self.quota = quota or get_quota_guard()
        self.transports: Dict[str, Transport] = transports or {
            "minimax": MiniMaxTransport(),
            "glm_opencode": GlmOpenCodeTransport(),
            "glm_opencode_alt": GlmOpenCodeAltTransport(),
            "deepseek": DeepSeekTransport(),
            "ollama": OllamaTransport(),
            # 第二条本地票：与 ollama 必须是**不同模型**，否则不构成交叉验证。
            # 选 qwen2.5 而非同系列的 qwen3，是因为交叉验证要的是「独立犯错」——
            # 跨代际模型的失败模式差异更大。7b 显存占用约 5GB，可与 14b 同时常驻
            # （实测 2080 Ti 22.5G：qwen3:14b 占 9G，仍余 10G），避免反复换入换出。
            "ollama2": OllamaTransport(
                name="ollama2",
                model_env="ANALYSIS_OLLAMA2_MODEL",
                default_model="qwen2.5:7b-instruct-q4_K_M",
                prefer_db=False,
            ),
        }
        # transport → (cooldown_until_ts, reason)
        self._cooldown: Dict[str, Tuple[float, str]] = {}

    # ------------------------------------------------------------------ status
    def _in_cooldown(self, name: str) -> Optional[str]:
        until, why = self._cooldown.get(name, (0.0, ""))
        if until > time.time():
            return f"冷却中（{why}），{int(until - time.time())}s 后重试"
        return None

    def _note_failure(self, name: str, error: str) -> None:
        if error and _AUTH_ERR.search(error):
            self._cooldown[name] = (time.time() + AUTH_COOLDOWN_SEC, error[:120])
            logger.warning("[ModelGateway] %s 鉴权失败，进入 %d 分钟冷却: %s", name, int(AUTH_COOLDOWN_SEC // 60), error[:200])

    def available(self, name: str) -> Tuple[bool, str]:
        """传输此刻是否可用：已配置 + 不在鉴权冷却。"""
        tr = self.transports.get(name)
        if tr is None:
            return False, f"未知传输 {name}"
        cd = self._in_cooldown(name)
        if cd:
            return False, cd
        return tr.configured()

    def status(self) -> Dict[str, Any]:
        out = {}
        for name, t in self.transports.items():
            st = t.status()
            cd = self._in_cooldown(name)
            if cd:
                st["configured"] = False
                st["detail"] = cd
            out[name] = st
        return {
            "transports": out,
            "primaries": self.primary_names(),
            "arbiter": self.arbiter_name(),
            "consensus_threshold": CONSENSUS_THRESHOLD,
            "quota": self.quota.snapshot(),
        }

    @staticmethod
    def primary_names() -> List[str]:
        raw = _env("ANALYSIS_PRIMARY_TRANSPORTS", "minimax,glm_opencode")
        names = [x.strip() for x in raw.split(",") if x.strip() in TRANSPORTS]
        return names[:2] if names else ["minimax", "glm_opencode"]

    @staticmethod
    def arbiter_name() -> str:
        # [2026-09-05] 默认仲裁 deepseek → glm_opencode_alt（deepseek 全面退役）
        name = _env("ANALYSIS_ARBITER_TRANSPORT", "glm_opencode_alt")
        return name if name in TRANSPORTS else "glm_opencode_alt"

    @staticmethod
    def fallback_names() -> List[str]:
        """主传输不可用时的顶替候选，按优先级排。

        [2026-09-04] 原降级只允许仲裁席位顶替一个主票（`arb_name not in names` 限制），
        两条主传输同时缺席时另一条无人顶上 → 直接退化单票、共识分封顶 0.6、产不出信号。
        实测就是这种状态：MiniMax 无 key + GLM 401。改为候选池后，缺几条补几条。

        [2026-09-05] 候选池首位 deepseek → glm_opencode_alt（deepseek 全面退役）。
        """
        raw = _env("ANALYSIS_FALLBACK_TRANSPORTS", "glm_opencode_alt,ollama,ollama2")
        return [x.strip() for x in raw.split(",") if x.strip() in TRANSPORTS]

    # ------------------------------------------------------------------ single call
    def call(
        self,
        task: str,
        system: str,
        user: str,
        *,
        transport: str,
        max_output_tokens: Optional[int] = None,
        temperature: float = DEFAULT_TEMPERATURE,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        context_hash: Optional[str] = None,
        data_cutoff_ms: Optional[int] = None,
        consensus_group: Optional[str] = None,
        role: str = "primary",
        context_pack: Optional[Dict[str, Any]] = None,
        schema_task: Optional[str] = None,
        record: bool = True,
        images: Optional[List[Dict[str, str]]] = None,
    ) -> ModelResult:
        """单模型调用。schema_task 缺省 = task（仲裁用 'arbiter'）。"""
        # [2026-09-04] 输出契约兜底注入。契约原本要各调用方自己拼进 system
        # （analysis_routes / tasks.py / 仲裁都拼了），但 signal_review、anomaly、
        # timing 三个 agent 全都漏了 —— 模型压根没收到字段要求，自由发挥出的 JSON
        # 必然 schema 失败（实测 qwen3 按信号源名分组返回，六个必填字段一个不剩）。
        # 光注册 schema 不够：不把契约发给模型，注册了也白搭。这类遗漏靠约定防不住，
        # 故在此统一兜底；已自行拼接的调用方按标志串识别，不重复注入。
        if system and schemas.CONTRACT_MARK not in system:
            system = f"{system}\n{schemas.output_contract(schema_task or task)}"

        tr = self.transports.get(transport)
        model = tr.model_name() if tr else ""
        res = ModelResult(transport=transport, model=model or "", task=task, ok=False)
        if tr is None:
            res.error = f"未知传输 {transport}"
            return res
        ok_cfg, why = self.available(transport)
        if not ok_cfg:
            res.error = f"未配置: {why}"
            res.quota = "unconfigured"
            if record:
                res.run_id = self._record(res, system, user, "skipped", context_hash, data_cutoff_ms, consensus_group, role, context_pack)
            return res

        max_out = int(max_output_tokens or self.quota.budget.max_output_tokens)
        est_ctx = estimate_tokens(system) + estimate_tokens(user)
        decision: Decision = self.quota.check(transport, task, est_context_tokens=est_ctx, max_output_tokens=max_out)
        res.quota = decision.action
        if not decision.ok:
            res.error = f"quota {decision.action}: {decision.reason}"
            if record:
                res.run_id = self._record(res, system, user, "skipped", context_hash, data_cutoff_ms, consensus_group, role, context_pack)
            return res

        t0 = time.time()
        raw: Optional[RawCompletion] = None
        try:
            raw = tr.complete(system, user, max_tokens=max_out, temperature=temperature, timeout_s=timeout_s, task=task,
                              images=images)
            res.text = raw.text or ""
            res.model = raw.model or model
            res.input_tokens, res.output_tokens = raw.input_tokens, raw.output_tokens
            obj = extract_json(res.text)
            if obj is None:
                res.error = "输出中无可解析 JSON"
            else:
                valid, errs = schemas.validate(schema_task or task, obj)
                res.json = obj
                res.schema_errors = errs
                res.ok = valid
                if not valid:
                    res.error = "schema 校验失败: " + "; ".join(errs[:6])
        except Exception as exc:
            # [2026-09-11 修复] 传输层断连重试：opencode sidecar 每 ~15min 崩溃重启
            # （exit 15），崩溃窗口内的调用以 ReadError/ConnectError 失败（实测
            # glm_opencode/midlong_thesis WinError 10054）。对连接类异常重试 1 次
            # （1.5s 后退），把窗口期成功率拉回；配额/配置错误不重试。
            _retry = int(os.getenv("MODEL_GATEWAY_RETRY_COUNT", "1") or 0)
            _retryable = (
                "ReadError", "ConnectError", "ConnectTimeout", "ReadTimeout",
                "ConnectionError", "RemoteProtocolError", "RemoteDisconnected",
                "ServerDisconnectedError", "ProxyError",
            )
            first_err = f"{type(exc).__name__}: {str(exc)[:400]}"
            if _retry > 0 and any(k in type(exc).__name__ or k in first_err for k in _retryable):
                time.sleep(1.5)
                try:
                    raw = tr.complete(system, user, max_tokens=max_out, temperature=temperature,
                                      timeout_s=timeout_s, task=task, images=images)
                    res.text = raw.text or ""
                    res.model = raw.model or model
                    res.input_tokens, res.output_tokens = raw.input_tokens, raw.output_tokens
                    obj = extract_json(res.text)
                    if obj is None:
                        res.error = "输出中无可解析 JSON"
                    else:
                        valid, errs = schemas.validate(schema_task or task, obj)
                        res.json = obj
                        res.schema_errors = errs
                        res.ok = valid
                        if not valid:
                            res.error = "schema 校验失败: " + "; ".join(errs[:6])
                    logger.warning(
                        "[ModelGateway] %s/%s 首次调用失败(%s)，重试后成功",
                        transport, task, first_err[:160],
                    )
                except Exception as exc2:
                    res.error = f"重试仍失败: {type(exc2).__name__}: {str(exc2)[:400]}"
                    logger.warning("[ModelGateway] %s/%s 调用失败: %s", transport, task, res.error)
                    self._note_failure(transport, res.error)
            else:
                res.error = first_err
                logger.warning("[ModelGateway] %s/%s 调用失败: %s", transport, task, res.error)
                self._note_failure(transport, res.error)
        res.latency_ms = int((time.time() - t0) * 1000)
        self.quota.record(
            transport, task, model=res.model, input_tokens=res.input_tokens, output_tokens=res.output_tokens,
            latency_ms=res.latency_ms, ok=res.ok,
        )
        if record:
            res.run_id = self._record(
                res, system, user, "ok" if res.ok else "error", context_hash, data_cutoff_ms, consensus_group, role, context_pack
            )
        return res

    def _record(self, res: ModelResult, system: str, user: str, status: str, context_hash, data_cutoff_ms, group, role, context_pack) -> str:
        run = ledgers.AnalysisRun(
            task=res.task, transport=res.transport, model=res.model, role=role, status=status,
            consensus_group=group, context_hash=context_hash or sha256_text(user), data_cutoff_ms=data_cutoff_ms,
            system_prompt_hash=sha256_text(system), prompt_excerpt=user[:8000],
            context_pack=context_pack if role == "primary" else None,
            output_text=res.text or None, output_json=res.json, input_tokens=res.input_tokens,
            output_tokens=res.output_tokens, latency_ms=res.latency_ms, error=res.error,
            meta={"quota": res.quota, "schema_errors": res.schema_errors},
        )
        ledgers.record_run(run)
        return run.id

    # ------------------------------------------------------------------ dual call
    def dual_call(
        self,
        task: str,
        system: str,
        user: str,
        *,
        primaries: Optional[List[str]] = None,
        arbiter: Optional[str] = None,
        max_output_tokens: Optional[int] = None,
        temperature: float = DEFAULT_TEMPERATURE,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        context_hash: Optional[str] = None,
        data_cutoff_ms: Optional[int] = None,
        context_pack: Optional[Dict[str, Any]] = None,
        parallel: bool = True,
        images: Optional[List[Dict[str, str]]] = None,
        images_for: Optional[List[str]] = None,
    ) -> ConsensusResult:
        """交叉验证协议：盲评 → 比对 → 分歧仲裁 → consensus_score。

        降级规则（方案第十节：GLM 不可用时降为 DeepSeek 第三票顶上，不改流水线结构）：
        某条主传输此刻不可用（未配置 / 鉴权冷却 / sidecar 离线）→ 用仲裁传输顶替为主票；
        顶替后仲裁席位为空 → 分歧只能走规则仲裁（无共识 = neither）。
        """
        names = list(primaries or self.primary_names())
        arb_name = arbiter or self.arbiter_name()
        group = ledgers.new_id()
        ch = context_hash or sha256_text(user)
        notes: List[str] = []

        # 逐个补位：主票缺席就从候选池里找一条还没被占用的顶上（见 fallback_names 注释）
        # [2026-09-04] 顶替前连配额一起预检。只看 configured 会把配额早已耗尽的传输选上
        # （实测 DeepSeek 5h 窗 37/30 仍被选为主票，调用必然 degrade），白占一个主票席位、
        # 且让本可顶上的传输没机会。配额是动态的，预检失败不代表实际调用一定失败，
        # 因此只用于排序取舍，真正的拦截仍在 call() 内部。
        _est_ctx = estimate_tokens(system) + estimate_tokens(user)
        _max_out = int(max_output_tokens or self.quota.budget.max_output_tokens)

        def _usable(name: str) -> Tuple[bool, str]:
            ok_c, why_c = self.available(name)
            if not ok_c:
                return False, why_c
            try:
                d = self.quota.check(name, task, est_context_tokens=_est_ctx, max_output_tokens=_max_out)
                if not d.ok:
                    return False, f"配额 {d.action}: {d.reason}"
            except Exception:
                pass  # 配额预检异常不应阻断补位，交给 call() 里的正式检查
            return True, ""

        used = set(names)
        for i, n in enumerate(names):
            # [2026-09-06] 配额耗尽也要补位。此前只看 available()/configured，
            # 云端主票日配额打满后仍占席位 → dual_call 双 skip、score=0、假中性刷屏。
            ok_n, why_n = _usable(n)
            if ok_n:
                continue
            for cand in self.fallback_names():
                if cand in used or not _usable(cand)[0]:
                    continue
                notes.append(f"{n} 不可用（{why_n}）→ 由 {cand} 顶替为主票")
                names[i] = cand
                used.add(cand)
                break
            else:
                notes.append(f"{n} 不可用（{why_n}）且无可用备胎")
        # 防「假交叉验证」：两条主票落到同一个模型时，它们的一致毫无信息量，
        # 却会算出很高的 consensus_score 并被写进 signal_ledger —— 这比没有交叉验证
        # 更危险。宁可退化成单票（单票的共识分被硬性压在 ≤0.6，不会入账本）。
        if len(names) == 2:
            try:
                m0 = (self.transports[names[0]].model_name() or "").strip()
                m1 = (self.transports[names[1]].model_name() or "").strip()
                if m0 and m0 == m1:
                    notes.append(f"{names[0]} 与 {names[1]} 同为模型 {m0} → 视作单票（同模型一致不构成交叉验证）")
                    names = names[:1]
            except Exception as exc:
                logger.debug("[ModelGateway] 主票同模型检查失败: %s", exc)

        if arb_name in names:
            arb_name = ""  # 仲裁传输被拉去当主票 → 本轮只能规则仲裁

        def _one(name: str) -> ModelResult:
            # [2026-09-05] images_for：图只发给指定传输（None=全发）。图审任务只给
            # GLM（Coding Plan 订阅内图片零边际成本）；MiniMax 按 token 计费、图片
            # 最贵 → 纯文本票。异构双票（图+深数 vs 纯深数）交叉验证信息量更高。
            imgs = images if (images_for is None or name in images_for) else None
            return self.call(
                task, system, user, transport=name, max_output_tokens=max_output_tokens, temperature=temperature,
                timeout_s=timeout_s, context_hash=ch, data_cutoff_ms=data_cutoff_ms, consensus_group=group,
                role="primary", context_pack=context_pack, images=imgs,
            )

        # 本地票之间强制串行：本机 GPU 推理本来就要排队，并发拿不到任何加速，
        # 却会在「模型冷启动」时踩踏 —— 实测两条本地传输并发首调（其中一个模型
        # 未加载）时，未加载的那条 8s 后返回空响应；同样两条模型串行冷启动、
        # 或并发热启动都正常。Ollama 默认 keep_alive 仅 5 分钟，系统闲置后的
        # 第一轮分析必然撞上冷启动，不串行的话这一轮就白跑。
        if parallel and len(names) > 1 and all(is_local_transport(n) for n in names):
            parallel = False
            notes.append("主票均为本地模型 → 串行执行（规避冷启动并发踩踏）")

        results: List[ModelResult] = []
        if parallel and len(names) > 1:
            out: Dict[str, ModelResult] = {}
            threads = [threading.Thread(target=lambda n=n: out.__setitem__(n, _one(n)), daemon=True) for n in names]
            for th in threads:
                th.start()
            for th in threads:
                th.join()
            results = [out[n] for n in names if n in out]
        else:
            results = [_one(n) for n in names]

        valid = [r for r in results if r.ok and r.json]
        for r in results:
            if not r.ok:
                notes.append(f"{r.transport}: {r.error}")

        cres = ConsensusResult(task=task, status="skipped", consensus_group=group, consensus_score=0.0, accepted=False,
                               final=None, primaries=results, context_hash=ch, notes=notes)
        if not valid:
            cres.status = "skipped"
            self._alert_skipped(task, notes)
        elif len(valid) == 1:
            r = valid[0]
            cres.status = "degraded"
            cres.final = dict(r.json)
            # 单票：置信折半且上限 0.6（永不达阈值，不可成为信号）
            conf = float(r.json.get("confidence", 0.5) or 0.5)
            cres.consensus_score = round(min(0.6, 0.5 * max(0.0, min(1.0, conf)) + 0.1), 3)
            cres.comparison = {"single_vote": r.transport}
            notes.append(f"单模型结论（{r.transport}），不得入 signal_ledger")
        else:
            a, b = valid[0], valid[1]
            cmp_ = compare_outputs(a.json, b.json)
            cres.comparison = cmp_
            score = cmp_["raw_score"]
            if score >= CONSENSUS_THRESHOLD:
                cres.status = "ok"
                cres.final = merge_outputs(a.json, b.json)
                cres.consensus_score = round(score, 3)
            elif cmp_.get("both_neutral"):
                # 两票都没方向：合并叙事即可，禁止升仲裁再被抬到 0.7。
                cres.status = "ok"
                cres.final = merge_outputs(a.json, b.json)
                cres.consensus_score = round(score, 3)
                notes.append("双票均为中性：这是「都没方向」，不是交易共识，不升仲裁、不入账本")
            else:
                arb = self._arbitrate(
                    task, system, user, a, b, arb_name, group, ch, data_cutoff_ms, timeout_s,
                    images=images if (images_for is None or arb_name in (images_for or ())) else None,
                    used_transports={a.transport, b.transport},
                    notes=notes,
                )
                cres.arbiter = arb
                if arb and arb.ok and arb.json:
                    verdict = str(arb.json.get("verdict", "neither")).upper()
                    if verdict in ("A", "B"):
                        chosen = a if verdict == "A" else b
                        cres.final = dict(chosen.json)
                        cres.final["arbitration"] = {
                            "verdict": verdict, "by": arb.transport,
                            "rationale": arb.json.get("rationale"),
                        }
                        cres.consensus_score = round(max(score, CONSENSUS_THRESHOLD), 3)
                        cres.status = "ok"
                    elif verdict == "MERGE":
                        cres.final = merge_outputs(a.json, b.json)
                        cres.final["direction"] = arb.json.get("direction", cres.final.get("direction"))
                        cres.final["strength"] = arb.json.get("strength", cres.final.get("strength"))
                        cres.final["arbitration"] = {
                            "verdict": "merge", "by": arb.transport,
                            "rationale": arb.json.get("rationale"),
                        }
                        cres.consensus_score = round(max(score, CONSENSUS_THRESHOLD), 3)
                        cres.status = "ok"
                    else:
                        cres.final = None
                        cres.consensus_score = round(score, 3)
                        cres.status = "ok"
                        notes.append("仲裁判 neither：双模型分歧且第三票不站队，不成信号")
                elif arb is None:
                    cres.final = None
                    cres.consensus_score = round(score, 3)
                    cres.status = "ok"
                    notes.append("无仲裁席位（规则仲裁）：双模型分歧，不成信号")
                else:
                    cres.final = None
                    cres.consensus_score = round(score, 3)
                    cres.status = "degraded"
                    notes.append(f"仲裁调用失败: {arb.error}；分歧未解")
        cres.accepted = bool(cres.final) and cres.consensus_score >= CONSENSUS_THRESHOLD and cres.status == "ok"

        # 共识行落库（role=consensus）
        run = ledgers.AnalysisRun(
            task=task, transport="consensus", model="+".join(r.model for r in valid) or None, role="consensus",
            status=cres.status, consensus_group=group, context_hash=ch, data_cutoff_ms=data_cutoff_ms,
            system_prompt_hash=sha256_text(system), prompt_excerpt=user[:2000], output_json=cres.final,
            consensus_score=cres.consensus_score, input_tokens=sum(r.input_tokens for r in results),
            output_tokens=sum(r.output_tokens for r in results), latency_ms=max([r.latency_ms for r in results] or [0]),
            error="; ".join(notes) if notes and not cres.final else None,
            meta={"comparison": cres.comparison, "accepted": cres.accepted, "primaries": [r.transport for r in results],
                  "arbiter": cres.arbiter.transport if cres.arbiter else None, "notes": notes},
        )
        cres.run_id = ledgers.record_run(run)
        return cres

    def _arbitrate(
        self,
        task,
        system,
        user,
        a: ModelResult,
        b: ModelResult,
        arb_name: str,
        group: str,
        ch: str,
        data_cutoff_ms,
        timeout_s: float,
        images: Optional[List[Dict[str, str]]] = None,
        used_transports: Optional[set] = None,
        notes: Optional[List[str]] = None,
    ) -> Optional[ModelResult]:
        """第三票仲裁。主仲裁不可用时按 ANALYSIS_FALLBACK_TRANSPORTS 依次顶替。

        [2026-09-06] 此前仲裁失败（配额打满 / sidecar 断连）直接 degraded，
        大量 midlong 票变成 score≈0.2 的「假中性」。本地 ollama 可作最后一道仲裁。
        """
        used = {str(x) for x in (used_transports or set()) if x}
        candidates: List[str] = []
        if arb_name and arb_name in self.transports and arb_name not in used:
            candidates.append(arb_name)
        for name in self.fallback_names():
            if name in self.transports and name not in used and name not in candidates:
                candidates.append(name)
        if not candidates:
            return None

        arb_system = (
            "你是量化投研的第三票仲裁员。两位分析员基于同一份 context pack 独立给出结论 A 与 B，现在出现分歧。"
            "请只依据 context pack 中的事实判断哪一方更有依据；若两者各有道理请给 merge 并给出你自己的方向与强度；"
            "若都缺乏依据请判 neither。禁止引入 context 之外的假设。\n" + schemas.output_contract("arbiter")
        )
        arb_user = (
            f"【任务】{task}\n\n【context pack】\n{user[:40000]}\n\n"
            f"【结论 A（{a.transport}/{a.model}）】\n{json.dumps(a.json, ensure_ascii=False)[:6000]}\n\n"
            f"【结论 B（{b.transport}/{b.model}）】\n{json.dumps(b.json, ensure_ascii=False)[:6000]}\n"
        )
        last: Optional[ModelResult] = None
        note_buf = notes if notes is not None else []
        for i, name in enumerate(candidates):
            # 仅当候选名在 images_for 意图内时才带图；这里简化：非首选仲裁不传图
            use_images = images if (i == 0 and name == arb_name) else None
            last = self.call(
                task, arb_system, arb_user, transport=name, max_output_tokens=1500,
                temperature=0.1, timeout_s=timeout_s, context_hash=ch,
                data_cutoff_ms=data_cutoff_ms, consensus_group=group, role="arbiter",
                schema_task="arbiter", images=use_images,
            )
            if last and last.ok and last.json:
                if name != arb_name:
                    note_buf.append(f"仲裁席 {arb_name or '-'} 不可用 → 由 {name} 顶替")
                return last
            err = (last.error if last else "unknown") or "unknown"
            note_buf.append(f"仲裁候选 {name} 失败: {err}")
        return last

    @staticmethod
    def _alert_skipped(task: str, notes: List[str]) -> None:
        try:
            from backend.services.ops.alerts import send_alert

            send_alert(
                "P2",
                f"深度分析任务 {task} 双模型均不可用，已跳过",
                "; ".join(notes)[:800] or "无可用传输",
                dedupe_key=f"analysis_skipped:{task}",
                source="model_gateway",
            )
        except Exception as exc:
            logger.debug("[ModelGateway] 告警发送失败: %s", exc)


_gateway: Optional[ModelGateway] = None
_gw_lock = threading.Lock()


def get_model_gateway() -> ModelGateway:
    global _gateway
    if _gateway is None:
        with _gw_lock:
            if _gateway is None:
                _gateway = ModelGateway()
    return _gateway
