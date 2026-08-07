"""
DeepSeek 配置合并：同一 API Key 的 Flash / Pro 合并为一条配置（model + model_deep）。
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from backend.database.connection import SessionLocal
from backend.database.models import LLMConfiguration, Account, ArbitrageProfileDB
from backend.utils.encryption import decrypt_llm_key

logger = logging.getLogger(__name__)

_FLASH_HINTS = ("flash", "chat")
_PRO_HINTS = ("pro", "reasoner", "r1")


def _is_flash_model(model: str) -> bool:
    m = (model or "").lower()
    return any(h in m for h in _FLASH_HINTS) and not any(h in m for h in _PRO_HINTS)


def _is_pro_model(model: str) -> bool:
    m = (model or "").lower()
    return any(h in m for h in _PRO_HINTS)


def _remap_config_refs(db: Session, old_id: int, new_id: int) -> None:
    db.query(Account).filter(Account.llm_config_id == old_id).update(
        {Account.llm_config_id: new_id}, synchronize_session=False
    )
    db.query(Account).filter(Account.llm_config_id_deep == old_id).update(
        {Account.llm_config_id_deep: new_id}, synchronize_session=False
    )
    for col in (
        ArbitrageProfileDB.linked_llm_config_id,
        ArbitrageProfileDB.strategy_llm_config_id,
        ArbitrageProfileDB.execution_llm_config_id,
    ):
        db.query(ArbitrageProfileDB).filter(col == old_id).update(
            {col: new_id}, synchronize_session=False
        )


def _pick_primary(candidates: List[LLMConfiguration]) -> LLMConfiguration:
    for c in candidates:
        if c.is_default == "true":
            return c
    flash = [c for c in candidates if _is_flash_model(c.model)]
    if flash:
        return max(flash, key=lambda x: x.usage_count or 0)
    return max(candidates, key=lambda x: x.usage_count or 0)


def _pick_pro_model(candidates: List[LLMConfiguration]) -> Optional[str]:
    for c in candidates:
        if _is_pro_model(c.model):
            return c.model
        if getattr(c, "model_deep", None) and _is_pro_model(c.model_deep):
            return c.model_deep
    return None


def consolidate_deepseek_configs(db: Optional[Session] = None) -> dict:
    """Merge duplicate DeepSeek configs that share the same API key."""
    own_session = db is None
    db = db or SessionLocal()
    merged = 0
    deleted = 0
    try:
        rows = (
            db.query(LLMConfiguration)
            .filter(LLMConfiguration.provider == "deepseek")
            .all()
        )
        groups: Dict[Tuple[str, str], List[LLMConfiguration]] = {}
        for row in rows:
            try:
                key = decrypt_llm_key(row.api_key)
            except Exception:
                key = row.api_key or ""
            sig = ((row.base_url or "").rstrip("/"), key)
            groups.setdefault(sig, []).append(row)

        for (_url, _key), items in groups.items():
            if len(items) < 2:
                continue
            primary = _pick_primary(items)
            pro_model = _pick_pro_model(items)
            flash_model = primary.model
            for c in items:
                if _is_flash_model(c.model):
                    flash_model = c.model
                    break

            # [fix] 仅合并 model_deep 字段（让 Flash 行也能访问 Pro 深度推理）
            # 不再删除 Pro 独立行 —— 前端"分析模型/执行模型"两个下拉框需要独立的 Pro 行可选
            changed = False
            if pro_model and not getattr(primary, "model_deep", None):
                primary.model_deep = pro_model
                changed = True
            if flash_model and primary.model != flash_model and _is_flash_model(flash_model):
                primary.model = flash_model
                changed = True

            # 合并为一条双模型配置，删除同 Key 的重复行
            for secondary in items:
                if secondary.id == primary.id:
                    continue
                _remap_config_refs(db, secondary.id, primary.id)
                if secondary.is_default == "true":
                    primary.is_default = "true"
                    secondary.is_default = "false"
                db.delete(secondary)
                deleted += 1
                changed = True

            if changed:
                merged += 1

        if merged:
            db.commit()
            logger.info(
                "[llm-config] DeepSeek 合并完成: %s 组, 删除 %s 条重复配置",
                merged,
                deleted,
            )
        return {"groups_merged": merged, "configs_deleted": deleted}
    except Exception as e:
        db.rollback()
        logger.warning("[llm-config] DeepSeek 合并失败: %s", e)
        return {"error": str(e)}
    finally:
        if own_session:
            db.close()
