# 移动端 ↔ 服务端 对接设计 · V2

> **前提纠正（V2）**：App 就是**打包的前端**，没有独立 App 后端。
> 因此本文不设计"后端服务"，只设计两件事：
> **① 打包后的前端怎么找到并连上现有 API**；**② 现有 API 要补什么契约**才能支撑开关与紧急停摆。
>
> V1 稿把打包形态误解成"带独立后端的 App"，已作废。前端界面见 `mobile/design/README.md`；
> 控制台界面见 `control.html`。

---

## 0. 先说一个硬阻断：现在的代码打包后连不上

这是本次核查最重要的发现，比开关更紧急。

```ts
// mobile/app/api/client.ts:1
const API_BASE = '/api'                      // ← 相对路径

// mobile/app/hooks/useWebSocket.ts:54-55
const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
const url = `${proto}//${window.location.host}/ws`   // ← 当前页面所在的 host
```

**相对路径 + 当前 host，等于「前端必须与 API 同源」。**
开发期能跑是因为 Vite 代理把 `/api`、`/ws` 转到了 `127.0.0.1:8000`。

但打包成 APK 后，页面来源会变成：

| 打包方式 | 页面 origin | `/api` 会打到哪 | 结果 |
|---|---|---|---|
| Capacitor（默认） | `https://localhost` | `https://localhost/api` | ❌ 404，打到壳自己 |
| Capacitor（androidScheme=http） | `http://localhost` | 同上 | ❌ |
| 直接 `file://` 加载 | `file://`（Origin 为 `null`） | `file:///api` | ❌ 完全无意义 |
| Tauri | `tauri://localhost` | 同上 | ❌ |

**所以打包之前必须先解决地址问题。** 两条路，见 §1、§2。

---

## 0.5 第二个硬阻断：移动端写操作一定 401

比地址问题更硬，因为它不会报「连不上」，而是**看起来能用、一写就失败**。

### 现状核查（三处事实）

```python
# ① backend/middleware/auth.py:115-119
def _requires_auth(path, method):
    if method in _WRITE_METHODS:        # POST/PUT/DELETE/PATCH
        return True                      # ← 所有写操作都要凭证
    if method == "GET":
        return any(path.startswith(p) for p in _DANGEROUS_GET_PREFIXES)
    return False                         # ← 普通 GET 全开放
```

```ts
// ② mobile/app/api/client.ts —— 全文 22 行，没有任何鉴权
const API_BASE = '/api'
const res = await fetch(url, { headers: { 'Content-Type': 'application/json' } })
//                                            ↑ 没有 Authorization
```

```
③ mobile/app/** 全目录 grep：Authorization / Bearer / token / login → 0 命中
```

### 推论

| 操作 | 现在会怎样 |
|---|---|
| 读权益、持仓、盘口、任务状态（GET） | ✅ 能显示（除少数 `_DANGEROUS_GET_PREFIXES`） |
| 暂停车道、全平、开总闸、启停会话（POST） | ❌ **401**，而且前端没有任何登录入口，无从恢复 |

**也就是说：控制台界面做得再好，现在一个开关都按不动。**
这正是这套系统里最怕的那种失败模式 —— 界面正常、操作静默失败。

### 复用桌面端已有的一套，不要另造

`frontend-next` 里已经有完整且经过修复的实现，移动端照抄即可：

| 位置 | 内容 |
|---|---|
| `src/lib/auth.ts` | 登录、`access_token` / `refresh_token`（`arena_access_token` / `arena_refresh_token`）、401 自动续期一次 |
| `src/lib/api.ts:136` | `headers.Authorization = \`Bearer ${token}\``（**注释明确写了"原为裸 fetch，无 Authorization、无续期、失败静默"** —— 同一个坑移动端正在重演） |
| `src/components/auth/AuthGate.tsx` | 未登录时的门 |

移动端要做的最小改动：

```ts
// mobile/app/api/client.ts
const ACCESS = 'arena_access_token'
export async function apiRequest<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const token = localStorage.getItem(ACCESS)
  const res = await fetch(API_BASE + endpoint, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers as any),
    },
  })
  if (res.status === 401) { /* 续期一次；再失败 → 跳登录 */ }
  ...
}
```

### 但这里有个必须说清的分工

移动端是**看板**，不该让人在手机上敲账号密码。两种做法：

| 方案 | 做法 | 适合 |
|---|---|---|
| **A. 手机登录一次，长期持有**（推荐起步） | 复用 JWT 登录，refresh token 存安全存储；有效期设长（如 30 天） | 单操作者，手机是私人设备 |
| **B. 手机只持一个专用控制令牌** | 后端加一把 `MOBILE_CONTROL_TOKEN`，权限**只够调 `/api/control/*`**，不能读密钥、不能改配置 | 更安全；令牌泄露的爆炸半径小 |

方案 B 更符合这套系统一贯的 fail-closed 风格，但要新增鉴权通道。
**建议先做 A 把流程跑通，再收紧到 B。**

### ⚠️ 另外两处会卡住移动端的鉴权细节

1. **`RISK_COMMAND_TOKEN` 当前为空**（`.env` 实测）。
   `agent_routes.py::_authorize` 在 token 为空时**只允许 127.0.0.1**；
   手机上一定是远程客户端 ⇒ 所有走 `X-Risk-Token` 的写操作会 **403**。
   **只要移动端要碰控制类端点，这把 token 必须配。**

2. **`AUTH_LOCAL_TENANT` 的回环放行对手机无效。**
   `_local_tenant_fallback_ok()` 只在 `client.host ∈ {127.0.0.1, ::1}` 时成立。
   也就是说"本机不用登录"这条便利**在手机上永远拿不到**，必须真凭证。

---

## 1. 推荐：同源托管（改 0 行前端代码）

后端**已经有这个能力**了，只是默认关着、且指向桌面版：

```python
# backend/main.py:2725-2789  —— 已存在
_BACKEND_SERVE_WEB = os.getenv("BACKEND_SERVE_WEB", "").strip().lower() in ("1","true","yes")
if _BACKEND_SERVE_WEB:
    _WEB_OUT_DIR = <repo>/frontend-next/out
    app.mount("/_next", StaticFiles(...))
    # + SPA 回退路由（/xxx → xxx.html，兜底 index.html）
```

设计：**把移动端加到同一个机制上，挂到 `/m/`**。

```
https://<你的服务器>/m/          ← 移动端页面（同源）
https://<你的服务器>/api/*       ← API（同源）
wss://<你的服务器>/ws            ← WebSocket（同源）
```

这样：
- `API_BASE = '/api'` **不用改**；`window.location.host` 天然就是 API host，**也不用改**；
- **完全没有 CORS**（同源请求不触发预检）；
- 地址只有一个：就是你打开页面的那个地址。

### 1.1 需要加的东西

```python
# backend/main.py，在现有 web-serve 段落旁边增加
_MOBILE_OUT_DIR = <repo>/mobile/dist
if os.path.isdir(_MOBILE_OUT_DIR):
    app.mount("/m/assets", StaticFiles(directory=os.path.join(_MOBILE_OUT_DIR, "assets")))

    @app.api_route("/m/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def _serve_mobile(full_path: str):
        # 精确文件 → 目录 index.html → 兜底 SPA index.html
        ...
```

注意三个必须处理的点：

| 点 | 说明 |
|---|---|
| **`/m` 不要重定向到 `/m/`** | 重定向会多一次往返，且部分 WebView 对 308 处理不一致。两个路径都直接返回 index.html |
| **`base` 要设成 `/m/`** | `vite.config.ts` 加 `base: '/m/'`。否则打包出的 `index.html` 里是 `/assets/xxx.js`，会打到站点根目录而不是 `/m/assets/` |
| **鉴权中间件要放行 `/m/`** | `middleware/auth.py` 里已有 `PUBLIC_PATHS`（含 `/static/`）。**页面本身必须免鉴权**，否则连登录页都加载不出来，形成死锁 |

### 1.2 地址配置自然消失

同源方案下，「服务器固定 IP」这件事变成：**你从哪个地址打开页面，就是哪个 API 地址**。
租到 IP 后填进地址栏即可，APK 不需要重打——前提是用 §1.3 的方式安装。

### 1.3 与 PWA 的关系

`mobile/public/manifest.json` 已经是合法 manifest（`display: standalone`）。
配合上述同源托管，`https://<服务器>/m/` 可以直接「添加到主屏幕」，得到一个**跟原生 App 几乎无差别**的入口，
且**没有 §1 表格里的任何 origin 问题**。

这条路能走通的话，我建议**先用 PWA，别急着做 APK**——省掉打包、签名、分发、CORS 四件事。

---

## 2. 次选：跨域打包（Capacitor / Tauri）

如果确实要 APK（离线壳、需要原生能力如后台保活、或不想暴露网页），则必须做三件事：

### 2.1 前端改两个文件

```ts
// mobile/app/api/client.ts —— 地址改为可配置
export const API_BASE = (localStorage.getItem('server_url') || '').replace(/\/$/, '') + '/api'
```

```ts
// mobile/app/hooks/useWebSocket.ts —— 不再用 location.host
const base = localStorage.getItem('server_url') || window.location.origin
const url = base.replace(/^http/, 'ws') + '/ws'
```

并要求：**地址必须在首帧之前已存在**，否则第一次请求会打到壳自己。
所以要么构建时注入默认值（`VITE_SERVER_URL`），要么做一个「先填地址」的首启页。

### 2.2 后端 CORS 必须放行壳的 origin（这是最容易卡住的一步）

现状：

```python
# backend/main.py:358-379
_ALLOWED_ORIGINS_ENV = os.getenv("FRONTEND_ORIGIN", "").strip()
if _ALLOWED_ORIGINS_ENV:
    _allowed_origins = [...]
elif _ENVIRONMENT == "production":
    _allowed_origins = []          # ← 生产环境默认只允许同源
else:
    _allowed_origins = ["*"]
```

壳的 origin **不是**普通网址，常见三种，且都不在默认白名单里：

| 打包方式 | 请求带的 Origin | 能否用 `allow_origins` 匹配 |
|---|---|---|
| Capacitor 默认 | `https://localhost` 或 `capacitor://localhost` | 可以（字符串精确匹配） |
| Capacitor（androidScheme=http） | `http://localhost` | 可以 |
| 直接 `file://` | 字面量 `null` | **不能靠 origin 字符串**，需 `allow_origin_regex` 或改加载方式 |

所以生产环境要设：

```bash
# 同源 PWA：不需要动
# Capacitor：按实际壳填
FRONTEND_ORIGIN=https://localhost,capacitor://localhost,http://localhost
```

若确实要用 `file://`，只能加正则（且这是我不推荐的做法）：

```python
app.add_middleware(CORSMiddleware, allow_origins=_allowed_origins,
                   allow_origin_regex=r"^(null|https?://localhost|capacitor://localhost|tauri://localhost)$",
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
```

⚠️ **两点必须知道**：
1. `allow_credentials=True` 与 `allow_origins=["*"]` 在规范上互斥，Starlette 会回显具体 origin 来规避。
   但在打包场景下**不要依赖这个**，显式列出更好排查。
2. **CORS 只管 HTTP，不管 WebSocket。** WS 的握手同样带 `Origin`，但校验由服务端自己决定。
   如果 `ws.py` 里有 origin 检查，打包后要一并放行壳的 origin，否则「API 通、实时流不通」这种半通状态最难查。

### 2.3 其他要一起处理的

| 项 | 说明 |
|---|---|
| `ws://` 明文 | Android 9+ 默认禁止明文 HTTP。必须 `https/wss`，或在壳里显式开 `usesCleartextTraffic`（不推荐） |
| 证书 | 自签证书在 WebView 里会直接失败；Capacitor 需显式配置信任策略 |
| token 存储 | 壳里存 token 要用系统安全存储（Keystore/Keychain）。**注意：若存 localStorage，同源托管下任何 XSS 都能读走控制令牌** |
| 混合内容 | 若壳是 `https://localhost` 而目标是 `http://`，浏览器会拦成 mixed content |

**结论**：跨域打包要处理的隐性成本远高于同源托管。除非有明确理由，走 §1。

---

## 3. 地址没有时的表现（无论哪条路都要做）

三种状态必须区分，**不能都显示"连接失败"**：

| 状态 | 触发条件 | 界面表现 |
|---|---|---|
| **未配置** | 壳模式下还没填地址 | 灰点 · 「未配置服务器地址」+ 去设置按钮 |
| **连接失败** | 有地址但请求失败 | 红点 · 「连接失败 · 重试中」+ 最后成功时间 |
| **部分可用** | API 通但 WS 不通 | 琥珀点 · 「实时流未连 · 数据为轮询」 |

**数据区在未配置时必须显示空骨架，不能显示 `0 / $0.00`。**
显示 0 会被读成"账户归零"，这比显示空白危险得多。

---

## 4. 服务端要补的契约（支撑开关与紧急停摆）

后端**能力基本都有**，只是散落各处、没有总闸。要补的不是大工程，是四处：

### 4.1 一张状态表 + 一张审计表

```sql
CREATE TABLE system_control_state (
  id SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),   -- 强制单行
  kill_switch     BOOLEAN NOT NULL DEFAULT FALSE,     -- L0 总闸（人工与看门狗共用）
  kill_reason     TEXT,                               -- 谁触发的、为什么
  hft_enabled     BOOLEAN NOT NULL DEFAULT TRUE,      -- L1
  trade_enabled   BOOLEAN NOT NULL DEFAULT TRUE,
  agent_enabled   BOOLEAN NOT NULL DEFAULT TRUE,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by TEXT                                     -- mobile / web / cli / breaker:data_stale
);

CREATE TABLE control_audit (            -- 只增不改
  id BIGSERIAL PRIMARY KEY, ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  actor TEXT NOT NULL,                  -- mobile / web / cli / breaker:<name>
  action TEXT NOT NULL, scope TEXT NOT NULL,
  before_val JSONB, after_val JSONB, req_id TEXT,
  ok BOOLEAN NOT NULL, error TEXT
);
```

> V1 稿这里还有 `deadman_enabled` / `deadman_timeout_sec` / `deadman_last_seen` 三列 —— **已删除**。
> 理由见 §4.4：停摆不该依赖手机的存活状态，后端已有看门狗提供检测信号。

### 4.2 一个统一求值入口（这是关键，不是那些表）

```python
def can_trade(lane_id: str) -> Decision:
    """所有执行路径的唯一判定入口。禁止各模块自己拼 if。"""
    if not system_state.kill_switch:      return Decision(False, "全局总闸关闭", "L0")
    if not module_enabled(...):           return Decision(False, "模块闸关闭",   "L1")
    if lane.status != "active":           return Decision(False, f"车道 {lane.status}", "L2")
    if lane.mode == "disabled":           return Decision(False, "mode=disabled", "L2")
    if account_of(lane).disable_trading:  return Decision(False, "账户已停交易", "L2")
    if state.breaker_active(lane_id):     return Decision(False, "风控熔断激活", "BREAKER")
    if stop_triggers.any_active():        return Decision(False, stop_triggers.first_reason(), "STOP_TRIGGER")
    return Decision(True, "放行")
```

**总闸是"求值时的与条件"，不是对各车道写入。**
所以打开总闸时，各车道回到各自原本状态，而不是"全被改成 stopped"。
这条决定了恢复是一次操作而不是 N 次。

### 4.3 一个新命名空间（转发，不重写）

现有能力直接复用，`/api/control/*` 只做归一化与汇总：

| 能力 | 现有端点（**不要重写**） |
|---|---|
| 车道启停 | `POST /api/hft/control/status`、`/api/trading/lanes/{id}/status` |
| 车道模式 | `POST /api/hft/control/mode` |
| 会话启停 | `POST /api/full-auto/{pause\|resume\|stop}/{sid}` |
| 策略启停 | `POST /api/ai-strategy/{id}/{pause\|resume}` |
| 账户停交易 | `POST /api/accounts/{id}/disable-trading` |
| Agent 任务 | `POST /api/ops/jobs/{name}/{enable\|disable}` |
| **紧急全平** | `POST /api/rebate/.../emergency-close-all`（**已经有了**） |

新增（只加不替换）：

```
GET  /api/control/state          五级开关的当前有效值 + 每级来源
POST /api/control/kill-switch    {on, reason, close_positions}   ← 总闸（人工）
POST /api/control/module         {module, enabled}
GET  /api/control/why-stopped    逐对象给出"为什么没在交易"的判定链
GET  /api/control/audit          变更历史
POST /api/control/close-all      {scope, reason}  幂等 + 单飞锁
GET  /api/control/breakers       当前各停摆触发器的值与阈值（§4.4）
```

`why-stopped` 返回**整条判定链**而不只是结论。这套系统里"改了没生效"反复出现过，
一次给全链比事后反推省事得多；前端也能直接渲染，不自己判断"是否在跑"。

### 4.4 停摆触发器：接进已有的看门狗，不新建检测

**先纠正 V1 稿的一个错误方向**：V1 提了「手机每 60s 发心跳，超时自动停」。
这是错的 —— **看门狗是服务端的事，跟手机没关系**，而且后端早就有看门狗了，那个设计是重复造轮子。

#### 已有的看门狗（实测，全部在服务端）

| 看门狗 | 位置 | 周期 | 检测什么 | **检测到之后做什么** |
|---|---|---|---|---|
| `backend-watchdog.ps1` | 部署脚本 | — | `/api/health` 探针 | **重启后端进程** |
| `job_watchdog` | `ops/job_registry.py:448` | 300s | 定时任务滞后(warn)/中断(critical) | **只发钉钉告警** |
| `_start_hang_watchdog` | `full_auto_trading_service.py:4022` | 每次 tick | 主循环 hang 超时 | **只释放循环状态与进程锁** |
| market data hub stale watchdog | `market_data_hub.py:493` | 30s | 行情源陈旧 | **回落 REST** |
| `user_stream_guard` | 独立守护线程 | 15s | worker 端口存活 | **拉起 worker** |
| `evo-quick-watchdog` | `evo_runtime.py:113` | — | 进化循环 | — |

**结论：检测层是够的，缺的是「停」这个动作。**

注意最后一列 —— 现有看门狗全部是**「检测 → 自愈」或「检测 → 告警」**：

```python
# job_registry.py:462 —— 发告警，然后就没有然后了
send_alert("P1" if st == "critical" else "P2", f"定时任务{'中断' if st == 'critical' else '滞后'}: {j['name']}", ...)

# full_auto_trading_service.py:4027 —— 释放锁让下一轮 tick 能进，交易继续
self._force_release_unified_loop_state(session_id, reason=f"循环超时({hang_timeout:.0f}s)，线程 detached")
```

所以「任务中断 → 发条钉钉 → 系统继续按老参数交易」是当前的真实行为。
**这才是缺口，而不是缺一个检测器。**

#### 改法：给已有信号接上执行器

```python
def evaluate_breakers() -> List[Breaker]:
    """由已有的看门狗数据源驱动，不新增采集。"""
    out = []
    # ① 决策循环 hang（hang watchdog 已经在报，只是没人接）
    if fullauto.last_hang_age_sec() > HANG_STOP_SEC:
        out.append(Breaker("fullauto_hang", "critical"))
    # ② 关键任务中断（job_watchdog 已经在算 stale）
    crit = [j["name"] for j in list_jobs()
            if j.get("stale") == "critical" and j["name"] in _DECISION_CRITICAL]
    if crit:
        out.append(Breaker("job_stalled", "critical", detail=crit))
    # ③ 行情/深度陈旧（hub watchdog 已经在测 age）
    if market_data.age_sec() > DATA_STALE_STOP_SEC:
        out.append(Breaker("data_stale", "critical"))
    return out
```

然后**接进同一个总闸** —— 复用 §4.2 的 `can_trade()`，不搞第二套停摆机制：

```
已有看门狗（检测）──▶ evaluate_breakers()（分级）──▶ kill_switch = TRUE（L0，同一个总闸）
                                                  └─▶ control_audit(actor='breaker:<name>')
```

于是 `/api/control/why-stopped` 的判定链会自然多出一项：
`{"level":"BREAKER","name":"行情陈旧","pass":false,"value":"age 312s > 180s"}` ——
**手机上一眼就能看到"是它自己停的，原因是数据陈旧"**，不需要任何手机侧参与。

#### 分级与阈值：停止的阈值必须比告警的严

这一条是对上面改法的风险约束。现在系统对「陈旧」是**自愈**（回退 REST、重启进程），
如果一陈旧就停交易，会**过度停摆**（数据源抖动一下就把策略停了，比不停更糟）。

所以：

| 级别 | 阈值 | 动作 |
|---|---|---|
| 告警 | 现有阈值 | 钉钉/事件流（**保持不变**） |
| 严重告警 | 2–3× 告警阈值 | 事件流标红 + 计入 `why-stopped` |
| **触发停止** | **持续超阈值 N 分钟且自愈失败** | 打开 L0 总闸 |

关键在「**自愈失败**」四个字：market data hub 回落 REST 成功了，就不该停。
只有回退也拿不到数据（REST 也陈旧）才停。

阈值建议放在配置里并给中文说明，与现有 `LaneRiskLimits` 那种「数值有、含义无」的做法拉开差距：

```python
@dataclass
class StopTriggers:
    data_stale_stop_sec: int = 300      # 行情陈旧且回落失败，超此时长即停
    fullauto_hang_stop_sec: int = 900   # 决策循环 hang 超此时长即停（比 hang_timeout 宽）
    job_stalled_stop_sec: int = 1800    # 关键任务中断超此时长即停
    auto_resume: bool = False           # 触发停止后是否自动恢复（默认否，见下）
```

#### 恢复：必须手动

与 §5 规则一致 —— **触发停止后不自动恢复**。
理由：如果是数据源抖动导致停摆，抖动过去后自动恢复等于"无感重启"，
人会在不知情下重新暴露在风险里。恢复必须由人在控制台点一次「确认恢复运行」。

#### 那手机到底负责什么

只有两件事，都不涉及"保活"：

1. **手动总闸**：人在控制台按下「紧急停摆」（§4.3 的 `kill-switch` 端点）；
2. **看状态**：看 `/api/control/why-stopped`，知道"是它自己停的还是一直在跑"。

手机**不参与任何自动停摆判定**。这也顺带解决了一个隐患：
不需要担心"心跳机制本身"成为新的故障点。

#### 部署位置

因为不再有手机心跳，就不需要 §5 里那个"独立小进程"的复杂方案了。
**触发器直接挂在已有的调度器上**（`job_watchdog` 已经是 300s 一跑，顺带算一次 `evaluate_breakers()` 即可），
零新增进程、零新增部署单元。

---

## 5. 三条不可妥协的规则

1. **默认只停新开仓，不平仓。** 平仓不可逆，必须让人主动选。
2. **触发停止后不自动恢复。** 人要明确确认（对自动触发与手动触发都适用）。
3. **紧急操作必须幂等。** 人会慌、会连点；对"全平"来说重复执行本身就是事故
   （第二次会打在已平的仓位上产生反向仓）。控制类 POST 一律带
   `X-Idempotency-Key` + 服务端单飞锁。

> V1 稿曾把规则写成「心跳恢复后不自动开跑」。心跳机制已废，规则本身不变，只是适用对象改为
> 「看门狗触发停止后」。**方向和结论都没变，错的是我给它配的实现手段。**

---

## 6. 实施顺序

| 阶段 | 内容 | 验收（注意措辞） |
|---|---|---|
| **P-1** | **鉴权接入**（§0.5）：`client.ts` 带 `Authorization`；配 `RISK_COMMAND_TOKEN` | 手机上暂停一次车道**真的暂停**，不是 401 |
| **P0** | `/m/` 同源托管 + `vite base:'/m/'` + 鉴权放行 | 手机浏览器打开 `http://<ip>:8000/m/` 能连上 API 与 WS |
| **P1** | `system_control_state` + `control_audit` + `can_trade()`，先切高频车道 | 改库里 `kill_switch=TRUE`，**下一个 tick 真的没报价** |
| **P2** | `/api/control/*`（state / kill-switch / why-stopped / audit） | 手机能停、能看原因、能看到审计 |
| **P3** | `evaluate_breakers()` 接进 `job_watchdog`（§4.4，**零新增进程**） | 手动把行情源掐 5 分钟，总闸**自己**打开，审计出现 `actor=breaker:data_stale` |
| **P4** | 中长线、Agent 任务也切到 `can_trade()` | 总闸一次停全部 |
| **P5** | 未配置/连接失败/部分可用三态 UI | 未配置时数据区是空骨架，不是 0 |

**P-1 和 P0 都可以立刻做**，且都不依赖开关设计 —— 它们只解决"打包后连不上 / 写不动"这两个硬阻断。

⚠️ **P1 的验收标准是"下一个 tick 真的没报价"，不是"接口返回成功"。**
这套系统里"改了没生效"出现过多次，开关的验收必须落在执行侧的可观测事实上。

### 6.1 顺手要补的两个小配置

| 项 | 现状 | 要做 |
|---|---|---|
| `vite.config.ts` 的 `base` | **未设** | 同源托管到 `/m/` 时必须 `base: '/m/'`，否则产物里是 `/assets/…`，会打到站点根目录 |
| `vite-plugin-pwa` | **未装**（`DESIGN_PROMPT.md` 写了要，`package.json` 里没有） | 走 PWA 路线时装上；否则 manifest.json 是摆设，装不了桌面图标 |

---

## 7. 待确认

1. **打包形态最终选哪个？** 我建议**先 PWA（同源托管）**，能省掉打包、签名、分发、CORS 四件事；
   确有需要再上 Capacitor。
2. **停摆触发器的阈值**（§4.4）：`data_stale_stop_sec=300` / `fullauto_hang_stop_sec=900` /
   `job_stalled_stop_sec=1800` 这三个值需要你按实际容忍度定。
   我按「比现有告警阈值严 2–3 倍，且要求自愈失败」来给的。
3. **哪些任务算「关键任务」**（`_DECISION_CRITICAL`）？中断了就停，需要你圈定名单。
   我倾向只含直接参与决策与下单的任务，学习/因子那类不该停交易。
4. **断连时是否平仓？** 本稿**默认不平**，需额外显式开启。
5. **总闸粒度**：一个全停够用，还是要"停交易但留 Agent 学习"？本稿按三模块闸设计，可组合。
6. **PWA 与 APK 并存**是否可接受？并存时两者共用同一套 `/api`，无需额外工作。

---

## 附：V1 稿的两处方向性错误（留档）

| 错误 | 为什么错 | 已改为 |
|---|---|---|
| 把 App 当成"带独立后端" | App 就是打包的前端，直接打现有 API | §0 起重写为「打包前端 ↔ API 契约」 |
| 提议「手机心跳 + 超时自动停」 | ① 看门狗是服务端的事，与手机无关；② 后端**已经有** 6 个看门狗，属重复造轮子 | §4.4：复用已有看门狗信号，只补"停"这个动作 |

留档的原因：这两条都是「看起来合理、实则与现有架构冲突」的提案。
本仓库里同类教训不少（`参数生效机制_为何改了没生效会反复发生`），记下来比删掉有用。
