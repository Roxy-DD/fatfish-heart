# 需要你做的最后一步：重启 DSH

更新时间：2026-11-20

## 为什么要重启

itsaplan 的 MCP 服务端已经完全正常：

```
POST https://itsaplan.lan/api/mcp  → HTTP 200
{"authenticated":true,"user":{"name":"大肥鱼","email":"dsh@qq.com"}}
```

但 DSH 里的连接还指向失效的旧地址：
```
当前: http://127.0.0.1:3000/mcp   （旧 Podman 端口，已不存在）
应为: https://itsaplan.lan/api/mcp
```

新地址是自签 HTTPS，Node.js 需要信任 Caddy 根证书。
证书变量已经设好（用户级，永久）：

```
NODE_EXTRA_CA_CERTS = C:\Users\xuee\AppData\Local\CaddyCA\caddy-root.crt
```

**但 DSH 进程是在这个变量设置之前启动的，读不到它。**
所以现在改连接器会报 TLS 错误。

## 操作步骤（两步）

### 第 1 步：完全退出 DSH

不是关窗口——要退出进程。在任务管理器里确认没有残留：

```powershell
Get-Process | Where-Object { $_.ProcessName -match "DeepSeek|dsh" } | Select-Object ProcessName, Id
```

如果还有进程，执行：
```powershell
Get-Process | Where-Object { $_.ProcessName -match "DeepSeek|dsh" } | Stop-Process -Force
```

### 第 2 步：重新打开 DSH

启动后**新进程会带上 NODE_EXTRA_CA_CERTS**，TLS 就能通了。

## 重启后怎么改连接器

在 DSH 里跟我说一句「改 itsaplan 的 MCP 地址」，我来改。
或者你自己在设置里改：

- MCP 连接器 → `itsaplan` → URL
- 改成：`https://itsaplan.lan/api/mcp`
- API key 保持不变（`x-api-key` 头，值不动）

## 验证是否成功

改完后让我执行：
```
mcp__itsaplan__list_projects
```
应该能列出 7 个项目。

## 如果你不想重启 DSH

有个不需要证书的替代方案（当前未启用）：

在 `deploy/itsaplan/docker-compose.override.yml` 的 api 服务下加：
```yaml
    ports:
      - '127.0.0.1:3000:3000'
```
然后 `docker compose up -d api`，MCP 地址保持 `http://127.0.0.1:3000/mcp` 不变。

⚠ 但 NAT 模式下这个映射可能与端口分配冲突（之前遇到过，
导致 api 起不来、登录 502）。**不推荐**，除非 HTTPS 方案走不通。

## 顺带：另外两个 MCP 也断了

| 连接 | 地址 | 状态 |
|---|---|---|
| soloup | `http://localhost:8788/mcp` | 拒绝连接（服务没运行）|
| siyuan-sisyphus | `http://127.0.0.1:6806/mcp` | 拒绝连接（思源没开？）|

这两个是你之前的服务，需要时自己启动即可，跟本次部署无关。
