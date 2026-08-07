# AlphaArena 部署清单 (DEPLOY.md)

> **合并/部署前必读。** 本文件汇总所有必须设置的 env / 必须替换的占位符 / 关键约束。
> 本分支含两大改造:交易执行层统一闸口 + 前后端分离/多租户/认证/部署。

---

## 🚨 硬约束(违反会导致故障)

### 1. DB 角色:RLS 与超用户

- **现状**:开发期 DB 连接用 `laobao`(PostgreSQL **superuser**)。
- **关键**:PostgreSQL superuser **无条件绕过 RLS**(FORCE 也无法覆盖)。
- **生产部署时**:若把 DB 角色换成非超用户(最小权限,安全最佳实践),RLS 才真正生效过滤读。
  - ✅ 本分支已处理:后台交易循环(scalp/coordinator/midlong + 6 个 APScheduler tick + 衍生线程)设了 `system_identity`(`is_admin=True`),非超用户下也能正常读写,不会 fail-closed 破坏交易。
  - ⚠️ **运维脚本/直连 DB 改数据**:若用非超用户角色直连改 tenant 表数据,会被 RLS `WITH CHECK` 拦截。运维操作需 `SET app.is_admin='on'` 或用超用户/`BYPASSRLS` 角色连。
  - ⚠️ **未来 Alembic 迁移**:对 FORCE 了 RLS 的表做 `UPDATE/DELETE/ALTER`,若连接角色是非超用户 owner,可能被过滤。迁移脚本里加 `op.execute("SET LOCAL app.is_admin='on'")` 或用超用户连跑迁移。详见 `backend/alembic/versions/0005_rls_policies.py` docstring。

### 2. 生产环境启动守卫

`ENVIRONMENT=production` 时,后端**拒绝启动**除非:
- `BACKEND_API_KEY` 已设(写操作运维通道)
- `JWT_SECRET` 已设且为强随机(≥16 字符,非默认值 `dev-only-change-me-in-prod`)

不满足则 `sys.exit(1)`。**部署前务必在 env 配好这两个。**

---

## 🔧 必须配置的环境变量

### 后端 (`arena-api`) — 线上

| 变量 | 必填 | 说明 |
|------|:---:|------|
| `JWT_SECRET` | ✅ | JWT 签名密钥,强随机 ≥16 字符(生产守卫) |
| `JWT_ALGORITHM` | | 默认 HS256 |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | | 默认 15 |
| `REFRESH_TOKEN_EXPIRE_DAYS` | | 默认 7 |
| `BACKEND_API_KEY` | ✅ | 内部/运维通道密钥(生产守卫) |
| `ENVIRONMENT` | ✅ | `production` |
| `DATABASE_URL` | ✅ | `postgresql+psycopg://.../alpha_arena` |
| `MARKET_DATABASE_URL` | ✅ | `postgresql+psycopg://.../alpha_market` |
| `ANALYTICS_DATABASE_URL` | ✅ | `postgresql+psycopg://.../alpha_analytics` |
| `REDIS_URL` | ✅(多worker) | `redis://host:6379/0`,WS 跨 worker 广播 + 配额。单 worker 可不设(退化为本地) |
| `FRONTEND_ORIGINS` | | CORS 白名单:`file://,app://,tauri://,http://localhost:5273`(逗号分隔) |
| `WEB_CONCURRENCY` | | gunicorn worker 数,默认 8(=CPU 核) |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | | 多 worker 下调小(8w×16≈128,防超 PG max_connections);或上 PgBouncer |
| `ADMIN_INIT_PASSWORD` | ⚠️ | default admin 初始密码(bcrypt hash 或明文,首次设)。**不设则 admin 无法登录**。用后即焚 |
| `ADMIN_REFRESH_TOKEN_EXPIRE_DAYS` | | admin refresh 有效期,默认 1(短于普通用户) |
| `HYPERLIQUID_ENCRYPTION_KEY` | ✅ | 交易所私钥解密用(已有,保留) |

### 后端 — 本地开发(独立 `.env`,绝不混入线上)

```
DATABASE_URL=sqlite:///./data/dev.db   # 本地测试库
ENVIRONMENT=development
```

### 前端 (`arena-web` / Electron) — 打包时注入

```
NEXT_PUBLIC_API_URL=https://api.yourdomain.com
NEXT_PUBLIC_WS_URL=wss://api.yourdomain.com/ws
NEXT_PUBLIC_VERSION=<版本号,从 package.json>
```

---

## 🔁 必须替换的占位符

部署前全局搜索替换 `api.yourdomain.com` → 你的真实域名:

| 位置 | 用途 |
|------|------|
| `deploy/nginx/alpha-arena.conf` | Nginx server_name + TLS 证书路径 |
| `deploy/nginx/README.md` | 文档示例 |
| `frontend-next/package.json` (`electron:build:prod` 脚本) | 打包注入的生产 API URL |
| `frontend-next/electron-builder.yml` (`publish.url`) | electron-updater 自动更新源 |

**不替换就部署**:`npm run electron:build:prod` 会产出指向不可解析域名的坏安装包;Nginx 配置无效。

---

## 📋 部署步骤(线上服务器)

1. **DB 准备**:3 个 Postgres 库(alpha_arena / alpha_market / alpha_analytics)。跑迁移:
   ```bash
   cd backend && alembic upgrade head   # 应用 0001-0007
   ```
   验证:`alembic current` 三个库都到 `0007 (head)`。

2. **admin 初始密码**:设 `ADMIN_INIT_PASSWORD` env(首次启动自动写入 default 用户的 bcrypt hash)。

3. **后端启动**:
   ```bash
   cd backend
   gunicorn -k uvicorn.workers.UvicornWorker -w 8 -b 127.0.0.1:8000 backend.main:app
   # 或用 deploy/gunicorn.conf.py: gunicorn -c deploy/gunicorn.conf.py backend.main:app
   ```
   后端只监听 127.0.0.1(不对外),由 Nginx 反代。

4. **Nginx**(见 `deploy/nginx/README.md`):
   ```bash
   sudo cp deploy/nginx/alpha-arena.conf /etc/nginx/sites-available/
   sudo ln -s /etc/nginx/sites-available/alpha-arena.conf /etc/nginx/sites-enabled/
   # 改 server_name + 证书路径
   sudo certbot --nginx -d api.yourdomain.com   # 申请 TLS
   sudo nginx -t && sudo systemctl reload nginx
   ```

5. **Redis**(多 worker 必备):起 Redis,后端 `REDIS_URL` 指向它。

6. **前端打包**(开发机/CI,非服务器):
   ```bash
   cd frontend-next
   # 改 package.json electron:build:prod 的域名后:
   npm run electron:build:prod
   # 产出 frontend-next/dist-electron/AlphaArena Setup x.x.x.exe
   ```
   分发给用户安装。

7. **验证**:
   ```bash
   curl https://api.yourdomain.com/api/health   # healthy
   # admin 首次登录(用 ADMIN_INIT_PASSWORD 设的密码)
   # 桌面应用装到干净机,连线上后端,登录,交易
   ```

---

## ⚠️ 已知遗留(非阻塞,按需处理)

- **`llm_usage_logs` 跨库 tenant_id 回填**:analytics 库独立,无法 join core 的 accounts,生产若 3 库分离则该表 tenant_id 留 NULL(全局可见)。若要按租户隔离 analytics,需 ETL 把 account_id→user_id 拉到 analytics 库。详见 `0004_add_tenant_isolation.py` docstring。
- **services 层 ~50 处阻塞 I/O**(ccxt/requests):多在缓存/后台路径,需逐个评估再包 `asyncio.to_thread`(盲目包可能双线程池嵌套)。路由层已由 FastAPI threadpool 自动 offload(已验证)。
- **`on_startup` 残留幂等 ALTER**(`signal_trade_feedback` 等):已 gate,不破坏,可后续统一收口进 Alembic。
- **6 个 orphan 表**(DB 有、ORM 模型无):`ai_analysis_logs`、`raw_market_events`、`mlto_debate_log`、`mlto_memory_events`、`mlto_signal_weights`、`mlto_thesis`、`mlto_thesis_events`(已废弃 MLTO 功能的日志/事件表)。这些表已在 DB 中,Alembic 不管它们(`create_all` 只建已注册模型)。有少量代码用原表名引用它们(保留策略/索引优化),所以**不能直接删表**(会运行时报错)。非阻塞:不影响 RLS/认证/部署,只是 schema 漂移。彻底处理需:补 ORM 类让 Alembic 接管,或连同引用代码一起清(属业务层改造)。注:早期调查的"10 orphan"含误判,实际只有这 6 个无 ORM 类(ai_decision_logs/decision_snapshots/risk_control_events 有模型,非 orphan)。

---

## 🧪 测试

部署后跑端到端验证:
```bash
# 后端测试(全量,忽略预存损坏文件)
cd backend && python -m pytest tests/ --ignore=tests/unit/test_phase5_adaptive_evolution.py -q
```
关键测试集(都应过):
- `test_rls_isolation.py` / `test_rls_after_commit.py` — 多租户隔离 + 致命陷阱守卫
- `test_auth.py` / `test_auth_middleware.py` — JWT 认证
- `test_admin_routes.py` / `test_vip_permissions.py` — admin + 权限
- `test_background_loops_rls.py` — 后台循环在 RLS 下正常(C1 修复)
- `test_ws_redis_bridge.py` — 多 worker WS 广播
- `test_trade_gate.py` / `test_leverage_authority.py` — 交易闸口

**注意**:RLS 隔离测试用 `NOSUPERUSER NOBYPASSRLS` 角色(`rls_test_*`)验证真隔离。若 DB 连接是超用户(laobao),RLS 被绕过,测试仍过但是"假通过"——生产换非超用户角色才真正生效。
