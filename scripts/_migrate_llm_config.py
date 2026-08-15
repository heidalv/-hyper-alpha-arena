# -*- coding: utf-8 -*-
"""[2026-08-15 LLM 统一重构 P2] 一次性数据治理：

1. 删除重复的「VIP选币 DeepSeek」(id=79)：与租户默认 id=17 同 key/URL/model，
   且 id=17 的 usage_scope 已含 coin_select（冗余配置制造者 =
   ensure_admin_coin_select_llm.py 旧逻辑，已重写为解析优先+幂等去重）。
2. 明文 api_key → Fernet 加密（id=17 此前为明文，与 id=79 加密存储不一致）。
"""
import os
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)) + "/..")
sys.path.insert(0, ".")
sys.path.append("backend")

from backend.database.connection import SessionLocal
from backend.database.models import LLMConfiguration
from backend.utils.encryption import encrypt_llm_key, is_encrypted


def main() -> None:
    db = SessionLocal()
    try:
        try:
            db.connection().exec_driver_sql("SET app.is_admin = 'on'")
        except Exception:
            pass

        rows = db.query(LLMConfiguration).all()
        print("迁移前共 %d 条配置:" % len(rows))
        for r in rows:
            print("  id=%s name=%s provider=%s model=%s tenant=%s default=%s encrypted=%s" % (
                r.id, r.name, r.provider, r.model, r.tenant_id, r.is_default,
                is_encrypted(r.api_key or ""),
            ))

        # 1. 删除重复的 coin_select 专项配置（默认配置已覆盖该用途）
        dup = (
            db.query(LLMConfiguration)
            .filter(
                LLMConfiguration.id == 79,
                LLMConfiguration.provider == "deepseek",
            )
            .first()
        )
        if dup:
            # 安全前置校验：租户默认配置确实含 coin_select
            default_cfg = (
                db.query(LLMConfiguration)
                .filter(
                    LLMConfiguration.tenant_id == dup.tenant_id,
                    LLMConfiguration.is_default == "true",
                    LLMConfiguration.is_active == "true",
                )
                .first()
            )
            covers = bool(
                default_cfg
                and "coin_select" in ((default_cfg.usage_scope or "").lower())
            )
            if covers:
                print("删除重复配置 id=79 name=%s（默认 id=%s 已覆盖 coin_select）" % (
                    dup.name, default_cfg.id,
                ))
                db.delete(dup)
            else:
                print("跳过删除 id=79：默认配置未覆盖 coin_select，先人工确认")
        else:
            print("id=79 不存在，跳过删除")

        # 2. 明文 key → 加密
        encrypted_n = 0
        for r in db.query(LLMConfiguration).filter(
            LLMConfiguration.api_key.isnot(None),
            LLMConfiguration.api_key != "",
        ).all():
            if not is_encrypted(r.api_key or ""):
                r.api_key = encrypt_llm_key(r.api_key)
                encrypted_n += 1
                print("加密 key: id=%s name=%s" % (r.id, r.name))
        print("明文 key 加密 %d 条" % encrypted_n)

        db.commit()
        print("迁移完成")
    except Exception as e:
        db.rollback()
        print("迁移失败: %s" % e)
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
