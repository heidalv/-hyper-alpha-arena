"""
Obsidian Vault API Routes
=========================
把项目里真实的 `obsidian_vault/` 暴露给前端 Obsidia 工作台，实现"网页里跑真 Obsidian"。

提供 4 个只读接口:
- GET /api/vault/tree            目录树(文件夹 + .md/.canvas 文件)
- GET /api/vault/file?path=      单个 markdown: 原始内容 + 解析后的 frontmatter + 正文
- GET /api/vault/index           全库索引(每篇 frontmatter + wikilink 出链/反链) —— 供 Graph & Dataview
- GET /api/vault/canvas?path=     透传 .canvas JSON

安全: 所有 path 参数都被限制在 VAULT_ROOT 内(路径穿越防护)。
性能: index 结果按"文件数 + 最新 mtime"签名做内存缓存, vault 没变化时直接命中。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/vault", tags=["Obsidian Vault"])

# ── vault 根目录: backend/ 上一级 = Hyper-Alpha-Arena/, 再 /obsidian_vault ──
_BACKEND_DIR = Path(__file__).resolve().parent.parent  # backend/
_PROJECT_DIR = _BACKEND_DIR.parent                     # Hyper-Alpha-Arena/
VAULT_ROOT = (_PROJECT_DIR / "obsidian_vault").resolve()

_IGNORE_DIRS = {".obsidian", ".git", ".trash", "__pycache__"}
_MD_SUFFIX = ".md"
_CANVAS_SUFFIX = ".canvas"

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


# ---------------------------------------------------------------------------
# 路径安全
# ---------------------------------------------------------------------------
def _safe_resolve(rel: str) -> Path:
    """把相对路径安全地解析到 VAULT_ROOT 内, 越界直接 400。"""
    rel = (rel or "").strip().replace("\\", "/").lstrip("/")
    target = (VAULT_ROOT / rel).resolve()
    if target != VAULT_ROOT and VAULT_ROOT not in target.parents:
        raise HTTPException(status_code=400, detail="非法路径(越界)")
    return target


def _rel_of(p: Path) -> str:
    """相对 VAULT_ROOT 的 POSIX 风格路径, 便于前端统一处理。"""
    return p.resolve().relative_to(VAULT_ROOT).as_posix()


def _ensure_vault() -> None:
    if not VAULT_ROOT.exists():
        raise HTTPException(status_code=404, detail=f"vault 不存在: {VAULT_ROOT}")


# ---------------------------------------------------------------------------
# frontmatter 解析(YAML 子集, 无第三方依赖)
# ---------------------------------------------------------------------------
def _parse_scalar(v: str) -> Any:
    v = v.strip()
    if v == "":
        return ""
    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
        return v[1:-1]
    low = v.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("null", "~"):
        return None
    if re.fullmatch(r"-?\d+", v):
        try:
            return int(v)
        except ValueError:
            return v
    if re.fullmatch(r"-?\d*\.\d+", v):
        try:
            return float(v)
        except ValueError:
            return v
    return v


def _split_list(inner: str) -> List[str]:
    """拆分 [a, "b, c", d] 里的元素, 尊重引号内的逗号。"""
    items: List[str] = []
    buf = ""
    quote: Optional[str] = None
    for ch in inner:
        if quote:
            buf += ch
            if ch == quote:
                quote = None
        elif ch in ('"', "'"):
            quote = ch
            buf += ch
        elif ch == ",":
            items.append(buf)
            buf = ""
        else:
            buf += ch
    if buf.strip():
        items.append(buf)
    return items


def parse_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    """返回 (frontmatter dict, 去掉 frontmatter 的正文)。"""
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    block = m.group(1)
    body = text[m.end():]
    meta: Dict[str, Any] = {}
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            meta[key] = [_parse_scalar(p) for p in _split_list(inner)] if inner else []
        else:
            meta[key] = _parse_scalar(val)
    return meta, body


def _extract_wikilinks(body: str) -> List[str]:
    """抽取 [[target]] / [[target|alias]] / [[target#heading]] 的 target。"""
    out: List[str] = []
    for raw in _WIKILINK_RE.findall(body):
        target = raw.split("|", 1)[0].split("#", 1)[0].strip()
        if target:
            out.append(target)
    return out


# ---------------------------------------------------------------------------
# 目录树
# ---------------------------------------------------------------------------
def _build_tree(directory: Path) -> Dict[str, Any]:
    children: List[Dict[str, Any]] = []
    try:
        entries = sorted(
            directory.iterdir(),
            key=lambda p: (not p.is_dir(), p.name.lower()),
        )
    except OSError:
        entries = []
    for entry in entries:
        if entry.name in _IGNORE_DIRS:
            continue
        if entry.is_dir():
            node = _build_tree(entry)
            if node["children"]:  # 跳过空目录
                children.append(node)
        elif entry.suffix in (_MD_SUFFIX, _CANVAS_SUFFIX):
            children.append({
                "type": "canvas" if entry.suffix == _CANVAS_SUFFIX else "file",
                "name": entry.stem,
                "path": _rel_of(entry),
            })
    return {
        "type": "folder",
        "name": directory.name,
        "path": _rel_of(directory) if directory != VAULT_ROOT else "",
        "children": children,
    }


@router.get("/tree")
def get_tree() -> Dict[str, Any]:
    """返回 vault 目录树(忽略 .obsidian 等)。"""
    _ensure_vault()
    tree = _build_tree(VAULT_ROOT)
    tree["name"] = VAULT_ROOT.name
    return tree


# ---------------------------------------------------------------------------
# 单文件
# ---------------------------------------------------------------------------
@router.get("/file")
def get_file(path: str = Query(..., description="相对 vault 根的 .md 路径")) -> Dict[str, Any]:
    _ensure_vault()
    target = _safe_resolve(path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail=f"文件不存在: {path}")
    if target.suffix != _MD_SUFFIX:
        raise HTTPException(status_code=400, detail="只支持 .md 文件")
    raw = target.read_text(encoding="utf-8", errors="replace")
    frontmatter, body = parse_frontmatter(raw)
    return {
        "path": _rel_of(target),
        "name": target.stem,
        "frontmatter": frontmatter,
        "body": body,
        "raw": raw,
        "outlinks": _extract_wikilinks(body),
        "mtime": target.stat().st_mtime,
    }


# ---------------------------------------------------------------------------
# canvas 透传
# ---------------------------------------------------------------------------
@router.get("/canvas")
def get_canvas(path: str = Query(..., description="相对 vault 根的 .canvas 路径")) -> Dict[str, Any]:
    _ensure_vault()
    target = _safe_resolve(path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail=f"canvas 不存在: {path}")
    if target.suffix != _CANVAS_SUFFIX:
        raise HTTPException(status_code=400, detail="只支持 .canvas 文件")
    try:
        data = json.loads(target.read_text(encoding="utf-8", errors="replace") or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"canvas 解析失败: {exc}") from exc
    return {"path": _rel_of(target), "name": target.stem, "data": data}


# ---------------------------------------------------------------------------
# 全库索引(TTL 缓存)
# ---------------------------------------------------------------------------
# 说明: vault 由后端 export_to_obsidian 持续写入, 若按 mtime 签名会频繁失效,
# 每次都重读上千文件并阻塞线程池。改用 TTL 缓存: 最多每 INDEX_TTL 秒重建一次,
# 知识库浏览容忍分钟级陈旧。
import time as _time

INDEX_TTL_SEC = 60.0
_index_cache: Dict[str, Any] = {"built_at": 0.0, "payload": None}


def _build_index() -> Dict[str, Any]:
    notes: List[Dict[str, Any]] = []
    stem_to_path: Dict[str, str] = {}
    rel_to_note: Dict[str, Dict[str, Any]] = {}

    for p in VAULT_ROOT.rglob(f"*{_MD_SUFFIX}"):
        if any(part in _IGNORE_DIRS for part in p.parts):
            continue
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        frontmatter, body = parse_frontmatter(raw)
        rel = _rel_of(p)
        folder = p.parent.relative_to(VAULT_ROOT).as_posix() if p.parent != VAULT_ROOT else ""
        note = {
            "path": rel,
            "name": p.stem,
            "folder": folder,
            "frontmatter": frontmatter,
            "outlinks_raw": _extract_wikilinks(body),
            "outlinks": [],   # 解析后填充
            "backlinks": [],  # 反链, 二次遍历填充
            "mtime": p.stat().st_mtime,
            "size": p.stat().st_size,
        }
        notes.append(note)
        stem_to_path.setdefault(p.stem, rel)
        rel_to_note[rel] = note

    # 解析 outlinks: 优先按相对路径, 否则按 basename 匹配
    for note in notes:
        resolved: List[str] = []
        for link in note["outlinks_raw"]:
            key = link.replace("\\", "/").lstrip("/")
            candidate = key if key.endswith(_MD_SUFFIX) else f"{key}{_MD_SUFFIX}"
            if candidate in rel_to_note:
                resolved.append(candidate)
            else:
                stem = Path(key).stem
                if stem in stem_to_path:
                    resolved.append(stem_to_path[stem])
        # 去重保序
        seen = set()
        note["outlinks"] = [x for x in resolved if not (x in seen or seen.add(x))]

    # 反链
    for note in notes:
        for target in note["outlinks"]:
            tgt = rel_to_note.get(target)
            if tgt is not None and note["path"] not in tgt["backlinks"]:
                tgt["backlinks"].append(note["path"])

    return {
        "vault": VAULT_ROOT.name,
        "count": len(notes),
        "notes": notes,
    }


@router.get("/index")
def get_index(refresh: bool = Query(False, description="强制重建索引")) -> Dict[str, Any]:
    """全库索引: 每篇 frontmatter + 出链/反链, 供 Graph 与 Dataview 使用。TTL 缓存(默认 60s)。"""
    _ensure_vault()
    now = _time.time()
    if (
        not refresh
        and _index_cache["payload"] is not None
        and (now - _index_cache["built_at"]) < INDEX_TTL_SEC
    ):
        return _index_cache["payload"]
    payload = _build_index()
    _index_cache["built_at"] = now
    _index_cache["payload"] = payload
    return payload
