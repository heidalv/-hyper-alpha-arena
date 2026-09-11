# -*- coding: utf-8 -*-
r"""
binance_socks_chain — 币安链路代理（链式出口）

链路: 本机(国内) -> 现有代理 127.0.0.1:1080 (HTTP CONNECT, Shadowsocks)
     -> 自建 SOCKS5 8.211.172.14:55620 (heidalv1)
     -> 币安 (出口 IP = 8.211.172.14 = API 白名单)

应用侧把 BINANCE_HTTPS_PROXY 指向本代理(默认 127.0.0.1:18080)，
ccxt/urllib 按普通 HTTP 代理使用；本进程负责在隧道上叠 SOCKS5 认证。
本机不能直连 8.211.172.14（用户定调：必须经现有代理链接）。

启动（脱离会话）:
  powershell -Command "Start-Process python.exe -ArgumentList 'D:\001Alpha\Hyper-Alpha-Arena\scripts\binance_socks_chain.py' -WindowStyle Hidden"
或 schtasks 常驻。
"""
import socket
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = int(__import__("os").getenv("CHAIN_PROXY_PORT", "18080"))
SS_PROXY = ("127.0.0.1", int(__import__("os").getenv("CHAIN_HOP1_PORT", "1080")))
SOCKS = ("8.211.172.14", 55620)
SOCKS_USER = b"heidalv1"
SOCKS_PASS = b"ccf184215"
CONNECT_TIMEOUT = 15.0
IDLE_TIMEOUT = 300.0


def _hop1_tunnel(host: str, port: int) -> socket.socket:
    """经本机现有代理(1080)打 HTTP CONNECT 隧道到自建 SOCKS 服务器。"""
    s = socket.create_connection(SS_PROXY, timeout=CONNECT_TIMEOUT)
    req = ("CONNECT %s:%d HTTP/1.1\r\nHost: %s:%d\r\nProxy-Connection: keep-alive\r\n\r\n"
           % (host, port, host, port)).encode()
    s.sendall(req)
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = s.recv(1024)
        if not chunk:
            raise ConnectionError("hop1 tunnel closed during handshake")
        buf += chunk
    if b" 200 " not in buf.split(b"\r\n", 1)[0]:
        raise ConnectionError("hop1 CONNECT rejected: %s" % buf.split(b"\r\n", 1)[0])
    return s


def _socks5_connect(tun: socket.socket, host: str, port: int) -> None:
    """在隧道上完成 SOCKS5 用户名密码认证并 CONNECT。"""
    tun.sendall(b"\x05\x01\x02")                    # 只协商用户名/密码认证
    rep = tun.recv(2)
    if len(rep) < 2 or rep[1] != 0x02:
        raise ConnectionError("socks auth method rejected: %r" % rep)
    tun.sendall(b"\x01" + bytes([len(SOCKS_USER)]) + SOCKS_USER
                + bytes([len(SOCKS_PASS)]) + SOCKS_PASS)
    auth = tun.recv(2)
    if len(auth) < 2 or auth[1] != 0x00:
        raise ConnectionError("socks auth failed: %r" % auth)
    hb = host.encode()
    tun.sendall(b"\x05\x01\x00\x03" + bytes([len(hb)]) + hb + struct.pack(">H", port))
    rep = tun.recv(4)
    if len(rep) < 4 or rep[1] != 0x00:
        raise ConnectionError("socks connect failed: %r" % rep)
    if rep[3] == 0x01:
        tun.recv(4 + 2)
    elif rep[3] == 0x04:
        tun.recv(16 + 2)
    else:
        ln = tun.recv(1)[0]
        tun.recv(ln + 2)


def _pump(a: socket.socket, b: socket.socket) -> None:
    """双向泵字节。"""
    def one(src, dst):
        try:
            src.settimeout(IDLE_TIMEOUT)
            while True:
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except Exception:
            pass
        finally:
            try:
                dst.shutdown(socket.SHUT_WR)
            except Exception:
                pass

    t1 = threading.Thread(target=one, args=(a, b), daemon=True)
    t2 = threading.Thread(target=one, args=(b, a), daemon=True)
    t1.start()
    t2.start()
    t1.join(IDLE_TIMEOUT)
    t2.join(IDLE_TIMEOUT)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 静默
        return

    def do_CONNECT(self):  # noqa: N802
        try:
            host, _, port_s = self.path.partition(":")
            port = int(port_s)
            # 链路: 1080 -> SOCKS 服务器(认证+CONNECT 到目标)
            tun = _hop1_tunnel(SOCKS[0], SOCKS[1])
            _socks5_connect(tun, host, port)
            self.send_response(200, "Connection Established")
            self.send_header("Proxy-Agent", "socks-chain")
            self.end_headers()
            _pump(tun, self.connection)
        except Exception as exc:
            try:
                self.send_error(502, str(exc)[:120])
            except Exception:
                pass
        finally:
            try:
                self.connection.close()
            except Exception:
                pass


def main():
    srv = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), _Handler)
    srv.daemon_threads = True
    print("socks-chain proxy listening on %s:%d -> %s:%d (via %s:%d)"
          % (LISTEN_HOST, LISTEN_PORT, SOCKS[0], SOCKS[1], SS_PROXY[0], SS_PROXY[1]), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
