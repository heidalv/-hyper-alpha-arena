# Hyper-Alpha-Arena 前端 UI 重设计提案总览

> 面向「Heidalv Alpha Arena — 加密量化交易终端」的 4 套整体前端设计方案。
> 每套方案均为**可直接落地的完整设计系统**：设计文档 + 可打开的真实 HTML 预览 + 可复制进 `globals.css` 的 token 文件。

---

## 一、现状审计（为什么要重设计）

对 `frontend-next/src/app/globals.css`、`Sidebar.tsx`、`TopBar.tsx`、仪表盘页面与实拍截图进行了审计，当前 UI 的问题：

| # | 问题 | 证据 |
|---|------|------|
| 1 | **页面无层级结构**：没有页头/标题/面包屑，所有页面平铺直叙 | 各 page.tsx 与截图 |
| 2 | **卡片与表格区分弱**：深色块 + 低对比边框，视觉"一坨黑" | 截图 00_home / 01_dashboard |
| 3 | **数字排版不佳**：非等宽字体导致价格/盈亏跳动 | globals.css 已加 tabular-nums，但部分组件未落实 |
| 4 | **无品牌感**：唯一的品牌元素是侧边栏一个 α 图标，无色彩记忆点 | Sidebar.tsx |
| 5 | **异常数据无防御显示**：胜率显示 2518.0%（数据 bug），UI 无任何提示 | 截图实测 |
| 6 | **无微动效/反馈**：hover、价格变动、状态变化无视觉反馈 | 全局搜索无动画 |
| 7 | **只有深色一种模式**，无浅色/跟随系统选项 | layout.tsx 强制 `dark` |
| 8 | **信息架构拥挤**：侧边栏 5 组 17 项，无搜索、无分组折叠 | Sidebar.tsx |

## 二、四套方案一览

| | **A 黑曜终端** | **B 极光引擎** | **C 曙光研究院** | **D 简一** |
|---|---|---|---|---|
| 定位 | 职业交易员工作台（深色） | 下一代加密交易台（深色） | 机构研究台（**浅色**） | 极简专注台（深色） |
| 气质 | 精密、克制、高密度 | 深邃、科技、有生命力 | 明亮、信赖、可打印 | 冷静、干净、留白 |
| 参照 | Bloomberg / TradingView Pro | OKX / Phantom / Linear | Morningstar / TradingView 浅色 | Linear / Vercel / Arc |
| 主色 | 科技蓝 `#4C8DFF` | 极光渐变 青`#22D3EE`→紫`#8B5CF6` | 靛蓝 `#3456D9` | 翡翠绿 `#10B981`（唯一强调色） |
| 圆角 | 6px（锐利） | 14px（玻璃） | 8px | 10px |
| 信息密度 | ★★★★★ | ★★★★ | ★★★ | ★★ |
| 视觉冲击 | ★★★ | ★★★★★ | ★★★★ | ★★★★ |
| 落地风险 | 最低（现主题的进化） | 中（玻璃拟态性能） | 低（纯 token 切换） | 低（改动面集中在样式） |
| 适合页面 | 全部 | 全部 | risk/intel/charts/settings | dashboard/strategy/risk |

## 三、方案详情

- [01 黑曜终端 Obsidian Terminal](01-obsidian-terminal.md) · [预览](previews/01-obsidian-terminal.html)
- [02 极光引擎 Aurora Engine](02-aurora-engine.md) · [预览](previews/02-aurora-engine.html)
- [03 曙光研究院 Dawn Research](03-dawn-research.md) · [预览](previews/03-dawn-research.html)
- [04 简一 Zen Mono](04-zen-mono.md) · [预览](previews/04-zen-mono.html)

**预览入口：** 打开 [`previews/index.html`](previews/index.html)（四套方案画廊）或直接双击各 `previews/0X-*.html`。

## 三·五、极光引擎全站扩展（已选定方案 B 全页面重做）

> 用户选定 **B 极光引擎 Aurora Engine** 作为全站设计语言，全部 21 个页面统一重做。

**入口：** [`aurora/index.html`](aurora/index.html) —— 单文件全站预览应用（左侧真实导航 18 项 + 顶栏 + ticker + 底部状态栏，点击导航切换页面，hash 路由 `#/dashboard` 直达，侧边栏可折叠、搜索可过滤导航）。

| 页面 | 文件 | 页面 | 文件 |
|---|---|---|---|
| 仪表盘 | `aurora/pages/01-dashboard.html` | 交易所管理 | `aurora/pages/11-exchange.html` |
| AI 策略 | `aurora/pages/02-strategy.html` | 全市场数据中台 | `aurora/pages/12-intel.html` |
| VIP AI 选币 | `aurora/pages/03-coin-select.html` | 因子系统 | `aurora/pages/13-factors.html` |
| Agent 监控 | `aurora/pages/04-agent-monitor.html` | 算力中心 | `aurora/pages/14-compute.html` |
| 模拟交易 | `aurora/pages/05-paper-trading.html` | 风控监控 | `aurora/pages/15-risk.html` |
| 实盘交易 | `aurora/pages/06-live-trading.html` | 运维看板(含报错中心) | `aurora/pages/16-ops.html` |
| 套利中心 | `aurora/pages/07-arbitrage.html` | 智能学习 | `aurora/pages/17-intelligent-learning.html` |
| Hyperliquid | `aurora/pages/08-hyperliquid.html` | 设置 | `aurora/pages/18-settings.html` |
| 短线配置 | `aurora/pages/09-scalp.html` | 系统日志 | `aurora/pages/19-logs.html` |
| 长线配置 | `aurora/pages/10-long.html` | 图表中心 / 登录 | `aurora/pages/20-charts.html` · `21-login.html` |

- **设计系统**：`aurora/aurora.css`（极光 token + 玻璃卡片/KPI/表格/徽章/按钮/表单/tab/进度/时间线/空状态等统一组件类）
- **二级组件库**：`aurora.css` 追加 Modal / Drawer / Toast / 命令面板 / 通知中心 / 下拉菜单 / 多步向导 / 开关 / 确认框 / 骨架屏 / 面包屑 / 折叠区 / 工具提示
- **全局交互层**：`shell.html` 内置 `AuroraUI`（`data-modal` / `data-drawer` / `data-close` / `data-toast` / `data-menu` / `data-confirm` 属性即用）；**Ctrl+K 命令面板**（真实页面清单）、铃铛**通知中心**、ESC 关闭、遮罩点击关闭
- **二级视图已全覆盖**：45 个弹窗、11 个抽屉、4 个下拉菜单、35 个确认框、109 个 toast、2 处多步向导、8 个开关、20 个 tab-pane——全部基于真实代码结构（会话管理/下单表单/五步流水/因子评估/调度任务/报错堆栈等），数据为模拟值
- **第三层细节（深度打磨）**：分页器/排序表头/表尾汇总行/表单校验态/滑块/步进器/快捷填充/复选框/订单状态机/冷却矩阵/逐笔成交流/深度曲线/K线十字线提示；全局实时机制——ticker 每 2.2s 跳动、KPI 数字滚动（count-up）、全站自动倒计时（刷新/冷却/赛季/结算）、页面切换骨架屏、14 页页头刷新倒计时+延迟指示
- **组件类参考**：`aurora/pages/README.md`（第 14 节为二级视图规范、第 15 节为深度细节规范；落地时可直接映射到 shadcn 组件）
- **构建**：`node aurora/build.js` 合并片段 → `aurora/index.html`
- **页面结构 100% 还原真实应用**：所有面板/表格/表单字段名照抄自 `frontend-next/src/app/*/page.tsx`，仅数据为模拟值；防御性显示（如胜率 2518.0% 标「数据异常」）贯穿全部页面
- 日志/图表/登录三页不在侧边栏导航中，用地址栏 hash `#/logs`、`#/charts`、`#/login` 直达

## 四、横向对比与推荐

| 维度 | A 黑曜终端 | B 极光引擎 | C 曙光研究院 | D 简一 |
|---|---|---|---|---|
| 模式 | 深色 | 深色 | **浅色** | 深色 |
| 主色 | 科技蓝 `#4C8DFF` | 青紫渐变 `#22D3EE→#8B5CF6` | 靛蓝 `#3456D9` | 翡翠绿 `#10B981`（唯一强调） |
| 圆角 / 密度 | 6px / ★★★★★ | 14px / ★★★★ | 8px / ★★★ | 10px / ★★ |
| 视觉记忆点 | 底部 Bloomberg 状态栏、整串着色 tape | 极光背景、玻璃卡片、渐变 KPI | 墨色页头×纸白正文（研报感） | 无边框分层、大留白、强调色纪律 |
| 对比度 | 全 AA（实测 8.5–16.4:1） | 渐变按钮用深色文字达 AA | 浅色天然高对比 | 主文字 17.3:1 AAA（翡翠按钮 AA 缺口已注明对策） |
| 落地风险 | 最低（现主题同构进化） | 中（blur 性能、渐变可访问性） | 低（纯 token 切换） | 低（但高密度页需保留 dense 变体） |
| 参照 | Bloomberg / TradingView Pro | OKX / Phantom / Linear | Morningstar / 彭博浅色 | Linear / Vercel / Arc |

### 推荐结论

1. **主线首选：A 黑曜终端** — 与现主题同构（token 同名、圆角同族），风险最低、最贴合量化终端定位，是"今天换、明天用"的选择。
2. **想要焕然一新：B 极光引擎** — 视觉冲击最强，适合做品牌门面（登录页、仪表盘），与 A 共用组件结构，可后期再做。
3. **浅色需求：C 曙光研究院** — 唯一浅色方案，适合研究/风控/设置页 + 研报导出，也是做"深/浅双主题"的起点（`.dark` 分支已预留）。
4. **专注场景：D 简一** — 适合 dashboard/strategy/risk 这类"盯数字"页面；/ops、/compute 等高密度页保留 dense 变体（只套色板不套留白）。

### 落地路线（任一主题通用，约 1 天/套）

1. **换 token（30 分钟）**：把 `previews/tokens-0X.css` 的 `:root` 块复制进 `frontend-next/src/app/globals.css` 替换同名块（oklch→hex，`@theme inline` 映射层零改动）。
2. **换字体（15 分钟）**：`src/app/layout.tsx` 用 `next/font/google` 引入对应字体（Inter/Noto Sans SC/Manrope + JetBrains Mono/IBM Plex Mono），注入 `--font-sans/--font-mono`。
3. **改组件（按文档 §9.2 清单）**：Sidebar（宽度/激活态）→ TopBar → PageHeader/TickerBar/StatusBar 新增 → dashboard 页 KPI/表格/空状态 → `cells.tsx` 加等宽数字与异常值防御显示 → 其余页面随 token 自动换肤。

> 📌 预览查看方式：`previews/index.html` 画廊或直接双击各 `previews/0X-*.html`（均为自包含单文件，1440×900 布局，双击即开）。
> 本机沙箱无法启动浏览器自动截图（无网络下载 Playwright 浏览器、系统无 Chrome/Edge、Electron 被环境阻止），预览以真实 HTML 交付；如需我用视觉模型评审效果，打开预览后截图发给我即可。
