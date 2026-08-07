from __future__ import annotations

from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.config.prompt_templates import DEFAULT_PROMPT_TEMPLATE, PRO_PROMPT_TEMPLATE, HYPERLIQUID_PROMPT_TEMPLATE
from repositories import prompt_repo

SYSTEM_USER = "system"


def seed_prompt_templates(db: Session) -> None:
    """Ensure default prompt templates exist in the database."""
    # Clean up legacy table if it still exists
    try:
        db.execute(text("DROP TABLE IF EXISTS model_prompt_overrides"))
        db.commit()
    except Exception:
        db.rollback()

    # Shared required placeholders for all system templates
    _SYSTEM_REQUIRED_PLACEHOLDERS = [
        "factor_engine_status",
        "adaptive_trading_summary",
        "factors_summary",
        "historical_analogies",
        "kline_technical_analysis",
        "confidence_calibration",
    ]

    templates_to_seed = [
        {
            "key": "default",
            "name": "Default Prompt",
            "description": "Baseline prompt used for AI trading decisions.",
            "template_text": DEFAULT_PROMPT_TEMPLATE,
            "required_placeholders": _SYSTEM_REQUIRED_PLACEHOLDERS,
            "is_legacy": "false",
        },
        {
            "key": "pro",
            "name": "Pro Prompt",
            "description": "Structured prompt inspired by Alpha Arena with richer context.",
            "template_text": PRO_PROMPT_TEMPLATE,
            "required_placeholders": _SYSTEM_REQUIRED_PLACEHOLDERS,
            "is_legacy": "false",
        },
        {
            "key": "hyperliquid",
            "name": "Hyperliquid Prompt",
            "description": "Specialized prompt for Hyperliquid perpetual contract trading with detailed margin and leverage information.",
            "template_text": HYPERLIQUID_PROMPT_TEMPLATE,
            "required_placeholders": _SYSTEM_REQUIRED_PLACEHOLDERS,
            "is_legacy": "false",
        },
    ]

    updated = False

    for item in templates_to_seed:
        existing = prompt_repo.get_template_by_key(db, item["key"])
        if not existing:
            prompt_repo.create_template(
                db,
                key=item["key"],
                name=item["name"],
                description=item["description"],
                template_text=item["template_text"],
                system_template_text=item["template_text"],
                updated_by=SYSTEM_USER,
                required_placeholders=item.get("required_placeholders"),
                is_legacy=item.get("is_legacy", "true"),
            )
            updated = True
        else:
            has_changes = False
            
            # Check if user has modified the template before updating system template
            user_has_modified = (existing.template_text != existing.system_template_text)
            
            if existing.name != item["name"]:
                existing.name = item["name"]
                has_changes = True
            if existing.description != item["description"]:
                existing.description = item["description"]
                has_changes = True
            if existing.system_template_text != item["template_text"]:
                existing.system_template_text = item["template_text"]
                has_changes = True
                
                # If user has not modified the template, automatically update template_text
                if not user_has_modified:
                    existing.template_text = item["template_text"]

            # Always ensure system templates are marked non-legacy
            if existing.is_legacy != "false":
                existing.is_legacy = "false"
                has_changes = True
            if not existing.required_placeholders:
                existing.required_placeholders = item.get("required_placeholders")
                has_changes = True

            if has_changes:
                existing.updated_by = SYSTEM_USER
                db.add(existing)
                updated = True

    if updated:
        db.commit()
