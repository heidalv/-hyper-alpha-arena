"""h540：**直连自建 SOCKS5 的可行性探针**——能否绕过死掉的 Shadowsocks 节点？

背景：SS 客户端当前选中的节点 `szyd.szst.net:62011` **TCP 不通**（h539 实测），
所以本地 1080 端口虽然接受 CONNECT，数据面却一个字节都过不去 ⇒ 采集器 WS 全断。
但 `scripts/binance_socks_chain.py` 里还有一条**自建 SOCKS5**（`8.211.172.14:55620`，
带认证），而该地址**从本机直连是通的**（早先 Test-NetConnection 实测 True）。

若它能用，就能**只给行情采集器换出口**（采集器读 `L1_PROXY`），
**完全不动用户的 VPN/其它流量** ⇒ 立刻恢复行情与车道。

本脚本只做探测（不写任何配置、不改任何进程）：
  1. 从 `binance_socks_chain.py` 解析 SOCKS5 主机/端口/认证（**不打印密码**）；
  2. 直连该 SOCKS5，做完整的认证握手；
  3. 在隧道里对 `fapi.asterdex.com:443` 做 TLS + `GET /fapi/v1/ping`；
  4. 在隧道里对 `fstream.asterdex.com:443` 做 TLS（行情 WS 的主机）。

用法：python scripts/h540_direct_socks_probe.py
"""
from __future__ import annotations

import pathlib
import re
import socket
import ssl
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]
CHAIN = ROOT / "scripts" / "binance_socks_chain.py"


def parse_creds() -> tuple[str, int, bytes, bytes]:
    src = CHAIN.read_text(encoding="utf-8", errors="replace")
    host = re.search(r'SOCKS\s*=\s*\(\s*"([^"]+)"\s*,\s*(\d+)\s*\)', src)
    user = re.search(r'SOCKS_USER\s*=\s*b"([^"]*)"', src)
    pwd = re.search(r'SOCKS_PASS\s*=\s*b"([^"]*)"', src)
    if not (host and user and pwd):
        raise SystemExit("无法从 binance_socks_chain.py 解析出 SOCKS5 凭据")
    return (host.group(1), int(host.group(2)),
            user.group(1).encode(), pwd.group(1).encode())


def socks5_connect(sock: socket.socket, user: bytes, pwd: bytes,
                   dest_host: str, dest_port: int) -> None:
    """完整 SOCKS5（含用户名/密码认证）+ CONNECT。"""
    sock.sendall(b"\x05\x01\x02")                     # VER=5, NMETHODS=1, USERPASS
    rep = sock.recv(2)
    if len(rep) < 2 or rep[1] != 0x02:
        raise ConnectionError(f"服务端不接受用户名/密码认证: {rep!r}")
    sock.sendall(b"\x01" + bytes([len(user)]) + user
                 + bytes([len(pwd)]) + pwd)
    auth = sock.recv(2)
    if len(auth) < 2 or auth[1] != 0x00:
        raise ConnectionError(f"认证失败: {auth!r}")
    hb = dest_host.encode()
    sock.sendall(b"\x05\x01\x00\x03" + bytes([len(hb)]) + hb
                 + dest_port.to_bytes(2, "big"))
    resp = sock.recv(4)
    if len(resp) < 4 or resp[1] != 0x00:
        raise ConnectionError(f"CONNECT 被拒: {resp!r}")
    atyp = resp[3]
    if atyp == 0x01:
        sock.recv(4 + 2)
    elif atyp == 0x03:
        n = sock.recv(1)[0]
        sock.recv(n + 2)
    elif atyp == 0x04:
        sock.recv(16 + 2)


def probe(dest_host: str, dest_port: int, http: bool,
          creds: tuple[str, int, bytes, bytes], timeout: float = 20.0) -> tuple[bool, str]:
    host, port, user, pwd = creds
    try:
        raw = socket.create_connection((host, port), timeout=timeout)
    except OSError as e:
        return False, f"直连 SOCKS5 {host}:{port} 失败: {e}"
    try:
        raw.settimeout(timeout)
        socks5_connect(raw, user, pwd, dest_host, dest_port)
    except Exception as e:
        raw.close()
        return False, f"SOCKS5 握手/连接失败: {type(e).__name__}: {e}"
    try:
        ctx = ssl.create_default_context()
        tls = ctx.wrap_socket(raw, server_hostname=dest_host)
        ver = tls.version()
        detail = f"TLS {ver}"
        if http:
            tls.sendall(f"GET /fapi/v1/ping HTTP/1.1\r\nHost: {dest_host}\r\n"
                        f"Connection: close\r\n\r\n".encode())
            data = tls.recv(200)
            detail += f" | HTTP 首行: {data.splitlines()[0].decode('latin-1', 'replace') if data else '(空)'}"
        tls.close()
        return True, detail
    except Exception as e:
        try:
            raw.close()
        except OSError:
            pass
        return False, f"隧道内 TLS 失败: {type(e).__name__}: {e}"


def main() -> int:
    creds = parse_creds()
    host, port, user, _pwd = creds
    print(f"SOCKS5 = {host}:{port}  user={user.decode()}  pass=<已隐去>")
    print("=" * 84)
    results = []
    for name, dh, dp, http in (("交易所 REST fapi", "fapi.asterdex.com", 443, True),
                               ("行情 WS 主机 fstream", "fstream.asterdex.com", 443, False),
                               ("对照 google", "www.google.com", 443, False)):
        ok, msg = probe(dh, dp, http, creds)
        results.append((name, ok, msg))
        print(f"  {name:24s} {'✓' if ok else '✗'}  {msg}")
    good = [r for r in results if r[1]]
    print("=" * 84)
    if all(r[1] for r in results[:2]):
        print("⇒ **可用**：直连 SOCKS5 能通到交易所。")
        print("   恢复方式（只影响采集器，不动 VPN）：给采集器设")
        print(f"     L1_PROXY=socks5://{user.decode()}:<password>@{host}:{port}")
        print("   然后用车道在役 5 币重启采集器（见 "
              "`scripts/h538_resume_after_outage.py --reopen-trials` 的第 4 步）。")
        print("   ⚠️ 采集器用 `python_socks.Proxy.from_url()`，支持 socks5:// 带认证的写法 ✓")
    elif good:
        print(f"⇒ 部分可用：{[r[0] for r in good]}；交易所不通 ⇒ 这条出口也救不了行情。")
    else:
        print("⇒ **不可用**：该 SOCKS5 也过不去（可能需要先经 SS 隧道，或它本身也坏了）。")
        print("   ⇒ 回到 h539 的结论：改在 Shadowsocks 客户端里换节点（14 个 TCP 可达）。")
    return 0 if all(r[1] for r in results[:2]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
