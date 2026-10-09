"""h586 — 待命采集器的**前置条件体检**（只读，R101）。

目的：在写"待命采集器"之前，确认三件事：
  1. 可用的 WebSocket / 代理库；
  2. 本机代理端口（`.env` 里 `MARKET_DATA_HTTP_PROXY=http://127.0.0.1:1080`）是否真的**能转发**
     ——本项目有过"本地 1080 回 `200 Connection established` 但零转发"的停摆史 ✗，
     所以必须**在隧道内做一次真实 TLS/WS 握手**才算通过；
  3. asterdex 的 WS 端点是否可达（fstream）。
**只读**：只建立连接并打印结果，不写任何表。

用法：python scripts/h586_ws_prereq.py
"""
from __future__ import annotations

import sys as _sys

try:  # 管道输出（GBK 控制台）遇到非 ASCII 不再崩溃
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import importlib
import socket
import sys

MODULES = ("websockets", "websocket", "aiohttp", "httpx", "socksio", "python_socks",
           "psycopg", "dotenv")
PROXIES = ("http://127.0.0.1:1080", "socks5://127.0.0.1:1080")
HOSTS = ("fstream.asterdex.com", "fapi.asterdex.com")


def main() -> int:
    print("=" * 84)
    print("1) 库可用性")
    print("=" * 84)
    for m in MODULES:
        try:
            mod = importlib.import_module(m)
            print(f"  ✓ {m:<14} {getattr(mod, '__version__', '?')}")
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ {m:<14} ({type(exc).__name__})")

    print("\n" + "=" * 84)
    print("2) DNS 解析（直连可达性）")
    print("=" * 84)
    for hst in HOSTS:
        try:
            ip = socket.gethostbyname(hst)
            print(f"  ✓ {hst} -> {ip}")
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ {hst} 解析失败: {type(exc).__name__}")

    print("\n" + "=" * 84)
    print("3) 通过代理做一次**真实 TLS 握手**（只测 fapi 的 443，不做业务请求）")
    print("=" * 84)
    try:
        import httpx
    except Exception:  # noqa: BLE001
        print("  ✗ 无 httpx，跳过")
        return 0
    for px in PROXIES:
        for hst in HOSTS:
            try:
                with httpx.Client(proxy=px, timeout=8.0, verify=True) as cli:
                    r = cli.get(f"https://{hst}/", headers={"User-Agent": "h586-prereq"})
                print(f"  ✓ {px} -> {hst}: HTTP {r.status_code}")
            except Exception as exc:  # noqa: BLE001
                msg = str(exc)
                print(f"  ✗ {px} -> {hst}: {type(exc).__name__}: {msg[:70]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
