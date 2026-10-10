# -*- coding: utf-8 -*-
"""删账户时，绑定会话必须先停再删，不能只处理 running。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = (ROOT / "backend/api/account_routes.py").read_text(encoding="utf-8")


def _fn(name: str) -> str:
    start = SRC.find(f"def {name}(")
    assert start > 0, name
    rest = SRC[start + 1:]
    nxt = rest.find("\ndef ")
    return SRC[start:start + 1 + (nxt if nxt > 0 else len(rest))]


def test_delete_account_stops_and_deletes_every_bound_session():
    fn = _fn("delete_account")
    assert "full_auto_service.delete_session(" in fn
    assert 'FullAutoSession.status == "running"' not in fn
    assert '"defensive"' in fn and '"paused"' in fn
    assert "有会话没能停止并删除，账户未移除" in fn
    assert "deleted_sessions" in fn
