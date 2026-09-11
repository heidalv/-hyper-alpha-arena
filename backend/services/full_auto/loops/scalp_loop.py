"""
短线因子独立循环（整改#8 scalp_loop 拆分）。

从 full_auto_trading_service._run_scalp_independent 迁出；
monolith 保留 thin shim 转发，C2/C3 golden 对拍不变。
"""
from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING, Dict, List

from sqlalchemy import text as _sa_text

if TYPE_CHECKING:
    from backend.services.full_auto_trading_service import FullAutoTradingService

logger = logging.getLogger(__name__)


def _pct_missing(v) -> bool:
    """[S1 2026-08-21] 涨跌幅缺失判定：仅 None/NaN 算缺失。

    0（横盘）与负值（下跌）都是合法观测，不得触发重算——原实现 `<= 0`
    会把下跌样本改写成「按 DF 重算的值」，污染 regime 判定。
    """
    if v is None:
        return True
    try:
        import math
        return math.isnan(float(v))
    except (TypeError, ValueError):
        return False


def _kline_close_at(kl, pos_from_end: int):
    """[S1 2026-08-21] 取倒数第 N 根收盘价，兼容 DataFrame 与 list[dict]。

    原实现 `_klines[-1].get("close")` 对 DataFrame 取列名 -1 必然 KeyError，
    被裸 except 吞掉——补数从未生效。DataFrame 必须走 iloc。
    返回 None 表示取不到（调用方跳过补数）。
    """
    try:
        if hasattr(kl, "iloc"):  # pandas DataFrame / Series
            v = kl.iloc[-pos_from_end]["close"]
            return float(v) if v is not None else None
        idx = len(kl) - pos_from_end
        if 0 <= idx < len(kl):
            row = kl[idx]
            if isinstance(row, dict):
                v = float(row.get("close") or 0)
                return v or None
            if isinstance(row, (list, tuple)) and len(row) > 4:
                v = float(row[4])
                return v or None
    except Exception:
        return None
    return None


def run_scalp_independent(svc: "FullAutoTradingService", session_id: str, tick: int) -> None:
    """短线因子独立交易（2026-06-18 三层架构）。"""
    from backend.services.resource_guard import hot_path_context

    with hot_path_context("scalp_tick"):
        _run_scalp_independent_inner(svc, session_id, tick)


def _run_scalp_independent_inner(svc: "FullAutoTradingService", session_id: str, tick: int) -> None:
    self = svc
    # [C1] 后台交易循环不在 HTTP 请求上下文,中间件不会为它设 tenant_id。
    # 非超用户 DB 角色下 RLS 会 fail-closed(0 行)破坏交易。系统循环是可信的
    # (等同 admin),设 system_identity 走 RLS 短路。在本 tick 全程生效,覆盖本
    # 函数内所有 SessionLocal(_db_sym / _db / _snap_db)。注意:线程不继承 ContextVar,
    # 故下方 _async_scalp_review 线程内需另行 set_system_identity()。
    from backend.core.tenant import set_system_identity
    set_system_identity()
    from backend.services.budget_service import budget_service
    from backend.services.scalp_factor_router import scalp_factor_router

    # 获取 session 的 symbols + account（统一 universe）
    _session_info = self._running_sessions.get(session_id, {})
    from backend.database.connection import SessionLocal
    from backend.database.models import Account, FullAutoSession
    _db_sym = SessionLocal()
    try:
        _session_row = _db_sym.query(FullAutoSession).filter(
            FullAutoSession.session_id == session_id
        ).first()
        symbols = self._resolve_session_trade_symbols(_session_row, _db_sym) if _session_row else []
    finally:
        _db_sym.close()
    if not symbols:
        symbols = _session_info.get("symbols") or self._orch_bg_symbols or ["BTC"]

    logger.info(f"[ScalpRouter独立] tick#{tick} 扫描 symbols={symbols}")

    # [2026-08-08 P1-4] 周期性热载进化因子 + 短线活跃集衰减复检，
    # 让日级/修复晋升的 evo_* / 公式因子进入本进程 FACTORS，无需重启。
    try:
        _reload_every = int(os.getenv("SCALP_EVO_HOT_RELOAD_EVERY_N", "30") or 30)
        if _reload_every > 0 and int(tick) % _reload_every == 0:
            from backend.services.factor_engine.base_factors import factor_engine as _fe
            _n = int(_fe.hot_reload() or 0)
            if _n:
                logger.info("[ScalpRouter独立] tick#%s 热载进化/公式因子 +%s", tick, _n)
            if int(tick) % max(_reload_every * 4, 1) == 0:
                from backend.services.factor_engine.scalp_active_factor_set import (
                    scalp_active_factor_set,
                )
                _pr = scalp_active_factor_set.recheck_and_prune()
                logger.info("[ScalpRouter独立] 短线活跃集 prune: %s", _pr)
    except Exception as _hr_err:
        logger.debug("[ScalpRouter独立] evo 热载跳过: %s", _hr_err)

    # 获取市场快照（从编排器缓存，可能为空——不阻断，K线自己拉）
    market_summary = self._market_scan_cache or {}
    # 不再因 market_summary 空就 return——K线数据自己从 DB 拉

    # 获取账户权益
    from backend.database.connection import SessionLocal, release_idle_txn
    from backend.database.models import FullAutoSession
    _db = SessionLocal()

    def _release_idle_txn() -> None:
        """[2026-09-02 挂事务修复] 结束当前"只读"事务，释放连接的 idle-in-transaction 状态。

        本函数体 2000+ 行同步跑在线程里：开头几条 SELECT 后事务就一直开着，之后每个
        币的 K 线/因子/LLM/交易所调用期间连接都是 idle-in-transaction。LeakGuard 三天
        实测每小时强杀 10-20 个这类事务（paper_positions 176 次、full_auto_sessions
        142 次为首），被杀后本 tick 后续 _db 操作全部报错——决策做了、单落不了库。
        语义见 connection.release_idle_txn：有未提交写入时不动，交给下方既有提交流程。
        """
        release_idle_txn(_db, where="scalp_loop")

    try:
        session_row = _db.query(FullAutoSession).filter(
            FullAutoSession.session_id == session_id
        ).first()
        if not session_row:
            return
        # [2026-09-03 v3 F1d] 本循环只负责"新开"短线仓：会话不在 running/defensive
        # （paused / drawdown_limit / stopped）时直接退出。此前无此检查，会话被暂停
        # 或触发回撤硬闸后短线仍在开仓，与 midlong_loop/trading_cycle_loop 口径不一致。
        if (getattr(session_row, "status", "") or "") not in ("running", "defensive"):
            return
        # 用正确的交易账户ID（paper_account_id，不是 session.account_id）
        trading_acct_id = self._get_trading_account_id(_db, session_row)
        # [2026-07-10 修复] 下方代码大量引用 account_id 变量，但实际变量名是
        # trading_acct_id → 导致所有短线下单 NameError: name 'account_id' is not defined。
        # 这里加别名，一次性修复所有引用点（下单/校准/门控/杠杆计算/持仓查询等）。
        account_id = trading_acct_id
        account = _db.query(Account).filter(
            Account.id == trading_acct_id
        ).first()
        if not account:
            return
        # 从 DB 直接查余额（get_balance 可能返回 None——依赖内存状态）
        _is_live_session = (getattr(session_row, "trading_mode", "paper") or "paper").strip().lower() == "live"
        if _is_live_session:
            # [2026-08-28 实盘打通] live 会话权益 = 币安真实余额（用户流快照→REST）
            from backend.services.full_auto.live_equity import get_live_equity
            equity = get_live_equity(account, trading_acct_id)
        else:
            from backend.database.models import PaperBalance
            from backend.services.paper_trading_engine import paper_engine
            _bal_row = _db.query(PaperBalance).filter(
                PaperBalance.account_id == trading_acct_id
            ).first()
            equity = float(getattr(_bal_row, "total_equity", 0) or getattr(_bal_row, "equity", 0) or 0) if _bal_row else 0.0
        # [S5 2026-08-21] 无余额快照 → 本轮不开仓（equity_unavailable）。
        # 原实现用 sum(margin)*3 反推权益：只有已有持仓才估得出、且估值失真
        # 会直接污染 sizing/层预算/风险上限等全部下游计算。
        if equity <= 0:
            logger.warning(
                "[ScalpRouter独立] equity_unavailable：账户 %s 无余额快照，本轮跳过开仓",
                trading_acct_id,
            )
            return
        account_id = trading_acct_id

        # 本 tick 已开仓数（防一波行情所有币齐开，2026-06-22 新增）
        _scalp_opens_this_tick = 0
        _scalp_tick_results: List[tuple] = []
        _scalp_block_counts: Dict[str, int] = {}

        def _bump_block(reason: str) -> None:
            _scalp_block_counts[reason] = _scalp_block_counts.get(reason, 0) + 1

        # [2026-07-11 性能修复] 原逻辑在下方 for 循环内，每个触发信号的 symbol 都单独
        # 发 3 次 PaperPosition 查询（同方向仓位/同方向计数/反方向计数），N 个 symbol
        # 命中信号时就是 3N 次 round-trip。这里改为循环开始前一次性拉取该账户全部
        # open + trade_nature='scalp' 持仓，按 (symbol, side) 分组存内存，循环内改用
        # 字典查找。同一 tick 内每个 symbol 只会被扫描一次，不存在"本 tick 内其它
        # symbol 开仓后过期"的问题，语义与逐次查询完全等价。
        from backend.database.models import PaperPosition
        _all_scalp_positions = (
            _db.query(PaperPosition)
            .filter(
                PaperPosition.account_id == account_id,
                PaperPosition.status == "open",
                PaperPosition.trade_nature == "scalp",
            )
            .all()
        )
        _scalp_pos_by_key: Dict[tuple, List[PaperPosition]] = {}
        for _p in _all_scalp_positions:
            _scalp_pos_by_key.setdefault((_p.symbol, _p.side), []).append(_p)

        # [2026-08-22 M0-4] 每日开仓上限接线（SCALP_DAILY_OPEN_CAP 原是死配置，
        # 无任何消费点）。按账户统计今日已开 scalp 仓，超限后本 tick 及后续 tick
        # 不再开新仓（计数在每个成功开仓后 +1）。
        _scalp_daily_cap = 0
        _scalp_opened_today = 0
        # [2026-09-03 v3 方向7] 配额数字单一来源：risk.daily_quota（runtime_tuning.scalp_daily_cap），
        # 不再直接读 settings.SCALP_DAILY_OPEN_CAP（旧 env 值已由 daily_quota 一次性播种进文件）。
        try:
            from backend.services.risk.daily_quota import cap_for as _quota_cap_for
            _scalp_daily_cap = int(_quota_cap_for("scalp") or 0)
        except Exception:
            try:
                from backend.config.settings import SCALP_DAILY_OPEN_CAP as _cap_ov
                _scalp_daily_cap = int(_cap_ov or 0)
            except Exception:
                _scalp_daily_cap = 0
        # [2026-08-28 实盘零成交修复] 实盘引导期每日开仓上限（默认 5 笔）：
        # 引导期门槛放宽后必须有每日硬顶兜底（配合风控硬顶/日亏熔断）。
        # 引导期满或 LIVE_SCALP_BOOTSTRAP_ENABLED=false 时退回 SCALP_DAILY_OPEN_CAP。
        try:
            if _is_live_session:
                from backend.services.full_auto.live_gate_policy import (
                    live_bootstrap_active,
                    live_scalp_daily_open_cap,
                )
                if live_bootstrap_active(account_id):
                    _live_cap = int(live_scalp_daily_open_cap())
                    if _live_cap > 0:
                        _scalp_daily_cap = min(_scalp_daily_cap or _live_cap, _live_cap)
        except Exception as _live_cap_err:
            logger.debug("[ScalpRouter独立] 实盘引导期每日上限读取失败: %s", _live_cap_err)
        if _scalp_daily_cap > 0:
            try:
                from datetime import datetime as _dt_cap, timezone as _tz_cap
                from backend.database.models import PaperPosition as _PP_cap
                _day_start = _dt_cap.now(_tz_cap.utc).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                _scalp_opened_today = int(
                    _db.query(_PP_cap)
                    .filter(
                        _PP_cap.account_id == account_id,
                        _PP_cap.trade_nature == "scalp",
                        _PP_cap.opened_at >= _day_start,
                    )
                    .count() or 0
                )
            except Exception as _cap_err:
                logger.debug(f"[ScalpRouter独立] 每日开仓计数失败: {_cap_err}")
                _scalp_daily_cap = 0  # 计数失败不拦截开仓（避免误杀），仅本 tick 失效

        # [2026-07-11 性能修复] 同理，5m K线此前逐 symbol 单独查询（未命中快照/缓存
        # 时每个 symbol 一次 DB round-trip）。这里对本 tick 全部 symbol 一次性批量
        # 预取（缓存命中的直接复用，未命中的合并成一次 IN(...) 查询），循环内先查
        # 这份预取结果，只有预取也未命中时才回退到逐个查询（行为不变，仅减少查询次数）。
        # 5m K线数据源（2026-07-31 修）：此前硬编码 hyperliquid，但账户实际在
        # asterdex 交易，而 hyperliquid 的 5m 采集长期滞后（实测 75 分钟，同时刻
        # asterdex 仅 5 分钟）——等于用一小时前的K线算短线因子。改为跟随活跃交易所；
        # 如需回滚可设 SCALP_KLINE_EXCHANGE=hyperliquid。
        _kl_ex = (os.getenv("SCALP_KLINE_EXCHANGE", "") or "").strip().lower() or None
        if not _kl_ex:
            try:
                from backend.services.exchange_config import (
                    get_active_exchange as _get_active_ex,
                )
                _kl_ex = (_get_active_ex() or "").strip().lower() or None
            except Exception:
                _kl_ex = None
        _kl_ex = _kl_ex or "asterdex"

        _batch_klines_5m: Dict[str, list] = {}
        try:
            from backend.services.kline_data_service import kline_service as _ks_batch
            _batch_klines_5m = _ks_batch.get_klines_batch_from_db(
                [s.upper() for s in symbols], "5m", 100, exchange=_kl_ex,
            )
        except Exception as _batch_kline_err:
            logger.debug(f"[ScalpRouter独立] 批量K线预取失败，回退逐个查询: {_batch_kline_err}")

        # [2026-09-02 挂事务修复] 准备段的读事务到此结束；逐币循环开头再释放一次，
        # 让上一个币迭代内的读查询不会把事务挂到几十秒后的下单提交。
        _release_idle_txn()

        # 扫描每个 symbol
        for sym in symbols:
            _release_idle_txn()
            # [2026-08-24 M0-H2] 每币迭代让渡 GIL：单进程内 scalp 循环（每 10s ×
            # 多币）连续 Python 计算（K线处理/因子/评估）会把事件循环饿死数秒，
            # /api/health 实测 1-8.7s → 前端超时误报"后端挂了"、平台监管按慢响应
            # 误杀重启（重启风暴的另一半根因）。sleep(0) 只释放一个调度窗口，
            # 对总耗时影响 <0.5%，让 HTTP 处理及时排上队。
            time.sleep(0)
            # [P1-2 2026-07-30] 周末/低流动性时段过滤
            # 加密24/7但周末流动性低40-60%，UTC 22-00最薄，插针频繁
            from datetime import datetime, timezone as _tz
            _now = datetime.now(_tz.utc)
            _is_weekend = _now.weekday() >= 5  # Saturday=5 Sunday=6
            _utc_hour = _now.hour
            _liquidity_mult = 1.0
            if _is_weekend:
                _liquidity_mult *= 0.5  # 周末缩仓50%
            if 22 <= _utc_hour or _utc_hour < 1:  # UTC 22-00最薄
                _liquidity_mult *= 0.7  # 再缩30%

            # ── 主动获取 K线 ──
            _md = dict(market_summary.get(sym) or {})
            # [2026-09-03 审查修正 A] 把会话交易模式带进因子融合：影子因子
            # （held-out 未过 role=paper / 进化仓 PAPER）在实盘会话权重归零、
            # 模拟会话保持封顶——见 paper_factor_policy。Router 回退路径复制的
            # 是这份 _md，同样带上。
            _md["trading_mode"] = "live" if _is_live_session else "paper"
            # 修复4：数据可靠性检查——data_reliable=False 或 data_stale 时跳过
            if not _md.get("data_reliable", True) or _md.get("data_stale"):
                logger.info(f"[ScalpRouter独立] {sym} 数据不可靠/过期，跳过")
                continue
            # 修复 BUG G：market_summary 用 current_price 不是 price
            if "current_price" in _md and "price" not in _md:
                _md["price"] = _md["current_price"]
                _md["mark_price"] = _md["current_price"]

            # ── 短线单币耗时把脉（临时诊断，2026-07-08）──
            # total 小但币间隔大 → 与主AI/中长线循环争抢；total 大 → 短线计算本身重。
            _prof: Dict[str, float] = {}
            _pf_ts = time.perf_counter()
            # 从 DB 拉 K线（Fix: 短线用 5m 而非 15m，与 signal_pre_screener 对齐）
            try:
                import pandas as pd

                from backend.services.kline_data_service import kline_service
                # 2026-07-06 整改（unified_data_pool 全量整合 · 因子引擎/scalp 决策取数）：
                # ScalpRouter 独立路径此前直接拉 5m 重算因子，与主链快照不同时点——scalp 用的
                # 因子输入 K 线可能比决策快照更新/更旧。灰度开关开启时优先复用快照的 5m
                # （与主链决策同一时点），快照缺失/过薄/过期或开关关时回退 DB（行为向后兼容）。
                _raw_klines = None
                try:
                    if os.getenv("COORDINATOR_CONSUME_SNAPSHOT_KLINES", "false").lower() in ("1", "true", "yes", "on"):
                        from backend.services.unified_data_pool import unified_data_pool
                        _snap5 = unified_data_pool.get_snapshot(
                            max_age=float(os.getenv("COORDINATOR_SNAPSHOT_MAX_AGE_SEC", "180") or 180)
                        )
                        if _snap5 is not None and getattr(_snap5, "klines", None):
                            _df5 = _snap5.klines.get((sym.upper(), "5m"))
                            if _df5 is not None and len(_df5) > 20:
                                _raw_klines = _df5.tail(100).to_dict("records")
                except Exception as _snap5_err:
                    logger.debug(f"[ScalpRouter独立] {sym} 快照5m复用失败，回退DB: {_snap5_err}")
                    _raw_klines = None
                if _raw_klines is None:
                    _raw_klines = _batch_klines_5m.get(sym.upper())
                if _raw_klines is None:
                    _raw_klines = kline_service.get_klines_from_db(
                        sym.upper(), "5m", 100, exchange=_kl_ex,
                    )
                if _raw_klines and len(_raw_klines) > 20:
                    _klines_df = pd.DataFrame(_raw_klines)
                    _md["klines"] = _klines_df
                    # 从 K线取最新价格（market_summary 可能没有 sym 数据）
                    if "price" not in _md and "close" in _klines_df.columns:
                        _latest_close = float(_klines_df.iloc[-1]["close"])
                        _md["price"] = _latest_close
                        _md["mark_price"] = _latest_close
                else:
                    logger.debug(f"[ScalpRouter独立] {sym} K线不足: {len(_raw_klines) if _raw_klines else 0}")
            except Exception as _kline_err:
                logger.warning(f"[ScalpRouter独立] {sym} K线获取失败: {_kline_err}")
            _prof["kline5m"] = time.perf_counter() - _pf_ts; _pf_ts = time.perf_counter()

            # 无 K线或数据不足则跳过（Fix 22c: 数据不足的币不交易）
            if "klines" not in _md:
                continue
            _scalp_kline_n = len(_md["klines"]) if hasattr(_md["klines"], '__len__') else 0
            if _scalp_kline_n < 30:
                logger.info(f"[ScalpRouter独立] {sym} K线仅{_scalp_kline_n}根(<30)，跳过（数据不足不交易）")
                continue

            # [2026-08-16 冻结数据防护] asterdex 未上线/低流动性币返回平盘假数据
            # （APT 5m 100根 high==low 99根且 volume 全 0；VIRTUAL 120根全平）。
            # 这类数据会产生满分垃圾信号（如 APT score=100 short），必须跳过。
            _kl_df = _md["klines"]
            try:
                _vol_sum = float(pd.to_numeric(_kl_df.get("volume"), errors="coerce").fillna(0).sum()) if "volume" in _kl_df.columns else 0.0
                _hh = pd.to_numeric(_kl_df.get("high"), errors="coerce")
                _ll = pd.to_numeric(_kl_df.get("low"), errors="coerce")
                _flat_cnt = int((_hh == _ll).sum())
                if _vol_sum <= 0 or _flat_cnt >= int(len(_kl_df) * 0.9):
                    logger.info(
                        f"[ScalpRouter独立] {sym} K线冻结(volsum={_vol_sum:.1f}, "
                        f"flat={_flat_cnt}/{len(_kl_df)})，跳过（避免垃圾信号）"
                    )
                    _md.pop("klines", None)
                    continue
            except Exception:
                pass

            # ── 数据新鲜度硬门（2026-07-31）──
            # 「根数够」不等于「数据新」：AI 选币轮出的币会停止采K线，但只要它还有
            # active 策略就仍留在扫描 universe 里，于是拿冻结的旧K线打分下单——实测
            # 出现过用 25 小时前的 5m K线开仓。这里以最新一根K线时间为准，过期只拦
            # 开仓（本循环不负责平仓，已有持仓的退出走独立路径，不受影响）。
            try:
                _kl = _md["klines"]
                _last_ts = 0
                if hasattr(_kl, "iloc") and "timestamp" in getattr(_kl, "columns", []):
                    _last_ts = int(_kl.iloc[-1]["timestamp"] or 0)
                elif isinstance(_kl, list) and _kl and isinstance(_kl[-1], dict):
                    _last_ts = int(_kl[-1].get("timestamp") or 0)
                if _last_ts > 1e12:  # 毫秒 → 秒
                    _last_ts = int(_last_ts / 1000)
                if _last_ts > 0:
                    _stale_sec = time.time() - _last_ts
                    _max_stale = float(os.getenv("SCALP_MAX_KLINE_STALE_SEC", "900") or 900)
                    if _stale_sec > _max_stale:
                        logger.warning(
                            f"[ScalpRouter独立] {sym} 5mK线已过期 {_stale_sec / 60:.0f} 分钟"
                            f"(上限{_max_stale / 60:.0f}分钟)，跳过开仓——避免用陈旧数据决策"
                        )
                        continue
            except Exception as _fresh_err:
                logger.debug(f"[ScalpRouter独立] {sym} K线新鲜度检查跳过: {_fresh_err}")

            # ── [PostFill Agent 2026-08-30] 持仓模式切换 ──
            # 该币已有 open scalp 仓 → 跳过入场分析重路径（因子/融合/门禁全套），
            # 改为轻量持仓监视：状态机复审（认错fast_cut/保本/ATR尾随/翻脸收紧）
            # 并执行。分批止盈仍统一归 paper 引擎 3s/30s 保护块（含可行性门）。
            # 历史注释"本循环不负责平仓"自本块起修正：本循环开始负责 scalp 仓的
            # 主动管理（平仓经 _close_position_live_aware，live/paper 双分流）。
            _pf_positions = (
                (_scalp_pos_by_key.get((sym, "long")) or [])
                + (_scalp_pos_by_key.get((sym, "short")) or [])
            )
            if _pf_positions:
                try:
                    from backend.services.full_auto.scalp_position_review import (
                        run_scalp_position_review,
                    )
                    _pf_out = run_scalp_position_review(
                        self, _db, session_row, account_id, sym, _pf_positions, _md,
                    )
                    if _pf_out.get("actions"):
                        logger.info(
                            "[PostFill][ScalpRouter] %s 持仓复审动作: %s",
                            sym, _pf_out["actions"],
                        )
                except Exception as _pf_err:
                    logger.warning("[PostFill][ScalpRouter] %s 持仓复审异常: %s", sym, _pf_err)
                continue  # 已持仓：本 tick 不做入场分析

            # ── 数据契约兜底：补全 volatility_value（2026-07-09 方案A）──
            # 短线严格数据校验（STRICT_DATA_GATE）要求 tier=short 必带 volatility_value，
            # 而 market_summary 对部分币未提供该字段 → 所有短线单（趋势打法 + 震荡MR）
            # 都被 [StrictData] missing=volatility_value 拦在最后一步、无法成交。这里用
            # 已就绪的 5m K线现算 ATR%（atr/price，与 structure_stop_calculator 口径一致）
            # 补上，仅在缺失/为 0 时兜底，绝不覆盖上游已有值。
            try:
                if float(_md.get("volatility_value") or 0) <= 0:
                    from backend.services.factor_engine.base_factors import (
                        factor_engine as _fe_vol,
                    )
                    _atr_ratio = float(_fe_vol.compute_atr_ratio(_md["klines"]))
                    if _atr_ratio > 0:
                        _md["volatility_value"] = round(_atr_ratio, 6)
                        _md.setdefault("atr_pct", _md["volatility_value"])
            except Exception as _vol_err:
                logger.debug(
                    f"[ScalpRouter独立] {sym} volatility_value 兜底失败: {_vol_err}"
                )

            # [S1 2026-08-21 修复] 补全 classify_regime 所需字段。
            # 原实现两处错误：① 触发条件 `<= 0` 把横盘 0 和真实负值也当缺失重算；
            # ② `_klines[-1].get("close")` 对 DataFrame 必然 KeyError、被裸
            # except 吞掉 → 补数从未生效。现改为：仅缺失(None/NaN)才补，iloc 取值。
            try:
                _kl_bf = _md.get("klines")
                if _kl_bf is not None and len(_kl_bf) >= 12:
                    if _pct_missing(_md.get("price_change_1h_pct")):
                        _c_now = _kline_close_at(_kl_bf, 1)
                        _c_prev = _kline_close_at(_kl_bf, 12)
                        if _c_now and _c_prev and _c_prev > 0:
                            _md["price_change_1h_pct"] = round((_c_now - _c_prev) / _c_prev * 100, 4)
                    if _pct_missing(_md.get("price_change_24h_pct")):
                        _n24 = min(288, len(_kl_bf))  # 24h = 288×5m
                        if _n24 >= 24:
                            _c_now = _kline_close_at(_kl_bf, 1)
                            _c_prev = _kline_close_at(_kl_bf, _n24)
                            if _c_now and _c_prev and _c_prev > 0:
                                _md["price_change_24h_pct"] = round((_c_now - _c_prev) / _c_prev * 100, 4)
                if float(_md.get("volatility_pct") or 0) <= 0:
                    _md["volatility_pct"] = _md.get("volatility_value", 0)
            except Exception as _bf_err:
                logger.debug(f"[ScalpRouter独立] {sym} regime 字段补数失败: {_bf_err}")

            # 修复 BUG B：优先读 factor_v3（V3 流水线的实际键名）
            _factor_v3 = (market_summary.get(sym) or {}).get("factor_v3")
            if _factor_v3:
                _md["factor_signal"] = _factor_v3  # 映射到 ScalpRouter 读的键
            else:
                # 2026-06-26：factor_v3 为空时，主动调因子引擎算一遍
                # 确保短线用完整因子引擎而非手搓指标
                try:
                    from backend.services.factor_engine.base_factors import factor_engine
                    from backend.services.factor_engine.factor_evaluation_pipeline import factor_pipeline
                    if "klines" in _md and hasattr(_md["klines"], '__len__'):
                        import pandas as _pd_kl
                        _kdf = _md["klines"] if isinstance(_md["klines"], _pd_kl.DataFrame) else _pd_kl.DataFrame(_md["klines"])
                        # ── 因子计算缓存（2026-07-08 短线提速）──
                        # compute_all_factors 实测 7~8s/币，是单轮第二大头。因子基于 5m
                        # K线，一根蜡烛内基本不变——按「5m 蜡烛桶」缓存：同一根蜡烛内必命中，
                        # 换蜡烛才重算（避免 TTL 缓存下「单轮>TTL 永远命不中」的陷阱）。
                        # 注意：真正的入场/退出仍走 scalp_factor_router.evaluate（不缓存、
                        # 每轮用最新价重算），故蜡烛内价格波动的响应性不受影响。
                        _fv = None
                        try:
                            _cd_sec = int(os.getenv("SCALP_FACTOR_CANDLE_SEC", "300") or 300)
                            _cd_sec = _cd_sec if _cd_sec > 0 else 300
                            # ── 换蜡烛错峰（2026-07-09 提速）──
                            # 原来所有币共用 time//300 的桶边界 → 每根新蜡烛所有币
                            # 同一 tick 一起 miss，5 币×~10s 重算挤爆单轮(50s+)。给每个
                            # 币一个确定性相位偏移(按币名散列，落在[0,_cd_sec))，让各币
                            # 的桶边界错开到不同 tick，把重算成本摊平到整个 5min 窗口。
                            # 代价：某币可能用「上一根蜡烛的因子」多撑最多相位秒数——因子
                            # 蜡烛内本就基本不变，影响可忽略；入场/退出仍每轮实时重算。
                            _phase = abs(hash(sym)) % _cd_sec
                            _bucket = int((time.time() + _phase) // _cd_sec)
                            _fc_hit = self._scalp_factor_cache.get(sym)
                            if _fc_hit and _fc_hit.get("bucket") == _bucket:
                                _fv = _fc_hit.get("fv")
                        except Exception:
                            _fv = None
                            _bucket = None
                        if not _fv:
                            # [2026-07-10 性能修复] 短线跳过 PATTERN/BEHAVIORAL（与 Router 共用常量）
                            # [2026-08-13 P0-1] 实盘因子集收敛：只计算有 OOS 证据的精选池因子
                            from backend.services.scalp.scalp_factor_exclude import (
                                get_scalp_factor_exclude_categories,
                                get_scalp_factor_allowlist,
                            )
                            _scalp_exclude = get_scalp_factor_exclude_categories()
                            _scalp_allow = get_scalp_factor_allowlist()
                            _fv = factor_engine.compute_all_factors(
                                _kdf, _md, exclude_categories=_scalp_exclude,
                                allowlist=_scalp_allow,
                            )
                            try:
                                self._scalp_factor_cache[sym] = {"bucket": _bucket, "fv": _fv}
                            except Exception:
                                pass
                        if _fv:
                            _cs = factor_pipeline.compute_weighted_signals(_fv, _md)
                            if _cs is not None:
                                _md["factor_signal"] = {
                                    "direction": float(_cs.direction),
                                    "strength": float(getattr(_cs, 'strength', abs(_cs.direction))),
                                }
                                # [2026-09-02 P1.2] 回退路径同样带上逐因子归因，
                                # 否则 factor_v3 缺失时（新币/管道滞后）落库的
                                # signal_log 又退回"只有一个 composite 分"。
                                try:
                                    _cs_attr = getattr(_cs, "attribution", None) or []
                                    if _cs_attr:
                                        _md["factor_signal"]["factor_contrib"] = [
                                            {
                                                "f": a.factor_id, "d": a.direction,
                                                "w": a.weight, "c": a.contrib,
                                                "cat": a.category,
                                            }
                                            for a in _cs_attr[:15]
                                        ]
                                except Exception:
                                    pass
                except Exception as _fe_err:
                    logger.debug(f"[ScalpRouter独立] {sym} 因子引擎主动计算失败: {_fe_err}")
            _prof["factors"] = time.perf_counter() - _pf_ts; _pf_ts = time.perf_counter()

            # 2026-06-26：加载 15m K线做多周期共振 + 传入 orchestrator 趋势
            # 2026-07-31：与 5m 同源，跟随活跃交易所，禁止再硬编码 hyperliquid。
            try:
                _raw_15m = kline_service.get_klines_from_db(
                    sym.upper(), "15m", 60, exchange=_kl_ex,
                )
                if _raw_15m and len(_raw_15m) > 20:
                    _md["klines_15m"] = pd.DataFrame(_raw_15m)
            except Exception:
                pass
            # 传入 orchestrator 趋势评估（用于趋势软加分）
            _orch_eval = (market_summary.get(sym) or {}).get("orchestrator")
            if _orch_eval:
                _md["orchestrator"] = _orch_eval
            _prof["kline15m"] = time.perf_counter() - _pf_ts; _pf_ts = time.perf_counter()

            # [fix] 注入订单流/衍生品数据，否则 cvd_ratio/oi_delta/taker_ratio/funding_rate
            # 因子全算 0（数据已在 market_flow_indicators DB + derivatives_analytics 就绪）
            try:
                from backend.services.factor_engine.factor_bridge import inject_orderflow_for_factors
                _md = inject_orderflow_for_factors(sym, _md, timeframe="5m")
            except Exception as _of_err:
                logger.debug(f"[ScalpRouter独立] {sym} 订单流注入跳过: {_of_err}")
            _prof["orderflow"] = time.perf_counter() - _pf_ts; _pf_ts = time.perf_counter()
            # [M0-H2] 评估阶段（scalp_factor_router.evaluate / ranging_mr，纯 Python
            # 单币最长 ~1s+）前再让渡一次 GIL（同每币迭代处注释）。
            time.sleep(0)

            # ── 震荡均值回归模式分流（2026-07-09）──
            # 仅当 regime==ranging 且 48×5m 振幅落在[MIN,MAX]区间时，改走独立的
            # "高抛低吸"打法（scalp_ranging_mr），趋势/极端/振幅不达标一律走原趋势
            # 打法（scalp_factor_router）。全程 SCALP_RANGING_MR_ENABLED 开关门控。
            _mr_active = False
            try:
                from backend.config.settings import SCALP_RANGING_MR_ENABLED as _MR_ON
                if _MR_ON:
                    from backend.services.decision_core.regime_agent import (
                        classify_regime as _cls_reg_mr,
                    )
                    from backend.services.scalp.scalp_ranging_mr import (
                        amplitude_in_band as _mr_amp_ok,
                    )
                    from backend.services.scalp.scalp_ranging_mr import (
                        evaluate_ranging_mr as _mr_eval,
                    )
                    from backend.services.scalp.scalp_ranging_mr import (
                        range_amplitude_pct as _mr_amp,
                    )
                    from backend.services.scalp.mr_regime_breakout_guard import (
                        mr_breakout_danger as _mr_guard,
                    )
                    _regime_mr = _cls_reg_mr(_md)
                    # [2026-09-09 震荡策略加固] 方向上下文缺失不接 MR：上游
                    # S1 补数（price_change_1h/24h_pct）失败或 K 线不足时，振幅
                    # 达标也只是"局部窄幅"，没有 24h/1h 方向背景佐证真震荡——
                    # 这正是历史趋势转换日 MR 按震荡"接飞刀"的盲区之一。
                    # 无背景一律回落到趋势打法（其 V5/regime 链有完整兜底）。
                    _dir_ctx_ok = (
                        _md.get("price_change_1h_pct") is not None
                        or _md.get("price_change_24h_pct") is not None
                    )
                    if not _dir_ctx_ok:
                        logger.info(
                            "[ScalpMR] %s 方向上下文缺失(1h/24h 涨跌未就绪)，"
                            "本 tick 不走均值回归（防无背景接飞刀）", sym,
                        )
                    _mr_owned = False  # MR 接管本 tick（regime=震荡 + 振幅在带内 + 守卫放行）
                    if _regime_mr.regime == "ranging" and _dir_ctx_ok and _mr_amp_ok(_mr_amp(_md)):
                        # [2026-08-23] regime 突变实时守卫：趋势转换日 MR 仍按震荡
                        # 接飞刀 → 用实时波动率/ADX 突变检测拦截，本 tick 改走趋势打法。
                        _mr_danger, _mr_danger_reason = _mr_guard(_md)
                        if _mr_danger:
                            logger.info(
                                "[ScalpMR] %s regime突变守卫: %s（本tick改走趋势打法）",
                                sym, _mr_danger_reason,
                            )
                        else:
                            _mr_owned = True
                            _sig = _mr_eval(sym, _md)
                            # [2026-09-09 震荡策略加固] 只有 MR 真正开火(buy/sell)
                            # 才占用 ranging_mr 标记：hold（未达边缘/区间中部）不能
                            # 把本 tick 的阈值 kind/EV 标签/仓位前缀误打成 MR 口径，
                            # 否则 MR 的打标统计会被 hold 污染。
                            if str(getattr(_sig, "action", "") or "") in ("buy", "sell"):
                                _md["ranging_mr"] = True
                                _mr_active = True
                            else:
                                logger.info(
                                    "[ScalpMR] %s 接管但未达边缘(hold)，不占用 MR 标记: %s",
                                    sym, (getattr(_sig, "reasoning", "") or "")[:120],
                                )
            except Exception as _mr_err:
                logger.debug(f"[ScalpMR] {sym} 均值回归分流跳过: {_mr_err}")
            if not _mr_active and not _mr_owned:
                _sig = scalp_factor_router.evaluate(
                    sym, _md,
                    mode=(getattr(session_row, "trading_mode", None) or "paper"),
                    # [2026-08-28 实盘零成交修复] 实盘自适应门槛按账户自身样本统计
                    account_id=account_id,
                )
            _prof["evaluate"] = time.perf_counter() - _pf_ts
            _prof["total"] = sum(_prof.values())
            # 诊断日志：默认开（方便观察缓存命中/争抢），设 SCALP_PROFILE_LOG=false 关闭
            if os.getenv("SCALP_PROFILE_LOG", "true").lower() in ("1", "true", "yes", "on"):
                logger.info(
                    "[ScalpProfile] %s total=%.2fs | kline5m=%.2f factors=%.2f kline15m=%.2f orderflow=%.2f evaluate=%.2f",
                    sym, _prof.get("total", 0.0), _prof.get("kline5m", 0.0), _prof.get("factors", 0.0),
                    _prof.get("kline15m", 0.0), _prof.get("orderflow", 0.0), _prof.get("evaluate", 0.0),
                )
            logger.info(f"[ScalpRouter独立] {sym} score={_sig.factor_score} dir={_sig.direction} action={_sig.action}")
            if str(_sig.action) == "hold" and int(_sig.factor_score or 0) >= 35:
                _reason = getattr(_sig, "reasoning", "") or "无原因"
                logger.warning(f"[ScalpRouter独立] {sym} 高分hold: score={_sig.factor_score} reason={_reason[:200]}")
                _bump_block("high_score_hold")

            # ── 清算磁吸反转硬退出（2026-07-07）──
            # 根因（用户实盘反馈"大涨方向错误赔了不少"排查发现）：已有 scalp 仓位
            # 开仓之后，如果行情出现高强度清算磁吸反向信号（例如持有空单，但
            # 上方出现大额空头清算磁吸——说明继续下跌的空间很小、反弹挤空概率
            # 很高），此前系统只会用这个信号拦「新开仓」，对「已经持有的反向
            # 仓位」完全不处理——只能被动等 master_running_reduce（历史胜率仅
            # 5%，见 master_close_guard.py 文档）慢慢削减，或者等 SL 硬扛到底。
            # 这里补一个主动退出：新证据（磁吸反转）出现就直接平掉冲突仓位，
            # 而不是等亏损累积到阈值才被动响应。
            if _sig.direction in ("long", "short"):
                try:
                    self._check_liq_magnet_reversal_exit(
                        db=_db, account_id=account_id, symbol=sym,
                        router_direction=_sig.direction,
                    )
                except Exception as _lm_exit_err:
                    logger.debug(
                        f"[ScalpRouter独立] {sym} 磁吸反转退出检查跳过: {_lm_exit_err}"
                    )
            try:
                _thresh = scalp_factor_router._get_adaptive_threshold(
                    sym, kind=("ranging_mr" if _mr_active else "trend"),
                    # [2026-08-28] 实盘按账户自身样本（显示口径与 evaluate 一致）
                    is_paper=not _is_live_session,
                    account_id=account_id,
                )
                # [2026-08-28 实盘收紧] 实盘因子评分门槛上浮 LIVE_SCORE_EXTRA：
                # 模拟盘验证可以粗放，实盘评分口径更严。
                if _is_live_session:
                    from backend.services.full_auto.live_gate_policy import live_score_extra
                    _thresh = int(_thresh) + int(live_score_extra())
            except Exception:
                _thresh = 25
            _breakdown = getattr(_sig, "factor_breakdown", None) or {}
            if not isinstance(_breakdown, dict):
                _breakdown = {}

            # ── 融合仲裁器输入初始化（阶段0：pwin 主轴）──
            _meta_pwin = None
            _fusion_decision = None

            # ── 真实信号日志（元标签数据采集，2026-07-08）──
            # 把"触发的"短线信号 + 因子快照落库，事后结算输赢，供元标签模型训练。
            # 安全降级：任何异常都不影响交易；flag SCALP_SIGNAL_LOG_ENABLED 可关。
            # P0-4A：影子写入 meta_p_win（不进决策）；P1-3：top 贡献因子归因。
            try:
                if str(_sig.direction or "") in ("long", "short"):
                    from backend.services.scalp_signal_logger import log_signal as _log_sig
                    _snap = dict(_breakdown)
                    for _k in ("oi_delta_pct", "cvd", "funding_rate", "buy_notional",
                               "sell_notional", "depth_ratio", "imbalance", "premium"):
                        if _k in _md:
                            _snap[f"of_{_k}"] = _md.get(_k)
                    # P1-3：按绝对值取 top 贡献因子，便于日后归因喂权重
                    try:
                        _ranked = sorted(
                            (
                                (str(k), float(v))
                                for k, v in _breakdown.items()
                                if isinstance(v, (int, float))
                            ),
                            key=lambda kv: abs(kv[1]),
                            reverse=True,
                        )[:8]
                        _snap["top_factors"] = [
                            {"name": n, "contrib": round(c, 6)} for n, c in _ranked
                        ]
                    except Exception:
                        pass
                    # P0-4A：meta 概率（影子 + 仲裁同源，v3 含 K线趋势特征）
                    try:
                        from backend.services.scalp_meta_trainer import predict_win_prob
                        _meta_feats = {
                            "factor_score": float(_sig.factor_score or 0),
                            "direction": str(_sig.direction or ""),
                            "symbol": str(sym or ""),
                            **{k: v for k, v in _snap.items() if not isinstance(v, (dict, list))},
                        }
                        # v3: 15m K线趋势特征。优先 _md(实时);数据中心判过期返回空时
                        # 直查 alpha_market DB 兜底(120s 缓存,与训练同源)。
                        _kline_feats = {}
                        try:
                            from backend.services.scalp_meta_trainer import compute_kline_feats
                            _kline_feats = compute_kline_feats(_md.get("klines_15m"))
                        except Exception:
                            pass
                        if not _kline_feats:
                            try:
                                from backend.services.scalp_meta_trainer import kline_feats_from_db_cached
                                _kline_feats = kline_feats_from_db_cached(sym)
                            except Exception:
                                pass
                        _mp = predict_win_prob(
                            _meta_feats, require_usable=False, kline_feats=_kline_feats,
                        )
                        if _mp is not None:
                            _snap["meta_p_win"] = round(float(_mp), 6)
                        _meta_pwin = _mp
                    except Exception:
                        pass
                    # [2026-08-22 M1-8] 信号日志移到门槛之后：原来在信号评估段就写
                    # （当日 18350 条 vs 154 个仓位），大量"触发但被闸拦截"的样本
                    # 污染下游校准/元模型胜率统计。现在仅当信号通过全部闸门、
                    # 真正要下单时才记录（见下方 place_order 前的 _log_sig）。
            except Exception as _log_err:
                logger.debug(f"[ScalpRouter独立] {sym} 信号日志跳过: {_log_err}")

            _scalp_factor = {
                "factor_score": int(_sig.factor_score or 0),
                "direction": str(_sig.direction or "neutral"),
                "action": str(_sig.action or "hold"),
                "threshold": int(_thresh),
                "reasoning": (getattr(_sig, "reasoning", None) or "")[:160],
                "breakdown": {str(k): v for k, v in list(_breakdown.items())[:6]},
                "updated_at": time.time(),
            }
            _sym_u = sym.upper()
            _cache_row = self._market_scan_cache.setdefault(_sym_u, {})
            if isinstance(_cache_row, dict):
                _cache_row["scalp_factor"] = _scalp_factor
            _scalp_tick_results.append((_sym_u, _scalp_factor))
            if _sig.action not in ("buy", "sell"):
                continue

            # [2026-09-02 修 UnboundLocalError] _trade_mode 原先在下方 ScalpExecutionGate
            # 处才赋值，但 pwin 仲裁（紧接其下）已经引用它。旧版仲裁异常 fail-open：
            # 每 tick 第一个 buy/sell 信号抛 UnboundLocalError 被静默放行、绕过 pwin
            # 闸门，之后的币才正常仲裁。G18 改 fail-closed 后第一个信号 continue 掉、
            # 永远到不了赋值行 → 17:47 起 2970 次 100% 拒开、短线 5 小时零成交。
            # 必须在仲裁之前就绑定。
            _trade_mode = (getattr(session_row, "trading_mode", None) or "paper")

            # ── pwin 主轴仲裁(2026-08-25 接线,decision_fusion_arbiter)──
            # 依据: 回放 pwin>=0.55 桶 59.2% 胜率净 +5.09;factor_score>=70 桶
            # 净 -332.55 全场最差。v3 元模型(OOS AUC 0.623, usable=true)为 pwin
            # 唯一来源;factor_score 仅并列参考,不再单独决定入场/仓位。
            # FUSION_MODE=factor 时本块不生效(行为等价 8/23 前)。
            _pwin_size_mult = 1.0
            try:
                from backend.services.decision_fusion_arbiter import (
                    decide_scalp, FUSION_MODE as _FM,
                )
                if _FM != "factor":
                    # [2026-08-29 全面修复 P2.4] 来源信用接线：fusion_attribution
                    # 该(source,nature,symbol)滚动 n>=20 且净期望<0 → credit=0 →
                    # 仲裁器 standdown。此前 credit 恒默认 1.0，影子统计从不闸门
                    # 交易（死参数）。异常 fail-open（credit=1.0）。
                    _credit_val = 1.0
                    try:
                        from backend.services.source_attribution import attribution as _attr_credit
                        _credit_val = _attr_credit.credit(
                            "factor", "scalp", str(sym).upper(),
                        )
                    except Exception:
                        _credit_val = 1.0
                    _arb = decide_scalp(
                        pwin=_meta_pwin,
                        factor_score=float(_sig.factor_score or 0),
                        direction=str(_sig.direction or "neutral"),
                        tp_pct=float(_sig.tp_pct or 0),
                        sl_pct=float(_sig.sl_pct or 0),
                        mode=_trade_mode,
                        account_id=account_id,
                        credit=_credit_val,
                        is_mr=bool(_md.get("ranging_mr")) if isinstance(_md, dict) else False,
                    )
                    _scalp_factor["pwin_arbiter"] = _arb.to_dict()
                    if not _arb.allowed:
                        logger.info(
                            "[ScalpRouter独立] %s pwin仲裁%s: %s (pwin=%s)",
                            sym, _arb.action, _arb.reason, _arb.tags.get("pwin"),
                        )
                        _bump_block("pwin_arbiter")
                        # [2026-08-27] 拦截可见性：此前 pwin 拦掉的信号不写快照，
                        # 时间线对短线完全哑掉（瞎子）。现在带拦截理由落快照，
                        # 配合 tier-activity 读侧去重（600s 窗口聚合）显示结局。
                        if session_row:
                            self._persist_tcp_snapshot(
                                session_row,
                                symbol=sym.upper(),
                                tier="short",
                                action=str(_sig.action or "hold"),
                                confidence=float(_sig.factor_score or 0),
                                reasoning=(getattr(_sig, "reasoning", None) or "")[:160],
                                evaluate_verdict={
                                    "allowed": False,
                                    "reason": f"{_arb.reason} (pwin={_arb.tags.get('pwin')})",
                                    "layer": "pwin_arbiter",
                                    "rule": "decision_fusion_arbiter",
                                },
                                source_lane="scalp_lane",
                                executed=False,
                            )
                        continue
                    if _arb.size_mult > 0:
                        _pwin_size_mult = float(_arb.size_mult)
            except Exception as _arb_err:
                # [2026-09-02 G18] 原为 fail-open + debug 日志：pwin 是唯一经数据验证
                # 有效的质量闸（12.8 万条回溯：<0.55 各档净收益全负、>=0.55 各档全正），
                # 异常放行等于让负期望信号绕过它裸奔进场，而 debug 级日志默认不输出，
                # 故障会被静默吞掉、无从发现。改为默认 fail-closed + warning 可见。
                _pwin_fail_closed = True
                try:
                    from backend.config.settings import SCALP_PWIN_FAIL_CLOSED
                    _pwin_fail_closed = bool(SCALP_PWIN_FAIL_CLOSED)
                except Exception:
                    pass
                if _pwin_fail_closed:
                    logger.warning(
                        "[ScalpRouter独立] %s pwin仲裁异常 → fail-closed 拒开: %s",
                        sym, _arb_err,
                    )
                    _bump_block("pwin_arbiter_error")
                    continue
                logger.warning(
                    "[ScalpRouter独立] %s pwin仲裁异常(降级放行): %s", sym, _arb_err,
                )

            # ── ScalpExecutionGate 统一规则门 ──
            from backend.services.scalp.scalp_execution_gate import scalp_execution_gate
            from backend.services.scalp.scalp_flash_veto import scalp_flash_veto
            # _trade_mode 已在 pwin 仲裁前绑定（见上方 [2026-09-02 修 UnboundLocalError]）
            _gate = scalp_execution_gate.evaluate(
                sym, _sig, _md, account_id=account_id, mode=_trade_mode,
            )
            # 震荡均值回归模式打标（2026-07-09）：MR 单用 scalp_mr_ 前缀，方便模拟盘
            # 观察期按 strategy_id 前缀单独统计 MR 打法的胜率/净盈亏/手续费占比。
            _scalp_strategy_id = (
                ("scalp_mr_" if _mr_active else "scalp_lane_")
                + (_gate.lane_decision_id or "")[:8]
            )
            from backend.services.decision_core.execute_proposal import evaluate_scalp_proposal
            from backend.services.decision_core.proposal import TradeProposal
            _scalp_prop = TradeProposal.from_agent(
                sym=sym.upper(),
                tier="short",
                action=str(_sig.action or "hold"),
                confidence=float(_gate.effective_score or _sig.factor_score or 0),
                trade_nature="scalp",
                sl_pct=float(_gate.sl_pct or _sig.sl_pct or 0),
                tp_pct=float(_gate.tp_pct or _sig.tp_pct or 0),
                source_lane="scalp_lane",
                reasoning=(getattr(_sig, "reasoning", None) or "")[:200],
            )
            # M8 周期共振层：发布短线信号 + 评估（PRL_ENABLED=false 时直通）
            try:
                from backend.services.portfolio.resonance_layer import (
                    PeriodSignal,
                    resonance_layer,
                )
                resonance_layer.publish(PeriodSignal(
                    symbol=sym.upper(),
                    tier="short",
                    direction=str(_sig.direction or "neutral"),
                    confidence=float(_sig.factor_score or 0),
                    source="scalp_loop",
                ))
                _scalp_prop = resonance_layer.evaluate(_scalp_prop)
            except Exception:
                pass
            _scalp_verdict = evaluate_scalp_proposal(
                db=_db,
                account_id=account_id,
                proposal=_scalp_prop,
                market_data=_md,
                gate_allowed=_gate.allowed,
                gate_reason=_gate.reason or "",
                gate_tier=_gate.tier or "",
                lane_decision_id=_gate.lane_decision_id or "",
                mode=(getattr(session_row, "trading_mode", None) or "paper"),
            )
            if session_row:
                self._persist_tcp_snapshot(
                    session_row,
                    symbol=sym.upper(),
                    tier="short",
                    action=_scalp_prop.action,
                    confidence=_scalp_prop.confidence,
                    reasoning=_scalp_prop.reasoning,
                    market_snapshot=_md,
                    proposal=_scalp_prop.to_dict(),
                    evaluate_verdict=_scalp_verdict.to_dict(),
                    source_lane="scalp_lane",
                    proposal_id=_scalp_prop.proposal_id,
                    executed=False,
                    strategy_id=_scalp_strategy_id,
                )
            if not _gate.allowed:
                logger.info(
                    f"[ScalpRouter独立] {sym} Gate拦截 tier={_gate.tier} "
                    f"reason={_gate.reason} id={_gate.lane_decision_id}"
                )
                _bump_block("gate")
                continue

            _sig.sl_price = _gate.sl_price or _sig.sl_price
            _sig.tp_price = _gate.tp_price or _sig.tp_price
            _sig.sl_pct = _gate.sl_pct or _sig.sl_pct
            _sig.tp_pct = _gate.tp_pct or _sig.tp_pct
            # [P1-2] 周末/时段缩仓（Gate 的 regime/软否决/降级缩仓由下方
            # regime 段单乘传导，不在本处重复乘 gate.size_multiplier——
            # [2026-08-10 问题二修复] 同源双乘导致仓位被平方压缩：
            # 实测 SKHYNIX gate.size_multiplier=0.25 被乘两次 →
            # 444u 权益 x1.5 名义 x0.25x0.25/10x = 4.16u 保证金（应 16.65u）
            _size_mult = _liquidity_mult * _pwin_size_mult

            if _gate.needs_veto and scalp_flash_veto.should_invoke(_gate.tier, _gate.needs_veto):
                _ohlc = []
                try:
                    _kdf_v = _md.get("klines")
                    if _kdf_v is not None and hasattr(_kdf_v, "tail"):
                        for _, _row in _kdf_v.tail(3).iterrows():
                            _ohlc.append({
                                "o": float(_row.get("open", 0)),
                                "h": float(_row.get("high", 0)),
                                "l": float(_row.get("low", 0)),
                                "c": float(_row.get("close", 0)),
                            })
                except Exception:
                    pass
                _veto_ctx = {
                    "symbol": sym,
                    "side": _sig.action,
                    "score": _gate.effective_score,
                    "entry": _sig.entry_price,
                    "sl": _gate.sl_price,
                    "tp": _gate.tp_price,
                    "advisory": (_gate.advisory.to_dict() if _gate.advisory else {}),
                    "recent_5m_ohlc": _ohlc,
                    # 2026-07-06：把因子明细（含 cycle_prob 融合breakdown）传给 Flash
                    # Veto，让边缘裁决LLM看到具体是哪些因子/AI概率引擎在起作用，
                    # 而不是只看一个总分瞎猜。
                    "factor_breakdown": _sig.factor_breakdown,
                }
                # [2026-08-07 修复] 长事务拆分：FlashVeto 边缘裁决 LLM（流式
                # 20-50s，scalp 10s 高频 tick 下几乎每轮命中）前先提交主连接事务，
                # 避免 LLM 期间连接 idle-in-transaction 被 LeakGuard 告警/强杀。
                try:
                    _db.commit()
                except Exception:
                    try:
                        _db.rollback()
                    except Exception:
                        pass
                _veto = scalp_flash_veto.evaluate(
                    _veto_ctx, account_id=account_id,
                    trading_mode=(getattr(session_row, "trading_mode", None) or "paper"),
                )
                scalp_flash_veto.record_audit(
                    _db,
                    symbol=sym,
                    score=_gate.effective_score,
                    verdict=_veto.verdict,
                    latency_ms=_veto.latency_ms,
                    source=_veto.source,
                    lane_decision_id=_gate.lane_decision_id,
                    account_id=account_id,
                    rationale=_veto.rationale,
                )
                if _veto.verdict == "veto":
                    logger.info(
                        f"[ScalpRouter独立] {sym} FlashVeto拦截 "
                        f"({_veto.rationale}) id={_gate.lane_decision_id}"
                    )
                    _bump_block("flash_veto")
                    continue
                # [S2 2026-08-21 修复] veto 未否决时不得把周末/薄时段缩仓重置回 1.0。
                # 原实现 `= ... else 1.0` 是赋值：accept 即抹掉 _liquidity_mult。
                _size_mult *= (_veto.size_multiplier if _veto.verdict == "downsize" else 1.0)

            if not _scalp_verdict.allowed:
                logger.info(
                    f"[ScalpRouter独立] {sym} TCP/V5拦截: {_scalp_verdict.reason}"
                )
                _bump_block("tcp_v5")
                continue

            # ── 多周期 H1–H5 约束强制生效（阶段一 1.4）──
            # 独立 scalp 循环此前完全绕过 4h/15m 频率约束，可在 4h 明确反向时逆势满仓开。
            # 复用 OrchBG 已缓存进 _md["orchestrator"] 的多周期偏向做轻量判定：
            # 4h 强反向→禁开、4h/15m 反向→缩仓、硬冲突≥2→hold。缩仓折进 _size_mult。
            try:
                from backend.services.scalp.scalp_mtf_constraint import (
                    evaluate_scalp_mtf_constraint,
                )
                _mtf = evaluate_scalp_mtf_constraint(sym, _sig.direction, _md)
                if _mtf.hold:
                    logger.info(
                        f"[ScalpRouter独立] {sym} 多周期约束禁开: {_mtf.reason} "
                        f"id={_gate.lane_decision_id}"
                    )
                    _bump_block("mtf")
                    continue
                if _mtf.size_multiplier < 0.999:
                    _size_mult *= _mtf.size_multiplier
                    logger.info(
                        f"[ScalpRouter独立] {sym} 多周期约束缩仓×{_mtf.size_multiplier:.2f}: {_mtf.reason}"
                    )
            except Exception as _mtf_err:
                logger.debug(f"[ScalpRouter独立] {sym} 多周期约束跳过: {_mtf_err}")

            logger.info(
                f"[ScalpRouter独立] {sym} Gate通过 tier={_gate.tier} "
                f"eff={_gate.effective_score} advisory="
                f"{getattr(_gate.advisory, 'advisory_verdict', '?')} "
                f"id={_gate.lane_decision_id}"
            )

            # ── 亏损币惩罚（设计 D2：日报 symbol_penalty 状态机驱动）──
            # watchlisted → 禁开该币（待复审恢复）；penalty<1.0 → 信号强度打折
            # [2026-08-28 实盘零成交修复] 状态机由 paper 日报驱动（全局 symbol
            # key），实盘 0 样本却被 paper 连亏观察名单禁开（实测 XRP 被 paper
            # 5 连亏日 watchlisted → 实盘恒禁）。引导期内实盘豁免该状态机
            # （R2 单笔已实现亏损 24h 禁开仍保留，见下方 FUSION_RISK_EVENT_BAN）；
            # 引导期满自动恢复全局惩罚（此时状态机也会吃到实盘自身结果）。
            _pen_score = float(getattr(_gate, "effective_score", 0) or 0)
            _pen_exempt = False
            if _is_live_session:
                try:
                    from backend.services.full_auto.live_gate_policy import (
                        live_bootstrap_active,
                    )
                    _pen_exempt = live_bootstrap_active(account_id)
                except Exception:
                    _pen_exempt = False
            else:
                # [2026-09-04] 模拟盘豁免亏损币状态机：该状态机由 paper 日报
                # 驱动，会把连亏的币禁开或 ×0.5 打折；打折后最高分 34.5 够不到
                # 探索门槛 45 → 开不了单 → 攒不到翻案样本 → 惩罚永不解除。
                # 模拟盘没有真金白银可保护，亏损本身就是要采集的样本。
                try:
                    from backend.config.settings import PAPER_SYMBOL_PENALTY_ENABLED
                    _pen_exempt = not PAPER_SYMBOL_PENALTY_ENABLED
                except Exception:
                    _pen_exempt = True
            try:
                from backend.services.symbol_penalty import get_penalty, is_watchlisted
                _pen = float(get_penalty(sym) or 1.0)
                if not _pen_exempt and (is_watchlisted(sym) or _pen <= 0.0):
                    logger.info(
                        f"[ScalpRouter独立] {sym} 在亏损币观察名单(penalty=0)，禁开待复审"
                    )
                    _bump_block("symbol_watchlist")
                    continue
                if not _pen_exempt and _pen < 1.0:
                    _pen_score = _pen_score * _pen
                    logger.info(
                        f"[ScalpRouter独立] {sym} 亏损币惩罚 penalty={_pen}: "
                        f"信号强度 {float(getattr(_gate, 'effective_score', 0) or 0):.3f} -> {_pen_score:.3f}"
                    )
                elif _pen_exempt and _pen < 1.0:
                    logger.info(
                        "[ScalpRouter独立] %s %s豁免亏损币惩罚(penalty=%.2f，原分 %.1f 不打折)",
                        sym,
                        "实盘引导期" if _is_live_session else "模拟盘采样",
                        _pen, _pen_score,
                    )
            except Exception as _pen_err:
                logger.debug(f"[ScalpRouter独立] {sym} symbol_penalty 检查跳过: {_pen_err}")

            # [S7 2026-08-21] 单一决策分贯穿：EV 闸 / 校准快照 / TCP 审计 /
            # AlphaBus 全部改用惩罚后的 _pen_score——原实现只有 short_tier 闸
            # 与 sizing 用惩罚分，EV/校准/审计跟 raw 分，同一笔单在不同环节
            # 呈现两个强度（校准器学到的是"没被打折的分"，高分更亏的口径
            # 之一即源于此）。无校准文件时 high_score_cap 不封顶（router 侧
            # 显式回退，本处不再二次处理）。
            _score_for_trade = float(_pen_score or _sig.factor_score or 0)
            try:
                _scalp_prop.confidence = float(_score_for_trade)
            except Exception:
                pass

            # ── 开仓冷却检查（2026-06-22 修复短线频繁开单） ──
            # 这是之前唯一缺失的门：平仓冷却有(reentry_cooldown)，开仓冷却没有。
            # 没有 → turbo 档 45s/tick 下每 tick 都能开，同 symbol 每小时 80 次。
            from backend.config.settings import (
                SCALP_MAX_OPENS_PER_TICK,
                SCALP_OPEN_COOLDOWN_SEC,
                SCALP_OPEN_SAME_SIDE_COOLDOWN_SEC,
            )
            _now = time.time()
            _side_str = "buy" if _sig.action == "buy" else "sell"

            # 每 tick 最大开仓数（防止一波行情所有币齐开）
            if _scalp_opens_this_tick >= SCALP_MAX_OPENS_PER_TICK:
                logger.info(
                    f"[ScalpRouter独立] {sym} 本 tick 已开 "
                    f"{_scalp_opens_this_tick}/{SCALP_MAX_OPENS_PER_TICK} 仓，跳过"
                )
                _bump_block("max_per_tick")
                continue

            # 通用冷却（同 symbol 任意方向）
            # [2026-08-22 M2-2] 冷却键纳入账户维度：原 key 无 account，多会话
            # 同一 symbol 互相共享冷却（或竞态绕开），也是双账户镜像开仓的
            # 放大器之一。
            _acct_key = f"a{account_id}:{sym}"
            _last_open_any = self._scalp_open_ts.get(_acct_key, 0.0)
            if _now - _last_open_any < SCALP_OPEN_COOLDOWN_SEC:
                logger.info(
                    f"[ScalpRouter独立] {sym} 开仓冷却中（距上次开仓 "
                    f"{int(_now - _last_open_any)}s < {SCALP_OPEN_COOLDOWN_SEC}s），跳过"
                )
                _bump_block("open_cooldown")
                continue
            # 同向冷却（同 symbol 同方向，更严）
            _side_key = f"a{account_id}:{sym}:{_side_str}"
            _last_open_side = self._scalp_open_ts_side.get(_side_key, 0.0)
            if _now - _last_open_side < SCALP_OPEN_SAME_SIDE_COOLDOWN_SEC:
                logger.info(
                    f"[ScalpRouter独立] {sym} {_side_str} 同向开仓冷却中（"
                    f"{int(_now - _last_open_side)}s < {SCALP_OPEN_SAME_SIDE_COOLDOWN_SEC}s），跳过"
                )
                _bump_block("same_side_cooldown")
                continue

            # 平仓后再开仓冷却（reentry_cooldown：short tier 默认 4h 同向）
            # 2026-06-27 修复：此前 ScalpRouter 只检查「距上次开仓」5~10min，
            # 未检查「距上次平仓」，导致止损/超时平仓后仍快速重开。
            try:
                from backend.services.reentry_cooldown import reopen_blocked
                _cd_blocked, _cd_reason = reopen_blocked(
                    account_id, sym, _side_str, new_tier="short",
                )
                if _cd_blocked:
                    logger.info(
                        f"[ScalpRouter独立] {sym} {_side_str} 平仓冷却拦截: {_cd_reason}"
                    )
                    continue
            except Exception as _cd_err:
                # 冷却检查失败时 fail-closed，禁止裸奔再开（2026-07-31）
                logger.warning(
                    f"[ScalpRouter独立] {sym} reentry_cooldown 异常，拒绝开仓: {_cd_err}"
                )
                continue

            # short/scalp 硬门槛（置信度 + 连续同向 + 币种熔断）
            try:
                from backend.services.short_tier_entry_gate import apply_short_tier_gate
                _gate_ok, _gate_reason = apply_short_tier_gate(
                    account_id=account_id,
                    symbol=sym,
                    side=_side_str,
                    action=_side_str,
                    confidence=float(_pen_score or _sig.confidence or _sig.factor_score or 0),
                    tier="short",
                    trade_nature="scalp",
                    mode=_trade_mode,  # [S4 2026-08-21] 真实模式：冷却时长 paper/live 分档
                )
                if not _gate_ok:
                    logger.info(
                        f"[ScalpRouter独立] {sym} 短线门槛拦截: {_gate_reason}"
                    )
                    _bump_block("short_tier")
                    continue
            except Exception as _gate_err:
                # [S9 2026-08-21] fail-closed：门槛检查异常不得放行下单
                # （对齐 unified_gate 同闸的 fail-closed 语义）
                logger.warning(
                    f"[ScalpRouter独立] {sym} short_tier_gate 异常，拒绝开仓: {_gate_err}"
                )
                _bump_block("short_tier_error")
                continue

            # ── 短线动态仓位（公共函数单源，禁止与 scalp_sizing 双份漂移）──
            from backend.services.full_auto.scalp_sizing import compute_scalp_dynamic_notional

            _sizing_entry = float(_sig.entry_price or _md.get("price") or 0)
            _sizing_vol = float(_md.get("volatility_pct") or _md.get("volatility_value") or 0.015)
            _scalp_size_pct = max(0.05, min(3.0, float(os.getenv("SCALP_SIZE_PCT", "0.50") or 0.50)))
            _min_margin_pct = max(0.01, min(0.20, float(os.getenv("SCALP_MIN_MARGIN_PCT", "0.025") or 0.025)))
            _scalp_risk_cap = max(
                0.01,
                min(0.08, float(os.getenv("SCALP_MAX_TRADE_RISK_PCT", "0.03") or 0.03)),
            )
            _dyn_lev = 10
            # [2026-08-28 P2] 账户级三周期杠杆覆盖（short），只收紧不放大
            try:
                from backend.services.leverage_authority import account_tier_leverage_override
                _tier_lev_scalp = account_tier_leverage_override(account, "short")
            except Exception:
                _tier_lev_scalp = None
            _conf_raw = float(
                _pen_score
                or getattr(_sig, "confidence", 0)
                or getattr(_sig, "factor_score", 0)
                or 0
            )
            _scalp_sl_pct = float(getattr(_sig, "sl_pct", 0) or 0)
            if _scalp_sl_pct <= 0 and _sizing_entry > 0 and float(getattr(_sig, "sl_price", 0) or 0) > 0:
                _scalp_sl_pct = abs(_sizing_entry - float(_sig.sl_price)) / _sizing_entry
            if _scalp_sl_pct <= 0:
                _scalp_sl_pct = 0.012
            _scalp_sl_pct = max(0.005, min(0.08, _scalp_sl_pct))

            try:
                from backend.services.position_sizing_agent import (
                    PositionSizingInput,
                    position_sizing_agent,
                )
                _scalp_plan = position_sizing_agent.build_plan(PositionSizingInput(
                    symbol=sym,
                    side=_side_str,
                    price=_sizing_entry,
                    confidence=_conf_raw,
                    total_equity=equity,
                    available_balance=equity,
                    stop_loss_price=float(_sig.sl_price or 0),
                    take_profit_price=float(_sig.tp_price or 0),
                    volatility_pct=_sizing_vol,
                    tier="short",
                    trade_nature="scalp",
                    market_regime="unknown",
                    risk_level="medium",
                    position_cap_override=_scalp_size_pct,
                    size_multiplier=1.0,
                    alignment_scale=1.0,
                    leverage_cap=_tier_lev_scalp or 10,
                ))
                _dyn_lev = max(5, min(_tier_lev_scalp or 10, int(_scalp_plan.leverage or 10)))
            except Exception as _sizing_err:
                logger.warning(
                    f"[ScalpRouter独立] {sym} 杠杆解析失败，固定10x继续: {_sizing_err}"
                )
                _dyn_lev = 10

            _dyn = compute_scalp_dynamic_notional(
                equity,
                base_size_pct=_scalp_size_pct,
                confidence=_conf_raw,
                sl_pct=_scalp_sl_pct,
                volatility_pct=_sizing_vol,
                size_mult=float(_size_mult or 1.0),
                leverage=int(_dyn_lev),
                min_margin_pct=_min_margin_pct,
                max_trade_risk_pct=_scalp_risk_cap,
            )
            _margin_est = float(_dyn.get("notional") or 0.0)
            _conf_n = _conf_raw / 100.0 if _conf_raw > 1.5 else _conf_raw
            _conf_n = max(0.35, min(0.95, float(_conf_n or 0.5)))
            logger.info(
                f"[ScalpRouter独立] {sym} 动态仓位(预): base={_scalp_size_pct:.0%} "
                f"q×{_dyn.get('q_mult', 0):.2f} sl×{_dyn.get('sl_mult', 0):.2f} "
                f"vol×{_dyn.get('vol_mult', 0):.2f} "
                f"notional=${_margin_est:.0f} margin=${_margin_est / max(_dyn_lev, 1):.0f} "
                f"lev={_dyn_lev}x conf={_conf_n:.2f} sl={_scalp_sl_pct:.2%}"
                f"{' floored' if _dyn.get('floored') else ''}"
                f"{' capped' if _dyn.get('capped') else ''}"
            )

            # ── 震荡市缩仓（[S10 2026-08-21] 取消 0.70 灰尘仓地板，深缩可到
            #     SCALP_RANGING_SIZE_FLOOR=0.35；灰尘仓由保证金下限兜底）──
            _regime_size_mult = float(getattr(_gate, "size_multiplier", 1.0) or 1.0)
            if _regime_size_mult < 0.999:
                _rg_floor = float(os.getenv("SCALP_RANGING_SIZE_FLOOR", "0.35") or 0.35)
                _regime_size_mult = max(_rg_floor, _regime_size_mult)
                logger.info(
                    f"[ScalpRouter独立] {sym} regime 缩仓生效: notional "
                    f"{_margin_est:.0f}->{_margin_est * _regime_size_mult:.0f} "
                    f"(size_multiplier={_regime_size_mult:.2f})"
                )
                _margin_est *= _regime_size_mult

            # 保证金下限：与 compute_scalp_dynamic_notional 一致（已可能 floored）
            _min_notional = float(equity) * _min_margin_pct * max(int(_dyn_lev), 1)
            if _margin_est < _min_notional:
                logger.info(
                    f"[ScalpRouter独立] {sym} 保证金下限抬升: notional "
                    f"{_margin_est:.0f}->{_min_notional:.0f} "
                    f"(min_margin={_min_margin_pct:.0%}equity)"
                )
                _margin_est = _min_notional

            # 短线风险硬顶（默认单笔亏损 ≤ 权益 3%）
            if _scalp_sl_pct > 0:
                from backend.services.position_sizing_agent import clamp_position_by_risk_cap
                _capped_notional = clamp_position_by_risk_cap(
                    equity=equity,
                    notional_value=_margin_est,
                    sl_pct=_scalp_sl_pct,
                    max_risk_pct=float(_scalp_risk_cap),
                )
                if _capped_notional < _margin_est - 1e-9:
                    logger.info(
                        f"[ScalpRouter独立] {sym} 短线风险硬顶: notional "
                        f"{_margin_est:.0f}->{_capped_notional:.0f} "
                        f"(max_loss≤{_scalp_risk_cap:.1%}equity)"
                    )
                _margin_est = _capped_notional
                if _margin_est + 1e-9 < _min_notional * 0.85:
                    logger.info(
                        f"[ScalpRouter独立] {sym} 风险硬顶后低于可用下限 "
                        f"(${_margin_est:.0f}<${_min_notional:.0f})，跳过"
                    )
                    _bump_block("size_floor_vs_risk")
                    continue

            # 预算闸门放在最终名义之后，避免「先过预算再被下限抬高」超配
            # [S3 2026-08-21] 传本轮真实 mode：原硬编码 "paper" 使 live 模式
            # 读到的是 paper 持仓的已用保证金——live 超限与否被 paper 侧决定。
            _bf = budget_service.scale_factor_for_layer(
                "short", equity, _trade_mode, account_id=account_id
            )
            if _bf <= 0:
                logger.info(f"[ScalpRouter独立] {sym} 短线层预算已满，跳过")
                continue
            if _bf < 1.0:
                _margin_est *= _bf
            # [2026-08-22 PROFIT-3] EV Governor 资金分配：正期望簇放大 / 负期望簇收缩
            # （由每日真实结果自动更新，不做开单门禁）
            try:
                from backend.services.ev_governor import cluster_mult as _ev_mult
                _evc = "scalp_ranging_mr" if _mr_active else "scalp_trend"
                _evm = _ev_mult(_evc)
                if abs(_evm - 1.0) > 1e-9:
                    logger.info(
                        f"[ScalpRouter独立] {sym} EV Governor 资金乘数 {_evm:.2f}({_evc}) → "
                        f"名义{_margin_est:.0f}->{_margin_est * _evm:.0f}"
                    )
                    _margin_est *= _evm
            except Exception as _evg_err:
                logger.debug(f"[ScalpRouter独立] {sym} EV 乘数读取失败: {_evg_err}")
            _scalp_req_margin = _margin_est / max(int(_dyn_lev or 1), 1)
            if not budget_service.can_open(
                "short", _scalp_req_margin, equity, _trade_mode,
                account_id=account_id,
            ):
                logger.info(
                    f"[ScalpRouter独立] {sym} 短线层预算不足，跳过 "
                    f"(名义{_margin_est:.0f}÷{_dyn_lev}x=保证金{_scalp_req_margin:.0f})"
                )
                continue

            # 修复 BUG H：记录本 tick 已交易的 symbol，防双重执行
            if not hasattr(self, '_scalp_traded_this_tick'):
                self._scalp_traded_this_tick = set()
            self._scalp_traded_this_tick.add(sym.upper())
            # 每 tick 开始时清空（在 _run_unified_loop 开头）

            side = "buy" if _sig.action == "buy" else "sell"

            # ── 修复：position.side 存的是 long/short 而非 buy/sell ──
            _pos_side = "long" if side == "buy" else "short"

            # 修复 BUG I：检查已有 scalp 仓位 add_count，防止无限加仓
            # [2026-07-11 性能修复] 改用循环开始前一次性预取的 _scalp_pos_by_key，
            # 消除逐 symbol 的 N+1 查询（见上方预取注释）。
            from backend.config.settings import SCALP_ROUTER_MAX_ADDS
            _same_side_positions = _scalp_pos_by_key.get((sym, _pos_side)) or []
            _existing_scalp = _same_side_positions[0] if _same_side_positions else None
            if _existing_scalp and (_existing_scalp.add_count or 0) >= SCALP_ROUTER_MAX_ADDS:
                logger.info(
                    f"[ScalpRouter独立] {sym} 已有 scalp 仓位 add_count={_existing_scalp.add_count} "
                    f">= 上限{SCALP_ROUTER_MAX_ADDS}，跳过加仓"
                )
                continue

            # 修复（2026-06-24）：限制同币种同方向同时持仓数（防止无限堆积）。
            # 原代码只检查第一个仓位的 add_count，但 add_count=0 的全新仓位不受限，
            # 导致 RESOLV 3天内开了 20 个独立 scalp 仓位、吃光全部资金。
            # 现加入硬上限：同 symbol 同方向 scalp 同时最多 1 个仓位。
            _scalp_count = len(_same_side_positions)
            if _scalp_count >= 1:
                logger.info(
                    f"[ScalpRouter独立] {sym} 已有 {_scalp_count} 个同方向 scalp 持仓，"
                    f"跳过（同币种同方向短线最多1仓，防止资金堆积）"
                )
                continue

            # [2026-07-23] 方向冲突最终拦截在 TradeGate(place_order 内,见 trade_gate.py)。
            # 此处 scalp-vs-scalp 快速短路保留用于内部计数/日志,实际拦截以闸为准。
            # [2026-07-10 修复] 禁止同币种反方向对冲（幽灵单根因）。
            # 此前 scalp_mr 和 scalp_lane 可在同币种同时开多空→开仓即被 netting 对冲→
            # pnl=0 只扣手续费。实测3天30笔幽灵单纯送手续费。
            # 现在：同币种已有任一方向 scalp 仓时，禁止开反方向新仓。
            _opposite_side = "short" if _pos_side == "long" else "long"
            _opposite_count = len(_scalp_pos_by_key.get((sym, _opposite_side)) or [])
            if _opposite_count > 0:
                logger.info(
                    f"[ScalpRouter独立] {sym} 已有{_opposite_side} scalp仓，"
                    f"禁止反方向开仓（防止对冲送手续费）"
                )
                continue

            # ── 手续费感知期望值闸门（阶段一 1.1，最后一道也是最关键一道）──
            # 前面所有门（因子分/gate/veto/冷却/预算/风险硬顶）都过了，这里做最终
            # 数学期望校验：扣掉往返手续费+滑点后，用校准胜率算这笔到底是不是正期望。
            # 负期望直接拦截——这是把"多而烂"改成"少而精"的核心闸门。
            _ev_pwin = 0.42
            _ev_pwin_src = "fallback"
            # [2026-07-11] MR 单独标签：不再借用趋势打法的历史胜率/止盈实现率
            # （见 settings.py SCALP_MR_EV_TP_REALIZATION / SCALP_MR_COLD_BASE_RATE
            # 注释——两套打法退出机制不同，硬套同一份口径会把 MR 的 EV 永远判负）。
            _ev_strategy_tag = "ranging_mr" if _mr_active else "trend"
            try:
                from backend.services.scalp.scalp_ev_gate import scalp_ev_gate
                _ev_exchange = (
                    getattr(account, "selected_exchange", None)
                    or getattr(account, "exchange", None)
                )
                _ev = scalp_ev_gate.evaluate(
                    symbol=sym,
                    factor_score=_score_for_trade,  # [S7] 惩罚后决策分（原 raw）
                    direction=str(_sig.direction or "neutral"),
                    tp_pct=float(getattr(_sig, "tp_pct", 0) or 0),
                    sl_pct=float(getattr(_sig, "sl_pct", 0) or 0),
                    notional_usd=float(_margin_est or 0),
                    exchange=_ev_exchange,
                    strategy_tag=_ev_strategy_tag,
                    mode=(getattr(session_row, "trading_mode", None) or "paper"),
                    funding_rate=float(_md.get("funding_rate", 0) or 0) if isinstance(_md, dict) else 0.0,
                    # [2026-08-28 实盘零成交修复] 实盘引导期 EV 地板按账户判定
                    account_id=account_id,
                )
                _ev_pwin = _ev.p_win
                _ev_pwin_src = _ev.p_win_source
                if not _ev.allowed:
                    logger.info(
                        f"[ScalpRouter独立] {sym} EV闸门拦截: {_ev.reason} "
                        f"id={_gate.lane_decision_id}"
                    )
                    continue
                logger.info(f"[ScalpRouter独立] {sym} EV闸门放行: {_ev.reason}")
            except Exception as _ev_err:
                # Paper：fail-open 保样本；Live：可 fail-closed（SCALP_EV_FAIL_CLOSED_LIVE）
                _mode_ev = (getattr(session_row, "trading_mode", None) or "paper").strip().lower()
                _live_fail_closed = True
                try:
                    from backend.config.settings import SCALP_EV_FAIL_CLOSED_LIVE
                    _live_fail_closed = bool(SCALP_EV_FAIL_CLOSED_LIVE)
                except Exception:
                    pass
                if _mode_ev == "live" and _live_fail_closed:
                    logger.warning(
                        f"[ScalpRouter独立] {sym} EV闸门异常 Live fail-closed: {_ev_err}"
                    )
                    continue
                logger.debug(f"[ScalpRouter独立] {sym} EV闸门异常(降级放行): {_ev_err}")

            # ── 组合级风险预算（v6 计划 阶段1 第4项，下单前最后一道检查）──
            # [2026-08-16 用户反馈] evaluate_open 实测 45s+（日VaR历史模拟+DB），
            # 是短线热路径的静默卡点：信号在 EV 闸后消失、整轮 hang 190s 被强制重开、
            # 后段币种永远执行不到。paper 阶段默认跳过本检查（PB_PAPER_SKIP=true），
            # 账户级累计熔断仍由 position_memory（当日累计亏损5%）兜底。
            try:
                _pb_skip = os.getenv("PB_PAPER_SKIP", "true").strip().lower() in (
                    "1", "true", "yes", "on",
                )
            except Exception:
                _pb_skip = True
            _mode_pb = (_trade_mode or "paper").strip().lower()
            if not (_pb_skip and _mode_pb == "paper"):
                try:
                    from backend.services.risk_management.portfolio_budget import (
                        portfolio_budget as _pb,
                    )
                    from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutTimeout
                    # 阶段3：evaluate_open 历史 45s+ 挂起修复——独立线程 + 超时降级。
                    # 超时→本次放行（不阻塞热路径，下 tick 重试）；正常→按结果拦截。
                    _pb_tmo = float(os.getenv("PB_CHECK_TIMEOUT_S", "6") or 6)
                    _pb_exec = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pbcheck")
                    _pb_fut = _pb_exec.submit(
                        _pb.evaluate_open,
                        symbol=sym, action=_side_str, notional_usd=float(_margin_est or 0),
                        equity=equity, strategy="scalp", mode=_trade_mode, db=_db,
                        account_id=account_id,
                    )
                    try:
                        _pb_dec = _pb_fut.result(timeout=_pb_tmo)
                    except _FutTimeout:
                        _pb_exec.shutdown(wait=False)
                        logger.warning(
                            f"[ScalpRouter独立] {sym} 组合预算检查超时(>{_pb_tmo:.0f}s)，本次放行"
                        )
                        _bump_block("portfolio_budget_timeout")
                    else:
                        _pb_exec.shutdown(wait=False)
                        if not _pb_dec.allowed:
                            logger.info(
                                f"[ScalpRouter独立] {sym} 组合预算拦截: "
                                f"{';'.join(_pb_dec.reasons[:3])} id={_gate.lane_decision_id}"
                            )
                            _bump_block("portfolio_budget")
                            continue
                except Exception as _pb_err:
                    # [S9 2026-08-21] fail-closed：组合预算闸异常不得放行下单
                    logger.warning(
                        f"[ScalpRouter独立] {sym} 组合预算异常，拒绝开仓: {_pb_err}"
                    )
                    _bump_block("portfolio_budget_error")
                    continue

            # 直接下单（修复 BUG C：用正确的 place_order kwargs）
            # 2026-06-22: 杠杆改为动态计算（市场 + 本金），不再硬编码 8x。
            # 2026-07-09: _dyn_lev 已在上方"层预算检查"前算好（含 risk_band 封顶）并
            # 直接复用，此处不再重复计算，保证预算折算与实际下单用的是同一杠杆。
            if not isinstance(locals().get("_dyn_lev"), int) or _dyn_lev <= 0:
                _dyn_lev = 8  # 兜底：理论上不会触发（上方已必定赋值）

            # ── TP/SL 兜底（2026-07-21 P0 修复）──
            # scalp_loop 之前直接传 _sig.sl_price / _sig.tp_price 给 place_order，
            # 当 entry_price=0（行情拉取失败）时 SL=0.0 透传落库 → 无止损裸奔。
            # 对齐 master_execution 路径（paper_execution.py L425）补 finalize_open_tp_sl。
            # [S11 2026-08-21] 入场价统一回退 _md.price；仍取不到则不开仓——
            # 原实现 quantity=_margin_est/0=0 仍会下单，落库 0 价裸奔仓位。
            _scalp_entry = float(_sig.entry_price or _md.get("price") or 0)
            _scalp_sl = float(_sig.sl_price or 0)
            _scalp_tp = float(_sig.tp_price or 0)
            if _scalp_entry > 0 and (_scalp_sl <= 0 or _scalp_tp <= 0):
                try:
                    from backend.services.full_auto.paper_tp_sl import finalize_open_tp_sl
                    _scalp_sl, _scalp_tp = finalize_open_tp_sl(
                        symbol=sym,
                        trade_nature="scalp",
                        side=side,
                        price=_scalp_entry,
                        plan_sl=_scalp_sl if _scalp_sl > 0 else None,
                        plan_tp=_scalp_tp if _scalp_tp > 0 else None,
                    )
                    logger.info(
                        f"[ScalpRouter独立] {sym} TP/SL 兜底: "
                        f"entry={_scalp_entry:.4f} sl={_scalp_sl:.4f} tp={_scalp_tp:.4f}"
                    )
                except Exception as _final_err:
                    logger.warning(
                        f"[ScalpRouter独立] {sym} finalize_open_tp_sl 异常: {_final_err}"
                    )

            # ── 实验A：退出参数覆盖（SCALP_EXIT_PARAM_OVERRIDE，默认关）──
            # 依据《BTC_ETH_SOL真实交易与参数对照》：真实进场在 SL1.2%/TP3.0% 下
            # 反事实回放 PF=1.76；当前 router/结构扫描给的 SL/TP 过宽且多单 SL>TP。
            # 开关默认关；开启后强制覆盖 scalp 的 SL/TP，可随时回滚。
            try:
                _exit_override = os.getenv("SCALP_EXIT_PARAM_OVERRIDE", "0").lower() in (
                    "1", "true", "yes", "on",
                )
                if _exit_override and _scalp_entry > 0:
                    _sl_o = float(os.getenv("SCALP_EXIT_SL_PCT", "0.012") or 0.012)
                    _tp_o = float(os.getenv("SCALP_EXIT_TP_PCT", "0.030") or 0.030)
                    if str(side).lower() in ("long", "buy"):
                        _scalp_sl = _scalp_entry * (1.0 - _sl_o)
                        _scalp_tp = _scalp_entry * (1.0 + _tp_o)
                    else:
                        _scalp_sl = _scalp_entry * (1.0 + _sl_o)
                        _scalp_tp = _scalp_entry * (1.0 - _tp_o)
                    logger.info(
                        f"[ScalpRouter独立] {sym} 实验A退出参数覆盖: "
                        f"sl={_sl_o:.1%} tp={_tp_o:.1%} -> SL={_scalp_sl:.4f} TP={_scalp_tp:.4f}"
                    )
            except Exception as _exit_ov_err:
                logger.debug(f"[ScalpRouter独立] {sym} 退出参数覆盖跳过: {_exit_ov_err}")

            # ── 阶段4修复：TP/SL 价格按【实际入场价】重锚 ──
            # XPL 3885 教训：gate/信号按 24s 前的价格(0.0900)算出 TP=0.09135/SL=0.08897，
            # 但市价单以 0.0954 成交（期间暴涨6%）→ long 仓 TP 落在入场价之下，
            # 开仓即触发「亏损型止盈」。用原百分比结构重算价格、锚定真实入场价。
            # [2026-08-24 短线深挖 E] 锚点优先级加固：原实现 data_center.get_price
            # 失败时回退 _scalp_entry（=信号旧价），导致 8/24 XPL 再现假止盈
            # （TP 0.09135 < 成交 0.0954）。新优先级：数据中枢实时价 → _md 最新价
            # → 信号价；并把锚点回写为入场价，保证下单量/日志与 SL/TP 同锚。
            # [2026-08-24 深挖 E2] 关键修复：旧重锚逻辑用"新锚点反推距离百分比再重建"
            # 在数学上是恒等变换（重建出的仍是旧绝对价），真实成交价滑离信号价后
            # RR 结构被扭曲（8/24 VIRTUAL：成交 0.7162 vs 信号 0.72 → SL 距 1.69%、
            # TP 距 0.98% 倒挂）。正确做法：直接复用信号/gate 已算好的 sl_pct/tp_pct
            # 百分比（含猎杀区加宽与 RR 修复后的最终值），围绕真实成交价重建价格。
            if _scalp_entry > 0 and _scalp_tp > 0 and _scalp_sl > 0:
                _side_is_long_ra = str(side).lower() in ("long", "buy")
                try:
                    from backend.services.data_center import data_center as _dc_ra
                    _live_px = float(_dc_ra.get_price(sym, purpose="trade") or 0)
                except Exception:
                    _live_px = 0.0
                if _live_px <= 0 and isinstance(_md, dict):
                    try:
                        _live_px = float(_md.get("price") or _md.get("mark_price") or 0)
                    except Exception:
                        _live_px = 0.0
                _anchor = _live_px if _live_px > 0 else _scalp_entry
                # E2：优先用信号最终百分比（gate 输出已含猎杀区加宽 + RR 修复），
                # 缺失时才用旧绝对价距离反推（仅兜底，同样不是恒等重建）。
                try:
                    _sig_sl_pct = float(getattr(_sig, "sl_pct", 0) or 0)
                    _sig_tp_pct = float(getattr(_sig, "tp_pct", 0) or 0)
                except Exception:
                    _sig_sl_pct = _sig_tp_pct = 0.0
                if 0.0005 < _sig_sl_pct <= 0.08 and 0.0005 < _sig_tp_pct <= 0.08:
                    _sl_pct_ra, _tp_pct_ra = _sig_sl_pct, _sig_tp_pct
                    _pct_src = "signal_pct"
                else:
                    _sl_pct_ra = abs(_anchor - _scalp_sl) / _anchor if _anchor > 0 else 0.0
                    _tp_pct_ra = abs(_scalp_tp - _anchor) / _anchor if _anchor > 0 else 0.0
                    _pct_src = "distance_fallback"
                if _sl_pct_ra > 0 and _tp_pct_ra > 0:
                    _scalp_sl = (_anchor * (1 - _sl_pct_ra) if _side_is_long_ra
                                 else _anchor * (1 + _sl_pct_ra))
                    _scalp_tp = (_anchor * (1 + _tp_pct_ra) if _side_is_long_ra
                                 else _anchor * (1 - _tp_pct_ra))
                    # 方向一致性终检：long 的 TP 必须 > 入场价、SL < 入场价（short 反之）
                    _tp_ok_ra = (_scalp_tp > _anchor) if _side_is_long_ra else (_scalp_tp < _anchor)
                    _sl_ok_ra = (_scalp_sl < _anchor) if _side_is_long_ra else (_scalp_sl > _anchor)
                    if not (_tp_ok_ra and _sl_ok_ra):
                        logger.warning(
                            "[ScalpRouter独立] %s %s TP/SL 方向异常(tp=%.5f sl=%.5f anchor=%.5f)，拒绝开仓",
                            sym, side, _scalp_tp, _scalp_sl, _anchor,
                        )
                        _bump_block("tp_sl_direction_guard")
                        continue
                    if abs(_anchor - _scalp_entry) > 1e-12:
                        logger.info(
                            "[ScalpRouter独立] %s 入场价重锚: signal=%.5f -> live=%.5f "
                            "(sl=%.5f tp=%.5f pct_src=%s)",
                            sym, _scalp_entry, _anchor, _scalp_sl, _scalp_tp, _pct_src,
                        )
                        _scalp_entry = _anchor  # 下单量/日志与 SL/TP 同锚
                    if abs(_scalp_tp - float(_sig.tp_price or 0)) > 1e-9 or abs(_scalp_sl - float(_sig.sl_price or 0)) > 1e-9:
                        logger.info(
                            "[ScalpRouter独立] %s TP/SL 重锚: sl=%.5f->%.5f tp=%.5f->%.5f (signal_price=%.5f live=%.5f pct_src=%s)",
                            sym, float(_sig.sl_price or 0), _scalp_sl, float(_sig.tp_price or 0), _scalp_tp,
                            float(_sig.entry_price or 0), _anchor, _pct_src,
                        )

            # [S11 2026-08-21] 无有效入场价不开仓（原实现会下 quantity=0 的废单）
            if _scalp_entry <= 0:
                logger.info(f"[ScalpRouter独立] {sym} 入场价无效({_scalp_entry})，跳过开仓")
                _bump_block("no_entry_price")
                continue

            try:
                # [修复] place_order 前刷新 DB 连接(防 600s factor 计算后连接失效)
                try:
                    _db.expire_all()
                    _db.execute(_sa_text("SELECT 1"))
                except Exception:
                    _db.rollback()
                    _db.expire_all()
                # [2026-08-17 仲裁 Gate] 与 master/MT 总控的相反方向冲突时拒绝开仓
                try:
                    from backend.services.full_auto.decision_arbitration import (
                        check_entry as _arb_check,
                        register_view as _arb_register,
                    )
                    _arb_conf = float(_sig.factor_score or 0)
                    _arb_register(sym, "short", "scalp", side, _arb_conf)
                    _arb_ok, _arb_why = _arb_check(sym, "short", "scalp", side, _arb_conf)
                    if not _arb_ok:
                        logger.info(
                            "[ArbGate] scalp 独立开仓被仲裁拒绝 %s/%s %s -> hold (%s)",
                            sym, "short", side, _arb_why,
                        )
                        _bump_block("arbitration_gate")
                        continue
                except Exception as _arb_err:
                    # [S9 2026-08-21] fail-closed：仲裁闸异常不得放行下单
                    # （仲裁正是防对冲冲突的，异常放行等于无仲裁裸开）
                    logger.warning(
                        "[ArbGate] scalp 仲裁异常，拒绝开仓 %s: %s", sym, _arb_err
                    )
                    _bump_block("arbitration_error")
                    continue
                # [2026-08-22 M0-4] 每日开仓上限
                if _scalp_daily_cap > 0 and _scalp_opened_today >= _scalp_daily_cap:
                    logger.info(
                        f"[ScalpRouter独立] {sym} 今日已开 {_scalp_opened_today}/"
                        f"{_scalp_daily_cap}，达到每日开仓上限，跳过 (M0-4)"
                    )
                    _bump_block("daily_open_cap")
                    continue
                # ── R2 风控禁开（阶段3）：该币 24h 内单笔已实现亏损 >1.5% 权益 → 禁开 ──
                try:
                    if os.getenv("FUSION_RISK_EVENT_BAN", "true").strip().lower() not in ("0", "false", "off"):
                        from backend.services.symbol_penalty import is_risk_banned as _risk_banned
                        if _risk_banned(sym):
                            logger.info("[FusionArbiter] %s 24h 风控禁开（单笔已实现亏损>1.5%%权益）", sym)
                            _bump_block("fusion_risk_ban")
                            continue
                except Exception as _rb_err:
                    logger.debug("[FusionArbiter] %s 风控禁开检查失败: %s", sym, _rb_err)

                # ── 融合仲裁器（阶段0 v2：pwin 主轴 + RR 下限；FUSION_MODE=factor 整体关闭）──
                try:
                    _fusion_mode = os.getenv("FUSION_MODE", "hybrid").strip().lower()
                except Exception:
                    _fusion_mode = "hybrid"
                if _fusion_mode != "factor":
                    try:
                        from backend.services.decision_fusion_arbiter import decide_scalp
                        # ── LLM thesis（阶段1）：orchestrator 信封方向 + 置信度 ──
                        _orch_f = _md.get("orchestrator") if isinstance(_md, dict) else {}
                        if not isinstance(_orch_f, dict):
                            _orch_f = {}
                        _dir_l = str(_sig.direction or "").strip().lower()
                        _tdir_raw = (
                            _orch_f.get("long_bias") if _dir_l == "long"
                            else _orch_f.get("short_bias")
                        ) or ""
                        _tdir = {"bullish": "long", "bearish": "short"}.get(
                            str(_tdir_raw).strip().lower()
                        )
                        _tconf_key = "long_confidence" if _dir_l == "long" else "short_confidence"
                        try:
                            _tconf = float(_orch_f.get(_tconf_key) or 0)
                        except Exception:
                            _tconf = 0.0
                        # [2026-08-29 全面修复 P2.4] 来源信用接线：该信号将由
                        # hybrid(有thesis)/factor 来源裁决，按其滚动净期望决定
                        # 是否 standdown。异常 fail-open（credit=1.0）。
                        _credit_fus = 1.0
                        try:
                            from backend.services.source_attribution import (
                                attribution as _attr_credit_f,
                            )
                            _credit_fus = _attr_credit_f.credit(
                                "hybrid" if _tdir else "factor",
                                "scalp", str(sym).upper(),
                            )
                        except Exception:
                            _credit_fus = 1.0
                        _fus = decide_scalp(
                            pwin=_meta_pwin,
                            factor_score=float(_score_for_trade or 0),
                            direction=str(_sig.direction or "long"),
                            thesis_dir=_tdir,
                            thesis_conf=_tconf,
                            tp_pct=float(getattr(_sig, "tp_pct", 0) or 0),
                            sl_pct=float(getattr(_sig, "sl_pct", 0) or 0),
                            mode=_trade_mode,
                            account_id=account_id,
                            credit=_credit_fus,
                            is_mr=bool(_md.get("ranging_mr")) if isinstance(_md, dict) else False,
                        )
                        # 阶段2：thesis 强反向冲突 → 1 次窄 JSON LLM 仲裁；
                        # 放行=0.25x 试探仓；失败/否决 → 保守 hold。
                        if _fus.action == "arbitrate":
                            try:
                                from backend.services.source_attribution import llm_arbitrate_conflict
                                _arb_res = llm_arbitrate_conflict(
                                    symbol=sym, direction=str(_sig.direction or "long"),
                                    thesis_dir=_tdir or "", thesis_conf=_tconf,
                                    pwin=float(_meta_pwin or 0),
                                    factor_score=float(_score_for_trade or 0),
                                    account_id=account_id,
                                    trading_mode=(_trade_mode or "paper"),
                                )
                            except Exception as _arb_call_err:
                                logger.debug("[FusionArbiter] %s LLM 仲裁异常: %s", sym, _arb_call_err)
                                _arb_res = None
                            if _arb_res is True:
                                _fus = type(_fus)("trade", 0.25, "hybrid", "thesis_conflict_llm_allowed",
                                                 {"thesis_dir": _tdir, "thesis_conf": _tconf})
                            else:
                                _fus = type(_fus)("hold", 0.0, "llm", "thesis_conflict_hold",
                                                 {"thesis_dir": _tdir, "thesis_conf": _tconf, "llm": _arb_res})
                        _fusion_decision = _fus.to_dict()
                        if not _fus.allowed:
                            logger.info(
                                "[FusionArbiter] %s %s 拦截: %s tags=%s",
                                sym, str(_sig.direction), _fus.reason, _fus.tags,
                            )
                            _bump_block("fusion_arbiter")
                            continue
                    except Exception as _fus_err:
                        # [2026-09-02 G18] 原为 fail-open（怕新代码 bug 停摆短线）。
                        # 但这一层同样以 pwin 为主轴，放行即绕过唯一有效的质量闸；
                        # 与上方主轴仲裁统一为默认 fail-closed，共用同一开关便于回滚。
                        _fus_fail_closed = True
                        try:
                            from backend.config.settings import SCALP_PWIN_FAIL_CLOSED
                            _fus_fail_closed = bool(SCALP_PWIN_FAIL_CLOSED)
                        except Exception:
                            pass
                        if _fus_fail_closed:
                            logger.warning(
                                "[FusionArbiter] %s 异常 → fail-closed 拒开: %s",
                                sym, _fus_err,
                            )
                            _bump_block("fusion_arbiter_error")
                            continue
                        _fusion_decision = {
                            "action": "trade", "source": "rule",
                            "reason": "arbiter_error_fail_open",
                            "error": str(_fus_err)[:120],
                        }
                        logger.warning("[FusionArbiter] %s 异常 fail-open: %s", sym, _fus_err)

                # [2026-08-24 短线深挖 I] 本地 LLM 确认层：全部规则闸门通过后、
                # 下单前用本地 Ollama 做一次 JSON 裁决。fail-open（任何异常放行），
                # 裁决结果写进 _snap（features_json.llm_confirm）供信号结算后
                # 对比 confirm/reject 两组胜率，LLM 边际可测量。
                # 关闭：SCALP_LLM_CONFIRM_ENABLED=0。
                _llm_confirm: Dict[str, Any] = {}
                try:
                    _llm_cfg_on = str(os.getenv("SCALP_LLM_CONFIRM_ENABLED", "true") or "").strip().lower()
                    if _llm_cfg_on in ("1", "true", "yes", "on"):
                        from backend.services.scalp.scalp_llm_confirm import scalp_llm_confirm
                        _llm_confirm = scalp_llm_confirm(
                            symbol=sym,
                            side=_pos_side,
                            score=float(_score_for_trade or 0),
                            sl_pct=float(getattr(_sig, "sl_pct", 0) or 0),
                            tp_pct=float(getattr(_sig, "tp_pct", 0) or 0),
                            is_mr=bool(_mr_active),
                            regime=(_md.get("regime") or "unknown") if isinstance(_md, dict) else "unknown",
                            funding=float(_md.get("funding_rate", 0) or 0) if isinstance(_md, dict) else 0.0,
                            market_data=_md if isinstance(_md, dict) else None,
                            account_id=account_id,
                        )
                        if not _llm_confirm.get("confirmed", True):
                            logger.info(
                                "[ScalpRouter独立] %s LLM确认层拒绝: %s (source=%s)",
                                sym, _llm_confirm.get("reason", ""), _llm_confirm.get("source", "?"),
                            )
                            _bump_block("llm_confirm_reject")
                            continue
                except Exception as _llm_cf_err:
                    logger.debug("[ScalpRouter独立] %s LLM确认层异常(fail-open): %s", sym, _llm_cf_err)
                    _llm_confirm = {"confirmed": True, "source": "error_fail_open",
                                    "reason": str(_llm_cf_err)[:60]}

                # [2026-08-22 M1-8] 信号日志在全部闸门通过、即将下单时写入
                try:
                    if _fusion_decision:
                        _snap["fusion"] = _fusion_decision
                    if _llm_confirm:
                        _snap["llm_confirm"] = _llm_confirm
                    # [2026-08-29 P2.6] 出场结构特征入快照（tp/sl/rr）：诊断显示
                    # 分数无信息而结构决定盈亏（SL avg -4.52 vs TP ≈0），meta
                    # 模型重训需要这些特征。最终结构由执行门算出（_gate），此处
                    # 在落信号前采集。
                    try:
                        _final_sl = float(getattr(_gate, "sl_pct", 0) or 0)
                        _final_tp = float(getattr(_gate, "tp_pct", 0) or 0)
                        if _final_sl > 0:
                            _snap["struct_sl_pct"] = round(_final_sl, 6)
                            _snap["struct_tp_pct"] = round(_final_tp, 6)
                            _snap["struct_rr"] = round(_final_tp / _final_sl, 4)
                    except Exception:
                        pass
                    _log_sig(
                        symbol=sym, direction=str(_sig.direction),
                        action=str(_sig.action or "buy"),
                        factor_score=float(_sig.factor_score or 0),
                        threshold=float(_thresh),
                        entry_price=float(_md.get("price") or _md.get("mark_price") or 0),
                        features=_snap, session_id=session_id, account_id=account_id,
                        # [2026-09-02 P1.1] 冻结这条信号的 TP/SL 距离，供
                        # triple-barrier 标签复算"TP 与 SL 谁先被触及"。
                        tp_pct=float(_sig.tp_pct or 0) or None,
                        sl_pct=float(_sig.sl_pct or 0) or None,
                    )
                except Exception as _log_err:
                    logger.debug(f"[ScalpRouter独立] {sym} 信号日志跳过: {_log_err}")

                # ── 做空车道闸门（2026-09-02 P2.4）────────────────────────
                # 依据：triple-barrier 口径 5.5 万条已结算信号，做空 32880 条
                # 胜率 30.3%、净 -34.14bp，总贡献 -112 万 bp；即便叠加最严的
                # pwin>=0.55 仍为 -14.15bp（2055 条）—— 做空不是"门槛没调好"，
                # 而是在当前因子体系下整体负期望。同期做多 22507 条净 +12.82bp，
                # pwin>=0.55 时 +28.73bp。
                # 拦截点特意放在 log_signal 之后：信号照常落库、TB 标签继续结算，
                # 做空样本不断积累（等它自己转正就能放开），只是不再下真单 ——
                # 这就是"转独立实验车道"的含义，而非把做空从学习闭环里删掉。
                if str(_sig.direction or "").lower() == "short":
                    try:
                        from backend.services.scalp.short_lane_policy import (
                            short_open_allowed,
                        )
                        _sh_ok, _sh_why = short_open_allowed(
                            pwin=_meta_pwin,
                            factor_score=float(_sig.factor_score or 0),
                        )
                    except Exception as _sh_err:
                        # fail-closed：闸门自身异常时不放行做空。做空已被证实为
                        # 负期望，异常放行等于回到亏损路径。
                        _sh_ok, _sh_why = False, f"policy_error:{_sh_err}"
                    if not _sh_ok:
                        logger.info(
                            "[ScalpRouter独立] %s 做空已闸断(%s)，信号已留档供学习",
                            sym, _sh_why,
                        )
                        _bump_block("short_lane_disabled")
                        continue

                # ── [2026-09-03 v3 F1a] 短线影子模式 ──────────────────────────
                # 数据：短线 08-26→09-02 净 -$1,193 / 手续费 $1,420，单笔目标 ≈ 4bp
                # 而往返成本 ≈ 8bp —— 车道几何上负期望。全部闸门与学习闭环照常，
                # 但走到下真单这一步改写 action='shadow_fill'（冻结本来会成交的
                # 入场/TP/SL/杠杆/名义），零手续费。恢复真单只能经 edge_ledger
                # 的晋升门（N ≥ 300 且净收益 95% 下界 > 0）。fail-closed：影子
                # 判定或落库异常一律不下真单。
                _shadow_on = True
                try:
                    from backend.services.scalp.shadow_mode import (
                        scalp_shadow_enabled as _shadow_enabled,
                        record_shadow_fill as _record_shadow,
                    )
                    _shadow_on = _shadow_enabled(_trade_mode)
                except Exception as _shadow_err:
                    logger.warning("[ScalpShadow] 开关读取异常，按影子处理: %s", _shadow_err)
                    _record_shadow = None
                if _shadow_on:
                    try:
                        if _record_shadow is not None:
                            _record_shadow(
                                symbol=sym, direction=str(_sig.direction or ""),
                                entry_price=_scalp_entry, tp_price=_scalp_tp, sl_price=_scalp_sl,
                                leverage=_dyn_lev, notional_usd=float(_margin_est or 0),
                                factor_score=float(_sig.factor_score or 0),
                                threshold=float(_thresh) if _thresh is not None else None,
                                session_id=session_id, account_id=account_id,
                                trade_mode=_trade_mode,
                                features={
                                    "pwin": _meta_pwin,
                                    "fusion_reason": (_fusion_decision or {}).get("reason"),
                                    "gate_tier": getattr(_gate, "tier", None),
                                },
                            )
                    except Exception as _shadow_rec_err:
                        logger.warning("[ScalpShadow] %s 影子落库异常: %s", sym, _shadow_rec_err)
                    logger.info(
                        "[ScalpShadow] %s %s 影子成交(不下真单) entry=%.6g tp=%.6g sl=%.6g lev=%s notional=%.2f",
                        sym, side, _scalp_entry, _scalp_tp, _scalp_sl, _dyn_lev, float(_margin_est or 0),
                    )
                    _bump_block("scalp_shadow_mode")
                    continue

                # [2026-08-28 实盘接线GAP-1] live 会话走 LiveExecutor（LPM 合并账本
                # 由 LIVE_SUB_POSITION_TRACKING 控制）；paper 维持原路径。
                if (_trade_mode or "").lower() == "live":
                    # [2026-08-28 实盘收紧] 每日开单总量上限：所有闸门都过了、
                    # 真正下单前最后校验（只检查不扣减，成交后才 bump——
                    # 此前预扣导致被拦信号白白消耗配额）。
                    try:
                        from backend.services.full_auto.live_gate_policy import (
                            live_enforce_daily_open_cap,
                        )
                        _q_ok, _q_used, _q_cap = live_enforce_daily_open_cap(
                            str(getattr(session_row, "session_id", "") or "")
                        )
                        if not _q_ok:
                            logger.info(
                                "[ScalpRouter独立] %s 实盘每日开单上限已达 %d/%d，跳过",
                                sym, _q_used, _q_cap,
                            )
                            _bump_block("live_daily_open_cap")
                            continue
                    except Exception as _q_err:
                        logger.debug("[ScalpRouter独立] 实盘配额检查跳过: %s", _q_err)
                    # [2026-08-29 符号校验] 信号池是多所数据；不在当前交易所
                    # 上市的币直接跳过（实测 XPL 不在币安永续，首单被拒）。
                    try:
                        from backend.services.full_auto.live_trading import (
                            live_symbol_tradable,
                        )
                        if not live_symbol_tradable(sym, "binance"):
                            _bump_block("symbol_not_on_exchange")
                            continue
                    except Exception:
                        pass
                    # [2026-08-29 门禁对齐] scalp 实盘车道接入与 master 同一
                    # 宪法检查 + 18% 敞口封顶。此前 scalp 直连 LiveExecutor
                    # 完全绕过宪法（实测 6×$44 VIRTUAL → $230 敞口/权益$67
                    # =340%），而 master 车道却被同一宪法拦死——两条车道口径
                    # 必须一致。
                    try:
                        from backend.services.full_auto.live_trading import (
                            fetch_live_account_snapshot,
                            live_constitutional_check_raw,
                        )
                        # [2026-08-29 快照原子性] 封顶与检查共用同一次快照：
                        # 此前两次独立获取，快照偶发失败时出现"未封顶却检查"
                        # 错位（实测 13:02 $32.74 未缩放被拦，同 tick 快照一败
                        # 一成）。快照失败 → 本 tick 跳过（fail-closed），
                        # 下一 tick（约10s）重试。
                        _lc_snap = fetch_live_account_snapshot(_db, int(trading_acct_id))
                        _lc_eq = float(_lc_snap.get("total_equity") or 0)
                        if _lc_eq <= 0:
                            logger.info(
                                "[ScalpRouter独立] %s 实盘快照不可用，本tick跳过",
                                sym,
                            )
                            _bump_block("live_snapshot_unavailable")
                            continue
                        _notional_live = float(_margin_est or 0)
                        if _notional_live > _lc_eq * 0.20:
                            _capped = _lc_eq * 0.18
                            logger.info(
                                "[LiveCap] %s 名义敞口 %.2f→%.2f (18%%权益)",
                                sym, _notional_live, _capped,
                            )
                            _notional_live = _capped
                        _lc_ok, _lc_msg = live_constitutional_check_raw(
                            _db, int(trading_acct_id), sym, side, _notional_live,
                            snapshot=_lc_snap,
                        )
                        if not _lc_ok:
                            logger.info(
                                "[ScalpRouter独立] %s 实盘宪法拦截: %s", sym, _lc_msg,
                            )
                            _bump_block("live_constitutional")
                            continue
                        _margin_est = _notional_live
                    except Exception as _lc_err:
                        logger.debug("[ScalpRouter独立] 宪法检查异常(放行): %s", _lc_err)
                    # [2026-09-04 杠杆统一] 实盘短线此前把上游 position_sizing_agent
                    # 算出的动态杠杆（5~10x）直接交给 LiveExecutor，绕过了
                    # leverage_authority —— 而 paper 侧经 trade_gate 会被改写成
                    # 币种档位（MAP 里 XRP/SOL=4、未列入=3）。同一个币在模拟盘 4x、
                    # 实盘 10x，两套账根本不可比；更要命的是交易所的杠杆是按 symbol
                    # 设的、同向同币仓位会合并，同币先后按不同倍数下单，后一次
                    # set_leverage 会覆盖前一次，已建仓位的爆仓价被就地改写。
                    # 这里改为与 paper 同一个权威源；解析失败一律拒单（与
                    # LIVE_LEVERAGE_FAIL_CLOSE 同精神：宁可不开，不能按错误倍数开）。
                    try:
                        from backend.services.leverage_authority import (
                            resolve_leverage as _auth_lev,
                        )
                        _live_lev = float(_auth_lev(
                            tier="short", requested=_dyn_lev, symbol=sym,
                        ))
                    except Exception as _lev_err:
                        logger.error(
                            "[ScalpRouter独立] %s 实盘杠杆解析失败，拒绝开仓: %s",
                            sym, _lev_err, exc_info=True,
                        )
                        _bump_block("live_leverage_resolve_failed")
                        continue
                    if _live_lev != _dyn_lev:
                        logger.info(
                            "[ScalpRouter独立] %s 实盘杠杆对齐币种档位 %.0fx→%.0fx"
                            "（名义 %.2f 不变，保证金随之调整）",
                            sym, _dyn_lev, _live_lev, float(_margin_est or 0),
                        )
                    try:
                        from backend.services.exchange.executors import OrderContext
                        from backend.services.exchange.live_executor import LiveExecutor
                        _ores = LiveExecutor().place_order(_db, OrderContext(
                            account_id=int(trading_acct_id),
                            symbol=sym,
                            side=side,
                            quantity=_margin_est / _scalp_entry if _scalp_entry > 0 else 0,
                            order_type="market",
                            leverage=_live_lev,
                            tp_price=_scalp_tp,
                            sl_price=_scalp_sl,
                            strategy_id=_scalp_strategy_id,
                            timeframe_tier="short",
                            trade_nature="scalp",
                        ))
                        _fill_res = {
                            "status": str(getattr(_ores, "status", "error") or "error"),
                            "position_id": getattr(_ores, "position_id", None),
                        }
                    except Exception as _live_err:
                        logger.error(
                            "[ScalpRouter独立] live 下单异常 %s: %s", sym, _live_err,
                            exc_info=True,
                        )
                        _bump_block("live_order_error")
                        continue
                else:
                    _fill_res = paper_engine.place_order(
                        db=_db,
                        account_id=trading_acct_id,  # [2026-07-10 修复] 原 account_id 未定义→NameError
                        symbol=sym,
                        side=side,
                        quantity=_margin_est / _scalp_entry if _scalp_entry > 0 else 0,
                        order_type="market",
                        leverage=_dyn_lev,  # 动态杠杆（市场 + 本金），异常兜底 8x
                        trade_nature="scalp",
                        timeframe_tier="short",
                        tp_price=_scalp_tp,  # 兜底后的 TP
                        sl_price=_scalp_sl,  # 兜底后的 SL
                        strategy_id=_scalp_strategy_id,
                    )
                _db.commit()

                # [S11 2026-08-21] 拒单/未成交不写任何成交副作用
                # （假冷却、假 _scalp_opens_this_tick、假 TCP executed=True、
                #   假校准快照都会污染后续门槛判断与日检对账）。
                _fill_ok = bool(
                    isinstance(_fill_res, dict)
                    and (int(_fill_res.get("position_id") or 0) > 0
                         or str(_fill_res.get("status") or "").lower() == "filled")
                )
                if not _fill_ok:
                    logger.warning(
                        "[ScalpRouter独立] %s %s 下单未成交(res=%s)，跳过冷却/计数/TCP 快照",
                        sym, side, _fill_res,
                    )
                    _bump_block("order_not_filled")
                    continue
                _scalp_opened_today += 1  # [M0-4] 成功开仓后计数 +1
                # [2026-08-28 实盘收紧] 真实成交后扣减每日开单配额
                if (_trade_mode or "").lower() == "live":
                    try:
                        from backend.services.full_auto.live_gate_policy import (
                            live_daily_open_bump,
                        )
                        live_daily_open_bump(
                            str(getattr(session_row, "session_id", "") or "")
                        )
                    except Exception:
                        pass

                # ── 融合归因（阶段2）：来源标签绑定仓位 ──
                try:
                    from backend.services.source_attribution import attribution as _attr_s
                    _pid_tag = (_fill_res or {}).get("position_id") if isinstance(_fill_res, dict) else None
                    _attr_s.tag_position(
                        int(_pid_tag or 0),
                        source=str((_fusion_decision or {}).get("source") or "factor"),
                        nature="scalp", symbol=sym,
                        meta={"fusion": _fusion_decision or {}},
                    )
                except Exception as _tag_err:
                    logger.debug("[FusionAttr] 标签绑定失败: %s", _tag_err)

                # ── 记录因子分快照供置信度校准（阶段一 1.2）──
                # 写入一条 scalp_composite 反馈行，trade_id=持仓id、signal_value=因子分。
                # 平仓时 paper_engine.update_trade_pnl 会按 trade_id 自动回填盈亏，
                # 校准器据此把"因子分→真实胜率"拟合出来（无需额外关仓钩子）。
                try:
                    _pos_id_for_cal = (_fill_res or {}).get("position_id") if isinstance(_fill_res, dict) else None
                    if _pos_id_for_cal:
                        from backend.services.scalp.scalp_confidence_calibrator import (
                            scalp_confidence_calibrator,
                        )
                        scalp_confidence_calibrator.record_scalp_composite(
                            db=_db,
                            account_id=trading_acct_id,  # [2026-07-10 修复] 原 account_id 未定义
                            trade_id=int(_pos_id_for_cal),
                            symbol=sym,
                            side=side,
                            factor_score=_score_for_trade,  # [S7] 校准学到驱动决策的分
                            direction=str(_sig.direction or "neutral"),
                            # [2026-07-11] 分开记录战绩，MR 自己攒自己的胜率曲线。
                            strategy_tag=_ev_strategy_tag,
                        )
                except Exception as _cal_rec_err:
                    logger.debug(f"[ScalpRouter独立] {sym} 校准快照记录跳过: {_cal_rec_err}")
                if not hasattr(self, "_scalp_lane_meta"):
                    self._scalp_lane_meta = {}
                self._scalp_lane_meta[f"{sym}:{side}"] = {
                    "lane_decision_id": _gate.lane_decision_id,
                    "gate_tier": _gate.tier,
                    "veto_at": time.time() if _gate.needs_veto else 0,
                    "opened_at": time.time(),
                }
                # 记录开仓时间戳（2026-06-22 开仓冷却用）
            # [M2-2] 冷却写入键与读取键一致（含账户维度）
                self._scalp_open_ts[f"a{account_id}:{sym}"] = time.time()
                self._scalp_open_ts_side[f"a{account_id}:{sym}:{_side_str}"] = self._scalp_open_ts[f"a{account_id}:{sym}"]
                _scalp_opens_this_tick += 1
                # [2026-08-27] 探索配额成交后扣减（决策时只打标签，避免被
                # 后续闸门拦掉仍占配额——实测预扣 5/5 只成交 1 笔）
                try:
                    _pa = (_scalp_factor or {}).get("pwin_arbiter") or {}
                    if _pa.get("tags", {}).get("explore_quota"):
                        from backend.services.decision_fusion_arbiter import explore_quota_bump
                        # [2026-08-29] 按账户扣减：paper/live 配额独立
                        explore_quota_bump(account_id)
                    # [2026-08-29 v2] 保底流量探针配额成交后扣减（同探索模式：
                    # 决策时只打标签，真实成交才计数）
                    # [2026-08-31] shadow_probe_quota 与 pwin_probe_quota 共享同一配额；
                    # 同时认 factor 车道(_pa)与 fusion 车道(_fusion_decision)的决定。
                    _probe_reasons = {"pwin_probe_quota", "shadow_probe_quota"}
                    if (_pa.get("reason") in _probe_reasons
                            or (_fusion_decision or {}).get("reason") in _probe_reasons):
                        from backend.services.decision_fusion_arbiter import probe_quota_bump
                        probe_quota_bump(account_id)
                except Exception:
                    pass
                # 修复5: 发布短线 Insight 到 AlphaBus（供中线/长线 overlay）
                try:
                    from backend.services.bus.alpha_bus import get_default_alpha_bus
                    from backend.services.contracts.types import Direction, Horizon
                    _bus = get_default_alpha_bus()
                    _bus.publish_insight(__import__("backend.services.contracts.types", fromlist=["Insight"]).Insight(
                        ts_ns=int(time.time() * 1e9),
                        instrument=__import__("backend.services.contracts.types", fromlist=["Instrument"]).Instrument(
                            symbol=sym, venue="paper", kind="perp"),
                        direction=Direction.LONG if side == "buy" else Direction.SHORT,
                        confidence=float(_score_for_trade or 50) / 100.0,  # [S7] 决策分
                        magnitude=0.0, period_ns=3600_000_000_000,
                        horizon=Horizon.SCALP, source="scalp_router",
                        expiry_ns=int((time.time() + 7200) * 1e9),
                    ))
                except Exception:
                    pass  # AlphaBus 不影响 scalp 主流程
                if session_row:
                    self._persist_tcp_snapshot(
                        session_row,
                        symbol=sym.upper(),
                        tier="short",
                        action=side,
                        confidence=_scalp_prop.confidence,
                        proposal=_scalp_prop.to_dict(),
                        evaluate_verdict={**_scalp_verdict.to_dict(), "allowed": True},
                        source_lane="scalp_lane",
                        proposal_id=_scalp_prop.proposal_id,
                        executed=True,
                        execution_channel=_mode_pb,  # [S3] 按真实 mode 写，live 不再标 paper
                        strategy_id=_scalp_strategy_id,
                    )
                _plan = (_md.get("_tpsl_plan") if isinstance(_md, dict) else None) or {}
                if not _plan and isinstance(getattr(_gate, "audit", None), dict):
                    _plan = _gate.audit.get("tpsl_plan") or {}
                _plan_bit = ""
                if isinstance(_plan, dict) and (_plan.get("reason") or _plan.get("playbook")):
                    _plan_bit = (
                        f" play={_plan.get('playbook') or getattr(_sig, 'tpsl_playbook', '')}"
                        f" regime={_plan.get('regime', '')}"
                        f" why={str(_plan.get('reason') or getattr(_sig, 'tpsl_reason', '') or '')[:80]}"
                    )
                logger.info(
                    f"[ScalpRouter独立]{'[ScalpMR]' if _mr_active else ''} {sym} {side} 成交! lev={_dyn_lev}x "
                    f"score={_sig.factor_score} eff={_gate.effective_score} "
                    f"entry={_sig.entry_price:.2f} sl={_sig.sl_pct:.2%} tp={_sig.tp_pct:.2%} "
                    f"lane={_gate.lane_decision_id}{_plan_bit}"
                )

                # ── 开单后 AI 即时复审（2026-06-22）──
                # 异步调用精简版 AI 审核，不阻塞交易主循环。
                # 仅传入当前仓位 + market_summary，不调完整分析师管道。
                # 复审结论为 close/reduce 时记录到 session event_log，
                # 由 _run_hold_timeout_ai_review_if_needed 在下个 tick 处理。
                _review_sym = sym
                _review_side = side
                _review_session_id = session_id
                _review_account_id = account_id
                _review_md = dict(_md) if _md else {}
                _review_entry = float(_sig.entry_price)
                _review_lev = int(_dyn_lev)  # 固化本次开仓杠杆，供异步复审用
                _review_lane_id = _gate.lane_decision_id
                _review_gate_tier = _gate.tier
                import threading as _thr
                def _async_scalp_review():
                    import time as _time

                    # [C1] 线程不继承 ContextVar,后台 review 线程需自己设 system_identity,
                    # 否则 _rdb 的 RLS 会 fail-closed。
                    from backend.core.tenant import set_system_identity as _set_sys_id
                    _set_sys_id()
                    from backend.database.connection import SessionLocal as _RSL
                    from backend.database.models import FullAutoSession as _RFS
                    _rdb = _RSL()
                    try:
                        _rsess = _rdb.query(_RFS).filter(
                            _RFS.session_id == _review_session_id
                        ).first()
                        if not _rsess:
                            return
                        # 精简版 AI 审核：只给仓位基础信息 + 市场上下文
                        try:
                            from backend.services.trend_agent import trend_agent
                            _review_pos = {
                                "symbol": _review_sym,
                                "side": _review_side,
                                "entry_price": _review_entry,
                                "mark_price": _review_md.get("mark_price") or _review_md.get("price", _review_entry),
                                "pnl_pct": 0.0,
                                "hold_hours": 0.0,
                                "leverage": _review_lev,
                            }
                            _reports = {}
                            if _review_md.get("factor_signal"):
                                _reports["scalp_factor"] = _review_md["factor_signal"]
                            _envs = {
                                _review_sym: {
                                    "price": _review_md.get("mark_price") or _review_md.get("price", _review_entry),
                                    "volume_24h": _review_md.get("volume_24h"),
                                    "rsi_14": _review_md.get("rsi"),
                                }
                            }
                            _review_result = trend_agent.review_position(
                                symbol=_review_sym,
                                side=_review_side,
                                position=_review_pos,
                                reports=_reports,
                                market_envs=_envs,
                                account_id=_review_account_id,
                            )
                            _action = (_review_result.get("action") or "hold").lower()
                            _reason = _review_result.get("reasoning", "")[:150]
                            _mark = float(
                                _review_md.get("mark_price")
                                or _review_md.get("price", _review_entry)
                                or _review_entry
                            )
                            _pnl_pct = 0.0
                            if _review_side == "buy":
                                _pnl_pct = (_mark - _review_entry) / _review_entry if _review_entry else 0
                            else:
                                _pnl_pct = (_review_entry - _mark) / _review_entry if _review_entry else 0
                            self._append_event(
                                _rsess, "scalp_ai_review",
                                f"{_review_sym} {_review_side} 开单后AI复审: {_action} | {_reason} "
                                f"| lane={_review_lane_id}",
                                severity="warning" if _action in ("close", "reduce") else "info",
                            )
                            # Phase 4: veto band 开单后 2min 内 AI close + 浮亏 → 即时 reduce
                            _fast_reduce = False
                            if _action in ("close", "reduce"):
                                _lane_meta = getattr(self, "_scalp_lane_meta", {}).get(
                                    f"{_review_sym}:{_review_side}", {}
                                )
                                _opened = float(_lane_meta.get("opened_at") or 0)
                                _veto_band = _review_gate_tier == "veto" or _lane_meta.get("gate_tier") == "veto"
                                if _veto_band and _opened and (_time.time() - _opened) < 120 and _pnl_pct < -0.005:
                                    _fast_reduce = True
                            if _action in ("close", "reduce"):
                                # [升级D] 禁用 scalp AI 反向平仓。
                                # 实测 ai_reverse 10笔胜率10%、亏$4.34。
                                # scalp 持仓短（<45min），AI 来不及做出准确判断。
                                import os as _os_d
                                if _os_d.getenv("SCALP_AI_REVERSE_DISABLED", "true").lower() in ("1", "true", "yes"):
                                    logger.info(
                                        f"[ScalpAI复审] {_review_sym} AI建议={_action} → 已禁用(让TP/SL管理)"
                                    )
                                    return
                                if _fast_reduce:
                                    try:
                                        from backend.database.models import PaperPosition as _PP
                                        from backend.services.paper_trading_engine import paper_engine as _pe
                                        _pos_side = "long" if _review_side == "buy" else "short"
                                        _pos_row = _rdb.query(_PP).filter(
                                            _PP.account_id == _review_account_id,
                                            _PP.symbol == _review_sym,
                                            _PP.side == _pos_side,
                                            _PP.status == "open",
                                            _PP.trade_nature == "scalp",
                                        ).order_by(_PP.opened_at.desc()).first()
                                        _qty = None
                                        if _action == "reduce" and _pos_row:
                                            _qty = float(_pos_row.size or 0) * 0.5
                                        _pe.close_position(
                                            _rdb,
                                            account_id=_review_account_id,
                                            symbol=_review_sym,
                                            side=_pos_side,
                                            quantity=_qty,
                                            reason=f"scalp_fast_review:{_reason[:80]}",
                                            strategy_id=getattr(_pos_row, "strategy_id", None),
                                        )
                                        _rdb.commit()
                                        logger.warning(
                                            f"[ScalpAI复审] {_review_sym} 加速{_action} "
                                            f"pnl={_pnl_pct:.2%} lane={_review_lane_id}"
                                        )
                                    except Exception as _fr_err:
                                        logger.debug(f"[ScalpAI复审] fast reduce skip: {_fr_err}")
                                else:
                                    logger.warning(
                                        f"[ScalpAI复审] {_review_sym} AI建议={_action} "
                                        f"reason={_reason} → 将在持仓时限复审中处理"
                                    )
                            else:
                                logger.info(
                                    f"[ScalpAI复审] {_review_sym} AI建议={_action} reason={_reason}"
                                )
                        except Exception as _ai_err:
                            logger.debug(f"[ScalpAI复审] {_review_sym} AI调用失败: {_ai_err}")
                    finally:
                        _rdb.close()
                _rt = _thr.Thread(target=_async_scalp_review, daemon=True, name=f"scalp-review-{sym}")
                _rt.start()

            except Exception as _order_err:
                _db.rollback()
                logger.warning(f"[ScalpRouter独立] {sym} {side} 下单失败: {_order_err}", exc_info=True)

        if _scalp_block_counts or _scalp_opens_this_tick:
            _parts_b = " ".join(f"{k}={v}" for k, v in sorted(_scalp_block_counts.items()))
            logger.info(
                f"[ScalpRouter独立] tick#{tick} 拦截统计 opens={_scalp_opens_this_tick} {_parts_b}"
            )

        if session_row and _scalp_tick_results:
            try:
                _parts = [
                    f"{s} {d['factor_score']}分 {d['direction']}/{d['action']}"
                    for s, d in _scalp_tick_results
                ]
                _event_detail = f"⚡ [短线因子] {' | '.join(_parts[:10])}"
                # 快照落库用【全新短连接】重查会话行再写（2026-07-09 修复 scalp_tick 泄漏）：
                # 本方法的 _db 跨整轮扫描长期持有，全 hold（无成交、不 commit）的 tick 里它
                # 会一直挂着读事务，扫描偏慢时被服务端 90s idle_in_transaction 超时掐断，
                # 直接在其上 commit 就报 "Can't reconnect..." 且快照丢失。改用全新连接
                # （取用自带 pre_ping 校验）读最新 last_market_summary、写回并提交，与 _db 解耦。
                from sqlalchemy.orm.attributes import flag_modified

                from backend.database.connection import SessionLocal as _SnapSL
                _snap_db = _SnapSL()
                try:
                    _row2 = _snap_db.query(FullAutoSession).filter(
                        FullAutoSession.session_id == session_id
                    ).first()
                    if _row2 is not None:
                        _ms = dict(_row2.last_market_summary or {})
                        for _sf_sym, _sf in _scalp_tick_results:
                            _prev = dict(_ms.get(_sf_sym) or {}) if isinstance(_ms.get(_sf_sym), dict) else {}
                            _prev["scalp_factor"] = _sf
                            self._attach_scalp_advisory_for_ui(_sf_sym, _prev)
                            _ms[_sf_sym] = _prev
                        _row2.last_market_summary = _ms
                        flag_modified(_row2, "last_market_summary")
                        self._append_event(_row2, "scalp_scan", _event_detail)
                        self._safe_commit(_snap_db, "scalp_tick", session=_row2)
                finally:
                    _snap_db.close()
            except Exception as _sf_persist_err:
                logger.debug(f"[ScalpRouter独立] 短线因子快照落库跳过: {_sf_persist_err}")

    finally:
        _db.close()
