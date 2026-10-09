# -*- coding: utf-8 -*-
"""Agent Wall API（`/api/agent-wall/*`）—— 画布式多 Agent 分析墙（全部只读）。

设计：`docs/Agent画布模块设计_20260919.md`；排查：`docs/Agent运行排查_20260919.md`

三个端点（**只允许这三个**，前端单页轮询也只用前两个）：
  GET /api/agent-wall/state            画布骨架：节点 + 边 + 分组摘要（2~3s 轮询）
  GET /api/agent-wall/tail?nodes=a,b   多节点**增量**分析流（游标在服务端按文件 offset 维护）
  GET /api/agent-wall/audit            断链与逻辑错误清单（现场判定 + 已核实结构性缺陷）

为什么不做成"每节点一个端点"：本项目后端是单进程、GIL 长期贴 1 核（`services/gil_watch.py` 实测），
24 个节点各自轮询会把请求数放大 24 倍 —— 这正是本轮"前端刷新慢"的根因之一。
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent-wall", tags=["agent-wall"])


@router.get("/state")
def agent_wall_state() -> Dict[str, Any]:
    """画布骨架（节点/边/分组）。节点状态复用 `job_registry` 的 stale 判定。"""
    from backend.services import agent_wall

    return agent_wall.build_state()


@router.get("/tail")
def agent_wall_tail(
    nodes: str = Query("", description="逗号分隔的节点 id；为空则返回空结果（不猜、不默认全网）"),
) -> Dict[str, Any]:
    """多节点增量分析流；一次请求返回全部请求节点的**新增**行。"""
    from backend.services import agent_wall

    ids = [x.strip() for x in (nodes or "").split(",") if x.strip()]
    # 上限保护：一次最多 12 个节点（视口内可见数量级），防止被当成"全网拉取"接口
    return agent_wall.tail(ids[:12])


@router.get("/audit")
def agent_wall_audit() -> Dict[str, Any]:
    """断链 / 逻辑错误清单（kind=live 现场重算，kind=static_verified 为排查实测）。"""
    from backend.services import agent_wall

    return agent_wall.audit()
