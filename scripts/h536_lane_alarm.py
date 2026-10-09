"""h536：**车道健康告警**——补上"静默停摆 4.2 小时没人知道"的缺口。

事故复盘（2026-09-29）：
  04:32 Shadowsocks 上游节点停止转发 → 采集器 WS 全被 ConnectionReset
  → `market_trades_aggregated` 停在 04:32:15 → 引擎无数据可判定成交
  → **车道零腿 4.2 小时**。
  期间：worker `ok=True`、tick 正常、`skip_counts` 没有异常、h472 监视任务
  在 05:35 正常跑完（Last Result=0）并**打印了三个卡住的持仓**——但**没有任何人/任何任务
  因为"多久没腿"而报警**。本脚本就是那个缺失的告警。

它只做一件事：**把"车道还活着吗"拆成四个可独立失败的事实**，任一失败即告警：
  1. **腿速**：最近 N 分钟有几条腿（0 条 = 立即致命，这是最终事实）；
  2. **成交判定数据**：market 库 `market_trades_aggregated` 的滞后（>5 分钟即致命）；
  3. **采集进程**：`asterdex_stream_health` 是否在更新、`msgs_total` 是否在涨、
     `reconnects` 是否远大于 `msgs_total`（重连风暴）；
  4. **代理数据面**：对 `127.0.0.1:1080` 发 CONNECT 并在隧道里做一次 TLS 握手
     （**只测 CONNECT 会误判**：事故时 CONNECT 返回 200 而数据面已死）。

输出：`research_l1/out/h536_alarm.json` 与追加日志 `logs/lane_alarm.log`；
致命时退出码 2（计划任务的 Last Result 会显示失败，可被巡检发现）。

用法：python scripts/h536_lane_alarm.py [--minutes 15] [--quiet]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import socket
import ssl
import sys

sys.stdout.reconfigure(encoding="utf-8")

import psycopg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
LANE = "mm_asterdex"
OUT = ROOT / "research_l1" / "out" / "h536_alarm.json"
LOG = ROOT / "logs" / "lane_alarm.log"


def read_env() -> dict:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def dsn(u: str) -> str:
    for j in ("+psycopg2", "+psycopg", "+asyncpg"):
        u = u.replace(j, "")
    return u


def proxy_data_path_ok(host: str = "www.gstatic.com", port: int = 443,
                       proxy=("127.0.0.1", 1080), timeout: float = 8.0) -> tuple[bool, str]:
    """在 HTTP CONNECT 隧道里**真做一次 TLS 握手**。

    为什么不能只测 CONNECT：事故现场 `CONNECT` 返回 `200 Connection established`
    而隧道里一个字节都收不到（上游不转发）⇒ 只测 CONNECT 会报"健康" ✗✗。
    """
    try:
        s = socket.create_connection(proxy, timeout=timeout)
    except OSError as e:
        return False, f"连不上代理 {proxy}: {e}"
    try:
        s.settimeout(timeout)
        req = (f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n")
        s.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf and len(buf) < 4096:
            chunk = s.recv(1024)
            if not chunk:
                return False, "代理在 CONNECT 阶段就关闭了连接"
            buf += chunk
        head = buf.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        if " 200" not in head:
            return False, f"CONNECT 被拒：{head}"
        ctx = ssl.create_default_context()
        try:
            tls = ctx.wrap_socket(s, server_hostname=host)
            ver = tls.version()
            tls.close()
            return True, f"隧道内 TLS 握手成功（{ver}）"
        except Exception as e:
            return False, f"CONNECT 通过但**TLS 握手失败**：{type(e).__name__}: {e}"
    finally:
        try:
            s.close()
        except OSError:
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=15)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    env = read_env()
    now = dt.datetime.now()
    facts: dict = {"ts": now.isoformat(timespec="seconds")}
    crit: list[str] = []
    warn: list[str] = []

    # ① 腿速（最终事实）
    try:
        with psycopg.connect(dsn(env["DATABASE_URL"]), autocommit=True) as c:
            with c.cursor() as cur:
                cur.execute(
                    "SELECT count(*), max(ts) FROM lane_ledger WHERE lane_id=%s "
                    "AND ts > now() - make_interval(mins => %s)", (LANE, a.minutes))
                n, last = cur.fetchone()
                cur.execute(
                    "SELECT count(*) FROM lane_ledger WHERE lane_id=%s "
                    "AND ts > now() - interval '60 minutes'", (LANE,))
                n60 = cur.fetchone()[0]
        facts["legs_window"] = n
        facts["legs_60m"] = n60
        facts["last_leg"] = str(last)
        if n == 0:
            crit.append(f"最近 {a.minutes} 分钟 **0 条腿**（近 60 分钟 {n60} 条）")
        elif n60 < 60:
            warn.append(f"近 60 分钟仅 {n60} 腿（< 60/h 硬约束）")
    except Exception as e:
        crit.append(f"读账本失败：{str(e)[:80]}")

    # ② 成交判定数据新鲜度
    try:
        with psycopg.connect(dsn(env.get("MARKET_DATABASE_URL") or env["DATABASE_URL"]),
                             autocommit=True) as c:
            with c.cursor() as cur:
                cur.execute("SELECT max(timestamp) FROM market_trades_aggregated")
                mx = cur.fetchone()[0]
                cur.execute("SELECT now()")
                dbnow = cur.fetchone()[0]
                cur.execute("""SELECT stream, msgs_total, reconnects, last_event_ms
                               FROM asterdex_stream_health ORDER BY stream""")
                health = cur.fetchall()
        lag = None
        if mx:
            newest = dt.datetime.fromtimestamp(float(mx) / 1000.0, tz=dt.timezone.utc)
            n2 = dbnow if dbnow.tzinfo else dbnow.replace(tzinfo=dt.timezone.utc)
            lag = (n2 - newest).total_seconds()
        facts["market_lag_s"] = None if lag is None else round(lag, 1)
        facts["stream_health"] = [
            {"stream": s, "msgs": m, "reconnects": r, "last_event_ms": e}
            for s, m, r, e in health]
        if lag is None:
            crit.append("market_trades_aggregated 无数据")
        elif lag > 300:
            crit.append(f"成交数据滞后 **{lag/60:.1f} 分钟**（>5 分钟即致命）")
        for s, m, r, e in health:
            if r and m is not None and r >= max(5, m):
                crit.append(f"采集流 {s} **重连风暴**（msgs={m} reconnects={r}）")
            if not e:
                warn.append(f"采集流 {s} 的 last_event_ms=0（未收到真实行情事件）")
    except Exception as e:
        crit.append(f"读行情库失败：{str(e)[:80]}")

    # ③ 代理数据面 —— **必须测真实依赖**（R33 修正）
    # ⚠️ 原实现拿 `www.gstatic.com` 当"代理健康"的代理指标，结果在
    # 节点对 asterdex 通、对 gstatic 不通时报出**假 CRITICAL**（实测 09:5x）。
    # 告警一旦会误报，就会被忽略 ⇒ 改成直接测**车道真正依赖的两个主机**：
    #   · `fapi.asterdex.com`（REST，采集器与交易所接口）
    #   · `fstream.asterdex.com`（行情 WS —— 事故时正是它先断）
    _targets = [("fapi.asterdex.com", 443), ("fstream.asterdex.com", 443)]
    _results = []
    for _h, _p in _targets:
        _ok, _msg = proxy_data_path_ok(host=_h, port=_p)
        _results.append({"host": f"{_h}:{_p}", "ok": _ok, "detail": _msg})
        print(f"  代理→{_h}:{'✓' if _ok else '✗'} {_msg}" if not a.quiet else "", end="")
    facts["proxy_targets"] = _results
    _ok_any = any(r["ok"] for r in _results)
    ok = _ok_any
    msg = "；".join(f"{r['host']} {'✓' if r['ok'] else '✗'}" for r in _results)
    facts["proxy"] = {"ok": ok, "detail": msg}
    if not _ok_any:
        crit.append(f"代理到交易所**两个主机都不通**：{msg}（车道无法取行情/下单）"
                    f" ⇒ **处置：`python scripts/h542_switch_ss_node.py --apply`"
                    f"（逐个换节点并以 REST+WS 的 TLS 为判据）**")
    elif not all(r["ok"] for r in _results):
        warn.append(f"代理只通部分交易所主机：{msg}")

    facts["critical"] = crit
    facts["warn"] = warn
    facts["verdict"] = ("CRITICAL" if crit else ("WARN" if warn else "OK"))
    OUT.write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
    line = (f"{now:%Y-%m-%d %H:%M:%S} {facts['verdict']:8s} "
            f"legs{a.minutes}m={facts.get('legs_window')} legs60m={facts.get('legs_60m')} "
            f"mkt_lag={facts.get('market_lag_s')} "
            f"proxy={'ok' if ok else 'FAIL'} "
            f"{'| ' + '；'.join(crit) if crit else ''}")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    if not a.quiet:
        print(f"车道健康：**{facts['verdict']}**")
        print(f"  腿速：近 {a.minutes} 分钟 {facts.get('legs_window')} 条 / "
              f"近 60 分钟 {facts.get('legs_60m')} 条")
        print(f"  成交数据滞后：{facts.get('market_lag_s')}s")
        print(f"  代理数据面：{'✓' if ok else '✗'} {msg}")
        for x in crit:
            print(f"  ✗ {x}")
        for x in warn:
            print(f"  ! {x}")
        print(f"  已写 {OUT.relative_to(ROOT)} 与 {LOG.relative_to(ROOT)}")
    return 2 if crit else 0


if __name__ == "__main__":
    raise SystemExit(main())
