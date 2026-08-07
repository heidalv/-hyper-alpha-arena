# AlphaArena Mobile — 移动端前端设计提示词

## 项目概述

为 Hyper Alpha Arena 量化交易系统开发一个独立的移动端前端应用，专门针对平板和手机设备。
该应用与现有的桌面端前端完全独立，共用同一个后端 API（FastAPI，端口8000）。
通过 Tailscale 组网，移动设备可以远程访问运行在 Mac 上的后端服务。

项目位置：`/Users/laobao/项目/claude/001-02Alpha/001Alpha/Hyper-Alpha-Arena/mobile/`

---

## 技术栈

- React 18 + TypeScript
- Vite 5（独立 vite.config.ts）
- Tailwind CSS 3（深色主题，交易终端风格）
- lightweight-charts（K线/权益曲线图表）
- 原生 fetch API（不使用 axios）
- PWA（vite-plugin-pwa，支持"添加到主屏幕"）

---

## 整体设计风格

### 视觉风格：专业交易终端
- 深色背景（#0a0e17 为主背景，#111827 为卡片背景）
- 绿色=盈利（#00dc82），红色=亏损（#ef4444）
- 蓝色=交互主色（#3b82f6），灰色=次要文字（#6b7280）
- 字体：系统默认 -apple-system, sans-serif
- 圆角：卡片 12px，按钮 8px
- 间距紧凑但触摸友好（最小触摸目标 44x44px）

### 布局原则
- 底部 Tab 导航（4个主Tab），不使用侧边栏
- 内容区域可垂直滚动，关键信息"首屏可见"
- 大卡片布局，避免密集表格
- 所有可操作元素最小 44px 高度
- 底部弹出面板（BottomSheet）替代弹窗

---

## 页面结构

### 整体布局（MobileShell）

```
┌──────────────────────────┐
│     顶部状态栏            │  ← 连接状态 + 账户名
├──────────────────────────┤
│                          │
│     页面内容区域          │  ← 根据底部Tab切换
│     （可垂直滚动）        │
│                          │
├──────────────────────────┤
│  监控 | 交易 | 自动 | 策略 │  ← 底部Tab导航栏
└──────────────────────────┘
```

### 底部导航栏（BottomNavBar）

4个Tab：
1. **监控**（图标：Activity） — 实时监控面板
2. **交易**（图标：CandlestickChart） — 持仓与交易
3. **自动**（图标：PlayCircle） — FullAuto 控制
4. **策略**（图标：Layers） — 策略信号管理

当前激活Tab高亮显示（蓝色图标+文字），未激活灰色。
底部导航固定在视口底部，不随页面滚动。

---

## 页面详细设计

### Tab 1: 监控页（MonitorPage）

#### 顶部：账户概览卡片（AccountCard）
```
┌──────────────────────────┐
│  总权益                    │
│  $543,796.45              │  ← 大字，白色
│  ▲ +$13,506 (+2.54%)     │  ← 绿色盈利 / 红色亏损
│                           │
│  可用资金        持仓价值   │
│  $458,273       $85,523   │  ← 灰色较小字
└──────────────────────────┘
```

#### 中部：权益曲线（EquityChart）
- 使用 lightweight-charts 绘制面积图
- 显示最近24小时权益变化
- 深色背景，绿色面积填充
- 支持手指拖拽和缩放
- 高度约 200px

#### 底部：持仓 Symbol 网格（SymbolGrid）
```
┌──────────┐  ┌──────────┐
│ BTC      │  │ ETH      │
│ ▲ +2.3%  │  │ ▼ -1.2%  │  ← 盈亏百分比 + 颜色
│ Long 5x  │  │ Short 3x │  ← 方向 + 杠杆
└──────────┘  └──────────┘
┌──────────┐  ┌──────────┐
│ SOL      │  │ DOGE     │
│ ▲ +5.1%  │  │ ▼ -0.8%  │
│ Long 10x │  │ Long 5x  │
└──────────┘  └──────────┘
```
- 2列网格布局
- 每个卡片可点击跳转到交易页该symbol
- 盈利卡片左边框绿色，亏损红色
- 实时通过 WebSocket 更新价格和盈亏

---

### Tab 2: 交易页（TradingPage）

#### 持仓列表（PositionList）
- 列表式布局，每个持仓一个卡片
- 支持左滑操作（SwipeAction）：露出红色"平仓"按钮

#### 持仓卡片（PositionCard）
```
┌──────────────────────────┐
│ BTC/USDT          Long 5x│  ← symbol名 + 方向标签 + 杠杆
│                          │
│ 入场: $101,234           │
│ 当前: $103,567  ▲ +2.30% │  ← 当前价 + 盈亏%
│ 数量: 0.5 BTC            │
│                          │
│ 盈亏: +$1,166.50         │  ← 大字，绿色/红色
│ 止盈: $108,000  止损: $98,500│  ← 灰色小字
└──────────────────────────┘
```
- 点击卡片弹出详情 BottomSheet（止盈止损调整）
- 左滑露出"平仓"按钮，点击弹出确认弹窗

#### 平仓确认弹窗（CloseConfirm）
```
┌──────────────────────────┐
│     确认平仓              │
│                          │
│  BTC/USDT Long           │
│  当前盈亏: +$1,166.50    │
│                          │
│  [取消]      [确认平仓]   │  ← 红色确认按钮
└──────────────────────────┘
```

#### 下单面板（OrderPanel）— 底部弹出 BottomSheet
- 右下角固定 "+" 圆形按钮（蓝色，56x56px）
- 点击弹出 BottomSheet：
  ```
  ┌──────────────────────────┐
  │  选择Symbol               │
  │  [BTC] [ETH] [SOL] ...   │  ← 横向滚动的symbol标签
  │                          │
  │  方向                     │
  │  [  做多  ]  [  做空  ]   │  ← 两个大按钮，选中高亮
  │                          │
  │  杠杆: 5x                │
  │  ◄━━━━━━━●━━━━━━━━━━►   │  ← 滑块，范围 1-20
  │                          │
  │  数量(USDT)               │
  │  [________________]      │  ← 输入框
  │  [25%] [50%] [75%] [100%]│  ← 快捷比例按钮
  │                          │
  │  [      确认下单      ]   │  ← 大蓝色按钮，48px高
  └──────────────────────────┘
  ```

---

### Tab 3: FullAuto 控制页（FullAutoPage）

#### Session 状态卡片（SessionCard）
```
┌──────────────────────────┐
│  ● 运行中                 │  ← 绿色圆点 + 状态文字
│  Session: fa_c8f0899147   │
│  运行时长: 12h 34m        │
│                          │
│  总权益: $543,796         │
│  总盈亏: ▲ +$13,506      │
│  当前回撤: 0.89%          │
│  活跃策略: 42  暂停: 33   │
└──────────────────────────┘
```
- 状态颜色：running=绿, defensive=橙, paused=灰, stopped=红

#### 控制按钮组（SessionControls）
```
┌──────────────────────────┐
│ 根据当前状态显示对应按钮：  │
│                          │
│ running:                 │
│   [暂停交易]  [停止会话]  │  ← 暂停=橙色，停止=红色
│                          │
│ paused:                  │
│   [恢复交易]  [停止会话]  │  ← 恢复=绿色
│                          │
│ defensive:               │
│   [仅可停止]              │  ← 防守模式只能停止
│                          │
│ stopped/无session:        │
│   [启动新会话]            │  ← 蓝色大按钮
└──────────────────────────┘
```
- 每个按钮最小高度 48px，宽度撑满
- 点击后弹出确认 BottomSheet

#### 风控状态（RiskStatus）
```
┌──────────────────────────┐
│  风控状态                  │
│                          │
│  per-symbol 冻结:         │
│  🔴 VVV  日亏-5.1%>3%    │  ← 被冻结的symbol，红色背景
│  🔴 DYM  日亏-3.2%>3%    │
│                          │
│  正常交易:                 │
│  🟢 BTC  -2.8%           │  ← 未冻结，正常显示
│  🟢 ETH  +1.5%           │
│  🟢 CHIP +5.3%           │
└──────────────────────────┘
```

#### 事件日志（EventLog）
- 最近20条事件，时间轴样式
- 左侧时间，右侧事件描述
- 不同类型事件用不同颜色左边框：
  - circuit_breaker = 红色
  - defensive_exit = 绿色
  - strategy_created/frozen = 蓝色
  - 其他 = 灰色
- 自动滚动到最新，支持上拉查看历史

---

### Tab 4: 策略信号页（StrategyPage）

顶部有两个子Tab切换：「策略」和「信号」

#### 策略列表子Tab（StrategyList）
```
┌──────────────────────────┐
│  BTC                     │  ← 按symbol分组标题
│  ┌────────────────────┐  │
│  │ BTC-trend-short    │  │
│  │ ● active  收益+3.2%│  │  ← 绿色活跃 / 灰色暂停
│  └────────────────────┘  │
│  ┌────────────────────┐  │
│  │ BTC-range-mid      │  │
│  │ ○ paused  收益-1.1%│  │
│  └────────────────────┘  │
│                          │
│  ETH                     │
│  ┌────────────────────┐  │
│  │ ETH-momentum-long  │  │
│  │ ● active  收益+0.8%│  │
│  └────────────────────┘  │
└──────────────────────────┘
```
- 点击卡片展开详情（内联展开或BottomSheet）
- 详情展示：策略ID、创建时间、累计PnL、交易次数

#### 信号流子Tab（SignalFeed）
```
┌──────────────────────────┐
│  ┌────────────────────┐  │
│  │ 🟢 BTC  BUY  65%   │  │  ← 方向图标 + symbol + 信号方向 + 置信度
│  │ 策略库信号 · 15:32  │  │  ← 来源 + 时间
│  │ EMA交叉+MACD金叉   │  │  ← 原因（灰色小字）
│  └────────────────────┘  │
│  ┌────────────────────┐  │
│  │ 🔴 ETH  SELL  55%  │  │
│  │ 策略库信号 · 15:28  │  │
│  │ RSI超买+趋势反转   │  │
│  └────────────────────┘  │
│  ┌────────────────────┐  │
│  │ ⚪ SOL  HOLD  20%  │  │  ← hold用白色/灰色
│  │ 策略库信号 · 15:25  │  │
│  │ 信号不足，观望      │  │
│  └────────────────────┘  │
└──────────────────────────┘
```
- 最新信号在最上面
- 下拉加载更多

---

## 数据对接

### HTTP API（后端 FastAPI，端口8000）

移动端通过 Vite proxy 在开发时转发到后端，生产时直接请求后端IP。

核心API端点（所有路径前缀 `/api`）：

#### 交易相关
- `GET /arena/positions?account_id={id}` — 获取持仓列表
- `POST /arena/buy` — 买入开多
- `POST /arena/sell` — 卖出开空/平仓
- `GET /arena/analytics?account_id={id}` — 获取账户分析数据
- `POST /arena/update-pnl` — 触发PnL更新

#### FullAuto 相关
- `GET /api/full-auto/sessions` — 获取所有session列表
- `GET /api/full-auto/status/{session_id}` — 获取session详细状态
- `POST /api/full-auto/start` — 启动新session
- `POST /api/full-auto/pause/{session_id}` — 暂停session
- `POST /api/full-auto/resume/{session_id}` — 恢复session
- `POST /api/full-auto/stop/{session_id}` — 停止session
- `POST /api/full-auto/health-check/{session_id}` — 触发手动巡检

#### 策略/信号相关
- `GET /api/ai-strategy/list?account_id={id}` — 策略列表
- `GET /api/signals/definitions` — 信号定义列表
- `GET /api/signals/detections` — 信号检测结果

#### 行情
- `GET /crypto/price/{symbol}` — 单symbol价格
- `GET /crypto/symbols` — 所有symbol列表
- `GET /kline/{symbol}?period=1h&count=200` — K线数据

#### 账户
- `GET /accounts` — 账户列表
- `GET /accounts/{id}` — 账户详情

### WebSocket（实时数据推送）

连接地址：`ws://{host}/ws`

连接时发送 bootstrap 消息：
```json
{
  "type": "bootstrap",
  "username": "default",
  "initial_capital": 10000,
  "trading_mode": "paper"
}
```

接收的消息类型：

| 消息类型 | 数据 | 移动端用途 |
|----------|------|-----------|
| `snapshot` / `full_snapshot` | overview, positions, orders, trades, ai_decisions, all_asset_curves | 初始化所有页面数据 |
| `delta` | changes: { overview?, positions?, orders?, trades? } | 增量更新持仓/盈亏 |
| `order_filled` | — | Toast 通知"订单成交" |
| `order_pending` | — | Toast 通知"订单等待中" |
| `trade_update` | trade | 新交易通知 + 持仓刷新 |
| `position_update` | positions | 持仓列表刷新 |
| `asset_curve_update` | data | 权益曲线刷新 |
| `error` | message | 错误 Toast |

### 数据类型定义

```typescript
// 账户概览
interface Overview {
  account: {
    id: number
    name: string
    account_type: string
    initial_capital: number
    current_cash: number
  }
  total_assets: number
  positions_value: number
  portfolio?: {
    total_assets: number
    positions_value: number
  }
}

// 持仓
interface Position {
  id: number
  account_id: number
  symbol: string
  name: string
  market: string
  quantity: number
  available_quantity: number
  avg_cost: number
  last_price?: number | null
  market_value?: number | null
}

// 订单
interface Order {
  id: number
  order_no: string
  symbol: string
  side: string
  order_type: string
  price?: number
  quantity: number
  filled_quantity: number
  status: string
}

// FullAuto Session
interface FullAutoSession {
  session_id: string
  status: 'running' | 'defensive' | 'paused' | 'stopped'
  pause_reason: string | null
  symbols: string[]
  total_pnl: number
  current_drawdown: number
  peak_balance: number
  active_strategy_ids: number[]
  terminated_strategy_ids: number[]
  events: Array<{ type: string; message: string; timestamp: string }>
  created_at: string
}

// 策略
interface AIStrategy {
  id: number
  name: string
  primary_symbol: string
  status: 'active' | 'paused' | 'terminated'
  total_pnl: number
  total_trades: number
  created_at: string
  genome?: Record<string, any>
}

// 信号
interface SignalDetection {
  id: number
  signal_name: string
  symbol: string
  direction: 'buy' | 'sell' | 'hold'
  confidence: number
  reason: string
  detected_at: string
}
```

---

## 组件设计规范

### 基础 UI 组件

#### TouchButton
- 最小高度 48px，圆角 8px
- 变体：primary(蓝) / danger(红) / success(绿) / ghost(透明边框)
- 支持 loading 状态（旋转图标 + 禁用点击）
- 支持 disabled 状态（灰色 + 禁用点击）
- 点击反馈：按下时透明度 0.8

#### BottomSheet
- 从底部滑出的面板
- 半透明黑色遮罩（点击遮罩关闭）
- 顶部有拖拽手柄（灰色横条）
- 内容区域可滚动
- 动画：300ms ease-out

#### SwipeAction
- 左滑露出操作按钮
- 使用 touch 事件（touchstart/touchmove/touchend）
- 滑动阈值 80px 触发
- 回弹动画

#### Badge
- 小标签，用于状态显示
- 变体：success(绿底) / danger(红底) / warning(橙底) / neutral(灰底)
- 圆角 4px，padding 2px 8px
- 文字 12px

#### PriceDisplay
- 数字显示组件
- 正数绿色，负数红色，零灰色
- 支持 前缀+/− 号
- 支持百分比模式

---

## 文件结构（最终产物）

```
mobile/
├── index.html
├── package.json
├── vite.config.ts
├── tailwind.config.js
├── postcss.config.js
├── tsconfig.json
├── public/
│   ├── manifest.json
│   └── icon-192.png
└── app/
    ├── main.tsx              # 入口
    ├── App.tsx               # 路由 + 布局
    ├── index.css             # 全局样式 + 深色主题
    ├── api/
    │   ├── client.ts         # HTTP 客户端封装
    │   ├── types.ts          # 所有 TypeScript 类型
    │   ├── trading.ts        # 交易/持仓 API
    │   ├── fullauto.ts       # FullAuto session API
    │   ├── strategy.ts       # 策略/信号 API
    │   └── market.ts         # 行情 API
    ├── hooks/
    │   ├── useWebSocket.ts   # WebSocket 连接 + 自动重连
    │   ├── usePositions.ts   # 持仓数据（WS 实时）
    │   ├── useSession.ts     # FullAuto session 状态
    │   └── useSwipe.ts       # 滑动手势 hook
    ├── components/
    │   ├── layout/
    │   │   ├── MobileShell.tsx
    │   │   ├── BottomNavBar.tsx
    │   │   ├── StatusBar.tsx
    │   │   └── PullToRefresh.tsx
    │   ├── monitor/
    │   │   ├── AccountCard.tsx
    │   │   ├── EquityChart.tsx
    │   │   ├── PnlSummary.tsx
    │   │   └── SymbolGrid.tsx
    │   ├── trading/
    │   │   ├── PositionList.tsx
    │   │   ├── PositionCard.tsx
    │   │   ├── OrderPanel.tsx
    │   │   └── CloseConfirm.tsx
    │   ├── fullauto/
    │   │   ├── SessionCard.tsx
    │   │   ├── SessionControls.tsx
    │   │   ├── RiskStatus.tsx
    │   │   └── EventLog.tsx
    │   ├── strategy/
    │   │   ├── StrategyList.tsx
    │   │   ├── StrategyCard.tsx
    │   │   ├── SignalFeed.tsx
    │   │   └── SignalCard.tsx
    │   └── ui/
    │       ├── TouchButton.tsx
    │       ├── SwipeAction.tsx
    │       ├── BottomSheet.tsx
    │       ├── Badge.tsx
    │       └── PriceDisplay.tsx
    └── pages/
        ├── MonitorPage.tsx
        ├── TradingPage.tsx
        ├── FullAutoPage.tsx
        └── StrategyPage.tsx
```

---

## Vite 配置要点

```typescript
// vite.config.ts
export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',      // 允许局域网/Tailscale访问
    port: 5174,            // 与桌面端5173区分
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/ws': { target: 'ws://127.0.0.1:8000', ws: true },
    },
  },
})
```

---

## Tailwind 深色主题配置

扩展 tailwind.config.js 的 colors：

```javascript
colors: {
  terminal: {
    bg: '#0a0e17',        // 主背景
    card: '#111827',       // 卡片背景
    border: '#1f2937',     // 边框
    profit: '#00dc82',     // 盈利绿
    loss: '#ef4444',       // 亏损红
    primary: '#3b82f6',    // 交互蓝
    muted: '#6b7280',      // 次要文字
    text: '#f9fafb',       // 主文字
    warning: '#f59e0b',    // 警告橙
  }
}
```

---

## PWA 配置

```json
// public/manifest.json
{
  "name": "AlphaArena",
  "short_name": "AlphaArena",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#0a0e17",
  "theme_color": "#0a0e17",
  "icons": [
    { "src": "/icon-192.png", "sizes": "192x192", "type": "image/png" },
    { "src": "/icon-512.png", "sizes": "512x512", "type": "image/png" }
  ]
}
```

---

## 实现注意事项

1. **触摸友好**：所有可点击元素最小 44x44px，按钮间有足够间距
2. **实时数据**：WebSocket 连接后，持仓/价格/盈亏全部实时更新，无需手动刷新
3. **断线重连**：WebSocket 断线后 3 秒自动重连，重连成功后发送 get_snapshot 重新同步
4. **网络适配**：移动网络可能不稳定，所有 API 调用需有超时处理和错误提示
5. **安全确认**：所有交易操作（开仓/平仓/启动/停止）必须二次确认
6. **性能**：避免不必要的重渲染，使用 React.memo / useMemo 优化大数据列表
7. **手势**：左滑平仓、下拉刷新等手势操作需使用原生 touch 事件，不依赖第三方库
8. **适配**：支持 768px-1024px（平板竖屏/横屏）和 375px-428px（手机）
