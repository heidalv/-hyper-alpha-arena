# 主题 B：极光引擎 Aurora Engine — 前端视觉设计方案

> 适用项目：Hyper-Alpha-Arena（AI 驱动的加密货币量化交易平台）
> 技术栈：Next.js 16 · React 19 · Tailwind v4 · shadcn/ui · Electron 桌面端
> 关联交付物：`docs/ui-redesign/previews/02-aurora-engine.html`（可双击打开预览）、`docs/ui-redesign/previews/tokens-02.css`（可直接替换 globals.css 的 token 块）

---

## 1. 设计理念

**一句话概念**：下一代加密量化交易台——深邃宇宙底色上极光涌动，数据如星空般发光。

极光引擎把「深空」与「极光」作为视觉母题：近黑深蓝的宇宙底色让所有内容退后、成为背景噪音，而青→紫的极光光带以 5–8% 的透明度缓缓流动，为界面注入「生机」而不喧宾夺主；数据（价格、KPI、曲线）被赋予「发光天体」的地位——渐变大数字、辉光徽章、玻璃卡片像悬浮在太空中的观测舱。这是一个**以数据为星**的科技美学旗舰方案，视觉冲击力全主题最强，适合作为产品对外的主视觉基调。

四条设计原则：

1. **数据是主角**：所有装饰（极光、辉光、渐变）透明度 ≤ 9%，只服务「让数字发光」，绝不遮挡扫读。
2. **宇宙底色**：`#070B15` 近黑带蓝，比普通黑更沉、更冷，是极光与数据发光的最佳底衬。
3. **玻璃分层**：半透明卡片 + `backdrop-blur(14px)` + 1px 白 8% 描边，用「材质透明度」表达层级关系（侧栏 < 卡片 < 弹出层）。
4. **生机与呼吸**：极光 12s 流动、spring 缓动入场、价格闪烁、状态点脉冲——静态的终端因此「活着」，但全部受 `prefers-reduced-motion` 约束。

---

## 2. 情绪板参照

| 参照产品 | 风格描述 | 本方案汲取点 |
| --- | --- | --- |
| OKX / Binance 深色模式 | 高密度行情、红绿涨跌语义、等宽数字不抖动、双列多栏布局 | 交易信息密度、涨跌语义、tabular-nums 防跳动 |
| Phantom Wallet 深色 | 深邃近黑蓝底 + 高饱和青/紫点缀、玻璃卡片、柔和辉光 | 宇宙感底色、青紫点缀、数字资产气质 |
| Raydium / Jupiter | 渐变品牌色、发光主按钮、sparkline 迷你走势 | 渐变按钮 + 同色辉光、迷你面积图 |
| Linear | spring 缓动、卡片悬浮、细腻 focus ring | 动效质感（`cubic-bezier(0.34,1.56,0.64,1)`）、卡片上浮 |
| Stripe 深色 | 低饱和背景 + 高饱和强调、大号渐变数字、克制的排版 | KPI 渐变数字、克制的高对比排版 |

**关键词**：极光、玻璃、辉光、深邃、生机。

---

## 3. 色彩系统

所有值（角色名与现有 token 完全一致，可直接替换）。半透明色给出的 oklch 为其在 `#070B15` 上合成后的近似值。

| 角色 | 值 | oklch 近似 | 用途 |
| --- | --- | --- | --- |
| `--background` | `#070B15` | `oklch(0.042 0.012 258)` | 页面深空底色（叠加极光径向渐变） |
| `--foreground` | `#EAF0FA` | `oklch(0.945 0.012 259)` | 主文字：冷白带蓝 |
| `--card` | `rgba(255,255,255,0.045)` | `≈ oklch(0.088 0.011 258)` | 玻璃卡片底（配合 blur 14px） |
| `--card-foreground` | `#EAF0FA` | `oklch(0.945 0.012 259)` | 卡片内主文字 |
| `--popover` | `#0C1120` | `oklch(0.063 0.016 258)` | 弹出层：实色保证可读 |
| `--popover-foreground` | `#EAF0FA` | `oklch(0.945 0.012 259)` | 弹出层文字 |
| `--muted` | `rgba(255,255,255,0.05)` | `≈ oklch(0.096 0.012 258)` | 静态弱背景 / 表格条纹 |
| `--muted-foreground` | `#94A1BC` | `oklch(0.667 0.036 259)` | 次要文字（AA 达标） |
| `--secondary` | `rgba(255,255,255,0.07)` | `≈ oklch(0.118 0.013 258)` | 次要容器 |
| `--secondary-foreground` | `#EAF0FA` | — | 次要容器文字 |
| `--accent` | `rgba(255,255,255,0.09)` | `≈ oklch(0.136 0.014 258)` | 高亮容器 / 悬停底 |
| `--accent-foreground` | `#EAF0FA` | — | 高亮容器文字 |
| `--primary` | `#22D3EE` | `oklch(0.789 0.154 211.5)` | 极光青：主按钮渐变起点、聚焦环 |
| `--primary-foreground` | `#041018` | `oklch(0.042 0.01 245)` | 渐变按钮上的深色文字 |
| `--destructive` | `#FB7185` | `oklch(0.712 0.194 13.4)` | 危险操作（与 --loss 同族） |
| `--destructive-foreground` | `#2B0A12` | — | 危险按钮文字 |
| `--ring` | `rgba(34,211,238,0.45)` | — | 键盘焦点环、辉光 |
| `--input` | `rgba(255,255,255,0.08)` | `≈ oklch(0.127 0.014 258)` | 输入框底 |
| `--border` | `rgba(255,255,255,0.09)` | `≈ oklch(0.136 0.014 258)` | 通用描边：白 9% |
| `--profit` | `#34D399` | `oklch(0.792 0.209 164)` | 涨（绿）：价格、正盈亏、BUY 徽章 |
| `--loss` | `#FB7185` | `oklch(0.712 0.194 13.4)` | 跌（红）：价格、负盈亏、SELL 徽章 |
| `--warning` | `#FBBF24` | `oklch(0.828 0.189 84.4)` | 警告（琥珀）：异常值、数据异常 |
| `--chart-1..5` | `#22D3EE / #8B5CF6 / #34D399 / #FB7185 / #FBBF24` | 见上 | 极光五色图表序列 |
| `--sidebar` | `rgba(9,13,24,0.92)` | `≈ oklch(0.05 0.013 258)` | 侧栏深蓝玻璃底 |
| `--sidebar-foreground` | `#AEB9D0` | `oklch(0.748 0.03 259)` | 侧栏导航文字 |
| `--sidebar-primary` | `#22D3EE` | — | 侧栏激活品牌色 |
| `--sidebar-primary-foreground` | `#041018` | — | 侧栏激活项文字 |
| `--sidebar-accent` | `rgba(255,255,255,0.06)` | — | 侧栏激活项底 |
| `--sidebar-accent-foreground` | `#EAF0FA` | — | 侧栏激活项文字 |
| `--sidebar-border` | `rgba(255,255,255,0.07)` | — | 侧栏描边 |
| `--sidebar-ring` | `rgba(34,211,238,0.45)` | — | 侧栏聚焦环 |
| `--radius` | `0.875rem`（14px） | — | 玻璃卡片大圆角（内部控件 8px） |

> 说明：新方案从 oklch 全面切换到 hex/rgba。oklch 在 Safari < 15.4、Chrome < 111 不支持；rgba 半透明值天然支持背景合成，更适合玻璃拟态。

---

## 4. 字体与排版

- 字体：`Inter`（西文）+ `Noto Sans SC`（汉字）；数字/价格统一 `JetBrains Mono`（回退 IBM Plex Mono）。
- 全局 `font-variant-numeric: tabular-nums`：KPI、价格、时间等所有数字等宽对齐，跳动归零（现有 globals.css 已具备，保留）。
- 大数字（KPI）600–700 字重 + 渐变文字（白 → 青 → 紫，`background-clip: text`）。

| 级别 | 字号 / 行高 | 字重 | 字体 | 场景 |
| --- | --- | --- | --- | --- |
| caption | 11px / 14px | 400–600 | Inter | 表头（大写+字距）、徽章、辅助说明 |
| body-sm | 12px / 18px | 400–500 | Inter/Noto | 表体次要、ticker 符号、时间戳 |
| body | 13px / 20px | 400–500 | Noto Sans SC | 正文、表体、决策描述 |
| body-lg | 14px / 20px | 500 | Noto Sans SC | 导航项、按钮文字 |
| title-sm | 16px / 22px | 600 | Noto Sans SC | 卡片标题 |
| title | 20px / 26px | 700 | Noto Sans SC | 页头标题 |
| kpi | 24–28px / 32px | 700 | JetBrains Mono | 统计卡主数字（渐变文字） |
| display | 32px+ | 800 | JetBrains Mono | 净值大数字、登录页 |

---

## 5. 布局系统

| 元素 | 规格 |
| --- | --- |
| 侧边栏 | 232px 固定宽（折叠态 64px），自身玻璃底 + blur |
| 顶栏 | 52px 高，玻璃底，搜索 / WS 状态 / 版本 / 铃铛 / 时钟 / 用户区 |
| ticker 行情条 | 高约 40px；与顶栏间距 8px；`sticky` 悬浮；胶囊圆角玻璃条 |
| 内容区 | 左右内边距 20px；12px 栅格（列间距 12px）；卡片间距 14px |
| 卡片 | padding 18–20px；圆角 14px（`--radius`）；hover 上浮 2px |
| 两栏布局 | 第一行 `1.3fr / 1fr`（策略表现表 + 账户/AI 决策）；第二行 `1.5fr / 1fr`（净值曲线 + 市场行情表） |
| 底部状态条 | 高 34–36px；连接状态 + 系统状态 + 时钟 |
| 断点 | ≥1440 全量展示；<1280 侧栏折叠 64px；<1024 两栏降为单列 |

---

## 6. 组件规范

**侧边栏**：232px 玻璃底（`--sidebar` + blur 12px）。激活项：左侧 3px 渐变竖条（`#22D3EE→#8B5CF6`，右上圆角 + 同色辉光）+ `--sidebar-accent` 底 + 图标渐变描边（文档级 SVG `url(#gradActive)` 共享 paint server）+ 文字提亮。组标题 10px 大写、字距 0.08em。导航项高 34px、圆角 8px、左右边距 8px；hover 白 4% 底。品牌区 48px：渐变 α 字标（线性渐变文字 + 2px 辉光）+ 双行品牌文字。

**顶栏**：52px 玻璃底。左：搜索胶囊（高 30、圆角 999、`⌘K` 快捷键 kbd；聚焦 `--ring` 辉光）。右：WS 状态胶囊（绿点「实时」/ 琥珀点「轮询中」）→ 版本号（12px mono muted）→「检查更新」幽灵按钮 → 铃铛（红色报错角标数字）→ 时钟（13px mono）→ 分隔线 → 用户区（28px 渐变头像 + 昵称 + Lv 徽章 + 退出图标）。

**ticker 行情条**：玻璃悬浮条（bg `rgba(9,14,25,0.78)` + blur 14px + 1px 边框 + 投影 + 圆角 12px），sticky 悬浮于顶栏下方 8px。每项：符号 12px uppercase muted + 价格 13px mono 600 + 涨跌 12px 着色并带小三角（涨绿跌红）；项间 1px 分隔。末尾 ●LIVE 绿点脉冲。价格变动触发 0.4s 背景闪烁动画（复用 `--profit/--loss` 30% 透明度）。

**统计卡（KPI）**：玻璃卡内 padding 16–18。左：30px 渐变底圆角方块徽章（内为渐变描边图标）+ 12px 标签；中：24–28px 渐变数字（600–700、mono、tabular-nums、白→青→紫渐变文字）；底：28px 高迷你面积图 SVG（渐变填充，随涨跌着色）。次级说明 11px muted。

**表格**：圆角外框卡片包裹（radius 14、`overflow:hidden`、1px 边框）。表头 11px 大写、字距 0.08em、muted、底部分隔线；行高 34px，hover 行 `rgba(255,255,255,0.04)`；数字列 mono tabular-nums 右对齐。**异常值防御性显示**：胜率 2518.0% 这类异常数据用 `--warning` 着色 + 半透明警示底 + 「数据异常」小徽章，避免误导交易判断。

**徽章**：胶囊形（radius 999）、11px 600。SELL：`--loss` 文字 + `rgba(251,113,133,0.10)` 底 + 1px 同色 35% 边框 + 4px 内发光点（`::before`，`box-shadow` 同色辉光）；BUY 反之用 `--profit`；「数据异常」用 `--warning` 同构。

**按钮**：主按钮 = 135° 渐变 `#22D3EE→#8B5CF6` + **深色文字 `#041018`**（整条渐变 AA ≥ 4.5，见 §8）+ 同色 20% 辉光投影；hover 上浮 1px、辉光增强（40%）。幽灵按钮：透明底 + 1px 边框，hover 白 6%。危险按钮：`--loss` 半透明底 + `--loss` 文字。高 32–36px、圆角 8px。

**输入**：高 32px、圆角 8px、底 `--input` + 1px 边框；聚焦：2px `--ring` 描边 + 外辉光；placeholder 12px muted。

**tab**：胶囊形（radius 999）。激活：渐变底 + 深色文字 + 微辉光；未激活：muted 文字，hover 白 5%。高 28–32px。

**图表**：统一渐变语言——面积图 `cyan→violet→transparent` 渐变填充、折线 2px 渐变描边、网格线白 6%；坐标文字 10px mono muted；数据末点高亮圆点 + 辉光；迷你 sparkline 渐变填充随涨跌着色；多序列按 `--chart-1..5` 循环。

**空状态**：玻璃虚线框（1px dashed 白 18% + radius 12 + 白 2% 底）；中央 40px 渐变描边图标；说明 12px muted；渐变主按钮「前往配置」+ 辉光。

---

## 7. 动效规范

统一 spring 缓动：`cubic-bezier(0.34, 1.56, 0.64, 1)`（过冲回弹感，用于入场/徽章弹出）。

| 场景 | 属性 | 缓动 | 时长 |
| --- | --- | --- | --- |
| 卡片入场（页加载/路由） | `opacity` + `translateY(8px)` | spring | 200ms（错峰 40ms stagger） |
| 卡片 hover | `translateY(-2px)` + 边框提亮 | `ease-out` | 150ms |
| 主按钮 hover | `translateY(-1px)` + 辉光增强 | `ease-out` | 150ms |
| 价格变动闪烁 | `background-color`（涨绿/跌红 30% → 透明） | `ease-out` | 400ms |
| 极光背景流动 | `transform`（光斑平移/缩放） | `ease-in-out` | 12s 循环 alternate |
| 状态点脉冲（LIVE/连接） | `opacity`/`scale` | `ease-in-out` | 2s 循环 |
| tab 切换 | `color`/`background` | `ease` | 150ms |

降级：`@media (prefers-reduced-motion: reduce)` 全局关闭动画与过渡。

---

## 8. 无障碍与对比度

关键计算（按最坏情况底色——玻璃卡片最深处）：

| 前景 / 背景 | 对比度 | 结论 |
| --- | --- | --- |
| `#EAF0FA` / `#070B15` | ≈ 16.0 : 1 | AAA |
| `#94A1BC` / `#070B15` | ≈ 8.0 : 1 | AAA |
| `#94A1BC` / 卡片合成色 | ≈ 7.0 : 1 | AAA |
| `#34D399` / 卡片合成色 | ≈ 9.2 : 1 | AAA |
| `#FB7185` / 卡片合成色 | ≈ 6.7 : 1 | AAA |
| `#FBBF24` / 卡片合成色 | ≈ 10.5 : 1 | AAA |
| `#AEB9D0` / 侧栏合成色 | ≈ 9.6 : 1 | AAA |
| `#041018`（深色文字）/ `#22D3EE` | ≈ 11.2 : 1 | AAA |
| `#041018` / `#8B5CF6`（渐变最暗端） | ≈ 4.8 : 1 | AA 通过 |
| 徽章文字 `#FB7185` / 10% 玫瑰底 | ≈ 5.8 : 1 | AA 通过 |

设计决策与保障：

1. **主按钮用深色文字**：`#22D3EE` 亮度很高，白字对比仅 ≈ 1.8（不合格）；改用近黑深蓝文字后整条青→紫渐变全部 ≥ 4.5:1。这也是 Linear / Stripe 深色按钮的通行做法。
2. **半透明层的文字按最暗底色核算**：极光光斑最亮处（≈ 8% 透明度）使底色亮度小幅抬升，核算后次要文字仍 ≥ 6:1；若个别极端叠加不足，用实色内层（如 `--popover`）兜底。
3. **颜色不作唯一信息通道**：涨跌同时使用 ▲▼ 三角 + 颜色；LIVE/连接状态同时有图形点与文字。
4. **键盘可达**：`:focus-visible` 2px `--ring` 描边 + 外辉光；导航/表格/按钮均可 Tab 聚焦。
5. **实时性**：行情区用 `aria-live="polite"` 的屏外摘要播报「BTC 下跌 0.75%」，避免读屏逐字轰炸。
6. **数字稳定性**：全局 `tabular-nums` 防跳动；渐变 KPI 数字旁保留 `aria-label` 原文，规避 `background-clip:text` 对朗读的干扰。

---

## 9. 落地实施

### 9.1 替换 globals.css（3 步）

1. **替换 token 块**：打开 `frontend-next/src/app/globals.css`，用 `docs/ui-redesign/previews/tokens-02.css` 的 `:root/.dark` 块整体替换第 54–102 行的旧变量块；保留 `@import`、`@theme inline`、`@layer base` 与现有动画。
2. **切换字体**：`@theme inline` 第 11 行 `--font-mono: var(--font-geist-mono)` 改为 `--font-mono: var(--font-mono)`；将 `frontend-next/src/app/layout.tsx` 中 next/font 的 Geist Sans / Geist Mono 换为 Inter / JetBrains Mono（`variable` 名保持不变，改动最小）。
3. **注入极光背景层**：在根布局 `frontend-next/src/app/layout.tsx` 或 `AppShell.tsx` 渲染一个 `position:fixed; inset:0; z-index:0` 的 `.aurora-bg` 层（3 个径向渐变光斑 cyan/violet/emerald，各 5–8% 透明度 + 顶部光带 + 12s 流动动画），内容容器 `position:relative; z-index:1`；并在 globals.css 追加 `aurora` 动画 keyframes 与玻璃工具类（`.glass-card`、`.grad-text`、`.grad-bar`、`.btn-primary-grad`）。

### 9.2 组件改造清单（对应真实文件）

| 文件 | 改造点 |
| --- | --- |
| `frontend-next/src/app/globals.css` | token 替换；新增极光 keyframes / 玻璃与渐变工具类；保留 body `tabular-nums` 与基础层 |
| `frontend-next/src/app/layout.tsx` | next/font 换 Inter + JetBrains Mono；渲染 `.aurora-bg` 背景层 |
| `frontend-next/src/components/layout/AppShell.tsx` | 侧栏 224→232px、顶栏 48→52px；内容区 12px 栅格 + 卡片间距 14px；接入 ticker 悬浮条 |
| `frontend-next/src/components/layout/Sidebar.tsx` | 激活指示条（第 164 行 `w-0.5 bg-primary`）改为 3px 渐变竖条 + 辉光；logo 区（第 106–108 行）改渐变 α 字标 + 发光；图标渐变描边（共享 `url(#gradActive)`）；组标题与 hover 微调 |
| `frontend-next/src/components/layout/TopBar.tsx` | 高度 52px；搜索框胶囊化 + `⌘K`；WS 状态胶囊化（实时/轮询中）；时钟/铃铛/用户区按 §6 |
| `frontend-next/src/app/dashboard/page.tsx` | 页头（标题 + 状态徽章）；4 统计卡（渐变数字 + 图标徽章 + 迷你面积图）；策略表现表（圆角外框 + 大写表头 + 胜率异常防御显示）；账户空状态卡（玻璃虚线框 + 渐变「前往配置」）；AI 决策列表 SELL 徽章；净值曲线渐变面积图；ticker 数据条 |
| `frontend-next/src/components/ui/card.tsx / badge.tsx / button.tsx / table.tsx` | Card 加 `backdrop-blur` 与 hover 上浮；Badge 增 sell/buy/warning 胶囊变体（含发光点）；Button 主变体加渐变 + 深色文字；Table 包圆角外框 |
| 其余页面（strategy / risk / ops / settings …） | 随 token 自动换肤；重点组件（tab、输入、图表）按 §6 跟进 |

---

## 10. 风险与取舍

1. **backdrop-filter 性能**：大量模糊层在 Electron 桌面端 / 低端 GPU 会掉帧。对策：同时处于模糊态的层 ≤ 8（内容区可见卡片约 6–8 张，可接受）；hover 时才增强；提供降级路径（`@supports not (backdrop-filter: blur(1px))` 时回退 `rgba(13,18,30,0.9)` 实色）。注意：祖先元素设 `filter` 会禁用子元素 backdrop-blur（Chromium 行为），`.aurora-bg` 层需与内容层分离、避免对内容容器加 filter。
2. **半透明叠加的可读性波动**：极光光斑会抬升卡片背后的亮度。对策：光斑透明度封顶 8%，文字对比按最亮处核算仍 AA；极端场景用 `--popover` 实色兜底。
3. **渐变文字兼容性**：`background-clip: text` 在旧 Safari 不支持。对策：提供纯色回退（`color: #7DD3FC` 优先声明）；渐变数字同时保留语义文本（aria-label）。
4. **视觉冲击与交易效率的平衡**：极光/辉光只用于背景、激活态与 KPI，数据区（表格、行情）保持高对比与高密度；防止装饰干扰扫读与报警辨识。
5. **大圆角与密度的取舍**：卡片 14px 大圆角营造玻璃质感，内部控件（按钮、输入、表格行）收至 8px，保证层级清晰且不牺牲信息密度。
6. **从 oklch 迁移的回归**：hex/rgba 表达无法直接写「半透明的 oklch 合成」，替换后需回归核对各页面的实景对比度（建议用 axe 或 Lighthouse 扫一遍）。
