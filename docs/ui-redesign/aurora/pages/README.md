# Aurora 全站设计系统 — 组件类参考（页面片段作者必读）

> 所有页面片段使用本系统类名，禁止自定义新类（除非用行内 style 做单点微调）。
> 页面片段 = 一个 `<section class="page" id="page-xxx">`，最终由 build.js 合并进 `../index.html`。

## 1. 页面骨架

```html
<section class="page" id="page-dashboard" data-title="仪表盘">
  <div class="page-head">
    <div>
      <h1 class="page-title">仪表盘 <span class="tag cyan">LIVE</span></h1>
      <p class="page-sub">AI 驱动的多周期加密量化交易终端 · 数据 10s 轮询</p>
    </div>
    <div class="page-actions">
      <button class="btn btn-ghost2 btn-sm">导出</button>
      <button class="btn btn-primary btn-sm">刷新</button>
    </div>
  </div>
  ...内容...
</section>
```

## 2. 布局网格

- `.grid2` 两栏 / `.grid3` 三栏 / `.grid-2-1` 左宽右窄 / `.grid-1-2` 左窄右宽 / `.grid-3-2` / `.stack` 纵向堆叠
- 都自带 `margin-bottom: 14px`；卡内边距统一由 `.glass` + `.card-body` 控制

## 3. 玻璃卡片

```html
<div class="glass hover">
  <div class="card-head">
    <div class="card-title"><svg>…</svg>策略表现</div>
    <span class="card-hint">更新于 2s 前</span>
  </div>
  <div class="card-body">…内容…</div>
</div>
```
- 表格卡片用 `<div class="card-body tbl-body"><div class="tbl-wrap">…table…</div></div>`

## 4. KPI 统计卡

```html
<div class="kpi-grid">
  <div class="glass kpi">
    <div class="kpi-label">总资产</div>
    <div class="kpi-value grad">$12,480.52</div>
    <div class="kpi-change up">▲ +2.4%</div>
    <div class="kpi-extra">较昨日 +$291.4</div>
    <div class="kpi-spark"><svg width="72" height="28" viewBox="0 0 72 28"><path d="M0 22 L10 18 L20 20 L30 12 L40 14 L50 8 L60 10 L72 4" stroke="#22D3EE" fill="none" stroke-width="1.5"/></svg></div>
  </div>
</div>
```
- `.kpi-value` 可用 `.grad`（白→青→紫渐变）、`.grad-green`、`.grad-red`
- 涨跌：`.kpi-change.up/.down/.flat`

## 5. 数据表格

```html
<div class="tbl-wrap"><table class="tbl">
  <thead><tr><th>币种</th><th class="r">价格</th><th class="r">24h</th><th class="c">状态</th></tr></thead>
  <tbody>
    <tr><td><span class="sym">BTC</span></td><td class="r">$70,905.00</td><td class="r down">-0.75%</td><td class="c"><span class="badge ok"><span class="bdot"></span>正常</span></td></tr>
  </tbody>
</table></div>
```
- 数字列一律 `class="r"`（右对齐 + 等宽）；涨 `class="r up"` 跌 `class="r down"` 警告 `class="r warn"`
- 徽章：`.badge sell|buy|warn|info|violet|ok|dim`（内含可选 `.bdot` 发光点）
- 空表格：`<div class="tbl-empty">暂无数据</div>` 或 `.empty` 空状态块

## 6. 按钮

- `.btn.btn-primary` 青紫渐变主按钮（深色文字） / `.btn.btn-ghost2` 玻璃次要 / `.btn.btn-danger` 红渐变 / `.btn-sm` 小号
- 图标：内联 `<svg viewBox="0 0 24 24" style="width:13px;height:13px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round">`（可用 lucide 图标 path）

## 7. 表单

```html
<div class="field"><label class="label">账户名称</label><input class="input" placeholder="如：BTC趋势"></div>
<div class="field"><label class="label">交易所</label><div class="select-wrap"><select class="select"><option>Hyperliquid</option></select></div></div>
<div class="form-grid"><div class="field">…</div><div class="field span2">…</div></div>
```

## 8. Tab 与分段

```html
<div class="tab-row">
  <button class="tab-item active">生命周期</button>
  <button class="tab-item">三通道健康</button>
</div>
<!-- 或胶囊分段 -->
<div class="seg">
  <button class="seg-item active">全部</button>
  <button class="seg-item">运行中</button>
</div>
```

## 9. 信息行 / 进度 / 状态

```html
<div class="kv"><span class="k">总权益</span><span class="v">$12,480.52</span></div>
<div class="kv"><span class="k">日亏损率</span><span class="v warn">-8.2%</span></div>
<div class="bar" style="margin:6px 0"><div class="bar-fill" style="width:62%"></div></div>  <!-- 也可 green/red/amber -->
<div class="status-line"><span class="si"><span class="dot"></span>Hyperliquid 已连接</span></div>
```
- 圆点：`.dot`（绿）`.dot.red` `.dot.amber` `.dot.cyan` `.dot.gray` `.dot.pulse`

## 10. 时间线（AI 决策流）

```html
<div class="timeline">
  <div class="tl-item sell">
    <div class="tl-head"><span class="badge sell">SELL</span><span class="tl-sym">BNB</span><span class="tl-time">6天前</span></div>
    <div class="tl-reason">多周期编排器强烈建议做空（中线看跌置信度 100%）…</div>
  </div>
</div>
```
- `.tl-item` 默认青色点；`.sell` 红点 / `.buy` 绿点

## 11. 空状态

```html
<div class="empty">
  <div class="empty-ico"><svg>…</svg></div>
  <div class="empty-t">未配置 Hyperliquid 账户</div>
  <div class="empty-s">配置 API 凭证后即可开始实盘交易</div>
  <button class="btn btn-primary btn-sm" style="margin-top:4px">前往配置</button>
</div>
```

## 12. 文本工具

`.mono` 等宽 / `.up` 绿 / `.down` 红 / `.warn` 琥珀 / `.muted` 灰 / `.num` / `.small` / `.tiny` / `.bold` / `.flex`（flex 行）/ `.spread`（两端对齐）

## 13. 规范红线

1. **涨绿跌红**：profit=#34D399 / loss=#FB7185 / warning=#FBBF24，不得调换
2. **数字等宽**：所有价格/百分比/统计数字用 `class="r"` 或 `.mono` 或 `.num`
3. **图标**：只允许内联 SVG（stroke 风格，`fill:none;stroke:currentColor;stroke-width:1.6~1.8`），参考 lucide 图标 path；禁止外链图片
4. **徽章语义**：SELL→`.badge.sell`、买入/正常→`.badge.buy`/`.badge.ok`、异常/警告→`.badge.warn`、信息→`.badge.info`
5. **防御性显示**：异常数据（如胜率 2518.0%）必须加 `.badge.warn`「数据异常」提示 + 数值 `class="r warn"`
6. **无横向滚动**：内容宽 1192px（1440 - 侧栏 232 - 左右 padding 40 - 滚动条），表格用 `.tbl-wrap` 包住
7. **不写** `<html>/<head>/<body>/<style>/<script>` 标签；片段只含 1 个 `<section class="page">` 及其内部
8. 每页必须真实还原给定页面结构（面板/表格/表单字段名照抄），数据可用合理模拟值
9. UTF-8 无 BOM；中文注释可少量使用

## 14. 二级视图规范（Modal / Drawer / Toast / 菜单）

外壳已提供全局交互层（`window.AuroraUI`），页面片段**只写 HTML，不写 JS**：

### 14.1 打开/关闭绑定（按钮上写属性即可）

```html
<button class="btn btn-primary" data-modal="m-create-session">创建会话</button>
<button class="row-btn cyan" data-drawer="d-session-detail">查看详情</button>
<button class="btn btn-ghost2" data-toast='{"type":"ok","title":"保存成功","msg":"配置已更新"}' >保存</button>
<button class="row-btn red" data-confirm='{"title":"确认删除会话？","msg":"将同时删除策略与持仓记录","danger":true}'>删除</button>
```
- `data-modal="id"` / `data-drawer="id"` → 打开对应 id 的弹窗/抽屉；`data-close` → 关闭所在的弹窗/抽屉
- `data-toast` → JSON：`{type: ok|err|warn|info, title, msg}`
- `data-confirm` → 弹出确认框（全局机制，无需额外 HTML）

### 14.2 Modal 模板（放在本页 `<section class="page">` 内任意位置）

```html
<div class="mask" id="m-create-session">
  <div class="modal wide">
    <div class="modal-head">
      <div><div class="modal-title"><svg>…</svg>创建 AI 交易会话</div><div class="modal-sub">启动一个新的自动交易会话</div></div>
      <button class="modal-x" data-close><svg viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12"/></svg></button>
    </div>
    <div class="modal-body">…表单（.field/.label/.input/.select）…</div>
    <div class="modal-foot">
      <button class="btn btn-ghost2" data-close>取消</button>
      <button class="btn btn-primary" data-close data-toast='{"type":"ok","title":"会话已创建","msg":"正在启动…"}'>创建</button>
    </div>
  </div>
</div>
```
注意：**外层 `.mask` 带 id，内层 `.modal` 不带**；data-close 由全局 JS 处理。

### 14.3 Drawer 模板（同样放在 section 内）

```html
<div class="drawer wide" id="d-session-detail">
  <div class="drawer-head">
    <div><div class="drawer-title">会话详情 · BTC 趋势</div><div class="drawer-sub">session #12 · 运行中</div></div>
    <button class="modal-x" data-close><svg viewBox="0 0 24 24"><path d="M18 6 6 18M6 6l12 12"/></svg></button>
  </div>
  <div class="drawer-body">…kv 行 / 表单 / 表格…</div>
  <div class="drawer-foot"><button class="btn btn-danger" data-close>停止会话</button><button class="btn btn-primary" data-close>保存修改</button></div>
</div>
```
（.drawer 无 .mask 包裹，全局 JS 直接管理 open/close）

### 14.4 其他二级组件

- **页内 tab 详情**：`.tab-row` + 多个 `.tab-pane`（`<div class="tab-pane" id="…">`，默认第一个显示，其余 `display:none`——用行内 style，展示态即可）
- **下拉菜单**：`<div class="menu" id="m-xxx">` + 触发按钮 `data-menu="m-xxx"`（.menu-item / .menu-sep）
- **面包屑**：`.crumbs`（`<a>` 链接 + `.sep` 分隔）
- **向导步骤条**：`.wizard-steps` + `.w-step`（.active 当前 / .done 完成）+ `.w-line`
- **开关**：`<span class="switch on"></span>`（on=开）
- **骨架屏**：`.skeleton`（配合宽高行内样式）
- **页内详情面板**：`.detail-pane`（master-detail 布局右列）
- **表格行操作**：`.row-btn`（cyan/green/red hover 变体）
- **表格选中行**：`<tr class="sel">`
- **可折叠**：`.collapse-head`（open 时加 .open）+ `.collapse-body`（open 时加 .open，行内 display 控制）

### 14.5 二级视图配额（每个页面至少补齐）

| 页面 | 必做二级视图 |
|---|---|
| dashboard | 账户切换下拉菜单 + 三周期状态详情弹窗 |
| strategy | 创建会话弹窗（交易账户/交易对/交易模式）+ 会话详情抽屉（风险等级/风控模式/AI槽位/胜率/PnL）+ 决策详情弹窗 |
| coin-select | 选币详情弹窗（verdict/陷阱/流动性/历史命中明细）|
| agent-monitor | 会话详情抽屉 + Tick 任务详情弹窗 + 日志详情弹窗 |
| paper-trading | 创建账户弹窗 + 平仓确认 + 止盈止损设置弹窗 |
| live-trading | 下单确认弹窗（OrderForm：做多/做空、交易对、数量、杠杆、TP/SL）+ 撤单确认 + 积分明细抽屉 |
| arbitrage | 引擎配置弹窗 + 返佣机会详情弹窗 + 策略详情抽屉 |
| hyperliquid | 订单历史抽屉（tab：订单/成交/资金费率）+ 持仓详情弹窗 |
| scalp | 高级参数弹窗 + 回测结果抽屉 |
| long | 提示词编辑弹窗（系统角色/任务指令/测试）+ 路线图详情弹窗 |
| exchange | 新建账户向导（3 步：基本信息→LLM→风控）+ API 凭证测试弹窗 + 账户详情抽屉 |
| intel | 单币种详情视图：tab（K线/深度/资金费率/OI）+ 周期切换 + 交易所切换 + 币种搜索下拉 |
| factors | 单因子详情弹窗（|IC|/ICIR/衰减半衰期/数据完整率/admission 判定）|
| compute | GPU 详情弹窗 + 训练任务详情抽屉（样本外AUC/重要特征Top5/绑定车道）|
| risk | 单账户风控详情弹窗 + 告警详情 + 熔断历史抽屉 |
| ops | 服务心跳详情弹窗 + 报错详情弹窗（堆栈/上下文）+ 管线步骤详情抽屉 |
| intelligent-learning | 7 个 tab 全部完整化（每 tab 独立内容卡组，见规范）|
| settings | LLM 测试结果弹窗 + 账户编辑弹窗 + 密钥详情弹窗 |
| logs | 日志详情弹窗（上下文/堆栈）+ 时间范围选择 |
| charts | 周期切换 seg + 指标叠加菜单 + 导出确认 |
| login | 登录失败错误提示（toast/表单错误态）|

## 15. 深度细节规范（第三层组件）

### 15.1 表格细节
```html
<!-- 排序表头 -->
<th class="sortable sorted">价格<span class="sort-ico"><svg viewBox="0 0 24 24"><path d="M12 5v14M5 12l7 7 7-7"/></svg></span></th>
<!-- 汇总行 -->
<tfoot><tr><td>合计</td><td class="r up">+$1,204.18</td></tr></tfoot>
<!-- 展开行 -->
<tr class="expand"><td colspan="6"><div class="expand-body"><div class="eb">参数A <b>12.5</b></div>…</div></td></tr>
<!-- 分页器 -->
<div class="pager">
  <span class="pager-info">共 128 条 · 第 1/9 页</span>
  <button class="pager-btn dis">‹</button><button class="pager-btn on">1</button><button class="pager-btn">2</button><button class="pager-btn">3</button><span class="pager-btn dis">…</span><button class="pager-btn">9</button><button class="pager-btn">›</button>
</div>
```

### 15.2 表单细节
```html
<div class="field">
  <label class="label">账户名称<span class="req">*</span></label>
  <input class="input err" placeholder="必填">
  <div class="field-err"><svg viewBox="0 0 24 24"><path d="M12 3 2.5 20h19L12 3z"/></svg>账户名称不能为空</div>
</div>
<div class="field">
  <label class="label">限价</label>
  <div class="input-group"><span class="pre">$</span><input placeholder="0.00"><span class="post">USD</span></div>
</div>
<!-- 滑块（杠杆） -->
<div class="range-row"><input class="range" type="range" min="1" max="20" value="5"><span class="range-val">5x</span></div>
<div class="range-ticks"><span>1x</span><span>5x</span><span>10x</span><span>20x</span></div>
<!-- 步进器（数量） -->
<div class="stepper"><button>−</button><input value="0.020"><button>+</button></div>
<!-- 快捷填充 -->
<div class="chip-row"><button class="chip-btn on">25%</button><button class="chip-btn">50%</button><button class="chip-btn">75%</button><button class="chip-btn">100%</button></div>
<!-- 复选框 -->
<label class="ck on"><span class="ck-box"><svg viewBox="0 0 24 24"><path d="M20 6 9 17l-5-5"/></svg></span>移动止盈</label>
```

### 15.3 状态细节
```html
<!-- 倒计时（自动倒数） -->
<span class="cd cd-auto" data-cd="30"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/></svg>下次刷新 <b>30</b>s</span>
<!-- 数据延迟 -->
<span class="lag"><svg viewBox="0 0 24 24"><path d="M12 9v4M12 17v.01"/></svg>行情延迟 2.4s</span>
<!-- 加载态按钮 -->
<button class="btn btn-primary loading"><span class="spin"></span>提交中…</button>
<!-- 骨架屏（页面切换时外壳自动演示，页面内可用作局部加载态） -->
<div class="skeleton" style="height:80px;border-radius:12px"></div>
<!-- 数字滚动 -->
<div class="count-up" data-val="12480.52" data-dec="2" data-pre="$">$0.00</div>
```

### 15.4 图表细节
```html
<!-- K 线 hover 十字线提示 -->
<div class="kwrap">
  <div class="kline-hint"><span>开 70,120</span><span>高 70,640</span><span>低 69,980</span><span>收 70,905</span><span class="up">+0.62%</span></div>
  <svg>…蜡烛图…</svg>
</div>
<!-- 订单状态机 -->
<span class="ord-flow"><span class="st done">挂单</span><span class="arrow">→</span><span class="st done">部分成交 42%</span><span class="arrow">→</span><span class="st on">成交中</span><span class="arrow">→</span><span class="st">完成</span></span>
```

### 15.5 打磨红线
1. 表格一律配分页器或汇总行（二选一）；长表加排序表头
2. 表单：必填项加 `.req`，关键字段演示错误态/正确态各一处；金额/价格字段用 `.input-group` 前后缀
3. 每个页面至少 1 处 `.cd-auto` 自动倒计时（刷新/冷却/赛季/到期）
4. 数据刷新类页面加 `.lag` 延迟指示
5. 大数字（权益/余额）用 `.count-up` 滚动
6. K 线/行情图用 `.kwrap` + `.kline-hint` 悬浮提示
7. 危险按钮加 `.btn.loading` 演示态或 data-confirm
8. 保持既有结构：只做增量增强，不重写页面
