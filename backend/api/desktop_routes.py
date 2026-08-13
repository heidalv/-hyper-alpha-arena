"""桌面客户端更新元数据 API。

更新包本体由 StaticFiles 挂在 /arena-updates/（releases/desktop/）。
本路由提供可读的 JSON，方便前端展示当前可下载版本。
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, Optional

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/desktop", tags=["desktop"])

_REPO_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
)
_UPDATES_DIR = os.path.join(_REPO_ROOT, "releases", "desktop")
_LATEST_YML = os.path.join(_UPDATES_DIR, "latest.yml")


def _parse_latest_yml(text: str) -> Dict[str, Any]:
    """极简解析 electron-builder 产出的 latest.yml（避免强依赖 PyYAML）。"""
    out: Dict[str, Any] = {}
    ver = re.search(r"(?m)^version:\s*(.+)\s*$", text)
    if ver:
        out["version"] = ver.group(1).strip().strip("\"'")
    path_m = re.search(r"(?m)^path:\s*(.+)\s*$", text)
    if path_m:
        out["path"] = path_m.group(1).strip().strip("\"'")
    sha = re.search(r"(?m)^sha512:\s*(.+)\s*$", text)
    if sha:
        out["sha512"] = sha.group(1).strip().strip("\"'")
    size = re.search(r"(?m)^\s*size:\s*(\d+)\s*$", text)
    if size:
        out["size"] = int(size.group(1))
    return out


def get_updates_dir() -> str:
    return _UPDATES_DIR


@router.post("/publish-notify")
def desktop_publish_notify(payload: Dict[str, Any], request: Request) -> Dict[str, Any]:
    """发布流水线回调：向已连接的桌面端广播 desktop_update。

    仅允许本机回环调用；跨机部署时可带 X-Backend-Api-Key 头（与 BACKEND_API_KEY 一致）。
    """
    host = request.client.host if request.client else ""
    key_ok = False
    try:
        from backend.config.settings import BACKEND_API_KEY
        key_ok = bool(BACKEND_API_KEY) and (
            request.headers.get("X-Backend-Api-Key") == BACKEND_API_KEY
        )
    except Exception:
        pass
    if host not in ("127.0.0.1", "::1", "localhost") and not key_ok:
        return {"ok": False, "error": "forbidden: loopback or valid API key required"}
    version = str((payload or {}).get("version") or "").strip()
    path = str((payload or {}).get("path") or "")
    if not version:
        return {"ok": False, "error": "missing version"}
    try:
        from backend.api.ws import notify_desktop_update
        notify_desktop_update(version, path)
    except Exception as exc:
        logger.warning("[desktop] publish-notify 广播调度失败: %s", exc)
        return {"ok": False, "error": str(exc)}
    logger.info("[desktop] publish-notify broadcast version=%s path=%s", version, path or "-")
    return {"ok": True, "version": version, "path": path}


@router.get("/version")
def desktop_version() -> Dict[str, Any]:
    """返回 releases/desktop/latest.yml 中的版本信息。"""
    if not os.path.isfile(_LATEST_YML):
        return {
            "available": False,
            "version": None,
            "path": None,
            "feed_url": "/arena-updates/",
            "updates_dir": _UPDATES_DIR,
            "message": "尚未发布桌面更新包（releases/desktop/latest.yml 不存在）",
        }
    try:
        with open(_LATEST_YML, "r", encoding="utf-8") as f:
            raw = f.read()
        meta = _parse_latest_yml(raw)
        files = [
            name
            for name in os.listdir(_UPDATES_DIR)
            if os.path.isfile(os.path.join(_UPDATES_DIR, name)) and not name.startswith(".")
        ]
        return {
            "available": bool(meta.get("version")),
            "version": meta.get("version"),
            "path": meta.get("path"),
            "sha512": meta.get("sha512"),
            "size": meta.get("size"),
            "files": sorted(files),
            "feed_url": "/arena-updates/",
            "updates_dir": _UPDATES_DIR,
        }
    except Exception as e:
        logger.warning("[desktop] parse latest.yml failed: %s", e)
        return {
            "available": False,
            "version": None,
            "feed_url": "/arena-updates/",
            "error": str(e),
        }
