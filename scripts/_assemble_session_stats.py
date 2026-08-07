"""Assemble session_stats.py from extracted body."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
body = (ROOT / "backend/services/full_auto/_session_stats_body.tmp").read_text(encoding="utf-8")
header = '''"""Session 绩效汇总 — 从 monolith _update_session_stats 迁出（整改#8 Phase2）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from sqlalchemy.orm import Session


@dataclass
class SessionStatsHost:
    get_trading_account_id: Callable = field(repr=False, default=lambda *a, **k: 0)


def build_session_stats_host(svc) -> SessionStatsHost:
    return SessionStatsHost(get_trading_account_id=svc._get_trading_account_id)


def update_session_stats(
    db: Session,
    session,
    active_ids: list,
    host: SessionStatsHost,
) -> None:
'''
dedented = ["    " + line[8:] if line.startswith("        ") else line for line in body.splitlines()]
out = ROOT / "backend/services/full_auto/session_stats.py"
out.write_text(header + "\n".join(dedented) + "\n", encoding="utf-8")
print(f"wrote {out} ({out.read_text(encoding='utf-8').count(chr(10))} lines)")
