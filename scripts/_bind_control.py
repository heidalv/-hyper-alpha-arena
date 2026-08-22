# -*- coding: utf-8 -*-
"""对比实验：0.0.0.0:8001 vs 127.0.0.1:8002 两个纯监听进程，哪个能活过沙箱回收。"""
import socket, datetime, os, time

mark = r"D:\001Alpha\Hyper-Alpha-Arena\logs\_bind_test.txt"
pid = os.getpid()
def _listen(host, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((host, port))
        s.listen(5)
    except Exception as e:
        s.close()
        return f"bind fail {host}:{port} {e}"
    return None

_which = int(os.environ.get("BIND_TEST", "1"))
if _which == 1:
    err = _listen("0.0.0.0", 8001)
    tag = "ALL"
else:
    err = _listen("127.0.0.1", 8002)
    tag = "LOCAL"
with open(mark, "a", encoding="utf-8") as f:
    f.write(f"{tag} pid={pid} {datetime.datetime.now().isoformat()} ok={err or 'listening'}\n")
while True:
    time.sleep(20)
