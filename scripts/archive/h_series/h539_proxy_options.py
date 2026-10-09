"""h539：**代理可恢复性排查**——除了当前这条死掉的线路，还有没有别的出口？

背景：2026-09-29 04:32 起 Shadowsocks 上游节点 `8.211.172.14:55620` TCP 可达但
**不转发流量**（隧道内 TLS 握手 SSLEOFError），导致行情采集全断、车道零腿。
这是整条链唯一的阻断点，因此值得先确认"有没有别的路可走"。

本脚本（**只读，不打印任何密码**）：
  1. 枚举本机所有监听端口 + 进程名，标出可能的代理端口（1080/7890/10808/… 等）；
  2. 读 Shadowsocks 4.4.1.0 的 `gui-config.json`，逐条打印
     `remarks / server:port / method`（**跳过 password**）并标出当前选中的 index；
  3. 对每个节点做 **TCP 可达性**探测（能连上 ≠ 能转发，但连不上就肯定不能用）；
  4. 给出"下一步该怎么试"的可执行建议。

用法：python scripts/h539_proxy_options.py
"""
from __future__ import annotations

import json
import pathlib
import socket
import sys

sys.stdout.reconfigure(encoding="utf-8")

SS_DIR = pathlib.Path(r"C:\Users\heida\Desktop\Shadowsocks-4.4.1.0")
SS_CFG = SS_DIR / "gui-config.json"
PROXY_PORTS = (1080, 1081, 1086, 1087, 1088, 7890, 7891, 7892, 7897, 7899,
               10808, 10809, 10810, 20170, 20171, 2080, 2081, 8889, 9090, 18080,
               8118, 9910, 2019)


def port_pids() -> list[tuple[int, int]]:
    """(port, pid) —— 用 psutil（若装了）否则退回 netstat。"""
    out: list[tuple[int, int]] = []
    try:
        import psutil  # type: ignore
        for c in psutil.net_connections(kind="tcp"):
            if c.status == "LISTEN" and c.laddr:
                out.append((int(c.laddr.port), int(c.pid or 0)))
        return out
    except Exception:
        pass
    import subprocess
    try:
        p = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True,
                           text=True, timeout=30)
        for line in (p.stdout or "").splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[3] == "LISTENING":
                try:
                    out.append((int(parts[1].rsplit(":", 1)[1]), int(parts[4])))
                except (ValueError, IndexError):
                    continue
    except Exception as e:
        print(f"  (netstat 失败: {e})")
    return out


def proc_name(pid: int) -> str:
    try:
        import psutil  # type: ignore
        return psutil.Process(pid).name()
    except Exception:
        return ""


def tcp_ok(host: str, port: int, timeout: float = 6.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def main() -> int:
    print("=" * 84)
    print("[1] 本机监听端口（标出可能的代理口）")
    print("=" * 84)
    plist = sorted(set(port_pids()))
    proxy_like = []
    for port, pid in plist:
        nm = proc_name(pid)
        interesting = (port in PROXY_PORTS or
                       any(k in nm.lower() for k in
                           ("clash", "v2ray", "sing", "xray", "shadow", "proxy",
                            "trojan", "wireguard", "mihomo", "hysteria", "naive")))
        if interesting:
            proxy_like.append((port, pid, nm))
    if proxy_like:
        for port, pid, nm in proxy_like:
            print(f"  {port:6d}  pid={pid:<7d} {nm}")
    else:
        print("  （没有发现代理类监听口 —— 除下面配置里的之外，本机可能只有一条线路）")
    print(f"  （全部监听口 {len(plist)} 个）")

    print("\n" + "=" * 84)
    print("[2] Shadowsocks 配置里的节点（**不含密码**）")
    print("=" * 84)
    if not SS_CFG.exists():
        print(f"  未找到 {SS_CFG}")
        return 1
    try:
        cfg = json.loads(SS_CFG.read_text(encoding="utf-8", errors="replace"))
    except Exception as e:
        print(f"  解析失败：{str(e)[:120]}")
        return 1
    configs = cfg.get("configs") or []
    idx = cfg.get("index")
    print(f"  节点数 {len(configs)}，当前选中 index={idx}"
          f"{'（0 基）' if idx is not None else ''}")
    print(f"  {'#':>3s} {'remarks':<22s} {'server':<20s} {'port':>6s} {'method':<22s} "
          f"{'TCP':>5s}  选中")
    rows = []
    for i, c in enumerate(configs):
        srv = str(c.get("server") or "")
        prt = int(c.get("server_port") or 0)
        ok = tcp_ok(srv, prt) if (srv and prt) else False
        mark = "★" if str(i) == str(idx) else ""
        print(f"  {i:>3d} {str(c.get('remarks') or '')[:22]:<22s} {srv:<20s} "
              f"{prt:>6d} {str(c.get('method') or '')[:22]:<22s} "
              f"{('✓' if ok else '✗'):>5s}  {mark}")
        rows.append({"i": i, "remarks": c.get("remarks"), "server": srv,
                     "port": prt, "method": c.get("method"), "tcp_ok": ok,
                     "selected": str(i) == str(idx)})
    alive = [r for r in rows if r["tcp_ok"] and not r["selected"]]
    dead_sel = [r for r in rows if r["selected"] and not r["tcp_ok"]]
    print("\n" + "=" * 84)
    print("[3] 结论与建议")
    print("=" * 84)
    if dead_sel:
        print("  · **当前选中的节点连 TCP 都不通** ⇒ 先换一个（下面有可用的）")
    else:
        print("  · 当前选中的节点 **TCP 可达但不转发**（数据面故障）——"
              "换节点是唯一可能立刻恢复的动作")
    if alive:
        print(f"  · 还有 {len(alive)} 个节点 TCP 可达："
              + "、".join(f"#{r['i']} {r['remarks'] or r['server']}" for r in alive))
        print("  · 建议动作：在 Shadowsocks 客户端里**切到上面任一节点**"
              "（或改 gui-config.json 的 `index` 后重启该客户端），"
              "然后跑 `scripts/h536_lane_alarm.py` 看『代理数据面』是否变 ✓。")
        print("    ⚠️ 切节点会同时影响这台机器的其它外网流量，故由你决定；"
              "本脚本不会改动任何配置。")
    else:
        print("  · 没有其它 TCP 可达的节点 ⇒ 需要更新订阅/节点列表，"
              "或改用别的出口（另一台机器的代理/公司网络）。")
        print("  · 若你有可用的 SOCKS5/HTTP 代理，也可直接把采集器指过去："
              "`set L1_PROXY=http://<host>:<port>`（h536 的『代理数据面』判据会验证它）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
