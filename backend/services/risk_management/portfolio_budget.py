"""
组合级风险预算 PortfolioBudget（v6 计划 阶段1 第4项）。

幻方顶层"CVaR 风险预算 + 自动熔断"在本项目的落地（按 crypto 波动率缩放，
不照搬 A 股 2% 回撤锁）。下单前最后一道检查，四条规则：

1. 单币种集中度上限：同币所有方向名义 / 权益 ≤ PB_MAX_SYMBOL_EXPOSURE_PCT
2. 组合日 VaR：持仓币 1d 收益按名义权重（含本单）加权 → 历史模拟 95% VaR，
   VaR / 权益 > PB_MAX_DAILY_VAR_PCT 拒开
3. 单策略回撤 3σ 熔断：策略历史已平仓 PnL 序列的当前回撤 > 3σ → 冻结该策略
4. 组合级冻结信号：硬指标触发 → 全局冻结 PB_FREEZE_COOLDOWN_SEC（可查可解冻）

接入：scalp_loop / midlong_helpers（下单前最后一道检查）。
热路径友好：收益序列 600s / 持仓 30s / 策略 PnL 300s TTL 缓存。
异常语义：paper fail-open（保样本）、live fail-closed（可配 PB_FAIL_CLOSED_LIVE）。
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_MIDLONG_NATURES = frozenset({"swing", "trend_follow", "position"})
_MIDLONG_TIERS = frozenset({"mid", "long"})

# [2026-08-06 修复] 策略回撤 σ 计算只看最近 N 天已平仓交易：
# ① 8 月前是废弃/迁移数据，全历史窗口会把 30.82σ 假熔断喂给短线策略（永久冻结）；
# ② 无时间窗查询每次扫全表（paper_positions 1271+ 行），实测出现 98s 挂起事务。
PB_DD_LOOKBACK_DAYS = 30

# [2026-08-07 修复] 冻结设计修正（用户硬性要求）：
# ① 冻结最小粒度 (account_id, strategy, symbol)：绝不允许因单一组合连续亏损冻结全部交易；
# ② 账户隔离：任何一级冻结不影响其他账户/其他策略/其他交易对；
# ③ 止血→修复→恢复：冻结即触发修复流水线（挖掘→回测→应用→解冻），冷却时间递减，绝不永久冻结。
PB_FREEZE_TOP_WORST = 3                 # 3σ 熔断时冻结该策略亏损最重的 symbol 数
PB_CONSEC_LOSS_LIMIT = 5                # 单 (账户,策略,交易对) 连续亏损熔断阈值
PB_ACCOUNT_FREEZE_COOLDOWN_SEC = 900.0  # 账户级冷却（15min，替代全局 3600s 硬冻结）


def _cfg_bool(name: str, default: bool = True) -> bool:
    v = os.getenv(name)
    if v is not None:
        return v.strip().lower() in ("1", "true", "yes", "on")
    try:
        from backend.config import settings
        return bool(getattr(settings, name, default))
    except Exception:
        return default


def _cfg_float(name: str, default: float) -> float:
    v = os.getenv(name)
    if v is not None:
        try:
            return float(v)
        except (TypeError, ValueError):
            pass
    try:
        from backend.config import settings
        return float(getattr(settings, name, default) or default)
    except Exception:
        return default


def _cfg_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is not None:
        try:
            return int(v)
        except (TypeError, ValueError):
            pass
    try:
        from backend.config import settings
        return int(getattr(settings, name, default) or default)
    except Exception:
        return default


@dataclass
class BudgetDecision:
    """组合预算决策结果。"""
    allowed: bool
    reasons: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    freeze_until: float = 0.0
    strategy: str = ""


def _pos_notional(pos: Dict[str, Any]) -> float:
    """单仓名义（size × 最新价，缺失时 margin × leverage 兜底）。"""
    try:
        size = float(pos.get("size") or pos.get("quantity") or 0)
        px = float(
            pos.get("mark_price")
            or pos.get("current_price")
            or pos.get("entry_price")
            or 0
        )
        if size > 0 and px > 0:
            return abs(size * px)
        margin = float(pos.get("margin") or 0)
        lev = float(pos.get("leverage") or 1) or 1
        if margin > 0:
            return abs(margin * lev)
    except Exception:
        return 0.0
    return 0.0


def _pos_dir(side: Any) -> str:
    s = str(side or "").lower()
    if s in ("long", "buy", "b"):
        return "long"
    if s in ("short", "sell", "s"):
        return "short"
    return ""


def _action_dir(action: str) -> str:
    a = (action or "").lower()
    if a in ("buy", "long"):
        return "long"
    if a in ("sell", "short"):
        return "short"
    return ""


def _is_strategy_pos(pos: Dict[str, Any], strategy: str) -> bool:
    nature = str(pos.get("trade_nature") or "").lower()
    tier = str(pos.get("timeframe_tier") or "").lower()
    if strategy == "midlong":
        return nature in _MIDLONG_NATURES or tier in _MIDLONG_TIERS
    if strategy == "scalp":
        return nature == "scalp" or tier == "short"
    # 默认：全部
    return True


def _signed_notional(pos: Dict[str, Any]) -> float:
    d = _pos_dir(pos.get("side"))
    n = _pos_notional(pos)
    if d == "long":
        return n
    if d == "short":
        return -n
    return 0.0


class PortfolioBudget:
    """组合级风险预算（单例：portfolio_budget）。"""

    def __init__(self) -> None:
        # 四级冻结（粒度由细到粗，全部按账户隔离）：
        # key: (account_id, strategy, symbol) → 默认最小粒度（止血点）
        # strategy: (account_id, strategy)；account: account_id；global: 仅运维手动
        self._key_frozen_until: Dict[Tuple[int, str, str], float] = {}
        self._strategy_frozen_until: Dict[Tuple[int, str], float] = {}
        self._account_frozen_until: Dict[int, float] = {}
        self._frozen_until: float = 0.0
        self._trigger_count: Dict[Any, int] = {}   # 触发次数（冷却递减依据）
        self._repair_locks: Dict[Any, bool] = {}   # 修复流水线去重
        self._repair_running: int = 0              # 修复流水线在跑数（全局并发闸）
        # [2026-08-08 P2-1] 修复失败后冷却，避免 depth/promote 失败时空转占锁
        self._repair_fail_until: Dict[Any, float] = {}
        self._cache: Dict[str, Any] = {}
        self._lock = __import__("threading").Lock()
        self._last_decision: Optional[BudgetDecision] = None

    # ── 公共入口 ──────────────────────────────────────────────

    def evaluate_open(
        self,
        *,
        symbol: str,
        action: str,
        notional_usd: float,
        equity: float,
        strategy: str = "scalp",
        mode: str = "paper",
        db=None,
        account_id: int = 0,
        positions: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> BudgetDecision:
        """下单前最后一道组合预算检查。

        positions: 全账户 open 持仓 dict 列表（缺省时模块内按 account 拉取并缓存）。
        """
        strategy = (strategy or "scalp").strip().lower()
        mode = (mode or "paper").strip().lower()
        sym = str(symbol or "").upper()
        reasons: List[str] = []
        metrics: Dict[str, Any] = {"strategy": strategy}

        if not _cfg_bool("PB_ENABLED", True):
            return BudgetDecision(True, [], metrics, strategy=strategy)

        try:
            # ── 0. 冻结信号（四级粒度：key→策略→账户→全局；只拦命中者，其余照常）──
            now = time.time()
            key_f = self._key_frozen_until.get((account_id, strategy, sym), 0.0)
            strat_f = self._strategy_frozen_until.get((account_id, strategy), 0.0)
            acct_f = self._account_frozen_until.get(account_id, 0.0)
            frozen_until = max(key_f, strat_f, acct_f, self._frozen_until)
            # [2026-08-07 诊断] 冻结表非空但未命中：节流打印当前表内容，
            # 便于发现 key 类型/格式错位（曾现冻结写入后 8 分钟再次触发）
            if frozen_until <= now and (self._key_frozen_until or self._strategy_frozen_until
                                        or self._account_frozen_until):
                _nz = time.time()
                if _nz - getattr(self, "_last_fz_diag", 0.0) > 30.0:
                    self._last_fz_diag = _nz
                    logger.info(
                        "[PortfolioBudget] 冻结检查未命中 acct=%r strat=%r sym=%r "
                        "key_frozen=%s strategy_frozen=%s account_frozen=%s",
                        account_id, strategy, sym,
                        {repr(k): int(v - _nz) for k, v in self._key_frozen_until.items() if v > _nz},
                        {repr(k): int(v - _nz) for k, v in self._strategy_frozen_until.items() if v > _nz},
                        {k: int(v - _nz) for k, v in self._account_frozen_until.items() if v > _nz},
                    )
            if frozen_until > now:
                if key_f > now:
                    scope = f"key({sym})"
                elif strat_f > now:
                    scope = f"strategy({strategy})"
                elif acct_f > now:
                    # 短线 daily_var 等曾用账户级冻结，会误伤中长线；
                    # 三层独立：中长线忽略账户级冻结，只认自身 strategy/key。
                    if strategy == "midlong" and key_f <= now and strat_f <= now:
                        metrics["ignored_account_freeze"] = True
                        scope = ""
                    else:
                        scope = f"account({account_id})"
                else:
                    scope = "global"
                if scope:
                    reasons.append(f"portfolio_frozen_until={int(frozen_until - now)}s scope={scope}")
                    metrics["frozen"] = True
                    metrics["freeze_scope"] = scope
                    return BudgetDecision(False, reasons, metrics, freeze_until=frozen_until, strategy=strategy)

            # ── 1. 单币种集中度 ──
            # 口径是「名义/权益」。10x 杠杆下名义=保证金×10，所以 0.30 的名义上限
            # 只允许约 3% 保证金——与短线抬高后的仓位（6%~15% 保证金）永久冲突，
            # 会触发冻结+修复冷却把短线锁死。短线单独放宽，且超限只拒单不冻结。
            # 中长线与短线常同币并存：集中度含全账户名义，须单独放宽，否则探针永远被
            # concentration 108%>80% 拦死；同样只拒单不冻结。
            pos_list = self._load_positions(db, account_id, positions)
            conc = self._concentration_pct(pos_list, sym, notional_usd, equity)
            metrics["concentration_pct"] = conc
            max_conc = _cfg_float("PB_MAX_SYMBOL_EXPOSURE_PCT", 0.30)
            _strat = str(strategy).lower()
            if _strat == "scalp":
                max_conc = _cfg_float(
                    "PB_SCALP_MAX_SYMBOL_EXPOSURE_PCT",
                    max(float(max_conc), 1.50),
                )
            elif _strat == "midlong":
                max_conc = _cfg_float(
                    "PB_MIDLONG_MAX_SYMBOL_EXPOSURE_PCT",
                    max(float(max_conc), 2.0),
                )
            if equity > 0 and conc > max_conc:
                reasons.append(f"concentration {conc:.0%}>{max_conc:.0%} ({sym})")
                if _strat in ("scalp", "midlong"):
                    # 拒单即可，勿冻结（避免抬仓/探针后每单都冻 30 分钟）
                    return BudgetDecision(False, reasons, metrics, strategy=strategy)
                self._freeze_via_coordinator(account_id, strategy, sym, reasons[-1])
                return BudgetDecision(False, reasons, metrics, strategy=strategy)

            # ── 2. 组合日 VaR（95% 历史模拟，含本单） ──
            var_ratio = self._daily_var_ratio(pos_list, sym, action, notional_usd, equity)
            metrics["var_95_pct"] = var_ratio
            max_var = _cfg_float("PB_MAX_DAILY_VAR_PCT", 0.05)
            if var_ratio is not None and var_ratio > max_var:
                reasons.append(f"daily_var {var_ratio:.1%}>{max_var:.1%}")
                # [2026-08-15 整改] 设计红线：不能因一个交易对影响全局。
                # daily_var 超限只冻结「触发本单的交易对」(key 级)，绝不冻结整个策略/账户；
                # 修复链（快速因子进化）随后接管。此前 scope="strategy" 会把短线全部币种
                # 一起冻结 900s——这是"全部冻结"的直接来源之一。
                self._freeze_via_coordinator(
                    account_id, strategy, sym, reasons[-1],
                    cooldown=_cfg_float("PB_ACCOUNT_FREEZE_COOLDOWN_SEC",
                                        PB_ACCOUNT_FREEZE_COOLDOWN_SEC),
                )
                return BudgetDecision(False, reasons, metrics, strategy=strategy)

            # ── 3. 单策略回撤 3σ 熔断 ──
            dd_sigma = self._strategy_drawdown_sigma(strategy, db, account_id)
            metrics["drawdown_sigma"] = dd_sigma
            sigma_cap = _cfg_float("PB_STRATEGY_DRAWDOWN_SIGMA", 3.0)
            # 中长线历史回撤序列波动大（纸盘探针期常 >3σ）；单独放宽避免永久熔断。
            if _strat == "midlong":
                sigma_cap = _cfg_float(
                    "PB_MIDLONG_DRAWDOWN_SIGMA",
                    max(float(sigma_cap), 10.0),
                )
            if dd_sigma is not None and dd_sigma > sigma_cap:
                reasons.append(f"{strategy} drawdown={dd_sigma:.2f}σ>{sigma_cap:.0f}σ")
                # 最小粒度：只冻结该策略下亏损最重的 symbol（止血），
                # 非亏损源的当前 symbol 放行继续后续规则，其余 symbol 不受影响
                worst = self._worst_symbols(
                    strategy, db, account_id,
                    top_k=_cfg_int("PB_FREEZE_TOP_WORST", PB_FREEZE_TOP_WORST),
                )
                metrics["drawdown_worst"] = worst
                if worst:
                    for wsym in worst:
                        self._freeze_via_coordinator(account_id, strategy, wsym,
                                                     why=f"drawdown {dd_sigma:.2f}σ")
                    if sym in worst:
                        return BudgetDecision(False, reasons, metrics, strategy=strategy)
                    # 本单非亏损源：继续后续规则（连亏/集中度等）
                else:
                    self._freeze_via_coordinator(account_id, strategy, sym,
                                                 why=f"drawdown {dd_sigma:.2f}σ")
                    return BudgetDecision(False, reasons, metrics, strategy=strategy)

            # ── 4. 单 (账户,策略,交易对) 连续亏损熔断（最小粒度，止血不杀死）──
            cons_losses = self._consecutive_losses(db, account_id, strategy, sym)
            metrics["consecutive_losses"] = cons_losses
            cons_cap = _cfg_int("PB_CONSEC_LOSS_LIMIT", PB_CONSEC_LOSS_LIMIT)
            if cons_losses is not None and cons_losses >= cons_cap:
                reasons.append(f"{sym} 连续亏损 {cons_losses} 笔>={cons_cap} 笔")
                self._freeze_via_coordinator(account_id, strategy, sym, reasons[-1])
                return BudgetDecision(False, reasons, metrics, strategy=strategy)

            reasons.append("ok")
            return BudgetDecision(True, reasons, metrics, strategy=strategy)
        except Exception as e:
            # 异常语义：paper fail-open 保样本；live fail-closed 保资金（可配）
            live_fail_closed = _cfg_bool("PB_FAIL_CLOSED_LIVE", True)
            if mode == "live" and live_fail_closed:
                reasons.append(f"pb_error_live_fail_closed: {str(e)[:120]}")
                logger.warning("[PortfolioBudget] live fail-closed: %s", e)
                return BudgetDecision(False, reasons, metrics, strategy=strategy)
            logger.debug("[PortfolioBudget] %s 异常(fail-open): %s", strategy, e)
            metrics["pb_error"] = str(e)[:200]
            return BudgetDecision(True, ["pb_error_fail_open"], metrics, strategy=strategy)

    # ── 冻结信号 ─────────────────────────────────────────────

    def _freeze_via_coordinator(
        self,
        account_id: int,
        strategy: str,
        symbol: str,
        why: str,
        *,
        cooldown: Optional[float] = None,
    ) -> None:
        """统一冻结出口（2026-08-15 整改）：
        本类内所有自动冻结都必须经此方法 → FreezeCoordinator（单一入口 + 台账）。
        严禁绕过直接调 self._freeze。"""
        try:
            from backend.services.risk_management.freeze_coordinator import freeze as _fz
            _fz(account_id, strategy, symbol, why=why, cooldown=cooldown)
        except Exception as _e:
            # 台账故障不允许破坏风控执行：降级回底层 _freeze（fail-closed 保持止血）
            logger.error("[PortfolioBudget] coordinator 调用失败，降级底层冻结: %s", _e)
            self._freeze(account_id, strategy, symbol, why=why, scope="key", cooldown=cooldown)

    def _freeze(
        self,
        account_id: int,
        strategy: str,
        symbol: str,
        why: str,
        *,
        scope: str = "key",
        cooldown: Optional[float] = None,
    ) -> None:
        """冻结（止血）：scope=key 最小粒度 (账户,策略,交易对) | account 账户级 | strategy 策略级。
        冷却时间随触发次数递减（3600→1800→900→450s），绝不自动全局冻结；
        触发即启动修复流水线（止血→修复→恢复），不做永久冻结。"""
        # [2026-08-08 P2-1] 短线默认更短冷却（900s），避免 opens 长期为 0
        if cooldown is not None:
            base = cooldown
        elif str(strategy).lower() == "scalp":
            base = _cfg_float("PB_SCALP_FREEZE_COOLDOWN_SEC", 900.0)
        else:
            base = _cfg_float("PB_FREEZE_COOLDOWN_SEC", 3600.0)
        sym = str(symbol or "").upper()
        key = (account_id, strategy, sym)
        cnt_key = key if scope == "key" else (account_id, strategy) if scope == "strategy" else (account_id, "__acct__")
        with self._lock:
            # [2026-08-07 诊断+防护] 冻结期内重复触发：正常路径下第 0 步冻结检查应
            # 拦截一切命中评估，走到这里说明评估链有漏网（曾现 ZEC 冻结 3600s 后
            # 8 分钟再次触发 n=2）。此处不重复计次/不刷新冻结，并留日志定位漏洞。
            if scope == "key":
                _exist = self._key_frozen_until.get(key, 0.0)
            elif scope == "strategy":
                _exist = self._strategy_frozen_until.get((account_id, strategy), 0.0)
            else:
                _exist = self._account_frozen_until.get(account_id, 0.0)
            if _exist > time.time():
                logger.warning(
                    "[PortfolioBudget] %s %s %s 冻结期内重复触发(忽略,防循环): "
                    "现有冻结剩余%ds key=%r",
                    account_id, strategy, sym, int(_exist - time.time()), key,
                )
                return
            # [2026-08-16 用户指令] 「亏一笔就冻结」机制整体删除。
            # paper 阶段亏损=训练数据（用户原话：前期就是要亏钱亏出数据）。
            # 因子/策略的处置走累计口径：累计亏损超限 → 下架 + 重挖 + 替代因子
            # 继续交易（修复流水线承担），绝不写冻结时间戳阻断其它交易对。
            if not _cfg_bool("PB_FREEZE_ENABLED", True):
                logger.info(
                    "[PortfolioBudget] 冻结已禁用(PB_FREEZE_ENABLED=false)，"
                    "仅启动修复流水线(下架/重挖/替代): %s %s %s",
                    account_id, strategy, sym,
                )
                self._spawn_repair(account_id, strategy, sym, why, scope)
                return
            n = self._trigger_count.get(cnt_key, 0) + 1
            self._trigger_count[cnt_key] = n
            decay = 2.0 ** min(n - 1, 3)          # 3600→1800→900→450
            # 短线最低冷却 180s（原 450），加快恢复交易
            _min_cd = 180.0 if str(strategy).lower() == "scalp" else 450.0
            cooldown_eff = max(_min_cd, base / decay)
            until = time.time() + cooldown_eff
            if scope == "account":
                self._account_frozen_until[account_id] = max(
                    self._account_frozen_until.get(account_id, 0.0), until)
            elif scope == "strategy":
                self._strategy_frozen_until[(account_id, strategy)] = max(
                    self._strategy_frozen_until.get((account_id, strategy), 0.0), until)
            else:
                self._key_frozen_until[key] = max(self._key_frozen_until.get(key, 0.0), until)
        logger.warning(
            "[PortfolioBudget] acct=%s %s %s 触发%s冻结 %ds(第%d次): %s",
            account_id, strategy, sym, scope, int(cooldown_eff), n, why,
        )
        # 止血后立即进入修复流水线（后台异步，不阻塞其他交易）
        self._spawn_repair(account_id, strategy, sym, why, scope)

    def _spawn_repair(self, account_id: int, strategy: str, symbol: str, why: str, scope: str) -> None:
        """启动修复流水线（后台线程）：记录进化事件 → 触发该组合快速因子挖掘/回测/应用
        → 流水线完成即自动解冻恢复（不等被动冷却）；任何异常/超时兜底解冻，绝不永久冻结。

        资源保护（2026-08-07 加固）：
        - 全局并发闸 PB_REPAIR_MAX_CONCURRENT（默认 1）：多个组合同时冻结时串行修复，
          防止 N 条完整进化闭环并发把进程/DB 连接池打爆（实测 3 条并发 → 32 loky
          worker × 每 worker 连接 → PG max_connections 打满，全库写入被拒）；
        - 并行度钳制 PB_REPAIR_MAX_WORKERS（默认 4）：GP/MCTS 默认按 CPU 核起 loky 池，
          修复轮限 4 worker，不冲击连接池；
        - 超时兜底 PB_REPAIR_TIMEOUT_SEC（默认 600）：流水线超限立即解冻该组合，
          止血不等于永久冻结（挖掘线程继续跑完自然退出，资源已受限可控）。"""
        if scope != "key":
            return  # 账户/策略级是短时冷却窗口本身，不做单组合修复
        key = (account_id, strategy, symbol)
        max_conc = max(1, _cfg_int("PB_REPAIR_MAX_CONCURRENT", 1))
        with self._lock:
            fail_until = float(self._repair_fail_until.get(key, 0.0) or 0.0)
            if fail_until > time.time():
                logger.info(
                    "[PortfolioBudget] 修复失败冷却中，跳过 %s %s 剩余%ds",
                    strategy, symbol, int(fail_until - time.time()),
                )
                return
            if self._repair_locks.get(key):
                return
            if self._repair_running >= max_conc:
                logger.warning(
                    "[PortfolioBudget] 修复流水线繁忙(%d/%d)，%s %s 跳过流水线(冷却到期自动恢复)",
                    self._repair_running, max_conc, strategy, symbol,
                )
                return  # 不阻塞交易：该组合等冷却自然恢复，其余组合不受影响
            self._repair_locks[key] = True
            self._repair_running += 1
        threading = __import__("threading")
        # 短线修复超时更短：深度不足会立即返回，无需占满 600s
        _default_to = 180.0 if str(strategy).lower() == "scalp" else 600.0
        timeout_sec = float(_cfg_float("PB_REPAIR_TIMEOUT_SEC", _default_to))

        def _unfreeze_safe() -> None:
            # 超时/完成共用解冻出口（幂等）
            self.manual_unfreeze(account_id=account_id, strategy=strategy, symbol=symbol)

        def _worker() -> None:
            timer = None
            # 修复链默认开启（原设计：冻结交易对 → 快速因子挖掘/回测/应用 → 完成即解冻）。
            # 历史版本因 quick 卡顿把默认关掉（spawn_evo=0），导致冻结只有冷却没有修复——
            # 2026-08-15 整改恢复默认 1；如需临时关闭显式设 PB_REPAIR_SPAWN_EVO=0。
            spawn_evo = os.getenv("PB_REPAIR_SPAWN_EVO", "1").strip().lower() in (
                "1", "true", "yes", "on",
            )
            if not spawn_evo:
                try:
                    from backend.services.evolution.factor_evolution_loop import _log_evolution
                    _log_evolution(
                        f"pb_freeze:{symbol}", "pb_freeze",
                        account_id=account_id, strategy=strategy, symbol=symbol,
                        reason=f"{str(why)[:160]} | spawn_evo=0 skip quick",
                    )
                except Exception:  # noqa: BLE001
                    pass
                logger.info(
                    "[PortfolioBudget] 冻结仅冷却，跳过 quick 进化 %s %s (PB_REPAIR_SPAWN_EVO=0)",
                    strategy, symbol,
                )
                with self._lock:
                    self._repair_running = max(0, self._repair_running - 1)
                    self._repair_locks.pop(key, None)
                return
            try:
                # 快速轮：限定单 symbol 挖掘/回测/应用（走既有进化链路）
                period = "5m" if strategy == "scalp" else "4h"
                env_backup: Dict[str, Optional[str]] = {}
                for _ev in ("FACTOR_GP_MAX_WORKERS", "FACTOR_MCTS_MAX_WORKERS"):
                    env_backup[_ev] = os.environ.get(_ev)
                try:
                    _mw = str(max(1, _cfg_int("PB_REPAIR_MAX_WORKERS", 4)))
                    os.environ["FACTOR_GP_MAX_WORKERS"] = _mw
                    os.environ["FACTOR_MCTS_MAX_WORKERS"] = _mw
                    from backend.services.evolution.factor_evolution_loop import (
                        _log_evolution,
                        run_factor_evolution_loop,
                    )
                    _log_evolution(
                        f"pb_freeze:{symbol}", "pb_freeze",  # phase 列 VARCHAR(20)，"portfolio_budget_freeze" 23 字符超长会 INSERT 失败
                        account_id=account_id, strategy=strategy, symbol=symbol,
                        reason=str(why)[:200],
                    )
                    # 超时兜底：流水线超限也立即解冻（止血不等于永久冻结）
                    timer = threading.Timer(timeout_sec, _unfreeze_safe)
                    timer.daemon = True
                    timer.start()
                    # [2026-08-07 快速修复] quick=True：压缩 GP/MCTS 挖掘规模 + 跳过
                    # WFO 双门禁（约省 9min），目标止血后 ~10min 内完成补挖替换，
                    # 避免 4 个 loky worker 长时间常驻（此前完整链路实测 3h+）。
                    # [2026-08-08 P0-3] 深度不足/晋升拒绝时进化立即返回 error，
                    # 此处记录后解冻，禁止把失败当成"修好了"。
                    from backend.services.evolution import evo_runtime as _evo_rt
                    if _evo_rt.is_running():
                        logger.warning(
                            "[PortfolioBudget] 跳过修复：因子进化已在跑 %s",
                            _evo_rt.snapshot(),
                        )
                        report = {"error": "already_running", "runtime": _evo_rt.snapshot()}
                    else:
                        report = run_factor_evolution_loop(
                            symbols=[symbol], period=period, quick=True,
                        )
                    if isinstance(report, dict) and report.get("error"):
                        logger.warning(
                            "[PortfolioBudget] 修复快失败 %s %s: error=%s elapsed=%s rejects=%s",
                            strategy, symbol, report.get("error"),
                            report.get("elapsed_sec"),
                            len(report.get("promote_rejects") or []),
                        )
                        # P2-1：失败后进入修复冷却，防止连环占用并发闸
                        _fail_cd = float(_cfg_float(
                            "PB_REPAIR_FAIL_COOLDOWN_SEC",
                            1800.0 if str(strategy).lower() == "scalp" else 3600.0,
                        ))
                        with self._lock:
                            self._repair_fail_until[key] = time.time() + _fail_cd
                    else:
                        logger.info(
                            "[PortfolioBudget] 修复完成 %s %s: promoted=%s active=%s elapsed=%s",
                            strategy, symbol,
                            (report or {}).get("promoted"),
                            (report or {}).get("active_total"),
                            (report or {}).get("elapsed_sec"),
                        )
                        with self._lock:
                            self._repair_fail_until.pop(key, None)
                finally:
                    for _ev, _old in env_backup.items():
                        if _old is None:
                            os.environ.pop(_ev, None)
                        else:
                            os.environ[_ev] = _old
            except Exception as e:
                logger.warning("[PortfolioBudget] 修复流水线异常(兜底解冻): %s", e)
            finally:
                if timer is not None:
                    timer.cancel()
                # 兜底：无论流水线成败，完成即解冻（宁可回到交易+继续学习，不可永久冻结）
                _unfreeze_safe()
                with self._lock:
                    self._repair_running = max(0, self._repair_running - 1)
                    self._repair_locks.pop(key, None)

        threading.Thread(target=_worker, daemon=True,
                         name=f"pb-repair-a{account_id}-{symbol}").start()

    def manual_unfreeze(
        self,
        account_id: Optional[int] = None,
        strategy: Optional[str] = None,
        symbol: Optional[str] = None,
    ) -> None:
        """手动解除冻结。全 None=全部；account_id=该账户全部级别；
        +strategy=该策略级+该策略下全部 key；+symbol=精确 key。"""
        # [2026-08-07 诊断] 记录调用参数（含类型），定位异常解冻调用方
        logger.info(
            "[PortfolioBudget] manual_unfreeze(acct=%r, strat=%r, sym=%r)",
            account_id, strategy, symbol,
        )
        with self._lock:
            if account_id is None:
                self._frozen_until = 0.0
                self._account_frozen_until.clear()
                self._strategy_frozen_until.clear()
                self._key_frozen_until.clear()
                return
            self._account_frozen_until.pop(account_id, None)
            if strategy is None:
                for k in [k for k in self._strategy_frozen_until if k[0] == account_id]:
                    self._strategy_frozen_until.pop(k, None)
                for k in [k for k in self._key_frozen_until if k[0] == account_id]:
                    self._key_frozen_until.pop(k, None)
                return
            self._strategy_frozen_until.pop((account_id, strategy), None)
            if symbol is None:
                for k in [k for k in self._key_frozen_until
                          if k[0] == account_id and k[1] == strategy]:
                    self._key_frozen_until.pop(k, None)
                return
            self._key_frozen_until.pop((account_id, strategy, str(symbol).upper()), None)

    def status(self) -> Dict[str, Any]:
        """供监控看板/前端：预算状态与最近决策（按账户分级冻结清单）。"""
        now = time.time()
        return {
            "enabled": _cfg_bool("PB_ENABLED", True),
            "global_frozen": self._frozen_until > now,
            "global_frozen_until": self._frozen_until,
            "account_frozen": {k: v for k, v in self._account_frozen_until.items() if v > now},
            "strategy_frozen": {k: v for k, v in self._strategy_frozen_until.items() if v > now},
            "key_frozen": {k: v for k, v in self._key_frozen_until.items() if v > now},
            "trigger_count": dict(self._trigger_count),
            "last_decision": self._last_decision,
        }

    # ── 持仓与数据 ───────────────────────────────────────────

    def _load_positions(
        self, db, account_id: int,
        positions: Optional[Sequence[Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        if positions is not None:
            return [p for p in positions if isinstance(p, dict)]
        if not db or not account_id:
            return []
        cache_key = f"pos:{account_id}"
        hit = self._cache.get(cache_key)
        if hit and time.time() - hit[0] < _cfg_float("PB_POS_CACHE_TTL_SEC", 30.0):
            return hit[1]
        try:
            from backend.services.paper_trading_engine import paper_engine
            rows = paper_engine.get_positions(db, account_id, status="open") or []
            out = [p for p in rows if isinstance(p, dict)]
        except Exception:
            out = []
        self._cache[cache_key] = (time.time(), out)
        return out

    @staticmethod
    def _concentration_pct(
        positions: List[Dict[str, Any]], symbol: str, add_notional: float, equity: float,
    ) -> float:
        """同币所有方向名义（含本单）/ 权益。"""
        sym = (symbol or "").upper()
        total = abs(float(add_notional or 0))
        for p in positions:
            if (str(p.get("symbol") or "").upper()) != sym:
                continue
            total += _pos_notional(p)
        if equity <= 0:
            return 0.0
        return total / equity

    def _daily_var_ratio(
        self,
        positions: List[Dict[str, Any]],
        symbol: str,
        action: str,
        add_notional: float,
        equity: float,
    ) -> Optional[float]:
        """历史模拟 95% 日 VaR / 权益。数据不足返回 None（该项 fail-open）。"""
        if equity <= 0:
            return None
        lookback = max(30, _cfg_int("PB_VAR_LOOKBACK_DAYS", 90))
        conf = float(_cfg_float("PB_VAR_CONFIDENCE", 0.95))
        alpha = (1.0 - conf) * 100.0  # 5.0

        # 组合名义权重：现有持仓（按 symbol 汇总净名义）+ 本单
        sym_notional: Dict[str, float] = {}
        sym_dir: Dict[str, str] = {}
        for p in positions:
            s = str(p.get("symbol") or "").upper()
            d = _pos_dir(p.get("side"))
            if not d:
                continue
            n = _pos_notional(p)
            cur = sym_notional.get(s, 0.0)
            sym_notional[s] = cur + (n if d == "long" else -n)
            sym_dir[s] = d
        add_dir = _action_dir(action)
        if add_dir:
            cur = sym_notional.get(symbol, 0.0)
            sym_notional[symbol] = cur + (abs(float(add_notional or 0)) if add_dir == "long" else -abs(float(add_notional or 0)))
            sym_dir[symbol] = add_dir

        total_notional = sum(abs(v) for v in sym_notional.values())
        if total_notional <= 0:
            return None

        # 各币 1d 收益序列（TTL 缓存），方向翻转：short 用 -r
        rets: List[np.ndarray] = []
        weights: List[float] = []
        for s, signed_n in sym_notional.items():
            r = self._daily_returns(s)
            if r is None or len(r) < 30:
                continue
            d = sym_dir.get(s, "long")
            if d == "short":
                r = -r
            rets.append(r)
            weights.append(abs(signed_n) / total_notional)

        if not rets:
            return None
        # 组合日收益 = Σ w_i × r_i（历史模拟：同期对齐取最短长度）
        n = min(len(r) for r in rets)
        combo = sum(w * r[-n:] for w, r in zip(weights, rets))
        var = float(-np.percentile(combo, alpha))
        if not np.isfinite(var):
            return None
        # 组合日最大损失比例（预算比较对象为权益比例 PB_MAX_DAILY_VAR_PCT）
        return max(0.0, var)

    def _daily_returns(self, symbol: str) -> Optional[np.ndarray]:
        """单币 1d 收益序列（缓存 600s）。"""
        sym = (symbol or "").upper()
        cache_key = f"ret1d:{sym}"
        hit = self._cache.get(cache_key)
        if hit and time.time() - hit[0] < _cfg_float("PB_KL_CACHE_TTL_SEC", 600.0):
            return hit[1]
        try:
            from backend.services.data_center import data_center
            result = data_center.get_klines(sym, "1d", count=_cfg_int("PB_VAR_LOOKBACK_DAYS", 90))
            df = result.to_dataframe()
            if df is None or len(df) < 30 or "close" not in getattr(df, "columns", []):
                return None
            close = df["close"].values.astype(float)
            r = close[1:] / close[:-1] - 1.0
            r = r[np.isfinite(r)]
            if len(r) < 30:
                return None
            self._cache[cache_key] = (time.time(), r)
            return r
        except Exception as e:
            logger.debug("[PortfolioBudget] %s 1d 收益获取失败: %s", sym, e)
            return None

    def _strategy_drawdown_sigma(
        self, strategy: str, db, account_id: int,
    ) -> Optional[float]:
        """策略历史已平仓 PnL 序列：当前回撤 / 序列 σ。数据不足返回 None。"""
        if not db or not account_id:
            return None
        cache_key = f"dd:{strategy}:{account_id}"
        hit = self._cache.get(cache_key)
        if hit and time.time() - hit[0] < _cfg_float("PB_DD_CACHE_TTL_SEC", 300.0):
            return hit[1]
        min_trades = _cfg_int("PB_MIN_TRADES_FOR_CIRCUIT", 10)
        try:
            from backend.database.models import PaperPosition
            import datetime as _dt
            cutoff = _dt.datetime.now() - _dt.timedelta(
                days=_cfg_int("PB_DD_LOOKBACK_DAYS", PB_DD_LOOKBACK_DAYS)
            )
            rows = (
                db.query(PaperPosition)
                .filter(
                    PaperPosition.account_id == account_id,
                    PaperPosition.status == "closed",
                    # [2026-08-06 修复] 时间窗：废弃数据（8 月前）不参与 σ 计算；
                    # closed_at 为空的老记录一并排除（无法确认时间的废弃数据）。
                    PaperPosition.closed_at >= cutoff,
                )
                .order_by(PaperPosition.closed_at.asc())
                .all()
            )
        except Exception as e:
            logger.debug("[PortfolioBudget] %s 历史交易查询失败: %s", strategy, e)
            return None
        pnls = []
        for r in rows:
            try:
                if not _is_strategy_pos(
                    {"trade_nature": r.trade_nature, "timeframe_tier": r.timeframe_tier},
                    strategy,
                ):
                    continue
                # [2026-08-07 修复] pnl 取差价口径：closed 仓的 unrealized_pnl 残留值
                # 语义不可靠（实测 923/923 非 0 且把盈利序列算成 -12.85），
                # 用 (close_price-entry_price)*size*方向 + 部分已实现，才是真实已实现口径。
                d = 1 if str(r.side or "").lower() in ("long", "buy") else -1
                if r.close_price is not None and r.entry_price:
                    pnl = (float(r.close_price) - float(r.entry_price)) * float(r.size or 0) * d
                else:
                    pnl = float(r.unrealized_pnl or 0)
                pnl = pnl + float(r.partial_realized_pnl or 0)
                if np.isfinite(pnl):
                    pnls.append(pnl)
            except Exception:
                continue
        if len(pnls) < min_trades:
            return None
        arr = np.asarray(pnls, dtype=float)
        sigma = float(arr.std())
        if sigma <= 0:
            return None
        equity_curve = np.cumsum(arr)
        peak = float(np.maximum.accumulate(equity_curve)[-1])
        current_dd = float(peak - equity_curve[-1])
        dd_sigma = current_dd / sigma
        self._cache[cache_key] = (time.time(), dd_sigma)
        return dd_sigma

    def _worst_symbols(
        self, strategy: str, db, account_id: int, top_k: int = 3,
    ) -> List[str]:
        """该策略 30 天 closed 按 symbol 汇总 PnL（差价口径），返回亏损最重 top_k（仅亏损者）。"""
        try:
            from backend.database.models import PaperPosition
            import datetime as _dt
            cutoff = _dt.datetime.now() - _dt.timedelta(
                days=_cfg_int("PB_DD_LOOKBACK_DAYS", PB_DD_LOOKBACK_DAYS)
            )
            rows = (
                db.query(PaperPosition)
                .filter(
                    PaperPosition.account_id == account_id,
                    PaperPosition.status == "closed",
                    PaperPosition.closed_at >= cutoff,
                )
                .all()
            )
        except Exception:
            return []
        by_sym: Dict[str, float] = {}
        for r in rows:
            if not _is_strategy_pos(
                {"trade_nature": r.trade_nature, "timeframe_tier": r.timeframe_tier},
                strategy,
            ):
                continue
            sym = str(r.symbol or "").upper()
            d = 1 if str(r.side or "").lower() in ("long", "buy") else -1
            try:
                if r.close_price is not None and r.entry_price:
                    pnl = (float(r.close_price) - float(r.entry_price)) * float(r.size or 0) * d
                else:
                    pnl = float(r.unrealized_pnl or 0)
                pnl += float(r.partial_realized_pnl or 0)
                by_sym[sym] = by_sym.get(sym, 0.0) + pnl
            except Exception:
                continue
        worst = sorted(by_sym.items(), key=lambda kv: kv[1])[:top_k]
        return [s for s, p in worst if p < 0]

    def _consecutive_losses(
        self, db, account_id: int, strategy: str, symbol: str,
    ) -> Optional[int]:
        """该 (账户,策略,交易对) 最近连续亏损笔数（差价口径）。
        无历史交易/异常返回 None（fail-open）。缓存 120s 防热路径拖累。"""
        if not db or not account_id:
            return None
        cache_key = f"cl:{account_id}:{strategy}:{str(symbol or '').upper()}"
        hit = self._cache.get(cache_key)
        if hit and time.time() - hit[0] < _cfg_float("PB_CONSEC_CACHE_TTL_SEC", 120.0):
            return hit[1]
        try:
            from backend.database.models import PaperPosition
            rows = (
                db.query(PaperPosition)
                .filter(
                    PaperPosition.account_id == account_id,
                    PaperPosition.status == "closed",
                    PaperPosition.symbol == str(symbol or "").upper(),
                    PaperPosition.closed_at.isnot(None),
                )
                .order_by(PaperPosition.closed_at.desc())
                .limit(10)
                .all()
            )
        except Exception:
            return None
        if not rows:
            return None
        n = 0
        for r in rows:
            if not _is_strategy_pos(
                {"trade_nature": r.trade_nature, "timeframe_tier": r.timeframe_tier},
                strategy,
            ):
                continue
            d = 1 if str(r.side or "").lower() in ("long", "buy") else -1
            try:
                if r.close_price is not None and r.entry_price:
                    pnl = (float(r.close_price) - float(r.entry_price)) * float(r.size or 0) * d
                else:
                    pnl = float(r.unrealized_pnl or 0)
                pnl += float(r.partial_realized_pnl or 0)
            except Exception:
                break
            if pnl < 0:
                n += 1
            else:
                break
        result = n
        self._cache[cache_key] = (time.time(), result)
        return result


# 单例
portfolio_budget = PortfolioBudget()
