"""h541：**出口 × 目标 的 TLS 可达性矩阵**——到底是"我们的线路"还是"WS 端点本身"？

h540 的事实：经直连 SOCKS5（8.211.172.14:55620），
  · `fapi.asterdex.com:443` REST → **HTTP 200 OK**（TLSv1.3）✓
  · `www.google.com:443` → ✓
  · **`fstream.asterdex.com:443`（行情 WS 主机）→ TLS 被重置** ✗
这有两种完全不同的解释，且对策相反：
  (A) 只有"我们可用的出口"到该主机不行（CloudFront 对该出口 IP 反滥用）⇒ 换出口即可；
  (B) **交易所的 WS 端点本身故障/下线** ⇒ 换出口没用，只能等，且整个事故的结论要改写。

本脚本用一张**矩阵**区分它们：同一批目标，分别经
  · 直连（无代理）、· 直连 SOCKS5、· 本地 SS 1080（当前死线路）
各测一次 TLS 握手（并测 fstream 的**明文 HTTP :80**，用于判断"是不是只有 TLS 层被拦"）。

用法：python scripts/h541_egress_matrix.py
"""
from __future__ import annotations

import pathlib
import socket
import ssl
import sys

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TARGETS = [
    ("交易所 REST   fapi.asterdex.com", "fapi.asterdex.com", 443),
    ("行情 WS 主机  fstream.asterdex.com", "fstream.asterdex.com", 443),
    ("行情 WS fstream (明文:80)", "fstream.asterdex.com", 80),
    ("对照 CloudFront d1.awsstatic.com", "d1.awsstatic.com", 443),
    ("对照 google", "www.google.com", 443),
]


def tls_probe(host: str, port: int, timeout: float = 15.0,
              do_http: bool = False) -> tuple[bool, str]:
    """直连（无代理）的 TLS 探测；:80 时走明文。"""
    try:
        raw = socket.create_connection((host, port), timeout=timeout)
    except OSError as e:
        return False, f"TCP 失败: {type(e).__name__}"
    try:
        raw.settimeout(timeout)
        if port == 80:
            raw.sendall(f"GET / HTTP/1.0\r\nHost: {host}\r\n\r\n".encode())
            data = raw.recv(120)
            return bool(data), ("明文 HTTP 有响应: "
                                + (data.splitlines()[0].decode('latin-1', 'replace')
                                   if data else "(空)"))
        tls = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
        v = tls.version()
        if do_http:
            tls.sendall(f"GET /fapi/v1/ping HTTP/1.1\r\nHost: {host}\r\n"
                        f"Connection: close\r\n\r\n".encode())
            d = tls.recv(120)
            v += " | " + (d.splitlines()[0].decode('latin-1', 'replace') if d else "(空)")
        tls.close()
        return True, f"TLS {v}"
    except Exception as e:
        try:
            raw.close()
        except OSError:
            pass
        return False, f"TLS 失败: {type(e).__name__}"


def via_socks(host: str, port: int, creds, timeout: float = 15.0,
              do_http: bool = False) -> tuple[bool, str]:
    from scripts.h540_direct_socks_probe import socks5_connect  # type: ignore
    sh, sp, user, pwd = creds
    try:
        raw = socket.create_connection((sh, sp), timeout=timeout)
    except OSError as e:
        return False, f"连 SOCKS5 失败: {type(e).__name__}"
    try:
        raw.settimeout(timeout)
        socks5_connect(raw, user, pwd, host, port)
        if port == 80:
            raw.sendall(f"GET / HTTP/1.0\r\nHost: {host}\r\n\r\n".encode())
            d = raw.recv(120)
            return bool(d), ("明文 HTTP 有响应: "
                             + (d.splitlines()[0].decode('latin-1', 'replace')
                                if d else "(空)"))
        tls = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
        v = tls.version()
        if do_http:
            tls.sendall(f"GET /fapi/v1/ping HTTP/1.1\r\nHost: {host}\r\n"
                        f"Connection: close\r\n\r\n".encode())
            d = tls.recv(120)
            v += " | " + (d.splitlines()[0].decode('latin-1', 'replace') if d else "(空)")
        tls.close()
        return True, f"TLS {v}"
    except Exception as e:
        try:
            raw.close()
        except OSError:
            pass
        return False, f"TLS 失败: {type(e).__name__}"


def via_local_ss(host: str, port: int, timeout: float = 12.0) -> tuple[bool, str]:
    """经本地 SS（HTTP CONNECT，当前死线路）——只测 CONNECT + TLS。"""
    try:
        raw = socket.create_connection(("127.0.0.1", 1080), timeout=timeout)
    except OSError as e:
        return False, f"连本地 1080 失败: {type(e).__name__}"
    try:
        raw.settimeout(timeout)
        raw.sendall(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n"
                    .encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            c = raw.recv(512)
            if not c:
                return False, "CONNECT 阶段被关闭"
            buf += c
        head = buf.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        if " 200" not in head:
            return False, f"CONNECT 被拒: {head}"
        tls = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
        v = tls.version()
        tls.close()
        return True, f"CONNECT 200 + TLS {v}"
    except Exception as e:
        return False, f"CONNECT 200 但 TLS 失败: {type(e).__name__}"
    finally:
        try:
            raw.close()
        except OSError:
            pass


def main() -> int:
    from scripts.h540_direct_socks_probe import parse_creds  # type: ignore
    creds = parse_creds()
    print("出口 × 目标 的可达性矩阵（✓=TLS 握手成功）")
    print("=" * 100)
    print(f"{'目标':<36s} {'直连':<22s} {'经 SOCKS5':<24s} {'经本地 SS1080':<24s}")
    rows = []
    for name, h, p in TARGETS:
        do_http = (h == "fapi.asterdex.com")
        d_ok, d_msg = tls_probe(h, p, do_http=do_http)
        s_ok, s_msg = via_socks(h, p, creds, do_http=do_http)
        l_ok, l_msg = (via_local_ss(h, p) if p == 443 else (False, "(跳过)"))
        mark = lambda ok: "✓" if ok else "✗"          # noqa: E731
        print(f"{name:<36s} {mark(d_ok)+' '+d_msg[:18]:<22s} "
              f"{mark(s_ok)+' '+s_msg[:20]:<24s} {mark(l_ok)+' '+l_msg[:20]:<24s}")
        rows.append({"target": name, "host": h, "port": p,
                     "direct": [d_ok, d_msg], "socks5": [s_ok, s_msg],
                     "local_ss": [l_ok, l_msg]})
    print("=" * 100)
    fstream = [r for r in rows if r["host"] == "fstream.asterdex.com" and r["port"] == 443][0]
    plain = [r for r in rows if r["host"] == "fstream.asterdex.com" and r["port"] == 80][0]
    rest = [r for r in rows if r["host"] == "fapi.asterdex.com"][0]
    print("判读：")
    if fstream["socks5"][0]:
        print("  · 经 SOCKS5 能通 fstream ⇒ **换出口即可恢复**（h540 的否定结论是抖动，重跑确认）")
    elif rest["socks5"][0] and not fstream["socks5"][0]:
        if plain["socks5"][0] or plain["direct"][0]:
            print("  · **明文 :80 通、TLS :443 被重置** ⇒ 该出口到 WS 主机的 **TLS 层被拦**"
                  "（CloudFront 反滥用/中间盒），不是端点下线；换出口仍可能有效")
        else:
            print("  · REST 通、WS 主机 TLS 与明文都不通 ⇒ 更像是"
                  "**该出口到 fstream 主机的整条路径被拦**（仍非端点下线的直接证据）")
        print("  · 结论：**必须换一个出口（SS 节点）再测 fstream**，才能区分 (A)/(B)")
    else:
        print("  · 各出口都不通 ⇒ 更可能是交易所侧/链路侧问题")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
