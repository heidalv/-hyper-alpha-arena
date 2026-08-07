# 动态止盈止损系统 — 全面改进设计稿

> 生成时间: 2026-04-02
> 影响范围: `paper_trading_engine.py`, `dynamic_sl_tp.py`, `risk_control_service.py`, `trading_analysts.py`
> 状态: **设计阶段，待审批后实施**

---

## 一、现状问题诊断

### 1.1 核心矛盾

当前 `paper_trading_engine.py` 的追踪止损系统存在以下结构性缺陷：

| # | 问题 | 现状 | 影响 |
|---|------|------|------|
| P1 | **追踪止损激活过早** | swing+BTC(low) 激活阈值仅 1.2%，追踪距离 0.8% | 微利即被短期波动洗出 |
| P2 | **无视 AI 止盈目标** | 追踪止损与 AI 设定的 TP/SL 完全独立 | AI 设 TP=+5% 但追踪止损在 +1.5% 就触发 |
| P3 | **止损后立即重开** | 平仓后冷却机制仅 20 分钟且不够可靠 | 刚平又开，刷手续费 |
| P4 | **保本止损推进后仍然亏损** | 保本推进激活于 +1.2%，但推进后价格正常回踩就被止损 | 盈利 $200 → 被止损亏 $130 |
| P5 | **分批止盈阈值固定** | `_TP_SAFETY_NET_BY_NATURE` 仅按 nature 分类，不看实际仓位盈亏 | 不灵活，无法根据实际利润调整 |
| P6 | **缺乏支撑/压力位参考** | 止损止盈完全基于百分比计算 | 在关键技术位附近被反复止损 |

### 1.2 根因分析

```
paper_trading_engine.update_all_positions()
    ├── 保本止损推进 (_BREAKEVEN_BY_NATURE)     ← 固定百分比，太敏感
    ├── 追踪止损 (_TRAILING_BY_NATURE)          ← 激活太早、距离太紧
    ├── SL 检查                                  ← 被保本推进后的太紧 SL 打掉
    ├── TP 检查                                  ← AI 设的 TP 到了才触发
    └── 追踪止损触发                              ← 利润低就触发，无视 TP
```

**核心矛盾**: 系统级止损（机械百分比）和 AI 级止损（基于分析判断）**互相冲突**。

---

## 二、改进设计

### 2.1 设计原则

1. **AI 意图优先** — 当 AI 明确设定了 TP/SL 时，系统级保护不应提前触发
2. **分层递进** — 保本止盈 → 分批止盈 → 移动止盈，利润越大保护越紧
3. **技术位感知** — 利用支撑/压力位作为止损止盈参考
4. **账户规模适配** — 阈值根据账户资金动态调整
5. **回撤保护** — 基于最高浮盈的百分比回撤保护，而非固定距离

### 2.2 整体架构

```
┌─────────────────────────────────────────────────────┐
│               动态止盈止损引擎 v2                       │
├─────────────────────────────────────────────────────┤
│                                                       │
│  Layer 1: 风控底线（硬性）                              │
│  ├── 爆仓保护（liquidation_price）                     │
│  ├── 日亏损熔断（risk_control_service）                │
│  └── 确定性风控门（deterministic_risk_gate）            │
│                                                       │
│  Layer 2: AI 意图层（软性，AI 可覆盖）                   │
│  ├── AI 设定的 TP 价格                                 │
│  ├── AI 设定的 SL 价格                                 │
│  └── AI partial_close_pct 指令                         │
│                                                       │
│  Layer 3: 利润保护层（渐进式，基于实际浮盈）              │
│  ├── Level 0: 浮盈 < TP×30%  → 不做任何保护            │
│  ├── Level 1: 浮盈 ≥ TP×30%  → 保本止损（SL 推到入场）  │
│  ├── Level 2: 浮盈 ≥ TP×50%  → 锁利 50%（分批止盈 30%）│
│  ├── Level 3: 浮盈 ≥ TP×70%  → 锁利 70%（分批止盈 30%）│
│  ├── Level 4: 浮盈 ≥ TP×90%  → 紧追踪（回撤 1.5% 平仓）│
│  └── Level 5: 浮盈 ≥ TP     → 正常止盈                 │
│                                                       │
│  Layer 4: 回撤保护（利润不回吐超限）                     │
│  ├── 峰值利润追踪（peak unrealized PnL）               │
│  ├── 最大回撤允许: 30% of peak（可配置）                 │
│  └── 触发时 close 剩余仓位                              │
│                                                       │
│  Layer 5: 技术位参考（增强精度）                        │
│  ├── K线支撑位 → SL 不得低于支撑位                     │
│  ├── K线压力位 → TP 参考压力位                          │
│  └── 无数据时回退到百分比计算                           │
│                                                       │
└─────────────────────────────────────────────────────┘
```

---

## 三、详细设计

### 3.1 利润保护层（替代现有追踪止损）

**文件**: `paper_trading_engine.py` — `update_all_positions()` 重构

#### 3.1.1 利润保护等级表

```python
_PROFIT_PROTECTION_LEVELS = {
    # profit_progress: 0.0~1.0 (浮盈/TP距离)
    # action: 保护动作
    #   "none"        - 不做任何事
    #   "breakeven"   - SL 推到入场价 + buffer
    #   "lock_30"     - 分批平仓 30%，剩余 SL 推到锁利位
    #   "lock_50"     - 分批平仓到只剩 50%
    #   "tight_trail" - 紧追踪止损 (1.5% 回撤)
    #   "tp"          - 全平止盈
    
    "level_0": {"min_progress": 0.00, "max_progress": 0.30, "action": "none"},
    "level_1": {"min_progress": 0.30, "max_progress": 0.50, "action": "breakeven"},
    "level_2": {"min_progress": 0.50, "max_progress": 0.70, "action": "lock_30"},
    "level_3": {"min_progress": 0.70, "max_progress": 0.90, "action": "lock_50"},
    "level_4": {"min_progress": 0.90, "max_progress": 0.99, "action": "tight_trail"},
    "level_5": {"min_progress": 1.00, "max_progress": 9.99, "action": "tp"},
}
```

#### 3.1.2 利润进度计算

```python
def _calc_profit_progress(self, pos) -> float:
    """计算当前利润相对于 TP 目标的进度 (0.0 ~ 1.0+)"""
    
    # 没有 TP 时回退到基于百分比的评估
    if not pos.tp_price or not pos.entry_price:
        # 使用 nature+vol 对应的追踪激活阈值作为 100% 基准
        return profit_pct / trail_activation if trail_activation > 0 else 0
    
    entry = float(pos.entry_price)
    tp = float(pos.tp_price)
    
    if pos.side == "long":
        current = float(pos.mark_price)
        tp_distance = tp - entry
    else:
        current = float(pos.mark_price)
        tp_distance = entry - tp
    
    if tp_distance <= 0:
        return 0
    
    if pos.side == "long":
        progress = (current - entry) / tp_distance
    else:
        progress = (entry - current) / tp_distance
    
    return max(0, progress)
```

#### 3.1.3 保护动作执行逻辑

```python
def _apply_profit_protection(self, pos, profit_progress, profit_pct):
    """根据利润进度执行保护动作"""
    
    level = self._get_protection_level(profit_progress)
    
    if level["action"] == "none":
        return  # 不管，让利润跑
    
    if level["action"] == "breakeven":
        # 仅推 SL 到入场价 + 0.1% buffer
        # 不触发追踪止损
        buffer_price = entry * 1.001 if side == "long" else entry * 0.999
        if current_sl is None or buffer better than current_sl:
            pos.sl_price = buffer_price
            pos.trailing_stop_price = None  # 清除追踪止损
    
    if level["action"] == "lock_30":
        # 如果还没执行过 lock_30
        if not state.get("lock_30_done"):
            # 分批平仓 30%
            self._partial_close_by_pct(pos, 0.30, reason="profit_lock_30")
            state["lock_30_done"] = True
        # SL 推到锁利位（保证至少锁住已实现利润的 50%）
        # trailing 设为 tight
    
    if level["action"] == "tight_trail":
        # 紧追踪：仅允许 1.5% 回撤
        # 注意：这里用的是绝对百分比，不是 TP 进度百分比
        ...
    
    if level["action"] == "tp":
        # 正常止盈
        self.close_position(db, pos.account_id, pos.symbol, pos.side, reason="tp")
```

### 3.2 回撤保护机制（新增）

**核心思想**: 不再基于固定追踪距离，而是基于**利润回撤比例**。

```python
@dataclass
class DrawdownProtection:
    """利润回撤保护配置"""
    # 当浮盈达到多少后开始追踪峰值（美元）
    activation_threshold_usd: float = 50.0
    
    # 最大允许回撤（占峰值的百分比）
    max_drawdown_from_peak: float = 0.30  # 30%
    
    # 紧急止损：回撤超过此值立即全平
    emergency_drawdown: float = 0.50  # 50%
    
    # 回撤计算周期（秒），避免瞬态价格触发
    lookback_seconds: float = 30.0
```

**执行逻辑**:
```python
def _check_drawdown_protection(self, pos, current_price):
    """利润回撤保护"""
    
    # 1. 计算当前总浮盈（含已部分平仓的利润）
    current_profit = self._calc_total_profit(pos, current_price)
    
    # 2. 更新峰值利润
    state["peak_profit"] = max(state.get("peak_profit", 0), current_profit)
    
    # 3. 利润不足时不激活
    if state["peak_profit"] < activation_threshold_usd:
        return
    
    # 4. 计算回撤
    drawdown = (state["peak_profit"] - current_profit) / state["peak_profit"]
    
    # 5. 回撤超限 → 全平保护
    if drawdown >= max_drawdown_from_peak:
        self.close_position(...)
        logger.info(f"利润回撤保护触发: peak=${peak:.2f}, "
                     f"current=${current:.2f}, drawdown={drawdown:.1%}")
```

**示例**:
- BTC 多单浮盈最高达 $200 (peak)
- 允许回撤 30%: $200 × 30% = $60
- 当浮盈跌到 $200 - $60 = $140 时触发保护 → 全平
- 最终利润: **$140**（而非原来被追踪止损打到亏 $130）

### 3.3 支撑/压力位集成

**数据来源**: K线分析结果（已有的 `kline_analyst` 输出）

```python
def _get_technical_levels(self, symbol: str) -> dict:
    """获取技术位参考"""
    # 从最近一次 K线分析结果中提取
    # 数据源: KlineAnalyst 的 report 中包含 support/resistance
    
    return {
        "nearest_support": float,   # 最近支撑位
        "nearest_resistance": float, # 最近压力位
        "support_strength": str,     # "strong" / "medium" / "weak"
        "resistance_strength": str,
    }
```

**使用方式**:
1. **止损优化**: SL 不低于最近强支撑位（long）/ 不高于最近强阻力位（short）
2. **止盈参考**: TP 可参考压力位，避免设在压力位之下
3. **追踪止损优化**: 追踪价不低于支撑位，避免被支撑位附近的正常波动触发

### 3.4 账户规模适配

**问题**: $100,000 账户的 $200 利润（0.2%）和 $1,000 账户的 $200 利润（20%）需要不同的保护策略。

```python
def _adapt_thresholds_by_account(self, account_equity: float, pos) -> dict:
    """根据账户规模调整阈值"""
    
    equity_usd = account_equity
    
    # 利润金额阈值（美元）
    if equity_usd >= 100000:
        # 大账户：利润保护激活阈值更高
        return {
            "breakeven_min_profit_usd": 200,    # 盈利 > $200 才推保本
            "lock_30_min_profit_usd": 500,      # 盈利 > $500 才锁利 30%
            "lock_50_min_profit_usd": 1000,     # 盈利 > $1000 才锁利 50%
            "trail_min_profit_usd": 2000,       # 盈利 > $2000 才启动紧追踪
        }
    elif equity_usd >= 10000:
        return {
            "breakeven_min_profit_usd": 50,
            "lock_30_min_profit_usd": 150,
            "lock_50_min_profit_usd": 300,
            "trail_min_profit_usd": 500,
        }
    else:
        return {
            "breakeven_min_profit_usd": 20,
            "lock_30_min_profit_usd": 50,
            "lock_50_min_profit_usd": 100,
            "trail_min_profit_usd": 200,
        }
```

### 3.5 分层止盈机制（替代现有固定百分比 TP）

**现有问题**: `_TP_SAFETY_NET_BY_NATURE` 是固定百分比，不看实际利润。

**改进方案**:

```python
# 利润保护分批止盈规则（基于实际利润金额 + TP 进度双条件）
_PROFIT_LOCK_RULES = {
    # lock_step: (tp_progress_min, min_profit_usd, close_pct, new_sl_progress)
    "step_1": {
        "tp_progress": 0.50,    # TP 进度 ≥ 50%
        "min_profit_usd": 50,   # 且利润 ≥ $50
        "close_pct": 0.25,      # 平 25%
        "set_sl_to": 0.30,      # SL 推到利润 30% 处
        "reason": "profit_lock_1",
    },
    "step_2": {
        "tp_progress": 0.70,
        "min_profit_usd": 100,
        "close_pct": 0.25,      # 再平 25%（累计 50%）
        "set_sl_to": 0.50,      # SL 推到利润 50% 处
        "reason": "profit_lock_2",
    },
    "step_3": {
        "tp_progress": 0.90,
        "min_profit_usd": 200,
        "close_pct": 0.25,      # 再平 25%（累计 75%）
        "set_sl_to": 0.70,      # SL 推到利润 70% 处
        "reason": "profit_lock_3",
    },
}
```

### 3.6 与 risk_control_service 的协调

**原则**: `risk_control_service.py` 管账户级风控（开仓前检查），`paper_trading_engine.py` 管持仓级保护（开仓后管理）。两者职责清晰分离。

```
risk_control_service (开仓前)          paper_trading_engine (开仓后)
├── 日亏损熔断                          ├── 利润保护层 (新增)
├── 单币种仓位限制                      ├── 回撤保护 (新增)
├── 保证金使用率                        ├── 分批止盈 (改进)
├── 确定性风控门                        ├── 支撑/压力位参考 (新增)
├── 连续亏损保护                        └── TP 目标达成止盈
└── 杠杆限制

协调接口:
- risk_control_service 提供 daily_loss_limit / consecutive_loss 数据
- paper_trading_engine 读取这些数据来调整保护阈值
  (例如：连续亏损 3 次后，追踪止损更紧；日亏损接近熔断线，保本更激进)
```

**新增协调逻辑**:

```python
def _get_risk_adjusted_thresholds(self, db, account_id) -> dict:
    """从风控服务获取风险状态，调整保护参数"""
    
    # 读取当日已实现亏损
    daily_loss_pct = self._get_daily_loss_pct(db, account_id)
    
    # 读取连续亏损次数
    consecutive_losses = self._get_consecutive_losses(db, account_id)
    
    adjustments = {}
    
    if daily_loss_pct > 0.03:  # 日亏 > 3%
        # 接近熔断线，保护更激进
        adjustments["breakeven_activation_multiplier"] = 0.5  # 激活阈值减半
        adjustments["max_drawdown_from_peak"] = 0.20          # 回撤容忍降低
    
    if consecutive_losses >= 3:
        # 连亏模式，更保守
        adjustments["lock_early"] = True                       # 提前锁利
        adjustments["tight_trail_distance_pct"] = 0.01         # 追踪更紧
    
    return adjustments
```

---

## 四、`dynamic_sl_tp.py` 改进设计

### 4.1 当前问题

`dynamic_sl_tp.py` 定义了 `DynamicStopManager`，但 **目前几乎没被 paper_trading_engine 调用**。两个系统完全独立：

- `dynamic_sl_tp.py`: 有 ATR 追踪、支撑/阻力位、分批止盈的框架
- `paper_trading_engine.py`: 自己实现了一套固定百分比的追踪止损

### 4.2 改进方案

将 `dynamic_sl_tp.py` 作为**止盈止损计算引擎**，`paper_trading_engine.py` 调用它来获取 SL/TP/追踪价格，而非自己硬编码。

```python
# paper_trading_engine.py 调用示例
def update_all_positions(self, db):
    from backend.services.adaptive_executor.dynamic_sl_tp import get_stop_manager
    
    for pos in open_positions:
        manager = get_stop_manager()
        
        # 1. 计算利润进度
        profit_progress = self._calc_profit_progress(pos)
        
        # 2. 获取当前保护等级
        protection = manager.get_protection_level(
            entry_price=float(pos.entry_price),
            current_price=current_price,
            side=pos.side,
            atr=atr,                          # 从 K线数据获取
            profit_progress=profit_progress,
            peak_profit=state.get("peak_profit", 0),
            account_equity=total_equity,
            technical_levels=self._get_technical_levels(pos.symbol),
        )
        
        # 3. 执行保护动作
        if protection.action == "breakeven":
            pos.sl_price = protection.sl_price
            pos.trailing_stop_price = None
        elif protection.action == "partial_close":
            self._partial_close_by_pct(pos, protection.close_pct, protection.reason)
        elif protection.action == "close":
            self.close_position(db, ...)
```

### 4.3 DynamicStopManager 新增接口

```python
class ProtectionAction(Enum):
    NONE = "none"
    BREAKEVEN = "breakeven"          # 推 SL 到保本
    PARTIAL_CLOSE = "partial_close"  # 分批止盈
    TIGHT_TRAIL = "tight_trail"      # 紧追踪
    CLOSE = "close"                  # 全平

@dataclass
class ProtectionResult:
    action: ProtectionAction
    sl_price: Optional[float]        # 新 SL 价格
    trailing_price: Optional[float]  # 新追踪价格
    close_pct: Optional[float]       # 分批平仓比例
    reason: str

class DynamicStopManager:
    def get_protection_level(
        self,
        entry_price: float,
        current_price: float,
        side: str,
        atr: float,
        profit_progress: float,       # 0.0~1.0+
        peak_profit: float,           # 峰值浮盈 ($)
        account_equity: float,        # 账户权益
        technical_levels: dict = None, # 支撑/压力位
        consecutive_losses: int = 0,   # 连续亏损次数
    ) -> ProtectionResult:
        """综合计算当前应执行的保护动作"""
        ...
```

---

## 五、实施计划

### Phase 1: 核心保护机制（优先级最高）

| 步骤 | 内容 | 文件 | 风险 |
|------|------|------|------|
| 1.1 | 实现利润进度计算 (`_calc_profit_progress`) | paper_trading_engine.py | 低 |
| 1.2 | 实现利润保护等级表 + 执行逻辑 | paper_trading_engine.py | 中 |
| 1.3 | 实现回撤保护机制 | paper_trading_engine.py | 中 |
| 1.4 | 替换现有追踪止损为新的保护层 | paper_trading_engine.py | **高** |
| 1.5 | 清除旧追踪止损残留数据 | paper_trading_engine.py | 低 |

### Phase 2: 技术位集成

| 步骤 | 内容 | 文件 | 风险 |
|------|------|------|------|
| 2.1 | 从 KlineAnalyst 提取支撑/压力位接口 | paper_trading_engine.py | 中 |
| 2.2 | SL/TP 参考技术位优化 | paper_trading_engine.py | 低 |
| 2.3 | 追踪止损尊重支撑位 | paper_trading_engine.py | 低 |

### Phase 3: 账户规模适配 + 风控协调

| 步骤 | 内容 | 文件 | 风险 |
|------|------|------|------|
| 3.1 | 账户规模自适应阈值 | paper_trading_engine.py | 低 |
| 3.2 | 读取风控状态调整保护参数 | paper_trading_engine.py + risk_control_service.py | 中 |
| 3.3 | 连续亏损时自动调整保护策略 | paper_trading_engine.py | 低 |

### Phase 4: DynamicStopManager 整合

| 步骤 | 内容 | 文件 | 风险 |
|------|------|------|------|
| 4.1 | 扩展 DynamicStopManager 接口 | dynamic_sl_tp.py | 中 |
| 4.2 | paper_trading_engine 调用 DynamicStopManager | paper_trading_engine.py | **高** |
| 4.3 | 单元测试覆盖 | tests/ | 低 |

### Phase 5: 测试验证

| 步骤 | 内容 |
|------|------|
| 5.1 | 回测验证: 对比新旧策略在历史数据上的表现 |
| 5.2 | 纸面交易观察: 上线后观察 48 小时 |
| 5.3 | 参数微调: 根据实际表现调整阈值 |

---

## 六、关键参数对照

### 6.1 追踪止损参数变更

| 参数 | 旧值 (swing+BTC) | 新值 | 说明 |
|------|-------------------|------|------|
| 激活阈值 | 1.2% price move | TP进度 × 30% | 基于 AI TP 目标而非固定百分比 |
| 追踪距离 | 0.8% | 回撤保护 30% of peak profit | 基于利润回撤而非价格距离 |
| 紧追踪距离 | 0.5% (tight) | 1.5% absolute from peak | 更宽容的回撤空间 |
| 保本推进 | +1.2% profit | TP进度 × 30% | 更晚推进，避免被洗 |

### 6.2 新增参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `max_drawdown_from_peak` | 0.30 (30%) | 峰值利润最大回撤比例 |
| `emergency_drawdown` | 0.50 (50%) | 紧急全平回撤比例 |
| `activation_threshold_usd` | $50 | 回撤保护激活最低利润 |
| `breakeven_min_profit_usd` | $50~$200 (按账户) | 保本推进最低利润 |
| `lock_1_tp_progress` | 0.50 | 第一批锁利 TP 进度 |
| `lock_2_tp_progress` | 0.70 | 第二批锁利 TP 进度 |

---

## 七、回滚方案

如果新系统上线后出现问题：
1. 新增环境变量 `PROFIT_PROTECTION_VERSION=v1|v2`
2. `v1` = 旧系统（现有追踪止损），`v2` = 新系统
3. 默认 `v2`，可随时切回 `v1`
4. 旧代码不删除，保留为 fallback

---

## 八、预期效果

| 场景 | 旧行为 | 新行为 |
|------|--------|--------|
| BTC 多单浮盈 $200，正常回调 $60 | 追踪止损触发，盈利变为 $0 或亏损 | 回撤 30% of $200 = $60，未超限，继续持仓 |
| 浮盈 $200 回调到 $130 | 可能被止损 | 回撤 35% > 30%，触发保护平仓，**锁定 $130 利润** |
| 浮盈 $50 时短期波动 | 保本止损推进后被打掉 | 利润 < $50 阈值，不推进保本，让利润跑 |
| AI 设 TP=$70,000 但浮盈到 $68,000 | 追踪止损可能在 $66,000 触发 | TP 进度 80%，执行 lock_50 保护，至少锁住一半利润 |
| 连续亏损 3 次后 | 保护参数不变 | 自动收紧保护，更早锁利 |

---

*设计稿结束。待用户确认后进入实施阶段。*
