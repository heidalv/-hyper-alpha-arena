"""h542：**换 Shadowsocks 节点并验证数据面**（可回滚，带配置备份）。

依据（本会话逐一取证）：
  · 事故起点 04:32，market 库三表同时冻结 ⇒ 采集侧断，不是引擎（h533/h534）；
  · 采集器走 `L1_PROXY`（默认 `http://127.0.0.1:1080`），而 1080 的 CONNECT 返回 200、
    **隧道里 TLS 一个字节都收不到**（h536 的判据）；
  · h539：SS 配置 16 个节点，**当前选中 #6 `szyd.szst.net:62011` 连 TCP 都不通**，
    另有 **14 个节点 TCP 可达** ⇒ 死的是选中的那条线路；
  · h540/h541：经另一条可用出口能拿到交易所 REST **HTTP 200**（⇒ **交易所在线**），
    但 `fstream`（行情 WS）:443 的 TLS 在各出口都被重置 ⇒ 必须换到"能通 WS"的出口。

⚠️ 换节点会影响这台机器的**全部**外网流量（不只是采集器）。判据是先测
`fapi`（REST）与 `fstream`（WS）的 **TLS 握手**：两者都过才认为该节点可用于行情。
当前选中节点已是死的（不转发任何流量）⇒ 换到任何可达节点都**严格不差于现状**。
配置会先备份；`--restore` 可一键还原到原始 index。

用法：
  python scripts/h542_switch_ss_node.py                 # 干跑：列出候选与判据
  python scripts/h542_switch_ss_node.py --apply          # 逐个试（最多 --max-tries 个）
  python scripts/h542_switch_ss_node.py --restore        # 还原原始 index 并重启客户端
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import socket
import ssl
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

SS_DIR = pathlib.Path(r"C:\Users\heida\Desktop\Shadowsocks-4.4.1.0")
SS_EXE = SS_DIR / "Shadowsocks.exe"
SS_CFG = SS_DIR / "gui-config.json"
BAK = SS_DIR / "gui-config.json.h542bak"
LOCAL = ("127.0.0.1", 1080)
# 候选优先级：先换"不同服务商"的（死的是 szyd.szst.net），再换同商的其它出口
CAND_PREF = [1, 7, 5, 2, 14, 12, 9, 11, 8, 10, 13, 15, 3, 4]


def tcp_ok(host: str, port: int, timeout: float = 6.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def local_port_ready(timeout: float = 25.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with socket.create_connection(LOCAL, timeout=3):
                return True
        except OSError:
            time.sleep(1.0)
    return False


def data_path_ok(host: str, port: int = 443, connect_timeout: float = 12.0,
                 tls_timeout: float = 12.0) -> tuple[bool, str]:
    """经本地 1080 做 CONNECT + **真 TLS 握手**（只测 CONNECT 会误报，见 h536）。"""
    try:
        s = socket.create_connection(LOCAL, timeout=connect_timeout)
    except OSError as e:
        return False, f"连本地 1080 失败: {type(e).__name__}"
    try:
        s.settimeout(connect_timeout)
        s.sendall(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n"
                  .encode())
        buf = b""
        while b"\r\n\r\n" not in buf and len(buf) < 4096:
            c = s.recv(512)
            if not c:
                return False, "CONNECT 阶段被关闭"
            buf += c
        head = buf.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        if " 200" not in head:
            return False, f"CONNECT 被拒: {head}"
        s.settimeout(tls_timeout)
        tls = ssl.create_default_context().wrap_socket(s, server_hostname=host)
        v = tls.version()
        tls.close()
        return True, f"CONNECT 200 + TLS {v}"
    except Exception as e:
        return False, f"CONNECT 200 但 TLS 失败: {type(e).__name__}"
    finally:
        try:
            s.close()
        except OSError:
            pass


def ss_pids() -> list[int]:
    out: list[int] = []
    try:
        import psutil  # type: ignore
        for p in psutil.process_iter(["name"]):
            if (p.info.get("name") or "").lower() == "shadowsocks.exe":
                out.append(p.pid)
        return out
    except Exception:
        pass
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Shadowsocks.exe", "/FO", "CSV"],
                           capture_output=True, text=True, timeout=20)
        for line in (r.stdout or "").splitlines()[1:]:
            parts = [x.strip('"') for x in line.split('","')]
            if parts and parts[1].isdigit():
                out.append(int(parts[1]))
    except Exception:
        pass
    return out


def stop_ss() -> None:
    for pid in ss_pids():
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True,
                       timeout=20)
    time.sleep(2.0)


def start_ss() -> bool:
    try:
        subprocess.Popen([str(SS_EXE)], cwd=str(SS_DIR),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    except Exception as e:
        print(f"    ✗ 启动失败: {str(e)[:100]}")
        return False
    return local_port_ready()


def read_cfg() -> dict:
    return json.loads(SS_CFG.read_text(encoding="utf-8", errors="replace"))


def write_cfg(cfg: dict) -> None:
    SS_CFG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def node_of(cfg: dict, i: int) -> dict:
    cfgs = cfg.get("configs") or []
    return cfgs[i] if 0 <= i < len(cfgs) else {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--restore", action="store_true")
    ap.add_argument("--max-tries", type=int, default=5)
    a = ap.parse_args()
    if not SS_CFG.exists():
        print(f"✗ 找不到 {SS_CFG}")
        return 1
    cfg = read_cfg()
    orig_idx = cfg.get("index")
    print(f"配置：{SS_CFG}")
    print(f"节点数 {len(cfg.get('configs') or [])}，当前 index={orig_idx}"
          f"（{node_of(cfg, int(orig_idx)).get('remarks')!r}）")

    if a.restore:
        if not BAK.exists():
            print("✗ 没有备份可还原")
            return 1
        shutil.copy2(BAK, SS_CFG)
        stop_ss()
        ok = start_ss()
        print(f"✓ 已还原为备份配置（index={read_cfg().get('index')}），客户端启动="
              f"{'成功' if ok else '失败'}")
        good, msg = data_path_ok("fapi.asterdex.com")
        print(f"  数据面复测：{'✓' if good else '✗'} {msg}")
        return 0

    # 候选：TCP 可达且不是当前那个
    cands = []
    for i in CAND_PREF:
        n = node_of(cfg, i)
        srv, prt = str(n.get("server") or ""), int(n.get("server_port") or 0)
        if not srv or not prt:
            continue
        ok = tcp_ok(srv, prt)
        cands.append((i, n.get("remarks") or "", srv, prt, ok))
    print("\n候选节点（TCP 可达性实测）")
    for i, rm, srv, prt, ok in cands:
        print(f"  #{i:<3d} {rm[:26]:<26s} {srv:<18s} {prt:>6d}  "
              f"{'✓ 可达' if ok else '✗ 不可达'}")
    usable = [c for c in cands if c[4]]
    if not usable:
        print("✗ 没有 TCP 可达的候选 ⇒ 需要更新订阅/节点列表")
        return 1
    print(f"\n判据：切换后必须同时满足 **fapi（REST）+ fstream（行情 WS）的 TLS 握手**")
    if not a.apply:
        print("⇒ DRY-RUN：未改动任何东西。加 --apply 才会逐个切换并验证。")
        return 0

    shutil.copy2(SS_CFG, BAK)
    print(f"✓ 已备份配置 → {BAK.name}（原 index={orig_idx}）")
    done = 0
    for i, rm, srv, prt, _ok in usable[: a.max_tries]:
        done += 1
        print(f"\n--- 试 #{i} {rm} ({srv}:{prt})")
        stop_ss()
        c = read_cfg()
        c["index"] = i
        write_cfg(c)
        if not start_ss():
            print("    ✗ 客户端未在 25s 内监听 1080")
            continue
        print("    客户端已启动，1080 在听")
        ok_r, msg_r = data_path_ok("fapi.asterdex.com")
        print(f"    REST fapi     : {'✓' if ok_r else '✗'} {msg_r}")
        ok_w, msg_w = data_path_ok("fstream.asterdex.com")
        print(f"    WS   fstream  : {'✓' if ok_w else '✗'} {msg_w}")
        if ok_r and ok_w:
            print(f"\n⇒ **#{i} {rm} 可用**（REST + WS 双通）⇒ 保持该节点。")
            print("   下一步：`python scripts/h538_resume_after_outage.py --reopen-trials "
                  "--apply`（会重启采集器并重开 ②③ 的试跑窗）")
            return 0
    print(f"\n⇒ 试了 {done} 个候选，没有一个让 fapi+fstream 同时通。")
    # 安全兜底：无论试的结果如何，**必须让客户端处于运行状态**
    # （否则用户的代理会从"线路死"变成"进程死"，那是更差的状态）。
    if not local_port_ready(timeout=1.0):
        print("   ⚠️ 客户端未在监听，尝试重新拉起……")
        if start_ss():
            print("   ✓ 客户端已拉起（index="
                  f"{read_cfg().get('index')}）")
        else:
            print("   ✗ 拉起失败 ⇒ 请手动启动 Shadowsocks.exe")
    print(f"   已把配置留在最后一个候选上（当前 index="
          f"{read_cfg().get('index')}）；如需回到原状：--restore")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
