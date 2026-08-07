#!/usr/bin/env python3
"""将 docs/opencode/prompts 同步到 backend/prompts/（供 opencode_bridge 读取）。"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(ROOT, "docs", "opencode", "prompts")
DEST = os.path.join(ROOT, "backend", "prompts")
MANIFEST = os.path.join(SRC, "manifest.yaml")


def _read_manifest() -> dict:
    with open(MANIFEST, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _copy_file(rel_path: str) -> str:
    src = os.path.join(SRC, rel_path)
    dest = os.path.join(DEST, rel_path.replace("/", os.sep))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copy2(src, dest)
    with open(src, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()[:16]
    return digest


def emit_all(*, quiet: bool = False) -> int:
    """同步 manifest 中全部 layer/task 到 backend/prompts/。供 CLI 与 startup 调用。"""
    if not os.path.isdir(SRC):
        if not quiet:
            print(f"[emit] source missing: {SRC}", file=sys.stderr)
        return 1

    manifest = _read_manifest()
    copied = 0
    for section in ("layers", "tasks"):
        for item in manifest.get(section, []):
            rel = item.get("path")
            if not rel:
                continue
            digest = _copy_file(rel)
            if not quiet:
                print(f"[emit] {rel} sha256:{digest}")
            copied += 1

    dest_manifest = os.path.join(DEST, "manifest.yaml")
    os.makedirs(DEST, exist_ok=True)
    shutil.copy2(MANIFEST, dest_manifest)
    if not quiet:
        print(f"[emit] done: {copied} files -> {DEST}")
    return 0


def main() -> int:
    return emit_all()


if __name__ == "__main__":
    raise SystemExit(main())
