"""source_attribution — 来源归因 / 信用分 / 出场通道熔断持久化 / 窄 LLM 仲裁（阶段2）。

设计依据：02_详细设计 v2 §2.2/§2.3 + 03 可行性验证。所有入口 try/except 包裹，
热路径只做内存字典操作；JSON 持久化节流（默认 60s），重启后恢复。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_STATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "fusion_attribution.json",
)

_MIN_SAMPLES = 20        # 归因最低样本
_EXPECT_THRESHOLD = 0.0  # 期望值 < 0 → shadow
# [2026-08-31 根因修复] shadow 判定窗口：累计制下旧亏损永远压在期望上，来源
# 一旦 shadow 几乎永无退出（退出需累计净期望回正，而 shadow 期间几乎无新样本）
# ——实测 5/7 币 factor|scalp 来源长期 standdown、短线 406 次拦截。改为滚动
# 窗口：只看最近 N 笔净收益，来源凭近期表现自然复活（旧亏损随时间滚出窗口）。
_ROLLING_WINDOW = int(os.getenv("SOURCE_ATTR_ROLLING_WINDOW", "30") or 30)

# [§81 修复 2026-09-11] 状态文件的"口径标记"：用于把 08-31 的**一次性**迁移
# （剥离累计制 shadow 标志）与"现行滚动窗标志"区分开。缺此标记的旧文件按迁移处理
# **一次**，随后写入标记 ⇒ 之后所有加载都保留滚动窗标志。
# 没有它时，判据 `bool(shadow or breaker_shadow)` 每次加载都会把当前滚动窗标志
# 从磁盘抹掉（实测 13 键/7 真 → 0 键），P20 的重建只恢复内存 ⇒ 持久化作废，
# 且 `EXIT_CHANNEL_REBUILD_ON_LOAD=false` 时重启直接退回"盲窗"。
SHADOW_MODE_KEY = "shadow_mode"
SHADOW_MODE_ROLLING = "rolling"


def _under_pytest() -> bool:
    """pytest 进程判定（避免测试进程把生产状态文件写花，见 §57/§75 纪律）。"""
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def _cfg_float_env(key: str, default: float) -> Optional[float]:
    """读一个浮点环境键（进程内缓存首个生效值；异常/空值回退默认）。

    [§84/P27-A] 这类"闸门参数"必须与生产**同源**读取（`.env` 由启动期 `load_dotenv`
    载入 `os.environ`），因此直接读 `os.environ` 而不是 settings —— 与
    `EXIT_CHANNEL_SHADOW_MIN_N` 等保持一致的读法。
    """
    try:
        raw = os.environ.get(key)
        if raw is None or str(raw).strip() == "":
            return float(default)
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


def normalize_reason(reason: str) -> str:
    """把带叙述的 close_reason 归并到通道键：trend_broken: xxx → trend_broken；[no_progress] ... → [no_progress]。"""
    r = (reason or "").strip()
    if not r:
        return "?"
    if r.startswith("["):
        end = r.find("]")
        return r[: end + 1] if end > 0 else r[:24]
    if ":" in r:
        return r.split(":", 1)[0].strip()
    return r


def _record_attribution_row(*, position_id: int, src: str, nature: str, symbol: str,
                            pnl: float, fee: float, net: float, win: bool,
                            close_reason: str, tier: str) -> None:
    """[U2-2] 归因事实行写入 brain_attribution（**幂等**；异常静默）。

    [2026-09-09 根因修复] 原实现自称「幂等表」但 INSERT 无 ON CONFLICT，表上只有
    `id` 主键 → 每个平仓事件都新增一行。实测 alpha_arena：1323 行 vs 721 个唯一
    `(position_id, src, tier)`，单仓最多重复 18 行。任何按本表做的来源归因都会
    按重复次数加权（本次调查曾因此得出「LLM mid 全亏 -238」的错误结论）。
    迁移 0024 建了唯一索引；此处改用 ON CONFLICT DO UPDATE 保证幂等。
    """
    from datetime import datetime as _dt, timezone as _tz
    from backend.database.connection import SessionLocal
    db = SessionLocal()
    try:
        from sqlalchemy import text
        db.execute(text(
            "INSERT INTO brain_attribution "
            "(position_id, src, nature, symbol, pnl, fee, net, win, close_reason, tier, created_at) "
            "VALUES (:pid, :src, :nat, :sym, :pnl, :fee, :net, :win, :reason, :tier, :ts) "
            "ON CONFLICT (position_id, src, tier) DO UPDATE SET "
            "nature = EXCLUDED.nature, symbol = EXCLUDED.symbol, pnl = EXCLUDED.pnl, "
            "fee = EXCLUDED.fee, net = EXCLUDED.net, win = EXCLUDED.win, "
            "close_reason = EXCLUDED.close_reason, created_at = EXCLUDED.created_at"
        ), {
            "pid": position_id, "src": (src or "unknown")[:32], "nat": (nature or "")[:32],
            "sym": (symbol or "")[:32], "pnl": pnl, "fee": fee, "net": net,
            "win": bool(win), "reason": close_reason, "tier": tier,
            "ts": _dt.now(_tz.utc),
        })
        db.commit()
    except Exception as e:
        logger.debug("[SourceAttr] DB 写穿跳过: %s", e)
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()


class SourceAttribution:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tags: Dict[str, Dict[str, Any]] = {}          # position_id -> {source, nature, symbol}
        self._stats: Dict[str, Dict[str, Any]] = {}         # src|nature|symbol -> {n, wins, gross, fee}
        self._shadow: Dict[str, bool] = {}                  # src|nature|symbol -> shadow
        self._breaker: Dict[str, Dict[str, int]] = {}       # tier|reason -> {n, wins}
        self._breaker_shadow: Dict[str, bool] = {}
        # [§95 2026-09-11 / 目标③「不产生悬挂仓位」] 抑制事件计数（按 `tier|通道`），
        # 供可选上限使用（`EXIT_SUPPRESS_MAX_COUNT`/`EXIT_SUPPRESS_WINDOW_H`，默认 0=关闭）。
        # 仅进程内保存（与其它闸门"重启重置"语义一致），元素为 `time.time()`。
        self._suppress_log: Dict[str, list] = {}
        self._last_save = 0.0
        self._loaded = False

    # ── 持久化 ──
    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            if os.path.exists(_STATE_PATH):
                with open(_STATE_PATH, "r", encoding="utf-8") as f:
                    d = json.load(f)
                _mode = str(d.get(SHADOW_MODE_KEY) or "").strip().lower()
                _legacy_file = _mode != SHADOW_MODE_ROLLING
                # [§81 修复 2026-09-11] 迁移判据**只对旧文件生效一次**。
                # 旧判据 `bool(d.get("shadow") or d.get("breaker_shadow"))` 无法区分
                # "08-31 之前的累计制标志" 与 "现行滚动窗标志"（两者同名同址）⇒
                # 每次加载都触发"剥离 + 回写磁盘"，把滚动窗熔断标志从磁盘抹掉。
                _stale_shadow = _legacy_file and bool(d.get("shadow") or d.get("breaker_shadow"))
                _disk_flags = dict(d.get("breaker_shadow") or {})
                with self._lock:
                    self._tags = d.get("tags", {})
                    self._stats = d.get("stats", {})
                    self._breaker = d.get("breaker", {})
                    if _legacy_file:
                        # [2026-08-31 滚动窗口迁移] 旧 shadow 标志是累计制判定的（全历史
                        # 期望<0 即永久 shadow，无滚动窗口可回放）——加载时清空，改由
                        # 新滚动窗口在后续平仓中重建（20 笔新样本预热期内不 shadow，
                        # 敞口由探针 0.125x/日配额限制）。
                        self._shadow = {}
                        self._breaker_shadow = {}
                    else:
                        # 现行口径：磁盘上的滚动窗**熔断标志**就是真实状态，必须保留
                        # （否则重启后又要等每个通道再平一次才恢复判定 —— P20 的意图）。
                        # ⚠️ 边界：`_shadow`（来源信用闸）**仍不跨重启**，那是 2026-09-02
                        # 明确的既有契约（`test_source_attribution.py::test_persistence_roundtrip`：
                        # "shadow 标志按设计不跨重启；重启后放行一笔，由一笔新平仓立即重建"）。
                        # 本次只修**出场通道熔断标志**的持久化，不动来源信用闸语义。
                        self._shadow = {}
                        self._breaker_shadow = _disk_flags
                if _stale_shadow:
                    # 立即把剥离后的状态回写磁盘：防止空态覆写防护/其他旧进程
                    # 把累计制 shadow 重新合并回内存或磁盘。
                    try:
                        with self._lock:
                            payload = {
                                "ts": time.time(),
                                SHADOW_MODE_KEY: SHADOW_MODE_ROLLING,
                                "tags": dict(self._tags),
                                "stats": dict(self._stats),
                                "shadow": {},
                                "breaker": dict(self._breaker),
                                "breaker_shadow": {},
                            }
                        tmp = _STATE_PATH + f".{os.getpid()}.migrate.tmp"
                        with open(tmp, "w", encoding="utf-8") as f:
                            json.dump(payload, f, ensure_ascii=False)
                        os.replace(tmp, _STATE_PATH)
                        logger.info("[SourceAttr] 已剥离磁盘旧累计制 shadow 标志（迁移至滚动窗口制）")
                    except Exception as e:
                        # [§81 修复] 原为 debug ⇒ 迁移失败（旧标志会被反复当成现行标志）
                        # 完全不可见。
                        logger.warning("[SourceAttr] shadow 迁移回写失败(旧标志可能被反复误判): %s", e)
                logger.info("[SourceAttr] 恢复状态: tags=%d stats=%d breaker=%d 熔断标志=%d（口径=%s）",
                            len(self._tags), len(self._stats), len(self._breaker),
                            len(self._breaker_shadow),
                            "旧文件已迁移" if _legacy_file else SHADOW_MODE_ROLLING)
                # [§77 执行 2026-09-10] **可选**：加载期就重建通道熔断标志，消除"重启盲窗"。
                # 默认关闭（EXIT_CHANNEL_REBUILD_ON_LOAD=false）⇒ 与既有行为完全一致；
                # 打开后用与 record_close 相同的口径恢复每个通道的既有判定。
                # 注意：打开会**减少**离场执行（被 shadow 的通道不再执行），属行为变更，
                # 故默认关、且由决策 P20 决定是否常开。
                try:
                    if os.environ.get("EXIT_CHANNEL_REBUILD_ON_LOAD", "false").strip().lower() in (
                        "1", "true", "yes", "on"
                    ):
                        rebuilt = self.rebuild_breaker_shadow()
                        with self._lock:
                            self._breaker_shadow = dict(rebuilt)
                        logger.warning(
                            "[SourceAttr] 加载期重建通道熔断标志（EXIT_CHANNEL_REBUILD_ON_LOAD=true）："
                            "%d 个通道已评估，其中 %d 个被 shadow",
                            len(rebuilt), sum(1 for v in rebuilt.values() if v),
                        )
                        # [§81 修复 2026-09-11] 重建结果**落盘**（仅在确有差异时写）：
                        #   ① 磁盘状态 = 内存状态，纯读取进程不再把标志"读没"；
                        #   ② `EXIT_CHANNEL_REBUILD_ON_LOAD` 之后关掉（回滚）重启，
                        #      也能从磁盘恢复标志，而不是直接退回"盲窗"。
                        if rebuilt and not _under_pytest() and rebuilt != _disk_flags:
                            self._maybe_save(force=True)
                            logger.info("[SourceAttr] 已把重建后的熔断标志落盘（%d 键，原磁盘 %d 键）",
                                        len(rebuilt), len(_disk_flags))
                except Exception as _rb_err:
                    logger.warning("[SourceAttr] 加载期重建熔断标志失败(不影响启动): %s", _rb_err)
        except Exception as e:
            logger.warning("[SourceAttr] 状态加载失败(以空态启动): %s", e)

    def _maybe_save(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_save < 60:
            return
        self._last_save = now
        try:
            os.makedirs(os.path.dirname(_STATE_PATH), exist_ok=True)
            tmp = _STATE_PATH + f".{os.getpid()}.tmp"
            with self._lock:
                payload = {
                    "ts": now,
                    SHADOW_MODE_KEY: SHADOW_MODE_ROLLING,
                    "tags": dict(self._tags),
                    "stats": dict(self._stats),
                    "shadow": dict(self._shadow),
                    "breaker": dict(self._breaker),
                    "breaker_shadow": dict(self._breaker_shadow),
                }
                empty = not (self._tags or self._stats or self._breaker
                             or self._shadow or self._breaker_shadow)
            # 空覆写防护（M4 custom_factor_store 同款教训）：内存空且磁盘已有非空数据
            # → 拒绝覆盖，并把磁盘内容合并回内存（防多进程/重启窗口清空历史归因）。
            if empty and os.path.exists(_STATE_PATH):
                try:
                    with open(_STATE_PATH, "r", encoding="utf-8") as f:
                        disk = json.load(f)
                    if disk.get("tags") or disk.get("stats") or disk.get("breaker"):
                        # [§81 修复 2026-09-11] 合并时是否丢弃**熔断标志**，取决于磁盘口径：
                        # **旧累计制**文件才丢弃（迁移语义），现行滚动窗口文件必须保留 ——
                        # 原实现无条件清空，等于给"任何一次空态保存"开了一条把熔断标志
                        # 清零的路（实测线上一天触发 5 次）。
                        # `_shadow`（来源信用）按既有契约一律不合并（不跨重启）。
                        _disk_mode = str(disk.get(SHADOW_MODE_KEY) or "").strip().lower()
                        _disk_legacy = _disk_mode != SHADOW_MODE_ROLLING
                        logger.warning(
                            "[SourceAttr] 拒绝空态覆盖磁盘非空状态（多进程防护）→ 合并磁盘内容"
                            "（%s）", "旧口径文件：熔断标志按迁移丢弃" if _disk_legacy
                            else "滚动窗口口径：熔断标志保留",
                        )
                        with self._lock:
                            self._tags = dict(disk.get("tags", {}) or {})
                            self._stats = dict(disk.get("stats", {}) or {})
                            self._shadow = {}
                            self._breaker = dict(disk.get("breaker", {}) or {})
                            self._breaker_shadow = (
                                {} if _disk_legacy else dict(disk.get("breaker_shadow") or {})
                            )
                        return
                except Exception:
                    pass
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(tmp, _STATE_PATH)
        except Exception as e:
            # [§80 修复 2026-09-11] 状态落盘失败原为 debug ⇒ 不可见。它决定"熔断窗能否跨重启保留"，
            # 静默失败会让窗口在重启后退化而无人知晓（§41.2/§51.7 同类纪律）。
            logger.warning("[SourceAttr] 状态保存失败(熔断窗可能不跨重启): %s", e)

    # ── 仓位标签（入场后调用）──
    def tag_position(self, position_id: int, source: str, nature: str = "",
                     symbol: str = "", meta: Optional[Dict[str, Any]] = None) -> None:
        if not position_id:
            return
        self._ensure_loaded()
        with self._lock:
            self._tags[str(int(position_id))] = {
                "source": source or "unknown", "nature": nature or "",
                "symbol": (symbol or "").upper(), "meta": meta or {}, "ts": time.time(),
            }
        # 开仓标签必须立刻落盘：平仓学习桥靠 tag_meta 回查 thesis_id；
        # 不落盘则重启/跨进程后 OWM 调权永久饿死。
        try:
            self._maybe_save(force=True)
        except Exception as exc:
            # [§80 同款] 打标失败 ⇒ 该仓平仓时不会进归因（熔断窗缺一条样本），必须可见
            logger.warning("[SourceAttr] tag_position 落盘跳过(该仓平仓将不进归因): %s", exc)

    # ── 平仓归因 + 通道熔断（close 钩子）──
    def tag_meta(self, position_id: int) -> Optional[Dict[str, Any]]:
        """[U3-2a] 回查仓位标签 meta（含 thesis_id），供学习桥兜底。"""
        self._ensure_loaded()
        with self._lock:
            t = self._tags.get(str(int(position_id)))
            if not t:
                return None
            return dict(t.get("meta") or {})

    def record_close(self, position_id: int, *, pnl: float, fee: float = 0.0,
                     close_reason: str = "", tier: str = "", symbol: str = "",
                     nature: str = "", source: Optional[str] = None) -> Dict[str, Any]:
        # [2026-08-31] 防测试污染：其他测试文件的假平仓会写进
        # data/fusion_attribution.json 与 brain_attribution 表，扰乱来源信用与
        # 出场通道熔断的 shadow 判定。
        #
        # [2026-09-02 收窄拦截面] 原实现把短路挡在**整个函数入口**，连纯内存
        # 统计一起禁掉了 —— 后果是 shadow 判定与通道熔断这两个风控功能在 pytest
        # 下恒为空态、完全无法验证（test_source_attribution.py 6 个用例长期红，
        # 等于该功能回归保护为零）。污染只来自两个副作用：自动落盘与写 DB。
        # 现在只跳过这两处，内存计算照常：
        #   - 本文件测试用 _fresh() 把 _STATE_PATH 指向独立临时文件、并新建实例，
        #     自身完全隔离；需要落盘时显式调 _maybe_save(force=True)，故此处不
        #     自动保存不影响它；
        #   - 其他测试的假平仓仍不触碰磁盘与 DB，进程内内存态随进程结束消失，
        #     且 shadow 需同 key 累积 20+ 笔才成立，偶发假平仓不足以触发。
        _in_pytest = _under_pytest()
        self._ensure_loaded()
        net = float(pnl or 0) - float(fee or 0)
        win = net > 0
        with self._lock:
            tag = self._tags.get(str(int(position_id)))
            src = (source or (tag or {}).get("source") or "unknown")
            nat = nature or (tag or {}).get("nature") or ""
            sym = (symbol or (tag or {}).get("symbol") or "").upper()
            key = f"{src}|{nat}|{sym}"
            st = self._stats.setdefault(
                key, {"n": 0, "wins": 0, "gross": 0.0, "fee": 0.0, "recent": []})
            st["n"] += 1
            st["wins"] += int(win)
            st["gross"] += float(pnl or 0)
            st["fee"] += float(fee or 0)
            # [2026-08-31 根因修复] 滚动窗口 shadow 判定：只看最近 _ROLLING_WINDOW
            # 笔净收益。旧累计制（全历史期望）让来源一旦 shadow 几乎永无退出——
            # 实测 5/7 币 factor|scalp 长期 standdown。滚动窗口下旧亏损滚出后，
            # 近期转正的来源自动复活；近期仍亏的来源保持 shadow（风控不削弱）。
            _rec = st.setdefault("recent", [])
            if not isinstance(_rec, list):
                _rec = []
                st["recent"] = _rec
            _rec.append(net)
            if len(_rec) > _ROLLING_WINDOW:
                _rec = _rec[-_ROLLING_WINDOW:]
                st["recent"] = _rec
            _roll_n = len(_rec)
            _roll_net = sum(_rec)
            if _roll_n >= _MIN_SAMPLES:
                self._shadow[key] = (_roll_net / _roll_n) < _EXPECT_THRESHOLD
            # 出场通道熔断（close_reason×tier 滚动 30 笔 wr<40% → shadow）
            bkey = f"{tier or '?'}|{normalize_reason(close_reason)}"
            bst = self._breaker.setdefault(bkey, {"n": 0, "wins": 0, "recent": []})
            bst["n"] += 1
            bst["wins"] += int(win)
            # [§84 执行 2026-09-11 / 决策 P27-A / 缺陷 #69] 记录**该通道最新样本时间**：
            # 熔断的"证据自锁"（抑制 ⇒ 不再产生样本 ⇒ 胜率永久冻结）需要一把新鲜度尺子；
            # 没有它就只能用两周前的证据拦今天的出场（实测 `mid|trend_broken` 14.1 天）。
            bst["last_ts"] = time.time()
            _brec = bst.setdefault("recent", [])
            if not isinstance(_brec, list):
                _brec = []
                bst["recent"] = _brec
            _brec.append(1 if win else 0)
            if len(_brec) > _ROLLING_WINDOW:
                _brec = _brec[-_ROLLING_WINDOW:]
                bst["recent"] = _brec
            # [2026-08-26 亏损复盘] 熔断阈值环境化：默认 30 笔/40%；震荡市里出血通道
            # 应更早 shadow（8/26 mid|trend_weaken 5 笔 0 胜仍在砍仓）。
            try:
                _shadow_min_n = int(float(os.environ.get("EXIT_CHANNEL_SHADOW_MIN_N", "30") or 30))
                _shadow_max_wr = float(os.environ.get("EXIT_CHANNEL_SHADOW_MAX_WR", "0.40") or 0.40)
            except Exception:
                _shadow_min_n, _shadow_max_wr = 30, 0.40
            # [2026-08-31] 通道熔断同样改滚动窗口（窗口=min(配置值, ROLLING_WINDOW)）
            _bwin_n = min(_shadow_min_n, _ROLLING_WINDOW)
            if len(_brec) >= _bwin_n:
                wr = sum(_brec[-_bwin_n:]) / _bwin_n
                self._breaker_shadow[bkey] = wr < _shadow_max_wr
            result = {"key": key, "n": st["n"], "net": round(st["gross"] - st["fee"], 4),
                      "shadow": bool(self._shadow.get(key)), "bkey": bkey,
                      "breaker_shadow": bool(self._breaker_shadow.get(bkey))}
        if not _in_pytest:
            self._maybe_save()
            # [U2-2 2026-08-25] M0 写穿：归因事实落 DB（brain_attribution；失败不影响 JSON 主链路）
            try:
                _record_attribution_row(
                    position_id=int(position_id), src=src, nature=nat, symbol=sym,
                    pnl=float(pnl or 0), fee=float(fee or 0), net=net, win=win,
                    close_reason=(close_reason or "")[:120], tier=(tier or "")[:16],
                )
            except Exception:
                pass
        return result

    # ── 查询接口 ──
    def credit(self, source: str, nature: str = "", symbol: str = "") -> float:
        self._ensure_loaded()
        with self._lock:
            if self._shadow.get(f"{source}|{nature}|{(symbol or '').upper()}"):
                return 0.0
        return 1.0

    def exit_channel_shadow(self, close_reason: str, tier: str) -> bool:
        self._ensure_loaded()
        with self._lock:
            return bool(self._breaker_shadow.get(f"{tier or '?'}|{normalize_reason(close_reason)}"))

    def exit_channel_evidence_fresh(self, close_reason: str, tier: str) -> Tuple[bool, Optional[float], float]:
        """该通道的熔断**证据是否新鲜**？返回 `(fresh, age_days, limit_days)`。

        [§84 执行 2026-09-11 / 决策 P27-A / 缺陷 #69] 背景：抑制发生在记账**之前**，
        被抑制的通道不再产生样本 ⇒ 滚动窗永久冻结（实测 `mid|trend_broken` 最新样本
        已 14.1 天而仍会抑制下一次离场）。本方法给闸门一把新鲜度尺子：

          * `BREAKER_EVIDENCE_STALE_DAYS`（生效值，`0`=关闭该约束 ⇒ 永远 fresh）；
          * 找不到 `last_ts`（旧状态文件/窗口无记录）⇒ **不新鲜**（无从证明 ⇒ 不抑制，
            与 P17「判据 stale 即不拒单」同款 fail-open），由 `_audit_ml/Z220`/回填脚本补时间戳；
          * 返回 `age_days=None` 表示"无时间戳"。
        """
        limit = _cfg_float_env("BREAKER_EVIDENCE_STALE_DAYS", 7.0)
        key = f"{tier or '?'}|{normalize_reason(close_reason)}"
        self._ensure_loaded()
        with self._lock:
            bst = (self._breaker or {}).get(key) or {}
        ts = bst.get("last_ts")
        if limit is None or limit <= 0:
            return True, None, 0.0
        if not ts:
            return False, None, float(limit)
        try:
            age_days = (time.time() - float(ts)) / 86400.0
        except (TypeError, ValueError):
            return False, None, float(limit)
        return (age_days <= float(limit)), age_days, float(limit)

    # ── [§95 2026-09-11 / 目标③「不产生悬挂仓位」] 抑制计数（可选上限的输入）──
    def note_suppression(self, close_reason: str, tier: str) -> int:
        """记录一次**实际生效**的抑制（由闸门在抑制成功时调用），返回该键窗口内计数。"""
        self._ensure_loaded()
        key = f"{tier or '?'}|{normalize_reason(close_reason)}"
        now = time.time()
        with self._lock:
            log = self._suppress_log.setdefault(key, [])
            log.append(now)
            window = _cfg_float_env("EXIT_SUPPRESS_WINDOW_H", 24.0) or 24.0
            cutoff = now - window * 3600.0
            self._suppress_log[key] = [t for t in log if t >= cutoff]
            return len(self._suppress_log[key])

    def suppression_count(self, close_reason: str, tier: str) -> int:
        """该 `tier|通道` 在窗口（`EXIT_SUPPRESS_WINDOW_H`，默认 24h）内的抑制次数。"""
        self._ensure_loaded()
        key = f"{tier or '?'}|{normalize_reason(close_reason)}"
        window = _cfg_float_env("EXIT_SUPPRESS_WINDOW_H", 24.0) or 24.0
        cutoff = time.time() - window * 3600.0
        with self._lock:
            log = self._suppress_log.get(key) or []
            return sum(1 for t in log if t >= cutoff)

    # ── [§77 执行 2026-09-10] 加载期重建通道熔断标志 ──
    def rebuild_breaker_shadow(self) -> Dict[str, bool]:
        """由**已持久化**的 `_breaker` 滚动窗重算 `_breaker_shadow`（纯函数，不读 DB）。

        背景（§77.3 实证）：`_ensure_loaded()` 出于 2026-08-31 的"累计制→滚动窗制"迁移
        **刻意清空** `_breaker_shadow`，而标志只在 `record_close()` 里按**被平仓的那个通道**
        更新。后果：**每次重启后**所有通道的熔断标志都为空，要等该通道**下一次平仓**才重建 ——
        这段"盲窗"里历史胜率 0–10% 的通道照常执行离场。

        本函数用与 `record_close()` **完全相同**的阈值/窗口口径从 `_breaker` 重算，
        因此结果等价于"每个通道刚平过一次"（不引入新样本，只**恢复**已有判定）。
        """
        try:
            _min_n = int(float(os.environ.get("EXIT_CHANNEL_SHADOW_MIN_N", "30") or 30))
            _max_wr = float(os.environ.get("EXIT_CHANNEL_SHADOW_MAX_WR", "0.40") or 0.40)
        except (TypeError, ValueError):
            _min_n, _max_wr = 30, 0.40
        win_n = min(_min_n, _ROLLING_WINDOW)
        out: Dict[str, bool] = {}
        for bkey, bst in (self._breaker or {}).items():
            try:
                rec = bst.get("recent") if isinstance(bst, dict) else None
                if not isinstance(rec, list) or len(rec) < win_n:
                    continue
                wr = sum(1 for x in rec[-win_n:] if x) / win_n
                out[str(bkey)] = wr < _max_wr
            except Exception:
                continue
        return out

    def snapshot(self) -> Dict[str, Any]:
        self._ensure_loaded()
        with self._lock:
            return {
                "stats": {k: dict(v) for k, v in self._stats.items()},
                "shadow": dict(self._shadow),
                "breaker": {k: dict(v) for k, v in self._breaker.items()},
                "breaker_shadow": dict(self._breaker_shadow),
            }


attribution = SourceAttribution()


# ── 窄 JSON LLM 仲裁（thesis 冲突时 1 次调用；失败返回 None → 调用方保守 hold）──
def llm_arbitrate_conflict(*, symbol: str, direction: str, thesis_dir: str,
                           thesis_conf: float, pwin: float, factor_score: float,
                           account_id: int = 0, trading_mode: str = "paper") -> Optional[bool]:
    """返回 True=放行(0.25x) / False=否决 / None=调用失败（保守 hold）。"""
    if os.getenv("FUSION_ARBITRATE_LLM", "true").strip().lower() in ("0", "false", "off"):
        return None
    try:
        from backend.services.llm_config_service import (
            get_llm_config_for_account, call_llm_api_sync,
        )
        cfg = get_llm_config_for_account(account_id, tier="quick") if account_id else None
        if not cfg:
            return None
        prompt = (
            "你是交易仲裁员。因子引擎给出一个短线信号，但长周期 LLM thesis 方向相反。"
            "请用一行 JSON 裁决是否放行（只输出 JSON）。\n"
            f"symbol={symbol} 因子方向={direction} thesis方向={thesis_dir} "
            f"thesis置信={thesis_conf:.2f} 元模型胜率={pwin:.3f} 因子分={factor_score:.0f}\n"
            'JSON 格式：{"allow": true/false, "reason": "<=30字"}。'
            "规则：证据不足默认 allow=false（宁可错过）。"
        )
        resp = call_llm_api_sync(
            cfg, messages=[{"role": "user", "content": prompt}],
            temperature=0, max_tokens=120, response_format={"type": "json_object"},
            timeout_s=8, caller="fusion_arbitrate",
        )
        content = (resp or {}).get("content") or ""
        import re as _re
        m = _re.search(r'\{\s*"allow"\s*:\s*(true|false)', content, _re.IGNORECASE)
        if not m:
            return None
        return m.group(1).lower() == "true"
    except Exception as e:
        logger.debug("[FusionArb] LLM 仲裁调用失败: %s", e)
        return None


# ── 每周 pwin 分桶验证（衰减监控；scripts/_fusion_weekly_validation.py 调用）──
def weekly_pwin_validation(days: int = 7) -> Dict[str, Any]:
    """统计最近 N 天已结算信号 pwin>=0.55 桶的胜率与净收益（OOS 衰减监控）。"""
    import psycopg
    try:
        con = psycopg.connect("host=localhost port=5432 dbname=alpha_arena user=laobao password=alpha_pass")
        cur = con.cursor()
        cur.execute("""
            SELECT COUNT(*),
                   SUM(CASE WHEN COALESCE(win,false) THEN 1 ELSE 0 END),
                   ROUND(COALESCE(SUM(net_ret),0)::numeric,6)
            FROM scalp_signal_log
            WHERE settled AND created_at >= now() - make_interval(days => %s)
              AND (features_json::json->>'meta_p_win')::float >= 0.55
        """, (days,))
        n, w, s = cur.fetchone()
        con.close()
        wr = (w or 0) / n if n else 0.0
        return {"days": days, "n": int(n or 0), "wr": round(wr, 4),
                "net": float(s or 0), "ok": n >= 100 and wr >= 0.55}
    except Exception as e:
        logger.warning("[FusionArb] 每周 pwin 验证失败: %s", e)
        return {"days": days, "n": 0, "wr": 0.0, "net": 0.0, "ok": False, "error": str(e)[:120]}
