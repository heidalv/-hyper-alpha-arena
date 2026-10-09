"""standby_ingest.py — 采集器**待命替换件**（R103）。**默认只干跑，不落库** ✓

背景：原件（`research_l1/services/aster_ws_ingest.py`，路径见事故记录）已被误删且无副本 ✗
（见 `研究结论/误删事故_20260929.md`；重建规格见该文 §8.1）。
本文件是**按规格重写的待命件**，用途有二：
  · 原件仍在运行时：用 `--probe` / 干跑模式**验证本实现**（不写库 ⇒ 不会与原件双写 ✗）；
  · 原件不可用时：由**你**决定是否启用（`--apply` 才写库，且需先停掉原件 ✓）。

用法：
    python scripts/standby_ingest.py --probe              # 探测端点形式与首批消息（不写库）
    python scripts/standby_ingest.py --dry --seconds 30   # 干跑 30s，统计各流消息/解析成功率
    python scripts/standby_ingest.py --apply              # **真正写库**（默认不开；需人工决定）

⚠️ 与原件**绝不能同时** `--apply`：两者都写同一批表 ⇒ 重复行会污染 `agg_trade_id` 去重假设 ✗。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

# [R230] **落盘日志**：本件常由计划任务经 `run-quiet.vbs` 静默启动 ⇒ 崩溃时**没有任何痕迹** ✗
# （2026-09-29 17:5x 实测：任务实例起来 20 秒后消失、查无原因 ✗）。
# ⇒ 凡启动/互锁/异常都写 `logs/standby_ingest.log` ✓，让"为什么死"变成可读的读数 ✓。
_LOG = ROOT / "logs" / "standby_ingest.log"


def _log(msg: str) -> None:
    try:
        _LOG.parent.mkdir(parents=True, exist_ok=True)
        with _LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:  # noqa: BLE001
        pass

# 规格来源：事故记录 §8.1（从真实数据反推 ✓）
SYMBOLS = (
           "BTCUSDT,ETHUSDT,PUMPUSDT,PLAYUSDT,USUSDT,DRAMUSDT,AVGOUSDT,HUMAUSDT,"
           "NATGASUSDT,BCHUSDT,PONSUSDT,METUSDT,ICPUSDT,TRUMPUSDT,LINKUSDT,GRAMUSDT,"
           "CASHCATUSDT,AAVEUSDT,FETUSDT,SYNUSDT,VVVUSDT,INJUSDT,DOSUSDT,MRNAUSDT,SEIUSDT,"
           "PENGUUSDT,KITEUSDT,SOLUSD1,BTCUSD1,ETHUSD1,"
        )
DEPTH_SYMBOLS = (
                 "BTCUSDT,ETHUSDT,PUMPUSDT,PLAYUSDT,"
              )
WS_HOST = "fstream.asterdex.com"
PROXY_HOST, PROXY_PORT = "127.0.0.1", 1080


def _load_env() -> dict:
    env = {}
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _mm_universe_syms() -> list:
    """[h704 2026-10-02] 做市车道宇宙的币(带 USDT 后缀)。

    病根(用户实测反馈"发现了 XLM,却不去激活采集"):深度采集名单是**硬编码**
    `DEPTH_SYMBOLS`,而做市车道的 v5 雷达每 15 分钟换币 ⇒ 换入的新币(实测 XLM)
    不在深度名单 ⇒ "在交易但没深度"。本函数让深度流**动态**并入车道宇宙。
    """
    try:
        from backend.services import lane_registry as _reg
        out = []
        for _lid in ("mm_asterdex", "mm_asterdex_live"):
            _lm = (_reg.get_lane(_lid) or {}).get("meta") or {}
            for _s in (_lm.get("symbols") or []):
                _u = f"{str(_s).upper()}USDT"
                if _u not in out:
                    out.append(_u)
        return out
    except Exception:
        return []


def _conn(url: str, timeout: float = 15.0):
    """用 HTTP 代理建 WS（`socksio` 未安装 ⇒ 走 HTTP 代理；已实测代理能转发 ✓）。"""
    import websocket  # websocket-client
    return websocket.create_connection(
        url, timeout=timeout, http_proxy_host=PROXY_HOST, http_proxy_port=PROXY_PORT)


def _candidates(stream: str) -> list:
    """待探测的端点形式（Binance 风格；asterdex 未文档化 ⇒ 实测决定 ✓）。"""
    return [
        f"wss://{WS_HOST}/ws/{stream}",
        f"wss://{WS_HOST}/stream?streams={stream}",
        f"wss://{WS_HOST}/ws?streams={stream}",
    ]


def _multi_url(streams: list) -> str:
    """合并流：一次连接订阅多个流（原件 35 币显然这么做 ✓）。"""
    return f"wss://{WS_HOST}/stream?streams=" + "/".join(streams)


def probe_multi(symbols: list, depth_symbols: list) -> int:
    """[R104] 验证**合并流**形式对三类流都可用（若可用，实现只需 3 条连接 ✓）。"""
    print("=" * 92)
    print("合并流探测（每类抽 2 个币，走 `/stream?streams=`）")
    print("=" * 92)
    ok = True
    for tag, tmpl, syms in (("trades", "{}@aggTrade", symbols[:2]),
                            ("book", "{}@bookTicker", symbols[:2]),
                            ("depth", "{}@depth20@100ms", depth_symbols[:2])):
        streams = [tmpl.format(s.lower()) for s in syms]
        url = _multi_url(streams)
        try:
            ws = _conn(url, timeout=10.0)
            ws.settimeout(8.0)
            names = set()
            for _ in range(6):
                msg = ws.recv()
                d = json.loads(msg)
                names.add(str((d.get("data") or d).get("s")))
            ws.close()
            print(f"  ✓ {tag}: {url[:88]}…  收到 symbol={sorted(names)}")
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"  ✗ {tag}: {type(exc).__name__}: {str(exc)[:70]}")
    print("=" * 92)
    print("⇒ " + ("合并流可用 ✓（实现用 3 条连接即可）" if ok else "合并流不可用 ✗（需按流类型分别连接）"))
    return 0 if ok else 1


def probe(symbols: list, depth_symbols: list, tests: int = 3) -> int:
    """探测：哪种端点形式能连通并收到消息（每条流各试一次）。"""
    print("=" * 92)
    print("端点探测（每种形式最多等 8 秒收一条消息）")
    print("=" * 92)
    trials = [
        ("trades", f"{symbols[0].lower()}@aggTrade"),
        ("book", f"{symbols[0].lower()}@bookTicker"),
        ("depth", f"{depth_symbols[0].lower()}@depth20@100ms"),
    ]
    ok_forms = {}
    for tag, stream in trials:
        print(f"\n[{tag}] stream={stream}")
        for url in _candidates(stream):
            t0 = time.time()
            try:
                ws = _conn(url, timeout=8.0)
                ws.settimeout(8.0)
                msg = ws.recv()
                ws.close()
                head = str(msg)[:120].replace("\n", " ")
                print(f"  ✓ {url}")
                print(f"      首条({time.time()-t0:.1f}s): {head}")
                ok_forms[tag] = url
                break
            except Exception as exc:  # noqa: BLE001
                print(f"  ✗ {url}  {type(exc).__name__}: {str(exc)[:70]}")
    print("\n" + "=" * 92)
    print("探测结论：" + (", ".join(f"{k}={v}" for k, v in ok_forms.items()) or "全部失败 ✗"))
    return 0 if ok_forms else 1


def _parse_trade(sym: str, d: dict, recv_ns: int) -> tuple | None:
    try:
        return (sym, int(d["E"]), int(d.get("T") or d["E"]), recv_ns, int(d["a"]),
                str(d["p"]), str(d["q"]), bool(d["m"]))
    except Exception:  # noqa: BLE001
        return None


def _parse_book(sym: str, d: dict, recv_ns: int) -> tuple | None:
    try:
        return (sym, int(d.get("E") or (recv_ns // 1_000_000)),
                int(d.get("T") or d.get("E") or (recv_ns // 1_000_000)), recv_ns,
                int(d["u"]), str(d["b"]), str(d["B"]), str(d["a"]), str(d["A"]))
    except Exception:  # noqa: BLE001
        return None


def _parse_depth(sym: str, d: dict, recv_ns: int) -> tuple | None:
    try:
        bids, asks = d.get("bids") or d.get("b") or [], d.get("asks") or d.get("a") or []
        return (sym, int(d.get("E") or (recv_ns // 1_000_000)), recv_ns,
                int(d.get("lastUpdateId") or d.get("u") or 0),
                int(len(bids)), json.dumps(bids), json.dumps(asks))
    except Exception:  # noqa: BLE001
        return None


def dry(symbols: list, depth_symbols: list, seconds: int) -> int:
    """干跑：并发订阅 3 条流（简化：顺序连、轮询 recv），统计消息数与解析成功率。**不写库** ✓"""
    import websocket  # noqa: F401
    streams = [
        ("trades", f"{symbols[0].lower()}@aggTrade", _parse_trade),
        ("book", f"{symbols[0].lower()}@bookTicker", _parse_book),
        ("depth", f"{depth_symbols[0].lower()}@depth20@100ms", _parse_depth),
    ]
    stats = {tag: {"msgs": 0, "parsed": 0, "bad": 0, "sample": None} for tag, *_ in streams}
    conns = {}
    for tag, stream, _fn in streams:
        for url in _candidates(stream):
            try:
                ws = _conn(url, timeout=10.0)
                ws.settimeout(2.0)
                conns[tag] = ws
                print(f"  [{tag}] 已连 {url}")
                break
            except Exception as exc:  # noqa: BLE001
                print(f"  [{tag}] 连不上 {url}: {str(exc)[:60]}")
    if not conns:
        print("✗ 全部流都连不上")
        return 1
    t_end = time.time() + seconds
    while time.time() < t_end:
        for tag, _stream, parser in streams:
            ws = conns.get(tag)
            if not ws:
                continue
            try:
                msg = ws.recv()
            except Exception:  # noqa: BLE001
                continue
            recv_ns = time.time_ns()
            stats[tag]["msgs"] += 1
            try:
                d = json.loads(msg)
                d = d.get("data") or d
                sym = str(d.get("s") or (d.get("stream") or "").split("@")[0].upper()
                          or symbols[0])
                row = parser(sym, d, recv_ns)
                if row:
                    stats[tag]["parsed"] += 1
                    if stats[tag]["sample"] is None:
                        stats[tag]["sample"] = row
                else:
                    stats[tag]["bad"] += 1
            except Exception:  # noqa: BLE001
                stats[tag]["bad"] += 1
    for ws in conns.values():
        try:
            ws.close()
        except Exception:  # noqa: BLE001
            pass
    print("\n" + "=" * 92)
    print(f"干跑 {seconds}s 统计（**未写任何库** ✓）")
    print("=" * 92)
    for tag, s in stats.items():
        print(f"  {tag:<8} 消息={s['msgs']:<6} 解析成功={s['parsed']:<6} 失败={s['bad']}")
        if s["sample"]:
            print(f"           样本: {str(s['sample'])[:150]}")
    return 0


def _self_pids() -> set:
    """[R230 修] **我自己的进程链**（含父进程）——互锁必须排除它们。

    为什么：本机跑 python 是**父子两条进程**（`.venv\\Scripts\\python.exe` 启动器 →
    `.runtime\\Python312\\python.exe` 真解释器），两者命令行**都含** `standby_ingest` ✗。
    首版互锁只排除 `os.getpid()` ⇒ 把**自己的父进程**当成"别的采集器"⇒ **自己拒绝自己** ✗✗
    （2026-09-29 17:4x 实测：`✗ 互锁拒绝：检测到其它采集器进程 pid=3216` 那正是我自己的启动器 ✗）。
    """
    pids = {str(os.getpid())}
    try:
        import subprocess
        q = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\""
             " | Select-Object ProcessId,ParentProcessId | ForEach-Object {"
             " \"$($_.ProcessId)`t$($_.ParentProcessId)\" }"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        parent: dict = {}
        for ln in (q.stdout or "").splitlines():
            parts = ln.split("\t")
            if len(parts) == 2 and parts[0].strip().isdigit():
                parent[parts[0].strip()] = parts[1].strip()
        cur = str(os.getpid())
        for _ in range(5):                     # 向上追 5 层足够覆盖"启动器→解释器"结构 ✓
            nxt = parent.get(cur)
            if not nxt or nxt in pids:
                break
            pids.add(nxt)
            cur = nxt
    except Exception:  # noqa: BLE001
        pass
    return pids


def _competing_ingestors() -> list:
    """检测是否已有**别的采集器**在跑（互锁用，只读）。"""
    out = []
    mine = _self_pids()
    try:
        import subprocess
        q = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\""
             " | Select-Object ProcessId,CommandLine | ForEach-Object {"
             " \"$($_.ProcessId)`t$($_.CommandLine)\" }"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        for line in (q.stdout or "").splitlines():
            if "aster_ws_ingest" in line or "standby_ingest" in line:
                pid = line.split("\t", 1)[0].strip()
                if not pid.isdigit():        # [R230] 命令行里含换行时会切坏 ⇒ 只认纯数字 pid ✓
                    continue
                if pid and pid not in mine:      # [R230] 排除**自己的整条进程链** ✓
                    out.append((pid, line.strip()[:120]))
    except Exception:  # noqa: BLE001
        pass
    return out


def run_ingest(symbols: list, depth_symbols: list, seconds: int, to_prod: bool) -> int:
    """[R104] 真正的采集循环：3 条合并流 + 批量写入。

    **默认写临时表**（`CREATE TEMP TABLE … (LIKE public.x INCLUDING ALL)` ⇒ 进程退出即消失，
    对生产表零影响 ✓）；只有 `--to-prod` 才写正式表 ✗，且此时**必须**通过互锁：
    不得有其它采集器在跑（否则双写 ✗）。
    """
    import websocket  # noqa: F401
    import psycopg
    env = _load_env()
    dsn = (env.get("MARKET_DATABASE_URL") or env.get("DATABASE_URL") or "").replace(
        "+psycopg2", "").replace("+psycopg", "").replace("+asyncpg", "")
    if "/alpha_market" not in dsn:
        dsn = dsn.replace("/alpha_arena", "/alpha_market")
    if not dsn:
        print("✗ 读不到 MARKET_DATABASE_URL / DATABASE_URL")
        return 1

    if to_prod:
        others = _competing_ingestors()
        if others:
            _log(f"互锁拒绝（检测到其它采集器）：{others[:2]}")
            print("✗ 互锁拒绝：检测到其它采集器进程 ⇒ 绝不双写 ✗")
            for pid, cl in others:
                print(f"    pid={pid} {cl}")
            print("    （如确要接管：先停掉原件，再重跑本命令 ✓）")
            return 3
        _log(f"互锁通过 ⇒ 以**写正式表**模式启动（pid={os.getpid()}）")
        print("⚠️ **写正式表模式**：互锁通过（无其它采集器）")
        tmap = {"trades": "public.asterdex_trades",
                "book": "public.asterdex_book_ticker",
                "depth": "public.asterdex_depth_snapshots"}
    else:
        print("✓ 临时表模式（对生产表零影响 ✓）")
        tmap = {"trades": "tmp_asterdex_trades", "book": "tmp_asterdex_book_ticker",
                "depth": "tmp_asterdex_depth_snapshots"}

    sql = {
        "trades": "INSERT INTO {t} (symbol,event_ts_ms,trade_ts_ms,recv_ts_ns,"
                  "agg_trade_id,price,qty,is_buyer_maker,ingest_ts)"
                  " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,now())",
        "book": "INSERT INTO {t} (symbol,event_ts_ms,trade_ts_ms,recv_ts_ns,update_id,"
                "bid_px,bid_qty,ask_px,ask_qty,ingest_ts)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,now())",
        "depth": "INSERT INTO {t} (symbol,event_ts_ms,recv_ts_ns,update_id,levels,"
                 "bids,asks) VALUES (%s,%s,%s,%s,%s,%s,%s)",
    }
    # [h704] depth 频道表做成**可变列表**:运行期每 5 分钟并入做市车道新宇宙币,
    # 变化时强制重连(见 _worker 里的刷新逻辑)。trades/book 保持启动期名单。
    depth_chans = [f"{s.lower()}@depth20@100ms" for s in depth_symbols]
    streams = [
        ("trades", [f"{s.lower()}@aggTrade" for s in symbols], _parse_trade),
        ("book", [f"{s.lower()}@bookTicker" for s in symbols], _parse_book),
        ("depth", depth_chans, _parse_depth),
    ]
    counts = {tag: {"msgs": 0, "rows": 0, "bad": 0, "reconnect": 0} for tag, *_ in streams}
    # [R231 **关键修复**] `--seconds 0` ⇒ **常驻不退出** ✓。
    # 首版默认 30 秒即退出（干净退出、exit 0、日志无异常 ✗）⇒ 症状是"数据只实时 30 秒，
    # 之后全部陈旧"，且查进程确实没了 ⇒ 看起来像崩溃、实际是**跑完就退** ✗✗（今晚为此
    # 反复重启了 5 次才定位 ✗）。作为**替代采集器**必须常驻 ⇒ 计划任务用 `--seconds 0` ✓。
    t_end = float("inf") if seconds <= 0 else time.time() + seconds
    # [R232] 冲库频率/批量：`book_ticker` 是**最高频**的一条（35 币、实测 ~2000 行/s 量级 ✗），
    # 每 1s / 200 行冲一次根本追不上 ⇒ 缓冲越积越多 ⇒ 实测滞后 45s ✗。
    # ⇒ 每 0.25s 冲一次、单批最多 1000 行 ✓（深度/trades 也受益 ✓）。
    FLUSH_ROWS, FLUSH_SEC = 1000, 0.25
    bufs = {tag: [] for tag, *_ in streams}
    last_flush = time.time()
    last_event = {tag: 0 for tag, *_ in streams}
    pending_reconn = {tag: 0 for tag, *_ in streams}

    def flush(cur_local, force: bool = False) -> int:
        """批量写入（book ~344 行/秒 ⇒ 逐行写太慢 ✗）。返回写入行数。"""
        n = 0
        for tag, rows in bufs.items():
            if not rows:
                continue
            if not force and len(rows) < FLUSH_ROWS and (
                    time.time() - last_flush) < FLUSH_SEC:
                continue
            cur_local.executemany(sql[tag].format(t=tmap[tag]), rows)
            n += len(rows)
            rows.clear()
        return n

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        if not to_prod:
            for tag, t in tmap.items():
                src = {"trades": "asterdex_trades", "book": "asterdex_book_ticker",
                       "depth": "asterdex_depth_snapshots"}[tag]
                cur.execute(f"CREATE TEMP TABLE {t} (LIKE public.{src} INCLUDING ALL)")
            print("  临时表已建：" + ", ".join(tmap.values()))
        conns = {}
        for tag, slist, _p in streams:
            for attempt in range(3):
                try:
                    ws = _conn(_multi_url(slist), timeout=15.0)
                    ws.settimeout(2.0)
                    conns[tag] = ws
                    print(f"  [{tag}] 已订阅 {len(slist)} 个流")
                    break
                except Exception as exc:  # noqa: BLE001
                    counts[tag]["reconnect"] += 1
                    print(f"  [{tag}] 连接失败({attempt+1}/3): {str(exc)[:60]}")
                    time.sleep(2)
        # [R232 **吞吐修复**] 单线程轮询 3 个 socket 时，每个 `recv()` 最多阻塞 2 秒
        # ⇒ 一轮可达 ~6 秒 ⇒ 面对 **28 币 × ~10 条/s** 的深度流根本读不完 ⇒
        # 实测深度滞后 **40–55 秒** ✗✗（用户现场："不是实时数据了" ✗）。
        # ⇒ 改为**每条流一个线程**（各自 recv/解析/入缓冲，锁保护 ✓），
        #   主线程只负责**定时批量写库 + 心跳**（DB 句柄不跨线程 ✗ ✓）。
        _lock = threading.Lock()
        _stop = threading.Event()

        def _worker(tag_: str, slist_: list, parser_, ws_) -> None:
            ws = ws_
            _last_dyn_check = 0.0
            while not _stop.is_set() and time.time() < t_end:
                # [h704] 深度流每 5 分钟重查做市车道宇宙:新币立即并入频道表并重连
                # (v5 雷达换币后 ≤5 分钟内深度自动激活,不再"发现了却不采集")。
                if tag_ == "depth" and time.time() - _last_dyn_check >= 300.0:
                    _last_dyn_check = time.time()
                    _base = [f"{s.lower()}@depth20@100ms" for s in depth_symbols]
                    for _u in _mm_universe_syms():
                        _c = f"{_u.lower()}@depth20@100ms"
                        if _c not in _base:
                            _base.append(_c)
                    if _base != list(slist_):
                        slist_[:] = _base
                        print(f"  [depth] 宇宙更新 ⇒ 重连({len(slist_)} 流)", flush=True)
                        if ws is not None:
                            try:
                                ws.close()
                            except Exception:  # noqa: BLE001
                                pass
                            ws = None
                            continue
                if ws is None:
                    try:
                        ws = _conn(_multi_url(slist_), timeout=15.0)
                        ws.settimeout(2.0)
                        with _lock:
                            counts[tag_]["reconnect"] += 1
                        print(f"  [{tag_}] 已连接（重连计数 {counts[tag_]['reconnect']}）", flush=True)
                    except Exception as exc:  # noqa: BLE001
                        print(f"  [{tag_}] 连接失败：{str(exc)[:50]}", flush=True)
                        time.sleep(2.0)
                    continue
                try:
                    msg = ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue                     # 常态：该流这段时间没消息 ⇒ 继续等 ✓
                except Exception:  # noqa: BLE001
                    with _lock:
                        counts[tag_]["reconnect"] += 1
                        pending_reconn[tag_] += 1
                        backoff = min(30, 2 ** min(5, pending_reconn[tag_]))
                    try:
                        ws.close()
                    except Exception:  # noqa: BLE001
                        pass
                    ws = None
                    time.sleep(backoff)
                    continue
                recv_ns = time.time_ns()
                try:
                    d = json.loads(msg)
                    d = d.get("data") or d
                    sym = str(d.get("s") or "")
                    row = parser_(sym, d, recv_ns)
                    with _lock:
                        counts[tag_]["msgs"] += 1
                        if row:
                            bufs[tag_].append(row)
                            last_event[tag_] = int(d.get("E") or (recv_ns // 1_000_000))
                        else:
                            counts[tag_]["bad"] += 1
                except Exception:  # noqa: BLE001
                    with _lock:
                        counts[tag_]["bad"] += 1

        _threads = [threading.Thread(
            target=_worker, args=(tag, slist, parser, conns.get(tag)), daemon=True)
            for tag, slist, parser in streams]
        for _t in _threads:
            _t.start()

        while time.time() < t_end:
            time.sleep(0.25)
            if (time.time() - last_flush) >= FLUSH_SEC:
                with _lock:
                    wrote = flush(cur)
                    counts["_wrote"] = counts.get("_wrote", 0) + wrote
                    last_flush = time.time()
                    if to_prod:
                        # [R105] 心跳：`asterdex_stream_health` 是"采集器是否活着"最直接的探针 ✓
                        for tag in bufs:
                            if last_event[tag]:
                                cur.execute(
                                    "UPDATE asterdex_stream_health SET last_event_ms=%s,"
                                    " last_recv_ns=%s, msgs_total=msgs_total+%s,"
                                    " reconnects=reconnects+%s, updated_at=now()"
                                    " WHERE stream=%s",
                                    (last_event[tag], time.time_ns(), counts[tag]["msgs"],
                                     pending_reconn[tag], tag))
                                pending_reconn[tag] = 0
        _stop.set()
        for _t in _threads:
            _t.join(timeout=5)
        with _lock:
            wrote = flush(cur, force=True)          # 收尾冲掉缓冲 ✓
            counts["_wrote"] = counts.get("_wrote", 0) + wrote
        for tag, t in tmap.items():
            cur.execute(f"SELECT count(*), max(symbol) FROM {t}")
            n, mx = cur.fetchone()
            print(f"  [{tag}] 表内行数={n}  样本 symbol={mx}")
    print("\n" + "=" * 92)
    print(f"采集 {seconds}s 结果（{'正式表' if to_prod else '临时表'}）")
    print("=" * 92)
    for tag, c in counts.items():
        if tag == "_wrote":
            continue
        print(f"  {tag:<8} 消息={c['msgs']:<7} 解析失败={c['bad']:<5}"
              f" 连接重连={c['reconnect']}")
    print(f"  合计入库 = {counts.get('_wrote', 0)} 行（批量写：每 {FLUSH_ROWS} 行或 {FLUSH_SEC}s 冲一次）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true", help="只探测端点形式（不写库）")
    ap.add_argument("--probe-multi", action="store_true", help="探测合并流形式（不写库）")
    ap.add_argument("--dry", action="store_true", help="干跑统计（不写库）")
    ap.add_argument("--apply", action="store_true",
                    help="**采集**：默认写同结构**临时表**（零生产影响 ✓）；"
                         "加 `--to-prod` 才写正式表 ✗（会先做互锁检查）")
    ap.add_argument("--to-prod", action="store_true",
                    help="配合 `--apply`：写**正式表** ✗（需先停掉原件，否则互锁拒绝）")
    ap.add_argument("--seconds", type=int, default=30,
                    help="运行秒数；**0 = 常驻不退出**（作为替代采集器时用 0 ✓）")
    ap.add_argument("--symbols", default=SYMBOLS)
    ap.add_argument("--depth-symbols", default=DEPTH_SYMBOLS)
    a = ap.parse_args()
    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    dsyms = [s.strip().upper() for s in a.depth_symbols.split(",") if s.strip()]
    # [h704] 深度名单动态并入做市车道宇宙(换币后自动纳入;这里先做启动期并入,
    # 运行期的增量订阅见 run_ingest 的周期性刷新)
    _dyn = _mm_universe_syms()
    for _s in _dyn:
        if _s not in dsyms:
            dsyms.append(_s)
            print(f"  [h704] 深度名单并入做市宇宙币 {_s}")
    print(f"符号：trades/book {len(syms)} 个；depth {len(dsyms)} 个（规格见事故记录 §8.1）")
    if a.probe:
        return probe(syms, dsyms)
    if a.probe_multi:
        return probe_multi(syms, dsyms)
    if a.apply:
        return run_ingest(syms, dsyms, a.seconds, to_prod=a.to_prod)
    return dry(syms, dsyms, a.seconds)


if __name__ == "__main__":
    # [R230] 任何未捕获异常都写进日志 ⇒ "起来了 20 秒又消失"这种事不再无迹可查 ✗→✓
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001
        import traceback
        _log("FATAL\n" + traceback.format_exc())
        raise
