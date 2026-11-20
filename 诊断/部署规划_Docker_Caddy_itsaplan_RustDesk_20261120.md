# 自托管环境规划：Docker Desktop + Caddy + itsaplan + RustDesk

规划日期：2026-11-20
状态：**规划阶段，未安装任何软件、未删除任何数据**
原则：先规划清楚 → 你确认 → 再下载安装（你自己下也可以）

---

## 第一部分：Docker Desktop 机制（回答你的核心问题）

### Q1：在 WSL 里是直接用 Docker 建虚拟机，还是要装发行版？

**答案：不需要你手动装任何发行版。Docker Desktop 自己会创建并管理它需要的 WSL2 发行版。**

事实（Docker 官方文档与本机实测）：

| 项 | 情况 |
|---|---|
| Docker Desktop 安装后 | 自动创建两个 WSL2 发行版：`docker-desktop` 和 `docker-desktop-data`（新版合并为一个 VHDX）|
| 你需要做的 | 只是安装 Docker Desktop，勾选 "Use WSL 2 instead of Hyper-V" |
| Ubuntu/Alpine 要不要装 | **不用**。容器镜像本身就带系统，不需要宿主机再有发行版 |
| 磁盘占用 | Docker Desktop 用一个 VHDX 文件存所有镜像/容器，默认在 `C:\Users\<用户>\AppData\Local\Docker\wsl\` |
| `wsl -l -v` 会看到 | 安装后会出现 `docker-desktop`（这是 Docker 自己建的，不是你要装的）|

**所以"越省空间、越好用、性能越好"= 不装额外发行版，让 Docker Desktop 用默认 WSL2 后端。** 这就是默认状态，无需优化。

### Q2：本机当前状态（实测）

| 项 | 状态 |
|---|---|
| Docker Desktop | ❌ **未安装**（开始菜单只有空文件夹，注册表/Program Files 无）|
| WSL2 发行版 | ❌ 空（之前已清空）|
| docker CLI | ❌ 无 |
| 结论 | 完全干净，可以从零按规划搭 |

### Q3：为什么选 Docker Desktop（回顾）

- 网络开箱即用：`-p 3000:3000` 直接 localhost 可达，**不用 netsh portproxy**（你之前在 Podman 上反复踩的坑）
- 个人使用免费
- Caddy、itsaplan、RustDesk 的官方文档都以 docker compose 为主

---

## 第二部分：整体架构（你描述的结构）

```
┌─────────────────────────────────────────────────────────┐
│  外部访问 / 局域网访问                                    │
│         https://app.xxx.com  /  https://rustdesk.xxx.com │
└──────────────────────┬──────────────────────────────────┘
                       │ 80/443
┌──────────────────────▼──────────────────────────────────┐
│  Caddy 容器（基础设施层，单独 docker-compose）             │
│  · 对外反代 + 自动 HTTPS                                  │
│  · 唯一的 80/443 入口                                     │
│  · 接管所有域名/路径的流量分发                             │
└──────┬─────────────────────────┬────────────────────────┘
       │ shared network          │
┌──────▼──────────┐    ┌─────────▼──────────┐
│ itsaplan 项目    │    │ RustDesk 项目       │
│ docker-compose  │    │ docker-compose     │
│ (自己的网络)     │    │ (自己的网络)        │
│ · postgres      │    │ · hbbs             │
│ · rustfs        │    │ · hbbr             │
│ · api/worker/   │    │                    │
│   bot/web       │    │                    │
└─────────────────┘    └────────────────────┘
```

**关键机制**：Caddy 和各项目不在同一个 docker-compose 里，靠 **external shared network**（外部共享网络）互通。Caddy 能通过容器名访问各项目的容器。

### 2.1 跨项目互通：external network（已验证）

Docker 每个 compose 项目默认建自己的 bridge 网络、彼此隔离。要让 Caddy 访问别的项目，建一个外部网络让大家都挂上去：

```bash
docker network create proxy    # 一次性
```

Caddy 的 compose（基础设施层）：
```yaml
services:
  caddy:
    image: caddy:2-alpine
    container_name: caddy
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
      - "443:443/udp"          # HTTP/3，可选
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data        # 证书/本地CA，必须持久化
      - caddy_config:/config
    networks: [proxy]
networks:
  proxy:
    external: true
volumes:
  caddy_data:
  caddy_config:
```

业务项目（itsaplan）的 compose 里，把要暴露的服务挂到 proxy：
```yaml
services:
  web:
    container_name: itsaplan-web   # 跨项目唯一名字，避免 DNS 冲突
    networks:
      - proxy      # 给 Caddy 访问
      - default    # 保留内部网络给 postgres/worker 等
    # 不再需要 ports 映射到宿主机
networks:
  proxy:
    external: true
```

**三个坑**（已验证）：
1. `external: true` 只是"网络由 compose 外管理"，跟"是否对外可达"无关
2. Caddyfile 里 `reverse_proxy` 必须写**容器内服务名 + 容器内部端口**（如 `itsaplan-web:3001`），不是宿主机映射端口
3. 多项目都有 `web` 服务会因 DNS 同名冲突 → 用唯一 `container_name` 或 network alias

### 2.2 Caddyfile 反代示例

```
{
    email admin@example.com
}

itsaplan.example.com {
    encode gzip
    reverse_proxy itsaplan-web:3001 {
        header_up X-Real-IP {remote_host}
    }
}
```

### 2.3 HTTPS：两种场景（已验证）

| 场景 | 做法 | 客户端体验 |
|---|---|---|
| **有公网域名** | site block 写域名，Caddy 自动向 Let's Encrypt 申请/续期证书 | 绿锁，零配置 |
| **纯局域网无域名** | `tls internal` 用 Caddy 自带本地 CA 签证书 | ⚠ 每台设备需手动导入 root.crt，否则安全警告 |

纯内网写法：
```
192.168.x.x {
    tls internal
    reverse_proxy itsaplan-web:3001
}
```
本地 CA 根证书在容器的 `/data/caddy/pki/authorities/local/root.crt`，**容器内无法自动装到 Windows 信任库，必须手动导出并导入每台客户端**（Caddy 官方明确说明这是管理员责任）。

**最佳体验**：有你能管理的真实域名 + 内网 DNS 指向 LAN IP + DNS challenge 签真证书 → 内网也绿锁、不用每台装证书。

---

## 第四部分：RustDesk 部署方案（调研已补全）

### 4.1 官方 compose（hbbs + hbbr）

```yaml
services:
  hbbs:
    container_name: hbbs
    ports:
      - 21115:21115        # NAT 类型测试
      - 21116:21116        # TCP 打洞
      - 21116:21116/udp    # UDP 心跳/注册（必须 TCP+UDP 都开）
      - 21118:21118        # WebSocket (Web 客户端)
    image: rustdesk/rustdesk-server:latest
    command: hbbs -r <你的域名>:21117
    volumes: [./data:/root]
    restart: unless-stopped
  hbbr:
    container_name: hbbr
    ports:
      - 21117:21117        # 中继 relay
      - 21119:21119        # WebSocket relay
    image: rustdesk/rustdesk-server:latest
    command: hbbr
    volumes: [./data:/root]
    restart: unless-stopped
```
官方仓库：https://github.com/rustdesk/rustdesk-server
⚠ Windows + Docker Desktop **不支持 `network_mode: host`**（官方明确只 Linux 可用），只能用 `-p` 端口映射。

### 4.2 ★ 关键结论：Caddy 普通反代不能代理 RustDesk 裸协议

RustDesk 用的是**私有 TCP/UDP 长连接**（21115/21116/21117），**不是 HTTP**。Caddy 的 `reverse_proxy` 是 HTTP 层的，代理不了。别指望把 21116 配进 Caddyfile。

三条路线：

| 路线 | 做法 | 评价 |
|---|---|---|
| **1. 直连暴露（OSS 最省事，推荐先用）** | 21115-21117 由路由器端口转发到 Windows 主机，Caddy 只管 itsaplan 的 HTTP | ✅ 最简单，P2P 直连可用；代价是暴露特征端口 |
| **2. WSS 走 443（要全走 Caddy 时）** | RustDesk 客户端填域名，Caddy 反代 `/ws/id`→hbbs:21118、`/ws/relay`→hbbr:21119 | ⚠ 必须有公网合法证书（客户端拒绝自签）、无 P2P 全走中继、21118/21119 要用防火墙限制只有 Caddy 可访问（否则可伪造 X-Real-IP）|
| **3. caddy-l4 层4代理** | `xcaddy` 自定义构建加 caddy-l4 插件代理裸 TCP/UDP | ❌ 实验性、非官方、复杂度高，不建议 |

**建议**：先用路线 1（直连），等确认需要统一 443 入口再考虑路线 2。

路线 2 的 Caddyfile 片段：
```
rustdesk.example.com {
    handle /ws/id* {
        reverse_proxy hbbs:21118 {
            header_up X-Real-IP {remote_host}
        }
    }
    handle /ws/relay* {
        reverse_proxy hbbr:21119 {
            header_up X-Real-IP {remote_host}
        }
    }
}
```

---

## 第三部分：itsaplan 部署方案（基于官方 self-hosting.md，已完整读取）

### 3.1 官方 stack 组成（docker-compose.yml 实际内容）

| 服务 | 镜像 | 端口 | 作用 |
|---|---|---|---|
| `postgres` | postgres:17-alpine | 内部 | 数据库，volume `postgres-data` |
| `minio` | rustfs/rustfs:1.0.0 | 内部 9000/9001 | 附件对象存储，volume `minio-data` |
| `minio-init` | rustfs | 一次性 | 创建 attachments bucket，跑完退出 |
| `migrate` | itsaplan-api | 一次性 | 备份+执行数据库迁移，跑完退出 |
| `api` | itsaplan-api | **${API_PORT:-3000}:3000** | 主 API（auth/email/MCP）|
| `worker` | itsaplan-worker | 无 | 后台任务/webhook/通知 |
| `bot` | itsaplan-bot | 无 | Telegram bot（必须单副本）|
| `web` | itsaplan-web | **${WEB_PORT:-3001}:3001** | Next.js 前端 |

三个 volume：`postgres-data`、`minio-data`、`db-backups`

### 3.2 必须填的 7 个变量（缺一不可，stack 拒绝启动）

| 变量 | 生成方式 |
|---|---|
| `POSTGRES_PASSWORD` | `openssl rand -base64 32` |
| `BETTER_AUTH_SECRET` | 同上 |
| `APP_ENCRYPTION_KEY` | 同上（⚠ 改了会导致已存凭据无法解密）|
| `S3_ACCESS_KEY_ID` | 任意 >3 字符 |
| `S3_SECRET_ACCESS_KEY` | `openssl rand -base64 32`（≥8 字符）|
| `API_URL` | 见 3.3 |
| `APP_URL` | 见 3.3 |

### 3.3 API_URL 的两种形式（官方明确，选哪种决定 Caddy 怎么配）

**形式 A：独立子域名**
```
API_URL=https://api.example.com
APP_URL=https://app.example.com
```
session cookie 设在整个父域 `.example.com` 上。

**形式 B：web 主机下的路径（推荐，只需一个域名一个证书）**
```
API_URL=https://app.example.com/api
APP_URL=https://app.example.com
```
Caddy 转发规则（官方原文）：
```
https://app.example.com/api/*  ->  api:3000/*   (去掉 /api 前缀)
https://app.example.com/*      ->  web:3001/*
```
且必须把 OAuth discovery 文档也转发到 api：
```
/.well-known/oauth-authorization-server*
/.well-known/openid-configuration*
/.well-known/oauth-protected-resource*
```

**建议用形式 B**：单个域名、单个证书、cookie 用 `__Host-` 前缀（更安全），MCP 的 OAuth 也能正常工作。

### 3.4 迁移数据恢复（你之前备份的）

从 `迁移备份_20261120.zip` 恢复：
```bash
# 1. 把 secrets/itsaplan.env 的贡献值填进新 .env
#    特别是 APP_ENCRYPTION_KEY / BETTER_AUTH_SECRET / POSTGRES_PASSWORD
#    ⚠ 这三个必须和原来一致，否则登录失效、凭据无法解密

# 2. 先起 stack 空库
docker compose up -d

# 3. 导入 pg_dump
docker compose exec -T postgres psql -U itsaplan -d itsaplan < itsaplan_full_20261119_093036.sql

# 4. 附件恢复
#    把 itsaplan-attachments/ 的内容拷进 minio-data volume
```

### 3.5 注意事项（官方文档重点）

- `Secure` cookie：生产环境必须 HTTPS，否则登录成功但 session 不保持（你之前踩过这个坑）
- MCP OAuth 只在 HTTPS 下工作；纯 HTTP 非 localhost 只能用个人 API key
- api 端口虽然发布到 host，但**只应对 Caddy 开放**，不要暴露公网
- 客户端 IP：Caddy 默认会正确处理 `X-Forwarded-For`，无需额外配置
- 更新：`docker compose pull && docker compose up -d`；`VERSION` 可锁定版本

---

## 第四部分：RustDesk 部署方案（等调研补全）

（待补：hbbs/hbbr 的 docker-compose、端口 21115-21119、Caddy TCP/UDP 反代可行性）

---

## 第五部分：执行顺序（规划，不执行）

1. 你确认本规划
2. 你下载安装 Docker Desktop（或让我装，需管理员）
3. 配置 WSL2 后端 + 限制内存（`.wslconfig` 已清空，写入 `memory=4GB`）
4. 建 Caddy 基础设施容器 + shared network
5. 部署 itsaplan（用形式 B 路径反代）+ 恢复数据
6. 部署 RustDesk（确认 Caddy 能否代理 TCP/UDP）
7. 逐项验证

---

## 第六部分：已确认的环境参数（2026-11-20 实测）

| 项 | 值 |
|---|---|
| 域名 | **无公网域名，纯局域网** |
| 热点 IP | `172.20.10.x`（DHCP 动态，每次重连可能变）|
| 代理 | clash-verge，`127.0.0.1:7897`（系统代理 + git 都走它）|
| 旧配置 | `API_URL=http://172.20.10.2:3000`、`NODE_ENV=development` |
| Docker Desktop | 用户自己装 |
| 旧 itsaplan | 已删除，备份完好 |

### 6.1 ★ 纯局域网的关键矛盾（必须解决）

itsaplan 官方文档要求 TLS reverse proxy，原因：
> 生产环境 session cookie 带 `Secure` 属性，**HTTP 下登录成功但 session 不保持**。
> `http://localhost` 是例外（Chrome/Firefox）。

你之前能跑通，是因为用了 **`NODE_ENV=development`**（cookie 不加 Secure）。
但在 Caddy + 纯 HTTP 局域网方案下，如果沿用生产配置就会**登录失败**。

**三条可行路**：

| 方案 | 做法 | 评价 |
|---|---|---|
| **A. 保持 development + HTTP（推荐先用）** | `NODE_ENV=development`，Caddy 只做 HTTP 反代，不启用 HTTPS | ✅ 最简单，和你之前能跑通的环境一致；代价是局域网明文 HTTP |
| **B. Caddy `tls internal` 自签 HTTPS** | Caddy 签本地 CA 证书，每台客户端导入 root.crt | ⚠ 绿锁但要在每台设备装证书；itsaplan 认 HTTPS |
| **C. 有真域名 + DNS challenge** | 需公网域名 | ❌ 你说没有，排除 |

**用户已选 B 路线**：Caddy 	ls internal 自签 HTTPS + NODE_ENV=production，每台设备导根证书。

### 6.2 ★ 代理的坑（实测风险）

系统代理开着（`127.0.0.1:7897`），影响：
1. **镜像拉取**：`docker pull` 走系统代理可能失败（之前 git push 反复 `schannel` 握手失败）
   → Docker Desktop：Settings → Resources → Proxies 填 `http://127.0.0.1:7897`
2. **局域网访问**：浏览器访问 `http://172.20.10.x` 时，若 clash 全局代理无 bypass，
   流量被代理转发出去 → 访问失败
   → 确保 `172.20.0.0/16` 在 bypass/直连规则里
3. **Caddy 自动 HTTPS**：走方案 A HTTP，不涉及

### 6.3 Docker Desktop 安装后必做配置

```ini
# C:\Users\xuee\.wslconfig（当前已清空，写入）
[wsl2]
memory=4GB
processors=4
swap=1GB
```
避免 vmmemWSL 又吃 1.9GB。

### 6.4 局域网固定地址问题（你之前的痛点）

热点 IP 每次重连变（`172.20.10.2 → .4 → .5`），导致前端写死 IP → `failed to fetch`。

**Docker 方案下的改善**：
- `API_URL`/`APP_URL` 不能写死动态 IP
- 解法：给 Windows 网卡配**固定 IP**（如 `<当前IP>`），
  或 IP 变了只改 `.env` 一处再 `docker compose up -d`（比 portproxy 简单得多）

---

## 待你决策

- [x] 域名：无公网域名，纯局域网
- [x] Docker Desktop：你自己装
- [x] 旧 itsaplan：已删除，备份完好
- [x] HTTPS 路线：**B（Caddy 自签 + 导证书）**
- [ ] 是否给 Windows 配固定内网 IP（`<当前IP>`）解决动态 IP 问题？
