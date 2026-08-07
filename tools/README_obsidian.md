# Obsidian Agent 进化中心 — Demo Vault

把这个 vault 当作 001Alpha 的 **「离线 Agent / 知识库可视化阅读器」**。
React 前端继续做实时交易和监控,这个 vault 专门做**复盘、归因、知识沉淀**。

---

## 🚀 5 分钟上手

### 1. 装 Obsidian
- 官网下载: https://obsidian.md/
- Windows 安装包,一路下一步

### 2. 打开 Vault
启动 Obsidian → **「Open folder as vault」** → 选:

```
D:\BaiduNetdiskDownload\001Alpha-Windows迁移包-20260610-2228\001Alpha\Hyper-Alpha-Arena\obsidian_vault
```

> 第一次打开会提示「信任此文件夹」,选 **Trust author**。

### 3. 装 Dataview 插件(关键!不装就看不到表)
- 左下角齿轮 ⚙️ → **第三方插件 (Community plugins)**
- 关掉「安全模式」(Restricted mode)
- 点「浏览 (Browse)」→ 搜 **`Dataview`** → 安装 → 启用
- (可选)同样装 **`Canvas`** 通常已内置,无需装

### 4. 打开主页
左侧文件树 → 双击 **`Agent进化中心.md`**
你会看到 8 张动态表(高严重度报告、亏损教训、Hermes 四层进化……),全是真实数据。

### 5. 打开 Canvas
左侧文件树 → `_canvas/` → **`Hermes四层进化.canvas`**
4 个彩色节点(L1 智慧 / L2 Prompt / L3 架构 / L4 创生),点节点跳详情。

### 6. 看关系网(知识库可视化精髓)
打开任意一条 `02-交易教训/lesson-*.md` → 点顶部工具栏的 **「关系图谱」图标**(或 `Ctrl+G`)
你会看到 `[[币种]]` 和 `[[策略]]` 自动织成的关系网。

---

## 📊 数据来源

| 数据 | 源 | 数量 | 用途 |
|---|---|---|---|
| OpenCode 分析报告 | `data/opencode_reports/*.md` | 190 | agent 思考/复盘结果 |
| 交易教训 | `data/qaa_knowledge/trading_lessons.jsonl` | 138 | RAG 知识库(双链 Graph) |
| Hermes 进化库 | `data/hermes_evolution.db` (SQLite) | 719+ | 四层进化引擎的全部产出 |
| **Agent 决策仲裁** | `data/decision_arbiter.jsonl` | 186 | **agent 怎么一步步做决策(拦截/放行)** |
| **治理器决策** | `data/governor_decisions.jsonl` | 106 | **opencode 提案的参数裁决** |
| **运行时治理** | `data/runtime_governor_decisions.jsonl` | 218 | **实时参数调优的胜出来源** |

**全部是离线文件**,不依赖 PostgreSQL,可在任何机器直接看。

---

## 🔄 数据更新

Agent 数据有新增后,重跑导出脚本即可刷新(幂等,会清空重建):

```bash
cd D:\BaiduNetdiskDownload\001Alpha-Windows迁移包-20260610-2228\001Alpha\Hyper-Alpha-Arena
python tools/export_to_obsidian.py
```

可自定义路径:
```bash
python tools/export_to_obsidian.py --data ./data --vault ./obsidian_vault
```

---

## 📁 Vault 结构

```
obsidian_vault/
├── Agent进化中心.md           # ★ MOC 主页(从这里开始)
├── _canvas/
│   └── Hermes四层进化.canvas   # 四层进化节点图(含决策流节点)
├── _layouts/
│   └── analysis-template.md   # Templater 模板(可选)
├── 01-分析报告/               # 190 个 OpenCode 分析报告
├── 02-交易教训/               # 138 个交易教训(带双链)
├── 03-Hermes进化/             # 719+ 个进化记录
│   ├── L1-智慧-*.md
│   ├── L1-Agent智慧-*.md
│   ├── L1-参数效应模式总览.md
│   ├── L2-Prompt-*.md
│   ├── L2-AB测试-*.md
│   ├── L3-提案-*.md
│   └── L4-创生-*.md
├── 04-Agent决策/              # ★ agent 决策过程流(新增)
│   ├── 仲裁-*.md              #   decision_arbiter(拦截/放行)
│   ├── 治理-*.md              #   governor_decisions(提案裁决)
│   ├── 运行时治理-*.md        #   runtime_governor(实时调参)
│   └── 00-仲裁规则统计.md
└── .obsidian/                 # 配置(首次打开自动补全)
```

---

## 🎯 验收清单(确认 demo 跑通)

- [ ] Obsidian 已打开此 vault
- [ ] 已启用 Dataview 插件
- [ ] 打开 `Agent进化中心.md` → 看到 **12 张表**有真实数据(不是空表)
- [ ] 打开 `_canvas/Hermes四层进化.canvas` → 看到 **6 个节点**(L1-L4 + 决策流 + MOC)和连线
- [ ] 打开任一 `02-交易教训/lesson-*.md` → 点 `[[币种]]` 跳转,关系图谱有节点
- [ ] 打开任一 `04-Agent决策/仲裁-*.md` → 看到完整决策上下文(为什么拦截/放行)
- [ ] 跑 `python tools/export_to_obsidian.py` 能重新生成,数字一致

全部通过 → 你已经看到了 Obsidian 在 agent/知识库可视化上的真实能力。

---

## ⚠️ 边界提醒

这个 vault 验证的是 Obsidian 作为 **离线知识库 / 可视化阅读器** 的能力:
- ✅ 强:复盘、归因浏览、双链关系网、Dataview 聚合、Canvas 流程图
- ❌ 弱:实时交易、WebSocket 行情、下单、Agent 实时流式输出

React 前端(`frontend/`)继续承担实时交易/监控职责,两者分工不冲突。

---

## 🛠️ 故障排查

**Dataview 表显示为空或代码块?**
→ 确认 Dataview 插件已**启用**(不只是安装)。设置 → 第三方插件 → 看开关是开的。

**Dataview 表显示「0 results」?**
→ 设置 → Dataview → 打开 **「Enable JavaScript Queries」** 和 **「Enable Inline JavaScript」**(一般默认就开)。
→ 确认 vault 路径正确,文件夹名带中文没问题(Dataview 支持中文 FROM)。

**Canvas 打不开?**
→ Obsidian 版本 ≥ 1.1(自带 Canvas)。升级 Obsidian。

**中文名乱码?**
→ 确认用 UTF-8 编码。脚本已用 `encoding='utf-8'` 写入,正常不会乱码。
