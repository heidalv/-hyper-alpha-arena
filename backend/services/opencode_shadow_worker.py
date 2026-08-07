"""Shadow Worker — worktree + port 8001 paper A/B（Tier C core py 变更）。"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

WORKTREE_ROOT = os.path.join("data", "opencode_worktrees")
_shadow_proc: Optional[subprocess.Popen] = None
_shadow_proposal_id: Optional[int] = None

_COPY_IGNORE = shutil.ignore_patterns(
    ".git",
    "node_modules",
    ".venv",
    "__pycache__",
    "*.pyc",
    "logs",
    "frontend/node_modules",
)


def _repo_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", ".."))


def get_worktree_path(proposal_id: int) -> str:
    return os.path.join(WORKTREE_ROOT, str(proposal_id))


def _extract_shadow_patches(proposal_json: str) -> List[Dict[str, Any]]:
    try:
        payload = json.loads(proposal_json or "{}")
    except Exception:
        payload = {}
    patches = payload.get("patches") or []
    return [p for p in patches if isinstance(p, dict) and (p.get("type") or "").lower() == "shadow_py"]


def _apply_shadow_patches(wt_root: str, patches: List[Dict[str, Any]]) -> List[str]:
    applied: List[str] = []
    for p in patches:
        rel = p.get("path") or p.get("key")
        content = p.get("content")
        if content is None:
            content = p.get("value")
        if not rel or content is None:
            continue
        rel = str(rel).replace("\\", "/").lstrip("/")
        dest = os.path.join(wt_root, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
            f.write(str(content))
        applied.append(rel)
    return applied


def _create_worktree(wt_path: str) -> tuple[bool, str]:
    root = _repo_root()
    if os.path.isdir(wt_path):
        shutil.rmtree(wt_path, ignore_errors=True)

    git_dir = os.path.join(root, ".git")
    if os.path.isdir(git_dir):
        try:
            proc = subprocess.run(
                ["git", "worktree", "add", "--detach", wt_path, "HEAD"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=180,
            )
            if proc.returncode == 0:
                return True, "git_worktree"
            logger.warning("[Shadow] git worktree failed: %s", proc.stderr[:300])
        except Exception as err:
            logger.warning("[Shadow] git worktree error: %s", err)

    try:
        shutil.copytree(root, wt_path, ignore=_COPY_IGNORE, dirs_exist_ok=False)
        return True, "copytree"
    except Exception as err:
        return False, str(err)


def prepare_shadow_worktree(proposal_id: int, db) -> Dict[str, Any]:
    """创建 worktree 并应用 shadow_py 补丁（不启动进程）。"""
    from backend.database.models import OpenCodeEvolutionProposalDB

    row = db.query(OpenCodeEvolutionProposalDB).filter(
        OpenCodeEvolutionProposalDB.id == proposal_id
    ).first()
    if not row:
        return {"ok": False, "error": "proposal not found"}

    shadow_patches = _extract_shadow_patches(row.proposal_json or "{}")
    wt_path = get_worktree_path(proposal_id)
    ok, method_or_err = _create_worktree(wt_path)
    if not ok:
        return {"ok": False, "error": f"worktree create failed: {method_or_err}"}

    applied = _apply_shadow_patches(wt_path, shadow_patches)
    meta_path = os.path.join(wt_path, "data", "shadow_meta.json")
    os.makedirs(os.path.dirname(meta_path), exist_ok=True)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "proposal_id": proposal_id,
                "patch_type": row.patch_type,
                "applied_files": applied,
                "method": method_or_err,
                "prepared_at": time.time(),
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    return {
        "ok": True,
        "proposal_id": proposal_id,
        "worktree": wt_path,
        "method": method_or_err,
        "applied_files": applied,
        "shadow_patch_count": len(shadow_patches),
    }


def run_pytest_in_worktree(proposal_id: int) -> Dict[str, Any]:
    wt = get_worktree_path(proposal_id)
    if not os.path.isdir(wt):
        return {"ok": False, "error": "worktree missing"}
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "backend/tests/test_master_close_guard.py", "-q"],
            cwd=wt,
            capture_output=True,
            text=True,
            timeout=120,
        )
        return {
            "ok": proc.returncode == 0,
            "stdout": proc.stdout[-2000:],
            "stderr": proc.stderr[-1000:],
        }
    except Exception as err:
        return {"ok": False, "error": str(err)}


def start_shadow_server(proposal_id: int, db=None) -> Dict[str, Any]:
    global _shadow_proc, _shadow_proposal_id
    from backend.config.settings import OPENCODE_SHADOW_PORT, OPENCODE_SHADOW_ENABLED

    if not OPENCODE_SHADOW_ENABLED:
        return {
            "ok": False,
            "error": "OPENCODE_SHADOW_ENABLED=false — 请在 .env 设为 true 并重启后端",
        }

    if db is not None:
        prep = prepare_shadow_worktree(proposal_id, db)
        if not prep.get("ok"):
            return prep
    else:
        wt = get_worktree_path(proposal_id)
        if not os.path.isdir(wt):
            return {"ok": False, "error": "worktree missing — call prepare first"}

    wt = get_worktree_path(proposal_id)
    stop_shadow_server()

    env = os.environ.copy()
    env["DEV_MODE"] = "false"
    env["OPENCODE_SHADOW_INSTANCE"] = "1"
    venv_python = sys.executable
    try:
        _shadow_proc = subprocess.Popen(
            [
                venv_python,
                "-m",
                "uvicorn",
                "backend.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(OPENCODE_SHADOW_PORT),
            ],
            cwd=wt,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _shadow_proposal_id = proposal_id
        return {
            "ok": True,
            "port": OPENCODE_SHADOW_PORT,
            "pid": _shadow_proc.pid,
            "proposal_id": proposal_id,
            "worktree": wt,
        }
    except Exception as err:
        return {"ok": False, "error": str(err)}


def stop_shadow_server() -> None:
    global _shadow_proc, _shadow_proposal_id
    if _shadow_proc and _shadow_proc.poll() is None:
        try:
            _shadow_proc.terminate()
            _shadow_proc.wait(timeout=10)
        except Exception:
            _shadow_proc.kill()
    _shadow_proc = None
    _shadow_proposal_id = None


def _fetch_srr(base_url: str, window: str, domain: str) -> Dict[str, Any]:
    url = f"{base_url.rstrip('/')}/api/analytics/strategy-runtime"
    with httpx.Client(timeout=15.0, trust_env=False) as client:
        resp = client.get(url, params={"window": window, "domain": domain})
        resp.raise_for_status()
        return resp.json()


def compare_shadow_srr(*, window: str = "24h", domain: str = "ai") -> Dict[str, Any]:
    """主实例 vs Shadow 实例 SRR 对比（Shadow 须已启动）。"""
    from backend.config.settings import OPENCODE_SHADOW_PORT

    st = shadow_status()
    if not st.get("running"):
        return {"ok": False, "error": "shadow not running"}

    main_base = "http://127.0.0.1:8000"
    shadow_base = f"http://127.0.0.1:{OPENCODE_SHADOW_PORT}"
    try:
        main_srr = _fetch_srr(main_base, window, domain)
        shadow_srr = _fetch_srr(shadow_base, window, domain)
    except Exception as err:
        return {"ok": False, "error": str(err), "shadow_status": st}

    def _pick(d: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "win_rate": float(d.get("win_rate") or 0),
            "total_pnl": float(d.get("total_pnl") or 0),
            "total_closed": int(d.get("total_closed") or 0),
            "master_close_loss_ratio": float(d.get("master_close_loss_ratio") or 0),
        }

    m = _pick(main_srr)
    s = _pick(shadow_srr)
    delta = {k: round(s[k] - m[k], 6) if isinstance(m[k], float) else s[k] - m[k] for k in m}
    verdict = "neutral"
    if s["win_rate"] > m["win_rate"] + 0.02 and s["total_pnl"] >= m["total_pnl"]:
        verdict = "shadow_better"
    elif s["win_rate"] < m["win_rate"] - 0.02 or s["total_pnl"] < m["total_pnl"] - 1.0:
        verdict = "main_better"

    return {
        "ok": True,
        "proposal_id": _shadow_proposal_id,
        "window": window,
        "domain": domain,
        "main": m,
        "shadow": s,
        "delta": delta,
        "verdict": verdict,
        "shadow_status": st,
    }


def shadow_status() -> Dict[str, Any]:
    from backend.config.settings import OPENCODE_SHADOW_PORT, OPENCODE_SHADOW_ENABLED

    running = _shadow_proc is not None and _shadow_proc.poll() is None
    return {
        "enabled": OPENCODE_SHADOW_ENABLED,
        "running": running,
        "port": OPENCODE_SHADOW_PORT,
        "pid": _shadow_proc.pid if running and _shadow_proc else None,
        "proposal_id": _shadow_proposal_id if running else None,
    }
