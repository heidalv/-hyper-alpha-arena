"""assistant_conversation_service 单元测试。"""
import uuid

import pytest

from backend.database.models import AlphaAssistantConversation, User
from backend.services.assistant_conversation_service import (
    append_message,
    create_conversation,
    get_messages_for_conversation,
    list_conversations,
    resolve_or_create_conversation,
)


@pytest.fixture
def default_user(db_session):
    user = db_session.query(User).filter(User.username == "default").first()
    if not user:
        user = User(username="default", email="default@local", is_active="true")
        db_session.add(user)
        db_session.commit()
    return user


def test_create_and_list_conversation(db_session, default_user):
    sid = str(uuid.uuid4())
    conv = create_conversation(db_session, user_id=default_user.id, session_uuid=sid, seed_welcome=False)
    append_message(db_session, conv, role="user", content="OpenCode 在线吗")
    append_message(db_session, conv, role="assistant", content="在线")
    rows = list_conversations(db_session, user_id=default_user.id)
    assert any(r["session_uuid"] == sid for r in rows)
    msgs = get_messages_for_conversation(db_session, sid, user_id=default_user.id)
    assert msgs is not None
    assert len(msgs) == 2


def test_resolve_or_create_reuses_session(db_session, default_user):
    sid = str(uuid.uuid4())
    create_conversation(db_session, user_id=default_user.id, session_uuid=sid)
    again = resolve_or_create_conversation(db_session, session_uuid=sid, user_id=default_user.id)
    assert again.session_uuid == sid
    count = db_session.query(AlphaAssistantConversation).filter_by(session_uuid=sid).count()
    assert count == 1
