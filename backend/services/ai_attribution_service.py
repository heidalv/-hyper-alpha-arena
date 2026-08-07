"""AI Attribution Service — Phase 2 存根（已废弃）

Phase 2 重构说明：
- ai_attribution（归因分析对话）功能已由 ai_decision_service 的通用分析接口替代。
- 保留存根函数签名以避免 analytics_routes.py 产生 ImportError。
"""
import logging
import warnings
from typing import Dict, List, Optional, Any, Generator
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

warnings.warn(
    "ai_attribution_service is deprecated (Phase 2), use ai_decision_service",
    DeprecationWarning, stacklevel=2,
)


def generate_attribution_analysis_stream(
    db: Session,
    account_id: int,
    user_message: str,
    conversation_id: Optional[int] = None,
) -> Generator[str, None, None]:
    """Stub: AI 归因分析已废弃（Phase 2）。"""
    logger.warning("generate_attribution_analysis_stream called on stub — Phase 2 deprecated")
    yield 'data: {"type":"error","message":"AI 归因分析功能已在 Phase 2 移除，请使用 AI 决策分析接口。"}\n\n'


def get_attribution_conversations(db: Session, user_id: int = 1) -> List[Dict]:
    """Stub: 返回空列表。"""
    return []


def get_attribution_messages(db: Session, conversation_id: int, user_id: int = 1) -> List[Dict]:
    """Stub: 返回空列表。"""
    return []
