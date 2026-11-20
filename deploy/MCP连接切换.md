# MCP 连接切换（DSH → 新地址）

更新时间：2026-11-20

## 现状

| 项 | 旧 | 新 |
|---|---|---|
| MCP 地址 | `http://127.0.0.1:3000/mcp`（旧 Podman 端口映射，已失效）| `https://itsaplan.lan/api/mcp` |
| 认证 | x-api-key | 相同（API key 在数据库里，已随备份恢复）|

**MCP 服务端已实测正常**：
```
POST https://itsaplan.lan/api/mcp
  → HTTP 200
  → {"authenticated":true,"user":{"name":"大肥鱼","email":"dsh@qq.com",...}}
```
身份和数据都正确恢复了。

## 为什么现在还不能切换

DSH 是 Node.js 写的，访问自签 HTTPS 需要信任 Caddy 的根证书：
```
未设置 NODE_EXTRA_CA_CERTS:
  ❌ UNABLE_TO_GET_ISSUER_CERT_LOCALLY
设置后:
  ✅ HTTP 401（正常，说明 TLS 通了）
```

**环境变量已设置好**（用户级，永久）：
```
NODE_EXTRA_CA_CERTS = C:\Users\xuee\AppData\Local\CaddyCA\caddy-root.crt
```

但 **DSH 主进程是在设置之前启动的，读不到新变量**，所以现在改连接器仍报 TLS 错误。

## 你需要做的（一次）

**完全退出 DSH 再重新打开**（不是关窗口，要退出进程）：

```powershell
# 确认 DSH 进程已完全退出
Get-Process | Where-Object { $_.ProcessName -match "DeepSeek" } | Select-Object ProcessName, Id
```

然后在 DSH 里让我改连接器，或者你手动改：

### 手动改法

DSH 设置 → MCP 连接器 → 找到 `itsaplan` → 改 URL：

```
旧: http://127.0.0.1:3000/mcp
新: https://itsaplan.lan/api/mcp
```

API key 保持不变（`x-api-key` 头，值不变）。

## 验证是否成功

重启后，在 DSH 里让我执行：

```
mcp__itsaplan__list_projects
```

应该能列出 7 个项目。

或者你自己测：
```powershell
$env:NODE_EXTRA_CA_CERTS = "C:\Users\xuee\AppData\Local\CaddyCA\caddy-root.crt"
node -e "const https=require('https');https.get('https://itsaplan.lan/api/projects',{headers:{'x-api-key':'你的key'}},r=>console.log('HTTP',r.statusCode))"
# 期望 HTTP 200
```

## 备用方案：不用 HTTPS，走 HTTP

如果你不想碰 NODE_EXTRA_CA_CERTS，可以给 api 加一个 HTTP 端口映射，
用 `http://127.0.0.1:3000/mcp` 访问（但 itsaplan 要求 HTTPS 时 cookie 才工作，
所以只适合 MCP 这种用 API key 的场景）。

在 `deploy/itsaplan/docker-compose.override.yml` 的 api 服务下取消注释：
```yaml
services:
  api:
    ports:
      - '3000:3000'   # 取消注释
```
然后 `docker compose up -d api`。

MCP URL 改回 `http://127.0.0.1:3000/mcp` 即可，**不需要证书**。

⚠ 但这会让 api 直接暴露到宿主机 3000 端口（仅本机可访问，因为 Docker Desktop
默认绑定 127.0.0.1）。如果是纯本机使用，这个方案更简单。

## 推荐

**推荐重启 DSH + 用 HTTPS**：
- 与浏览器访问一致（都走 `https://itsaplan.lan`）
- 不用暴露额外端口
- NODE_EXTRA_CA_CERTS 已配好，一次到位

**如果嫌麻烦**：用备用方案的 HTTP 端口映射，立刻可用。
