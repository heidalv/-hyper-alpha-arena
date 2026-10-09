# -*- coding: utf-8 -*-
"""[F283 2026-09-16] MM 车道**独立 ticker 进程**：把 L1 做市的 tick 从 API 后端解耦。

动机（本轮实测）：宿主侧有一个 ~5 分钟周期的启动器反复重启 API 后端
（`logs/backend.pid*.log` 寿命 4~7 分钟：13:04 / 13:08 / 13:15 / 13:18 / 13:25 …）。
车道 tick 原本跑在后端的 APScheduler 里 ⇒ 每重启一次就丢一段 tick，并在恢复时
因 F279 的跨停机伪收益**额外站开一个波动窗口**（5~6 分钟）⇒ 测量连续性不成立。
而行情数据由数据中心**独立进程**持续写入（后端停机期间未断流：今日断点普查
仅 3 处、最长 2.8 分钟）⇒ 只要把 tick 移出 API 进程，后端被谁重启都不再中断车道。

本进程做的事（复用与后端**完全同一条**代码路径，零重复实现 ✓）：
  · 每 `MM_LANE_WORKER_INTERVAL_SEC`（默认 1s）调 `runner.shadow_tick_task(lane_id)`
    —— 内含"车道存在 / mode=paper / status=active / 非演练"全部前置校验；
  · 落库与账本写入都在 `runner.tick()` 内部（与进程内模式逐字一致）；
  · 心跳写 `logs/mm_lane_status.json`（供巡检/前端读取：ticks/fills/last_tick/skip）；
  · 单实例锁 `logs/mm_lane_worker.lock`（PID 存活即退出，防双 tick ✗）。

启用（与后端配合，缺一不可）：
  1) 后端 `.env` 加 `MM_LANE_TICKER=external`（后端就不再注册进程内 tick，防双写）；
  2) 起本进程（计划任务 `DSH_MM_WORKER`，与其它 DSH_* 任务同模式，脱离会话存活）。
回退：删掉 `.env` 里那行 + 停本进程，后端即恢复进程内 tick（旧行为逐字一致）。
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── [整顿轮·T34 2026-10-06] **本进程必须自己加载配置**（但只加载专用文件）──────
#
# 事故（实测）：`.env` 里所有 `MM_*` 配置对本进程**从未生效**。
#   · 只有 API 后端在 `backend/main.py:20` 调 `load_dotenv()`；
#   · 本进程（独立 ticker）**从不加载 .env** ⇒ `os.getenv("MM_...")` 全返回 None。
#   ⇒ 整顿期里我往 `.env` 加的每一条配置，**本进程一律没读到**。
#
# ── [T36 2026-10-06] 为什么第一版修法**依然无效**（根因）──────────────────
#   第一版用 `from dotenv import load_dotenv`，外面套 `except Exception: pass`。
#   **本进程跑的是 `\.runtime\Python312\python.exe`，该环境没有 `dotenv` 模块**
#   ⇒ `ModuleNotFoundError` ⇒ 被那个 `except` **静默吞掉** ⇒ 配置依然没加载。
#   而我当时是在 `.venv` 的解释器里测的（那里有 dotenv）⇒ **测了个不同的环境**，
#   于是"逻辑正确"的假象维持了好几轮。
#
#   教训有两层，都已固化：
#     ① 验证必须用**跑该代码的那个解释器**（本进程是 .runtime，不是 .venv）；
#     ② `except: pass` 会把"加载失败"伪装成"没有配置" ⇒ 必须告警。
#
# 修法：**不依赖任何第三方库**，自己解析 `.env.worker`（简单 KEY=VALUE）。
#   ⇒ 根除"依赖缺失导致静默失败"这一整类问题。
#
# **为什么只加载 `.env.worker` 而不是整个 `.env`**：
#   `.env` 里另有 6 个有真实功能读取点、且值有风险的项：
#     · `MM_STOP_LOSS_BP=0` → `core.py:1018`
#     · `MM_W_BASE_BP=5` / `MM_K_INV=1.0` → `core.py:66,69,82`
#     · `MM_MAX_ONE_SIDE_SEC=300` → `core.py:993`
#     · `MM_SPREAD_MULT=0.5` → `core.py:143,158`
#     · `MM_LANE_LIMITS_ENFORCE` → `core.py:1508`
#   一次性全激活 = 同时改动宽度/库存/止损/限额四条主链路，无法逐条证明安全。
#   ⇒ 白名单式：只读 `.env.worker`。文件不存在 ⇒ 不加载任何东西（旧行为）。
#
# 回滚：删掉 `.env.worker` ⇒ 行为与修复前一致。
def _load_worker_env(_p: Path) -> int:
    """极简 KEY=VALUE 解析（无第三方依赖）。返回成功加载的条数。"""
    if not _p.exists():
        return 0
    _n = 0
    try:
        for _line in _p.read_text(encoding="utf-8", errors="replace").splitlines():
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _k, _, _v = _line.partition("=")
            _k = _k.strip()
            _v = _v.strip().strip('"').strip("'")
            if _k and _k not in os.environ:   # 真实环境变量优先（与后端同口径）
                os.environ[_k] = _v
                _n += 1
    except Exception as _e:  # noqa: BLE001  —— 必须可见，不得静默
        print(f"[mm-worker] 警告: 读取 {_p.name} 失败: {_e}", flush=True)
    return _n


_WORKER_ENV_LOADED = _load_worker_env(ROOT / ".env.worker")
if _WORKER_ENV_LOADED:
    print(f"[mm-worker] 已从 .env.worker 加载 {_WORKER_ENV_LOADED} 条配置",
          flush=True)

LANE = os.getenv("MM_LANE_WORKER_LANE", "mm_asterdex")
# 报价跟着实时盘口走。15 秒是旧的快照轮询，盘口本身每秒都在更新。
INTERVAL = float(os.getenv("MM_LANE_WORKER_INTERVAL_SEC", "1") or 1)
LOG_DIR = ROOT / "logs"
STATUS = LOG_DIR / "mm_lane_status.json"
# [h665d 2026-10-01] 单实例锁必须按**车道**加(而非仅按 LOG_DIR):
# 事故:实盘 worker 的 bat 被 cmd 码页吞掉 set 行后跑成第二个 mm_asterdex 进程,
# 双 tick 同一车道 3 分钟 → lane_runtime_state 被两进程互写 → UNI 仓位 avg_px
# 污染 → 一条 +3345bp 幽灵止盈腿(+17.98U 假利润,已删)。
LOCK = LOG_DIR / f"mm_lane_worker_{LANE}.lock"
LOG = LOG_DIR / "mm_lane_worker.log"

# [F294 2026-09-21] 这三个路径允许被 env 覆盖。
# 动机：单实例锁的回归测试必须能**真实启动一个 worker 子进程**，而子进程只认
# 模块级的 LOCK/LOG ⇒ 测试写下的行会混进**生产** `logs/mm_lane_worker.log`
# （实测发生过：测试的 `another worker alive` 与 `lock warning` 出现在生产日志里，
#  而那份日志是故障复盘唯一的证据源）。有 env 覆盖后，子进程不再污染生产文件。
_env_dir = os.getenv("MM_LANE_WORKER_LOG_DIR", "").strip()
if _env_dir:
    LOG_DIR = Path(_env_dir)
    STATUS = LOG_DIR / "mm_lane_status.json"
    # [h665d] 锁仍按车道命名(见上):LOG_DIR 变了也必须防同车道双进程
    LOCK = LOG_DIR / f"mm_lane_worker_{LANE}.lock"
    LOG = LOG_DIR / "mm_lane_worker.log"
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def log(msg: str) -> None:
    line = datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " [mm-worker] " + msg
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


WORKER_SCRIPT = Path(__file__).name        # "mm_lane_worker.py"


def _is_our_worker(pid: int, *, exclude_self: bool = True) -> bool:
    """[F294 2026-09-21] 该 PID 是否是**本脚本**的实例（而不只是任意 python）。

    为什么要认脚本：单实例的判据必须是「还有没有**同一个 worker**」，
    而不是「还有没有别的 python」。后者在包装器/父进程场景下会误判。

    `exclude_self=False` 用于回答"**当前进程自己**是不是本脚本的 worker"
    （锁自证用）——此时不能再排除自己。
    """
    if pid <= 0:
        return False
    if exclude_self and pid == os.getpid():
        return False
    try:
        import psutil
        if not psutil.pid_exists(pid):
            return False
        p = psutil.Process(pid)
        if not p.is_running():
            return False
        if "python" not in (p.name() or "").lower():
            return False
        cl = " ".join(p.cmdline() or []).lower()
        return WORKER_SCRIPT.lower() in cl
    except Exception:
        # 查不到命令行 ⇒ **保守当作是我们自己的另一个实例**（宁可退让，不可双 tick）
        return True


def _holds_lock() -> bool:
    """[F294] 锁文件当前是否写着**本进程** PID（循环内复查用）。"""
    try:
        return int((LOCK.read_text(encoding="ascii") or "0").strip() or 0) == os.getpid()
    except Exception:
        return False


def _pid_file() -> Path:
    return LOCK.with_name(LOCK.name + ".pid")


def _lock_ok() -> bool:
    """[F294 2026-09-21] 单实例锁 —— 修「双 worker 同时 tick 同一车道」事故。

    # 事故（2026-09-21 09:13:59~09:16:28 实测）

    宇宙收缩到 `[ASTER,SOL,XRP]` 后，DOGE **仍被正常做市 4.5 分钟**
    （18 笔真实账本成交、峰值 |持仓| $303）。逐条核对 `lane_ledger`：
    DOGE 与 ASTER/XRP/SOL **交错出现在同一个 tick 时间戳** ⇒
    **当时有两个 worker 进程在同时 tick 同一条车道**，各自持有自己的
    `ShadowRunner`（各自 `self.symbols`），互相看不见对方的成交，
    却共用一份 `lane_runtime_state` / 账本 ⇒ 状态被交叉覆写（持仓先增后减的乱序）。

    # 病根

    旧判据是 `old > 0 and old != os.getpid()`：**锁文件里恰好是自己的 PID 时不检查**。
    而「杀掉旧 worker、立刻重启新 worker」正是锁文件刚被新进程写过的窗口：
    旧进程在自己循环里读到新 PID、发现 `old == os.getpid()` ⇒ 判为"锁是我的" ⇒
    **两个进程都继续 tick**（日志里那条 `another worker alive` 只出现在**新**进程，
    旧进程一声不响地活着 ⇒ 静默双跑 ✗）。

    # 修法

      1. 锁里是**另一个本脚本实例**（不要求 PID 不同）⇒ 退出；
      2. 判据从「任意 python」收紧为「命令行含 `mm_lane_worker.py`」；
      3. 自己成为持有者时**另写一个永不变化的 `*.lock.pid`**，作为"我是持有者"的自证；
      4. 循环内每次 tick 前复查锁仍属于自己 ⇒ 一旦易主**立即退出**（不再静默双跑）。
    """
    try:
        if LOCK.exists():
            old = 0
            try:
                old = int((LOCK.read_text(encoding="ascii") or "0").strip() or 0)
            except Exception:
                old = 0
            # 关键修复：不再要求 old != os.getpid()（见 docstring 的病根）
            if _is_our_worker(old, exclude_self=False):
                log(f"another worker alive (pid={old}) -> exit")
                return False
        LOCK.write_text(str(os.getpid()), encoding="ascii")
        try:
            _pid_file().write_text(str(os.getpid()), encoding="ascii")
        except Exception:
            pass
        return True
    except Exception as e:      # 锁异常不阻断（单实例由计划任务策略兜底）
        log(f"lock warning: {e}")
        return True


def _snapshot(res: dict) -> dict:
    """心跳内容：优先从 runner.status() 取与 /shadow 同名的字段（可逐项对照 ✓）。

    [F285 2026-09-16] 还要带 `states`（每币持仓/挂单价/挂单时刻）：F284 只合并了
    计数类字段，看板上的"我方挂单/持仓"仍来自 API 进程内那份（启动时读入、之后
    不随 worker 更新）⇒ 会滞后。心跳带 states 后 `/shadow` 与看板才能显示真值。
    """
    out = {"ts": time.time(), "ok": bool(res.get("ok")), "reason": str(res.get("reason") or "")}
    # [h843] tick 逐段计时探针透传到心跳(白名单是显式的,不加这里就看不到)
    if isinstance(res.get("phase_ms"), dict):
        out["phase_ms"] = res["phase_ms"]
    try:
        from backend.services.market_maker.runner import get_runner
        r = get_runner(LANE)
        st = r.status() if r else {}
        for k in ("ticks", "fills", "flattens", "last_tick_ts", "equity",
                  "fill_notional", "quoted_decisions", "skip_counts", "side_counts",
                  # [F288 2026-09-16] 报价行为类读数也要进心跳：digest 里
                  # `avg_width_bp / avg_sigma_all / fills_per_hour` 此前恒为 None
                  # （它们同样只在**真正 tick 的进程**里有值）⇒ 每日口径无可比读数 ✗
                  "avg_width_bp", "avg_sigma", "avg_sigma_all", "fills_per_hour",
                  "quote_modes", "frozen_share", "avg_base_bp", "sigma_decisions",
                  # [F295 2026-09-21] 实际生效的开关：心跳里必须能直接读到
                  # "实盘按什么参数在跑"。此前只能读注册表 + 猜 env 覆盖，
                  # 而 env 覆盖正是"注册表与实盘不一致"的经典来源 ⇒ 调参变猜谜。
                  "params", "limits",
                  # [F327 2026-09-21] 「谁在权威（注册表 or env）」必须进心跳。
                  # ⚠️ 本函数是**显式字段白名单**，不是整份 status 透传 ——
                  # 只在 `runner.status()` 里加字段**到不了心跳**（实测：
                  # 加了 param_authority 后心跳里仍是空）。
                  # 归因脚本/LLM 读的是心跳，所以不列在这里 = 不存在。
                  "param_authority",
                  # [F339 2026-09-22] 车道级闸门的可见性 + 日亏闸的判定输入。
                  # ⚠️ 本次又踩了同一个白名单（F327 已写明警告）：
                  # 在 `runner.status()` 里加了这四个键、却漏了这一行 ⇒
                  # 心跳里全是 None，等于没加。
                  # 为什么必须有：`daily_loss_stop_pct=80%` 在 12 天里 0 次触发
                  # （尾部零保护）之所以一直没被发现，就是因为车道暂停
                  # **从来没有任何可读出口**。`day_pnl_usd` 是闸门真正读到的数，
                  # `day_pnl_limit_usd` 是触发线，两者一起才知道"离触发多远"。
                  "lane_pause_counts", "lane_pause_last",
                  "day_pnl_usd", "day_pnl_limit_usd",
                  # [h621 2026-09-29] 成交判定源（tick/bucket）进心跳：白名单陷阱
                  # 第三次提醒——本函数是显式白名单，runner.status() 新字段不列
                  # 在这里就到不了心跳（F327/F339 都踩过）。
                  "seg_source",
                  # [h624] markout / 门禁 KPI（A1+A6）——同上，白名单必须显式列出
                  "h624_kpi",
                  "direction_card",
                  # [h657] Q 速控快照——白名单陷阱**第五次**提醒:不列这里就到不了心跳
                  "q_speed",
                  # [h664] 方向分数影子快照
                  "dir_score",
                  # [h665] 实盘执行桥快照(纸面车道为 None)
                  "live_bridge",
                  # [h667] 模拟按真实交易所条件(订单速率/过滤器跳过)
                  "venue_filters",
                  # [h672] 前端警报事件流
                  "events",
                  # [h680] 自驱动宇宙雷达状态
                  "universe_radar",
                  # [h631 2026-09-29] 白名单陷阱**第四次**（F327/F339/h621 之后）：
                  # `gate_probe_counts` 在 `runner.status()` 里有、也被
                  # `worker_status._WORKER_KEYS` 收过，却**漏在本元组** ⇒ 心跳里
                  # 根本没有这个键 ✗。代价：23:07 重启后实测 `side_counts.none=128`
                  # （一个报价都没有），而 `skip_counts` 只留 top-12 ⇒ 12 名之外的
                  # 成因**完全不可见**，只能靠猜。
                  # 探针字典本身就是"与掩码无关"的口径（GATE_PROBES 在返回前就打点），
                  # 正是为这种"全部被拦"的场景设计的 ⇒ 必须进心跳。
                  "gate_probe_counts",
                  # 同批补：跳价暂停 / markout 停加仓的**逐币**明细（谁被暂停一眼可见）
                  "jump_pause_symbols", "markout_halt_symbols",
                  # [F339b] `states` 也放进同一个元组：此前它在循环之后**单独**赋值
                  # （`out["states"] = _states`），于是 `_states` 为空时键就不存在
                  # ⇒ 与其它字段的"键恒在、值可为 None"语义不一致，
                  # 也让"三道白名单逐字段对齐"的测试无法统一表达。
                  # 现在它走同一路径，值在下面被"瘦身"覆盖（避免心跳文件膨胀）。
                  "states"):
            out[k] = st.get(k)
        # symbols 也回显：宇宙被谁改成什么，一眼可见（F283 热更新是否生效的证据）
        out["symbols"] = st.get("symbols")
        out["gap_repair"] = st.get("gap_repair")
        # [F285] 每币运行态（持仓/挂单）——只留看板需要的字段，避免心跳文件膨胀
        #
        # ── [整顿轮·T40 2026-10-06] **补上出口/计数器字段** ──────────────────
        # 事故（实测）：本元组此前只留 8 个字段，**把
        # `SymbolState.to_dict()` 里所有计数器都丢掉了** —— 而 `runner.status()`
        # 明明把它们都吐出来了。后果：
        #   · `timeout_exit_blocked` 等计数器**从来没有被任何人观测到过**
        #     （core.py:1070 的注释还写着"监控 timeout_exit_blocked"，实际看不到）
        #   · 这是同一份文件里**第 6 次**踩"显式白名单"坑
        #     （F327 / F339 / h621 / h624 / h657 都记过）
        # ⇒ 我连续 5 轮"改动无效"的根因之一就是**看不到这些计数器**，
        #   只能靠读代码推断；而推断错了也发现不了。
        # 本次只补**出口/风险相关**的小整数字段，体积可忽略（每币 8 个整数）。
        _states = {}
        for sym, s in (st.get("states") or {}).items():
            if not isinstance(s, dict):
                continue
            _states[sym] = {k: s.get(k) for k in
                            ("qty", "avg_px", "avg_mid", "opened_ts", "quote_bid",
                             "quote_ask", "quote_mid", "quote_ts",
                             # [T40] 出口/风险计数器（此前被白名单丢掉）
                             "timeout_exit_blocked", "take_profit_hits",
                             "stop_since", "decay_since", "last_stop_ts",
                             "pattern_tag", "book_slot_open")}
        out["states"] = _states
    except Exception as e:
        out["status_err"] = str(e)
    return out


def main() -> int:
    once = "--once" in sys.argv
    # [F294] `--lock-check`：只打印单实例锁的当前判定，**不 tick、不改锁**。
    # 为什么需要：旧锁机制出过"两个 worker 同时 tick 却一声不响"的事故，
    # 而当时**没有任何办法在事故中确认自己是不是持有者** ⇒ 先补可观测性。
    if "--lock-check" in sys.argv:
        old = 0
        try:
            old = int((LOCK.read_text(encoding="ascii") or "0").strip() or 0)
        except Exception:
            pass
        log(f"lock-check: lock={old} self={os.getpid()} "
            f"other_worker_alive={_is_our_worker(old, exclude_self=False)} "
            f"i_hold_lock={_holds_lock()}")
        return 0
    log(f"start lane={LANE} interval={INTERVAL}s pid={os.getpid()}")
    if not _lock_ok():
        return 0
    from backend.services.market_maker.runner import shadow_tick_task

    n = 0
    fails = 0
    while True:
        n += 1
        # [F294] 单实例自证：每拍 tick 前确认锁仍属于自己。
        # 事故复盘：旧 worker 在自己循环里读到**新** worker 写下的 PID，
        # 因 `old == os.getpid()` 被判为"锁是我的" ⇒ 静默活到 09:16:28 ✗。
        # 现在一旦锁易主就**立即退出**，绝不再双跑。
        if not _holds_lock():
            log(f"lock lost (holder changed) -> exit at n={n}")
            return 0
        try:
            res = shadow_tick_task(LANE)
            snap = _snapshot(res if isinstance(res, dict) else {})
            if not snap["ok"]:
                fails += 1
            else:
                fails = 0
            try:
                STATUS.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass
            # 日志节流：大约每分钟一条；失败立即记（可诊断 ✓）
            _every = max(20, int(60 / max(INTERVAL, 0.2)))
            if n % _every == 1 or not snap["ok"]:
                log(f"n={n} ok={snap['ok']} reason={snap['reason'][:80]} "
                    f"ticks={snap.get('ticks')} fills={snap.get('fills')} "
                    f"last_tick={snap.get('last_tick_ts')} skip={snap.get('skip_counts')}")
        except Exception as e:
            fails += 1
            log(f"tick 异常(第{fails}次): {e}")
            log(traceback.format_exc()[-600:])
        if once:
            return 0
        # 异常退避：连续失败时拉长间隔，避免刷爆数据库/日志
        time.sleep(min(INTERVAL * (1 + min(fails, 4)), 120.0))


if __name__ == "__main__":
    raise SystemExit(main())
