"""
LLM Configuration Routes

API endpoints for unified LLM configuration management.
Provides CRUD operations for the centralized model configuration repository.
"""
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel

from backend.core.tenant import tenant_id_var
from backend.database.connection import get_db
from backend.utils.encryption import encrypt_llm_key, decrypt_llm_key
from database.models import LLMConfiguration, Account, ArbitrageProfileDB

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/llm-configs", tags=["LLM Configurations"])


# ============== Pydantic Models ==============

class LLMConfigCreate(BaseModel):
    """Create a new LLM configuration."""
    name: str
    provider: str  # openai, deepseek, qwen, volcengine, custom
    description: Optional[str] = None
    model: str
    model_deep: Optional[str] = None  # same API, pro/reasoner model
    base_url: str
    api_key: str
    is_default: bool = False
    usage_scope: Optional[str] = None  # comma-separated usage keys, empty = general


class LLMConfigUpdate(BaseModel):
    """Update an existing LLM configuration."""
    name: Optional[str] = None
    description: Optional[str] = None
    model: Optional[str] = None
    model_deep: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    is_default: Optional[bool] = None
    is_active: Optional[bool] = None
    usage_scope: Optional[str] = None


class LLMConfigResponse(BaseModel):
    """LLM configuration response."""
    id: int
    name: str
    provider: str
    description: Optional[str]
    model: str
    model_deep: Optional[str] = None
    usage_scope: Optional[str] = None
    base_url: str
    api_key_masked: str  # Only show last 4 chars
    is_default: bool
    is_active: bool
    last_tested_at: Optional[str]
    test_status: Optional[str]
    test_message: Optional[str]
    usage_count: int
    last_used_at: Optional[str]
    accounts_count: int
    profiles_count: int = 0
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


class LLMConfigListResponse(BaseModel):
    """List of LLM configurations."""
    total: int
    items: List[LLMConfigResponse]


# ============== Helper Functions ==============

def mask_api_key(api_key: str) -> str:
    """Mask API key for security, showing only last 4 characters."""
    if not api_key or len(api_key) < 8:
        return "****"
    return f"****{api_key[-4:]}"


def mask_api_key_partial(api_key: str) -> str:
    """部分掩码 key(§6.4.2):保留前缀与末尾字符供用户辨认,中间打码。

    用于 GET /{id}/api-key 端点:不再返回完整明文 key,只返回 ``sk-****abcd``
    形态。后端内部仍通过 ``decrypt_llm_key`` 拿到真实 key 调用 provider。
    """
    if not api_key:
        return "****"
    if len(api_key) < 8:
        return "****"
    return api_key[:3] + "****" + api_key[-4:]


def _current_tenant_id(request: Request) -> Optional[int]:
    """读取当前请求的租户 id(应用层 BYOK stamp 用)。

    优先从中间件注入的 ``scope["state"]["tenant_id"]`` 读取;ContextVar
    (``tenant_id_var``) 作为备用来源(RLS 钩子也读它)。两者都为空表示
    未认证/运维通道 → 返回 None。
    """
    # 中间件把 JWT claim 写入 scope["state"](见 middleware/auth.py)
    state = request.scope.get("state") or {}
    tid = state.get("tenant_id")
    if tid is None:
        # 退而求其次读 ContextVar(与 RLS 钩子同源)
        tid = tenant_id_var.get()
    if tid is None:
        return None
    try:
        return int(tid)
    except (TypeError, ValueError):
        return None


# [2026-08-15 LLM 统一重构] provider → 允许模型白名单（与 /providers 的
# model_variants 同源）。未列出的 provider 不限制（local/ollama/custom）。
_MODEL_WHITELIST: Dict[str, set] = {
    "deepseek": {"deepseek-v4-flash", "deepseek-chat"},
    "volcengine": {"deepseek-v4-flash-260425", "deepseek-v3-250624"},
    "openai": set(),
    "qwen": set(),
    "local": set(),
    "custom": set(),
}


def _validate_model_whitelist(provider: str, model: str) -> Optional[str]:
    """模型名不在该 provider 白名单内 → 返回错误描述；合法返回 None。"""
    p = (provider or "").strip().lower()
    wl = _MODEL_WHITELIST.get(p)
    if not wl:  # 未列 provider 或白名单为空 → 不限制
        return None
    m = (model or "").strip().lower()
    if m not in wl:
        allowed = ", ".join(sorted(wl)) or "（无预设，请自定义）"
        return (
            f"模型「{model}」不在 provider={p} 的白名单内（允许：{allowed}）。"
            "统一默认 deepseek-v4-flash；如需换模型请先修改后端白名单，禁止界面绕过。"
        )
    return None


def _count_profile_refs(db: Session, config_id: int) -> int:
    total = 0
    for col in (
        ArbitrageProfileDB.linked_llm_config_id,
        ArbitrageProfileDB.strategy_llm_config_id,
        ArbitrageProfileDB.execution_llm_config_id,
    ):
        total += db.query(ArbitrageProfileDB).filter(col == config_id).count()
    return total


def _unlink_llm_config_refs(db: Session, config_id: int) -> None:
    db.query(Account).filter(Account.llm_config_id == config_id).update(
        {Account.llm_config_id: None}, synchronize_session=False
    )
    db.query(Account).filter(Account.llm_config_id_deep == config_id).update(
        {Account.llm_config_id_deep: None}, synchronize_session=False
    )
    for col in (
        ArbitrageProfileDB.linked_llm_config_id,
        ArbitrageProfileDB.strategy_llm_config_id,
        ArbitrageProfileDB.execution_llm_config_id,
    ):
        db.query(ArbitrageProfileDB).filter(col == config_id).update(
            {col: None}, synchronize_session=False
        )


def config_to_response(config: LLMConfiguration, db: Session) -> LLMConfigResponse:
    """Convert LLMConfiguration to response model."""
    # Count accounts using this config (both quick and deep references)
    accounts_count = db.query(Account).filter(
        (Account.llm_config_id == config.id) | (Account.llm_config_id_deep == config.id)
    ).count()
    profiles_count = _count_profile_refs(db, config.id)
    
    return LLMConfigResponse(
        id=config.id,
        name=config.name,
        provider=config.provider,
        description=config.description,
        model=config.model,
        model_deep=getattr(config, "model_deep", None),
        usage_scope=getattr(config, "usage_scope", None),
        base_url=config.base_url,
        api_key_masked=mask_api_key(decrypt_llm_key(config.api_key)),
        is_default=config.is_default == "true",
        is_active=config.is_active == "true",
        last_tested_at=config.last_tested_at.isoformat() if config.last_tested_at else None,
        test_status=config.test_status,
        test_message=config.test_message,
        usage_count=config.usage_count,
        last_used_at=config.last_used_at.isoformat() if config.last_used_at else None,
        accounts_count=accounts_count,
        profiles_count=profiles_count,
        created_at=config.created_at.isoformat() if config.created_at else "",
        updated_at=config.updated_at.isoformat() if config.updated_at else ""
    )


# ============== API Endpoints ==============

@router.get("", response_model=LLMConfigListResponse)
def list_llm_configs(
    provider: Optional[str] = Query(None, description="Filter by provider"),
    active_only: bool = Query(True, description="Only return active configurations"),
    db: Session = Depends(get_db)
):
    """
    List all LLM configurations.
    
    Supports filtering by:
    - provider: Filter by LLM provider (openai, deepseek, etc.)
    - active_only: Only return active configurations (default: True)
    """
    try:
        query = db.query(LLMConfiguration)
        
        if provider:
            query = query.filter(LLMConfiguration.provider == provider)
        
        if active_only:
            query = query.filter(LLMConfiguration.is_active == "true")
        
        # Order by: default first, then by usage count, then by name
        query = query.order_by(
            LLMConfiguration.is_default.desc(),
            LLMConfiguration.usage_count.desc(),
            LLMConfiguration.name
        )
        
        configs = query.all()
        
        return LLMConfigListResponse(
            total=len(configs),
            items=[config_to_response(c, db) for c in configs]
        )
        
    except Exception as e:
        logger.error(f"Failed to list LLM configurations: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/all", response_model=LLMConfigListResponse)
def list_all_llm_configs(
    db: Session = Depends(get_db)
):
    """
    List ALL LLM configurations (including inactive and templates).
    Used for configuration management UI.
    """
    try:
        configs = db.query(LLMConfiguration).order_by(
            LLMConfiguration.is_default.desc(),
            LLMConfiguration.is_active.desc(),
            LLMConfiguration.name
        ).all()
        
        return LLMConfigListResponse(
            total=len(configs),
            items=[config_to_response(c, db) for c in configs]
        )
        
    except Exception as e:
        logger.error(f"Failed to list all LLM configurations: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/providers")
def list_providers():
    """
    List available LLM providers with their default configurations.
    """
    return {
        "providers": [
            {
                "id": "openai",
                "name": "OpenAI",
                "default_model": "gpt-4o",
                "default_base_url": "https://api.openai.com/v1",
                "description": "OpenAI GPT系列模型",
                "key_placeholder": "sk-..."
            },
            {
                "id": "deepseek",
                "name": "Deepseek",
                "default_model": "deepseek-v4-flash",
                "default_base_url": "https://api.deepseek.com",
                "description": "DeepSeek 高性价比模型（统一默认 V4 Flash）",
                "key_placeholder": "sk-...",
                "model_variants": [
                    {"value": "deepseek-v4-flash", "label": "DeepSeek V4 Flash", "tier": "quick"},
                    {"value": "deepseek-chat", "label": "DeepSeek Chat (Flash 别名)", "tier": "quick"},
                ],
                "dual_model": True,
                "dual_model_hint": "统一使用 V4 Flash；深度任务也走同一模型",
            },
            {
                "id": "qwen",
                "name": "通义千问 (Qwen)",
                "default_model": "qwen-plus",
                "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "description": "阿里云通义千问",
                "key_placeholder": "sk-..."
            },
            {
                "id": "volcengine",
                "name": "火山方舟 Ark (Volcengine)",
                "default_model": "deepseek-v4-flash-260425",
                "default_base_url": "https://ark.cn-beijing.volces.com/api/v3",
                "description": "火山方舟 Ark（OpenAI 兼容）。可托管 DeepSeek 等模型，调用模型名如 deepseek-v4-flash-260425，或用接入点ID ep-xxxxx",
                "key_placeholder": "火山方舟 API Key（如 xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx）",
                "model_variants": [
                    {"value": "deepseek-v4-flash-260425", "label": "DeepSeek V4 Flash (Ark 托管)", "tier": "deep"},
                    {"value": "deepseek-v3-250624", "label": "DeepSeek V3 (Ark 托管)", "tier": "deep"},
                ],
            },
            {
                "id": "local",
                "name": "内网本地模型 (Local / Unsloth)",
                "default_model": "local-model",
                "default_base_url": "http://10.29.193.24:8888/v1",
                "description": "内网部署的 OpenAI 兼容服务（Unsloth / vLLM / Ollama / LM Studio 等）",
                "key_placeholder": "sk-unsloth-..."
            },
            {
                "id": "custom",
                "name": "自定义 (Custom)",
                "default_model": "",
                "default_base_url": "",
                "description": "其他OpenAI兼容的API服务",
                "key_placeholder": "API Key"
            }
        ]
    }


@router.get("/usages")
def list_llm_usages():
    """List registered LLM usage scopes for backend-side routing."""
    try:
        from backend.services.llm_config_service import LLM_USAGE_REGISTRY
        return {
            "usages": [
                {"key": key, "label": label, "description": desc}
                for key, (label, desc) in LLM_USAGE_REGISTRY.items()
            ]
        }
    except Exception as e:
        logger.error(f"Failed to list LLM usages: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{config_id}", response_model=LLMConfigResponse)
def get_llm_config(
    config_id: int,
    db: Session = Depends(get_db)
):
    """Get a specific LLM configuration by ID."""
    config = db.query(LLMConfiguration).filter(LLMConfiguration.id == config_id).first()
    
    if not config:
        raise HTTPException(status_code=404, detail=f"LLM configuration {config_id} not found")
    
    return config_to_response(config, db)


@router.get("/{config_id}/api-key")
def get_llm_config_api_key(
    config_id: int,
    db: Session = Depends(get_db)
):
    """
    Get a MASKED view of the API key for a configuration (§6.4.2).

    出于安全考虑,本端点只返回部分掩码的 key(如 ``sk-****abcd``),不再泄露
    完整明文。后端内部需要真实 key 时(如 POST /{id}/test 调用 provider)
    通过 ``decrypt_llm_key`` 自行解密,不经过此 HTTP 接口。
    """
    config = db.query(LLMConfiguration).filter(LLMConfiguration.id == config_id).first()

    if not config:
        raise HTTPException(status_code=404, detail=f"LLM configuration {config_id} not found")

    full_key = decrypt_llm_key(config.api_key)
    return {
        "id": config.id,
        "model": config.model,
        "base_url": config.base_url,
        "api_key": mask_api_key_partial(full_key),
        "api_key_masked": mask_api_key(full_key),
    }


@router.post("", response_model=LLMConfigResponse)
def create_llm_config(
    data: LLMConfigCreate,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Create a new LLM configuration.

    BYOK (§6.4): 必须认证 —— 配置归属当前请求租户,新行 stamp tenant_id,
    否则 RLS WITH CHECK 会因 tenant_id 与 GUC 不一致而拒绝(且 NOT NULL 约束
    要求非空)。未认证请求由中间件拦截(401);这里再防御性校验一次。

    [2026-08-15 LLM 统一重构] 权威约束：
      - 模型白名单：provider 有 model_variants 白名单时必须命中（deepseek 只允许 flash/chat）；
      - 同源去重：同租户已有 (provider, base_url, is_active) 配置 → 拒绝重复创建。
    """
    tenant_id = _current_tenant_id(request)
    if tenant_id is None:
        # 中间件理应在 POST 上已拒绝无凭证请求;若运维通道(api_key,无租户上下文)
        # 漏到这里,无法 stamp tenant → 拒绝(不允许全局 BYOK 配置)。
        raise HTTPException(
            status_code=401,
            detail="Authentication required to create an LLM configuration",
        )

    # 模型白名单（与 /providers 的 model_variants 同源；未列 provider 不限制）
    _wl_err = _validate_model_whitelist(data.provider, data.model)
    if _wl_err:
        raise HTTPException(status_code=400, detail=_wl_err)
    if data.model_deep:
        _wl_err_deep = _validate_model_whitelist(data.provider, data.model_deep)
        if _wl_err_deep:
            raise HTTPException(status_code=400, detail=_wl_err_deep)

    # 同源去重：同租户同 provider+base_url 的激活配置只允许一条
    _dup = (
        db.query(LLMConfiguration)
        .filter(
            LLMConfiguration.tenant_id == tenant_id,
            LLMConfiguration.provider == (data.provider or ""),
            LLMConfiguration.base_url == ((data.base_url or "").rstrip("/")),
            LLMConfiguration.is_active == "true",
        )
        .first()
    )
    if _dup:
        raise HTTPException(
            status_code=400,
            detail=(
                f"已存在同源配置「{_dup.name}」(id={_dup.id})——同一 provider+base_url "
                "不允许重复创建。请复用现有配置或先停用旧配置。"
            ),
        )

    try:
        # If setting as default, unset current default **within same tenant only**
        if data.is_default:
            db.query(LLMConfiguration).filter(
                LLMConfiguration.is_default == "true",
                LLMConfiguration.tenant_id == tenant_id,
            ).update({"is_default": "false"})

        config = LLMConfiguration(
            name=data.name,
            provider=data.provider,
            description=data.description,
            model=data.model,
            model_deep=data.model_deep,
            usage_scope=data.usage_scope or None,
            base_url=(data.base_url or "").rstrip("/"),
            api_key=encrypt_llm_key(data.api_key),
            is_default="true" if data.is_default else "false",
            is_active="true",
            test_status="pending",
            tenant_id=tenant_id,  # BYOK: stamp 归属租户
        )

        db.add(config)
        db.commit()
        db.refresh(config)

        logger.info(
            f"Created LLM configuration: {config.name} (id={config.id}, tenant={tenant_id})"
        )

        return config_to_response(config, db)

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to create LLM configuration: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{config_id}", response_model=LLMConfigResponse)
def update_llm_config(
    config_id: int,
    data: LLMConfigUpdate,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Update an existing LLM configuration.

    BYOK (§6.4): 配置归属不变 —— 更新不修改 tenant_id,保持原属主。
    跨租户访问由 RLS 自动过滤(读时 USING 隐藏它,写时 WITH CHECK 拒绝)。
    """
    config = db.query(LLMConfiguration).filter(LLMConfiguration.id == config_id).first()

    if not config:
        raise HTTPException(status_code=404, detail=f"LLM configuration {config_id} not found")

    # [2026-08-15 LLM 统一重构] 权威约束（与 create 一致）
    if data.model is not None:
        _wl_err = _validate_model_whitelist(config.provider, data.model)
        if _wl_err:
            raise HTTPException(status_code=400, detail=_wl_err)
    if data.model_deep is not None and data.model_deep:
        _wl_err_deep = _validate_model_whitelist(config.provider, data.model_deep)
        if _wl_err_deep:
            raise HTTPException(status_code=400, detail=_wl_err_deep)
    if data.base_url is not None and (data.base_url or "").rstrip("/") != (config.base_url or ""):
        _owner = getattr(config, "tenant_id", None)
        _dup = (
            db.query(LLMConfiguration)
            .filter(
                LLMConfiguration.id != config_id,
                LLMConfiguration.provider == config.provider,
                LLMConfiguration.base_url == (data.base_url or "").rstrip("/"),
                LLMConfiguration.is_active == "true",
            )
        )
        if _owner is not None:
            _dup = _dup.filter(LLMConfiguration.tenant_id == _owner)
        _dup_row = _dup.first()
        if _dup_row:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"已存在同源配置「{_dup_row.name}」(id={_dup_row.id})——"
                    "同一 provider+base_url 不允许重复配置。"
                ),
            )

    try:
        # If setting as default, unset current default **within same tenant only**
        if data.is_default is True:
            owner = getattr(config, "tenant_id", None)
            q = db.query(LLMConfiguration).filter(
                LLMConfiguration.is_default == "true",
                LLMConfiguration.id != config_id,
            )
            if owner is not None:
                q = q.filter(LLMConfiguration.tenant_id == owner)
            q.update({"is_default": "false"})
        
        # Update fields
        if data.name is not None:
            config.name = data.name
        if data.description is not None:
            config.description = data.description
        if data.model is not None:
            config.model = data.model
        if data.model_deep is not None:
            config.model_deep = data.model_deep or None
        if data.usage_scope is not None:
            config.usage_scope = data.usage_scope or None
        if data.base_url is not None:
            config.base_url = data.base_url
        if data.api_key is not None:
            config.api_key = encrypt_llm_key(data.api_key)
            config.test_status = "pending"  # Reset test status when key changes
        if data.is_default is not None:
            config.is_default = "true" if data.is_default else "false"
        if data.is_active is not None:
            config.is_active = "true" if data.is_active else "false"
        
        db.commit()
        db.refresh(config)
        
        logger.info(f"Updated LLM configuration: {config.name} (id={config.id})")
        
        return config_to_response(config, db)
        
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to update LLM configuration: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{config_id}")
def delete_llm_config(
    config_id: int,
    force: bool = Query(False, description="Unlink all references then delete"),
    db: Session = Depends(get_db)
):
    """
    Delete an LLM configuration.
    Use force=true to unlink accounts / arbitrage profiles first.
    """
    config = db.query(LLMConfiguration).filter(LLMConfiguration.id == config_id).first()
    
    if not config:
        raise HTTPException(status_code=404, detail=f"LLM configuration {config_id} not found")
    
    quick_count = db.query(Account).filter(Account.llm_config_id == config_id).count()
    deep_count = db.query(Account).filter(Account.llm_config_id_deep == config_id).count()
    profiles_count = _count_profile_refs(db, config_id)
    total_refs = quick_count + deep_count + profiles_count
    
    if total_refs > 0 and not force:
        details = []
        if quick_count:
            details.append(f"{quick_count} 个交易员(执行/快速)")
        if deep_count:
            details.append(f"{deep_count} 个交易员(分析/深度)")
        if profiles_count:
            details.append(f"{profiles_count} 个套利档案")
        raise HTTPException(
            status_code=400,
            detail=f"无法删除：{'；'.join(details)} 正在使用。可勾选「强制删除并解除关联」",
        )
    
    try:
        if force and total_refs > 0:
            _unlink_llm_config_refs(db, config_id)
        
        if config.is_default == "true":
            replacement = (
                db.query(LLMConfiguration)
                .filter(
                    LLMConfiguration.id != config_id,
                    LLMConfiguration.is_active == "true",
                )
                .order_by(LLMConfiguration.usage_count.desc())
                .first()
            )
            if replacement:
                replacement.is_default = "true"
                config.is_default = "false"
            elif not force:
                raise HTTPException(
                    status_code=400,
                    detail="无法删除唯一的默认配置，请先创建其他配置或强制删除",
                )
        
        name = config.name
        db.delete(config)
        db.commit()
        
        logger.info(f"Deleted LLM configuration: {name} (id={config_id}, force={force})")
        
        return {"success": True, "message": f"Configuration '{name}' deleted"}
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to delete LLM configuration: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/consolidate-deepseek")
def consolidate_deepseek_endpoint(db: Session = Depends(get_db)):
    """Merge duplicate DeepSeek configs (same API key) into one dual-model entry."""
    from backend.services.llm_config_consolidation import consolidate_deepseek_configs
    result = consolidate_deepseek_configs(db)
    return {"success": True, **result}


@router.post("/{config_id}/test")
def test_llm_config(
    config_id: int,
    db: Session = Depends(get_db)
):
    """
    Test an LLM configuration by making a simple API call.
    """
    import requests
    import json
    
    config = db.query(LLMConfiguration).filter(LLMConfiguration.id == config_id).first()
    
    if not config:
        raise HTTPException(status_code=404, detail=f"LLM configuration {config_id} not found")
    
    api_key = decrypt_llm_key(config.api_key)
    if not api_key:
        return {
            "success": False,
            "message": "API key is required",
            "test_status": "failed"
        }
    
    try:
        base_url = config.base_url.rstrip('/')
        model = config.model
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
        
        # Check for reasoning models that don't support temperature
        model_lower = model.lower()
        is_reasoning_model = any(x in model_lower for x in [
            'gpt-5', 'o1-preview', 'o1-mini', 'o1-', 'o3-', 'o4-',
            'deepseek-r1', 'deepseek-reasoner', 'deepseek-v4-flash',
            'qwq', 'qwen-plus-thinking', 'qwen-max-thinking', 'qwen3-thinking',
            'claude-4', 'claude-sonnet-4-5',
            'gemini-2.5', 'gemini-3', 'gemini-2.0-flash-thinking',
            'grok-3-mini'
        ])
        
        is_o1_series = any(x in model_lower for x in ['o1-preview', 'o1-mini', 'o1-'])
        
        # Build request payload
        if is_o1_series:
            payload = {
                "model": model,
                "messages": [
                    {"role": "user", "content": "Say 'Connection test successful' if you can read this."}
                ]
            }
        else:
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "Say 'Connection test successful' if you can read this."}
                ]
            }
        
        if not is_reasoning_model:
            payload["temperature"] = 0.1
            payload["max_tokens"] = 50
        else:
            payload["max_completion_tokens"] = 50
        
        # Make the test request
        response = requests.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=30
        )
        
        if response.status_code == 200:
            # Update test status
            config.last_tested_at = datetime.now(timezone.utc)
            config.test_status = "success"
            config.test_message = "连接测试成功"
            db.commit()
            
            return {
                "success": True,
                "message": "连接测试成功",
                "test_status": "success"
            }
        else:
            error_message = f"API返回错误: {response.status_code}"
            try:
                error_data = response.json()
                if "error" in error_data:
                    error_message = error_data["error"].get("message", error_message)
            except:
                pass
            
            config.last_tested_at = datetime.now(timezone.utc)
            config.test_status = "failed"
            config.test_message = error_message
            db.commit()
            
            return {
                "success": False,
                "message": error_message,
                "test_status": "failed"
            }
            
    except requests.ConnectionError:
        error_message = f"无法连接到 {config.base_url}"
        config.last_tested_at = datetime.now(timezone.utc)
        config.test_status = "failed"
        config.test_message = error_message
        db.commit()
        return {
            "success": False,
            "message": error_message,
            "test_status": "failed"
        }
    except requests.Timeout:
        error_message = "请求超时，服务可能不可用"
        config.last_tested_at = datetime.now(timezone.utc)
        config.test_status = "failed"
        config.test_message = error_message
        db.commit()
        return {
            "success": False,
            "message": error_message,
            "test_status": "failed"
        }
    except Exception as e:
        error_message = f"测试失败: {str(e)}"
        config.last_tested_at = datetime.now(timezone.utc)
        config.test_status = "failed"
        config.test_message = error_message
        db.commit()
        return {
            "success": False,
            "message": error_message,
            "test_status": "failed"
        }


@router.post("/{config_id}/set-default")
def set_default_llm_config(
    config_id: int,
    db: Session = Depends(get_db)
):
    """Set a configuration as the default."""
    config = db.query(LLMConfiguration).filter(LLMConfiguration.id == config_id).first()
    
    if not config:
        raise HTTPException(status_code=404, detail=f"LLM configuration {config_id} not found")
    
    try:
        # Unset current default
        db.query(LLMConfiguration).filter(
            LLMConfiguration.is_default == "true"
        ).update({"is_default": "false"})
        
        # Set new default
        config.is_default = "true"
        db.commit()
        
        logger.info(f"Set default LLM configuration: {config.name} (id={config.id})")
        
        return {"success": True, "message": f"'{config.name}' is now the default configuration"}
        
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to set default LLM configuration: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{config_id}/usage")
def get_llm_config_usage(
    config_id: int,
    db: Session = Depends(get_db)
):
    """Get usage statistics for a configuration."""
    config = db.query(LLMConfiguration).filter(LLMConfiguration.id == config_id).first()
    
    if not config:
        raise HTTPException(status_code=404, detail=f"LLM configuration {config_id} not found")
    
    # Get accounts using this config (both quick and deep references)
    accounts = db.query(Account).filter(
        (Account.llm_config_id == config_id) | (Account.llm_config_id_deep == config_id)
    ).all()
    
    return {
        "config_id": config.id,
        "config_name": config.name,
        "usage_count": config.usage_count,
        "last_used_at": config.last_used_at.isoformat() if config.last_used_at else None,
        "accounts": [
            {
                "id": a.id,
                "name": a.name,
                "is_active": a.is_active == "true",
                "auto_trading_enabled": a.auto_trading_enabled == "true"
            }
            for a in accounts
        ]
    }


@router.post("/{config_id}/increment-usage")
def increment_llm_config_usage(
    config_id: int,
    db: Session = Depends(get_db)
):
    """
    Increment usage counter for a configuration.
    Called internally when the config is used for an API call.
    """
    config = db.query(LLMConfiguration).filter(LLMConfiguration.id == config_id).first()
    
    if not config:
        raise HTTPException(status_code=404, detail=f"LLM configuration {config_id} not found")
    
    try:
        config.usage_count = (config.usage_count or 0) + 1
        config.last_used_at = datetime.now(timezone.utc)
        db.commit()
        
        return {"success": True, "usage_count": config.usage_count}
        
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to increment usage: {e}")
        raise HTTPException(status_code=500, detail=str(e))
