"""为管理员租户写入 VIP 选币用 LLM（coin_select），密钥来自 .env DEEPSEEK_*。"""
from __future__ import annotations

import os

from backend.core.tenant import set_request_identity
from backend.database.connection import SessionLocal
from backend.database.models import LLMConfiguration
from backend.utils.encryption import encrypt_llm_key


def main() -> None:
    tid = int(os.getenv("COIN_SELECT_ADMIN_TENANT_ID", "326") or "326")
    api_key = (os.getenv("DEEPSEEK_API_KEY") or "").strip()
    base_url = (os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").strip()
    if not api_key:
        raise SystemExit("DEEPSEEK_API_KEY missing in env")

    set_request_identity(tid, "admin")
    db = SessionLocal()
    try:
        existing = (
            db.query(LLMConfiguration)
            .filter(
                LLMConfiguration.tenant_id == tid,
                LLMConfiguration.is_active == "true",
            )
            .all()
        )
        for row in existing:
            scope = (row.usage_scope or "").lower()
            if "coin_select" in scope or not scope:
                print(f"already have config id={row.id} name={row.name} scope={row.usage_scope}")
                return

        # 取消同租户旧默认
        db.query(LLMConfiguration).filter(
            LLMConfiguration.tenant_id == tid,
            LLMConfiguration.is_default == "true",
        ).update({"is_default": "false"})

        cfg = LLMConfiguration(
            name="VIP选币 DeepSeek",
            provider="deepseek",
            description="平台共用 AI 选币（仅 coin_select；交易仍用各账户自备 Key）",
            model="deepseek-chat",
            model_deep="deepseek-reasoner",
            usage_scope="coin_select,assistant,kline_analysis",
            base_url=base_url.rstrip("/"),
            api_key=encrypt_llm_key(api_key),
            is_default="true",
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
