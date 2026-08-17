# 主题 A：黑曜终端 Obsidian Terminal — 前端视觉设计方案

> 项目：Hyper-Alpha-Arena（AI 驱动加密量化交易平台）
> 范围：`frontend-next`（Next.js 16 + React 19 + Tailwind v4 + shadcn/ui）与 Electron 桌面端共享的视觉体系
> 配套交付：`previews/01-obsidian-terminal.html`（可打开预览）、`previews/tokens-01.css`（可直接落地的 token 块）
> 状态：设计定稿 v1.0

---

## 1. 设计理念

**一句话概念**：给职业交易员的"黑曜石工作台"——把 Bloomberg 终端精神现代化：**极致数据密度、绝对克制、毫厘级对齐**。

黑曜石（Obsidian）是火山玻璃：在极暗的底色上，切面锐利、棱线分明、反射出克制的冷光。本主题以此隐喻整台交易终端：

- **暗而不脏**：底色取冷蓝黑 `#0A0D12`，四个背景层级（background → card → muted → accent）按 L 值 0.143–0.254 递进，任何层级切换都不会发灰发脏。
- **数据即主角**：界面不靠插画、不靠大色块、不靠圆角卡片堆叠；靠的是 30px 行高的细网格表格、右对齐等宽数字、10px 大写表头。任何像素都服务"读数据"这一个动作。
- **色彩唯一语义**：蓝 = 交互/品牌，绿 = 涨/盈利，红 = 跌/亏损，琥珀 = 警告/异常。全站仅此四义，杜绝装饰性彩色。
- **毫厘级对齐**：4px 栅格派生一切间距（8/12/14/16/24px），行高、字号、边框宽度全部取整，杜绝半像素模糊。
- **防御性显示**：交易终端必须对脏数据负责。异常值（如截图中的"胜率 2518.0%"）用红色警告样式 + "数据异常"徽章显式标出，绝不静默渲染。

一句话品牌口号：**"黑曜切面，只反射市场真相。"**

## 2. 情绪板参照

| 参照产品 | 提取的风格要素 | 在本主题中的落点 |
|---|---|---|
| **Bloomberg Terminal** | 全屏信息密度、四色功能键、底部行情状态条、多面板网格 | 底部 28px 状态栏、数据密度、克制配色 |
| **TradingView Pro（桌面版）** | 深色图表、右对齐等宽数字、上涨/下跌着色、tab 切换工具链 | 数字排版规范、涨跌色、工具 tab |
| **Da Vinci Terminal** | 交易员工作台的"精密仪器感"、高密度表格 + 细网格 | 表格行高 30px、细网格线、sticky 表头 |
| **彭博终端任意截图** | 大写字母间距表头、冒号对齐、状态徽章体系 | 10px 大写表头、徽章体系 |
| **黑曜石（实物）** | 冷蓝黑底、锐利切面、克制的反射光 | 四层背景递进、2px 语义色条、低饱和蓝 |

**关键词**：精密仪器 / 暗色石材 / 等宽数字 / 低饱和蓝 / 数据密度 / 绝对克制。

## 3. 色彩系统

HEX 为唯一事实来源；oklch 为近似换算（供现有 globals.css 对照迁移，数值由脚本按 sRGB→OKLab 精确换算）。对比度基于 WCAG 2.1 相对亮度公式实测计算。

### 3.1 背景与表面

| 角色 | HEX | oklch 近似 | 用途 |
|---|---|---|---|
| `--background` | `#0A0D12` | `oklch(0.158 0.012 261)` | 页面底色，黑曜岩 |
| `--card` | `#10151C` | `oklch(0.194 0.016 257)` | 卡片/面板 |
| `--popover` | `#0C1016` | `oklch(0.172 0.014 258)` | 弹层/下拉/命令面板 |
| `--muted` | `#161D27` | `oklch(0.229 0.022 257)` | 静态弱化容器 |
| `--secondary` | `#131923` | `oklch(0.212 0.022 261)` | 次要容器 |
| `--accent` | `#1A2330` | `oklch(0.254 0.028 258)` | hover/高亮容器 |
| `--input` | `#141A23` | `oklch(0.216 0.020 258)` | 输入框底色 |
| `--border` | `#1E2631` | `oklch(0.266 0.024 256)` | 主边框/网格线 |
| `--sidebar` | `#070A0E` | `oklch(0.143 0.010 253)` | 侧边栏（比背景暗一档，压层次） |
| `--sidebar-accent` | `#121823` | `oklch(0.208 0.024 262)` | 侧栏选中项底色 |
| `--sidebar-border` | `#141B25` | `oklch(0.220 0.022 257)` | 侧栏分割线 |

### 3.2 文字与交互

| 角色 | HEX | oklch 近似 | 用途 | 对比度（vs 背景/卡片） |
|---|---|---|---|---|
| `--foreground` | `#E7ECF4` | `oklch(0.942 0.012 260)` | 主文字 | 16.4:1 / 15.5:1 |
| `--muted-foreground` | `#7E8AA0` | `oklch(0.631 0.036 263)` | 次要文字 | 5.6:1 / 5.3:1 ✅AA |
| `--primary` | `#4C8DFF` | `oklch(0.658 0.182 261)` | 品牌蓝：主按钮/链接/激活态 | 6.1:1 / 5.7:1 |
| `--ring` | `rgb(76 141 255 / 0.35)` | `oklch(0.658 0.182 261 / 0.35)` | 焦点环 | — |
| `--sidebar-foreground` | `#C6CFDD` | `oklch(0.852 0.022 259)` | 侧栏文字 | 12.6:1（vs 侧栏） |

> 备注：白字在 `--primary` 上约 3.2:1，属"大文本/UI 组件"档（≥3:1，AA 大文本达标）；主按钮文字固定 14px/600 字重即可。正文中的主色文字（链接、侧栏激活项）建议使用亮一档的 `#7DA8FF`（vs 背景 8.3:1）以保 AA 全文达标，或直接用 `--primary`（6.1:1 已达标）。

### 3.3 交易语义色与图表

| 角色 | HEX | oklch 近似 | 用途 | 对比度（vs 卡片） |
|---|---|---|---|---|
| `--profit`（涨/盈利） | `#00C896` | `oklch(0.739 0.152 167)` | 涨、正盈亏 | 8.5:1 |
| `--loss`（跌/亏损） | `#FF5C7A` | `oklch(0.697 0.198 13)` | 跌、负盈亏、止损、SELL | 6.2:1 |
| `--warning` | `#F5B83D` | `oklch(0.819 0.150 81)` | 警告、数据异常、延迟 | 10.3:1 |

图表序列 `--chart-1..5`：`#4C8DFF`（蓝，主）→ `#00C896`（绿，盈利）→ `#FF5C7A`（红，亏损）→ `#F5B83D`（琥珀，警告）→ `#A78BFA`（紫，扩展，oklch 0.709 0.159 294）。取色顺序即图表默认序列，与语义色同族，保证"图例 = 业务语义"。

**规则**：全站禁用装饰性彩色。彩色只允许出现在上述语义角色的位置；不确定时一律用 `--muted-foreground` 灰。

## 4. 字体与排版

### 4.1 字体栈

| 角色 | 字体 | 备注 |
|---|---|---|
| 正文/界面 | `Inter`（拉丁）+ `Noto Sans SC`（汉字）+ 系统中文回退（PingFang SC / Microsoft YaHei） | 桌面端离线时打包 Noto Sans SC 子集，网页端走 Google Fonts |
| 数字/代码 | `JetBrains Mono`（备选 `IBM Plex Mono`）+ 等宽回退栈 | 价格、表格数字、时间、版本号 |
| 全局数字排版 | `font-variant-numeric: tabular-nums` **必须全局开启** | 数字不跳动，毫厘级对齐 |

### 4.2 字号阶梯（Type Scale）

| 档位 | 字号/行高 | 字重 | 用途 |
|---|---|---|---|
| T10 | 10px / 14px | 600 | 分组标题、表头（大写 + 0.08em 字距）、角标 |
| T11 | 11px / 16px | 500 | 状态徽章、表格次要信息、tooltip |
| T12 | 12px / 18px | 400/500 | 表格正文、ticker、页头徽章 |
| T13 | 13px / 20px | 400 | 卡片辅助文字、AI 决策理由 |
| T14 | 14px / 22px | 500 | 侧边栏项、按钮、卡片标题、页头标题（500/600） |
| T18 | 18px / 26px | 600 | 页头页面标题 |
| T22 | 22px / 30px | 600 | 统计卡主数值（等宽） |
| T28 | 28px / 36px | 600 | 仪表盘大数字（总资产主显示，可选） |

标题可轻微收紧字距：`letter-spacing: -0.01em`（仅 T18 以上），正文保持 0。

## 5. 布局系统

### 5.1 结构尺寸（自上而下）

| 区块 | 高度/宽度 | 说明 |
|---|---|---|
| 侧边栏 | 宽 216px（折叠 64px） | 独立暗层，激活项 2px 主色条 |
| 顶栏 | 高 44px | 搜索/状态/版本/铃铛/时钟/头像 |
| Ticker 行情条 | 高 36px | 顶栏下独立细条，1px 底边框，随页面视差固定 |
| 内容区 | 8px 栅格 | padding 16px，卡片间距 12px |
| 底部状态栏 | 高 28px | 整页最底部、全宽，1px 顶边框 |
| 卡片 | padding 14px，圆角 `--radius`(6px)，1px 边框 | 极简边框卡片 |

### 5.2 栅格

- 基础单位 **4px**；内容区按 8px 栅格排布（padding 16、间距 12/16/24 均为 4 的倍数）。
- 内容区列布局：`grid-template-columns: repeat(12, 1fr)`，两栏用 `7fr/5fr`（表格为主）或 `6fr/6fr`（对等）。
- 统计卡一行 4 卡：`repeat(4, 1fr)`，间距 12px。
- 侧栏宽度 216px 由 `w-56`（224px）收窄而来，多出的 8px 还给数据区；折叠态 64px（图标 16px + 两侧 24px 留白）。

## 6. 组件规范

### 6.1 侧边栏（Sidebar.tsx）
宽 216px（折叠 64px），底色 `--sidebar`，与主区之间 1px `--sidebar-border` 分割。激活项：左侧 2px `--primary` 竖条（绝对定位伪元素）+ `--primary` 文字 + `--sidebar-accent` 底色；hover 仅背景 `--sidebar-accent` 半透明。分组标题 10px 大写（`text-transform: uppercase; letter-spacing: 0.08em`）`--muted-foreground`。品牌区 44px：8×8 圆角徽标（α）+ 名称 + "量化交易终端"副标题。折叠态：图标水平居中，标题隐藏，hover 出 tooltip；VIP 项带 4px 金色小圆点。

### 6.2 顶栏（TopBar.tsx）
高 44px，1px 底边框，内容右侧对齐密度排列：搜索胶囊（`--input` 底、18px 高、⌘K 键帽徽标）→ WS 状态（绿点 + "实时"，轮询时琥珀点 + "轮询中"）→ 桌面版本号（mono 11px）→ "检查更新"文字按钮 → 铃铛（错误角标：红色胶囊数字，P0/P1 计数）→ 时钟（mono 12px tabular）→ 用户头像（圆形首字母 + VIP 等级 + 退出图标）。分隔用 1px 竖线 + 12px 间距。

### 6.3 Ticker 行情条（PriceTicker.tsx / 新组件）
顶栏下方独立 36px 细条，1px 底边框，背景 `--card`。每项：币种（12px 600，等宽）+ 价格（mono 12px tabular）+ 涨跌（`--profit`/`--loss` 着色 + 4px 小三角 ▲/▼）。视差固定（`position: sticky; top: 44px`），横向溢出自动隐藏。末尾常驻绿点 + "LIVE" 徽标（呼吸动画 2s）。

### 6.4 统计卡（cells.tsx → StatCard）
极简边框卡片（1px `--border`、6px 圆角、padding 14px、`--card` 底）+ **左侧 2px 语义色条**（资产=蓝 / 盈亏=绿红随符号 / 胜率=琥珀 / 持仓=蓝）+ 右上角 64×24 迷你 sparkline（内联 SVG，`--chart-*` 单色描边 + 12% 透明度渐变填充）。主数值 T22 mono tabular，标签 T11 `--muted-foreground`。数值为 0 时正常灰显（`--foreground`），不为 0 才着色。

### 6.5 表格（本主题主角）
- 行高 30px，单元格垂直居中；数字列**右对齐** + mono + tabular-nums。
- 1px `--border` 细网格线（横向完整、纵向仅列间）；表头 sticky（顶部 0，`--card` 底 + 2px 下边框），10px 大写 + 0.08em 字距 + `--muted-foreground`。
- hover：整行 `--accent` 6% 背景 + 左侧 2px `--primary` 竖条。
- 排序箭头、页码等控件 11px，交互态 `--primary`。
- **防御性显示**：值超出合理域（如胜率 >100% 或 <0%）→ 数字着 `--loss` 红 + 单元格 6% 红底 + "数据异常"小徽章（`--loss` 边框胶囊）+ `title` 提示；空数据渲染 `—` 而非 0。

### 6.6 徽章（badge.tsx）
语义色小胶囊：6px 圆角（`--radius`）、padding 2px 8px、11px/600、**大写**（SELL / BUY / LIVE / VIP / 数据异常）。底色 = 语义色 12% 透明度，文字 = 语义色本体（绿/红/琥珀），保证文字对比度（≥6:1）。变体：outline（透明底 + 语义色 1px 边框）用于次级状态。

### 6.7 按钮（button.tsx）
主按钮：`--primary` 底 + 白字 14px/600（AA 大文本档 3.2:1），hover 提亮 6%（叠加白色 8% 蒙层），active 下沉 1px。次按钮：透明底 + 1px `--border` + `--foreground` 文字，hover 背景 `--accent`。危险按钮：`--loss` 同款。禁用：40% 透明度 + `not-allowed`。圆角 `--radius`，高度 30/34/38 三档，过渡 150ms。

### 6.8 输入框（input.tsx / 搜索）
`--input` 底色、1px `--border`、6px 圆角、内 padding 8px 12px；聚焦时 `--ring` 环（2px 35% 透明蓝）+ 边框转 `--primary`；placeholder 用 `--muted-foreground`。高度 30/34px（顶栏搜索 18px 紧凑版）。键盘提示键帽（kbd）：1px 边框 + 4px 圆角 + 11px mono。

### 6.9 Tab（tabs.tsx）
下划线式：文字 13px/500，激活项 `--foreground` + 2px `--primary` 下划线（底部对齐），未激活 `--muted-foreground` hover 转 `--foreground`；容器底部 1px `--border`。适合策略页（短线/长线）与市场中台（K 线/资金流）切换。

### 6.10 图表（EquityCurve.tsx 等）
坐标轴/网格线用 `--border`（40% 透明度），数据线用 `--chart-1..5` 序列，区域渐变填充（主色 14% → 0）。价格轴数字 mono tabular 11px `--muted-foreground`。tooltip：`--popover` 底 + 1px `--border` + 12px 数字 + 语义色圆点。涨跌分色仅用于涨跌图（如 K 线），净值曲线用主蓝单色。

### 6.11 空状态（cells.tsx → EmptyState）
虚线框（1px dashed `--border`，6px 圆角）+ 居中 24px 图标（`--muted-foreground`）+ 标题 T13 + 说明 T11 + 主按钮"前往配置"（`--primary`）。如账户未配置卡："尚未配置交易账户，配置后即可开始模拟/实盘交易"。

### 6.12 底部状态栏（新组件 StatusBar）
整页最底部、全宽 28px，1px 顶边框，`--sidebar` 底（与侧栏同族，视觉收拢）。左侧："● Hyperliquid 已连接"（绿点）+ "⚡ 系统运行中"（琥珀/蓝点），状态失败转 `--loss` 红点；右侧 mono 12px 时钟（HH:MM:SS）+ 时区。仿 Bloomberg 的"仪器底盘"收尾。

### 6.13 页头（新组件 PageHeader）
每页顶部统一：左侧页面标题 T18/600 + 面包屑（11px `--muted-foreground`，"/" 分隔，末级高亮）；右侧状态徽章区（如"数据延迟 2s"琥珀胶囊、"WS 实时"绿胶囊）。解决现页面无层级的问题。

## 7. 动效规范

- **价格闪烁**：数值变化时单元格背景 `--profit`/`--loss` 30% 透明度 → 透明，**0.4s ease-out** 淡出（复用现有 `.flash-profit`/`.flash-loss`，仅替换色值）。
- **过渡**：所有 hover/激活/展开统一 **150ms ease**（颜色、背景、边框），位移类（侧栏折叠）200ms。
- **呼吸指示**：LIVE 绿点 / 连接状态点 2s 呼吸（opacity 1→0.4）。
- **克制原则**：禁用弹性、缩放弹跳、旋转入场；页面内容不做整屏动画；动画只存在于"数据变化"与"状态变化"两处。尊重 `prefers-reduced-motion`，开启时全部降级为瞬时。

## 8. 无障碍与对比度

所有核心文字对比度均 ≥ 4.5:1（WCAG AA 普通文本），实测数据（相对亮度公式）：

| 前景 | 背景 | 对比度 | 结论 |
|---|---|---|---|
| `--foreground` | `--background` | 16.4:1 | AAA |
| `--muted-foreground` | `--background` | 5.6:1 | AA |
| `--muted-foreground` | `--card` | 5.3:1 | AA |
| `--muted-foreground` | `--muted` | 4.9:1 | AA |
| `--primary` | `--background` | 6.1:1 | AA |
| `--profit` | `--card` | 8.5:1 | AAA |
| `--loss` | `--card` | 6.2:1 | AA |
| `--warning` | `--background` | 10.9:1 | AAA |
| `--sidebar-foreground` | `--sidebar` | 12.6:1 | AAA |

其余要求：全部交互元素可键盘聚焦（焦点环 `--ring` 2px）；数字 tabular-nums 防跳动；色彩从不作为唯一信息通道（涨跌同时有 ▲/▼ 符号与正负号）；`prefers-reduced-motion` 降级动画；语义色文字在 12% 透明底色上仍保持本体色文字（≥6:1）。已知边界：白字 on `--primary` 为 3.2:1（AA 大文本档达标），主按钮以 14px/600 与实心底保证可读性，见 §3.2 备注。

## 9. 落地实施

### 9.1 替换 globals.css 步骤

1. **换 token**：将 `previews/tokens-01.css` 中 `:root, .dark { ... }` 整块复制进 `frontend-next/src/app/globals.css`，替换原第 56–102 行同名块。变量名完全一致 → `@theme inline` 映射层（第 7–52 行）**零改动**，所有 `bg-card`、`text-muted-foreground` 等工具类自动继承新值。
2. **换字体**：`layout.tsx` 用 `next/font/google` 引入 Inter / Noto Sans SC / JetBrains Mono（或 Electron 端本地打包子集），赋值给 `--font-sans` / `--font-mono`；确保 `body { font-variant-numeric: tabular-nums }` 保留（现已有）。
3. **改度量**：`Sidebar.tsx` 折叠判断与宽度常量 `w-56` → `w-[216px]`；`TopBar.tsx` 高度 h-12 → h-11（44px）；其余如 `w-16`（64px 折叠）不变。
4. **加固件**：新增 `PageHeader.tsx`、`TickerBar.tsx`、`StatusBar.tsx` 三个小组件并挂入 `AppShell.tsx`；`PriceTicker.tsx` 改造为 36px 细条并 `sticky top-44px`。
5. **表格规范化**：在 globals.css 增加一个 `.data-table` 工具类（30px 行高、右对齐等宽数字、细网格、sticky 表头、hover 行高亮），各页表格换用；胜率类字段加 `0–100` 域校验 + 异常徽章（见 §6.5）。
6. **验证**：对比度数值对照 §8 表；1440×900 下无横向滚动；`--radius` 不变（0.375rem）故圆角零回归。

### 9.2 需要改造的组件清单（对应真实文件）

| 文件 | 改造内容 |
|---|---|
| `src/app/globals.css` | 替换 `:root, .dark` token 块；新增 `.data-table`、页头/状态栏基础类 |
| `src/app/layout.tsx` | 引入三套字体并注入 `--font-sans/--font-mono` |
| `src/components/layout/Sidebar.tsx` | 宽 216px；激活项 2px 主色条 + 主色文字；分组标题大写 10px；折叠 tooltip |
| `src/components/layout/TopBar.tsx` | 高 44px；WS 状态胶囊化；错误角标胶囊；间距/分隔线规范化 |
| `src/components/layout/AppShell.tsx` | 挂载 TickerBar、StatusBar、PageHeader 容器 |
| `src/components/layout/CommandPalette.tsx` | 弹层用 `--popover`，快捷键键帽样式 |
| `src/components/trading/PriceTicker.tsx` | 36px 独立细条 + 视差固定 + ▲/▼ 着色 |
| `src/app/dashboard/page.tsx` | 页头（标题/面包屑/延迟徽章）；统计卡加 2px 色条与 sparkline |
| `src/components/trading/cells.tsx` | StatCard（色条+sparkline）、EmptyState（虚线框+引导按钮）、异常值防御显示 |
| `src/components/trading/DecisionTimeline.tsx` | 决策项 SELL/BUY 语义胶囊徽章 + 时间 mono |
| `src/components/charts/EquityCurve.tsx` | 取 `--chart-1..5` 序列、网格线淡化、坐标数字 mono |
| `src/components/ui/badge.tsx` | 6px 圆角、11px/600、大写、语义色 12% 底 |
| `src/components/ui/button.tsx` | 主按钮 14px/600 白字、hover 叠加提亮、150ms |
| `src/components/ui/card.tsx` | padding 14px 规范 |

## 10. 风险与取舍

| 风险/取舍 | 说明与对策 |
|---|---|
| 白字 on 主蓝 3.2:1 | 属 AA"大文本/UI 组件"档；主按钮强制 14px/600，正文主色文字改用 `#7DA8FF` 亮蓝或保持 `--primary`（6.1:1 已达标） |
| `--muted-foreground` 在 `--muted` 上 4.9:1 | 紧贴 AA 下限；规范禁止在 muted 容器里放 12px 以下文字，必要时提为 `--secondary-foreground` |
| 胜率 2518.0% 类脏数据 | 域校验 + 红字 + "数据异常"徽章 + title 说明，异常值不进计算展示（§6.5） |
| 中文字体体积 | Inter+Noto Sans SC 全量约 1MB+；网页走 Google Fonts `display=swap`，Electron 桌面端按常用字子集打包（或系统字体回退），详见 §4.1 |
| 216px 侧栏收窄 8px | 数据区多 8px；图标/文字 13px 下无拥挤风险，折叠态 64px 与现状一致 |
| oklch vs HEX | 现有 globals.css 为 oklch 写法；新主题以 HEX 为事实来源、oklch 仅注释参考，避免双源漂移；现代浏览器两者均支持 |
| 动效克制 | 牺牲"炫"，换取交易员对瞬时价格变化的零干扰；`prefers-reduced-motion` 全覆盖 |
| 高密度与触屏 | 30px 行高、30px 按钮对鼠标/键盘友好，触屏点击目标略小（≥24px 满足 WCAG）；移动端另行布局，不在本主题范围 |

---

*本方案配套 `previews/01-obsidian-terminal.html`（1440×900 可视化预览）与 `previews/tokens-01.css`（可直接复制的 token 块）。*
