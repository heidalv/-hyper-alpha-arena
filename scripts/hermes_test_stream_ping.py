"""Sidecar 流式连通性快速探测（小 prompt，应在 2 分钟内返回）。"""
from __future__ import annotations

import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

from backend.services.opencode_bridge import (
    collect_http_agent_stream_text,
    health_check,
    _agent_plan,
    _model,
)

print("sidecar:", health_check(), flush=True)
t0 = time.time()
raw, err = collect_http_agent_stream_text(
    system_prompt="You are a test assistant. Reply ONLY with JSON: {\"ok\": true, \"msg\": \"stream works\"}",
    user_text="ping",
    agent=_agent_plan(),
    model_slug=_model(),
    session_title="Hermes stream ping",
    log_prefix="Hermes:ping",
    idle_timeout_s=120.0,
    max_duration_s=300.0,
)
print(json.dumps({
    "elapsed_s": round(time.time() - t0, 1),
    "error": err,
    "raw_preview": (raw or "")[:200],
}, ensure_ascii=False), flush=True)
