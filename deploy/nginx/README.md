# Nginx 部署

## 前置
- 域名 `api.yourdomain.com` 解析到服务器 IP
- 服务器装好 Nginx + certbot(Let's Encrypt)

## 步骤

1. **复制配置**:
   ```bash
   sudo cp deploy/nginx/alpha-arena.conf /etc/nginx/sites-available/
   sudo ln -s /etc/nginx/sites-available/alpha-arena.conf /etc/nginx/sites-enabled/
   ```
2. **改 server_name + 证书路径**:把 `api.yourdomain.com` 改成你的真实域名。
3. **申请 TLS 证书**(Let's Encrypt):
   ```bash
   sudo certbot --nginx -d api.yourdomain.com
   ```
   certbot 会自动改 ssl_certificate 路径或生成在 /etc/letsencrypt/live/。
4. **测试配置**:
   ```bash
   sudo nginx -t
   ```
5. **重载**:
   ```bash
   sudo systemctl reload nginx
   ```

## 后端启动(配合此 Nginx)
```bash
# gunicorn 多 worker(阶段5.2)
cd backend
gunicorn -k uvicorn.workers.UvicornWorker -w 8 -b 127.0.0.1:8000 backend.main:app
```
后端只监听 127.0.0.1(不对外),由 Nginx 反代对外 443。

## 验证
```bash
curl https://api.yourdomain.com/api/health   # 应返回 healthy
wscat -c wss://api.yourdomain.com/ws          # WS 应能连(需 token)
```

## 桌面应用连这个后端
Electron 打包时注入 `NEXT_PUBLIC_API_URL=https://api.yourdomain.com`、
`NEXT_PUBLIC_WS_URL=wss://api.yourdomain.com/ws`(阶段5.4)。
