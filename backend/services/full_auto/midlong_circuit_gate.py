"""midlong_circuit_gate — 中长线（mid/long tier）开仓熔断器。

[2026-08-29 全面修复 · P1.4] 数据依据（trade_facts 8/2-8/29）：
  - mid 层 233 笔 -325.31，其中空头 -601.73（avg -4.97/笔）；
  - 8/12-13 VELVET 两天 54 笔空 -410：同价位反复开空打损（单笔 -90 级），
    mid 单笔风险是 scalp 的 ~35 倍（SL avg -41.38 vs -1.18）却无任何熔断；
  - scalp 层早有 short_tier_entry_gate 熔断，mid/long 层完全裸奔。

本模块补齐：
  1. 连续亏损熔断：同 (account, symbol) 连亏 N 笔（默认 3，mid 单笔损失大，
     阈值比 scalp 的 8 更紧）→ 冷却 12h；
  2. 单 symbol 日亏上限：当日累计净亏（含费）≤ -cap（默认 60 USD，env 可配）
     → 冷却到次日；
  3. 状态落盘 data/midlong_circuit_state.json（重启不丢，沿 short_tier 惯例）。

P1.5：MIDLONG_OPEN_SHORT_ENABLED=false（默认）暂停 mid/long 新开空头
（mid 空头 30 天 -601 是最差象限；多头 +276 唯一健康）。
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# ── 配置（env 直读，免 settings 循环依赖；settings 可后续透传）──
def _env_f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


def _env_b(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or ("true" if default else "false")).strip().lower()
    return raw in ("1", "true", "yes", "on")


CONSEC_LOSSES_LIMIT = int(_env_f("MIDLONG_CIRCUIT_CONSEC_LOSSES", 3) or 3)
COOLDOWN_S = int(_env_f("MIDLONG_CIRCUIT_COOLDOWN_S", 12 * 3600) or 12 * 3600)
DAILY_LOSS_CAP = _env_f("MIDLONG_CIRCUIT_DAILY_LOSS_CAP", 60.0)  # USD，≤0 关闭
SHORT_OPEN_ENABLED = _env_b("MIDLONG_OPEN_SHORT_ENABLED", False)


def _short_mode() -> str:
    """mid 空头模式（2026-09-09 二次修订）：

      - **regime_gated（默认）**：只在**日线确认下行趋势**时允许开空
        （收盘价 < EMA200 且 60 日动量 < -5%）；同时在该 regime 下**禁止做多**。
      - `off`：全停（上一版的机械做法，已被实测否决）。
      - `conditional`：旧口径（4h 偏空 或 24h 跌≥2%）——实测是追跌条件，净失血。
      - `on`：无条件放行（调试用）。

    ## 为什么从 off 改成 regime_gated（数据依据）

    `_audit_ml/N3_regime.py`（29 币日线 × 2018 天级样本，EMA200 + 60 日动量分档）：

    | regime | 方向 | n | 均值% | 胜率 | t |
    |---|---|---|---|---|---|
    | up | long-14d | 4134 | **+1.038** | 0.468 | **+4.11** |
    | up | short-14d | 4134 | -1.038 | 0.532 | -4.11 |
    | chop | long-14d | 5192 | +0.138 | 0.447 | +0.61 |
    | down | long-14d | 8899 | **-0.618** | 0.447 | **-4.09** |
    | down | short-14d | 8899 | **+0.618** | 0.542 | **+4.09** |

    即"能不能做空"完全取决于 regime：**下行 regime 里做空是正边际（t=+4.09）**，
    上行 regime 里做空是负边际（t=-4.11）。

    用真实成交验证（`_audit_ml/N4_gate.py` / `N5_variants.py`，281 笔 mid/long 平仓）：

    | 方案 | 保留笔数 | 合计 PnL |
    |---|---|---|
    | A 现状（空头全停，只做多） | 164 | -65.51 |
    | B 旧 conditional（等价全放行） | 281 | -127.57 |
    | **C/G regime 门（up/chop 只多，down 可空）** | 175 | **-44.19** |
    | F 严格（up 多 / down 空 / chop 空仓） | 136 | -30.14 |

    **down-regime 的 46 笔空头合计 +2.33（净正）**——全停等于把这部分正贡献也砍了。
    regime 门比全停再改善 +21.32，同时保留了趋势反转时的做空能力。

    回滚：`MIDLONG_SHORT_MODE=off`（回到机械全停）或 `=conditional`（旧口径）。
    """
    if SHORT_OPEN_ENABLED:
        return "on"
    return (os.getenv("MIDLONG_SHORT_MODE", "regime_gated") or "regime_gated").strip().lower()


# ── 日线 regime（EMA200 + 60 日动量）缓存：symbol -> (ts, regime) ──
_DAILY_REGIME_CACHE: Dict[str, tuple] = {}
_DAILY_REGIME_TTL_S = 1800.0  # 日线 regime 半小时刷新一次足够


def _daily_regime(symbol: str) -> str:
    """日线 regime：up / down / chop（数据不足返回 "" → 调用方 fail-open）。

    定义（与 `_audit_ml/N3_regime.py` 完全一致，避免双口径）：
      - up   ：收盘 > EMA200 且 60 日动量 > +5%
      - down ：收盘 < EMA200 且 60 日动量 < -5%
      - chop ：其余
    """
    sym = str(symbol or "").upper()
    if not sym:
        return ""
    now = time.time()
    row = _DAILY_REGIME_CACHE.get(sym)
    if row and now - row[0] < _DAILY_REGIME_TTL_S:
        return row[1]
    try:
        from backend.services.kline_data_service import kline_service

        raw = kline_service.get_aggregated_klines(sym, "1d", count=260)
        if not raw or len(raw) < 70:
            return ""
        closes = [float(r["close"]) for r in raw if r.get("close") is not None]
        if len(closes) < 70:
            return ""
        n = len(closes)
        # EMA200（不足 200 根用现有均值近似，与实测脚本同口径）
        if n >= 200:
            k = 2.0 / 201.0
            ema = closes[0]
            for c in closes[1:]:
                ema = c * k + ema * (1 - k)
        else:
            ema = sum(closes) / n
        px = closes[-1]
        base = closes[-61] if n >= 61 else closes[0]
        mom60 = (px / base - 1.0) if base > 0 else 0.0
        if px > ema and mom60 > 0.05:
            reg = "up"
        elif px < ema and mom60 < -0.05:
            reg = "down"
        else:
            reg = "chop"
        _DAILY_REGIME_CACHE[sym] = (now, reg)
        return reg
    except Exception as exc:
        logger.debug("[MidCircuit] 日线 regime 读取失败 %s: %s", sym, exc)
        return ""


def _regime_short_ok(symbol: str) -> bool:
    """regime_gated：仅下行 regime 允许开空。"""
    return _daily_regime(symbol) == "down"


def _regime_long_ok(symbol: str) -> bool:
    """regime_gated：下行 regime 禁止做多（实测 down-long 14d 均值 -0.618%、t=-4.09）。"""
    return _daily_regime(symbol) != "down"


def _chop_flat_enabled() -> bool:
    """chop（震荡）regime 是否空仓（不新开任何方向）。

    [2026-09-09 第十轮] 实测（`_audit_ml/P1_chop.py`，281 笔真实 mid/long 平仓）：
    chop 期间 85 笔合计 **-53.66**（多头 -14.05 / 空头 -39.60）。
    含 chop 空仓的方案 F（up 多 / down 空 / chop 空仓）= **-30.14**（136 笔），
    优于当前方案 C/H（up/chop 只多 + down 空）= -44.19（175 笔），再改善 +14.05。
    代价：成交笔数 175 → 136（-22%）。

    默认 **false**（保守上线，先观察当前方案 C 的实盘表现）；置 true 即启用。
    """
    return os.getenv("MIDLONG_CHOP_MODE", "long_only").strip().lower() in (
        "flat", "off", "none", "empty", "1", "true", "yes", "on",
    )


def _down_short_enabled() -> bool:
    """down（下行趋势）regime 是否允许开空。

    [2026-09-09 第十五轮] learned 准入增加 **chg24≤-1%** 条件：

    历史空头逐笔归因（`backend/scripts/deep_short_attribution.py`，107 笔）：
      入场毒性是唯一负期望来源——按历史入场点持有 72h/14d 反而 -5.34%/-12.31%，
      出场结构实际**救了钱**（出场差 +4.74% vs 72h 持有）；MFE>50bp 占比 0.00。

    大样本条件验证（`backend/scripts/test_short_entry_conditions.py`，
    30 币 down-regime 1h 信号 n=31684，2025-07 起）：
      - 原 learned（分位≥60+RSI55-80）整体 +0.79%/72h，但时间切分后
        **前段 +2.47% / 后段 -0.74%**——后段（2026-04 起）变负，不稳。
      - 加 chg24≤-1%（组合 D）：前段 +1.01% / 后段 +1.07%（**唯一两段皆正**），
        trail 口径 前段 +1.04%(n=216,胜率0.708) / 后段 +1.36%(n=95,胜率0.758)；
        非重叠样本 +0.34%/中位 +1.41%；逐币 21/28 正。
      - 语义：down 趋势中「区间上沿 + 已开始回落（chg24≤-1%）」的做空，
        不追涨（chg24≥+5% 的历史入场 72h -13.6%）。

    模式（`MIDLONG_DOWN_SHORT_MODE`）：
      - `learned`（默认）：down regime 且「分位≥60% + RSI∈[55,80] + chg24≤-1%」
      - `flat`：down regime 完全不开空
      - `allowed`：down regime 无条件放行（旧口径，仅调试）
    """
    return os.getenv("MIDLONG_DOWN_SHORT_MODE", "learned").strip().lower()


def _long_mode() -> str:
    """mid/long 多头模式（[2026-09-09 第十六轮] 引入，[第十七轮] 按 hub 成交样本修正）。

    与空头侧对称：多头不再受 `MIDLONG_SHORT_MODE` 牵制，独立由本开关治理。

    ## 数据依据（三份独立证据，全部本机数据库可复现）

    1. **历史逐笔归因**（`backend/scripts/deep_long_attribution.py`，140 笔）：
       入场有边际、出场砍掉边际——实际 -0.59% vs 72h 持有 +0.24% vs 14d 持有 +11.24%
       （出场差 -11.82%）。即多头主修项是出场结构（§9 已改 SL6/追踪3-1.5/168h），
       本门是入场侧的加强，与空头侧第十五轮对称。

    2. **大样本条件验证**（`backend/scripts/test_long_entry_conditions.py` +
       `_audit_ml/W4_long_gate_design.py`，30 币 × 1h，2025-07 起，n=53624 信号）：

       | regime | 配置 | 72h 前段 | 72h 后段 | trail |
       |---|---|---|---|---|
       | up | 无条件 | +0.006% | +2.385% | +1.40%(0.67) |
       | **up** | **chg24≥3%** | **+0.718%** | **+5.448%** | **+2.34%(0.71)** |
       | up | pos<60（低吸） | **-0.479%** | +1.764% | +1.11%(0.66) |
       | chop | 无条件 | -0.122% | -0.392% | +0.28%(0.59) |
       | **chop** | **pos≥60 且 chg≥2%** | **+0.745%** | **+0.449%** | **+0.69%(0.65)** |
       | chop | pos<60（低吸） | -0.142% | -0.678% | +0.30%(0.59) |
       | chop | 接刀 chg<-5% | -2.959% | -2.513% | -0.19%(0.59) |
       | down | 无条件 | -1.826% | +0.130% | -0.11%(0.58) |

    3. **最终配置复核**（`_audit_ml/W5_long_gate_final.py`）：
       - U2（up+chg24≥3%）：非重叠 72h +2.698%（n=166）、trail +1.718%（n=176）；
         逐币 trail 17/23 正。对照 up 无条件非重叠 72h 仅 +0.925%。
       - C2（chop+pos≥60+chg≥2%）：非重叠 72h +0.146%（n=345）、trail +0.380%
         （n=357）；逐币 20/28 正。对照 chop 无条件 72h 前后段全负。
       - down 无条件：72h -0.85%（前段 -1.83%）、逐币 11/28 正 → 拦（t=-4.09，第九轮）。

    ## [第十七轮] hub 成交样本修正（deployment 口径，n=234 笔历史多头成交）

    第十六轮的 bar 级大样本结论**不能直接套在 hub 成交流上**：
    `deep_long_freshness.py` + `_audit_ml/W7/W8` + `deep_long_sources.py` 实测：

    | 口径 | 第十六轮门 | 实测 |
    |---|---|---|
    | 现行门放行集（up+chg≥3 或 chop+pos≥60+chg≥2） | n=56 | 72h **-2.04%** |
    | up+chg∈[3,6)（加 spike 上界） | n=17 | 72h +0.58% |
    | up+chg∈[6,10)（spike 追入） | n=23 | 72h **-3.19%** |
    | up+chg≥10（spike 追入） | n=14 | 72h **-5.05%** |
    | **chop 无条件** | n=62 | 72h **+4.66%**（前段 +3.70%/n=57，后段 +10.97%/n=6） |
    | chop 被现行门拦掉的部分（pos<60 或 chg<2） | n=47 | 72h **+5.03%**（恰是最好的那批） |
    | down | n=38 | 72h -1.79% → 拦 |

    **两处修正**：
    - up 分支加 **spike 上界 chg24<6%**：hub 在 chg≥6 的入场是事件尖峰追入
      （毒性 n=37 笔 -3.19%/-5.05%），[3,6) 的温和动量入场才是正边际；
    - chop 分支**取消 pos≥60+chg≥2 要求**：hub 的 chop 成交 72h +4.66% 且
      前后段皆正，被拦掉的恰是回调低吸这批最好的入场。chop 的历史失血来自
      出场结构（深度一：chop 出场差 -5.07%）与空头（P1_chop：空头 -39.60），
      不是 chop 多头入场本身。chop 高位保护仍由上游位置闸（ranging 决策态
      禁 pos≥60，§3 实测）承担，`MIDLONG_CHOP_MODE=flat` 仍是最终刹车。

    **修正后门在 hub 样本上的分离度**：放行集（up+chg∈[3,6) ∪ chop）
    72h **+3.78%**（n=79，前段 +3.35%/后段 +4.26%）；拦下集（up 非带内 117 笔
    -0.65% ∪ down 38 笔 -1.79%）。对照修正前放行集 -2.04%。

    ## [第十八轮] 用近 30 天「实际 P&L」复核并回滚 chop 放宽

    第十七轮的 chop 放宽只用 72h 前向收益衡量；加入**实际兑现**（含出场）后
    （`_audit_ml/X8_gate_variants_30d.py`，近 30 天 184 笔真实成交）：

    | 门变体 | n | 实际均值 | 胜率 |
    |---|---|---|---|
    | 不拦 | 184 | -0.406% | 0.277 |
    | 仅拦 down | 159 | -0.267% | 0.302 |
    | round-16（up≥3 无上界） | 44 | +0.200% | 0.477 |
    | round-17（chop 无条件） | 80 | -0.107% | 0.225 |
    | **up[3,6) + chop pos≥60&chg≥2** | **28** | **+0.406%** | **0.536** |
    | up[3,6) + chop pos<60 | 45 | -0.124% | 0.222 |

    即：**up 的 spike 上界（第十七轮）与 chop 的位置+动量条件（第十六轮）
    都是对的，第十七轮把 chop 放宽是错的**——被放回的 chop 成交（-0.38%/笔）
    没有可收割的 spike。第十八轮已回滚 chop 分支。多头口径下
    up[3,6)+chopC2 = **+1.722%/笔、胜率 0.857（n=14）**。

    模式（`MIDLONG_LONG_MODE`）：
      - `learned`（默认）：down 拦；up 需 chg24∈[3,6)；chop 需 pos24≥60% 且 chg24≥2%；
        数据不足 fail-open（多头侧一贯口径，防过度阻止）
      - `regime_only`：仅 down 拦（第九轮至今的生产行为，回滚档）
      - `allow_all`：完全不拦（第八轮之前的行为，仅调试）
    """
    return os.getenv("MIDLONG_LONG_MODE", "learned").strip().lower()


# ── learned 多头准入特征缓存 ──
_LONG_FEAT_CACHE: Dict[str, tuple] = {}
_LONG_FEAT_TTL = 900.0  # 15 分钟


def _long_learned_tiers() -> set:
    """learned 多头特征闸作用的车道（默认仅 mid）。

    [2026-09-10 第二十一轮] 依据（`_audit_ml/Y26_long_gate_impact.py`，近 30 天）：
    long 层 27 笔中，被该闸拦掉的 18 笔净 **+$55.48**（14 个赢家 +$143.07、
    4 个亏家 -$87.60），而放行的 9 笔净 **-$0.07**（含 BTC 单笔 -$45.99）。
    即「up+chg∈[3,6)」这套动量门是**为 mid 层标定的**，用在趋势车道（Chandelier
    管理、以回调/中等动量入场）上会拦掉大部分利润。
    回滚/扩展：`MIDLONG_LONG_LEARNED_TIERS=mid,long`。
    """
    raw = (os.getenv("MIDLONG_LONG_LEARNED_TIERS", "mid") or "mid").strip().lower()
    return {x.strip() for x in raw.split(",") if x.strip()}


def _tier_in_learned(tier: str) -> bool:
    return str(tier or "mid").strip().lower() in _long_learned_tiers()


def _long_learned_ok(symbol: str, regime: str) -> Tuple[bool, str]:
    """learned 多头准入（第十七轮引入，第十八轮按近 30 天实际 P&L 收敛）：

      - regime=up  ：chg24 ∈ [+3%, +6%)（`MIDLONG_LONG_CHG24_MIN_UP_PCT` /
        `MIDLONG_LONG_CHG24_MAX_UP_PCT`）——温和动量确认，拒 spike 追入；
      - regime=chop：pos24 ≥ 60% 且 chg24 ≥ +2%
        （`MIDLONG_LONG_CHOP_POS_MIN_PCT` / `MIDLONG_LONG_CHG24_MIN_CHOP_PCT`）
        ——区间上沿突破确认（第十八轮恢复；见 `_long_mode` 的证据修正）；
      - 其它 regime：放行（down 已在上游被 `_daily_regime` 拦）。

    数据依据（第十八轮，`_audit_ml/X8_gate_variants_30d.py`，近 30 天 184 笔
    真实成交的**实际 P&L**）：
      up[3,6)+chop pos≥60&chg≥2 → n=28 +0.406%/笔 胜率 0.536（多头 +1.722%/0.857）
      up[3,6)+chop 无条件      → n=80 -0.107%/笔 胜率 0.225
      round-16（up≥3 无上界）  → n=44 +0.200%/笔 胜率 0.477
      不拦                     → n=184 -0.406%/笔
      仅拦 down                → n=159 -0.267%/笔

    数据不足 → **fail-open（放行）**：多头是唯一健康侧，且本门只做减法，
    与位置闸 fail-open、regime 未知 fail-open 一致（防过度阻止）。
    """
    from backend.services.kline_data_service import kline_service

    sym = str(symbol or "").upper()
    now = time.time()
    row = _LONG_FEAT_CACHE.get(sym)
    if row and now - row[0] < _LONG_FEAT_TTL:
        return row[1], row[2]
    try:
        raw = kline_service.get_aggregated_klines(sym, "1h", count=30)
        if not raw or len(raw) < 26:
            res = (True, "learned_long_nodata(fail-open)")
            _LONG_FEAT_CACHE[sym] = (now, res[0], res[1])
            return res
        closes = [float(r["close"]) for r in raw]
        chg24 = (closes[-1] / closes[-25] - 1.0) * 100 if closes[-25] > 0 else 0.0
        if regime == "chop":
            # [第十八轮恢复] chop 需「区间上沿 + 动量」：近 30 天实际 P&L 显示
            # 无条件放行的 chop 成交 -0.107%/笔（n=80），加回该条件后 +0.406%/笔（n=28）。
            highs = [float(r["high"]) for r in raw]
            lows = [float(r["low"]) for r in raw]
            win = list(zip(highs, lows))[-24:]
            hi = max(h for h, _ in win)
            lo = min(l for _, l in win)
            pos = (closes[-1] - lo) / (hi - lo) * 100 if hi > lo else 50.0
            pos_min = _env_f("MIDLONG_LONG_CHOP_POS_MIN_PCT", 60.0)
            chg_min = _env_f("MIDLONG_LONG_CHG24_MIN_CHOP_PCT", 2.0)
            if pos < pos_min:
                res = (False, f"learned_long_chop_pos{pos:.0f}<{pos_min:.0f}")
            elif chg24 < chg_min:
                res = (False, f"learned_long_chop_chg24_{chg24:+.1f}<{chg_min:+.1f}")
            else:
                res = (True, f"learned_long_chop_ok(pos{pos:.0f},chg{chg24:+.1f})")
        elif regime == "up":
            # [第十七轮] chg24 ∈ [3, 6)：温和动量确认；≥6% 是 spike 追入
            # （hub 成交实测 chg∈[6,10) 72h -3.19%、≥10% -5.05%）。
            chg_min = _env_f("MIDLONG_LONG_CHG24_MIN_UP_PCT", 3.0)
            chg_max = _env_f("MIDLONG_LONG_CHG24_MAX_UP_PCT", 6.0)
            if chg24 < chg_min:
                res = (False, f"learned_long_up_chg24_{chg24:+.1f}<{chg_min:+.1f}")
            elif chg24 >= chg_max:
                res = (False, f"learned_long_up_chg24_{chg24:+.1f}≥{chg_max:+.1f}(spike)")
            else:
                res = (True, f"learned_long_up_ok(chg{chg24:+.1f}∈[{chg_min:.0f},{chg_max:.0f}))")
        else:
            res = (True, f"learned_long_regime_{regime}_allow")
        _LONG_FEAT_CACHE[sym] = (now, res[0], res[1])
        return res
    except Exception as exc:
        logger.warning("[MidCircuit] learned 多头特征读取失败(fail-open) %s: %s", sym, exc)
        return True, f"learned_long_err_failopen:{str(exc)[:40]}"


# ── learned 空头准入特征缓存 ──
_SHORT_FEAT_CACHE: Dict[str, tuple] = {}
_SHORT_FEAT_TTL = 900.0  # 15 分钟


def _short_learned_ok(symbol: str) -> Tuple[bool, str]:
    """learned 空头准入：down regime + 分位≥60% + RSI∈[55,80] + chg24≤-1%。

    数据依据（第十五轮，test_short_entry_conditions.py）：
      - 原两条件（分位+RSI）时间切分后后段转负（-0.74%/72h）；
      - 加 chg24≤-1% 后前后段皆正（+1.01%/+1.07%），trail 0.71/0.76 胜率；
      - 语义：不追涨（历史追涨空头 72h -13.6%），只做「区间上沿已开始回落」。

    任一步数据不足 → fail-closed（不允许空头），与「down 全关」同保守。
    """
    from backend.services.kline_data_service import kline_service

    sym = str(symbol or "").upper()
    now = time.time()
    row = _SHORT_FEAT_CACHE.get(sym)
    if row and now - row[0] < _SHORT_FEAT_TTL:
        return row[1], row[2]
    try:
        raw = kline_service.get_aggregated_klines(sym, "1h", count=30)
        if not raw or len(raw) < 26:
            return False, "learned_short_no_data"
        closes = [float(r["close"]) for r in raw]
        highs = [float(r["high"]) for r in raw]
        lows = [float(r["low"]) for r in raw]
        win = list(zip(highs, lows))[-24:]
        hi = max(h for h, _ in win)
        lo = min(l for _, l in win)
        px = closes[-1]
        pos = (px - lo) / (hi - lo) * 100 if hi > lo else 50.0
        seg = closes[-15:]
        delta = [seg[k + 1] - seg[k] for k in range(len(seg) - 1)]
        gain = sum(max(d, 0) for d in delta) / len(delta)
        loss = sum(max(-d, 0) for d in delta) / len(delta)
        rsi = 100 - 100 / (1 + gain / loss) if loss > 0 else 100.0
        chg24 = (closes[-1] / closes[-25] - 1.0) * 100.0 if closes[-25] > 0 else 0.0
        _chg_max = _env_f("MIDLONG_SHORT_CHG24_MAX_PCT", -1.0)
        if pos < 60.0:
            res = (False, f"learned_short_pos{pos:.0f}<60")
        elif rsi < 55.0 or rsi > 80.0:
            res = (False, f"learned_short_rsi{rsi:.0f}∉[55,80]")
        elif chg24 > _chg_max:
            res = (False, f"learned_short_chg24_{chg24:+.1f}%>{_chg_max:+.1f}%")
        else:
            res = (True, f"learned_short_ok(pos{pos:.0f},rsi{rsi:.0f},chg24{chg24:+.1f}%)")
        _SHORT_FEAT_CACHE[sym] = (now, res[0], res[1])
        return res
    except Exception as exc:
        logger.debug("[MidCircuit] learned 空头特征读取失败 %s: %s", sym, exc)
        return False, f"learned_short_err:{str(exc)[:40]}"


def _short_bias_ok(market_summary: Optional[dict], symbol: str) -> bool:
    """conditional 模式的下行证据：orchestrator mid_bias=bearish 或 24h 跌≥2%。"""
    try:
        ms = (market_summary or {}).get(symbol) or {}
        if not isinstance(ms, dict):
            return False
        orch = ms.get("orchestrator") if isinstance(ms.get("orchestrator"), dict) else {}
        if str(orch.get("mid_bias") or "").strip().lower() == "bearish":
            return True
        chg24 = float(ms.get("price_change_24h_pct") or 0)
        if chg24 <= -0.02:
            return True
    except Exception:
        return False
    return False

_STATE_FILE = os.path.join("data", "midlong_circuit_state.json")
_state: Dict[str, dict] = {}
_loaded = False


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        if os.path.exists(_STATE_FILE):
            with open(_STATE_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                _state.update(d)
                logger.info("[MidCircuit] 已加载熔断状态 %d 条", len(d))
    except Exception as exc:
        logger.warning("[MidCircuit] 状态加载失败(按空状态启动): %s", exc)


def _save() -> None:
    try:
        os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
        _tmp = _STATE_FILE + ".tmp"
        with open(_tmp, "w", encoding="utf-8") as f:
            json.dump(_state, f, ensure_ascii=False, indent=2)
        os.replace(_tmp, _STATE_FILE)
    except Exception as exc:
        logger.warning("[MidCircuit] 状态落盘失败: %s", exc)


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _acct_key(account_id: Optional[int], symbol: str) -> str:
    return f"{int(account_id or 0)}:{(symbol or '').upper()}"


def _learned_paper_probe_enabled() -> bool:
    """[M4 2026-09-14] paper 下 learned 窄带闸从 hold 降级为缩仓探针。"""
    return _env_b("MIDLONG_LEARNED_PAPER_PROBE", True)


def _learned_paper_probe_mult() -> float:
    try:
        v = float(os.getenv("MIDLONG_LEARNED_PAPER_PROBE_MULT", "0.25") or 0.25)
        return max(0.05, min(1.0, v))
    except (TypeError, ValueError):
        return 0.25


def _paper_probe_reason(orig: str, mult: float) -> str:
    """带稳定标记的探针放行 reason：调用方（midlong_executor）据此乘 margin。"""
    return f"paper_probe×{mult:.2f}: {orig}"


def check_midlong_entry(
    account_id: Optional[int],
    symbol: str,
    side: str = "",
    tier: str = "mid",
    market_summary: Optional[dict] = None,
) -> Tuple[bool, str]:
    """中长线新开仓检查。返回 (allowed, reason)；fail-open（异常放行）。

    [2026-09-09 二次修订] 空头策略默认 `regime_gated`：仅日线确认下行趋势
    （收盘<EMA200 且 60 日动量<-5%）允许开空（down 空头按 `MIDLONG_DOWN_SHORT_MODE`
    三条件 learned 准入）。off/conditional/on 保留为回滚档。

    [2026-09-09 第十六轮] 多头独立治理（`MIDLONG_LONG_MODE`，默认 learned）：
    down 拦 + up 需 chg24≥3% + chop 需 pos24≥60% 且 chg24≥2%（见 `_long_mode`）。

    [M4 2026-09-14] learned 窄带闸 paper 探针：模拟账户（loss_locks_disabled）
    命中 learned 窄带否决时不再 hold，改为缩仓×0.25 放行（reason 带
    `paper_probe×` 标记，midlong_executor 据此乘 margin）。理由：learned 窄带
    （chg24∈[3,6) 等）是按旧策略样本标定，paper 的使命是收集当前策略新样本；
    regime 方向性硬拦（down 禁多 / 非 down 禁空 / short off）保持原样。
    回滚：MIDLONG_LEARNED_PAPER_PROBE=false。
    """
    try:
        if not _env_b("MIDLONG_CIRCUIT_ENABLED", True):
            return True, ""
        _load()
        side_l = (side or "").lower()
        _sym_u = (symbol or "").upper()
        # paper 探针开关：与亏损锁禁用同一判据（模拟账户）
        try:
            from backend.services.risk_management.loss_lock_policy import loss_locks_disabled as _lld
            _paper_probe = bool(_lld(account_id)) and _learned_paper_probe_enabled()
        except Exception:
            _paper_probe = False
        if side_l in ("sell", "short"):
            _mode = _short_mode()
            if _mode == "off":
                return False, "midlong_short_off: mid空头全停(MIDLONG_SHORT_MODE=off)"
            # [2026-09-08] 日内波段(short 档)豁免下行证据闸：日内空是均值回归/超买摸顶，
            # 等 4h 趋势确认就错过了；主脑 accepted 空头论题本身就是证据。
            # 熔断闸（连亏3→12h+日亏帽）在下方照常生效托底。
            _is_intraday = str(tier or "").lower() == "short"
            if _mode == "regime_gated" and not _is_intraday:
                _reg = _daily_regime(_sym_u)
                if not _reg:
                    return False, (
                        "midlong_short_regime_unknown: 日线 regime 不可判（数据不足），"
                        "mid/long 空头 fail-closed"
                    )
                if _reg != "down":
                    return False, (
                        f"midlong_short_regime_block: 日线 regime={_reg}（非下行），"
                        f"mid/long 空头仅在下行趋势放行"
                    )
                # [第十五轮] down-regime 空头按模式准入：
                #   learned（默认）：分位≥60% + RSI∈[55,80] + chg24≤-1%
                #     （时间切分前后段 +1.01%/+1.07%，唯一两段皆正）
                #   flat：全关；allowed：无条件（旧口径）
                _dsm = _down_short_enabled()
                if _dsm == "flat":
                    return False, (
                        "midlong_short_down_flat: 日线 regime=down，但 down 空头开关关闭"
                        "（MIDLONG_DOWN_SHORT_MODE=learned/allowed 恢复）"
                    )
                if _dsm == "learned":
                    _ok, _why = _short_learned_ok(_sym_u)
                    if not _ok:
                        if _paper_probe:
                            return True, _paper_probe_reason(
                                f"midlong_short_learned_block: {_why}",
                                _learned_paper_probe_mult(),
                            )
                        return False, f"midlong_short_learned_block: {_why}"
            elif _mode == "conditional" and not _is_intraday and not _short_bias_ok(market_summary, _sym_u):
                return False, (
                    "midlong_short_no_bias: mid空头需下行证据(4h偏空或24h跌≥2%),"
                    "熔断闸(连亏3→12h+日亏帽)兜底"
                )
        elif side_l in ("buy", "long"):
            # [2026-09-09 第十六轮] 多头独立治理（MIDLONG_LONG_MODE）；
            # [第十七轮] up 加 spike 上界；[第十八轮] chop 回滚为 pos≥60+chg≥2；
            # [第二十一轮] learned 特征闸**只作用于 mid 层**（见 `_long_learned_tiers`）
            if str(tier or "").lower() != "short":
                _lm = _long_mode()
                if _lm != "allow_all":
                    _reg = _daily_regime(_sym_u)
                    if _reg == "down":
                        return False, (
                            "midlong_long_regime_block: 日线 regime=down（下行趋势），"
                            "mid/long 多头在下行趋势禁开（实测 down-long 14d -0.618%/t=-4.09）"
                        )
                    if _lm == "learned" and _reg in ("up", "chop") and _tier_in_learned(tier):
                        _ok, _why = _long_learned_ok(_sym_u, _reg)
                        if not _ok:
                            if _paper_probe:
                                return True, _paper_probe_reason(
                                    f"midlong_long_learned_block: {_why}",
                                    _learned_paper_probe_mult(),
                                )
                            return False, f"midlong_long_learned_block: {_why}"
        # [2026-09-09 第十轮] chop 空仓开关（默认 long_only，置 flat 启用）
        if _chop_flat_enabled() and str(tier or "").lower() != "short" and side_l in ("buy", "long", "sell", "short"):
            _reg_c = _daily_regime(_sym_u)
            if _reg_c == "chop":
                return False, (
                    "midlong_chop_flat: 日线 regime=chop（震荡），chop 空仓开关已启用"
                    "（实测 chop 期间 85 笔 -53.66）"
                )
        # [2026-09-11 用户指令] **模拟账户不做亏损冻结**：
        # 「模拟账户本来就是收集交易数据，你还弄个极端亏损冻结？」
        # 纸面连亏/日亏 = 训练数据，不是资金损失。策略质量闸（regime /
        # learned 条件 / 位置闸 / chop）在上面已照常生效，只跳过下面这段
        # 纯亏损触发的 (account,symbol) 冷却与日亏帽。
        # 判据唯一权威在 risk_management/loss_lock_policy（paper 默认禁用）。
        from backend.services.risk_management.loss_lock_policy import loss_locks_disabled

        if loss_locks_disabled(account_id):
            return True, ""
        key = _acct_key(account_id, symbol)
        st = _state.get(key)
        if not st:
            return True, ""
        now = time.time()
        banned_until = float(st.get("banned_until", 0) or 0)
        if banned_until and now < banned_until:
            remain_min = int((banned_until - now) / 60)
            return False, (
                f"mid_circuit_banned: {symbol} 连亏{int(st.get('consec_losses', 0))}笔"
                f"/日亏{st.get('day_pnl', 0):.1f} 冷却剩余{remain_min}min"
            )
        if banned_until and now >= banned_until:
            st["banned_until"] = 0
            st["consec_losses"] = 0
        return True, ""
    except Exception as exc:
        logger.warning("[MidCircuit] 检查异常(fail-open): %s", exc)
        return True, ""


def record_midlong_outcome(
    account_id: Optional[int],
    symbol: str,
    net_pnl: float,
) -> None:
    """平仓后记录净盈亏（调用方传 net=pnl-fee），更新熔断状态。

    [轮120 2026-09-19 **用户拍板恢复**] 原注释写着
    「[2026-09-11 用户指令] 模拟账户直接返回：不做任何亏损触发的冷却/熔断记账」
    —— 后果实测：状态文件 `data/midlong_circuit_state.json` **9 天没写**
    （mtime 09-10 20:48），今天 UNI 连吃 3 个 SL（−13.43）也没有得到"单币 12h 冷却"。
    用户 2026-09-19 明确口径：「单币亏钱，就是冻结单个亏钱的币」⇒ 恢复记账。
    **粒度不变**：只按 (account, symbol) 记账与禁开，**不引入任何全局冻结**
    （全局只在 `tier_circuit_breaker` 日亏预算 / 极端 regime 触发）。
    回滚：`MIDLONG_CIRCUIT_PAPER_LOCK=false` → 回到 09-11 的"paper 不记账"。
    """
    try:
        if not _env_b("MIDLONG_CIRCUIT_ENABLED", True):
            return
        # [轮120] paper 恢复记账；显式开关保留回滚能力
        if not _env_b("MIDLONG_CIRCUIT_PAPER_LOCK", True):
            from backend.services.risk_management.loss_lock_policy import loss_locks_disabled

            if loss_locks_disabled(account_id):
                return
        _load()
        key = _acct_key(account_id, symbol)
        today = _today()
        st = _state.setdefault(
            key, {"consec_losses": 0, "day": today, "day_pnl": 0.0, "banned_until": 0}
        )
        # 跨天重置日累计
        if st.get("day") != today:
            st["day"] = today
            st["day_pnl"] = 0.0
        st["day_pnl"] = round(float(st.get("day_pnl", 0.0)) + float(net_pnl or 0), 4)
        now = time.time()
        # [2026-08-31 根治] 冷却到期即在记录侧清零，不再依赖"下次开仓检查"才重置。
        _banned_until = float(st.get("banned_until", 0) or 0)
        if _banned_until and now >= _banned_until:
            st["banned_until"] = 0
            st["consec_losses"] = 0
        # 本次平仓时是否仍在冷却中（到期清零后为 False）
        _in_cooldown = float(st.get("banned_until", 0) or 0) > now
        if float(net_pnl or 0) < 0:
            st["consec_losses"] = int(st.get("consec_losses", 0)) + 1
            # [2026-08-31 根治] 冷却期内继续亏损不再顺延 12h——否则亏损不止、
            # 熔断永不解除（实测 BNB 连亏 98 笔被反复续期）。连亏计数仍保留，
            # 但 ban 时长封顶为"触发时 + COOLDOWN_S"；日亏帽仍可延长到次日。
            if st["consec_losses"] >= CONSEC_LOSSES_LIMIT and not _in_cooldown:
                st["banned_until"] = now + COOLDOWN_S
                logger.warning(
                    "[MidCircuit] 🚫 %s 连续亏损 %d 笔 → 冷却 %dh",
                    key, st["consec_losses"], COOLDOWN_S // 3600,
                )
        else:
            st["consec_losses"] = 0
        # 日亏上限（跨过即熔断到次日 00:00 本地）
        if DAILY_LOSS_CAP > 0 and float(st["day_pnl"]) <= -abs(DAILY_LOSS_CAP):
            try:
                _next_day = time.mktime(time.strptime(today, "%Y-%m-%d")) + 86400
                st["banned_until"] = max(float(st.get("banned_until", 0) or 0), _next_day)
                logger.warning(
                    "[MidCircuit] 🚫 %s 当日累计净亏 %.2f ≤ -%.0f → 熔断至次日",
                    key, float(st["day_pnl"]), abs(DAILY_LOSS_CAP),
                )
            except Exception:
                st["banned_until"] = now + COOLDOWN_S
        _save()
    except Exception as exc:
        logger.debug("[MidCircuit] 结果记录异常: %s", exc)
