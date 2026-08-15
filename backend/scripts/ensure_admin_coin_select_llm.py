"""为管理员租户确保 coin_select 用途的 LLM 可用（幂等，不制造重复配置）。

[2026-08-15 LLM 统一重构]
- 先走 resolve_llm(usage="coin_select")：已有任何可用配置（含租户默认的
  usage_scope 覆盖）即视为已满足，直接退出——不再新建第二条相同配置；
- 仅在解析失败且确实无可用配置时才创建，且**不取消租户默认**、不抢 is_default；
- 幂等判重：同 (tenant, provider, base_url) 已存在 → 复用，不重复建。
"""
from __future__ import annotations

import os

from backend.core.tenant import set_request_identity
from backend.database.connection import SessionLocal
from backend.database.models import LLMConfiguration
from backend.utils.encryption import encrypt_llm_key


def main() -> None:
    tid = int(os.getenv("COIN_SELECT_ADMIN_TENANT_ID", "326") or "326")
    set_request_identity(tid, "admin")

    # 1. 权威解析：已有配置能服务 coin_select 就不建
    try:
        from backend.services.llm_config_service import resolve_llm
        resolved = resolve_llm(usage="coin_select", tenant_id=tid)
        if resolved and getattr(resolved, "api_key", None):
            print(
                "coin_select 已由现有配置覆盖: id=%s name=%s model=%s（无需新建）"
                % (resolved.id, resolved.name, resolved.model)
            )
            return
    except Exception as e:
        print(f"resolve 失败，继续检查: {e}")

    api_key = (os.getenv("DEEPSEEK_API_KEY") or "").strip()
    base_url = (os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").strip().rstrip("/")
    if not api_key:
        raise SystemExit("DEEPSEEK_API_KEY missing in env")

    db = SessionLocal()
    try:
        # 2. 幂等判重：同 (tenant, provider, base_url) 已存在 → 直接复用
        existing = (
            db.query(LLMConfiguration)
            .filter(
                LLMConfiguration.tenant_id == tid,
                LLMConfiguration.provider == "deepseek",
                LLMConfiguration.base_url == base_url,
                LLMConfiguration.is_active == "true",
            )
            .first()
        )
        if existing:
            print(f"复用现有配置 id={existing.id} name={existing.name}")
            return

        # 3. 确实缺配置才创建（不取消租户默认、不设 is_default）
        cfg = LLMConfiguration(
            name="DeepSeek V4 Flash (coin_select 兜底)",
            provider="deepseek",
            description="coin_select 兜底配置（默认配置已覆盖时不会创建本行）",
            model="deepseek-v4-flash",
            model_deep="deepseek-v4-flash",
            usage_scope="coin_select",
            base_url=base_url,
            api_key=encrypt_llm_key(api_key),
            is_default="false",
            is_active="true",
            test_status="pending",
            tenant_id=tid,
        )
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
        print(f"created id={cfg.id} tenant={tid} model={cfg.model} scope={cfg.usage_scope}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
