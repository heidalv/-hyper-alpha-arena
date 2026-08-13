"""Range-capable update feed for /arena-updates.

Starlette's StaticFiles (0.38.x) does not honor HTTP Range requests, so
electron-updater cannot perform blockmap differential downloads and falls back
to downloading the full installer every time. This minimal ASGI app serves
releases/desktop with single-range and multi-range support, which unlocks
incremental updates.
"""
from __future__ import annotations

import os
import re
from urllib.parse import unquote

import anyio

_REPO_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
)
UPDATES_DIR = os.path.join(_REPO_ROOT, "releases", "desktop")

_CHUNK = 256 * 1024


def _resolve(rel: str):
    if not rel or ".." in rel or "\\" in rel or "/" in rel:
        return None
    base = os.path.normpath(UPDATES_DIR) + os.sep
    fp = os.path.normpath(os.path.join(UPDATES_DIR, rel))
    if not fp.startswith(base) or not os.path.isfile(fp):
        return None
    return fp


def _content_type(name: str) -> str:
    if name.endswith(".yml"):
        return "text/plain; charset=utf-8"
    return "application/octet-stream"


def _parse_range(value: str, size: int):
    m = re.match(r"^bytes=(.+)$", value.strip())
    if not m or size <= 0:
        return None
    out = []
    for part in m.group(1).split(","):
        part = part.strip()
        mm = re.match(r"^(\d*)-(\d*)$", part)
        if not mm:
            return None
        a, b = mm.group(1), mm.group(2)
        if a == "" and b == "":
            return None
        if a == "":
            suffix = int(b)
            start = max(0, size - suffix)
            end = size - 1
        else:
            start = int(a)
            end = int(b) if b else size - 1
        if start < 0 or start >= size or end < start:
            return None
        out.append((start, min(end, size - 1)))
    return out


def _read_full_chunks(fp: str):
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    fd = os.open(fp, flags)
    try:
        while True:
            chunk = os.read(fd, _CHUNK)
            if not chunk:
                break
            yield chunk
    finally:
        os.close(fd)


def _read_range_chunks(fp: str, start: int, length: int):
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    fd = os.open(fp, flags)
    try:
        os.lseek(fd, start, os.SEEK_SET)
        remaining = length
        while remaining > 0:
            chunk = os.read(fd, min(_CHUNK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        os.close(fd)


def _next_chunk(gen):
    try:
        return next(gen)
    except StopIteration:
        return None


async def _respond(send, status: int, headers: list, body: bytes, more: bool = False) -> None:
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body, "more_body": more})


async def update_feed_asgi(scope, receive, send) -> None:
    if scope["type"] == "lifespan":
        return
    if scope["type"] != "http":
        await _respond(send, 404, [(b"content-type", b"text/plain")], b"not found")
        return

    path = unquote(scope.get("path", ""))
    prefix = "/arena-updates/"
    if not path.startswith(prefix):
        await _respond(send, 404, [(b"content-type", b"text/plain")], b"not found")
        return
    fp = _resolve(path[len(prefix):])
    if not fp:
        await _respond(send, 404, [(b"content-type", b"text/plain")], b"not found")
        return

    size = os.path.getsize(fp)
    name = os.path.basename(fp)
    ctype = _content_type(name)
    raw_headers = {
        k.decode("latin1").lower(): v.decode("latin1")
        for k, v in scope.get("headers") or []
    }
    range_value = raw_headers.get("range")

    if not range_value:
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-length", str(size).encode()),
                (b"content-type", ctype.encode()),
                (b"accept-ranges", b"bytes"),
            ],
        })
        _gen = _read_full_chunks(fp)
        while True:
            chunk = await anyio.to_thread.run_sync(_next_chunk, _gen)
            if chunk is None:
                break
            await send({"type": "http.response.body", "body": chunk, "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})
        return

    ranges = _parse_range(range_value, size)
    # 只支持单段 Range（electron-updater 0.2.3 起 useMultipleRangeRequest=false）。
    # 多段 Range 直接回退完整下载：旧客户端会立刻放弃差分并转全量，避免
    # multipart/byteranges 解析在隧道环境下卡死下载。
    if ranges is None or len(ranges) != 1:
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-length", str(size).encode()),
                (b"content-type", ctype.encode()),
                (b"accept-ranges", b"bytes"),
            ],
        })
        _gen = _read_full_chunks(fp)
        while True:
            chunk = await anyio.to_thread.run_sync(_next_chunk, _gen)
            if chunk is None:
                break
            await send({"type": "http.response.body", "body": chunk, "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})
        return

    start, end = ranges[0]
    length = end - start + 1
    await send({
        "type": "http.response.start",
        "status": 206,
        "headers": [
            (b"content-length", str(length).encode()),
            (b"content-range", f"bytes {start}-{end}/{size}".encode()),
            (b"content-type", ctype.encode()),
            (b"accept-ranges", b"bytes"),
        ],
    })
    _gen = _read_range_chunks(fp, start, length)
    while True:
        chunk = await anyio.to_thread.run_sync(_next_chunk, _gen)
        if chunk is None:
            break
        await send({"type": "http.response.body", "body": chunk, "more_body": True})
    await send({"type": "http.response.body", "body": b"", "more_body": False})
