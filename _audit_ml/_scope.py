# -*- coding: utf-8 -*-
"""[§92 2026-09-11] 薄转发：口径逻辑已提升到 `backend/config/audit_scope.py`。

保留本文件让既有 `_audit_ml/*`（`from _scope import ...`）无需改动。
"""
from backend.config.audit_scope import (  # noqa: F401
    ARCHIVED_ACCOUNT_IDS, DEFAULT_ACCOUNT_ID, EXPERIMENT_ACCOUNT_IDS,
    account_clause, account_id, describe_scope,
)
