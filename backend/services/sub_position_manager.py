"""
虚拟子仓位管理器 — AI 决策与交易引擎之间的审核层。

核心职责:
1. review_open   审核开仓: 子仓数量、方向一致性、手续费门卫、nature 唯一性
2. review_reduce 审核减仓: 冷却时间、最小利润、比例上限
3. review_flip   审核方向翻转: 多周期确认
4. reconcile     定期对账: 子仓合计 == 交易所实际仓位
5. get_sub_positions 获取指定 symbol 的所有活跃子仓

设计原则:
- audit_only=True 时只记日志不拦截 (安全上线用)
- trade_nature=None 的历史仓位按 "swing" 默认处理
"""

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════
#  每种 trade_nature 的管理规则
# ═══════════════════════════════════════════════════════

NATURE_RULES: Dict[str, Dict[str, Any]] = {
    "position": {
        "label": "长线持仓",
        "expected_hold_hours": 168,
        "reduce_cooldown_hours": 48,
        "min_profit_for_reduce_pct": 0.08,
        "sl_atr_mult": 4.0,
        "tp_stages": [
            {"pct": 0.08, "lock": 0.25},
            {"pct": 0.15, "lock": 0.55},
            {"pct": 0.25, "lock": 0.85},
        ],
        "max_reduce_ratio": 0.25,
        "position_weight": 0.60,
    },
    "trend_follow": {
        "label": "趋势跟随",
        "expected_hold_hours": 168,
        "reduce_cooldown_hours": 24,
        "min_profit_for_reduce_pct": 0.05,
        "sl_atr_mult": 3.0,
        "tp_stages": [
            {"pct": 0.05, "lock": 0.30},
            {"pct": 0.10, "lock": 0.60},
            {"pct": 0.15, "lock": 0.80},
        ],
        "max_reduce_ratio": 0.30,
        "position_weight": 0.50,
    },
    "swing": {
        "label": "波段",
        "expected_hold_hours": 24,
        "reduce_cooldown_hours": 6,
        "min_profit_for_reduce_pct": 0.02,
        "sl_atr_mult": 1.5,
        "tp_stages": [
            {"pct": 0.03, "lock": 0.40},
            {"pct": 0.06, "lock": 0.70},
        ],
        "max_reduce_ratio": 0.50,
        "position_weight": 0.30,
    },
    "intraday": {
        "label": "日内",
        "expected_hold_hours": 4,
        "reduce_cooldown_hours": 1,
        "min_profit_for_reduce_pct": 0.0,
        "sl_atr_mult": 1.0,
        "tp_stages": [
            {"pct": 0.02, "lock": 0.50},
            {"pct": 0.04, "lock": 1.00},
        ],
        "max_reduce_ratio": 1.00,
        "position_weight": 0.20,
    },
    "scalp": {
        "label": "日内",
        # [2026-07-31] 名义 expected=3h，实际开仓还会被 runtime 复审点（常 2h×节奏）再砍。
        # 禁止 Master AI 延长；超时硬平。旧 8h 与 MR 快进快出冲突，并造成假「AI已延长」。
        "expected_hold_hours": 3,
        "reduce_cooldown_hours": 0.5,
        "min_profit_for_reduce_pct": 0.0,
        "sl_atr_mult": 1.5,
        "tp_stages": [
            {"pct": 0.03, "lock": 0.40},
            {"pct": 0.06, "lock": 1.00},
        ],
        "max_reduce_ratio": 1.00,
        "position_weight": 0.20,
    },
}

# 历史遗留别名：仅为向后兼容（空字典表示不再做别名替换）
# 之前的别名 scalp→intraday / position→trend_follow 导致这两档永远不出现
_NATURE_ALIAS: Dict[str, str] = {}

MAX_SUB_POSITIONS_PER_SYMBOL = 3

# nature → timeframe_tier 映射 (兼容旧架构)
NATURE_TO_TIER = {
    "scalp": "short",
    "intraday": "short",
    "swing": "mid",
    "position": "long",
    "trend_follow": "long",
}

# 统一权威:tier→nature 映射委托 tp_sl_authority(消除第4处分歧,见 I1)
from backend.services.tp_sl_authority import TIER_TO_NATURE  # short→scalp/long→trend_follow
# 备选 fallback:历史 long→position 的保守映射,仅零读取者保留作向后兼容参考
TIER_TO_NATURE_FALLBACK = {
    "short": "scalp",
    "mid": "swing",
    "long": "trend_follow",  # 与权威一致(原 position 已弃用)
}


def normalize_nature(raw: Optional[str]) -> str:
    """将各种 nature 名称统一到五种主类型。

    当前支持 5 档:
      scalp / intraday / swing / trend_follow / position
    未知别名兜底为 swing。
    """
    if not raw:
        return "swing"
    n = raw.strip().lower()
    if n in NATURE_RULES:
        return n
    if n in _NATURE_ALIAS:
        return _NATURE_ALIAS[n]
    return "swing"


def get_rules(nature: str) -> Dict[str, Any]:
    """获取指定 nature 的管理规则，兜底 swing。"""
    return NATURE_RULES.get(normalize_nature(nature), NATURE_RULES["swing"])


class SubPositionManager:
    """虚拟子仓位审核层"""

    def __init__(self, audit_only: bool = True):
        self.audit_only = audit_only

    # ────────────────────────────────────────
    #  查询当前子仓位
    # ────────────────────────────────────────

    def get_sub_positions(
        self, db: Session, account_id: int, symbol: str
    ) -> List[Dict[str, Any]]:
        """获取指定 symbol 的所有活跃子仓位。"""
        from backend.database.models import PaperPosition

        positions = (
            db.query(PaperPosition)
            .filter(
                PaperPosition.account_id == account_id,
                PaperPosition.symbol == symbol,
                PaperPosition.status == "open",
            )
            .all()
        )

        result = []
        for p in positions:
            nature = normalize_nature(getattr(p, "trade_nature", None))
            rules = get_rules(nature)
            margin = float(p.margin or 0)
            upnl = float(p.unrealized_pnl or 0)
            pnl_pct = (upnl / margin) if margin > 0 else 0.0

            age_hours = 0.0
            if p.opened_at:
                try:
                    opened = p.opened_at
                    if opened.tzinfo is None:
                        opened = opened.replace(tzinfo=timezone.utc)
                    age_hours = (datetime.now(timezone.utc) - opened).total_seconds() / 3600.0
                except Exception:
                    pass

            last_reduce_ts = getattr(p, "last_reduce_at", None)
            cooldown_remaining_h = 0.0
            if last_reduce_ts:
                try:
                    lr = last_reduce_ts
                    if lr.tzinfo is None:
                        lr = lr.replace(tzinfo=timezone.utc)
                    elapsed_h = (datetime.now(timezone.utc) - lr).total_seconds() / 3600.0
                    cooldown_remaining_h = max(0, rules["reduce_cooldown_hours"] - elapsed_h)
                except Exception:
                    pass

            result.append({
                "id": p.id,
                "symbol": p.symbol,
                "side": p.side,
                "size": float(p.size),
                "entry_price": float(p.entry_price),
                "mark_price": float(p.mark_price or p.entry_price),
                "leverage": float(p.leverage or 1),
                "margin": margin,
                "unrealized_pnl": upnl,
                "pnl_pct": pnl_pct,
                "trade_nature": nature,
                "strategy_id": p.strategy_id,
                "timeframe_tier": p.timeframe_tier,
                "tp_price": p.tp_price,
                "sl_price": p.sl_price,
                "age_hours": round(age_hours, 1),
                "reduce_count": int(getattr(p, "reduce_count", 0) or 0),
                "cooldown_remaining_h": round(cooldown_remaining_h, 1),
                "expected_hold_hours": rules["expected_hold_hours"],
                "opened_at": str(p.opened_at) if p.opened_at else None,
            })

        return result

    def get_all_sub_positions(
        self, db: Session, account_id: int
    ) -> Dict[str, List[Dict[str, Any]]]:
        """获取所有 symbol 的子仓位，按 symbol 分组。"""
        from backend.database.models import PaperPosition

        positions = (
            db.query(PaperPosition)
            .filter(
                PaperPosition.account_id == account_id,
                PaperPosition.status == "open",
            )
            .all()
        )

        grouped: Dict[str, List] = {}
        for p in positions:
            sym = p.symbol
            if sym not in grouped:
                grouped[sym] = []

            nature = normalize_nature(getattr(p, "trade_nature", None))
            rules = get_rules(nature)
            margin = float(p.margin or 0)
            upnl = float(p.unrealized_pnl or 0)
            pnl_pct = (upnl / margin) if margin > 0 else 0.0

            age_hours = 0.0
            if p.opened_at:
                try:
                    opened = p.opened_at
                    if opened.tzinfo is None:
                        opened = opened.replace(tzinfo=timezone.utc)
                    age_hours = (datetime.now(timezone.utc) - opened).total_seconds() / 3600.0
                except Exception:
                    pass

            grouped[sym].append({
                "id": p.id,
                "side": p.side,
                "size": float(p.size),
                "entry_price": float(p.entry_price),
                "margin": margin,
                "pnl_pct": pnl_pct,
                "trade_nature": nature,
                "strategy_id": p.strategy_id,
                "age_hours": round(age_hours, 1),
                "reduce_count": int(getattr(p, "reduce_count", 0) or 0),
                "label": rules["label"],
            })

        return grouped

    # ────────────────────────────────────────
    #  审核: 开仓
    # ────────────────────────────────────────

    def review_open(
        self,
        db: Session,
        account_id: int,
        symbol: str,
        side: str,
        trade_nature: str,
        notional_usd: float = 0,
        tp_pct: float = 0,
        total_equity: float = 0,
        agent_independent: bool = False,
    ) -> Tuple[bool, str]:
        """审核开仓请求。

        Returns:
            (通过, 原因)
        """
        nature = normalize_nature(trade_nature)
        subs = self.get_sub_positions(db, account_id, symbol)

        # 1. 同标的子仓数量 < MAX
        if len(subs) >= MAX_SUB_POSITIONS_PER_SYMBOL:
            reason = (
                f"{symbol} 已有 {len(subs)} 个子仓位"
                f"(上限{MAX_SUB_POSITIONS_PER_SYMBOL})"
            )
            return self._verdict(False, reason, "open", symbol, nature, db=db, account_id=account_id)

        # 2. 方向一致性
        if subs:
            existing_side = subs[0]["side"]
            want_side = "long" if side in ("buy", "long") else "short"
            if want_side != existing_side:
                # 2026-07-06 整改（审查 3 #17）：review_flip 此前定义了却从未被
                # 调用，导致所有反向开仓请求都被无差别拒绝——即使 MLTO 三周期
                # 已经一致确认了反向趋势，也必须先手动平仓再开新仓，多付一轮
                # 手续费/滑点还慢半拍。这里接入 review_flip，用 MLTO 最近一次
                # 三层 bias 判断这次"反向"到底是噪音还是真正的趋势翻转：
                # 只有 full_flip（三周期方向一致）才允许直接把这次开仓视为
                # 翻转动作放行，其余级别（reduce_swing_intraday/reduce_all_
                # keep_trend/none）说明证据不足以支持整体反向，维持原有的
                # "需先翻转"拒绝，避免仅凭单一信号就反手开仓。
                flip_level, flip_reason = "none", "无MLTO三周期决策数据，按无证据处理"
                try:
                    from backend.services.multi_timeframe_orchestrator import mt_orchestrator
                    mtf_decision = mt_orchestrator.get_last_decision(symbol)
                    if mtf_decision is not None:
                        flip_level, flip_reason = self.review_flip(
                            symbol,
                            long_bias=mtf_decision.long_view.bias,
                            mid_bias=mtf_decision.mid_view.bias,
                            short_bias=mtf_decision.short_view.bias,
                        )
                except Exception as _flip_err:
                    logger.debug(f"[SubPositionManager] review_flip 数据获取失败: {_flip_err}")

                if flip_level != "full_flip":
                    reason = (
                        f"{symbol} 已有 {existing_side} 子仓，不能开 {want_side} "
                        f"(需先翻转; MLTO判定={flip_level}: {flip_reason})"
                    )
                    return self._verdict(False, reason, "open", symbol, nature, db=db, account_id=account_id)

        # 3. 该 nature 是否已有活跃子仓
        existing_natures = {s["trade_nature"] for s in subs}
        if nature in existing_natures:
            reason = f"{symbol} 已有 {nature} 子仓位，不能重复开仓"
            return self._verdict(False, reason, "open", symbol, nature, db=db, account_id=account_id)

        # 3b. AI 自动选币强制隔离：只允许短线(intraday)和中线(swing)，严禁长线
        # [2026-07-20 修复 — 用户反馈"之前改了好几次都没成功"的根因]
        # 此前这里有个 (PAPER_FAST_TRIAL or agent_independent) 的豁免分支，理由是
        # "TrendAgent 已放行→允许试单"——但 PAPER_FAST_TRIAL 默认跟随
        # FULLAUTO_FLOW_MODE=ai_first 常态开启，等于该豁免几乎永远成立，把这道
        # "最后一道闸"直接废掉：不管上游 mlto_cycle.py/master_execution.py 的分析层
        # 过滤加了多少次，只要有任何遗漏路径（新路径/未来改动/竞态）把 AI 选币的
        # trend_follow/position 开仓请求送到这里，都会被这条豁免重新放行，这正是
        # 之前多次修复"看起来改了但没生效"的根本原因——upstream 分析层过滤和这里
        # 的最终执行闸互相矛盾。现在改为无条件硬闸：AI 选币永远不允许开
        # trend_follow/position，不因 PAPER_FAST_TRIAL/agent_independent 而放行，
        # 与"长线只做会话固定交易对"的要求保持单一、无例外的口径。
        # 不影响短线(intraday)/中线(swing)：AUTO_COIN_ALLOWED_NATURES 本就包含这两者。
        try:
            from backend.services.auto_coin_selector import is_auto_coin_symbol
            from backend.config.settings import AUTO_COIN_ALLOWED_NATURES
            _is_ai_coin = is_auto_coin_symbol(symbol)
            if not _is_ai_coin:
                # 内存态兜底：is_auto_coin_symbol 只查进程内存里已注册的选币调度器
                # (_auto_symbols)，进程重启后调度器还没重建时会漏判——这正是本次
                # 修复要杜绝的"看似生效实则漏一个入口"问题，因此这里再查一次数据库
                # 权威字段 (FullAutoSession.auto_coin_symbols) 兜底，双重确认不是
                # AI 选币才放行，宁可多查一次库也不漏判。
                try:
                    from backend.database.models import FullAutoSession
                    _sym_u = str(symbol).strip().upper()
                    _sess = (
                        db.query(FullAutoSession)
                        .filter(
                            (FullAutoSession.paper_account_id == account_id)
                            | (FullAutoSession.account_id == account_id),
                            FullAutoSession.status != "stopped",
                        )
                        .order_by(FullAutoSession.id.desc())
                        .first()
                    )
                    if _sess:
                        _db_auto = {
                            str(s).strip().upper()
                            for s in (getattr(_sess, "auto_coin_symbols", None) or [])
                            if s
                        }
                        _is_ai_coin = _sym_u in _db_auto
                except Exception:
                    pass
            if _is_ai_coin:
                _allowed = set(AUTO_COIN_ALLOWED_NATURES)
                if nature not in _allowed:
                    reason = (
                        f"{symbol} 是AI自动选币，只允许短线/中线交易"
                        f"(允许: {sorted(_allowed)}，"
                        f"当前: {nature})"
                    )
                    return self._verdict(False, reason, "open", symbol, nature, db=db, account_id=account_id)
        except ImportError:
            pass  # 自动选币模块不可用时放行

        # 4. 手续费+滑点门卫（综合成本校验）
        if notional_usd > 0 and tp_pct > 0:
            from backend.services.fee_guard import fee_guard, calc_slippage_rate
            _slip = calc_slippage_rate(notional_usd, nature, is_sl=False)
            ok, fee_reason = fee_guard.check_open(
                notional_usd, tp_pct,
                slippage_rate=_slip, trade_nature=nature,
            )
            if not ok:
                return self._verdict(False, fee_reason, "open", symbol, nature, db=db, account_id=account_id)

        # 5. 总仓位风控上限 (所有子仓合计保证金 < 40% 权益)
        if total_equity > 0:
            total_margin = sum(s["margin"] for s in subs)
            if total_margin / total_equity > 0.40:
                reason = (
                    f"{symbol} 子仓合计保证金${total_margin:.1f} "
                    f"占权益{total_margin/total_equity:.0%} > 40%"
                )
                return self._verdict(False, reason, "open", symbol, nature, db=db, account_id=account_id)

        return self._verdict(True, "ok", "open", symbol, nature)

    # ────────────────────────────────────────
    #  审核: 减仓
    # ────────────────────────────────────────

    def review_reduce(
        self,
        db: Session,
        position_id: int,
        reduce_pct: float = 0.5,
        is_stop_loss: bool = False,
    ) -> Tuple[bool, str]:
        """审核减仓请求。

        Args:
            position_id: PaperPosition.id
            reduce_pct: 减仓比例 0-1
            is_stop_loss: 是否止损触发 (止损不受冷却/利润限制)

        Returns:
            (通过, 原因)
        """
        from backend.database.models import PaperPosition

        pos = db.query(PaperPosition).filter(PaperPosition.id == position_id).first()
        if not pos:
            return True, "position not found, pass-through"

        if is_stop_loss:
            return True, "止损不受限制"

        nature = normalize_nature(getattr(pos, "trade_nature", None))
        rules = get_rules(nature)
        symbol = pos.symbol
        _aid = getattr(pos, "account_id", None)
        margin = float(pos.margin or 0)
        upnl = float(pos.unrealized_pnl or 0)
        pnl_pct = (upnl / margin) if margin > 0 else 0.0

        # 1. 冷却时间
        last_reduce = getattr(pos, "last_reduce_at", None)
        if last_reduce:
            try:
                lr = last_reduce
                if lr.tzinfo is None:
                    lr = lr.replace(tzinfo=timezone.utc)
                elapsed_h = (datetime.now(timezone.utc) - lr).total_seconds() / 3600.0
                cooldown = rules["reduce_cooldown_hours"]
                if elapsed_h < cooldown:
                    reason = (
                        f"{symbol}[{nature}] 减仓冷却中: "
                        f"距上次{elapsed_h:.1f}h < {cooldown}h"
                    )
                    return self._verdict(False, reason, "reduce", symbol, nature, db=db, account_id=_aid)
            except Exception:
                pass

        # 2. 最小利润要求 (亏损时不受此限制 — 亏损减仓属于止损行为)
        min_profit = rules["min_profit_for_reduce_pct"]
        if pnl_pct > 0 and pnl_pct < min_profit:
            reason = (
                f"{symbol}[{nature}] 浮盈{pnl_pct:.1%} < "
                f"最小减仓利润{min_profit:.0%}"
            )
            return self._verdict(False, reason, "reduce", symbol, nature, db=db, account_id=_aid)

        # 3. 手续费+滑点门卫 (intraday 的 min_profit_for_reduce_pct=0 时启用)
        if min_profit == 0 and pnl_pct > 0:
            from backend.services.fee_guard import fee_guard, calc_slippage_rate
            reduce_notional = float(pos.size or 0) * float(pos.mark_price or pos.entry_price) * reduce_pct
            reduce_pnl = upnl * reduce_pct
            _slip = calc_slippage_rate(reduce_notional, nature, is_sl=is_stop_loss)
            ok, fee_reason = fee_guard.check_reduce(
                reduce_notional, reduce_pnl,
                slippage_rate=_slip, trade_nature=nature, is_sl=is_stop_loss,
            )
            if not ok:
                return self._verdict(False, fee_reason, "reduce", symbol, nature, db=db, account_id=_aid)

        # 4. 单次减仓比例上限
        max_ratio = rules["max_reduce_ratio"]
        if reduce_pct > max_ratio:
            reason = (
                f"{symbol}[{nature}] 减仓比例{reduce_pct:.0%} > "
                f"上限{max_ratio:.0%}"
            )
            return self._verdict(False, reason, "reduce", symbol, nature, db=db, account_id=_aid)

        return self._verdict(True, "ok", "reduce", symbol, nature)

    # ────────────────────────────────────────
    #  审核: 方向翻转
    # ────────────────────────────────────────

    def review_flip(
        self,
        symbol: str,
        long_bias: str,
        mid_bias: str,
        short_bias: str,
    ) -> Tuple[str, str]:
        """审核方向翻转请求。

        Returns:
            (flip_level, 原因)
            flip_level: "none" | "reduce_swing_intraday" | "reduce_all_keep_trend" | "full_flip"
        """
        biases = [long_bias, mid_bias, short_bias]
        bearish_count = sum(1 for b in biases if b == "bearish")
        bullish_count = sum(1 for b in biases if b == "bullish")

        # 全部反转
        if bearish_count >= 3 or bullish_count >= 3:
            return "full_flip", f"三周期全部一致({long_bias}/{mid_bias}/{short_bias})"

        # 中+短反转
        if mid_bias == short_bias and mid_bias != "neutral":
            if long_bias != mid_bias:
                return "reduce_all_keep_trend", (
                    f"中+短反转({mid_bias}), 长线({long_bias})未确认"
                )

        # 只有短线反转
        if short_bias != "neutral" and short_bias != mid_bias:
            return "reduce_swing_intraday", (
                f"仅短线反转({short_bias}), 中线({mid_bias})未确认"
            )

        return "none", "方向一致，无需翻转"

    # ────────────────────────────────────────
    #  对账
    # ────────────────────────────────────────

    def reconcile(
        self, db: Session, account_id: int, symbol: str,
        exchange_qty: float = 0, exchange_side: str = "",
    ) -> Dict[str, Any]:
        """对账: 子仓合计 vs 交易所仓位。"""
        subs = self.get_sub_positions(db, account_id, symbol)
        internal_qty = sum(s["size"] for s in subs)
        internal_side = subs[0]["side"] if subs else ""

        result = {
            "symbol": symbol,
            "internal_qty": internal_qty,
            "internal_side": internal_side,
            "exchange_qty": exchange_qty,
            "exchange_side": exchange_side,
            "sub_count": len(subs),
            "matched": True,
            "natures": [s["trade_nature"] for s in subs],
        }

        if exchange_qty > 0:
            diff = abs(internal_qty - exchange_qty)
            if diff / max(exchange_qty, 1e-8) > 0.01:
                result["matched"] = False
                logger.warning(
                    f"[SubPosMgr] 对账不一致 {symbol}: "
                    f"内部={internal_qty:.6f} 交易所={exchange_qty:.6f} "
                    f"差额={diff:.6f}"
                )

        return result

    # ────────────────────────────────────────
    #  构建 prompt 上下文
    # ────────────────────────────────────────

    def build_prompt_context(
        self, db: Session, account_id: int, symbols: List[str]
    ) -> str:
        """为 AI prompt 构建子仓位状态描述。"""
        lines = []
        for sym in symbols:
            subs = self.get_sub_positions(db, account_id, sym)
            if not subs:
                continue

            lines.append(f"\n{sym}:")
            for s in subs:
                rules = get_rules(s["trade_nature"])
                cd_str = (
                    f"冷却中({s['cooldown_remaining_h']:.0f}h)"
                    if s["cooldown_remaining_h"] > 0
                    else "可操作"
                )
                lines.append(
                    f"  [{s['trade_nature']}] "
                    f"{s['side'].capitalize()} {s['size']:.4f} "
                    f"@ ${s['entry_price']:.2f} | "
                    f"持仓{s['age_hours']:.0f}h | "
                    f"{s['pnl_pct']:+.1%} | "
                    f"SL ${s['sl_price'] or 0:.2f} | "
                    f"减仓{s['reduce_count']}次 | "
                    f"{cd_str}"
                )

        if not lines:
            return ""

        header = "=== 当前子仓位 ==="
        rules_block = (
            "\n=== 子仓管理规则 ===\n"
            "- trend_follow(趋势): 除非多周期全反转否则不动, 减仓需利润>5%且间隔>24h\n"
            "- swing(波段): 短线回调可减仓, 但利润需>2%且间隔>6h\n"
            "- intraday(日内): 灵活操作, 但预期利润必须覆盖3倍手续费\n"
            "- scalp(日内): 预期持仓≤8h, SL=1.5×ATR, 灵活止盈\n"
            "- 同标的最多3个子仓, 必须同方向\n"
            "- 你的 action 需通过 trade_nature 指明针对哪个子仓"
        )

        return header + "".join(lines) + rules_block

    # ────────────────────────────────────────
    #  内部
    # ────────────────────────────────────────

    def _verdict(
        self, passed: bool, reason: str,
        action: str, symbol: str, nature: str,
        db: Optional[Session] = None,
        account_id: Optional[int] = None,
    ) -> Tuple[bool, str]:
        """统一审核结果日志。audit_only 模式下拦截只记日志不阻止。

        深挖第 3 轮 (2026-05-08)：拒绝事件统一落盘到 risk_control_events，
        guard_name 用 'sub_position_manager:<action>' 区分 open/reduce/flip。
        如果 reason 中含 'fee=' 或 'slip=' 或 '综合成本'，标 guard_name='fee_guard'。
        """
        if not passed:
            if self.audit_only:
                logger.info(
                    f"[SubPosMgr][AUDIT] {action} {symbol}[{nature}] "
                    f"拦截(仅记录): {reason}"
                )
                # audit_only 模式下不写 risk_control_events 避免误导
                return True, f"[AUDIT] {reason}"
            else:
                logger.info(
                    f"[SubPosMgr] {action} {symbol}[{nature}] "
                    f"拦截: {reason}"
                )
                if db is not None and account_id is not None:
                    try:
                        from backend.services.unified_risk_gate import record_guard_block
                        _is_fee_related = any(
                            kw in reason for kw in ("综合成本", "fee=", "slip=", "滑点", "手续费")
                        )
                        record_guard_block(
                            db,
                            account_id=account_id,
                            guard_name=("fee_guard" if _is_fee_related
                                        else f"sub_position_manager:{action}"),
                            symbol=symbol, side=nature,
                            reason=reason,
                            extra={"action": action, "nature": nature,
                                   "audit_only": False},
                        )
                    except Exception:
                        pass
                return False, reason
        return True, reason


sub_position_manager = SubPositionManager(audit_only=False)
